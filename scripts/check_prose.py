#!/usr/bin/env python3
"""Fail when a page of the model talks about its governance, the method or itself.

A page under ``architecture/`` describes its subject: the clients, processes,
actors, rules and capabilities of the thing being modelled. Who approves what
and when lives in AGENTS.md; how the method works lives in the plugin and in
the contributing guide; how far a page has been validated lives in its status
line and nowhere else. This check reads every model page and fails on the
vocabulary that gives away a sentence about the writing rather than about the
subject — the third validator, beside ``check_links.py`` and ``check_model.py``.

What is read: every ``*.md`` under ``architecture/``, except the narrative
folders (``scope``, ``decisions``, ``reviews``, ``engagements``, ``reference``),
the relationship catalogue, the model's front door and the contract pages
(``architecture/README.md``, ``federation.md``, ``imports.md``,
``domains/README.md``), where governance and method belong, and a layer front
page whose status says it is not started, which says what opens it. Skipped
inside a page: fenced code, headings, the navigation line, the viewpoint and
status lines, HTML comments, tables, and the ``## Metamodel`` section of a
layer front page, which is the one place a layer says how its diagrams read.

The vocabulary lives in ``scripts/prose-denylist.json`` as regular expressions
grouped by what they betray — governance, method, the document itself. The
same file carries the labels this script skips (``_preamble_lines``,
``_exempt_sections``, ``_not_started``), so a project in another language
translates the JSON and leaves this file identical to the scaffold's. Add a
pattern when a new kind of noise slips through; remove one when it fires on
the subject's own words. A pattern that starts with ``(?-i:`` is matched
case-sensitively; every other one ignores case.

Usage: ``python3 scripts/check_prose.py`` (exit 1 on any hit) or
``python3 scripts/check_prose.py --report`` to list hits without failing.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DENYLIST = Path(__file__).resolve().parent / "prose-denylist.json"
SKIP_DIRS = {"scope", "decisions", "reviews", "engagements", "reference"}
SKIP_FILES = {"relationships.md", "relaciones.md", "federation.md", "imports.md"}
SKIP_PAGES = {"architecture/README.md", "architecture/domains/README.md"}  # governance and method live here
HEADING = re.compile(r"^##\s")
NAV_LINE = re.compile(r"^\s*_\[")


def load() -> tuple[list[tuple[str, re.Pattern[str]]], re.Pattern[str], re.Pattern[str], re.Pattern[str]]:
    data = json.loads(DENYLIST.read_text(encoding="utf-8"))
    patterns = []
    for group, items in data.items():
        if group.startswith("_"):
            continue
        for item in items:
            flags = 0 if item.startswith("(?-i:") else re.IGNORECASE
            patterns.append((group, re.compile(item.replace("(?-i:", "(?:"), flags)))
    preamble = data.get("_preamble_lines", ["**ArchiMate viewpoint:**", "**Status:**"])
    skip_line = re.compile(r"^\s*(?:#|<!--|-->|\||" + "|".join(re.escape(p) for p in preamble) + ")")
    exempt = data.get("_exempt_sections", ["Metamodel"])
    exempt_section = re.compile(r"^##\s+(?:" + "|".join(re.escape(e) for e in exempt) + r")\s*$")
    not_started = re.compile("^" + re.escape(data.get("_not_started", "**Status:** ○")), re.M)
    return patterns, skip_line, exempt_section, not_started


def pages(not_started: re.Pattern[str]) -> list[Path]:
    found = []
    for path in sorted((REPO_ROOT / "architecture").rglob("*.md")):
        rel = path.relative_to(REPO_ROOT)
        if SKIP_DIRS & set(rel.parts) or path.name in SKIP_FILES or rel.as_posix() in SKIP_PAGES:
            continue
        if not_started.search(path.read_text(encoding="utf-8")):
            continue
        found.append(path)
    return found


def prose_lines(text: str, skip_line: re.Pattern[str], exempt_section: re.Pattern[str]):
    fence, exempt, comment = False, False, False
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        if "<!--" in stripped:
            comment = "-->" not in stripped
            continue
        if comment:
            comment = "-->" not in stripped
            continue
        if HEADING.match(line):
            exempt = bool(exempt_section.match(line))
        if exempt or not stripped or skip_line.match(line) or NAV_LINE.match(line):
            continue
        yield number, line


def main(argv: list[str]) -> int:
    report = "--report" in argv
    patterns, skip_line, exempt_section, not_started = load()
    hits = []
    for path in pages(not_started):
        rel = path.relative_to(REPO_ROOT)
        for number, line in prose_lines(path.read_text(encoding="utf-8"), skip_line, exempt_section):
            visible = re.sub(r"\]\([^)]*\)", "]", line)  # a link target is not prose
            for group, pattern in patterns:
                match = pattern.search(visible)
                if match:
                    hits.append((rel, number, group, match.group(0), line.strip()))
                    break
    for rel, number, group, word, line in hits:
        excerpt = line if len(line) <= 140 else line[:137] + "..."
        print(f"{rel}:{number}: [{group}] «{word}» — {excerpt}")
    if hits:
        print(f"\n{len(hits)} sentence(s) speak about governance, the method or the document; a model page speaks about its subject.")
        return 0 if report else 1
    print("Every model page speaks about its subject.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
