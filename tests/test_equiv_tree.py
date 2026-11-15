# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

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


# -- placed_nodes: where Verilator's blocks and calls are -----------------------------------------


def test_placed_nodes_lists_the_nodes_of_known_files_only() -> None:
    tree = {
        "type": "NETLIST",
        "modulesp": [
            {"type": "ALWAYS", "keyword": "always_comb", "loc": "e,5:3,5:9"},
            {"type": "FUNCREF", "name": "rd", "dotted": "g", "loc": "e,6:7,6:9"},
            {"type": "FUNCREF", "name": "std", "loc": "z,1:1,1:2"},  # a file meta does not know
            {"type": "VAR", "name": "x"},  # no position
        ],
    }
    got = sorted(et.placed_nodes(tree, META), key=lambda n: n.line)
    assert [(n.type, n.keyword, n.name, n.dotted, n.file, n.line, n.col) for n in got] == [
        ("ALWAYS", "always_comb", "", "", "core.v", 5, 3),
        ("FUNCREF", "", "rd", "g", "core.v", 6, 7),
    ]


def test_placed_nodes_fails_closed() -> None:
    with pytest.raises(et.TreeError):
        list(et.placed_nodes([], META))
    with pytest.raises(et.TreeError):
        list(et.placed_nodes({}, {}))


# -- x and z parameters (xz_parameter) -----------------------------------------------------------


def const(name: str, line: int = 1) -> dict[str, Any]:
    return {"type": "CONST", "name": name, "loc": f"e,{line}:9,{line}:12"}


def param(name: str, value: str, line: int, **more: Any) -> dict[str, Any]:
    return var(f"({name})", name, line, varType="LPARAM", isParam=True,
               valuep=[const(value, line)], **more)  # fmt: skip


@pytest.mark.parametrize("value", ["32'hxxxxxxxx", "4'b10z1", "1'bx", "32'sbxxxx"])
def test_a_parameter_with_an_x_or_z_bit_is_named(value: str) -> None:
    got = et.xz_parameter(module(param("Q", "32'h1", 2), param("K", value, 4)), META, OURS)
    assert got is not None and got.name == "K" and (got.file, got.line) == ("core.v", 4)
    assert "computes the parameter `K` with an x or z bit" in got.sentence
    assert "`1/0`" in got.sentence and "Give it a defined value" in got.sentence


@pytest.mark.parametrize("kind", [{"varType": "GPARAM"}, {"varType": "GENVAR", "isParam": False}])
def test_a_generate_parameter_or_genvar_counts_too(kind: dict[str, Any]) -> None:
    node = var("(G)", "G", 3, valuep=[const("32'hx", 3)], **kind)
    got = et.xz_parameter(module(node), META, OURS)
    assert got is not None and got.name == "G"


def test_a_folded_constant_with_an_x_bit_is_found_and_a_named_parameter_wins_on_its_line() -> None:
    got = et.xz_parameter(module(const("8'bxxxxxxxx", 6)), META, OURS)
    assert got is not None and got.name == "" and "computes a constant" in got.sentence
    both = module(const("8'bxxxxxxxx", 6), param("P", "8'hx", 6))
    got = et.xz_parameter(both, META, OURS)
    assert got is not None and got.name == "P"


def test_defined_values_a_z_label_and_other_files_are_fine() -> None:
    assert et.xz_parameter(module(param("W", "32'sh8", 2), const("8'h1f", 3)), META, OURS) is None
    assert et.xz_parameter(module(const("4'b1z0z", 3)), META, OURS) is None  # a casez label
    value = const("32'hx", 2) | {"loc": "d,2:9,2:12"}
    other = var("(X)", "X", 2, varType="LPARAM", valuep=[value]) | {"loc": "d,2:1,2:4"}
    assert et.xz_parameter(module(other), META, OURS) is None


def test_the_first_by_file_order_then_line() -> None:
    late = param("A", "1'bx", 9)
    early = param("B", "1'bx", 3)
    early["loc"] = "f,3:1,3:4"  # project.v comes first in OURS
    got = et.xz_parameter(module(late, early), META, OURS)
    assert got is not None and (got.name, got.file) == ("B", "project.v")
    with pytest.raises(et.TreeError):
        et.xz_parameter([], META, OURS)


# -- read before write in a combinational block (early_read) -------------------------------------


def dtype(addr: str, rng: str | None = "7:0") -> dict[str, Any]:
    node: dict[str, Any] = {"type": "BASICDTYPE", "addr": addr, "keyword": "logic"}
    return node | ({"range": rng} if rng is not None else {})


TYPES = [dtype("(D8)"), dtype("(D1)", None), dtype("(D2)", "1:0")]
T = var("(T)", "t", 2, varType="VAR", dtypep="(D8)")
Y = var("(Y)", "y", 2, varType="VAR", dtypep="(D8)")
A = var("(I)", "a", 2, varType="PORT", dtypep="(D8)")
S = var("(Z)", "s", 2, varType="PORT", dtypep="(D2)")
_N = iter(range(10**6))


def r(to: str, name: str, line: int, access: str = "RD") -> dict[str, Any]:
    return ref(f"(v{next(_N)})", to, name, line, access=access)


def w(to: str, name: str, line: int) -> dict[str, Any]:
    return r(to, name, line, "WR")


def sel(inner: dict[str, Any], lsb: int | dict[str, Any], width: int) -> dict[str, Any]:
    low = const(f"32'h{lsb:x}") if isinstance(lsb, int) else lsb
    return {"type": "SEL", "fromp": [inner], "lsbp": [low], "widthConst": width}


def assign(lhs: dict[str, Any], rhs: dict[str, Any], kind: str = "ASSIGN") -> dict[str, Any]:
    return {"type": kind, "lhsp": [lhs], "rhsp": [rhs]}


def if_(cond: dict[str, Any], then: list[Any], other: list[Any] | None = None) -> dict[str, Any]:
    return {"type": "IF", "condp": [cond], "thensp": then, "elsesp": other or []}


def item(stmts: list[Any], *labels: str) -> dict[str, Any]:
    return {"type": "CASEITEM", "condsp": [const(x) for x in labels], "stmtsp": stmts}


def case(*items: dict[str, Any], kwd: str = "case") -> dict[str, Any]:
    expr = r("(Z)", "s", 3) | {"dtypep": "(D2)"}
    return {"type": "CASE", "kwd": kwd, "exprp": [expr], "itemsp": list(items)}


def comb(*stmts: dict[str, Any], keyword: str = "always_comb", **more: Any) -> dict[str, Any]:
    return {"type": "ALWAYS", "keyword": keyword, "loc": "e,2:3,2:9", "stmtsp": list(stmts), **more}


def early(*stmts: dict[str, Any], **more: Any) -> et.EarlyRead | None:
    tree = module(*TYPES, T, Y, A, S, comb(*stmts, **more))
    return et.early_read(tree, META, OURS)


def write_t_first() -> dict[str, Any]:
    return assign(w("(T)", "t", 3), r("(I)", "a", 3))


def read_t(line: int = 9) -> dict[str, Any]:
    return assign(w("(Y)", "y", line), r("(T)", "t", line))


def test_a_read_after_the_write_is_fine_and_before_it_is_refused() -> None:
    assert early(write_t_first(), read_t()) is None
    got = early(read_t(3), assign(w("(T)", "t", 4), r("(I)", "a", 4)))
    assert got is not None and (got.name, got.file, got.line) == ("t", "core.v", 3)
    assert "on some path through the block" in got.sentence
    assert "Write each signal at the top of the block" in got.sentence


def test_a_variable_the_block_does_not_write_may_be_read_anywhere() -> None:
    assert early(assign(w("(Y)", "y", 3), r("(I)", "a", 3))) is None


def test_an_if_keeps_only_what_both_branches_write() -> None:
    cond = r("(I)", "a", 3)
    both = if_(cond, [write_t_first()], [write_t_first()])
    assert early(both, read_t()) is None
    got = early(if_(cond, [write_t_first()]), read_t())  # no else: the old t on that path
    assert got is not None and got.line == 9
    got = early(if_(cond, [write_t_first()], [assign(w("(Y)", "y", 5), r("(I)", "a", 5))]),
                read_t())  # fmt: skip
    assert got is not None


def test_a_case_keeps_what_every_item_writes_and_a_case_that_may_not_match_nothing() -> None:
    every = [item([write_t_first()], v) for v in ("2'h0", "2'h1", "2'h2", "2'h3")]
    assert early(case(*every), read_t()) is None  # the labels cover every value of `s`
    assert early(case(item([write_t_first()], "2'h0"), item([write_t_first()])), read_t()) is None
    # rcasenodef: no default and labels that do not cover `s`: nothing in the case may run
    got = early(case(*every[:3]), read_t())
    assert got is not None and got.line == 9
    got = early(case(every[0], item([])), read_t())  # the default does not write it
    assert got is not None


def test_a_casez_label_with_wildcards_covers_every_value_it_matches() -> None:
    assert early(case(item([write_t_first()], "2'b1?"), item([write_t_first()], "2'b0?"),
                      kwd="casez"), read_t()) is None  # fmt: skip
    # in a plain case a z label never matches: not a full case
    got = early(case(item([write_t_first()], "2'b1z"), item([write_t_first()], "2'b0z")), read_t())
    assert got is not None


def test_constant_selects_count_bit_by_bit() -> None:
    low = assign(sel(w("(T)", "t", 3), 0, 4), r("(I)", "a", 3))
    high = assign(sel(w("(T)", "t", 4), 4, 4), r("(I)", "a", 4))
    assert early(low, high, read_t()) is None  # pieces that add up to all of t
    assert early(low, assign(w("(Y)", "y", 9), sel(r("(T)", "t", 9), 0, 4))) is None  # the low bits
    got = early(low, assign(w("(Y)", "y", 9), sel(r("(T)", "t", 9), 4, 4)))  # rpieceread
    assert got is not None and got.name == "t"
    got = early(low, read_t())
    assert got is not None


def test_a_variable_select_and_a_nonblocking_write_add_nothing() -> None:
    varsel = assign(sel(w("(T)", "t", 3), r("(I)", "a", 3), 8), r("(I)", "a", 3))
    assert early(varsel, read_t()) is not None
    nba = assign(w("(T)", "t", 3), r("(I)", "a", 3), "ASSIGNDLY")
    assert early(nba, read_t()) is not None  # rnbacomb


def test_a_concatenation_target_writes_every_part() -> None:
    both = {"type": "CONCAT", "lhsp": [w("(T)", "t", 3)], "rhsp": [w("(Y)", "y", 3)]}
    assert early(assign(both, r("(I)", "a", 3)), read_t()) is None


def test_a_loop_body_starts_from_what_was_written_before_and_adds_nothing_after() -> None:
    body = [{"type": "LOOPTEST", "condp": [r("(I)", "a", 4)]}, read_t(5), write_t_first()]
    got = early({"type": "LOOP", "stmtsp": body})  # vloopit: a later pass reads the old t
    assert got is not None and got.line == 5
    assert early(write_t_first(), {"type": "LOOP", "stmtsp": body}) is None  # written before
    loop = {"type": "LOOP", "stmtsp": [write_t_first()]}
    assert early(loop, read_t()) is not None  # the loop may run zero times
    with pytest.raises(et.TreeError):
        early({"type": "LOOP", "stmtsp": [], "elsesp": [write_t_first()]})


def test_a_disable_keeps_what_every_way_out_of_the_block_wrote() -> None:
    go = {"type": "JUMPGO", "blockp": "(J)"}
    jump = {"type": "JUMPBLOCK", "addr": "(J)",
            "stmtsp": [if_(r("(I)", "a", 3), [go]), write_t_first()]}  # fmt: skip
    got = early(jump, read_t())  # rbreak: the jump skips the write
    assert got is not None
    with pytest.raises(et.TreeError):
        early({"type": "JUMPGO", "blockp": "(nowhere)"})


def test_an_output_argument_never_counts_as_written() -> None:
    """Round 13 w01ifctl: Verilator writes `if (c) y = f(a, t); else y = 0;` as a ternary, so a
    call can sit on one path of a statement. `equiv_sv` refuses such arguments; the walk counts
    none of them as surely written (and an inout actual is not a read it could check)."""
    task = {"type": "TASKREF", "name": "fill", "taskp": "(F)",
            "argsp": [{"type": "ARG", "exprp": [w("(T)", "t", 3)]}]}  # fmt: skip
    assert early({"type": "STMTEXPR", "exprp": [task]}, read_t()) is not None
    call = {"type": "FUNCREF", "name": "f", "taskp": "(F)",
            "argsp": [{"type": "ARG", "exprp": [w("(T)", "t", 3)]}]}  # fmt: skip
    ternary = {"type": "COND", "condp": [r("(I)", "a", 3)], "thenp": [call],
               "elsep": [{"type": "CONST", "name": "8'h0"}]}  # fmt: skip
    assert early(assign(w("(Y)", "y", 3), ternary), read_t()) is not None


def test_a_named_block_local_and_a_hierarchical_read_are_judged_too() -> None:
    begin = {"type": "BEGIN", "declsp": [var("(L)", "l", 3, dtypep="(D1)")],
             "stmtsp": [read_t(4), write_t_first()]}  # fmt: skip
    assert early(begin) is not None
    xref = r("(T)", "t", 4) | {"type": "VARXREF", "dotted": "top"}
    got = early(assign(w("(Y)", "y", 4), xref), write_t_first())  # h06selfdot: `top.t`
    assert got is not None and got.name == "t"


def test_only_combinational_blocks_are_walked() -> None:
    late = (read_t(3), write_t_first())
    for keyword in ("always_ff", "always_latch", "cont_assign"):
        assert early(*late, keyword=keyword) is None
    edge = [{"type": "SENTREE", "sensesp": [{"type": "SENITEM", "edgeType": "POS"}]}]
    assert early(*late, keyword="always", sentreep=edge) is None
    level = [{"type": "SENTREE", "sensesp": [{"type": "SENITEM", "edgeType": "CHANGED"}]}]
    assert early(*late, keyword="always", sentreep=level) is not None  # MyHDL's @(a, b)
    assert early(*late, keyword="always") is not None  # @*
    with pytest.raises(et.TreeError):
        early(*late, keyword="always_strange")
    odd = [{"type": "SENTREE", "sensesp": [{"type": "SENITEM", "edgeType": "ODD"}]}]
    with pytest.raises(et.TreeError):
        early(*late, keyword="always", sentreep=odd)


@pytest.mark.parametrize(
    "stmt",
    [
        {"type": "WAIT"},
        {"type": "FORK"},
        assign(w("(T)", "t", 3), r("(I)", "a", 3)) | {"timingControlp": [{"type": "DELAY"}]},
        {"type": "BEGIN", "declsp": [{"type": "TYPEDEF"}], "stmtsp": []},
        case(item([write_t_first()])) | {"notParallelp": [{"type": "CONST"}]},
    ],
)
def test_a_statement_the_walk_does_not_know_raises(stmt: dict[str, Any]) -> None:
    with pytest.raises(et.TreeError):
        early(stmt)


def test_an_unresolved_or_strange_reference_raises() -> None:
    with pytest.raises(et.TreeError):
        early(assign(w("(nothing)", "t", 3), r("(I)", "a", 3)))
    with pytest.raises(et.TreeError):
        early(write_t_first(), assign(w("(Y)", "y", 4), r("(T)", "t", 4, access="ODD")))


# -- read before write: every place a statement reads (round 14 g23, g24, g25, g36, g39) ---------


def half_t() -> dict[str, Any]:
    """`if (a) t = a;`: `t` is written on one path only, so a later read can see the old `t`."""
    return if_(r("(I)", "a", 3), [write_t_first()])


def to_y(line: int) -> dict[str, Any]:
    return assign(w("(Y)", "y", line), r("(I)", "a", line))


def test_an_if_condition_reads_its_signals() -> None:
    """g24: `if (t)` after a write of `t` on one path only reads the old `t`."""
    got = early(half_t(), if_(r("(T)", "t", 4), [to_y(5)], [to_y(6)]))
    assert got is not None and (got.name, got.line) == ("t", 4)
    assert early(write_t_first(), if_(r("(T)", "t", 4), [to_y(5)], [to_y(6)])) is None


def test_a_case_label_and_a_case_expression_read_their_signals() -> None:
    """g23: a case item labelled `t`; g36: `case (t)`."""
    label = {"type": "CASEITEM", "condsp": [r("(T)", "t", 4)], "stmtsp": [to_y(4)]}
    got = early(half_t(), case(label, item([to_y(5)])))
    assert got is not None and (got.name, got.line) == ("t", 4)
    on_t = r("(T)", "t", 4) | {"dtypep": "(D8)"}
    by_t = {"type": "CASE", "kwd": "case", "exprp": [on_t], "itemsp": [item([to_y(5)])]}
    got = early(half_t(), by_t)
    assert got is not None and (got.name, got.line) == ("t", 4)
    assert early(write_t_first(), by_t) is None


def test_the_index_of_a_target_reads_its_signals() -> None:
    """g25: `y[t] = 1'b1` reads `t` to know which bit it writes."""
    target = {"type": "SEL", "fromp": [w("(Y)", "y", 4)], "lsbp": [r("(T)", "t", 4)],
              "widthConst": 1}  # fmt: skip
    got = early(half_t(), assign(target, const("1'h1", 4)))
    assert got is not None and (got.name, got.line) == ("t", 4)
    assert early(write_t_first(), assign(target, const("1'h1", 4))) is None


ARRAY = {"type": "UNPACKARRAYDTYPE", "addr": "(DA)", "declRange": "[0:2]", "refDTypep": "(D8)"}
M = var("(MA)", "m", 2, varType="VAR", dtypep="(DA)")


def element(index: int, access: str, line: int) -> dict[str, Any]:
    ref_m = ref(f"(m{next(_N)})", "(MA)", "m", line, access=access, dtypep="(DA)")
    return {"type": "ARRAYSEL", "fromp": [ref_m], "bitp": [const(f"2'h{index}", line)],
            "dtypep": "(D8)"}  # fmt: skip


def array_early(*stmts: dict[str, Any]) -> et.EarlyRead | None:
    tree = module(*TYPES, ARRAY, T, Y, A, S, M, comb(*stmts))
    return et.early_read(tree, META, OURS)


def test_an_array_element_counts_its_own_bits_at_its_own_place() -> None:
    """g39: `m[0] = a; m[2] = ~a; y = m[1]; m[1] = ...`: the elements written first are 8 bits
    each at index * 8, so they do not cover `m[1]`; after all three, all of `m` is written."""
    first = assign(element(0, "WR", 3), r("(I)", "a", 3))
    last = assign(element(2, "WR", 4), r("(I)", "a", 4))
    read_m1 = assign(w("(Y)", "y", 5), element(1, "RD", 5))
    write_m1 = assign(element(1, "WR", 6), r("(I)", "a", 6))
    got = array_early(first, last, read_m1, write_m1)
    assert got is not None and (got.name, got.line) == ("m", 5)
    whole = assign(w("(Y)", "y", 7), ref("(mw)", "(MA)", "m", 7, dtypep="(DA)"))
    assert array_early(first, last, write_m1, read_m1, whole) is None
    got = array_early(first, write_m1, whole)  # m[2] is never written before the read of all m
    assert got is not None and got.line == 7


