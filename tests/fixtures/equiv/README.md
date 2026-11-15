# Equivalence check fixtures

Real Yosys 0.62 logs and models from the toolchain image, one folder per scenario. They back the
unit tests of the log readers (`tests/test_equiv_parse.py`) and of the verdict mapping
(`tests/test_equiv_worker.py`).

- A `*.log` file is the log of one Yosys call of the check, reduced to the lines the readers look
  at (guard markers, `equiv_status`, `sat` results and the `sat -show` tables) with the rest of
  Yosys's pass output removed. No line is edited. The table rows are kept only in logs that hold a
  counterexample.
- `equiv.json` and `equiv.il` are the stage-1 model of the scenario, unedited.
- The designs are the counter (`counter_*`), a shift register without reset (`shiftnr_*`), and the
  tri-state, async-set-and-reset, initial-value and clock fixtures of the spike. The netlists were
  made by Yosys with a synthesis flow close to the production one and then edited by hand (one cell
  changed, the clock inverted, a loop made), which is what the scenario name says:
  `counter_m0442` a flip-flop that holds a constant output after reset never set it,
  `counter_m0115` a one-cycle reset difference, `counter_m0043` a bug that shows 130 cycles after
  reset, `shiftnr_m0165` an inverted flop output, `shiftnr_m0365` a harmless one, `clkinv` a flop
  clocked from the inverted clock, `loop` a combinational loop, `clockgate` a clock-gate cell.
- `initstate_deep` (a register with an initial value that reaches a pin only at cycle 42),
  `initstate_initz` (a counter with an initial value and no reset) and `initstate_equal` (initial
  values that reset overwrites) are the logs of the initial-value state check (stage 2): the RTL
  with its initial values against the RTL without, on every register after the reset cycle.
- `counter_good/base.log` and `counter_m0442/base.log` (stage 3, the X run and the DEF run) were
  made again by the round-15 code: the X run now takes each side from its own copy of the
  stage-1 model (top `base_x`), so every netlist value in its table is defined. In
  `counter_m0442` the X run finds uio_oe[0] at cycle 1: RTL 0, netlist 1.
- `bmc_once.log` is the one-shot search from reset (stage 4, `equiv_script.miter_once`: one `sat`
  problem of 33 time steps) next to the step-by-step search (`bmc.log`) on the same miter, both
  made by the round-14 code with the production netlist: `counter_good/bmc_once.log` (a proof),
  `seg7reg` (the registered seven-segment decoder of the `seg7reg` design fixture: the one-shot
  proves 32 cycles in about a second, the step-by-step `bmc.log` was stopped at a 20 s limit
  after 18 cycles) and `counter_swap` (the counter's netlist with one cell swapped, nand3 to
  nor3: both find the difference at cycle 4, with different traces).
- `tests/fixtures/equiv/check_netlist.py` is the test-only script that gives the worker's check a
  hand-edited netlist inside the container (the production verb cannot take one).
- `tests/fixtures/equiv/hidden_lint.py` is the test-only script that runs the worker's lint
  without comments (`equiv._hidden_lint`) inside the container, past the text rule that would
  refuse the same lint_off first.
