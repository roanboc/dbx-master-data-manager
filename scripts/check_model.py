#!/usr/bin/env python3
"""Check that element-ID references in this repo resolve to real elements.

`architecture-document-style` gives every element an ID (`G1`, `CAP3`, `SALES.BSVC3`) and
says an ID is assigned once and never reused, so that a stale reference
fails loudly rather than silently pointing at something else. Nothing
enforced that until this script: `check_links.py` verifies that a *link*
resolves, and an agent reading `relieves PAIN2` has no cheap way to notice
that `PAIN2` was deleted three initiatives ago.

**The parse lives in `model_graph.py`**, which this script imports. It moved
there when the published view of the model became a second consumer of the
same reading of the same convention; a second parser would have drifted from
this one silently. What stayed here is the judgement — the checks below
and the exit code. Nothing is persisted: validation needs a parse, not a
store, so this script still builds the graph, checks it and exits.

Nine things are checked, per project:

- **Dangling references** — every referenced ID resolves to a definition.
  A qualified reference (`SALES.BSVC3`) resolves inside that domain's
  folder under `domains/`; an unknown domain is an error.
- **Duplicate definitions** — no ID is defined twice.
- **Retired then live** — no ID appears in both a live table and a
  `## Retired` section. Retired IDs stay retired.
- **Orphan levels** — a leveled ID (`CAP1.2`, `BPROC7.2.1`) has the element
  one level up defined too. A hierarchical identifier that names a parent
  nobody wrote is the same defect as a dangling reference.
- **A restated name that has drifted** — a relationship table in its full
  form writes each end's archetype and name beside its identifier, so the
  person approving it can read it without holding every catalogue open. The
  name is a copy of a fact the defining catalogue owns, and this is what holds
  the two in step: rename an element and every table naming it fails until it
  is updated. It is `P1`'s escape clause used the way `element-prefixes.json`
  uses it — one unavoidable copy, with a check on it. The compact form
  (`From | To | Relationship`, notes after) restates nothing and is held
  against nothing: the identifier is the whole reference. The **archetype** is
  deliberately not checked: it cannot drift away from the prefix sitting in
  the cell beside it, and the word for it is language-dependent where the
  prefix is not.
- **A section whose diagram trails its tables, or a document with no view at
  all** — every element document carries a view and **each section opens with
  its own diagram**, per `architecture-document-style` § Document skeleton and
  `references/archimate-on-mermaid.md` § Diagrams come first, one per section.
  The check is the enforceable core of both: a defining document carries at
  least one ```mermaid fence, and inside any section that has both a fence and
  a table the fence comes first. Per section, not per document — a document
  that stacks every diagram at the top and then runs all its prose and tables
  underneath satisfies a document-wide test and defeats the rule, and a
  document whose first section is a summary table is not asked for a picture
  it has nothing to draw.
- **A stereotype on a content node** — a Mermaid node label carries
  «Guillemets» only in a notation diagram, per
  `references/archimate-on-mermaid.md` § 1. Node labels. A notation diagram
  declares itself with `%% legend` as a comment line inside its own fence:
  the marker is read, never the heading beside it, so the check holds in a
  model written in any language.
- **Undeclared status** — a document that defines an element says in its
  preamble how far it has been validated, with one of the three glyphs in
  `architecture-document-style` § Document status. A catalogue of elements
  somebody mentioned in a meeting and a layer a Requester approved look
  identical on the page, and an agent that cannot tell them apart will build
  on the wrong one. This is checked on the glyph, never on the word beside
  it, so it holds in a model written in   any language.
- **Unreplaced layer templates** — once a layer defines an element, its README
  is no longer a scaffold. A `TEMPLATE` marker means its notation or layer
  view still describes a hypothetical subject instead of the model's subject.

An ID can carry two dot-separated qualifiers and they mean different things,
so the parser reads outwards from the type prefix: upper-case segments
*before* it are the domain path (`SALES.BSVC3`), numeric segments *after* it
are the catalogue's levels (`BSVC3.1`). Only the first makes a reference
qualified — `architecture-document-style` § Levels number hierarchically.

A **project** is the directory containing an `architecture/` folder, so IDs are
scoped per project and two projects may each own a `G1`.

**Only the numbered layers are checked.** They describe the current state.
`architecture/scope/`, `architecture/decisions/`, `architecture/reviews/` and `architecture/engagements/` live inside
`architecture/` too, but they are narrative *about* the model, and narrative
legitimately contains illustrations ("no component verifies that `PAIN2`
resolves"), hypotheticals, and references to elements that have since been
retired. They also cite elements in the same bolded
form a motivation document uses to define one, so a definition and a mention
are indistinguishable there.

The decisive argument is `RULE6`: a merged scope document is immutable. The
model moves on and the document does not, so it will eventually reference
something that no longer exists — and no edit is permitted to fix it.
Reference-checking a frozen document is incoherent, not merely awkward.

Deliberately not checked:

- `architecture/scope/`, `architecture/decisions/`, `architecture/reviews/` and `architecture/engagements/` inside
  `architecture/`, and anything outside `architecture/` entirely, per above.
- The scaffold under `scaffold/` — its layer READMEs and the skill files
  beside them carry illustrative IDs inside templates
  (`BSVC1`, `SALES.BSVC3`, `RULE7`); validating the documentation of the
  convention would fail on itself.
- A project whose `architecture/` defines no elements — an unfilled scaffold,
  whose layer READMEs are full of illustrative placeholders by design.
- Tables with no ID column — they predate the convention or hold prose.
  They are counted and reported as unvalidated coverage, not as errors.
- Fenced code blocks, for the same reason `check_links.py` skips them.
- Whether a "Realized by" cell points at a file that exists. That is the
  grounding rule, and it is still enforced only for links.
"""
import sys
from pathlib import Path