def test_a_label_wider_than_the_case_expression_does_not_make_the_case_full() -> None:
    """A 3-bit label never matches a 2-bit expression once that is 0..3: labels 4..7 cover none
    of its values, so the case may match nothing and `t` keeps its old value."""
    wide = [item([write_t_first()], f"3'h{v}") for v in (4, 5, 6, 7)]
    got = early(case(*wide), read_t())
    assert got is not None and got.line == 9
    narrow = [item([write_t_first()], f"2'h{v}") for v in (0, 1, 2, 3)]
    assert early(case(*narrow), read_t()) is None


# -- generate conditions that can be x (x_condition) ------------------------------------------


def gen_if(cond: dict[str, Any], line: int = 4) -> dict[str, Any]:
    block = {"type": "GENBLOCK", "name": "g", "loc": f"e,{line}:20,{line}:21", "itemsp": []}
    return {"type": "GENIF", "loc": f"e,{line}:3,{line}:5", "condp": [cond], "thensp": [block]}


def op(kind: str, lhs: dict[str, Any], rhs: dict[str, Any], line: int = 4) -> dict[str, Any]:
    return {"type": kind, "loc": f"e,{line}:10,{line}:11", "lhsp": [lhs], "rhsp": [rhs]}


Z_REF = ref("(Zr)", "(Zp)", "Z", 4)


def number(text: str, line: int = 4) -> dict[str, Any]:
    return const(text, line)


ONE_BY_Z = op("DIV", number("?32?sh1"), Z_REF)
MINUS_ONE = {"type": "NEGATE", "loc": "e,4:15,4:16", "lhsp": [number("?32?sh1")]}
CLOG2 = {"type": "CLOG2", "loc": "e,4:8,4:14", "lhsp": [ONE_BY_Z]}
PICK = {"type": "FUNCREF", "name": "pick", "loc": "e,4:8,4:12", "taskp": "(F)"}


@pytest.mark.parametrize(
    ("cond", "what"),
    [
        (op("EQ", ONE_BY_Z, number("?32?sh0")), "`/` or `%`"),  # b03arr: (1 / Z) == 0
        (op("MODDIV", number("?32?sh5"), number("?32?sh0")), "`/` or `%`"),  # 5 % 0
        (op("EQ", CLOG2, number("?32?sh0")), "`/` or `%`"),  # x10clogif: $clog2(1 / Z) == 0
        (op("POW", Z_REF, MINUS_ONE), "`**`"),  # b06impure: Z ** -1
        (op("POW", number("?32?sh0"), Z_REF), "`**`"),  # 0 ** N
        (PICK, "call of the function `pick`"),
    ],
)
def test_a_generate_if_condition_that_can_be_x_is_refused(cond: dict[str, Any], what: str) -> None:
    got = et.x_condition(module(gen_if(cond)), META, OURS)
    assert got is not None and (got.file, got.line) == ("core.v", 4)
    assert what in got.sentence and "condition of a generate `if`, `case` or `for`" in got.sentence
    assert "Compute the value in a localparam first" in got.sentence


@pytest.mark.parametrize(
    "cond",
    [
        op("MODDIV", Z_REF, number("?32?sh2")),  # WIDTH % 2
        op("DIV", Z_REF, number("8'h4")),  # N / 4'd4
        op("POW", number("?32?sh2"), Z_REF),  # 2 ** N
        op("POW", Z_REF, number("?32?sh3")),  # N ** 3
        op("POWSS", Z_REF, number("?32?sh3")),  # signed, non-negative exponent
        op("LT", Z_REF, number("?32?sh8")),
    ],
)
def test_a_generate_condition_with_safe_operands_passes(cond: dict[str, Any]) -> None:
    assert et.x_condition(module(gen_if(cond)), META, OURS) is None


def test_a_negative_or_unknown_exponent_or_divisor_is_refused() -> None:
    minus_one = number("?32?shffffffff")  # a signed -1
    assert et.x_condition(module(gen_if(op("POW", Z_REF, minus_one))), META, OURS) is not None
    assert et.x_condition(module(gen_if(op("DIV", Z_REF, number("4'b10z1")))), META, OURS)
    assert et.x_condition(module(gen_if(op("POW", Z_REF, number("?32?ux3")))), META, OURS)


def test_generate_case_labels_and_for_headers_are_conditions_too() -> None:
    div = op("DIV", number("?32?sh2"), Z_REF, 6)
    label = {"type": "GENCASEITEM", "loc": "e,6:13,6:14", "condsp": [number("?32?sh1", 6), div]}
    gencase = {"type": "GENCASE", "loc": "e,5:3,5:7", "exprp": [Z_REF], "itemsp": [label]}
    got = et.x_condition(module(gencase), META, OURS)
    assert got is not None and got.line == 6
    on = {"type": "GENCASE", "loc": "e,5:3,5:7", "exprp": [op("MODDIV", Z_REF, Z_REF, 5)]}
    assert (got := et.x_condition(module(on), META, OURS)) is not None and got.line == 5
    for part in ("initsp", "condp", "incsp"):
        loop = {"type": "GENFOR", "loc": "e,7:3,7:6", part: [op("DIV", Z_REF, Z_REF, 7)]}
        got = et.x_condition(module({"type": "GENBLOCK", "genforp": [loop]}), META, OURS)
        assert got is not None and got.line == 7, part


def test_an_x_operator_outside_a_generate_condition_is_not_this_rule() -> None:
    """A parameter's value and an expression in a block are judged by their values elsewhere
    (`xz_parameter`, the simulator's parameters)."""
    value = var("(P)", "P", 3, varType="LPARAM", isParam=True,
                valuep=[op("DIV", number("?32?sh1"), Z_REF, 3)])  # fmt: skip
    assert et.x_condition(module(value), META, OURS) is None


def test_the_first_x_condition_by_file_order_then_line_and_other_files_are_not_judged() -> None:
    late = gen_if(op("DIV", number("?32?sh1"), Z_REF, 9), 9)
    first = gen_if(op("DIV", number("?32?sh1"), Z_REF) | {"loc": "f,2:10,2:11"})  # project.v
    got = et.x_condition(module(late, first), META, OURS)
    assert got is not None and (got.file, got.line) == ("project.v", 2)
    std = gen_if(op("DIV", number("?32?sh1"), Z_REF) | {"loc": "d,3:10,3:11"})  # Verilator's own
    assert et.x_condition(module(std), META, OURS) is None
    with pytest.raises(et.TreeError):
        et.x_condition([], META, OURS)
    odd = {"type": "GENIF", "loc": "e,4:3,4:5", "condp": {"odd": 1}}
    with pytest.raises(et.TreeError):
        et.x_condition(module(odd), META, OURS)


# -- the value of every parameter (round 15) --------------------------------------------------


def elab_param(addr: str, name: str, line: int, value: str, **more: Any) -> dict[str, Any]:
    """A parameter as Verilator's elaborated tree has it (`valuep`: one CONST)."""
    return var(
        addr, name, line, varType="LPARAM", isParam=True,
        valuep=[{"type": "CONST", "name": value, "loc": f"e,{line}:9,{line}:10"}], **more,
    )  # fmt: skip


def mod(addr: str, name: str, level: int, *kids: dict[str, Any], kind: str = "MODULE") -> dict:
    return {"type": kind, "addr": addr, "name": name, "level": level, "stmtsp": list(kids)}


def cell(name: str, to: str, *bounds: str) -> dict[str, Any]:
    """An instance; with `bounds` (`"32'sh3", "32'sh0"`) an instance array, which Verilator keeps
    as one CELL with a range (`sub u [3:0] (...)`)."""
    got = {"type": "CELL", "name": name, "modp": to, "loc": "e,1:1,1:2"}
    if bounds:
        got["rangep"] = [{"type": "RANGE", "leftp": [const(bounds[0])],
                          "rightp": [const(bounds[1])]}]  # fmt: skip
    return got


# The shape of round 15's probe: `core` instantiates `mid` twice with Z = 1 (Verilator shares
# one copy) and once with Z = 5; each `mid` holds one `leaf`, whose K follows Z. Round 16: `core`
# also holds an array of two `mid`s with Z = 1 (`mid ma [1:0]`) inside a generate block.
PARAM_TREE = {
    "type": "NETLIST",
    "modulesp": [
        mod("(T)", "tt_um_core", 1, cell("core", "(C)")),
        mod("(P)", "pk", 2, elab_param("(PP)", "PP", 3, "32'sh7"), kind="PACKAGE"),
        mod(
            "(C)",
            "core",
            2,
            cell("m0", "(M)"),
            cell("m1", "(M)"),
            cell("m2", "(M5)"),
            {
                "type": "GENBLOCK",
                "name": "genblk1",
                "stmtsp": [
                    elab_param("(Q)", "Q", 33, "8'h33"),
                    cell("ma", "(M)", "32'sh1", "32'sh0"),
                ],
            },
            var("(G)", "g", 14, varType="GENVAR"),
            elab_param("(R)", "R", 21, "1.5"),
            elab_param("(N)", "N", 22, "4'he"),
        ),
        mod("(M)", "mid", 3, elab_param("(Z1)", "Z", 18, "32'sh1"), cell("l0", "(L1)")),
        mod("(M5)", "mid__Z5", 3, elab_param("(Z5)", "Z", 18, "32'sh5"), cell("l0", "(L5)")),
        mod(
            "(L1)",
            "leaf__K1",
            4,
            elab_param("(K1)", "K", 6, "32'sh1"),
            func("(F1)", "f", 12, elab_param("(FL1)", "FL", 13, "32'sh5")),
        ),
        mod(
            "(L5)",
            "leaf__K5",
            4,
            elab_param("(K5)", "K", 6, "32'sh5"),
            func("(F5)", "f", 12, elab_param("(FL5)", "FL", 13, "32'sh5")),
        ),
        mod("(U)", "unused", 2, elab_param("(UK)", "UK", 40, "32'sh9")),
    ],
}


TOP, CORE = ("tt_um_core",), ("tt_um_core", "core")
GEN = (*CORE, "genblk1")


def test_every_parameter_value_has_the_path_of_each_module_instance() -> None:
    """Every instance and loop pass, named as a simulator names it: through every level, the
    blocks around a CELL and each element of an instance array."""
    got = {k: v and sorted(v) for k, v in et.parameter_values(PARAM_TREE, META, OURS).items()}
    mids = [(*CORE, "m0"), (*CORE, "m1"), (*GEN, "ma[0]"), (*GEN, "ma[1]")]  # the copy with Z = 1
    assert got == {
        k: v and sorted(v)
        for k, v in {
            ("core.v", 3, "PP"): [(("pk",), 7)],  # a package once, by its name
            ("core.v", 33, "Q"): [(GEN, 0x33)],
            ("core.v", 21, "R"): None,  # a real is not a plain number
            ("core.v", 22, "N"): [(CORE, 14)],
            ("core.v", 18, "Z"): [(m, 1) for m in mids] + [((*CORE, "m2"), 5)],
            ("core.v", 6, "K"): [((*m, "l0"), 1) for m in mids] + [((*CORE, "m2", "l0"), 5)],
            # inside a function too
            ("core.v", 13, "FL"): [((*m, "l0", "f"), 5) for m in [*mids, (*CORE, "m2")]],
            ("core.v", 40, "UK"): [],  # nothing instantiates it
        }.items()
    }  # the genvar has no value: left out


def test_a_value_is_negative_when_its_type_is_signed() -> None:
    """Verilator 5.046 writes `32'hfffffffe` for -2 in the final tree: the sign comes from the
    parameter's data type (through a typedef), or from an `'s` on the constant when the type is
    not known."""
    types = [
        {"type": "BASICDTYPE", "addr": "(i32)", "keyword": "integer", "signed": True},
        {"type": "BASICDTYPE", "addr": "(u8)", "keyword": "logic"},
        {"type": "REFDTYPE", "addr": "(ref)", "refDTypep": "(i32)"},
    ]
    tree = {
        "type": "NETLIST",
        "modulesp": [
            mod(
                "(T)", "tt_um_core", 1,
                elab_param("(A)", "A", 2, "32'hfffffffe", dtypep="(i32)"),
                elab_param("(B)", "B", 3, "8'hfe", dtypep="(u8)"),
                elab_param("(D)", "D", 4, "32'hfffffffe", dtypep="(ref)"),
                elab_param("(E)", "E", 5, "8'shfe"),
                elab_param("(F)", "F", 6, "8'shfe", dtypep="(u8)"),
            ),
        ],
        "typeTablep": [{"type": "TYPETABLE", "typesp": types}],
    }  # fmt: skip
    got = et.parameter_values(tree, META, OURS)
    assert got == {
        ("core.v", 2, "A"): [(TOP, -2)],
        ("core.v", 3, "B"): [(TOP, 0xFE)],
        ("core.v", 4, "D"): [(TOP, -2)],
        ("core.v", 5, "E"): [(TOP, 0xFE)],  # no data type: not signed
        ("core.v", 6, "F"): [(TOP, 0xFE)],  # the parameter's type decides
    }


def test_each_value_has_the_width_of_its_constant_and_the_sign_of_its_type() -> None:
    """Round 23 wcat: an untyped `8'hff + 8'h00` is `8'hff` in Verilator's final tree (8 bits,
    which Yosys and the gates keep; Icarus makes it 9). `parameter_shapes` gives every value
    that `parameter_values` gives, with the same paths, and None where it gives None."""
    types = [
        {"type": "BASICDTYPE", "addr": "(i32)", "keyword": "integer", "signed": True},
        {"type": "BASICDTYPE", "addr": "(u8)", "keyword": "logic"},
        {"type": "REFDTYPE", "addr": "(ref)", "refDTypep": "(i32)"},
    ]
    tree = {
        "type": "NETLIST",
        "modulesp": [
            mod(
                "(T)", "tt_um_core", 1,
                elab_param("(A)", "A", 2, "32'hfffffffe", dtypep="(i32)"),
                elab_param("(B)", "B", 3, "8'hff", dtypep="(u8)"),
                elab_param("(D)", "D", 4, "33'h1_0000_0000", dtypep="(ref)"),
                elab_param("(E)", "E", 5, "8'shfe"),  # no data type: not signed
                elab_param("(R)", "R", 6, "1.5"),
                {**elab_param("(X)", "X", 7, "4'hx"), "dtypep": "(u8)"},
            ),
        ],
        "typeTablep": [{"type": "TYPETABLE", "typesp": types}],
    }  # fmt: skip
    assert et.parameter_shapes(tree, META, OURS) == {
        ("core.v", 2, "A"): [(TOP, (32, True))],
        ("core.v", 3, "B"): [(TOP, (8, False))],
        ("core.v", 4, "D"): [(TOP, (33, True))],
        ("core.v", 5, "E"): [(TOP, (8, False))],
        ("core.v", 6, "R"): None,
        ("core.v", 7, "X"): None,
    }
    values = et.parameter_values(PARAM_TREE, META, OURS)
    shapes = et.parameter_shapes(PARAM_TREE, META, OURS)
    assert shapes.keys() == values.keys()
    for key, got in values.items():
        assert (got is None) == (shapes[key] is None), key
        assert [p for p, _ in got or []] == [p for p, _ in shapes[key] or []], key
    assert shapes[("core.v", 33, "Q")] == [(GEN, (8, False))]
    assert shapes[("core.v", 40, "UK")] == []  # nothing instantiates it


@pytest.mark.parametrize(
    ("bounds", "names"),
    [
        (("32'sh3", "32'sh0"), ["u[0]", "u[1]", "u[2]", "u[3]"]),
        (("32'sh1", "32'sh3"), ["u[1]", "u[2]", "u[3]"]),  # [1:3]
        (("32'shffffffff", "32'sh0"), ["u[-1]", "u[0]"]),  # [-1:0]
    ],
)
def test_an_instance_array_counts_each_element_by_its_index(
    bounds: tuple[str, str], names: list[str]
) -> None:
    """Round 16 s37, t01, g2: `sub #(.W(2)) u [3:0] (...)` is one CELL in Verilator's tree and four
    instances (`u[0]` to `u[3]`) in a simulator's."""
    tree = {
        "type": "NETLIST",
        "modulesp": [
            mod("(T)", "tt_um_core", 1, cell("u", "(S)", *bounds)),
            mod("(S)", "sub", 2, elab_param("(W)", "W", 4, "32'sh2")),
        ],
    }
    got = et.parameter_values(tree, META, OURS)
    assert got == {("core.v", 4, "W"): [((*TOP, n), 2) for n in names]}
    unreadable = cell("u", "(S)", *bounds)
    unreadable["rangep"][0]["leftp"] = [{"type": "VARREF", "name": "N"}]
    tree["modulesp"][0]["stmtsp"] = [unreadable]
    with pytest.raises(et.TreeError, match="not a plain number"):
        et.parameter_values(tree, META, OURS)


def test_only_our_files_and_plain_values_count() -> None:
    tree = {
        "type": "NETLIST",
        "modulesp": [
            mod(
                "(T)", "tt_um_core", 1,
                elab_param("(A)", "A", 2, "32'sh1"),
                {**elab_param("(B)", "B", 3, "32'sh1"), "loc": "d,3:1,3:2"},  # <verilated_std>
                {**elab_param("(C)", "C", 4, "8'h1"), "valuep": []},
                {**elab_param("(D)", "D", 5, "8'h1"), "valuep": [{"type": "INITARRAY"}]},
                elab_param("(E)", "E", 6, "8'hx1"),
                {**elab_param("(F)", "F", 7, "32'h2"), "isParam": None, "varType": "GPARAM"},
            ),
        ],
    }  # fmt: skip
    got = et.parameter_values(tree, META, OURS)
    assert got == {
        ("core.v", 2, "A"): [(TOP, 1)],
        ("core.v", 4, "C"): None,
        ("core.v", 5, "D"): None,
        ("core.v", 6, "E"): None,  # an x is `xz_parameter`'s
        ("core.v", 7, "F"): [(TOP, 2)],
    }


@pytest.mark.parametrize(
    ("modules", "why"),
    [
        ([mod("(T)", "t", 1, cell("c", "(nowhere)"))], "names no module"),
        ([mod("(T)", "t", 2)], "exactly one top module"),
        ([mod("(T)", "t", 1), mod("(S)", "s", 1)], "exactly one top module"),
        ([mod("(T)", "t", 1, cell("a", "(A)")), mod("(A)", "a", 2, cell("a", "(A)"))], "loop"),
        ([mod("(T)", "t", 1, cell("u", "(T)", "32'sh0", "32'sh186a0"))], "more than"),
        (
            [
                mod("(T)", "t", 1, {**cell("u", "(A)", "32'sh1", "32'sh0"), "rangep": [{}, {}]}),
                mod("(A)", "a", 2),
            ],
            "unreadable range",
        ),
    ],
)
def test_an_instance_count_that_cannot_be_trusted_raises(modules: list[Any], why: str) -> None:
    with pytest.raises(et.TreeError, match=why):
        et.parameter_values({"type": "NETLIST", "modulesp": modules}, META, OURS)
    with pytest.raises(et.TreeError):
        et.parameter_values({"type": "NETLIST"}, META, OURS)


