# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""`explain`: look up a skill, a skill section or a glossary term."""

from __future__ import annotations

from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from siliconsmith.context import ServerContext
from siliconsmith.knowledge import Doc, Knowledge, Match, find, load
from siliconsmith.result import ToolResult

DESCRIPTION = """\
Explain a topic in plain English from the packaged skills and glossary. topic can be a skill \
name ('chip-design-loop', 'tt-rules', 'amaranth-style', 'myhdl-style', 'cocotb2-tests': you get \
the overview and the list of sections), a section heading, a glossary term ('latch', 'slack', \
'GDS'), or a few keywords. The best match comes back with up to 4 other suggestions. 'list' or an \
empty topic returns the catalogue. An unknown topic is not an error; it returns suggestions. The \
summary is the start of the text; details.text has all of it and details.source says where it \
came from. Start with explain('chip-design-loop').
"""


def _doc_lines(doc: Doc) -> list[str]:
    lines = [f"{doc.name}: {doc.description}"]
    if doc.intro:
        lines += ["", doc.intro]
    lines += ["", "Sections (explain('<name>/<section>') shows one):"]
    lines += [f"- {s.heading}" for s in doc.sections]
    return lines


def _full_doc(doc: Doc) -> str:
    return "\n".join(_doc_lines(doc))


def catalogue(knowledge: Knowledge) -> ToolResult:
    lines = ["Skills (explain('<name>') shows the overview and the sections):"]
    lines += [f"- {d.name}: {d.description}" for d in knowledge.skills]
    glossary = knowledge.glossary
    lines.append(f"Glossary: {len(glossary.sections)} terms. explain('glossary') lists them.")
    lines.append("Start with explain('chip-design-loop').")
    details: dict[str, Any] = {
        "matched": True,
        "skills": [{"name": d.name, "description": d.description} for d in knowledge.skills],
        "glossary_terms": [s.heading for s in glossary.sections],
    }
    return ToolResult.from_lines(True, lines, details=details)


def _suggestion(match: Match) -> dict[str, str]:
    return {"topic": match.label, "source": match.doc.source}


def explain_topic(knowledge: Knowledge, topic: str) -> ToolResult:
    topic = topic.strip()
    if topic.lower() in ("", "list"):
        return catalogue(knowledge)
    best, others = find(knowledge, topic)
    if best is None:
        names = [d.name for d in knowledge.skills]
        lines = [
            f"No match for {topic!r}.",
            f"Skills: {', '.join(names)}, glossary.",
            "Try explain('list'), a skill name, or a term such as 'latch' or 'reset'.",
        ]
        return ToolResult.from_lines(
            True,
            lines,
            details={"matched": False, "topic": topic, "suggestions": []},
            next_hints=["explain('list') shows everything that can be explained"],
        )
    if best.section is None:
        title, text, body = best.doc.name, _full_doc(best.doc), _doc_lines(best.doc)
    else:
        title = best.label
        text = f"{best.section.heading}\n\n{best.section.text}"
        body = [f"{best.doc.name}: {best.section.heading}", "", *best.section.text.splitlines()]
    lines = list(body)
    if others:
        lines.insert(1, "See also: " + "; ".join(f"explain('{m.label}')" for m in others))
    return ToolResult.from_lines(
        True,
        lines,
        details={
            "matched": True,
            "topic": topic,
            "title": title,
            "text": text,
            "source": best.doc.source,
            "suggestions": [_suggestion(m) for m in others],
        },
        next_hints=[f"explain('{m.label}')" for m in others],
    )


def register(server: MCPServer, ctx: ServerContext) -> None:
    @server.tool(
        name="explain",
        description=DESCRIPTION,
        annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
    )
    def explain(topic: str = "list") -> ToolResult:
        return explain_topic(load(), topic)
