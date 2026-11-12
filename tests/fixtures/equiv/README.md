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
- `tests/fixtures/equiv/check_netlist.py` is the test-only script that gives the worker's check a
  hand-edited netlist inside the container (the production verb cannot take one).