# -- constant arguments to a function that can give x (constant_call) ----------------------------

VEC = var("(V)", "v", 5, varType="PORT", isFuncLocal=True, direction="INPUT")
IDX = var("(K)", "k", 5, varType="PORT", isFuncLocal=True, direction="INPUT")
PICKED = var("(PR)", "pick", 5, isFuncReturn=True, isFuncLocal=True)
NAMED = [
    var("(LP)", "P", 2, varType="LPARAM", isParam=True, valuep=[const("32'h6", 2)]),
    var("(GP)", "W", 2, varType="GPARAM", isParam=True, valuep=[const("32'h6", 2)]),
    var("(GV)", "g", 2, varType="GENVAR"),
    var("(WS)", "w", 2, varType="VAR"),
]


def set_return(value: dict[str, Any], to: str = "(PR)", name: str = "pick") -> dict[str, Any]:
    target = ref(f"({next(_N)})", to, name, 6, access="WR")
    return {"type": "ASSIGN", "rhsp": [value], "lhsp": [target]}


def bit(index: dict[str, Any], kind: str = "SELBIT", access: str = "RD") -> dict[str, Any]:
    """`v[index]`, read (or written, with access="WR")."""
    return {"type": kind, "fromp": [ref(f"({next(_N)})", "(V)", "v", 6, access=access)],
            "bitp": [index], "loc": "e,6:5,6:8"}  # fmt: skip


def pick(body: dict[str, Any], name: str = "pick", addr: str = "(F)") -> dict[str, Any]:
    """`function pick(input [3:0] v, input integer k)` with `body`."""
    return func(addr, name, 5, PICKED, VEC, IDX, body)


def k() -> dict[str, Any]:
    return ref(f"({next(_N)})", "(K)", "k", 6)


def pick_call(*args: dict[str, Any], line: int = 9, to: str = "(F)") -> dict[str, Any]:
    """`pick(args...)` at `line`, as Verilator links it (each argument under an ARG)."""
    return call(f"({next(_N)})", to, "pick", line) | {
        "argsp": [{"type": "ARG", "exprp": [a]} for a in args]
    }


def named(addr: str, name: str) -> dict[str, Any]:
    return ref(f"({next(_N)})", addr, name, 9)


def constant(*kids: dict[str, Any]) -> et.Unknown | None:
    return et.constant_call(module(*NAMED, *kids), META, OURS)


READS_AT_K = pick(set_return({"type": "LOGNOT", "lhsp": [bit(k())]}))  # pick = !v[k]


@pytest.mark.parametrize(
    "args",
    [
        [const("4'ha"), const("32'h6")],  # pick(4'b1010, 6)
        [const("4'ha"), named("(LP)", "P")],  # a localparam
        [const("4'ha"), named("(GP)", "W")],  # a parameter
        [const("4'ha"), named("(GV)", "g")],  # a genvar (in a generate loop)
        [const("4'ha"), {"type": "ATTROF", "fromp": [named("(WS)", "w")]}],  # $bits(w)
    ],
)
def test_a_constant_call_of_a_function_that_reads_at_a_computed_index_is_refused(
    args: list[dict[str, Any]],
) -> None:
    got = constant(READS_AT_K, pick_call(*args))
    assert got is not None and (got.name, got.file, got.line) == ("pick", "core.v", 9)
    assert "calls the function `pick` with constant arguments" in got.sentence
    assert "Compute it in a localparam first (`localparam integer V = pick(...);`" in got.sentence


@pytest.mark.parametrize(
    "body",
    [
        pick(set_return(bit(k(), kind="SELPLUS") | {"widthp": [const("32'h2")]})),  # v[k +: 2]
        pick(set_return({"type": "DIV", "lhsp": [const("32'h8")], "rhsp": [k()]})),  # 8 / k
        pick(set_return({"type": "MODDIV", "lhsp": [k()], "rhsp": [const("32'h0")]})),  # k % 0
    ],
)
def test_a_part_select_or_a_divide_in_the_function_counts_too(body: dict[str, Any]) -> None:
    assert constant(body, pick_call(const("4'ha"), const("32'h6"))) is not None


def test_a_function_that_calls_such_a_function_counts_too_and_so_does_an_unknown_one() -> None:
    outer = func("(O)", "outer", 7, set_return(pick_call(ref("(o1)", "(K)", "k", 8), line=8)))
    got = constant(READS_AT_K, outer, call("(c)", "(O)", "outer", 11))
    assert got is not None and (got.name, got.line) == ("outer", 11)
    assert constant(pick_call(const("4'ha"), line=12, to="(nowhere)")) is not None


@pytest.mark.parametrize(
    "where",
    [
        # localparam integer V = pick(4'b1010, 6);
        var(
            "(L)",
            "V",
            9,
            varType="LPARAM",
            isParam=True,
            valuep=[pick_call(const("4'ha"), const("32'h6"))],
        ),
        # child #(.W(pick(4'b1010, 6))) u (...); (and `defparam u.W = ...`, the same PIN)
        {
            "type": "CELL",
            "name": "u",
            "paramsp": [
                {
                    "type": "PIN",
                    "modVarp": "(GP)",
                    "exprp": [pick_call(const("4'ha"), const("32'h6"))],
                }
            ],
        },
    ],
)
def test_a_constant_call_in_a_parameter_value_is_fine(where: dict[str, Any]) -> None:
    assert constant(READS_AT_K, where) is None


def override(to: str | None) -> dict[str, Any]:
    """`#(.X(pick(4'b1010, 6)))` whose PIN points at `to` (`modVarp`)."""
    pin = {"type": "PIN", "exprp": [pick_call(const("4'ha"), const("32'h6"))]}
    return {"type": "CELL", "name": "u", "paramsp": [pin | ({"modVarp": to} if to else {})]}


RANGED = {
    "type": "BASICDTYPE",
    "rangep": [
        {
            "type": "RANGE",
            "leftp": [pick_call(const("4'ha"), const("32'h6"))],
            "rightp": [const("32'h0")],
        }
    ],
}


@pytest.mark.parametrize(
    "where",
    [
        # round 16 brange3 and p13typ64: `localparam logic [pick(4'b1010, 6):0] V = ...;` the
        # range is not compared (only the value), so a call there is refused
        var(
            "(L)",
            "V",
            9,
            varType="LPARAM",
            isParam=True,
            childDTypep=[RANGED],
            valuep=[const("32'h1")],
        ),
        var("(W2)", "w2", 9, varType="WIRE", childDTypep=[RANGED]),  # `wire [pick(...):0] w2`
        override(None),  # an override whose parameter does not resolve
        override("(WS)"),  # ... or is not a parameter
        override("(GV)"),  # ... a genvar
    ],
)
def test_a_constant_call_in_a_declarations_range_or_an_unknown_override_is_refused(
    where: dict[str, Any],
) -> None:
    got = constant(READS_AT_K, where)
    assert got is not None and (got.name, got.line) == ("pick", 9)
    assert "outside the value of a parameter or localparam" in got.sentence


def test_the_parameters_whose_value_holds_such_a_call_are_named() -> None:
    """Round 16 breal: `localparam real R = pick(4'b1010, 6);` is allowed by `constant_call`
    only if R's value is compared: `valued_calls` names R (for an override, the child's
    parameter), and `equiv_icarus.uncompared` refuses it when R is a real."""
    value = var("(R)", "R", 7, varType="LPARAM", isParam=True,
                valuep=[pick_call(const("4'ha"), const("32'h6"))])  # fmt: skip
    other = var("(O)", "O", 8, varType="LPARAM", isParam=True,
                valuep=[pick_call(named("(WS)", "w"))])  # fmt: skip
    tree = module(*NAMED, READS_AT_K, value, other, override("(GP)"), override("(WS)"))
    assert et.valued_calls(tree, META, OURS) == {("core.v", 7, "R"), ("core.v", 2, "W")}
    assert et.valued_calls(tree, META, ["project.v"]) == set()
    safe = pick(set_return(bit(const("32'h2"))))  # v[2]: no call can give x
    assert et.valued_calls(module(*NAMED, safe, value), META, OURS) == set()
    with pytest.raises(et.TreeError):
        et.valued_calls([], META, OURS)


@pytest.mark.parametrize(
    "arg",
    [
        named("(WS)", "w"),  # a signal: the proof sees every value
        {"type": "ADD", "lhsp": [const("32'h1")], "rhsp": [named("(WS)", "w")]},
        {"type": "PARSEREF", "name": "x"},  # a name not resolved (a module nothing uses)
        ref("(dangling)", "(no var)", "y", 9),
    ],
)
def test_a_call_with_an_argument_that_reads_a_variable_is_fine(arg: dict[str, Any]) -> None:
    assert constant(READS_AT_K, pick_call(const("4'ha"), arg)) is None


@pytest.mark.parametrize(
    "body",
    [
        # low_mask(n) = (1 << n) - 1: no select, no divide
        pick(
            set_return(
                {
                    "type": "SUB",
                    "lhsp": [{"type": "SHIFTL", "lhsp": [const("32'h1")], "rhsp": [k()]}],
                    "rhsp": [const("32'h1")],
                }
            )
        ),
        pick(set_return(bit(const("32'h2")))),  # v[2]: a constant index
        pick(set_return({"type": "DIV", "lhsp": [k()], "rhsp": [const("32'h4")]})),  # k / 4
        pick({"type": "ASSIGN", "rhsp": [const("1'h1")], "lhsp": [bit(k(), access="WR")]}),
    ],
)
def test_a_function_without_a_computed_read_index_or_a_divide_is_fine_anywhere(
    body: dict[str, Any],
) -> None:
    assert constant(body, pick_call(const("4'ha"), const("32'h6"))) is None


def test_the_first_constant_call_by_file_order_then_line() -> None:
    late = pick_call(const("4'ha"), const("32'h1"), line=4)
    first = pick_call(const("4'ha"), const("32'h2"), line=20) | {"loc": "f,20:5,20:8"}
    std = pick_call(const("4'ha"), const("32'h3"), line=1) | {"loc": "d,1:5,1:8"}
    got = constant(READS_AT_K, late, first, std)
    assert got is not None and (got.file, got.line) == ("project.v", 20)
    assert constant(READS_AT_K, std) is None  # another file's call is not judged
    with pytest.raises(et.TreeError):
        et.constant_call([], META, OURS)


# -- a select proven inside the range is not an x source (round 16) -------------------------------
# The shapes are those of Verilator's tree before V3Param: a typed VAR keeps its type in
# `childDTypep` (a BASICDTYPE with a RANGE of constants, or an integer keyword with `range`), a
# `for` loop is `i = C0` right before a LOOP of [LOOPTEST, body..., `i = i +- 1`].


def basic(left: int | dict[str, Any] = 7, right: int = 0, **more: Any) -> dict[str, Any]:
    """`logic [left:right]` (a constant expression as `left` too)."""
    high = const(f"?32?sh{left:x}") if isinstance(left, int) else left
    rng = {"type": "RANGE", "leftp": [high], "rightp": [const(f"?32?sh{right:x}")]}
    return {"type": "BASICDTYPE", "keyword": "logic", "rangep": [rng], **more}


INTEGER = {"type": "BASICDTYPE", "keyword": "integer", "range": "31:0", "signed": True}


def local(addr: str, name: str, dtype: dict[str, Any], **more: Any) -> dict[str, Any]:
    return var(addr, name, 5, isFuncLocal=True, childDTypep=[dtype], **more)


def arg(addr: str, name: str, dtype: dict[str, Any]) -> dict[str, Any]:
    return local(addr, name, dtype, varType="PORT", direction="INPUT")


def rd(addr: str, name: str) -> dict[str, Any]:
    return ref(f"({next(_N)})", addr, name, 6)


def wr(addr: str, name: str) -> dict[str, Any]:
    return ref(f"({next(_N)})", addr, name, 6, access="WR")


def num(value: int, sized: str = "?32?sh") -> dict[str, Any]:
    return const(f"{sized}{value:x}")