import re

from model_graph import (
    FEDERATION_DOC,
    IMPORTS_DOC,
    MODEL_DIR,
    REPO_ROOT,
    domain_of,
    federation_id_of,
    find_projects,
    imports_of,
    parent_of,
    parse_project,
    project_key,
    qualifier_of,
)


def _normalised(name: str) -> str:
    """A name reduced to what a comparison should care about.

    Whitespace runs and letter case are formatting; a different word is a
    rename. Comparing raw strings would fail a document over two spaces, and a
    check that fails wrongly teaches people to ignore the checks that do not.
    """
    return re.sub(r"\s+", " ", name).strip().casefold()


def _mermaid_fences(text: str) -> list[tuple[int, str]]:
    """(1-based opening line, body) for every ```mermaid fence in the document.

    Walked line by line rather than matched with one expression, so a fence
    nested inside a longer one closes where it really closes.
    """
    fences: list[tuple[int, str]] = []
    ticks = ""
    start = 0
    body: list[str] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        opener = re.match(r"^(`{3,}|~{3,})(.*)$", line)
        if ticks:
            if opener and opener.group(1)[0] == ticks[0] and len(opener.group(1)) >= len(ticks):
                if start:
                    fences.append((start, "\n".join(body)))
                ticks, start, body = "", 0, []
            else:
                body.append(line)
        elif opener:
            ticks = opener.group(1)
            start = line_no if opener.group(2).strip().lower() == "mermaid" else 0
            body = []
    if ticks and start:
        fences.append((start, "\n".join(body)))
    return fences


