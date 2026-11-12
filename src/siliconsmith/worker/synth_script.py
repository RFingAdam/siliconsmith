# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-FileCopyrightText: 2020-2024 Efabless Corporation
# SPDX-License-Identifier: Apache-2.0

"""The Yosys script that turns the emitted Verilog into sky130 cells.

This is the one script builder for every M1 step that needs a netlist: `synthesize`, gate-level
simulation (M1-2) and the equivalence check (M1-3) all call it, so they always see the same flow.

Adapted from LibreLane (Apache-2.0, Copyright 2020-2024 Efabless Corporation; its file also
adapts Yosys's `synth.cc` and `opt.cc`, ISC licensed). No LibreLane or Yosys code is vendored: the
pass order and the ABC script text are transcribed here.

Source: LibreLane 3.0.14, `librelane/scripts/pyosys/synthesize.py` (functions `librelane_proc`,
`librelane_opt`, `librelane_synth` and `synthesize`), `librelane/scripts/pyosys/ys_common.py`
(how the inputs are read) and `librelane/scripts/pyosys/construct_abc_script.py` (the ABC script
of the default `SYNTH_STRATEGY`, "AREA 0"), with the values the Tiny Tapeout flow ends up with
for sky130A (open_pdks 8afc8346, `sky130/openlane/config.tcl` and
`sky130_fd_sc_hd/config.tcl`, read through LibreLane's `pdk_compat.py`):

    SYNTH_HIERARCHY_MODE flatten        SYNTH_TIE_UNDEFINED low        SYNTH_SPLITNETS true
    SYNTH_ADDER_TYPE YOSYS              SYNTH_DIRECT_WIRE_BUFFERING true
    SYNTH_STRATEGY "AREA 0"             SYNTH_ABC_BUFFERING / SYNTH_SIZING false
    SYNTH_NORMALIZE_SINGLE_BIT_VECTORS true      SYNTH_WRITE_NOATTR true
    SYNTH_TIEHI/LO_CELL sky130_fd_sc_hd__conb_1/HI and /LO
    SYNTH_BUFFER_CELL sky130_fd_sc_hd__buf_2/A/X
    SYNTH_DRIVING_CELL sky130_fd_sc_hd__inv_2, OUTPUT_CAP_LOAD 33.442
    SYNTH_EXCLUDED_CELL_FILE no_synth.cells, PNR_EXCLUDED_CELL_FILE drc_exclude.cells
    SYNTH_LATCH_MAP latch_map.v, SYNTH_TRISTATE_MAP tribuff_map.v

Deviations from LibreLane (every one is also listed in PLAN.md section 6):

1. No pyosys. LibreLane drives Yosys from Python; this is the same pass sequence as a plain Yosys
   script. Its hand-written optimization loops (`librelane_opt`) are Yosys's own `opt` pass, whose
   options map one to one: `opt -nodffe -nosdff`, `opt`, `opt -fast -full` and `opt -full`
   (`-full` is `-mux_undef -mux_bool -undriven -fine` plus `opt_share`).
2. `d.optimize()`, `d.sort()` and `d.check()` (internal consistency checks and ordering of the
   in-memory design) are left out; they do not change the netlist.
3. The excluded cells are passed as `-dont_use` to `dfflibmap` and `abc` instead of being cut out
   of a copy of the liberty file. The result is the same cell choice. The full liberty file is
   read for `stat`, so the area of every cell is known.
4. One extra boundary: the flow is split into a front half (everything up to and including the
   generic gate-level `stat`) and a back half (technology mapping onwards), joined by a
   `write_rtlil` / `read_rtlil` round trip, so the worker can stop a hopelessly big design before
   the slow `abc` run (`synth.py`). The passes and their order are unchanged.
5. `SYNTH_SHOW` (graphviz) and `SYNTH_AUTONAME` are off in LibreLane's defaults and not run.
   `SYNTH_ADDER_TYPE` is YOSYS, so the `rca`/`csa`/`fa` maps (which are in the image) are not
   applied, exactly as in the Tiny Tapeout flow. The clock-gating pass is off by default.
6. Blackbox cell models come from the liberty file only (LibreLane also reads macro views; there
   are none here). Sources are read with `read_verilog -defer -noautowire -sv` from the run
   folder with relative names, so Yosys's source attributes read `counter.v:12.5-14.6`.
7. The SDC file for ABC names the driving cell without the `/Y` pin suffix, because ABC's
   `read_constr` takes a bare cell name (see `sdc_text`).

Stdlib only: this runs in the container (and in the host's unit tests).
"""

from __future__ import annotations

from pathlib import PurePosixPath