def ar(kind: str, a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    return {"type": kind, "lhsp": [a], "rhsp": [b]}


def read_bit(vector: dict[str, Any], index: dict[str, Any], kind: str = "SELBIT") -> dict[str, Any]:
    return {"type": kind, "fromp": [vector], "bitp": [index], "loc": "e,6:5,6:8"}


def for_loop(
    i: str, start: dict[str, Any], test: dict[str, Any], body: list[Any], step: int = 1
) -> list[dict[str, Any]]:
    """`for (i = start; test; i = i +- 1) body` as Verilator links it: two statements."""
    bump = ar("ADD" if step > 0 else "SUB", rd(i, "i"), num(1, "?32?h"))
    stmts = [{"type": "LOOPTEST", "condp": [test]}, *body, assign(wr(i, "i"), bump)]
    return [assign(wr(i, "i"), start), {"type": "LOOP", "stmtsp": stmts}]


RET8 = local("(R8)", "f", basic(7), isFuncReturn=True)
V8 = arg("(V8)", "v", basic(7))
I32 = local("(I)", "i", INTEGER)


def f8(*body: dict[str, Any], extra: tuple[dict[str, Any], ...] = ()) -> dict[str, Any]:
    """`function [7:0] f(input [7:0] v, ...extra); integer i; body`."""
    return func("(F8)", "f", 5, RET8, V8, *extra, I32, {"type": "BEGIN", "stmtsp": list(body)})


def f8_call(*args: dict[str, Any], line: int = 9) -> dict[str, Any]:
    return call(f"({next(_N)})", "(F8)", "f", line) | {
        "argsp": [{"type": "ARG", "exprp": [a]} for a in (args or (num(0x81, "8'h"),))]
    }


def judged(fn: dict[str, Any], *more: dict[str, Any], **kw: Any) -> et.Unknown | None:
    """`constant_call` on a module with `fn` and a constant call of it (and `more`)."""
    args = kw.pop("args", ())
    return et.constant_call(module(*NAMED, fn, *more, f8_call(*args)), META, OURS, **kw)


def lt(a: dict[str, Any], b: dict[str, Any], kind: str = "LT") -> dict[str, Any]:
    return ar(kind, a, b)


def v_at(index: dict[str, Any]) -> dict[str, Any]:
    """`f = {7'd0, v[index]}`: a read of v at `index`."""
    return assign(wr("(R8)", "f"), read_bit(rd("(V8)", "v"), index))


SEGS = var("(SG)", "SEGS", 3, varType="LPARAM", isParam=True, childDTypep=[basic(111)],
           valuep=[num(0, "112'h")])  # fmt: skip
D4 = arg("(D4)", "d", basic(3))
K3 = arg("(K3)", "k", basic(2))
S4 = arg("(S4)", "s", basic(3, signed=True))
MEM = local("(MEM)", "mem", {"type": "UNPACKARRAYDTYPE", "childDTypep": [basic(7)],
                              "rangep": [{"type": "RANGE", "leftp": [num(0)],
                                          "rightp": [num(3)]}]})  # fmt: skip
WPAR = var("(WP)", "W", 2, varType="GPARAM", isParam=True, valuep=[num(8)])
TW = local("(TW)", "t", basic(ar("SUB", rd("(WP)", "W"), num(1))))
KL = local("(KL)", "k", basic(2))  # a 3-bit local, not an argument
# g(v, k) = v[k], k a 3-bit argument: proven inside g
G = func("(G)", "g", 4, local("(RG)", "g", basic(0), isFuncReturn=True), arg("(VG)", "v", basic(7)),
         arg("(KG)", "k", basic(2)), {"type": "BEGIN", "stmtsp": [assign(wr("(RG)", "g"), read_bit(
             rd("(VG)", "v"), rd("(KG)", "k")))]})  # fmt: skip


def g_of(index: dict[str, Any]) -> dict[str, Any]:
    """`f = g(v, index)` inside f."""
    args = [{"type": "ARG", "exprp": [e]} for e in (rd("(V8)", "v"), index)]
    return assign(wr("(R8)", "f"), call(f"({next(_N)})", "(G)", "g", 6) | {"argsp": args})


@pytest.mark.parametrize(
    ("fn", "known"),
    [
        # s17: SEGS[d*7 +: 7], d a 4-bit argument: 0 to 105, + 6 <= 111
        (
            f8(assign(wr("(R8)", "f"), read_bit(rd("(SG)", "SEGS"), ar("MUL", rd("(D4)", "d"),
             num(7)), "SELPLUS") | {"widthp": [num(7)]}), extra=(D4,)),
            None,
        ),
        # s46: for (i = 0; i < 8; i = i + 1) f = f ^ v[i]
        (f8(*for_loop("(I)", num(0), lt(rd("(I)", "i"), num(8)), [v_at(rd("(I)", "i"))])), None),
        # e4: for (i = 7; i >= 0; i = i - 1) ... v[i], i a signed integer
        (
            f8(*for_loop("(I)", num(7), lt(rd("(I)", "i"), num(0), "GTE"),
                         [v_at(rd("(I)", "i"))], step=-1)),
            None,
        ),
        # frev: v[7 - i] for i = 0 to 7 (the bound on the left: 8 > i)
        (
            f8(*for_loop("(I)", num(0), lt(num(8), rd("(I)", "i"), "GT"),
                         [v_at(ar("SUB", num(7), rd("(I)", "i")))])),
            None,
        ),
        # i <= 7, and the read of i in the test and the step are outside the body
        (f8(*for_loop("(I)", num(0), lt(rd("(I)", "i"), num(7), "LTE"), [v_at(rd("(I)", "i"))])),
         None),
        # mem[k[1:0]], an array of 4: a constant part-select of an argument as the index
        (
            f8(MEM, assign(wr("(R8)", "f"), read_bit(rd("(MEM)", "mem"), {
                "type": "SELEXTRACT", "fromp": [rd("(K3)", "k")], "leftp": [num(1)],
                "rightp": [num(0)]})), extra=(K3,)),
            None,
        ),
        # mem[0][k]: one element's packed range, k 3 bits
        (
            f8(MEM, assign(wr("(R8)", "f"), read_bit(read_bit(rd("(MEM)", "mem"), num(0)),
                                                       rd("(K3)", "k"))), extra=(K3,)),
            None,
        ),
        # t[W - 1 - k] with `logic [W-1:0] t` and W = 8 in every instance, agreed
        (
            f8(TW, assign(wr("(R8)", "f"), read_bit(rd("(TW)", "t"), ar("SUB", ar(
                "SUB", rd("(WP)", "W"), num(1)), rd("(K3)", "k")))), extra=(K3,)),
            {("core.v", 2, "W"): 8},
        ),
        # a divide by k + 1, k 3 bits unsigned: 1 to 8, never 0
        (
            f8(assign(wr("(R8)", "f"), ar("DIV", rd("(V8)", "v"),
                                          ar("ADD", rd("(K3)", "k"), num(1)))), extra=(K3,)),
            None,
        ),
        # pick(v, k) with an 8-bit v and a 3-bit argument k
        (f8(v_at(rd("(K3)", "k")), extra=(K3,)), None),
        # g(v, k) inside f, g's select proven: with f's own argument k, and with a constant
        (f8(g_of(rd("(K3)", "k")), extra=(K3,)), None),
        (f8(g_of(num(3))), None),
    ],
)  # fmt: skip
def test_a_select_or_divide_proven_in_range_lets_a_constant_call_through(
    fn: dict[str, Any], known: dict[et.ParamKey, int] | None
) -> None:
    assert judged(fn, SEGS, WPAR, G, agreed=known) is None


@pytest.mark.parametrize(
    "fn",
    [
        # pick(v, k) with k 3 bits and v 4 bits wide... here v is 8 bits and k is a signed 4 bits
        f8(v_at(rd("(S4)", "s")), extra=(S4,)),
        # v4[k], k 3 bits: 4 to 7 are outside [3:0]
        f8(assign(wr("(R8)", "f"), read_bit(rd("(D4)", "d"), rd("(K3)", "k"))), extra=(D4, K3)),
        # a loop that runs one pass too far: i <= 8 over an 8-bit vector
        f8(*for_loop("(I)", num(0), lt(rd("(I)", "i"), num(8), "LTE"), [v_at(rd("(I)", "i"))])),
        # v[i - 1] with i from 0
        f8(*for_loop("(I)", num(0), lt(rd("(I)", "i"), num(8)),
                     [v_at(ar("SUB", rd("(I)", "i"), num(1)))])),
        # an integer index outside any loop: every integer value
        f8(v_at(rd("(I)", "i"))),
        # SEGS[d*7 +: 8]: the last part reaches bit 112
        f8(assign(wr("(R8)", "f"), read_bit(rd("(SG)", "SEGS"), ar("MUL", rd("(D4)", "d"),
           num(7)), "SELPLUS") | {"widthp": [num(8)]}), extra=(D4,)),
        # v[d - 4'd1], d 4 bits unsigned: d = 0 wraps around to 15 (an unsigned context)
        f8(v_at(ar("SUB", rd("(D4)", "d"), num(1, "4'h"))), extra=(D4,)),
        # v[k + (-1)]: a negative operand in an unsigned context (k unsigned) is read as large
        f8(v_at(ar("ADD", rd("(K3)", "k"), num(0xFFFFFFFF, "?32?sh"))), extra=(K3,)),
        # a divide by k, which can be 0, and by k - 1 (wraps)
        f8(assign(wr("(R8)", "f"), ar("DIV", rd("(V8)", "v"), rd("(K3)", "k"))), extra=(K3,)),
        # t[W - 1 - k] without an agreed value of W
        f8(TW, assign(wr("(R8)", "f"), read_bit(rd("(TW)", "t"), ar("SUB", ar(
            "SUB", rd("(WP)", "W"), num(1)), rd("(K3)", "k")))), extra=(K3,)),
        # v[k] with a 3-bit local k that nothing writes first (x), and with an argument k that
        # the function writes (fail closed, even with a value in range)
        f8(KL, v_at(rd("(KL)", "k"))),
        f8(assign(wr("(K3)", "k"), num(1)), v_at(rd("(K3)", "k")), extra=(K3,)),
        # g(v, k) inside f with that unwritten local k: x reaches g's proven select
        f8(KL, g_of(rd("(KL)", "k"))),
        # g(v, i) after a loop: i outside its loop's body has no proven range
        f8(*for_loop("(I)", num(0), lt(rd("(I)", "i"), num(8)), [v_at(rd("(I)", "i"))]),
           g_of(rd("(I)", "i"))),
    ],
)  # fmt: skip
def test_a_select_that_can_leave_the_range_still_refuses_a_constant_call(
    fn: dict[str, Any],
) -> None:
    got = judged(fn, SEGS, WPAR, G)
    assert got is not None and got.name == "f" and got.line == 9


# -- round 17: a constant select is an operand only of an operand, as wide as its indices -------


def part(vector: dict[str, Any], left: int, right: int) -> dict[str, Any]:
    """`vector[left:right]` at constant indices."""
    return {"type": "SELEXTRACT", "fromp": [vector], "leftp": [num(left)],
            "rightp": [num(right)], "loc": "e,6:5,6:8"}  # fmt: skip


def to_f(value: dict[str, Any]) -> dict[str, Any]:
    """`f = value`."""
    return assign(wr("(R8)", "f"), value)


W2 = arg("(W2)", "w", basic(1))  # a 2-bit argument
T3 = local("(T3)", "t", {"type": "UNPACKARRAYDTYPE", "childDTypep": [basic(2)],
                          "rangep": [{"type": "RANGE", "leftp": [num(0)],
                                      "rightp": [num(3)]}]})  # fmt: skip
LOC = local("(LC)", "loc", basic(1))  # a 2-bit local
SEGS_AGREED = {("core.v", 3, "SEGS"): 0}
SEGS_PART = f8(
    to_f(
        read_bit(
            rd("(SG)", "SEGS"),
            ar("ADD", ar("MUL", rd("(K3)", "k"), num(7)), part(rd("(SG)", "SEGS"), 2, 0)),
        )
    ),
    extra=(K3,),
)


@pytest.mark.parametrize(
    ("fn", "known"),
    [
        # v[i[2:0]] for i = 0 to 7: a constant select of a loop variable in the loop's body
        pytest.param(f8(*for_loop("(I)", num(0), lt(rd("(I)", "i"), num(8)),
                                  [v_at(part(rd("(I)", "i"), 2, 0))])), None, id="loop-part"),
        # v[k[1:0]] and v[k[2]], k an argument the function never writes
        pytest.param(f8(v_at(part(rd("(K3)", "k"), 1, 0)), extra=(K3,)), None, id="arg-part"),
        pytest.param(f8(v_at(read_bit(rd("(K3)", "k"), num(2))), extra=(K3,)), None,
                     id="arg-bit"),
        # SEGS[k * 7 + SEGS[2:0]], SEGS a typed localparam with one agreed value: 0 to 56
        pytest.param(SEGS_PART, SEGS_AGREED, id="param-part"),
    ],
)  # fmt: skip
def test_a_constant_select_of_an_operand_is_an_operand(
    fn: dict[str, Any], known: dict[et.ParamKey, int] | None
) -> None:
    assert judged(fn, SEGS, WPAR, G, agreed=known) is None


@pytest.mark.parametrize(
    "fn",
    [
        # ua04, arrelc: d[t[2]] with `reg [2:0] t [0:3]` and t[2] = 7: an element of an array is
        # 3 bits (0 to 7, outside the 4-bit d), not one bit per index of the array
        pytest.param(f8(T3, assign(read_bit(wr("(T3)", "t"), num(2)), num(7, "3'h")),
                        to_f(read_bit(rd("(D4)", "d"), read_bit(rd("(T3)", "t"), num(2)))),
                        extra=(D4,)), id="array-element"),
        # locsel2: v[loc[1:0]] with a local that only k == 3 sets: x for every other k
        pytest.param(f8(LOC, if_(ar("EQ", rd("(K3)", "k"), num(3)),
                                 [assign(wr("(LC)", "loc"), num(1, "2'h"))]),
                        v_at(part(rd("(LC)", "loc"), 1, 0)), extra=(K3,)), id="local-part"),
        # rb02: v[f[2:0]], the function's own result before it is written (x)
        pytest.param(f8(v_at(part(rd("(R8)", "f"), 2, 0))), id="own-result"),
        # SEGS[k * 7 + SEGS[2:0]] without an agreed value of SEGS
        pytest.param(SEGS_PART, id="param-unagreed"),
        # w[k[1:0]]: k[1:0] is 0 to 3 (two bits) and w has two bits
        pytest.param(f8(to_f(read_bit(rd("(W2)", "w"), part(rd("(K3)", "k"), 1, 0))),
                        extra=(W2, K3)), id="part-width"),
        # v[k[4:3]] with a 3-bit k: a constant select outside k's range reads x
        pytest.param(f8(v_at(part(rd("(K3)", "k"), 4, 3)), extra=(K3,)), id="part-outside"),
        # mem[0][d]: one 8-bit element at a 4-bit index (8 to 15 outside it)
        pytest.param(f8(MEM, to_f(read_bit(read_bit(rd("(MEM)", "mem"), num(0)),
                                           rd("(D4)", "d"))), extra=(D4,)), id="element-index"),
        # v / (d + 4'd1), d 4 bits: 15 + 1 is 16, which 4 bits do not hold (the divisor is taken
        # as its own context, which can only refuse more)
        pytest.param(f8(to_f(ar("DIV", rd("(V8)", "v"), ar("ADD", rd("(D4)", "d"),
                                                           num(1, "4'h")))), extra=(D4,)),
                     id="divisor-wraps"),
        # d[k[1:0] + 3'd1 + 2'sb11]: 0 to 3 as whole numbers, but the 3-bit context is unsigned
        # and extends 2'sb11 with zeros (3, not -1): k[1:0] + 4, outside the 4-bit d
        pytest.param(f8(to_f(read_bit(rd("(D4)", "d"), ar("ADD", ar(
            "ADD", part(rd("(K3)", "k"), 1, 0), num(1, "3'h")), num(3, "2'sh")))),
            extra=(D4, K3)), id="negative-zero-extended"),
        # SEGS[i + 4'sb1111] for i = 0 to 7: the signed constant is -1, so i = 0 reads SEGS[-1]
        pytest.param(f8(*for_loop("(I)", num(0), lt(rd("(I)", "i"), num(8)), [to_f(read_bit(
            rd("(SG)", "SEGS"), ar("ADD", rd("(I)", "i"), num(0xF, "4'sh"))))])),
            id="signed-constant"),
        # v[k + d]: 0 to 22 as whole numbers, which the 4-bit context does not hold
        pytest.param(f8(v_at(ar("ADD", rd("(K3)", "k"), rd("(D4)", "d"))), extra=(K3, D4)),
                     id="add-two-ranges"),
        # v[k -: 4]: k - 3 is below bit 0 for k < 3
        pytest.param(f8(to_f(read_bit(rd("(V8)", "v"), rd("(K3)", "k"), "SELMINUS")
                             | {"widthp": [num(4)]}), extra=(K3,)), id="minus-part"),
        # v / (0 - i) for i = -3 to -1: 1 to 3, but from a negative operand, which a wider
        # unsigned context extends with zeros
        pytest.param(f8(*for_loop("(I)", num(0xFFFFFFFD), lt(rd("(I)", "i"), num(0)), [to_f(
            ar("DIV", rd("(V8)", "v"), ar("SUB", num(0), rd("(I)", "i"))))])),
            id="divisor-negative-operand"),
        # for (i = 7; i >= k; i--) v[i - 1]: a bound that is not one constant (k = 0 reads v[-1])
        pytest.param(f8(*for_loop("(I)", num(7), lt(rd("(I)", "i"), rd("(K3)", "k"), "GTE"),
                                  [v_at(ar("SUB", rd("(I)", "i"), num(1)))], step=-1),
                        extra=(K3,)), id="down-loop-variable-bound"),
        # v[k + W] without an agreed value of W
        pytest.param(f8(v_at(ar("ADD", rd("(K3)", "k"), rd("(WP)", "W"))), extra=(K3,)),
                     id="unagreed-parameter"),
    ],
)  # fmt: skip
def test_a_select_or_divide_that_is_not_proven_refuses_a_constant_call(fn: dict[str, Any]) -> None:
    got = judged(fn, SEGS, WPAR, G)
    assert got is not None and got.name == "f" and got.line == 9


def test_an_element_of_an_array_parameter_is_not_one_bit_even_with_a_known_value() -> None:
    """d[TP[2] * k[0]] with `localparam [2:0] TP [0:3]`: the element is 3 bits (0 to 7), so the
    index can leave the 4-bit d, whatever value the check holds for TP."""
    tp = var("(TP)", "TP", 4, varType="LPARAM", isParam=True, childDTypep=[{
        "type": "UNPACKARRAYDTYPE", "childDTypep": [basic(2)],
        "rangep": [{"type": "RANGE", "leftp": [num(0)], "rightp": [num(3)]}]}])  # fmt: skip
    index = ar("MUL", read_bit(rd("(TP)", "TP"), num(2)), read_bit(rd("(K3)", "k"), num(0)))
    fn = f8(to_f(read_bit(rd("(D4)", "d"), index)), extra=(D4, K3))
    got = judged(fn, tp, agreed={("core.v", 4, "TP"): 0})
    assert got is not None and got.name == "f" and got.line == 9


def loop_with(
    *body: dict[str, Any], start: dict[str, Any] | None = None, step: int = 1, var_: str = "(I)"
) -> list[dict[str, Any]]:
    return for_loop(var_, start or num(0), lt(rd(var_, "i"), num(8)), [*body, v_at(rd(var_, "i"))],
                    step=step)  # fmt: skip


U4 = local("(U4)", "u", basic(3))  # an unsigned 4-bit loop variable


@pytest.mark.parametrize(
    "stmts",
    [
        # the body writes i (directly, and as an output argument of a call)
        loop_with(assign(wr("(I)", "i"), num(9))),
        loop_with(assign(wr("(R8)", "f"), call("(c9)", "(F8)", "f", 6) | {
            "argsp": [{"type": "ARG", "exprp": [wr("(I)", "i")]}]})),
        # a `break` or `continue` (Verilator's JUMPGO)
        loop_with({"type": "JUMPGO", "blockp": "(J)"}),
        # a step the wrong way (i < 8 counting down) and a step of 2
        loop_with(step=-1),
        [assign(wr("(I)", "i"), num(0)), {"type": "LOOP", "stmtsp": [
            {"type": "LOOPTEST", "condp": [lt(rd("(I)", "i"), num(8))]}, v_at(rd("(I)", "i")),
            assign(wr("(I)", "i"), ar("ADD", rd("(I)", "i"), num(2)))]}],
        # a start that is not a constant
        loop_with(start=rd("(V8)", "v")),
        # no start right before the loop
        [assign(wr("(I)", "i"), num(0)), assign(wr("(R8)", "f"), num(0)),
         *loop_with()[1:]],
        # an unsigned 4-bit u counting down while u >= 0: never stops (u wraps to 15)
        for_loop("(U4)", num(7), lt(rd("(U4)", "u"), num(0), "GTE"),
                 [v_at(rd("(U4)", "u"))], step=-1),
        # a test against a bound past u's range (u < 16 never fails)
        for_loop("(U4)", num(0), lt(rd("(U4)", "u"), num(16)), [v_at(rd("(U4)", "u"))]),
    ],
)  # fmt: skip
def test_a_loop_that_is_not_simple_gives_its_variable_every_value(
    stmts: list[dict[str, Any]],
) -> None:
    got = judged(f8(*stmts, extra=(U4,)), SEGS, WPAR)
    assert got is not None and got.name == "f"


@pytest.mark.parametrize(
    "value",
    [
        num(0, "8'bx"),  # an x argument: the proven select reads x
        ar("DIV", num(1), num(0)),  # 1 / 0
        call("(c7)", "(F8)", "f", 9),  # another call: it can return x (an unset result)
    ],
)
def test_a_constant_call_whose_argument_can_be_x_is_refused_when_a_select_was_proven(
    value: dict[str, Any],
) -> None:
    fn = f8(*for_loop("(I)", num(0), lt(rd("(I)", "i"), num(8)), [v_at(rd("(I)", "i"))]))
    assert judged(fn, args=(value,)) is not None
    assert judged(fn) is None  # the same call with 8'h81
    plain = f8(assign(wr("(R8)", "f"), rd("(V8)", "v")))  # no select: an x argument is fine
    assert judged(plain, args=(value,)) is None


def test_the_agreed_values_are_one_value_that_both_tools_give_in_every_instance() -> None:
    a, b = ("core.v", 2, "W"), ("core.v", 3, "N")
    c, d = ("core.v", 4, "M"), ("core.v", 5, "R")
    verilator = {a: [(("t",), 8), (("u",), 8)], b: [(("t",), 1), (("u",), 2)], c: [(("t",), 3)],
                 d: None}  # fmt: skip
    simulator = {a: [(("t",), 8)], b: [(("t",), 1)], c: [(("t",), 4)], d: [(("t",), 1)]}
    assert et.agreed_values(verilator, simulator) == {a: 8}
    assert et.agreed_values(verilator, {}) == {}


# -- round 18: a call cycle is judged whatever order the calls come in -------------------------
# mr4db: f(v, k, d) reads v[k] (k 3 bits) and, inside `if (d) $display(...)`, calls g(v, k);
# g(v, k) = f(v, k, 1'b0). With a 6-bit v the select can leave the range (k = 6 or 7), with an
# 8-bit v it cannot. The walk meets the last module item first: a localparam that calls f placed
# after the inline call of g used to judge f first, read g while f was still being judged (as
# safe), and keep g as safe.


def cycle(width: int) -> list[dict[str, Any]]:
    """f and g calling each other, with a `width`-bit v."""

    def args(*exprs: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": "ARG", "exprp": [e]} for e in exprs]

    to_g = call(f"({next(_N)})", "(CG)", "g", 6) | {"argsp": args(rd("(CV)", "v"), rd("(CK)", "k"))}
    f = func("(CF)", "f", 5, local("(CR)", "f", basic(7), isFuncReturn=True),
             arg("(CV)", "v", basic(width - 1)), arg("(CK)", "k", basic(2)),
             arg("(CD)", "d", basic(0)), {"type": "BEGIN", "stmtsp": [
                 if_(rd("(CD)", "d"), [{"type": "DISPLAY", "fmtp": [to_g]}]),
                 if_({"type": "LOGNOT", "lhsp": [read_bit(rd("(CV)", "v"), rd("(CK)", "k"))]},
                     [assign(wr("(CR)", "f"), num(1, "8'h"))],
                     [assign(wr("(CR)", "f"), num(2, "8'h"))])]})  # fmt: skip
    to_f = call(f"({next(_N)})", "(CF)", "f", 7) | {
        "argsp": args(rd("(GVC)", "v"), rd("(GKC)", "k"), num(0, "1'h"))
    }
    g = func("(CG)", "g", 7, local("(GRC)", "g", basic(7), isFuncReturn=True),
             arg("(GVC)", "v", basic(width - 1)), arg("(GKC)", "k", basic(2)),
             {"type": "BEGIN", "stmtsp": [assign(wr("(GRC)", "g"), to_f)]})  # fmt: skip
    return [f, g]


def cycle_calls(width: int, real: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    """`g(6'h3F, 3'd6)` inline at line 9 (or `localparam real RR = g(...)` at line 8 with
    `real`), and `localparam [7:0] P0 = f(6'h3F, 3'd1, 1'b0);` at line 10."""
    vec = num(0x3F, f"{width}'h")
    g_args = [{"type": "ARG", "exprp": [e]} for e in (vec, num(6, "3'h"))]
    f_args = [{"type": "ARG", "exprp": [e]} for e in (vec, num(1, "3'h"), num(0, "1'h"))]
    of_g = call(f"({next(_N)})", "(CG)", "g", 8 if real else 9) | {"argsp": g_args}
    first = var("(RR)", "RR", 8, varType="LPARAM", isParam=True, valuep=[of_g]) if real else of_g
    of_f = call(f"({next(_N)})", "(CF)", "f", 10) | {"argsp": f_args}
    return first, var("(P0)", "P0", 10, varType="LPARAM", isParam=True, valuep=[of_f])


@pytest.mark.parametrize("p0_last", [True, False], ids=["p0-after", "p0-before"])
def test_a_call_cycle_cannot_hide_a_select_whatever_order_the_calls_come_in(p0_last: bool) -> None:
    inline, p0 = cycle_calls(6)
    order = [inline, p0] if p0_last else [p0, inline]
    got = et.constant_call(module(*NAMED, *cycle(6), *order), META, OURS)
    assert got is not None and (got.name, got.line) == ("g", 9)
    # the same cycle with an 8-bit v: every select is proven, and so is g's call of f
    inline, p0 = cycle_calls(8)
    order = [inline, p0] if p0_last else [p0, inline]
    assert et.constant_call(module(*NAMED, *cycle(8), *order), META, OURS) is None


@pytest.mark.parametrize("p0_last", [True, False], ids=["p0-after", "p0-before"])
def test_a_call_cycle_cannot_hide_a_risky_call_in_a_parameter_value(p0_last: bool) -> None:
    """vc03: `localparam real RR = g(6'h3F, 3'd6);`, whose value is not compared, must be named
    by `valued_calls` (so `equiv_icarus.uncompared` refuses it) in either order."""
    rr, p0 = cycle_calls(6, real=True)
    tree = module(*NAMED, *cycle(6), *([rr, p0] if p0_last else [p0, rr]))
    assert et.valued_calls(tree, META, OURS) == {("core.v", 8, "RR"), ("core.v", 10, "P0")}
    rr, p0 = cycle_calls(8, real=True)
    tree = module(*NAMED, *cycle(8), *([rr, p0] if p0_last else [p0, rr]))
    assert et.valued_calls(tree, META, OURS) == set()


def with_k(addr: str, name: str, line: int, body: dict[str, Any]) -> dict[str, Any]:
    """`function name(input integer k)` with `body`; its argument VAR is `addr` + "k"."""
    k = var(f"({addr}k)", "k", line, varType="PORT", isFuncLocal=True, direction="INPUT")
    return func(f"({addr})", name, line, k, body)


def k_of(addr: str, line: int, to: str, name: str) -> dict[str, Any]:
    """`name(k)` with the argument k of the function `addr` (not a constant call)."""
    arg_ = {"type": "ARG", "exprp": [ref(f"({next(_N)})", f"({addr}k)", "k", line)]}
    return call(f"({next(_N)})", to, name, line) | {"argsp": [arg_]}


@pytest.mark.parametrize("caller_first", [True, False])
def test_a_chain_of_calls_is_followed_to_its_end_in_either_order(caller_first: bool) -> None:
    """h(k) calls g(k), which calls pick(4'b1010, k) (pick reads at a computed index): a
    constant call of h is as risky as pick. One pass over the functions in one order would
    judge h before g's level rose."""
    g = with_k("GG", "g", 6, set_return(pick_call(const("4'ha"), ref("(gk)", "(GGk)", "k", 6),
                                                  line=6), "(RG2)", "g"))  # fmt: skip
    h = with_k("HH", "h", 7, set_return(k_of("HH", 7, "(GG)", "g"), "(RH)", "h"))
    chain = [h, g, READS_AT_K] if caller_first else [READS_AT_K, g, h]
    got = constant(*chain, call("(c7)", "(HH)", "h", 11) | {"argsp": [{"type": "ARG", "exprp": [
        const("32'h6")]}]})  # fmt: skip
    assert got is not None and (got.name, got.line) == ("h", 11)


def test_a_call_inside_a_function_of_no_function_the_tree_holds_counts() -> None:
    outer = with_k("OO", "outer", 7, set_return(k_of("OO", 8, "(nowhere)", "pick"), "(RO)", "o"))
    top = call("(c)", "(OO)", "outer", 11) | {"argsp": [{"type": "ARG", "exprp": [const("32'h6")]}]}
    got = constant(outer, top)
    assert got is not None and (got.name, got.line) == ("outer", 11)
    # the same function calling a function without a computed index is fine
    safe = with_k("SS", "safe", 7, set_return(rd("(SSk)", "k"), "(RS)", "safe"))
    calls_safe = with_k("OO", "outer", 7, set_return(k_of("OO", 8, "(SS)", "safe"), "(RO)", "o"))
    assert constant(safe, calls_safe, top) is None


# -- round 18: a real argument can be x ---------------------------------------------------------
# ri09: `f(integer'(R))` with `localparam real R = 1.0e999` (infinite): a simulator converts an
# infinite or NaN real to an all-x integer, so f's proven select `v[k]` reads x. The same with a
# real literal, a real or untyped parameter that holds a real (also one an override sets: its
# value is not a plain number in both tools, see `plain_values`), and a real system function.

PROVEN_K = f8(v_at(rd("(K3)", "k")), extra=(K3,))  # v[k], k 3 bits into 8: proven
REAL_R = var(
    "(RL)",
    "R",
    4,
    varType="LPARAM",
    isParam=True,
    childDTypep=[{"type": "BASICDTYPE", "keyword": "real"}],
    valuep=[const("inf.0")],
)
PLAIN = frozenset({("core.v", 2, "P"), ("core.v", 2, "W")})  # P and W: plain in both tools


def unary(kind: str, operand: dict[str, Any], **more: Any) -> dict[str, Any]:
    return {"type": kind, "lhsp": [operand], **more}


@pytest.mark.parametrize(
    ("value", "plain"),
    [
        pytest.param(const("inf.0"), PLAIN, id="literal-inf"),  # f(1.0e999)
        pytest.param(const("3.0"), PLAIN, id="literal-finite"),  # any real literal (fail closed)
        pytest.param(const('"AB"'), PLAIN, id="string"),
        pytest.param(named("(RL)", "R"), PLAIN, id="real-parameter"),  # f(R)
        pytest.param(named("(LP)", "P"), frozenset(), id="parameter-not-plain"),
        pytest.param(ar("ADD", named("(LP)", "P"), num(1)), frozenset({("core.v", 2, "W")}),
                     id="parameter-not-plain-in-a-sum"),
        pytest.param(unary("CAST", named("(LP)", "P"), childDTypep=[INTEGER]), PLAIN,
                     id="cast"),  # integer'(P): a cast counts even of a plain parameter
        pytest.param(unary("CASTPARSE", num(3)), PLAIN, id="size-cast"),  # 8'(3)
        pytest.param(unary("SQRTD", unary("NEGATE", num(1))), PLAIN, id="sqrt"),  # $sqrt(-1)
        pytest.param(unary("RTOIS", unary("BITSTOREALD", num(0x7FF0000000000000, "64'h"))),
                     PLAIN, id="bitstoreal"),  # $rtoi($bitstoreal(64'h7ff0...))
        pytest.param(unary("LOGD", num(0)), PLAIN, id="ln"),  # $ln(0)
        pytest.param(unary("REALTOBITS", num(1)), PLAIN, id="realtobits"),
        pytest.param(unary("SOMENEWD", num(1)), PLAIN, id="unknown-d"),  # fail closed
    ],
)  # fmt: skip
def test_a_real_argument_can_be_x_and_refuses_a_proven_constant_call(
    value: dict[str, Any], plain: frozenset[et.ParamKey]
) -> None:
    got = judged(PROVEN_K, REAL_R, args=(num(0x81, "8'h"), value), plain=plain)
    assert got is not None and (got.name, got.line) == ("f", 9)
    assert "a real: a simulator converts an infinite or NaN real to an all-x integer" in (
        got.sentence
    )
    assert judged(PROVEN_K, REAL_R, args=(num(0x81, "8'h"), num(3)), plain=plain) is None


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(named("(LP)", "P"), id="localparam"),
        pytest.param(named("(GP)", "W"), id="parameter"),
        pytest.param(named("(GV)", "g"), id="genvar"),  # always a number
        pytest.param(ar("ADD", named("(LP)", "P"), num(1)), id="add"),
        pytest.param(ar("AND", named("(LP)", "P"), num(3)), id="and"),
        pytest.param({"type": "COND", "condp": [ar("LOGAND", num(1), num(1))],
                      "thenp": [num(1)], "elsep": [unary("REDAND", num(3))]}, id="cond"),
        pytest.param(unary("SIGNED", named("(LP)", "P")), id="signed"),
        pytest.param(unary("UNSIGNED", named("(GP)", "W")), id="unsigned"),
        pytest.param(ar("NEQWILD", num(1), num(2)), id="wildcard-compare"),
        pytest.param({"type": "ATTROF", "fromp": [named("(WS)", "w")]}, id="bits"),
    ],
)  # fmt: skip
def test_a_plain_argument_lets_a_proven_constant_call_through(value: dict[str, Any]) -> None:
    assert judged(PROVEN_K, REAL_R, args=(num(0x81, "8'h"), value), plain=PLAIN) is None


def test_the_plain_values_are_numbers_in_both_tools() -> None:
    a, b, c = ("core.v", 2, "W"), ("core.v", 3, "R"), ("core.v", 4, "S")
    d, e = ("core.v", 5, "E"), ("core.v", 6, "U")
    verilator = {a: [(("t",), 8), (("u",), 9)], b: None, c: [(("t",), 1)], d: [], e: [(("t",), 1)]}
    simulator = {a: [(("t",), 8), (("u",), 9)], b: [(("t",), 1)], c: None, d: []}
    assert et.plain_values(verilator, simulator) == {a}  # several values are fine
    assert et.plain_values(verilator, {}) == set()


# -- round 19: a real in any constant call, a hierarchical name, a parameter no tool builds -------
# xr01: `f(g.R)` with `if (1) begin : g localparam real R = 1.0e999; end` (a VARXREF before
# V3Param, which no rule looked at). xs5: `case (ui_in) f(R): ...` with `f = a + 1`, a function
# without a computed index: Yosys works the call out itself without a warning, a simulator gets
# x back and its case label of x matches nothing, while the gates need not keep the x.

SAFE_F = f8(to_f(rd("(V8)", "v")))  # f = v: no computed select, no divide
XREF = {"type": "VARXREF", "name": "R", "dotted": "g", "loc": "e,9:5,9:8"}  # g.R
DOT = {"type": "DOT", "name": "R", "loc": "e,9:5,9:8"}  # a dotted name not resolved
STD_P = var("(SP)", "SP", 2, varType="LPARAM", isParam=True, valuep=[num(3)]) | {
    "loc": "d,2:1,2:4"  # a parameter of Verilator's own std package, not one of our files
}
P_KEY, W_KEY = ("core.v", 2, "P"), ("core.v", 2, "W")
REAL_ARGUMENTS = [
    pytest.param(const("inf.0"), PLAIN, id="literal-inf"),  # f(1.0e999)
    pytest.param(const("2.5"), PLAIN, id="literal-finite"),  # any real literal (fail closed)
    pytest.param(named("(RL)", "R"), PLAIN, id="real-parameter"),  # f(R)
    pytest.param(named("(LP)", "P"), frozenset({W_KEY}), id="localparam-not-plain"),
    # X8: a GPARAM (a child's parameter that an override sets to a real) counts like an LPARAM
    pytest.param(named("(GP)", "W"), frozenset({P_KEY}), id="parameter-not-plain"),
    pytest.param(XREF, PLAIN, id="hierarchical"),  # f(g.R)
    pytest.param(DOT, PLAIN, id="unresolved-dot"),
    pytest.param(unary("SQRTD", unary("NEGATE", const("1.0"))), PLAIN, id="sqrt"),
    pytest.param(unary("SQRTD", num(4)), PLAIN, id="sqrt-of-a-number"),  # $sqrt(4): makes a real
    pytest.param(unary("ITORD", num(3)), PLAIN, id="itor"),  # $itor(3)
    pytest.param(unary("RTOIS", unary("BITSTOREALD", num(0x7FF0000000000000, "64'h"))), PLAIN,
                 id="bitstoreal"),
]  # fmt: skip


@pytest.mark.parametrize(("value", "plain"), REAL_ARGUMENTS)
@pytest.mark.parametrize("proven", [False, True], ids=["safe-function", "proven-function"])
def test_a_real_or_hierarchical_argument_refuses_a_constant_call_of_any_function(
    value: dict[str, Any], plain: frozenset[et.ParamKey], proven: bool
) -> None:
    fn, args = (PROVEN_K, (num(0x81, "8'h"), value)) if proven else (SAFE_F, (value,))
    got = judged(fn, REAL_R, args=args, plain=plain)
    assert got is not None and (got.name, got.line) == ("f", 9)
    if proven:  # the constant-call sentence, which names the real too
        assert "a hierarchical name, a real: a simulator converts an infinite or NaN real to " in (
            got.sentence
        )
    else:  # round 20: the sentence of a real argument, which counts for every call
        assert "with an argument that can hold a real" in got.sentence
        assert "Compute an integer localparam from the real first" in got.sentence
    assert "which any function can give back" in got.sentence


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(num(3), id="number"),
        pytest.param(named("(LP)", "P"), id="plain-localparam"),
        pytest.param(named("(GP)", "W"), id="plain-parameter"),
        pytest.param(named("(GV)", "g"), id="genvar"),
        pytest.param(const("'1"), id="fill"),  # not a real (a proven function refuses it)
        pytest.param(const("8'h41"), id="sized"),  # "A" as Verilator links it in an argument
        pytest.param(const('"AB"'), id="string"),
        pytest.param(unary("CASTPARSE", num(3)), id="size-cast"),  # 8'(3): its operand counts
        pytest.param(unary("CAST", named("(LP)", "P"), childDTypep=[INTEGER]), id="cast"),
        pytest.param(ar("DIV", num(8), named("(LP)", "P")), id="divide"),  # x is not a real
        pytest.param({"type": "ATTROF", "fromp": [named("(WS)", "w")]}, id="bits"),
    ],
)  # fmt: skip
def test_an_argument_that_holds_no_real_lets_a_safe_constant_call_through(
    value: dict[str, Any],
) -> None:
    assert judged(SAFE_F, REAL_R, args=(value,), plain=PLAIN) is None


