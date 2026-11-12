# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""The packaged skills and glossary, and a small keyword search over them.

The skills live in `siliconsmith/skills/<name>/SKILL.md` and the glossary in
`siliconsmith/skills/glossary.md`, both read with `importlib.resources` so that they work from a
wheel. A skill is a YAML front matter block (`name`, `description`) followed by Markdown whose
`##` headings split it into sections. The glossary has one `##` section per term.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from importlib.resources.abc import Traversable

import yaml

GLOSSARY = "glossary"
STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "do",
        "does",
        "for",
        "from",
        "how",
        "i",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "the",
        "to",
        "what",
        "when",
        "where",
        "which",
        "why",
        "with",
        "you",
        "your",
        "my",
        "me",
        "can",
        "use",
        "using",
        "about",
        "tell",
        "explain",
    }
)


@dataclass(frozen=True)
class Section:
    heading: str
    text: str


@dataclass(frozen=True)
class Doc:
    """A skill or the glossary: a name, a one-line description, an overview and sections."""

    name: str
    description: str
    intro: str
    sections: tuple[Section, ...]
    source: str  # package path, for example "skills/tt-rules/SKILL.md"
    is_glossary: bool = False


@dataclass(frozen=True)
class Match:
    """One search hit. `section` is None when the hit is a whole document's overview."""

    doc: Doc
    section: Section | None
    score: int = 0

    @property
    def label(self) -> str:
        """The topic string that `explain` resolves back to this hit."""
        if self.section is None:
            return self.doc.name
        if self.doc.is_glossary:
            return self.section.heading
        return f"{self.doc.name}/{self.section.heading}"


@dataclass(frozen=True)
class Knowledge:
    docs: tuple[Doc, ...] = field(default_factory=tuple)

    @property
    def skills(self) -> tuple[Doc, ...]:
        return tuple(d for d in self.docs if not d.is_glossary)

    @property
    def glossary(self) -> Doc:
        return next(d for d in self.docs if d.is_glossary)


def norm(text: str) -> str:
    """Lowercase and reduce to single-space-separated letters and digits."""
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def split_front_matter(text: str) -> tuple[dict[str, object], str]:
    """Split `---` YAML front matter from the Markdown body. Raises ValueError if malformed."""
    match = re.match(r"---\n(.*?\n)---\n(.*)\Z", text, re.S)
    if not match:
        raise ValueError("missing YAML front matter (a block between two '---' lines)")
    data = yaml.safe_load(match.group(1))
    if not isinstance(data, dict):
        raise ValueError("front matter must be a YAML mapping")
    return data, match.group(2)


def split_sections(body: str) -> tuple[str, tuple[Section, ...]]:
    """Return the text before the first `##` heading (minus a `#` title), and the sections.

    Lines inside fenced code blocks are never treated as headings.
    """
    intro: list[str] = []
    heads: list[tuple[str, list[str]]] = []
    in_fence = False
    for line in body.splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
        if not in_fence and line.startswith("## "):
            heads.append((line[3:].strip(), []))
        elif heads:
            heads[-1][1].append(line)
        elif not (not in_fence and line.startswith("# ")):
            intro.append(line)
    sections = tuple(Section(h, "\n".join(lines).strip("\n")) for h, lines in heads)
    return "\n".join(intro).strip("\n"), sections


def parse_skill(text: str, source: str) -> Doc:
    meta, body = split_front_matter(text)
    name, description = meta.get("name"), meta.get("description")
    if not isinstance(name, str) or not isinstance(description, str):
        raise ValueError(f"{source}: front matter needs string 'name' and 'description'")
    intro, sections = split_sections(body)
    return Doc(name, description.strip(), intro, sections, source)


def parse_glossary(text: str, source: str) -> Doc:
    intro, sections = split_sections(text)
    description = "Plain-English meanings of chip design terms, with RF comparisons."
    return Doc(GLOSSARY, description, intro, sections, source, is_glossary=True)


def skills_root() -> Traversable:
    return resources.files("siliconsmith") / "skills"


@lru_cache(maxsize=1)
def load() -> Knowledge:
    """Read every skill and the glossary from the package data."""
    root = skills_root()
    docs: list[Doc] = []
    for entry in sorted(root.iterdir(), key=lambda e: e.name):
        skill = entry / "SKILL.md"
        if entry.is_dir() and skill.is_file():
            docs.append(
                parse_skill(skill.read_text(encoding="utf-8"), f"skills/{entry.name}/SKILL.md")
            )
    glossary = root / "glossary.md"
    docs.append(parse_glossary(glossary.read_text(encoding="utf-8"), "skills/glossary.md"))
    return Knowledge(tuple(docs))


def _keys(heading: str) -> set[str]:
    """Normalized names a heading answers to: with and without a trailing (...) aside."""
    return {norm(heading), norm(re.sub(r"\(.*?\)", "", heading))} - {""}


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith(("ches", "shes", "xes", "sses")):
        return word[:-2]
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def _tokens(text: str) -> list[str]:
    return [_stem(w) for w in norm(text).split() if w not in STOPWORDS and len(w) > 1]


def exact(knowledge: Knowledge, topic: str) -> list[Match]:
    """Documents and sections whose name or heading is the topic (case and punctuation aside)."""
    key = norm(topic)
    if not key:
        return []
    hits = [Match(d, None, 100) for d in knowledge.docs if key == norm(d.name)]
    for doc in knowledge.docs:
        for section in doc.sections:
            names = _keys(section.heading) | {
                f"{norm(doc.name)} {k}" for k in _keys(section.heading)
            }
            if key in names:
                hits.append(Match(doc, section, 100))
    return hits


def search(knowledge: Knowledge, topic: str) -> list[Match]:
    """Keyword scoring over headings and text. Best first; empty if no word matches."""
    words = set(_tokens(topic))
    if not words:
        return []
    scored: list[Match] = []
    for doc in knowledge.docs:
        name_words = set(_tokens(doc.name))
        doc_words = set(_tokens(doc.description))
        for section in doc.sections:
            head = set(_tokens(section.heading))
            body = Counter(_tokens(section.text))
            score = 0
            for word in words:
                if word in head:
                    score += 6
                if word in body:
                    score += 2 + min(body[word], 3)
                if word in name_words:
                    score += 4
                if word in doc_words:
                    score += 1
            if score:
                scored.append(Match(doc, section, score))
    return sorted(scored, key=lambda m: -m.score)


def find(knowledge: Knowledge, topic: str, limit: int = 4) -> tuple[Match | None, list[Match]]:
    """The best match for `topic` and up to `limit` other suggestions.

    A skill name, then a section heading or glossary term, are matched exactly first; keyword
    scoring decides otherwise and also supplies the other suggestions after an exact hit.
    """
    hits = [*exact(knowledge, topic), *search(knowledge, topic)]
    if not hits:
        return None, []
    best, rest = hits[0], hits[1:]
    seen = {best.label}
    others: list[Match] = []
    for hit in rest:
        if hit.label not in seen:
            seen.add(hit.label)
            others.append(hit)
    return best, others[:limit]


def full_text(doc: Doc) -> str:
    """Everything in a document: description, overview and every section, as Markdown."""
    parts = [f"{doc.name}: {doc.description}"]
    if doc.intro:
        parts.append(doc.intro)
    parts += [f"## {s.heading}\n\n{s.text}" for s in doc.sections]
    return "\n\n".join(parts)
