# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The packaged skills and glossary, and the `explain` tool."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from conftest import REPO_ROOT
from mcp import Client

from siliconsmith import knowledge
from siliconsmith.knowledge import Knowledge, load, parse_skill, split_sections
from siliconsmith.result import MAX_LINE_CHARS, summary_within_limits
from siliconsmith.server import build_server
from siliconsmith.tools.explain import explain_topic

SKILL_NAMES = ("amaranth-style", "chip-design-loop", "cocotb2-tests", "myhdl-style", "tt-rules")
GLOSSARY_TERMS = (
    "RTL",
    "HDL",
    "Testbench",
    "cocotb",
    "Simulation",
    "Icarus Verilog",
    "Verilator",
    "Lint",
    "Latch",
    "Multiple drivers",
    "Clock domain",
    "Reset",
    "Wrapper",
    "Synthesis",
    "Netlist",
    "Gate-level simulation",
    "Standard cell",
    "Utilization",
    "Do-not-use cell",
    "PDK",
    "sky130",
    "Place and route",
    "GDS",
    "DRC",
    "LVS",
    "Antenna",
    "Slack",
    "Setup and hold violation",
    "Timing closure",
    "Tile",
    "Shuttle",
    "Tiny Tapeout",
    "Waveform",
    "Spec",
    "Requirement coverage",
    "Verilog",
    "Combinational logic",
    "Register",
    "MCP server",
    "Docker image",
)

pytestmark = pytest.mark.anyio


# -- the packaged files ---------------------------------------------------------------------------


def test_skills_are_the_five_expected() -> None:
    assert tuple(sorted(d.name for d in load().skills)) == SKILL_NAMES


def test_front_matter_matches_folders() -> None:
    folders = {p.parent.name for p in (REPO_ROOT / "src/siliconsmith/skills").glob("*/SKILL.md")}
    assert folders == set(SKILL_NAMES)
    for doc in load().skills:
        assert doc.source == f"skills/{doc.name}/SKILL.md"
        assert doc.description.strip()
        assert "\n" not in doc.description
        assert doc.description.startswith("Use ")
        assert len(doc.sections) >= 1


def test_skill_sections_are_short_enough_to_read() -> None:
    for doc in load().skills:
        for section in doc.sections:
            prose = re.sub(r"```.*?```", "", section.text, flags=re.S)
            assert len(prose) <= 1500, f"{doc.name}/{section.heading} is too long"


def test_glossary_has_the_required_terms() -> None:
    headings = {s.heading for s in load().glossary.sections}
    assert set(GLOSSARY_TERMS) <= headings
    for section in load().glossary.sections:
        assert section.text.strip(), section.heading


def test_docs_glossary_is_the_packaged_glossary() -> None:
    packaged = (REPO_ROOT / "src/siliconsmith/skills/glossary.md").read_text(encoding="utf-8")
    assert (REPO_ROOT / "docs/glossary.md").read_text(encoding="utf-8") == packaged


def test_banned_cocotb_names_only_appear_in_the_cocotb_skill_or_docs() -> None:
    # The guard script skips Markdown on purpose; this keeps the old names out of the design
    # skills' code examples (the cocotb skill names them on purpose, in a table).
    for name in ("amaranth-style", "myhdl-style", "chip-design-loop", "tt-rules"):
        text = (REPO_ROOT / f"src/siliconsmith/skills/{name}/SKILL.md").read_text(encoding="utf-8")
        assert not re.search(r"BinaryValue|cocotb\.fork|units=", text), name


def test_every_skill_example_block_is_tagged_completely() -> None:
    for doc in load().skills:
        text = (REPO_ROOT / "src/siliconsmith" / doc.source).read_text(encoding="utf-8")
        for info in re.findall(r"^```(\S.*)$", text, re.M):
            if "example=" in info:
                for attr in ("project=", "frontend=", "file="):
                    assert attr in info, f"{doc.name}: {info!r} lacks {attr}"


def test_parse_skill_rejects_missing_front_matter() -> None:
    with pytest.raises(ValueError, match="front matter"):
        parse_skill("# no front matter\n\n## A\n\ntext\n", "x")


def test_parse_skill_needs_name_and_description() -> None:
    with pytest.raises(ValueError, match="name"):
        parse_skill("---\nname: x\n---\n## A\n", "x")


def test_split_sections_ignores_headings_inside_code_fences() -> None:
    body = "# Title\n\nintro\n\n## One\n\n```python\n## not a heading\n```\n\n## Two\n\ntext\n"
    intro, sections = split_sections(body)
    assert intro == "intro"
    assert [s.heading for s in sections] == ["One", "Two"]
    assert "## not a heading" in sections[0].text


# -- explain --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def kb() -> Knowledge:
    return load()


def test_skill_name_gives_overview_and_section_list(kb: Knowledge) -> None:
    result = explain_topic(kb, "chip-design-loop")
    assert result.ok
    assert result.details["source"] == "skills/chip-design-loop/SKILL.md"
    assert "Stop rules" in result.details["text"]
    assert "The steps" in result.summary


