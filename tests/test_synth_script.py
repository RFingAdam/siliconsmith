# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""The Yosys script builder: LibreLane 3.0.14's order, the do-not-use lists, the clock."""

from __future__ import annotations

import re

import pytest

from siliconsmith.worker import synth_script as ss

DONT_USE = [
    "sky130_fd_sc_hd__dfxtp_1",
    "sky130_fd_sc_hd__lpflow_isobufsrc_1",
    "sky130_fd_sc_hd__ha_1",
]
SOURCES = ["project.v", "counter.v"]


def commands(script: str) -> list[str]:
    return [ln for ln in script.splitlines() if ln and not ln.startswith("#")]


def verbs(script: str) -> list[str]:
    """The Yosys pass of each command (`tee -o file pass args` counts as the inner pass)."""
    out = []
    for line in commands(script):
        words = line.split()
        if words[0] == "tee":
            words = words[3:]
        out.append(words[0])
    return out


def index_of(script: str, start: str) -> int:
    for i, line in enumerate(commands(script)):
        if line.startswith(start) or re.match(rf"tee -o \S+ {re.escape(start)}", line):
            return i
    raise AssertionError(f"{start!r} is not in the script")


def front() -> str:
    return ss.front_script(SOURCES, "tt_um_counter", "synth")


def back(period: float = 100) -> str:
    return ss.back_script("tt_um_counter", "synth", period, DONT_USE)


def test_front_half_follows_the_librelane_order() -> None:
    order = [
        "read_liberty -lib",
        "read_verilog",
        "hierarchy -check -top tt_um_counter",
        "rename -top tt_um_counter",
        "tribuf",
        "proc_clean",
        "proc_dlatch",
        "proc_dff",
        "check",
        "flatten",
        "fsm",
        "wreduce",
        "peepopt",
        "alumacc",
        "share",
        "memory -nomap",
        "memory_map",
        "techmap",
        "abc -fast",
        "stat",
        "write_rtlil",
    ]
    script = front()
    positions = [index_of(script, step) for step in order]
    assert positions == sorted(positions), dict(zip(order, positions, strict=True))


def test_back_half_follows_the_librelane_order() -> None:
    order = [
        "read_liberty -lib",
        "read_rtlil",
        f"techmap -map {ss.TRISTATE_MAP}",
        f"techmap -map {ss.LATCH_MAP}",
        "dfflibmap",
        "abc -script",
        "setundef -zero",
        "hilomap",
        "splitnets",
        "insbuf",
        "check",
        "stat -json",
        "write_verilog",
    ]
    script = back()
    positions = [index_of(script, step) for step in order]
    assert positions == sorted(positions), dict(zip(order, positions, strict=True))


def test_splitnets_is_followed_by_a_purging_clean_up() -> None:
    cmds = commands(back())
    split = cmds.index("splitnets")
    assert cmds[split + 1] == "opt_clean -purge"


def test_the_optimization_loops_are_yosys_opt_with_the_librelane_options() -> None:
    opts = [ln for ln in commands(front()) if ln.split()[0] == "opt"]
    assert opts == [
        "opt -nodffe -nosdff",
        "opt",
        "opt",
        "opt -fast -full",
        "opt -full",
        "opt -fast",
        "opt -fast",
        "opt -fast",
    ]


def test_both_do_not_use_lists_reach_dfflibmap_and_abc() -> None:
    script = back()
    for tool in ("dfflibmap", "abc"):
        (line,) = [ln for ln in commands(script) if ln.startswith(tool)]
        assert all(f"-dont_use {cell}" in line for cell in DONT_USE), tool
        assert f"-liberty {ss.LIBERTY}" in line


def test_abc_gets_the_clock_period_in_picoseconds() -> None:
    assert "-D 100000 " in back(100)
    assert "-D 20000 " in back(20)
    assert "-D 15152 " in back(15.152)
    assert "-D 1000 " in back(1)