PDK_ROOT = "/opt/pdk/sky130A"
LIBRARY = "sky130_fd_sc_hd"
LIBERTY = f"{PDK_ROOT}/libs.ref/{LIBRARY}/lib/{LIBRARY}__tt_025C_1v80.lib"
CELL_MODELS = (
    f"{PDK_ROOT}/libs.ref/{LIBRARY}/verilog/primitives.v",
    f"{PDK_ROOT}/libs.ref/{LIBRARY}/verilog/{LIBRARY}.v",
)
TECH_DIR = f"{PDK_ROOT}/libs.tech/openlane/{LIBRARY}"
NO_SYNTH_CELLS = f"{TECH_DIR}/no_synth.cells"
DRC_EXCLUDE_CELLS = f"{TECH_DIR}/drc_exclude.cells"
LATCH_MAP = f"{TECH_DIR}/latch_map.v"
TRISTATE_MAP = f"{TECH_DIR}/tribuff_map.v"

TIEHI_CELL = (f"{LIBRARY}__conb_1", "HI")
TIELO_CELL = (f"{LIBRARY}__conb_1", "LO")
BUFFER_CELL = (f"{LIBRARY}__buf_2", "A", "X")
DRIVING_CELL = f"{LIBRARY}__inv_2"
OUTPUT_CAP_LOAD = "33.442"  # femtofarad, as LibreLane's sky130 config

# What LibreLane's synthesis defines for the design's `ifdef`s.
DEFINES = ("PDK_sky130A", f"SCL_{LIBRARY}", "__librelane__", "__pnr__")

# File names inside the run's `out/synth/` folder.
GENERIC_RTLIL = "generic.il"
ABC_SCRIPT = "area0.abc"
ABC_SDC = "synthesis.abc.sdc"
REPORTS = "reports"
FRONT_LOG = "front.log"
BACK_LOG = "back.log"


def parse_cell_list(text: str) -> list[str]:
    """Cell names from a `*.cells` file: one per line, blank lines and `#` comments skipped."""
    cells: list[str] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line and line not in cells:
            cells.append(line)
    return cells


def abc_area0_script() -> str:
    """LibreLane 3.0.14's ABC script for `SYNTH_STRATEGY` "AREA 0" with its default options.

    Transcribed from `ABCScriptCreator.generate_abc_script` (`construct_abc_script.py`), with
    `SYNTH_ABC_BUFFERING`, `SYNTH_SIZING`, `SYNTH_ABC_USE_MFS3`, `SYNTH_ABC_AREA_USE_NF` and the
    two legacy flags all false, so the empty lines it would print are left out.
    """
    rf, rfz = "drf -l", "drf -l -z"
    rw, rwz = "drw -l", "drw -l -z"
    b = "balance"
    resyn2 = f"{b}; {rw}; {rf}; {b}; {rw}; {rwz}; {b}; {rfz}; {rwz}; {b}"
    choice2 = (
        f"fraig_store; {b}; fraig_store; {resyn2}; fraig_store; {resyn2}; "
        f"fraig_store; {resyn2}; fraig_store; fraig_restore"
    )
    lines = [
        "fx",
        "mfs",
        "strash",
        rf,
        resyn2,
        "retime -M 5",
        "scleanup",
        choice2,
        "amap -m -Q 0.1 -F 20 -A 20 -C 5000",
        "retime",
        "&get -n",
        "&st",
        "&dch",
        "&nf",
        "&put",
        "stime -p",
        "print_stats -m",
    ]
    return "\n".join(lines) + "\n"


def sdc_text() -> str:
    """The constraints file ABC reads (`-constr`): the driving cell and the output load.

    LibreLane writes `set_driving_cell <SYNTH_DRIVING_CELL>`; for sky130 that value carries a
    pin (`sky130_fd_sc_hd__inv_2/Y`). ABC's constraint reader wants the cell name alone, so the
    pin is left off here.
    """
    return f"set_driving_cell {DRIVING_CELL}\nset_load {OUTPUT_CAP_LOAD}\n"


def _ps(clock_period_ns: float) -> str:
    return str(round(clock_period_ns * 1000))


def _dont_use_args(cells: list[str]) -> str:
    return " ".join(f"-dont_use {cell}" for cell in cells)


def _read_blackboxes() -> list[str]:
    """`add_blackbox_models`: every library cell as a black box (ys_common.py)."""
    return [
        f"read_liberty -lib -ignore_miss_dir -setattr blackbox -setattr keep_hierarchy {LIBERTY}"
    ]