def test_a_real_argument_in_a_parameter_value_is_refused_and_not_left_to_the_comparison() -> None:
    """rk10 (round 20): `localparam [7:0] K = g(R)` with `R = 1.0e999` and g without a
    computed index. Verilator and a simulator both give K the value 1, Yosys works the call out
    itself, silently, to 2, so the comparison of the values cannot see it: `constant_call`
    refuses a real argument in a parameter value too (it was left to the comparison). The
    parameters `valued_calls` names do not change: a proven function's call there with a
    hierarchical argument is named (its value must then be compared), a real argument of a
    function without a computed index is not."""

    def value(*args: dict[str, Any]) -> dict[str, Any]:
        call_f = call("(c)", "(F8)", "f", 7) | {
            "argsp": [{"type": "ARG", "exprp": [a]} for a in args]
        }
        return var("(V)", "V", 7, varType="LPARAM", isParam=True, valuep=[call_f])

    def valued(fn: dict[str, Any], *args: dict[str, Any]) -> frozenset[et.ParamKey]:
        return et.valued_calls(module(*NAMED, fn, REAL_R, value(*args)), META, OURS)

    def refused(fn: dict[str, Any], *args: dict[str, Any]) -> et.Unknown | None:
        tree = module(*NAMED, fn, REAL_R, value(*args))
        return et.constant_call(tree, META, OURS, plain=PLAIN)

    assert valued(SAFE_F, named("(RL)", "R")) == set()
    assert valued(SAFE_F, XREF) == set()
    assert valued(PROVEN_K, num(0x81, "8'h"), XREF) == {("core.v", 7, "V")}
    assert valued(PROVEN_K, num(0x81, "8'h"), num(3)) == set()
    for fn, args in [
        (SAFE_F, (named("(RL)", "R"),)),
        (SAFE_F, (XREF,)),
        (SAFE_F, (unary("SQRTD", unary("NEGATE", const("1.0"))),)),  # rk03: $sqrt(-1.0)
        (SAFE_F, (ar("MUL", named("(RL)", "R"), named("(RL)", "R")),)),  # rk08: R * R
        (PROVEN_K, (num(0x81, "8'h"), XREF)),
    ]:
        got = refused(fn, *args)
        assert got is not None and (got.name, got.line) == ("f", 7)
        assert "with an argument that can hold a real" in got.sentence
    assert refused(SAFE_F, num(3)) is None
    assert refused(SAFE_F, named("(LP)", "P")) is None  # a plain localparam
    assert refused(PROVEN_K, num(0x81, "8'h"), num(3)) is None


def test_a_real_argument_in_a_parameter_override_is_refused() -> None:
    """rk11 (round 20): `rk11_sub #(.P(g(R)))`: the child's P is 1 in Verilator and a
    simulator, 2 in Yosys."""

    def over(*args: dict[str, Any], to: str | None = "(GP)") -> dict[str, Any]:
        call_f = call("(c)", "(F8)", "f", 8) | {
            "argsp": [{"type": "ARG", "exprp": [a]} for a in args]
        }
        pin = {"type": "PIN", "exprp": [call_f]} | ({"modVarp": to} if to else {})
        return {"type": "CELL", "name": "u", "paramsp": [pin]}

    for to in ("(GP)", None):
        tree = module(*NAMED, SAFE_F, REAL_R, over(named("(RL)", "R"), to=to))
        got = et.constant_call(tree, META, OURS, plain=PLAIN)
        assert got is not None and (got.name, got.line) == ("f", 8)
        assert "also in a parameter value, an override" in got.sentence
    tree = module(*NAMED, SAFE_F, REAL_R, over(num(3)))
    assert et.constant_call(tree, META, OURS, plain=PLAIN) is None


# b1case and b3ff (round 20): `for (i = 0; i < 1; i = i + 1) case (ui_in) f(R + i): ...` with an
# `integer i`; b2case: `h = f(R + a);` in a function h whose argument is a. Verilator sees a call
# whose argument reads a variable, Yosys works it out itself once it unrolls the loop or works
# out h, and converts R silently: the real counts in an argument that reads a variable too.
I_SIG = var("(IS)", "i", 3, varType="VAR", childDTypep=[INTEGER])  # `integer i` of a block


