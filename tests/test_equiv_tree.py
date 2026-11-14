# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""The function-purity walker on small hand-written syntax trees (no Verilator needed).

The real trees come from `verilator --json-only`; `tests/integration/test_equiv.py` runs it.
"""

from __future__ import annotations

from typing import Any

import pytest

from siliconsmith.worker import equiv_tree as et

META = {
    "files": {
        "e": {"filename": "core.v"},
        "f": {"filename": "project.v"},
        "d": {"filename": "<verilated_std>"},
    }
}
OURS = ["project.v", "core.v"]


def var(addr: str, name: str, line: int = 1, **more: Any) -> dict[str, Any]:
    return {"type": "VAR", "addr": addr, "name": name, "loc": f"e,{line}:1,{line}:4", **more}


def ref(addr: str, to: str, name: str, line: int = 1, **more: Any) -> dict[str, Any]:
    return {
        "type": "VARREF",
        "addr": addr,
        "name": name,
        "varp": to,
        "loc": f"e,{line}:5,{line}:8",
        "access": "RD",
        **more,
    }


def call(addr: str, to: str, name: str, line: int = 1) -> dict[str, Any]:
    return {
        "type": "FUNCREF",
        "addr": addr,
        "name": name,
        "taskp": to,
        "loc": f"e,{line}:5,{line}:8",
    }


def func(
    addr: str, name: str, line: int, *kids: dict[str, Any], kind: str = "FUNC", file: str = "e"
) -> dict[str, Any]:
    return {
        "type": kind,
        "addr": addr,
        "name": name,
        "loc": f"{file},{line}:1,{line}:4",
        "stmtsp": list(kids),
    }


def module(*kids: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "NETLIST",
        "modulesp": [{"type": "MODULE", "addr": "(M)", "stmtsp": list(kids)}],
    }


SIG = var("(S)", "cnt", 3, varType="VAR")
ARG = var("(A)", "d", 5, varType="PORT", isFuncLocal=True, direction="INPUT")
RET = var("(R)", "rd", 5, isFuncReturn=True, isFuncLocal=True)


def rd_func(*extra: dict[str, Any], name: str = "rd", addr: str = "(F)") -> dict[str, Any]:
    """A function `rd(d)` that reads its argument and writes its return value, plus `extra`."""
    return func(
        addr,
        name,
        5,
        RET,
        ARG,
        ref("(r1)", "(A)", "d", 6),
        ref("(r2)", "(R)", "rd", 6, access="WR"),
        *extra,
    )


def judge(*kids: dict[str, Any], ours: list[str] | None = None) -> et.Impure | None:
    return et.impure_function(module(*kids), META, OURS if ours is None else ours)


def test_a_function_of_its_arguments_and_locals_is_pure() -> None:
    local = var("(L)", "tmp", 5, isFuncLocal=True)
    assert judge(SIG, rd_func(local, ref("(r3)", "(L)", "tmp", 7))) is None


def test_a_module_signal_read_is_refused_with_a_clean_sentence() -> None:
    found = judge(SIG, rd_func(ref("(r3)", "(S)", "cnt", 7)))
    assert found is not None
    assert (found.name, found.file, found.line) == ("rd", "core.v", 5)
    assert found.sentence == (
        "the function `rd` (core.v, line 5) reads `cnt` (line 7), which is not one of its "
        "arguments: a simulator's always @* does not re-run when it changes; pass it in as an "
        "argument"
    )


def test_a_module_signal_written_is_refused() -> None:
    found = judge(SIG, rd_func(ref("(r3)", "(S)", "cnt", 7, access="WR")))
    assert found is not None and "writes `cnt` (line 7)" in found.sentence


@pytest.mark.parametrize(
    "extra",
    [
        {"type": "VARXREF", "addr": "(x)", "name": "cnt", "dotted": "top", "loc": "e,7:1,7:4"},
        {"type": "FUNCREF", "addr": "(x)", "name": "g", "dotted": "u", "loc": "e,7:1,7:4"},
        {"type": "RAND", "addr": "(x)", "loc": "e,7:1,7:4"},
    ],
    ids=["varxref", "dotted call", "random"],
)
def test_a_hierarchical_or_stateful_reference_is_refused(extra: dict[str, Any]) -> None:
    found = judge(SIG, rd_func(extra))
    assert found is not None and found.name == "rd" and "(line 7)" in found.sentence


@pytest.mark.parametrize(
    "param",
    [
        var("(P)", "K", 2, isParam=True, varType="LPARAM"),
        var("(P)", "K", 2, varType="GPARAM"),
        var("(P)", "K", 2, varType="GENVAR"),
        var("(P)", "K", 2, isParam=True),
    ],
    ids=["localparam", "parameter", "genvar", "isParam"],
)
def test_parameters_localparams_and_genvars_are_fine(param: dict[str, Any]) -> None:
    assert judge(param, rd_func(ref("(r3)", "(P)", "K", 7))) is None


def test_a_function_that_calls_an_impure_one_is_impure_and_names_the_chain() -> None:
    inner = func("(G)", "inner", 9, var("(Gr)", "inner", 9), ref("(g1)", "(S)", "cnt", 10))
    outer = rd_func(call("(c1)", "(G)", "inner", 8))
    found = judge(SIG, outer, inner)
    assert found is not None and found.name == "rd"
    assert "calls `inner` (line 8), which is not pure: `inner` reads `cnt` (line 10)" in (
        found.sentence
    )


def test_a_function_that_calls_a_pure_one_is_pure() -> None:
    inner = func("(G)", "inner", 9, var("(Gr)", "inner", 9), var("(Ga)", "a", 9, isFuncLocal=True))
    assert judge(SIG, rd_func(call("(c1)", "(G)", "inner", 8)), inner) is None


def test_recursion_alone_is_not_impure_but_recursion_into_an_impure_one_is() -> None:
    assert judge(rd_func(call("(c1)", "(F)", "rd", 8))) is None
    a = func("(G)", "a", 9, call("(c2)", "(F)", "rd", 9))
    found = judge(SIG, rd_func(call("(c1)", "(G)", "a", 8), ref("(r9)", "(S)", "cnt", 8)), a)
    assert found is not None and found.name == "rd"
    other = judge(SIG, rd_func(call("(c1)", "(G)", "a", 8), ref("(r9)", "(S)", "cnt", 8)), a)
    assert other is not None
    first_a = func("(G)", "a", 2, call("(c2)", "(F)", "rd", 2))
    found = judge(SIG, rd_func(call("(c1)", "(G)", "a", 8), ref("(r9)", "(S)", "cnt", 8)), first_a)
    assert found is not None and found.name == "a" and found.line == 2  # first by position


def test_a_void_function_is_judged_like_a_function() -> None:
    """Verilator models `function void` as a TASK node; the `task` keyword is refused by tokens."""
    pure = func("(T)", "split", 4, var("(h)", "hi", 4, direction="OUTPUT"), kind="TASK")
    assert judge(SIG, pure) is None
    bad = func("(T)", "split", 4, ref("(w)", "(S)", "cnt", 5, access="WR"), kind="TASK")
    found = judge(SIG, bad)
    assert found is not None and found.sentence.startswith("the function `split` (core.v, line 4)")
    assert "writes `cnt` (line 5)" in found.sentence


def test_a_call_to_an_impure_void_function_is_impure() -> None:
    bad = func("(T)", "w", 4, ref("(w)", "(S)", "cnt", 5, access="WR"), kind="TASK")
    found = judge(SIG, bad, rd_func(call("(c1)", "(T)", "w", 8)))
    assert found is not None and found.name == "w"  # the void function itself comes first by line


def test_the_first_impure_function_by_file_then_line() -> None:
    late = func("(F2)", "late", 20, var("(x)", "late", 20), ref("(r)", "(S)", "cnt", 21))
    early = func("(F3)", "early", 3, var("(y)", "early", 3), ref("(q)", "(S)", "cnt", 4), file="f")
    found = judge(SIG, late, early)
    assert found is not None and (found.name, found.file) == ("early", "project.v")


def test_a_function_of_another_file_is_not_judged() -> None:
    std = func("(F2)", "std", 20, ref("(r)", "(S)", "cnt", 21), file="d")
    assert judge(SIG, std) is None


def test_a_call_to_a_function_the_check_cannot_judge_is_impure() -> None:
    std = func("(F2)", "std", 20, file="d")
    found = judge(rd_func(call("(c1)", "(F2)", "std", 8)), std)
    assert found is not None and "which the check cannot read" in found.sentence


@pytest.mark.parametrize(
    "extra",
    [
        {"type": "WEIRD", "addr": "(x)", "varp": "(nowhere)", "loc": "e,7:1,7:4"},
        {"type": "WEIRD", "addr": "(x)", "taskp": "(nowhere)", "loc": "e,7:1,7:4"},
        {"type": "WEIRD", "addr": "(x)", "varp": "(F)", "loc": "e,7:1,7:4"},  # not a VAR
        {"type": "WEIRD", "addr": "(x)", "taskp": "(A)", "loc": "e,7:1,7:4"},  # not a function
        {"type": "PARSEREF", "addr": "(x)", "loc": "e,7:1,7:4"},
        {"type": "VARREF", "addr": "(x)", "varp": "(A)", "loc": "z,7:1,7:4"},  # unknown letter
        {"type": "VARREF", "addr": "(x)", "varp": "(A)"},  # no position
    ],
    ids=["varp", "taskp", "varp-not-var", "taskp-not-func", "unresolved", "letter", "no-loc"],
)
def test_a_node_the_walker_does_not_understand_fails_closed(extra: dict[str, Any]) -> None:
    with pytest.raises(et.TreeError):
        judge(SIG, rd_func(extra))


def test_a_function_with_an_unknown_file_letter_fails_closed() -> None:
    with pytest.raises(et.TreeError):
        judge(func("(F2)", "g", 3, file="q"))


@pytest.mark.parametrize("meta", [{}, {"files": []}, {"files": {"e": {}}}, None])
def test_an_unreadable_file_table_fails_closed(meta: Any) -> None:
    with pytest.raises(et.TreeError):
        et.impure_function(module(), meta, OURS)


def test_a_tree_that_is_not_an_object_fails_closed() -> None:
    with pytest.raises(et.TreeError):
        et.impure_function([], META, OURS)


def test_a_default_value_wrapper_is_shown_under_the_name_the_user_wrote() -> None:
    wrapped = func("(F)", "rd__Vtcwrap_2", 5, ref("(r)", "(S)", "cnt", 6))
    found = judge(SIG, wrapped)
    assert found is not None and found.name == "rd" and "`rd` (core.v" in found.sentence


# -- Verilator drops what Yosys keeps (a constant that is x: 1/0) --------------------------------


def blocks(*nodes: dict[str, Any]) -> dict[str, Any]:
    return {"type": "NETLIST", "modulesp": list(nodes)}


def always(line: int, *, keyword: str = "always", sens: bool = False) -> dict[str, Any]:
    return {
        "type": "ALWAYS",
        "keyword": keyword,
        "loc": f"e,{line}:3,{line}:9",
        "sentreep": [{"type": "SENTREE"}] if sens else [],
    }


def test_a_tree_with_every_declared_block_is_complete() -> None:
    tree = blocks(func("(F)", "rd", 3), func("(G)", "rd", 3), always(5), always(5))
    # two copies (a for-generate) at one position count once
    assert et.missing_blocks(tree, META, {"core.v": (1, 1)}) is None
    assert et.missing_blocks(tree, META, {"core.v": (0, 0), "project.v": (0, 0)}) is None


def test_a_dropped_function_or_always_star_is_found() -> None:
    tree = blocks(func("(F)", "rd", 3), always(5))
    got = et.missing_blocks(tree, META, {"core.v": (2, 1)})
    assert got is not None and "core.v declares 2 function or task(s)" in got
    assert "holds only 1" in got
    got = et.missing_blocks(tree, META, {"core.v": (1, 2)})
    assert got is not None and "2 `always @*` block(s)" in got


def test_only_a_task_or_function_of_that_file_counts() -> None:
    tree = blocks(func("(F)", "rd", 3, file="f"), func("(T)", "tk", 4, kind="TASK"))
    assert et.missing_blocks(tree, META, {"core.v": (1, 0)}) is None
    assert et.missing_blocks(tree, META, {"core.v": (2, 0)}) is not None


def test_a_clocked_always_or_a_continuous_assign_is_not_an_always_star() -> None:
    tree = blocks(always(5, sens=True), always(6, keyword="cont_assign"))
    assert et.missing_blocks(tree, META, {"core.v": (0, 1)}) is not None


def test_missing_blocks_fails_closed() -> None:
    with pytest.raises(et.TreeError):
        et.missing_blocks([], META, {"core.v": (0, 0)})
    with pytest.raises(et.TreeError):
        et.missing_blocks(blocks(), {}, {"core.v": (0, 0)})