def front_script(
    sources: list[str],
    top: str,
    out: str,
    *,
    keep_front_stat: bool = True,
) -> str:
    """Everything up to the generic gate-level `stat`; ends with `write_rtlil <out>/generic.il`.

    `sources` are paths relative to the folder Yosys runs in; `out` is the output folder
    (relative to it too) that holds `reports/`.
    """
    reports = f"{out}/{REPORTS}"
    lines = ["# LibreLane 3.0.14 synthesize.py, front half (see worker/synth_script.py)"]
    lines += _read_blackboxes()
    defines = " ".join(f"-D{d}" for d in DEFINES)
    for source in sources:
        lines.append(f"read_verilog -defer -noautowire -sv {defines} {source}")
    lines += [
        f"hierarchy -check -top {top} -nokeep_prints -nokeep_asserts",
        f"rename -top {top}",
        f"select -module {top}",
        "attrmap -remove single_bit_vector",  # SYNTH_NORMALIZE_SINGLE_BIT_VECTORS
        "select -clear",
        "tribuf",  # SYNTH_TRISTATE_MAP is set for sky130
        # -- librelane_synth(): hierarchy, proc and the optimization sequence
        f"hierarchy -check -top {top} -nokeep_prints -nokeep_asserts",
        # librelane_proc()
        "proc_clean",
        "proc_rmdead",
        "proc_prune",
        "proc_init",
        "proc_arst",
        "proc_rom",
        "proc_mux",
        f"tee -o {reports}/latch.rpt proc_dlatch",
        "proc_dff",
        "proc_memwr",
        "proc_clean",
        f"tee -o {reports}/pre_synth_chk.rpt check",
        "opt_expr",
        # flatten (SYNTH_HIERARCHY_MODE flatten), then the optimization sequence
        "flatten",
        "opt_expr",
        "opt_clean",
        "opt -nodffe -nosdff",  # librelane_opt(nodffe=True, nosdff=True)
        "fsm",
        "opt",  # librelane_opt()
        "wreduce",
        "peepopt",
        "opt_clean",
        "alumacc",
        "share",
        "opt",
        "memory -nomap",
        "opt_clean",
        "opt -fast -full",  # librelane_opt(fast, opt_share, mux_undef, mux_bool, undriven, fine)
        "memory_map",
        "opt -full",
        "techmap",
        "opt -fast",
        "opt -fast",
        "abc -fast",  # Yosys older than 0.68: LibreLane adds -fast
        "opt -fast",
        f"hierarchy -check -top {top} -nokeep_prints -nokeep_asserts",
        f"tee -o {reports}/generic_chk.rpt check",
        f"tee -o {reports}/generic.rpt stat",
    ]
    if keep_front_stat:
        lines.append(f"tee -o {reports}/generic.json stat -json")
    lines.append(f"write_rtlil {out}/{GENERIC_RTLIL}")
    return "\n".join(lines) + "\n"


def back_script(
    top: str,
    out: str,
    clock_period_ns: float,
    dont_use: list[str],
    *,
    netlist: str | None = None,
) -> str:
    """Technology mapping onwards: reads `<out>/generic.il`, writes the sky130 netlist.

    `dont_use` is every cell of `no_synth.cells` and `drc_exclude.cells`. The netlist defaults
    to `<out>/<top>.v`.
    """
    reports = f"{out}/{REPORTS}"
    netlist = netlist or f"{out}/{top}.v"
    dont = _dont_use_args(dont_use)
    lib_flags = f"-liberty {LIBERTY}"
    hi_cell, hi_port = TIEHI_CELL
    lo_cell, lo_port = TIELO_CELL
    buf_cell, buf_in, buf_out = BUFFER_CELL
    lines = ["# LibreLane 3.0.14 synthesize.py, back half (see worker/synth_script.py)"]
    lines += _read_blackboxes()
    lines += [
        f"read_rtlil {out}/{GENERIC_RTLIL}",
        "delete t:$print",
        "delete t:$assert",
        "opt",
        "opt_clean -purge",
        f"tee -o {reports}/pre_techmap.rpt stat {lib_flags}",
        # tri-state buffers, then latches (the FA map only applies to SYNTH_ADDER_TYPE FA)
        f"techmap -map {TRISTATE_MAP}",
        "simplemap",
        f"techmap -map {LATCH_MAP}",
        "simplemap",
        f"dfflibmap {lib_flags} {dont}",
        f"tee -o {reports}/post_dff.rpt stat {lib_flags}",
        f"abc -script {out}/{ABC_SCRIPT} -D {_ps(clock_period_ns)} -constr {out}/{ABC_SDC} "
        f"-showtmp {lib_flags} {dont}",
        "setundef -zero",  # SYNTH_TIE_UNDEFINED low
        f"hilomap -hicell {hi_cell} {hi_port} -locell {lo_cell} {lo_port}",
        "splitnets",
        "opt_clean -purge",
        f"insbuf -buf {buf_cell} {buf_in} {buf_out}",
        f"tee -o {reports}/chk.rpt check",
        f"tee -o {reports}/stat.json stat -json {lib_flags}",
        f"tee -o {reports}/stat.rpt stat {lib_flags}",
        f"write_verilog -noattr -noexpr -nohex -nodec -defparam {netlist}",
    ]
    return "\n".join(lines) + "\n"


def full_script(
    sources: list[str], top: str, out: str, clock_period_ns: float, dont_use: list[str]
) -> str:
    """Front and back half in one script, without the early-stop boundary.

    Gate-level simulation and the equivalence check call this when they re-synthesize in one go.
    The back half's `read_rtlil` is replaced by simply carrying on with the same design.
    """
    back = back_script(top, out, clock_period_ns, dont_use).splitlines()
    marker = f"read_rtlil {out}/{GENERIC_RTLIL}"
    start = back.index(marker) + 1
    front = front_script(sources, top, out).splitlines()
    return "\n".join([*front, *back[start:]]) + "\n"


def netlist_name(top: str) -> str:
    return PurePosixPath(f"{top}.v").name