@pytest.mark.parametrize("proven", [False, True], ids=["safe-function", "proven-function"])
@pytest.mark.parametrize(
    "value",
    [
        pytest.param(ar("ADD", named("(RL)", "R"), named("(IS)", "i")), id="loop-variable"),
        pytest.param(ar("ADD", named("(RL)", "R"), named("(WS)", "w")), id="signal"),
        pytest.param(ar("ADD", XREF, named("(WS)", "w")), id="hierarchical"),
        pytest.param(ar("ADD", const("2.5"), named("(WS)", "w")), id="literal"),
    ],
)
def test_a_real_in_an_argument_that_reads_a_variable_is_refused(
    value: dict[str, Any], proven: bool
) -> None:
    fn, args = (PROVEN_K, (num(0x81, "8'h"), value)) if proven else (SAFE_F, (value,))
    got = judged(fn, REAL_R, I_SIG, args=args, plain=PLAIN)
    assert got is not None and (got.name, got.line) == ("f", 9)
    assert "with an argument that can hold a real" in got.sentence
    plain = ar("ADD", named("(LP)", "P"), named("(IS)", "i"))  # P + i: no real
    args = (num(0x81, "8'h"), plain) if proven else (plain,)
    assert judged(fn, REAL_R, I_SIG, args=args, plain=PLAIN) is None


def test_a_real_in_a_call_inside_another_function_is_refused() -> None:
    """b2case: `function h(input [3:0] a); h = f(R + a);` called as `case (ui_in) h(4'd0):`."""
    inner = call("(ci)", "(F8)", "f", 6) | {"argsp": [{"type": "ARG", "exprp": [
        ar("ADD", named("(RL)", "R"), ref("(ha)", "(HHk)", "k", 6))]}]}  # fmt: skip
    h = with_k("HH", "h", 5, set_return(inner, "(RH)", "h"))
    top = call("(ct)", "(HH)", "h", 11) | {"argsp": [{"type": "ARG", "exprp": [num(0)]}]}
    tree = module(*NAMED, SAFE_F, REAL_R, h, top)
    got = et.constant_call(tree, META, OURS, plain=PLAIN)
    assert got is not None and (got.name, got.line) == ("f", 6)  # the inner call, by line
    assert "with an argument that can hold a real" in got.sentence


def test_an_unresolved_plain_name_counts_only_in_a_constant_call() -> None:
    """A PARSEREF is a plain name of a module that nothing instantiates: in a call that reads a
    variable it is that variable (`test_a_call_with_an_argument_that_reads_a_variable_is_fine`),
    but read only for its size (`$bits(x)`, the call is constant) it is a name the check cannot
    judge, so it counts as one that can hold a real (a DOT counts either way)."""
    size = {"type": "ATTROF", "fromp": [{"type": "PARSEREF", "name": "x"}]}
    assert judged(SAFE_F, REAL_R, args=(size,), plain=PLAIN) is not None
    in_sum = ar("ADD", {"type": "PARSEREF", "name": "x"}, named("(WS)", "w"))
    assert judged(SAFE_F, REAL_R, args=(in_sum,), plain=PLAIN) is None
    dot = ar("ADD", DOT, named("(WS)", "w"))
    assert judged(SAFE_F, REAL_R, args=(dot,), plain=PLAIN) is not None


# u04e, v03 (round 20): `localparam [39:0] MSG = "HELLO";` then `first_char(MSG)` in an assign. A
# simulator lists a sized string localparam as a string, so it is not plain, but its declared
# packed range makes it hold bits: no real can reach the call through it.
def typed(addr: str, name: str, dtype: dict[str, Any] | None, kind: str = "LPARAM") -> Any:
    more = {"childDTypep": [dtype]} if dtype is not None else {}
    return var(addr, name, 3, varType=kind, isParam=True, valuep=[const('"A"')], **more)


@pytest.mark.parametrize(
    ("dtype", "holds_bits"),
    [
        pytest.param(basic(39), True, id="packed-range"),  # localparam [39:0] MSG = "HELLO"
        pytest.param(basic(7, signed=True), True, id="signed-range"),
        pytest.param(INTEGER, True, id="integer"),
        pytest.param({"type": "BASICDTYPE", "keyword": "logic"}, True, id="logic-one-bit"),
        pytest.param({"type": "BASICDTYPE", "keyword": "LOGIC_IMPLICIT"}, False, id="untyped"),
        pytest.param({"type": "BASICDTYPE", "keyword": "LOGIC_IMPLICIT", "signed": True}, False,
                     id="signed-only"),  # localparam signed P = 1.0e999 holds a real
        pytest.param({"type": "BASICDTYPE", "keyword": "real"}, False, id="real"),
        pytest.param({"type": "BASICDTYPE", "keyword": "string"}, False, id="string-type"),
        pytest.param({"type": "REFDTYPE", "name": "byte_t"}, False, id="typedef"),
        pytest.param(None, False, id="no-type"),
    ],
)  # fmt: skip
@pytest.mark.parametrize("kind", ["LPARAM", "GPARAM"])
def test_a_parameter_declared_to_hold_bits_holds_no_real(
    dtype: dict[str, Any] | None, holds_bits: bool, kind: str
) -> None:
    param = typed("(TP)", "MSG", dtype, kind)
    got = judged(SAFE_F, param, args=(named("(TP)", "MSG"),), plain=PLAIN)
    assert (got is None) == holds_bits
    if got is not None:
        assert "with an argument that can hold a real" in got.sentence
    # a proven function still refuses a parameter that is not plain, whatever its type (a
    # string is not a number the proof can bound)
    got = judged(PROVEN_K, param, args=(num(0x81, "8'h"), named("(TP)", "MSG")), plain=PLAIN)
    assert got is not None and "calls the function `f` with constant arguments" in got.sentence
    # and a plain one is fine whatever its type
    plain = PLAIN | {("core.v", 3, "MSG")}
    assert judged(SAFE_F, param, args=(named("(TP)", "MSG"),), plain=plain) is None


def test_a_parameter_inside_a_function_always_counts_as_built() -> None:
    """rk18 (round 20): Verilator's elaborated tree leaves out the local parameters of a
    function that it only works out for a parameter value, so only the simulator lists them,
    and a name the two tools spelled differently (`\\R"`) was then built by neither: `f(R)` in
    g's body counted as dead code. A parameter declared inside a function counts as built."""
    local_r = var("(FR)", "R", 6, varType="LPARAM", isParam=True,
                  childDTypep=[{"type": "BASICDTYPE", "keyword": "real"}],
                  valuep=[const("inf.0")])  # fmt: skip
    inner = call("(ci)", "(F8)", "f", 7) | {"argsp": [{"type": "ARG", "exprp": [
        named("(FR)", "R")]}]}  # fmt: skip
    g = func("(G2)", "g", 5, local("(RG2)", "g", basic(7), isFuncReturn=True), local_r,
             {"type": "BEGIN", "stmtsp": [set_return(inner, "(RG2)", "g")]})  # fmt: skip
    tree = module(*NAMED, SAFE_F, g)
    got = et.constant_call(tree, META, OURS, plain=PLAIN, built=frozenset())
    assert got is not None and (got.name, got.line) == ("f", 7)
    # the same localparam outside a function, built by no tool, is dead code
    outside = local_r | {"isFuncLocal": False}
    call_r = f8_call(named("(FR)", "R"))
    tree = module(*NAMED, SAFE_F, outside, call_r)
    assert et.constant_call(tree, META, OURS, plain=PLAIN, built=frozenset()) is None
    # and one inside a task counts as built too (fail closed; the source check refuses a task)
    task = func("(T2)", "t", 5, local_r, {"type": "BEGIN", "stmtsp": []}, kind="TASK")
    tree = module(*NAMED, SAFE_F, task, call_r)
    assert et.constant_call(tree, META, OURS, plain=PLAIN, built=frozenset()) is not None


def test_a_parameter_with_more_than_one_type_node_is_not_counted_as_holding_bits() -> None:
    """Fail closed: only a VAR with exactly one type node, a BASICDTYPE of an integer type."""
    real = {"type": "BASICDTYPE", "keyword": "real"}
    assert et._holds_bits({"childDTypep": [basic(7)]})
    assert not et._holds_bits({"childDTypep": [basic(7), real]})
    assert not et._holds_bits({"childDTypep": ["(1)"]})  # a reference, not a node
    assert not et._holds_bits({})
    # round 21 HB2: the keyword alone is not enough, the node must be a BASICDTYPE
    assert not et._holds_bits({"childDTypep": [{"type": "REFDTYPE", "keyword": "logic"}]})
    # round 21 HB9: the declared type (childDTypep) decides, not the resolved one (dtypep,
    # an `addr`), which every VAR of Verilator's tree has too
    assert et._holds_bits({"dtypep": "(d1)", "childDTypep": [basic(7)]})


def test_the_constant_call_sentence_comes_first_at_a_call_that_is_both() -> None:
    """A proven function's constant call with a real argument is named with the constant-call
    sentence (it names the real too), a call of a function without a computed index with the
    sentence of a real argument."""
    real = named("(RL)", "R")
    both = judged(PROVEN_K, REAL_R, args=(num(0x81, "8'h"), real), plain=PLAIN)
    assert both is not None and "with constant arguments outside the value" in both.sentence
    assert "a real: a simulator converts an infinite or NaN real" in both.sentence
    only = judged(SAFE_F, REAL_R, args=(real,), plain=PLAIN)
    assert only is not None and "with an argument that can hold a real" in only.sentence


@pytest.mark.parametrize("fn", [PROVEN_K, SAFE_F], ids=["proven-function", "safe-function"])
def test_a_parameter_that_no_tool_builds_counts_as_plain(fn: dict[str, Any]) -> None:
    """r01, r07, r09 (round 19): a localparam in a generate branch that the parameters do not
    choose, or a parameter of a sub-module that no branch instantiates, is in neither tool's
    values: the call that uses it is dead code in every tool. One that a tool builds, or one of
    a file that is not ours, still counts as not plain."""
    args = (num(0x81, "8'h"), named("(LP)", "P")) if fn is PROVEN_K else (named("(LP)", "P"),)
    assert judged(fn, REAL_R, args=args) is not None  # no `built`: every parameter can be x
    assert judged(fn, REAL_R, args=args, built=frozenset()) is None  # P is built by no tool
    assert judged(fn, REAL_R, args=args, built=frozenset({P_KEY})) is not None
    assert judged(fn, REAL_R, args=args, built=frozenset({W_KEY})) is None
    std = (num(0x81, "8'h"), named("(SP)", "SP")) if fn is PROVEN_K else (named("(SP)", "SP"),)
    assert judged(fn, REAL_R, STD_P, args=std, built=frozenset()) is not None  # not our file
    # a real localparam that no tool builds: dead code too
    real = (num(0x81, "8'h"), named("(RL)", "R")) if fn is PROVEN_K else (named("(RL)", "R"),)
    assert judged(fn, REAL_R, args=real, built=frozenset()) is None
    assert judged(fn, REAL_R, args=real, built=frozenset({("core.v", 4, "R")})) is not None


def test_a_local_parameter_inside_a_begin_of_a_function_is_a_local_too() -> None:
    """Round 21 FL4: a localparam in a `begin ... end` of a function is still one of its locals
    (counted as built: `f(R)` there is no dead code, whatever `built` says)."""
    nested = var("(NR)", "R", 6, varType="LPARAM", isParam=True, valuep=[const("inf.0")])
    inner = call("(cn)", "(F8)", "f", 7) | {"argsp": [{"type": "ARG", "exprp": [
        named("(NR)", "R")]}]}  # fmt: skip
    g = func("(G4)", "g", 5, local("(RG4)", "g", basic(7), isFuncReturn=True),
             {"type": "BEGIN", "stmtsp": [nested, set_return(inner, "(RG4)", "g")]})  # fmt: skip
    tree = module(*NAMED, SAFE_F, g)
    assert "(NR)" in et._function_locals(tree)
    got = et.constant_call(tree, META, OURS, plain=PLAIN, built=frozenset())
    assert got is not None and (got.name, got.line) == ("f", 7)


# Round 21 (fl01, fl04, r04, r15, r17): `localparam MIN_DEPTH = 2;` inside a constant helper,
# passed to another function with the helper's argument. Verilator's elaborated tree leaves out
# a function that it only works out for a parameter value (or never calls), so only the
# simulator lists the local, and the untyped local counted as a parameter that can hold a real.
UNTYPED = {"type": "BASICDTYPE", "keyword": "LOGIC_IMPLICIT"}
MIN_DEPTH = var("(MD)", "MIN_DEPTH", 6, varType="LPARAM", isParam=True, childDTypep=[UNTYPED],
                valuep=[const("?32?sh2")])  # fmt: skip
MD_KEY = ("core.v", 6, "MIN_DEPTH")


def helper(*args: dict[str, Any], kid: dict[str, Any] = MIN_DEPTH) -> dict[str, Any]:
    """`function [7:0] g(input [7:0] v); localparam MIN_DEPTH = 2; g = f(args...);`."""
    inner = call("(ch)", "(F8)", "f", 7) | {"argsp": [{"type": "ARG", "exprp": [a]} for a in args]}
    return func("(G5)", "g", 5, local("(RG5)", "g", basic(7), isFuncReturn=True),
                arg("(VG5)", "v", basic(7)), kid,
                {"type": "BEGIN", "stmtsp": [set_return(inner, "(RG5)", "g")]})  # fmt: skip


def test_a_function_local_that_only_the_simulator_lists_with_a_plain_number_holds_no_real() -> None:
    reads = ar("ADD", rd("(VG5)", "v"), named("(MD)", "MIN_DEPTH"))  # f(v + MIN_DEPTH)
    tree = module(*NAMED, SAFE_F, helper(reads))
    built = frozenset({MD_KEY})
    got = et.constant_call(tree, META, OURS, plain=PLAIN, built=built)
    assert got is not None and "with an argument that can hold a real" in got.sentence  # bf5eb0a
    lone = frozenset({MD_KEY})
    assert et.constant_call(tree, META, OURS, plain=PLAIN, built=built, simulator_only=lone) is None
    # another key, or the same name outside a function, still counts (fail closed)
    other = frozenset({("core.v", 7, "MIN_DEPTH")})
    assert et.constant_call(tree, META, OURS, plain=PLAIN, built=built, simulator_only=other)
    outside = module(*NAMED, SAFE_F, MIN_DEPTH, f8_call(named("(MD)", "MIN_DEPTH")))
    assert et.constant_call(outside, META, OURS, plain=PLAIN, built=built, simulator_only=lone)
    # and it is still not plain for a proven function's constant call: an argument that can be
    # x is judged as before (`_x_argument`)
    proven = module(*NAMED, PROVEN_K, helper(num(0x81, "8'h"), named("(MD)", "MIN_DEPTH")))
    got = et.constant_call(proven, META, OURS, plain=PLAIN, built=built, simulator_only=lone)
    assert got is not None and "with constant arguments" in got.sentence


def test_the_simulator_only_values_are_plain_numbers_verilator_does_not_list() -> None:
    a, b, c, d, e = (("core.v", n, x) for n, x in enumerate("ABCDE", 2))
    verilator = {a: [(("t",), 8)], b: None}
    simulator = {a: [(("t",), 8)], b: [(("t",), 1)], c: [(("t", "f"), 2)], d: None, e: []}
    assert et.simulator_only_values(verilator, simulator) == {c}  # d: a real or a string
    assert et.simulator_only_values(verilator, {}) == set()


# Round 23 (a01, a04, e02, e03, e06, e08, e10): a localparam inside a function that Verilator only
# works out as a constant (in a range or a replication count) is listed by the simulator alone,
# so its value was never compared, and `1 << 32`, `8'hff + 8'h01` or `$bits(a)` there differ
# between Icarus and Verilator/Yosys. `uncompared_local` refuses such a local unless it is one
# number as written.
M_KEY = ("core.v", 6, "M")
LISTED = {M_KEY: [(("tt_um_x", "core", "h"), 2)]}


def m_local(value: dict[str, Any], dtype: dict[str, Any] = UNTYPED) -> dict[str, Any]:
    return var("(ML)", "M", 6, varType="LPARAM", isParam=True, isFuncLocal=True,
               childDTypep=[dtype], valuep=[value])  # fmt: skip


def h_with(*kids: dict[str, Any], kind: str = "FUNC") -> dict[str, Any]:
    """`function integer h(input integer a); kids...; endfunction`."""
    return func("(H)", "h", 5, local("(RH)", "h", INTEGER, isFuncReturn=True),
                arg("(AH)", "a", INTEGER), *kids, kind=kind)  # fmt: skip


def lone_local(
    *kids: dict[str, Any],
    verilator: et.ParamValues | None = None,
    simulator: et.ParamValues | None = None,
) -> et.Unknown | None:
    tree = module(*NAMED, *kids)
    return et.uncompared_local(
        tree, META, OURS, {} if verilator is None else verilator,
        LISTED if simulator is None else simulator,
    )  # fmt: skip


ATTR_BITS = {"type": "ATTROF", "attrType": "DIM_BITS", "fromp": [rd("(AH)", "a")]}


@pytest.mark.parametrize(
    ("value", "dtype"),
    [
        pytest.param(ar("SHIFTL", num(1), num(32)), UNTYPED, id="shift-1<<32"),
        pytest.param(ar("ADD", num(0xFF, "8'h"), num(1, "8'h")), UNTYPED, id="add-overflow"),
        pytest.param(ATTR_BITS, UNTYPED, id="bits-of-argument"),
        pytest.param({"type": "NEGATE", "lhsp": [num(1)]}, UNTYPED, id="negative"),
        pytest.param(f8_call(num(3)), UNTYPED, id="a-call"),
        pytest.param(num(0x100000000, "?36?h"), UNTYPED, id="unsized-wider-than-32"),
        pytest.param(num(0x100000000, "?33?sh"), UNTYPED, id="unsized-decimal-2**32"),
        pytest.param(const("4'b1x00"), UNTYPED, id="x-bit"),
        pytest.param(const("1.0e999"), UNTYPED, id="real-literal"),
        pytest.param(num(2), {"type": "BASICDTYPE", "keyword": "real"}, id="real-type"),
        pytest.param(num(2), {"type": "REFDTYPE", "name": "t"}, id="typedef"),
        pytest.param(num(5), basic(ar("SUB", ATTR_BITS, num(1))), id="range-from-bits"),
        pytest.param(num(5), basic(num(0x100000000, "?36?h")), id="range-bound-unsized-wide"),
    ],
)
def test_a_function_local_that_only_the_simulator_lists_must_be_a_number_as_written(
    value: dict[str, Any], dtype: dict[str, Any]
) -> None:
    got = lone_local(h_with(m_local(value, dtype)))
    assert got is not None and (got.name, got.file, got.line) == ("M", "core.v", 6), got
    assert "declares the localparam `M` inside the function `h`" in got.sentence
    assert "Move the localparam out of the function" in got.sentence


@pytest.mark.parametrize(
    ("value", "dtype"),
    [
        pytest.param(num(2), UNTYPED, id="untyped-decimal"),  # flocal: MIN_DEPTH = 2
        pytest.param(num(0xFF, "8'h"), UNTYPED, id="sized"),
        pytest.param(num(0x80, "8'sh"), basic(7), id="signed-sized-with-range"),
        pytest.param(num(0x100000000, "33'h"), basic(32), id="sized-wider-than-32"),
        pytest.param(num(7), INTEGER, id="integer"),
        pytest.param(num(0x7FFFFFFF, "?32?h"), UNTYPED, id="unsized-32-bits"),
    ],
)
def test_a_function_local_written_as_a_number_needs_no_comparison(
    value: dict[str, Any], dtype: dict[str, Any]
) -> None:
    assert lone_local(h_with(m_local(value, dtype))) is None