def test_section_heading_gives_that_section(kb: Knowledge) -> None:
    result = explain_topic(kb, "Stop rules")
    assert result.ok
    assert result.details["title"] == "chip-design-loop/Stop rules"
    assert "8" in result.details["text"]
    assert (
        explain_topic(kb, "chip-design-loop/Stop rules").details["title"] == result.details["title"]
    )


def test_glossary_term_gives_the_entry_with_aliases(kb: Knowledge) -> None:
    result = explain_topic(kb, "latch")
    assert result.details["source"] == "skills/glossary.md"
    assert result.details["title"] == "Latch"
    assert explain_topic(kb, "GDS").details["title"] == "GDS"
    assert explain_topic(kb, "Tiny-Tapeout").details["title"] == "Tiny Tapeout"


def test_keywords_find_the_best_section_and_suggestions(kb: Knowledge) -> None:
    result = explain_topic(kb, "how do I fix the active low reset in amaranth")
    assert result.ok and result.details["matched"] is True
    assert result.details["source"] == "skills/amaranth-style/SKILL.md"
    assert 1 <= len(result.details["suggestions"]) <= 4
    assert result.next_hints


def test_list_and_empty_topic_return_the_catalogue(kb: Knowledge) -> None:
    for topic in ("list", "", "  LIST "):
        result = explain_topic(kb, topic)
        assert result.ok
        assert {s["name"] for s in result.details["skills"]} == set(SKILL_NAMES)
        assert "RTL" in result.details["glossary_terms"]
        assert "chip-design-loop" in result.summary


def test_unknown_topic_is_ok_with_suggestions(kb: Knowledge) -> None:
    result = explain_topic(kb, "zzzzqqq")
    assert result.ok is True
    assert result.details["matched"] is False
    assert "explain('list')" in result.summary
    # nothing matched, so the skills and the glossary are offered
    assert {s["topic"] for s in result.details["suggestions"]} >= {*SKILL_NAMES, "glossary"}


def test_skill_overview_carries_the_rules_and_the_whole_skill(kb: Knowledge) -> None:
    result = explain_topic(kb, "chip-design-loop")
    assert "Never edit a test to make a wrong design pass" in result.summary
    assert "open_questions" in result.summary
    text = result.details["text"]
    for section in kb.skills[1].sections:
        assert f"## {section.heading}" in text
    assert "## What done looks like" in text
    assert result.next_hints


def test_exact_section_hit_still_suggests_related_topics(kb: Knowledge) -> None:
    result = explain_topic(kb, "latches")
    assert result.details["title"] == "amaranth-style/Latches"
    assert any(s["topic"] == "Latch" for s in result.details["suggestions"])
    assert explain_topic(kb, "latches in verilog").details["matched"] is True


def test_no_line_is_cut_in_a_summary(kb: Knowledge) -> None:
    for doc in kb.docs:
        for line in [doc.intro, *(s.text for s in doc.sections)]:
            for part in line.splitlines():
                assert len(part) <= MAX_LINE_CHARS, f"{doc.name}: {part[:60]}"


async def test_skills_only_name_tools_that_exist(tmp_path: Path, kb: Knowledge) -> None:
    async with Client(build_server(tmp_path, None)) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
    steps = next(s for s in kb.skills[1].sections if s.heading == "The steps")
    assert kb.skills[1].name == "chip-design-loop"
    step_tools = re.findall(r"^\d+\. `([a-z_]+)`", steps.text, re.M)
    assert len(step_tools) >= 6
    assert set(step_tools) <= tools, set(step_tools) - tools


def test_summaries_always_fit_and_details_keep_the_full_text(kb: Knowledge) -> None:
    for doc in kb.docs:
        for topic in [doc.name, *(f"{doc.name}/{s.heading}" for s in doc.sections)]:
            result = explain_topic(kb, topic)
            assert summary_within_limits(result.summary), topic
            assert result.details["matched"] is True, topic
    long = explain_topic(kb, "cocotb2-tests/Example: the tests")
    assert "more lines in details" in long.summary
    assert "def test_wraps_to_zero" in long.details["text"]


def test_every_heading_resolves_to_itself(kb: Knowledge) -> None:
    for doc in kb.docs:
        for section in doc.sections:
            topic = section.heading if doc.is_glossary else f"{doc.name}/{section.heading}"
            best = knowledge.find(kb, topic)[0]
            assert best is not None and best.section is not None
            assert best.section.heading == section.heading, topic


async def test_explain_tool_over_mcp(tmp_path: Path) -> None:
    async with Client(build_server(tmp_path, None)) as client:
        result = await client.call_tool("explain", {"topic": "slack"})
        assert not result.is_error
        assert result.structured_content is not None
        assert result.structured_content["details"]["title"] == "Slack"
        default = await client.call_tool("explain", {})
        assert default.structured_content is not None
        assert "chip-design-loop" in default.structured_content["summary"]


async def test_instructions_point_at_the_loop_skill(tmp_path: Path) -> None:
    server = build_server(tmp_path, None)
    assert "explain('chip-design-loop')" in (server.instructions or "")