def test_tie_cells_buffer_and_write_options_are_librelane_sky130s() -> None:
    script = back()
    assert "hilomap -hicell sky130_fd_sc_hd__conb_1 HI -locell sky130_fd_sc_hd__conb_1 LO" in script
    assert "insbuf -buf sky130_fd_sc_hd__buf_2 A X" in script
    assert "write_verilog -noattr -noexpr -nohex -nodec -defparam synth/tt_um_counter.v" in script
    assert "stat -json -liberty" in script


def test_design_is_read_with_librelane_flags_and_defines() -> None:
    lines = [ln for ln in commands(front()) if ln.startswith("read_verilog")]
    assert len(lines) == 2
    for line, source in zip(lines, SOURCES, strict=True):
        assert line.startswith("read_verilog -defer -noautowire -sv ")
        assert line.endswith(source)
        assert "-DPDK_sky130A" in line and "-DSCL_sky130_fd_sc_hd" in line
        assert "-D__librelane__" in line and "-D__pnr__" in line


def test_the_default_adder_type_applies_no_adder_map() -> None:
    """LibreLane maps $add only for SYNTH_ADDER_TYPE FA, RCA or CSA; Tiny Tapeout uses YOSYS."""
    for script in (front(), back()):
        assert "rca_map" not in script and "csa_map" not in script and "fa_map" not in script


def test_every_pass_is_a_yosys_command_the_flow_uses() -> None:
    known = {
        "read_liberty", "read_verilog", "read_rtlil", "hierarchy", "rename", "select", "attrmap",
        "tribuf", "proc_clean", "proc_rmdead", "proc_prune", "proc_init", "proc_arst", "proc_rom",
        "proc_mux", "proc_dlatch", "proc_dff", "proc_memwr", "check", "opt_expr", "flatten",
        "opt_clean", "opt", "fsm", "wreduce", "peepopt", "alumacc", "share", "memory",
        "memory_map", "techmap", "abc", "stat", "write_rtlil", "delete", "simplemap",
        "dfflibmap", "setundef", "hilomap", "splitnets", "insbuf", "write_verilog",
    }  # fmt: skip
    for script in (front(), back()):
        assert set(verbs(script)) <= known, set(verbs(script)) - known


def test_full_script_is_front_then_back_without_the_round_trip() -> None:
    full = ss.full_script(SOURCES, "tt_um_counter", "synth", 100, DONT_USE)
    assert "read_rtlil" not in full
    assert full.count("read_liberty -lib") == 1
    assert verbs(full).index("abc") < len(verbs(full))
    assert commands(full)[: len(commands(front()))] == commands(front())
    assert commands(full)[-1].startswith("write_verilog")
    assert full.index("proc_dlatch") < full.index("dfflibmap")


def test_abc_script_is_librelanes_area_strategy_zero() -> None:
    lines = ss.abc_area0_script().splitlines()
    assert lines[:3] == ["fx", "mfs", "strash"]
    assert "amap -m -Q 0.1 -F 20 -A 20 -C 5000" in lines
    assert lines[-2:] == ["stime -p", "print_stats -m"]
    resyn2 = "balance; drw -l; drf -l; balance; drw -l; drw -l -z; balance; drf -l -z; drw -l -z"
    assert f"{resyn2}; balance" in lines
    assert "retime -M 5" in lines
    assert not any(line.strip() == "" for line in lines)


def test_sdc_names_the_driving_cell_and_the_load() -> None:
    assert ss.sdc_text() == "set_driving_cell sky130_fd_sc_hd__inv_2\nset_load 33.442\n"


def test_cell_list_parsing_skips_comments_blanks_and_duplicates() -> None:
    text = "# header\nsky130_fd_sc_hd__a\n\nsky130_fd_sc_hd__b  # why\nsky130_fd_sc_hd__a\n"
    assert ss.parse_cell_list(text) == ["sky130_fd_sc_hd__a", "sky130_fd_sc_hd__b"]


@pytest.mark.parametrize("script", [front(), back()])
def test_scripts_use_only_relative_names_for_the_design_and_absolute_for_the_pdk(
    script: str,
) -> None:
    for line in commands(script):
        for word in line.split():
            if word.startswith("/"):
                assert word.startswith("/opt/pdk/sky130A/"), line