def test_a_function_local_with_a_value_of_two_nodes_is_no_number_as_written() -> None:
    two = m_local(num(2)) | {"valuep": [num(2), num(3)]}
    assert lone_local(h_with(two)) is not None
    assert lone_local(h_with(m_local(num(2)) | {"valuep": []})) is not None


def test_a_function_local_is_judged_unless_both_tools_list_it_with_plain_numbers() -> None:
    shifted = h_with(m_local(ar("SHIFTL", num(1), num(32))))
    # compared by `equiv_icarus.disagreement`: fine here
    assert lone_local(shifted, verilator=LISTED) is None
    # listed by one tool only, by none, or not as a plain number: refused
    assert lone_local(shifted, verilator=LISTED, simulator={}) is not None
    assert lone_local(shifted, simulator={}) is not None
    assert lone_local(shifted, verilator={M_KEY: None}, simulator=LISTED) is not None
    assert lone_local(shifted, verilator=LISTED, simulator={M_KEY: None}) is not None
    # a number as written that the simulator lists as a string or real is refused too, while one
    # that no tool lists (a dead function) or that only the simulator lists is fine
    plain = h_with(m_local(num(0x4142, "16'h")))
    assert lone_local(plain, simulator={M_KEY: None}) is not None
    assert lone_local(plain, simulator={}) is None
    assert lone_local(plain) is None
    # another key does not stand for it
    other = {("core.v", 7, "M"): LISTED[M_KEY]}
    assert lone_local(shifted, verilator=other, simulator=other) is not None


def test_a_function_local_counts_as_compared_only_when_verilator_lists_every_copy() -> None:
    """Round 24 (fpart3, b19): Verilator keeps the function in the module copy that calls it at
    run time (u1) and leaves it out of the copy that only works it out as a constant (u2), where
    the local overflows. The simulator lists both copies with the value Verilator gives u1, so
    the values agree as sets, and `disagreement` cannot pair the paths: the local must be judged
    as one Verilator does not list (a number as written or refused)."""
    u1, u2 = ("tt_um_x", "core", "u1", "f"), ("tt_um_x", "core", "u2", "f")
    both = {M_KEY: [(u1, 2147483648), (u2, 2147483648)]}
    one = {M_KEY: [(u1, 2147483648)]}
    shifted = h_with(m_local(ar("SHIFTL", num(1), num(32))))
    got = lone_local(shifted, verilator=one, simulator=both)
    assert got is not None and (got.name, got.line) == ("M", 6)
    assert "declares the localparam `M` inside the function `h`" in got.sentence
    # whatever the order of the simulator's copies (round 25 C6: the copy Verilator leaves out
    # first, fpname)
    assert lone_local(shifted, verilator=one, simulator={M_KEY: both[M_KEY][::-1]}) is not None
    # every copy listed by Verilator, also in another order or with one more: compared
    assert lone_local(shifted, verilator=both, simulator=both) is None
    assert lone_local(shifted, verilator={M_KEY: both[M_KEY][::-1]}, simulator=both) is None
    assert lone_local(shifted, verilator=both, simulator=one) is None
    # the copy Verilator lists is another path than the simulator's: not compared
    other = {M_KEY: [(("tt_um_x", "core", "u3", "f"), 2147483648)]}
    assert lone_local(shifted, verilator=other, simulator=one) is not None
    # a number as written needs no comparison, wherever it is listed
    plain = h_with(m_local(num(0xFF, "8'h")))
    assert lone_local(plain, verilator=one, simulator=both) is None


def test_only_a_local_of_a_function_or_task_of_our_files_is_judged() -> None:
    shifted = ar("SHIFTL", num(1), num(32))
    # a module-level localparam is compared by `equiv_icarus.disagreement` (or is a genvar)
    assert lone_local(m_local(shifted)) is None
    assert lone_local(h_with(m_local(shifted), kind="TASK")) is not None
    # in a `begin ... end` of the function
    assert lone_local(h_with({"type": "BEGIN", "stmtsp": [m_local(shifted)]})) is not None
    # a function of a file that is not ours
    theirs = func("(H)", "h", 5, m_local(shifted) | {"loc": "d,6:1,6:4"}, file="d")
    assert lone_local(theirs) is None


def test_the_first_local_that_is_not_compared_is_named() -> None:
    first = var("(MA)", "A", 3, varType="LPARAM", isParam=True, isFuncLocal=True,
                childDTypep=[UNTYPED], valuep=[ar("ADD", num(1), num(1))])  # fmt: skip
    g = func("(G)", "g", 2, first)
    got = lone_local(h_with(m_local(ar("SHIFTL", num(1), num(32)))), g)
    assert got is not None and (got.name, got.line) == ("A", 3)
    assert "inside the function `g`" in got.sentence


@pytest.mark.parametrize(
    "name",
    [
        "__VhshA1dlyVwN2C4BND9YaIpsePOeaBBJGrBmlEar82xW",  # a plain name of 128 characters
        "M______Vhsh3kQ9zXbN",  # `\M.............................`: an escaped name
    ],
)
def test_a_parameter_name_that_verilator_hashed_fails_closed(name: str) -> None:
    """Round 21 k04, k05: Verilator 5.046 replaces a name of 128 characters or more by a hash,
    so the simulator's key of that parameter never matches and its values (an untyped
    localparam that overflows) were never compared. Both trees raise, also for a local of a
    function that only the linked tree holds; a parameter of another file does not."""
    tree = {"type": "NETLIST", "modulesp": [mod("(T)", "tt_um_core", 1,
                                                elab_param("(H)", name, 8, "32'sh0"))]}  # fmt: skip
    with pytest.raises(et.TreeError, match=r"core\.v, line 8: Verilator shortens") as raised:
        et.parameter_values(tree, META, OURS)
    assert len(f"no usable syntax tree (TreeError: {raised.value})") <= 200  # `clip` in equiv
    assert "shorter name (`__`, `$` and an escaped name's symbols count as" in str(raised.value)
    hashed = MIN_DEPTH | {"name": name}
    linked = module(*NAMED, SAFE_F, helper(rd("(VG5)", "v"), kid=hashed))
    with pytest.raises(et.TreeError, match="use a shorter name"):
        et.constant_call(linked, META, OURS, plain=PLAIN)
    std = module(*NAMED, SAFE_F, STD_P | {"name": name})
    assert et.constant_call(std, META, OURS, plain=PLAIN) is None


def test_the_built_values_are_what_either_tool_builds() -> None:
    a, b, c, d = ("core.v", 2, "A"), ("core.v", 3, "B"), ("core.v", 4, "C"), ("core.v", 5, "D")
    e = ("core.v", 6, "E")
    verilator = {a: [(("t",), 8)], b: None, c: [], d: []}  # c, d: a module with no instance
    simulator = {d: [(("t",), 1)], e: None}
    assert et.built_values(verilator, simulator) == {a, b, d, e}
    assert et.built_values(verilator, {}) == {a, b}  # a value that is not a number: built
    assert et.built_values({}, simulator) == {d, e}


# -- round 18: guards of `_Ranges` that only a test of their own can pin --------------------------

V16 = arg("(V16)", "u", basic(15))  # a 16-bit argument
U2 = arg("(U2)", "q", basic(1))  # a 2-bit argument
H2 = arg("(H2)", "h", basic(1, signed=True))  # a signed 2-bit argument
S4L = local("(S4L)", "s", basic(3, signed=True))  # a signed 4-bit local
B8 = local("(B8)", "i", {"type": "BASICDTYPE", "keyword": "byte", "range": "7:0", "signed": True})
REALARG = arg("(RA)", "r", {"type": "BASICDTYPE", "keyword": "real"})
# `logic [7:-4]`: a range with a negative bound (as a signed constant)
NEGV = arg("(NV)", "n", {"type": "BASICDTYPE", "keyword": "logic", "rangep": [{
    "type": "RANGE", "leftp": [num(7)], "rightp": [num(0xFFFFFFFC)]}]})  # fmt: skip
WIDE = local("(WD)", "t", basic(255))  # a 256-bit local


def u_at(index: dict[str, Any]) -> dict[str, Any]:
    """`f = u[index]`, u 16 bits."""
    return to_f(read_bit(rd("(V16)", "u"), index))


@pytest.mark.parametrize(
    ("fn", "known"),
    [
        # tg68 (G68): t[k][0] with `reg [7:0] t [0:3]` and a 3-bit k: the array has 4 elements,
        # not 8 (its element's packed range is not the array's)
        pytest.param(f8(MEM, to_f(read_bit(read_bit(rd("(MEM)", "mem"), rd("(K3)", "k")),
                                           num(0))), extra=(K3,)), None, id="array-index"),
        # tg21 (G21): v[i - q] for i = 0 to 7, q 2 bits: i - q reaches -3
        pytest.param(f8(*for_loop("(I)", num(0), lt(rd("(I)", "i"), num(8)),
                                  [v_at(ar("SUB", rd("(I)", "i"), rd("(U2)", "q")))]),
                        extra=(U2,)), None, id="loop-minus-argument"),
        # tg1 (G1): u[s - 4'sb1000], s signed 4 bits: 7 + 8 wraps to -1 in 4-bit arithmetic
        pytest.param(f8(u_at(ar("SUB", rd("(S4)", "s"), num(8, "4'sh"))), extra=(V16, S4)),
                     None, id="signed-wraps"),
        # G22: t[s * h + 8'sd0], s signed 4 bits and h signed 2 bits: -8 * 1 is -8 (a corner
        # of the low and the high end), outside the 256-bit t
        pytest.param(f8(WIDE, to_f(read_bit(rd("(WD)", "t"), ar("ADD", ar(
            "MUL", rd("(S4)", "s"), rd("(H2)", "h")), num(0, "8'sh")))), extra=(S4, H2)),
                     None, id="multiply-corners"),
        # G2: n[k[1:0] - 1] with `logic [7:-4] n`: an unsigned context reads 0 - 1 as a large
        # number, not -1, even though -1 lies inside n's range
        pytest.param(f8(to_f(read_bit(rd("(NV)", "n"), ar("SUB", part(rd("(K3)", "k"), 1, 0),
                                                          num(1)))), extra=(NEGV, K3)),
                     None, id="unsigned-negative"),
        # G24: t[i * W] for a byte i = 0 to 39 and W = 5 of unknown sign: 0 to 195 fits 8 bits
        # unsigned but not signed (a signed W makes the product signed: 195 is -61)
        pytest.param(f8(WIDE, B8, *for_loop("(B8)", num(0), lt(rd("(B8)", "i"), num(40)),
                                            [to_f(read_bit(rd("(WD)", "t"), ar(
                                                "MUL", rd("(B8)", "i"), rd("(WP)", "W"))))])),
                     {("core.v", 2, "W"): 5}, id="unknown-sign-signed"),
        # G25: n[W - i] for a byte i = 0 to 7 and W = 5 of unknown sign: -2 to 5 fits signed,
        # but an unsigned W makes it unsigned (5 - 7 is 254)
        pytest.param(f8(B8, *for_loop("(B8)", num(0), lt(rd("(B8)", "i"), num(8)),
                                      [to_f(read_bit(rd("(NV)", "n"), ar(
                                          "SUB", rd("(WP)", "W"), rd("(B8)", "i"))))]),
                        extra=(NEGV,)), {("core.v", 2, "W"): 5}, id="unknown-sign-unsigned"),
        # G8: v[r] with a real argument r: only a vector or integer type has a range
        pytest.param(f8(v_at(rd("(RA)", "r")), extra=(REALARG,)), None, id="real-index"),
        # G10: v[2][k]: a select of one bit of a plain vector has no range of its own
        pytest.param(f8(to_f(read_bit(read_bit(rd("(V8)", "v"), num(2)), rd("(K3)", "k"))),
                        extra=(K3,)), None, id="bit-of-a-bit"),
    ],
)  # fmt: skip
def test_a_range_guard_refuses_what_its_operands_cannot_bound(
    fn: dict[str, Any], known: dict[et.ParamKey, int] | None
) -> None:
    got = judged(fn, SEGS, WPAR, G, agreed=known)
    assert got is not None and got.name == "f" and got.line == 9


def test_the_range_guard_cases_have_proven_twins() -> None:
    """The same shapes inside the range are proven, so each case above fails on its guard."""
    twins = [
        # t[k[1:0]][0]: 4 elements
        f8(MEM, to_f(read_bit(read_bit(rd("(MEM)", "mem"), part(rd("(K3)", "k"), 1, 0)),
                              num(0))), extra=(K3,)),
        # v[i - q] for i = 3 to 7: 0 to 7
        f8(*for_loop("(I)", num(3), lt(rd("(I)", "i"), num(8)),
                     [v_at(ar("SUB", rd("(I)", "i"), rd("(U2)", "q")))]), extra=(U2,)),
        # u[k + 8], k 3 bits: 8 to 15 (the 16-bit vector of the cases above)
        f8(u_at(ar("ADD", rd("(K3)", "k"), num(8))), extra=(V16, K3)),
        # t[s * h + 8'sd16]: -14 + 16 to 16 + 16
        f8(WIDE, to_f(read_bit(rd("(WD)", "t"), ar("ADD", ar(
            "MUL", rd("(S4)", "s"), rd("(H2)", "h")), num(16, "8'sh")))), extra=(S4, H2)),
    ]  # fmt: skip
    for fn in twins:
        assert judged(fn, SEGS, WPAR, G) is None
    # t[i * W] for a byte i = 0 to 24 and W = 5: 0 to 120 fits 8 bits signed and unsigned
    wide = f8(WIDE, B8, *for_loop("(B8)", num(0), lt(rd("(B8)", "i"), num(25)), [to_f(read_bit(
        rd("(WD)", "t"), ar("MUL", rd("(B8)", "i"), rd("(WP)", "W"))))]))  # fmt: skip
    assert judged(wide, SEGS, WPAR, G, agreed={("core.v", 2, "W"): 5}) is None


def test_a_parameter_of_unknown_sign_is_one_bit_wider_when_it_is_signed() -> None:
    """G71 (round 19): t[i + W] for a byte i = 0 to 3 and W = 200 of unknown sign: 200 to 203
    fits 8 bits unsigned, and a signed W is at least 9 bits wide (200 needs them), so the sum
    fits 9 bits signed too and the select is proven. Without that one bit it would not fit 8
    bits signed, and the call would be refused."""
    fn = f8(WIDE, B8, *for_loop("(B8)", num(0), lt(rd("(B8)", "i"), num(4)), [to_f(read_bit(
        rd("(WD)", "t"), ar("ADD", rd("(B8)", "i"), rd("(WP)", "W"))))]))  # fmt: skip
    assert judged(fn, SEGS, WPAR, G, agreed={("core.v", 2, "W"): 200}) is None
    assert judged(fn, SEGS, WPAR, G, agreed={("core.v", 2, "W"): 253}) is not None  # to 256


@pytest.mark.parametrize(
    "stmts",
    [
        # G39: an unsigned 4-bit u counting up while u < 16 never stops (u wraps to 0)
        pytest.param(for_loop("(U4)", num(0), lt(rd("(U4)", "u"), num(16)),
                              [u_at(rd("(U4)", "u"))]), id="up-past-the-type"),
        # G42: a signed 4-bit s counting down while s >= -8 never stops (s wraps to 7)
        pytest.param(for_loop("(S4L)", num(7, "4'sh"), lt(rd("(S4L)", "s"), num(8, "4'sh"),
                                                          "GTES"),
                              [u_at(ar("ADD", rd("(S4L)", "s"), num(8)))], step=-1),
                     id="down-past-the-type"),
        # G40, G45: an integer i from -2 while i < 4'd8: an unsigned compare reads -2 as large
        pytest.param(for_loop("(I)", num(0xFFFFFFFE), lt(rd("(I)", "i"), num(8, "4'h")),
                              [u_at(ar("ADD", rd("(I)", "i"), num(2)))]), id="up-mixed-sign"),
        # G43, G45: an integer i from 7 while i >= 4'd0: unsigned, never false, i goes below 0
        pytest.param(for_loop("(I)", num(7), lt(rd("(I)", "i"), num(0, "4'h"), "GTE"),
                              [u_at(rd("(I)", "i"))], step=-1), id="down-mixed-sign"),
        # G44: counting up while i > -8: the test does not bound i from above
        pytest.param(for_loop("(I)", num(0), lt(rd("(I)", "i"), num(0xFFFFFFF8), "GTS"),
                              [u_at(ar("ADD", rd("(I)", "i"), num(7)))]), id="up-with-greater"),
    ],
)  # fmt: skip
def test_a_loop_guard_alone_refuses_a_loop_over_a_vector_wide_enough(
    stmts: list[dict[str, Any]],
) -> None:
    """Each loop indexes a 16-bit vector that every value the loop would claim fits, so only the
    loop's own guard can refuse it (the loops of `test_a_loop_that_is_not_simple_...` read an
    8-bit vector, which refuses them anyway)."""
    got = judged(f8(*stmts, extra=(V16, U4, S4L)), SEGS, WPAR)
    assert got is not None and got.name == "f"


# -- an argument that Yosys works out at its own width (narrow_argument) -------------------------

NL_KEY = ("core.v", 2, "LAST")
CORE_PATH = ("tt_um_x", "core")


def lparam(addr: str, name: str, line: int, dtype: dict[str, Any]) -> dict[str, Any]:
    return var(addr, name, line, varType="LPARAM", isParam=True, childDTypep=[dtype])


def fn(addr: str, name: str, ret: dict[str, Any], *ports: dict[str, Any]) -> dict[str, Any]:
    """`function name(ports...)` with its return variable under `fvarp`, as Verilator links it."""
    back = local(f"({name}_r)", name, ret, isFuncReturn=True, direction="OUTPUT")
    return func(addr, name, 5, *ports) | {"fvarp": [back]}


LAST = lparam("(NL)", "LAST", 2, basic(3))  # localparam [3:0] LAST, agreed 15
SA = lparam("(NSA)", "SA", 2, basic(3, signed=True))  # localparam signed [3:0] SA = -8
AMT = lparam("(NAM)", "AMT", 2, basic(3))  # a shift amount
NS4 = var("(NS)", "s", 3, varType="VAR", childDTypep=[basic(3)])  # a 4-bit signal
NS8 = var("(NW)", "w", 3, varType="VAR", childDTypep=[basic(7)])  # an 8-bit signal
F_8 = fn("(NF)", "f", basic(7), arg("(NFA)", "a", basic(7)))  # f(input [7:0] a)
F_2 = fn("(NG)", "g", basic(7), arg("(NGA)", "a", basic(7)), arg("(NGB)", "b", basic(3)))
CLOG2 = fn("(NC)", "clog2", INTEGER, arg("(NCV)", "value", INTEGER))  # input integer
SHAPES = {
    NL_KEY: [(CORE_PATH, (4, False))],
    ("core.v", 2, "SA"): [(CORE_PATH, (4, True))],
    ("core.v", 2, "AMT"): [(CORE_PATH, (4, False))],
}
AGREED = {NL_KEY: 15, ("core.v", 2, "SA"): -8, ("core.v", 2, "AMT"): 3}


