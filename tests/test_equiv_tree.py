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