def _sections(text: str) -> list[tuple[str, int, int, int]]:
    """(heading, its line, first mermaid line, first table line) per section.

    A section runs from one heading to the next of any level; whatever sits
    above the first heading is the preamble and gets an empty heading. Fenced
    bodies are skipped, so an example table inside a code block is not the
    section's first table.
    """
    fence_lines = {}
    for open_line, body in _mermaid_fences(text):
        fence_lines[open_line] = True
    inside = ""
    heading, heading_line = "", 0
    fence_at, table_at = 0, 0
    out: list[tuple[str, int, int, int]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        opener = re.match(r"^(`{3,}|~{3,})(.*)$", line)
        if inside:
            if opener and opener.group(1)[0] == inside[0] and len(opener.group(1)) >= len(inside):
                inside = ""
            continue
        if opener:
            inside = opener.group(1)
            if line_no in fence_lines and not fence_at:
                fence_at = line_no
            continue
        head = re.match(r"^ {0,3}(#{1,6})[ \t]+(.*)$", line)
        if head:
            out.append((heading, heading_line, fence_at, table_at))
            heading, heading_line = head.group(2).strip(), line_no
            fence_at, table_at = 0, 0
            continue
        if line.lstrip().startswith("|") and not table_at:
            table_at = line_no
    out.append((heading, heading_line, fence_at, table_at))
    return out


def check_foreign(project: Path, parsed, known: dict) -> list[str]:
    """Every reference that names another model resolves, or is declared.

    Two cases, and they are genuinely different rather than one case with a
    fallback:

    - **The model is in this repository.** Its definitions are already parsed,
      so the reference resolves exactly, and an import row restating the
      element's name is held against the real one.
    - **The model is elsewhere.** Nothing here can see it. The reference must
      be declared in `architecture/imports.md`, which is what turns "somebody
      typed an identifier" into "this model states a dependency". Whether the
      declaration still matches the upstream is a question for a command
      somebody runs, never for a check that would make network calls on every
      pull request.
    """
    errors: list[str] = []
    declared = imports_of(project)
    declared_ids: dict[str, str] = {}
    seen: set[tuple[str, str, Path]] = set()
    for alias, model, element, md_file in parsed.foreign:
        if (alias, element, md_file) in seen:
            continue
        seen.add((alias, element, md_file))
        rel = md_file.relative_to(REPO_ROOT)
        reference = f"{alias}.{element}"
        here = known.get(model) if model else None
        if here is not None:
            # The target's own front door declares its federation ID; a
            # mapping that disagrees with it is two names for one model.
            if alias not in declared_ids:
                declared_ids[alias] = federation_id_of(here.project)
            owned = declared_ids[alias]
            if owned and owned != alias:
                errors.append(
                    f"{project.name}/{MODEL_DIR}/{FEDERATION_DOC}: `{alias}` maps to "
                    f"`{model}`, whose front door declares the federation ID "
                    f"`{owned}`. The model that declares an ID owns it"
                )
            if element not in here.defined and element not in here.retired:
                errors.append(
                    f"{rel}: `{reference}` names no element in `{model}`, which "
                    f"is in this repository and was checked directly"
                )
                continue
            if reference in declared:
                written = declared[reference][0]
                real = here.names.get(element, "")
                if written and real and _normalised(written) != _normalised(real):
                    errors.append(
                        f"{project.name}/{MODEL_DIR}/{IMPORTS_DOC}: `{reference}` is "
                        f'written here as "{written}" and defined as "{real}". The '
                        f"model that defines an element owns its name"
                    )
            continue
        if reference not in declared:
            errors.append(
                f"{rel}: `{reference}` names a model outside this repository and "
                f"is not declared in {MODEL_DIR}/{IMPORTS_DOC}. Nothing here can "
                f"see it, so a reference to it has to be a stated dependency"
            )
    return errors


def check_project(project: Path, known: dict | None = None) -> tuple[list[str], int, int]:
    """Return (errors, definition count, unvalidated-table count)."""
    parsed = parse_project(project)
    errors: list[str] = sorted(parsed.duplicates)

    for element, md_file in sorted(parsed.retired.items()):
        if element in parsed.defined:
            errors.append(
                f"{md_file.relative_to(REPO_ROOT)}: `{element}` is retired but "
                f"still defined live in {parsed.defined[element].relative_to(REPO_ROOT)}"
            )

    for element, md_file in sorted(parsed.defined.items()):
        parent = parent_of(element)
        if parent and parent not in parsed.defined and parent not in parsed.retired:
            errors.append(
                f"{md_file.relative_to(REPO_ROOT)}: `{element}` is one level "
                f"below `{parent}`, which is not defined in this project"
            )

    # Keyed the same way `parsed.statuses` is, so the two line up without
    # either side having to reconstruct the other's paths.
    defining = {
        str(path.relative_to(REPO_ROOT)).replace("\\", "/")
        for path in set(parsed.defined.values()) | set(parsed.retired.values())
    }
    for doc, (status, count) in sorted(parsed.statuses.items()):
        if doc not in defining:
            # A document defining nothing - an index, a layer README of a
            # layer nobody has filled - owes no status. It has nothing whose
            # standing a reader could mistake.
            continue
        if count == 0:
            errors.append(
                f"{doc}: defines elements and declares no status. Open the "
                f"preamble with one of ○ (not started), ◐ (draft catalogue) "
                f"or ● (validated)"
            )
        elif count > 1:
            errors.append(
                f"{doc}: the preamble carries {count} status glyphs, so a "
                f"reader cannot tell which one it means"
            )
        elif not status:  # pragma: no cover - unreachable while count == 1
            errors.append(f"{doc}: unrecognised status glyph")

    # A filled layer's README is part of the model's front door: its metamodel
    # and layer view must describe this subject, not retain the scaffold's
    # hypothetical labels. Unfilled layers remain templates until discovery
    # reaches them.
    for layer in sorted({(REPO_ROOT / doc).parent for doc in defining}):
        readme = layer / "README.md"
        if not readme.is_file():
            continue
        try:
            text = readme.read_text(encoding="utf-8")
        except OSError:
            continue
        if re.search(r"<!--\s*TEMPLATE\b", text, re.I):
            errors.append(
                f"{readme.relative_to(REPO_ROOT)}: defines a filled layer but "
                f"retains a `TEMPLATE` marker. Replace the scaffold notation "
                f"and layer view with this model's elements and relationships"
            )

    # Every element document opens with its views, and every section with its
    # own (`architecture-document-style` § Document skeleton;
    # `references/archimate-on-mermaid.md` § Diagrams come first, one per
    # section). Per section rather than per document: a document that stacks
    # every diagram at the top and runs its prose underneath passes a
    # document-wide test while defeating the rule it claims to satisfy. A layer
    # README that only indexes other documents defines nothing and is exempt.
    for doc in sorted(defining):
        try:
            text = (REPO_ROOT / doc).read_text(encoding="utf-8")
        except OSError:
            continue
        fence = text.find("```mermaid")
        if fence < 0:
            errors.append(
                f"{doc}: defines elements and carries no view. Give each section its own "
                f"diagram before that section's tables, in a ```mermaid fence"
            )
            continue
        for heading, line_no, fence_line, table_line in _sections(text):
            if fence_line and table_line and fence_line > table_line:
                where = f"under \"{heading}\"" if heading else "in the preamble"
                errors.append(
                    f"{doc}:{line_no}: the view {where} comes after that section's "
                    f"first table. A section opens with its own diagram and its "
                    f"tables follow it"
                )

    # A stereotype belongs on a legend node and nowhere else
    # (`references/archimate-on-mermaid.md` § 1. Node labels). A notation
    # diagram says so with `%% legend` inside its own fence, so the marker is
    # what is read and the heading beside it never has to be in English.
    for doc in sorted(defining):
        try:
            text = (REPO_ROOT / doc).read_text(encoding="utf-8")
        except OSError:
            continue
        for line_no, body in _mermaid_fences(text):
            if re.search(r"^\s*%%\s*legend\b", body, re.M):
                continue
            if "\u00ab" in body:
                errors.append(
                    f"{doc}:{line_no}: a node label carries a \u00abstereotype\u00bb. "
                    f"Glyph, shape and colour already say the type: write "
                    f"`<glyph> <name> [<ID>]`. A notation diagram that means to "
                    f"keep them declares itself with a `%% legend` line inside "
                    f"the fence"
                )

    # A legend, where a document still keeps one, shows the types and how they
    # connect (`references/archimate-on-mermaid.md` § A diagram explains
    # itself). Two or more types drawn with no edge,
    # above diagrams that draw one, is a key to the notation and not to the
    # layer. Read from the fences alone - the marker, the node openers and
    # the arrows - so it holds in any language.
    legend_re = re.compile(r"^\s*%%\s*legend\b", re.M)
    node_re = re.compile(r'^\s*[A-Za-z0-9_]+\s*[\[\(\{>/]+\s*"', re.M)
    edge_re = re.compile(r"-->|---|-\.->|==>")
    for doc in sorted(defining):
        try:
            text = (REPO_ROOT / doc).read_text(encoding="utf-8")
        except OSError:
            continue
        fences = _mermaid_fences(text)
        drawn = sum(len(edge_re.findall(body)) for _, body in fences if not legend_re.search(body))
        for line_no, body in fences:
            if not legend_re.search(body):
                continue
            types = len(node_re.findall(body))
            if types >= 2 and drawn and not edge_re.search(body):
                errors.append(
                    f"{doc}:{line_no}: the legend shows {types} types and no connection "
                    f"between them, while the document's diagrams draw {drawn}. A legend "
                    f"shows the types and how they typically connect - one edge per pair "
                    f"of types the diagrams below connect"
                )

    for said, md_file in parsed.restatements:
        canonical = parsed.names.get(said.element)
        if not canonical or not said.written:
            # Nothing to hold it against: either the element is defined as a
            # bolded lead-in with no catalogue row, or the cell was left blank.
            # A missing description is a legibility problem for a reader to
            # notice, not a false failure to manufacture here.
            continue
        if _normalised(said.written) != _normalised(canonical):
            errors.append(
                f"{md_file.relative_to(REPO_ROOT)}: `{said.element}` is written "
                f'here as "{said.written}" and defined as "{canonical}". The '
                f"catalogue that defines an element owns its name"
            )

    seen: set[tuple[str, Path]] = set()
    for reference, md_file in parsed.references:
        if (reference, md_file) in seen:
            continue
        seen.add((reference, md_file))
        scope = domain_of(md_file, project)
        qualifier = qualifier_of(reference)
        candidates = [reference]
        if not qualifier and scope:
            candidates.insert(0, f"{scope}.{reference}")
        if any(
            candidate in parsed.defined or candidate in parsed.retired
            for candidate in candidates
        ):
            continue
        rel = md_file.relative_to(REPO_ROOT)
        if qualifier and qualifier not in parsed.domains:
            errors.append(
                f"{rel}: `{reference}` is qualified by `{qualifier}`, which is "
                f"neither a domain of this model nor a federation ID mapped in "
                f"{MODEL_DIR}/{FEDERATION_DOC}"
            )
        else:
            errors.append(f"{rel}: `{reference}` is not defined in this project")

    for legacy_ref, md_file in parsed.legacy:
        errors.append(
            f"{md_file.relative_to(REPO_ROOT)}: `{legacy_ref}` uses the retired "
            f"`model::ID` notation. Reference it by federation ID — `ORG.STK1` — "
            f"declared on that model's front door and mapped in "
            f"{MODEL_DIR}/{FEDERATION_DOC}"
        )

    errors.extend(check_foreign(project, parsed, known or {}))

    return errors, len(parsed.defined), parsed.skipped


def main() -> int:
    # Findings carry em-dashes, notation glyphs and whatever a heading is
    # named in. A console that cannot encode them should show a replacement
    # character, not raise and take the whole run down with it.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover - older or wrapped streams
            pass
    all_errors: list[str] = []
    summary: list[str] = []
    # Every model in the repository, parsed before any is judged: a reference
    # that crosses from one to another can only be resolved by something that
    # has both.
    known = {project_key(project): parse_project(project) for project in find_projects()}
    for project in find_projects():
        errors, defined, skipped = check_project(project, known)
        rel = project.relative_to(REPO_ROOT) if project != REPO_ROOT else Path(".")
        if not defined:
            # An unfilled template scaffold: its layer READMEs are full of
            # illustrative placeholders by design, so there is nothing to check.
            summary.append(f"  {rel}/{MODEL_DIR}: no elements defined — scaffold, not checked")
            continue
        all_errors.extend(errors)
        note = f", {skipped} table(s) without an ID column not validated" if skipped else ""
        summary.append(f"  {rel}/{MODEL_DIR}: {defined} element(s){note}")

    for line in summary:
        print(line)
    if all_errors:
        print("Element-ID problems found:")
        for error in all_errors:
            print(f"  {error}")
        return 1
    print("All element-ID references resolve.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