def last() -> dict[str, Any]:
    return ref(f"({next(_N)})", "(NL)", "LAST", 9)


def nref(to: str, name: str) -> dict[str, Any]:
    return ref(f"({next(_N)})", to, name, 9)


def un(kind: str, a: dict[str, Any]) -> dict[str, Any]:
    return {"type": kind, "lhsp": [a]}


def s4() -> dict[str, Any]:
    return nref("(NS)", "s")


def w8() -> dict[str, Any]:
    return nref("(NW)", "w")


def carry() -> dict[str, Any]:
    """`LAST + 4'd1`: 16 at 8 bits, 0 at 4."""
    return ar("ADD", last(), num(1, "4'h"))


def low_k(to: str) -> dict[str, Any]:
    """`k[3:0] + 4'd15`."""
    low = {"type": "SELEXTRACT", "fromp": [nref(to, "k")], "leftp": [num(3)], "rightp": [num(0)]}
    return ar("ADD", low, num(15, "4'h"))


def call_of(*args: dict[str, Any], to: str = "(NF)", name: str = "f", line: int = 9,
            labels: tuple[str, ...] = ()) -> dict[str, Any]:  # fmt: skip
    """`name(args...)` at `line` (`.label(arg)` for each of `labels`)."""
    pairs = zip(labels, args[: len(labels)], strict=True)
    named_args = [{"type": "ARG", "name": n, "exprp": [a]} for n, a in pairs]
    plain = [{"type": "ARG", "exprp": [a]} for a in args[len(labels) :]]
    return call(f"({next(_N)})", to, name, line) | {"argsp": named_args + plain}


def narrow(
    *kids: dict[str, Any],
    agreed: dict[Any, int] | None = None,
    shapes: dict[Any, Any] | None = None,
) -> et.Unknown | None:
    tree = module(LAST, SA, AMT, NS4, NS8, F_8, F_2, CLOG2, *kids)
    return et.narrow_argument(
        tree, META, OURS, AGREED if agreed is None else agreed, SHAPES if shapes is None else shapes
    )


def test_a_constant_argument_that_carries_past_its_own_width_is_refused() -> None:
    """Round 25 (k07, k03): `localparam AW = clog2(LAST + 1'b1);` with `localparam [3:0] LAST =
    4'd15` is 16 in a simulator and Verilator (the 32-bit input is the context) and 0 in Yosys
    0.62, which works the constant argument out at its own 4 bits; `generate if (AW == 4)` took
    the other branch in the gates."""
    plus_one = ar("ADD", last(), num(1, "1'h"))
    aw = lparam("(AW)", "AW", 9, UNTYPED)
    got = narrow(aw | {"valuep": [call_of(plus_one, to="(NC)", name="clog2")]})
    assert got is not None and (got.name, got.line) == ("clog2", 9)
    narrower = "with an argument that is 4 bits wide, narrower than the function's input (32 bits)"
    assert narrower in got.sentence
    assert "A simulator works the argument out at the input's width, Yosys 0.62" in got.sentence
    assert "Make the argument as wide as the input: use a plain number (`LAST + 1`" in got.sentence
    assert "`localparam integer N = LAST + 1'b1;`, then `clog2(N)`)" in got.sentence
    # anywhere: an assign, an override, an always block (any FUNCREF of the tree)
    assert narrow(un("ASSIGNW", call_of(carry()))) is not None


@pytest.mark.parametrize(
    ("agreed", "shapes", "arg", "refused"),
    [
        # LAST = 14: 14 + 1 = 15 fits 4 bits, the same at every width
        pytest.param({NL_KEY: 14}, None, None, False, id="no-carry"),
        # localparam [4:0] LAST = 5'd15: 16 fits 5 bits
        pytest.param(None, {NL_KEY: [(CORE_PATH, (5, False))]}, None, False, id="five-bits"),
        # no agreed value of LAST: not proven
        pytest.param({}, None, None, True, id="no-value"),
        # LAST + 1 (unsized: 32 bits, as wide as the input)
        pytest.param(None, None, ar("ADD", last(), num(1)), False, id="unsized"),
        # {4'd0, LAST} + 8'd1 (Verilator writes `{1{...}}`): 16 fits 8 bits
        pytest.param(None, None, ar("ADD", {"type": "REPLICATE", "srcp": [
            ar("CONCAT", num(0, "4'h"), last())], "countp": [num(1)]}, num(1, "8'h")), False,
            id="widened"),
        # the narrowest width of a parameter with several: 4 bits in one instance
        pytest.param({}, {NL_KEY: [(CORE_PATH, (32, False)), ((*CORE_PATH, "u"), (4, False))]},
                     None, True, id="narrowest-instance"),
        pytest.param({}, {NL_KEY: [(CORE_PATH, (32, False))]}, None, False, id="wide-instance"),
    ],
)  # fmt: skip
def test_a_value_proven_the_same_at_every_width_or_a_wide_argument_passes(
    agreed: dict[Any, int] | None, shapes: dict[Any, Any] | None, arg: dict[str, Any] | None,
    refused: bool,
) -> None:  # fmt: skip
    """`clog2(LAST + 1'b1)` passes when no bit is cut at the argument's own width (a value that
    both tools agree on, every result on the way below 2 ** width), or when the argument is
    already as wide as the input; the narrowest instance of a parameter counts."""
    expr = arg if arg is not None else ar("ADD", last(), num(1, "1'h"))
    got = narrow(un("ASSIGNW", call_of(expr, to="(NC)", name="clog2")), agreed=agreed,
                 shapes=shapes)  # fmt: skip
    assert (got is not None) == refused


@pytest.mark.parametrize(
    "expr",
    [
        pytest.param(un("NOT", last()), id="not"),  # ~4'hf is 0 in 4 bits, f0 in 8
        pytest.param(un("NEGATE", last()), id="negate"),
        pytest.param(ar("SHIFTL", last(), num(1)), id="shift-left"),
        pytest.param(ar("MUL", last(), num(2, "4'h")), id="multiply"),
        pytest.param(ar("SUB", num(0, "4'h"), num(1, "4'h")), id="borrow"),
        pytest.param(ar("POW", last(), num(2, "2'h")), id="power"),
        pytest.param(un("NOT", ar("XOR", last(), num(3, "4'h"))), id="xnor"),
        pytest.param(ar("SHIFTR", nref("(NSA)", "SA"), num(1)), id="signed-shift-right"),
        pytest.param(ar("DIV", nref("(NSA)", "SA"), num(0xF, "4'sh")), id="signed-divide"),
        pytest.param(ar("ADD", num(7, "4'sh"), num(1, "4'sh")), id="signed-sign-bit"),
        # `t'(LAST + 4'd1)` for a typedef `t`: a width the check does not know
        pytest.param(un("CASTPARSE", ar("ADD", last(), num(1, "4'h"))) | {"dtp": [
            {"type": "REFDTYPE"}], "loc": "e,9:7,9:8"}, id="cast"),
        pytest.param(ar("SHIFTL", num(1, "4'h"), nref("(NAM)", "AMT")), id="shift-by-amount"),
        # `4'd1 << $bits(s)`: `$bits` of a signal is a number, not a read of the signal
        pytest.param(ar("SHIFTL", num(1, "4'h"), {"type": "ATTROF", "fromp": [s4()]}),
                     id="shift-by-bits"),
    ],
)  # fmt: skip
def test_an_operator_that_a_wider_width_can_change_is_refused(expr: dict[str, Any]) -> None:
    """Probed in Icarus, Verilator and Yosys 0.62 (f with an 8-bit input): each differs in the
    gates. A cast is a node the walk does not know (fail closed), and so is a typedef's width."""
    agreed = AGREED | ({("core.v", 2, "AMT"): 4} if "AMT" in str(expr) else {})
    got = narrow(un("ASSIGNW", call_of(expr)), agreed=agreed)
    assert got is not None and got.name == "f"


@pytest.mark.parametrize(
    "expr",
    [
        pytest.param(ar("AND", last(), num(3, "4'h")), id="and"),
        pytest.param(ar("OR", last(), num(0, "4'h")), id="or"),
        pytest.param(ar("SHIFTR", last(), num(1)), id="unsigned-shift-right"),
        pytest.param(ar("SHIFTRS", nref("(NSA)", "SA"), num(1)), id="arithmetic-shift-right"),
        pytest.param(ar("DIV", last(), num(2, "4'h")), id="unsigned-divide"),
        pytest.param(un("NOT", nref("(NW)", "w")), id="not-of-a-signal"),
        pytest.param(ar("CONCAT", ar("ADD", last(), num(1, "4'h")), nref("(NS)", "s")),
                     id="self-determined-concatenation"),
        pytest.param({"type": "COND", "condp": [ar("EQ", nref("(NW)", "w"), ar(
            "ADD", last(), num(1, "4'h")))], "thenp": [num(1, "8'h")], "elsep": [num(2, "8'h")]},
                     id="compare-in-a-condition"),
        pytest.param(nref("(NSA)", "SA"), id="a-signed-parameter-alone"),
        pytest.param(ar("ADD", num(7, "4'h"), num(1, "4'h")), id="unsigned-top-bit"),
        pytest.param(ar("SHIFTL", num(1, "4'h"), nref("(NAM)", "AMT")), id="shift-by-3"),
        # as wide as the input: the same carry in every tool, whatever the value
        pytest.param(ar("ADD", num(0xFF, "8'h"), num(1, "8'h")), id="as-wide-as-the-input"),
    ],
)  # fmt: skip
def test_an_operator_that_keeps_its_bits_or_a_value_that_fits_passes(expr: dict[str, Any]) -> None:
    """Each gives the same bits in Icarus, Verilator and Yosys 0.62 (f with an 8-bit input)."""
    assert narrow(un("ASSIGNW", call_of(expr))) is None


def test_only_a_part_that_reads_no_signal_is_worked_out_at_the_argument_width() -> None:
    """Round 25 (probes a5, a6, a8): Yosys works a part of the argument that reads no signal out
    at the width of the whole argument: `f(s + (LAST + 4'd1))` with a 4-bit `s` loses the carry,
    `f(w + (LAST + 4'd1))` with an 8-bit `w` (as wide as the input) does not, and neither does
    `f((s + LAST) + 4'd1)`, whose parts that read no signal are plain numbers. The arms of `?:`
    take the context, its condition does not."""
    assert narrow(un("ASSIGNW", call_of(ar("ADD", s4(), carry())))) is not None
    assert narrow(un("ASSIGNW", call_of(ar("OR", carry(), s4())))) is not None
    arms = {"type": "COND", "condp": [s4()], "thenp": [num(0, "4'h")], "elsep": [carry()]}
    assert narrow(un("ASSIGNW", call_of(arms))) is not None
    assert narrow(un("ASSIGNW", call_of(ar("ADD", w8(), carry())))) is None
    assert narrow(un("ASSIGNW", call_of(ar("ADD", ar("ADD", s4(), last()), num(1, "4'h"))))) is None
    assert narrow(un("ASSIGNW", call_of(ar("ADD", s4(), num(1, "4'h"))))) is None
    # another argument that reads a signal does not help (probe a4: f2(A + 4'd1, s))
    assert narrow(un("ASSIGNW", call_of(carry(), s4(), to="(NG)", name="g"))) is not None
    # a named argument is matched to its port: `.b` is 4 bits, so `LAST + 4'd1` fills it
    to_b = call_of(carry(), w8(), to="(NG)", name="g", labels=("b", "a"))
    assert narrow(un("ASSIGNW", to_b)) is None
    to_a = call_of(carry(), w8(), to="(NG)", name="g", labels=("a", "b"))
    assert narrow(un("ASSIGNW", to_a)) is not None


def test_a_loop_variable_or_a_function_variable_counts_as_a_number() -> None:
    """Round 25 (probes a2, a3d, a3f): Yosys puts a number in for the variable of a `for` loop it
    unrolls (`f(k[3:0] + 4'd15)` in an always block or a function) and for the variables of a
    function it works out with constant arguments (`f(x + 4'd1)` inside `g(4'd15)`), so these
    count as numbers; any other signal keeps Yosys from working the part out first."""
    flagged = var("(NK)", "k", 3, varType="VAR", childDTypep=[INTEGER], isUsedLoopIdx=True)
    plain = var("(NK)", "k", 3, varType="VAR", childDTypep=[INTEGER])
    assert narrow(flagged, un("ASSIGNW", call_of(low_k("(NK)")))) is not None
    assert narrow(plain, un("ASSIGNW", call_of(low_k("(NK)")))) is None
    # not marked, but set by the statement right before a LOOP (a `for` loop's first assignment)
    start = assign(nref("(NK)", "k") | {"access": "WR"}, num(0))
    loop = {"type": "LOOP", "stmtsp": [call_of(low_k("(NK)"))]}
    assert narrow(plain, {"type": "ALWAYS", "stmtsp": [start, loop]}) is not None
    # an argument of the function the call is in
    x = arg("(NX)", "x", basic(3))
    inside = fn("(NH)", "h", basic(7), x, call_of(ar("ADD", nref("(NX)", "x"), num(1, "4'h"))))
    assert narrow(inside) is not None
    assert narrow(fn("(NH)", "h", basic(7), x, call_of(nref("(NX)", "x")))) is None  # no operator


def test_an_input_or_a_width_the_check_cannot_work_out_fails_closed() -> None:
    """A call of a function the tree does not hold, an argument of a type the check cannot size:
    refused when a part that reads no signal can carry, never for a plain one."""
    assert narrow(un("ASSIGNW", call_of(carry(), to="(NOPE)"))) is not None
    assert narrow(un("ASSIGNW", call_of(last(), to="(NOPE)"))) is None
    typed = var("(NT)", "t", 3, varType="VAR", childDTypep=[{"type": "REFDTYPE"}])
    assert narrow(typed, un("ASSIGNW", call_of(ar("ADD", nref("(NT)", "t"), carry())))) is not None
    assert narrow(typed, un("ASSIGNW", call_of(ar("ADD", nref("(NT)", "t"), num(1))))) is None


def cast_of(operand: dict[str, Any], size: dict[str, Any] | None = None,
            to: dict[str, Any] | None = None) -> dict[str, Any]:  # fmt: skip
    """`size'(operand)` (CASTPARSE), or `type'(operand)` (CAST) for a dtype `to`, at line 9."""
    if to is not None:
        return {"type": "CAST", "fromp": [operand], "childDTypep": [to], "loc": "e,9:5,9:8"}
    return {"type": "CASTPARSE", "lhsp": [operand], "dtp": [size], "loc": "e,9:5,9:8"}


def test_a_size_or_type_cast_of_a_constant_that_carries_is_refused_too() -> None:
    """Round 25 (probes lp_widen, lp_int, sig_int): Yosys 0.62 works the operand of a cast out
    the same way, at its own width first: `localparam integer P = 8'(LAST + 4'd1);` and
    `assign y = int'(LAST + 4'd1);` are 16 in Icarus and Verilator and 0 in the gates."""
    p = lparam("(NP)", "P", 9, INTEGER)
    got = narrow(p | {"valuep": [cast_of(carry(), num(8))]})
    assert got is not None and (got.name, got.line) == ("", 9)
    assert "casts a value that is 4 bits wide, narrower than the cast (8 bits)" in got.sentence
    assert "A simulator works the operand out at the cast's width" in got.sentence
    assert "Make the operand as wide as the cast" in got.sentence
    assert "then `8'(N)`)" in got.sentence
    got = narrow(un("ASSIGNW", cast_of(carry(), to=INTEGER)))
    assert got is not None and "narrower than the cast (32 bits)" in got.sentence
    # `W'(...)`: the width of a parameter's agreed value (8, or 4: as wide as the operand)
    assert narrow(un("ASSIGNW", cast_of(carry(), nref("(NAM)", "AMT"))),
                  agreed=AGREED | {("core.v", 2, "AMT"): 8}) is not None  # fmt: skip
    assert narrow(un("ASSIGNW", cast_of(carry(), nref("(NAM)", "AMT"))),
                  agreed=AGREED | {("core.v", 2, "AMT"): 4}) is None  # fmt: skip
    # `f(8'(LAST + 4'd1))`: the argument is as wide as the input, the cast is what differs;
    # `4'(integer'(R))` cuts a 32-bit value (the realfin control): no carry is lost
    got = narrow(un("ASSIGNW", call_of(cast_of(carry(), num(8)))))
    assert got is not None and got.name == "" and "narrower than the cast (8 bits)" in got.sentence
    assert narrow(un("ASSIGNW", cast_of(cast_of(last(), to=INTEGER), num(4)))) is None
    assert narrow(un("ASSIGNW", call_of(cast_of(last(), num(8))))) is None  # f(8'(LAST)): 8 bits
    # a typedef's width is not known: refused only when a part can carry
    assert narrow(un("ASSIGNW", cast_of(carry(), {"type": "REFDTYPE"}))) is not None
    assert narrow(un("ASSIGNW", cast_of(last(), {"type": "REFDTYPE"}))) is None


@pytest.mark.parametrize(
    "cast",
    [
        pytest.param(lambda: cast_of(last(), num(8)), id="a-parameter-alone"),  # 8'(LAST)
        pytest.param(lambda: cast_of(ar("ADD", s4(), num(1, "4'h")), num(8)), id="a-signal"),
        pytest.param(lambda: cast_of(ar("ADD", num(0xFF, "8'h"), num(1, "8'h")), num(4)),
                     id="narrowing"),  # 4'(8'hff + 8'h01): every tool cuts the same 8 bits
        pytest.param(lambda: cast_of(ar("ADD", last(), num(1, "4'h")), num(8)), id="no-carry"),
    ],
)  # fmt: skip
def test_a_cast_that_every_tool_works_out_the_same_passes(cast: Any) -> None:
    """Probed (lp_cast_plain, x_castsig, lp_narrow): the same bits in Icarus, Verilator and Yosys
    0.62; with LAST = 14 the sum fits 4 bits."""
    agreed = AGREED | {NL_KEY: 14}
    assert narrow(un("ASSIGNW", cast()), agreed=agreed) is None


def test_the_first_narrow_argument_by_file_order_then_line_and_only_in_our_files() -> None:
    later, earlier = call_of(carry(), line=12), call_of(carry(), line=10)
    got = narrow(un("ASSIGNW", later), un("ASSIGNW", earlier))
    assert got is not None and got.line == 10
    theirs = call_of(carry()) | {"loc": "d,9:5,9:8"}
    assert narrow(un("ASSIGNW", theirs)) is None
    with pytest.raises(et.TreeError):
        et.narrow_argument([], META, OURS)
