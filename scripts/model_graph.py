#!/usr/bin/env python3
"""Parse the layered model into a graph, once, for whoever needs it.

`check_model.py` built this graph in memory, checked it and threw it away.
That was the right shape while validation was the only consumer. A published
view of the model is a second consumer, and it needs the same parse — so the
parse moved here rather than being written twice, because two parsers of the
same convention drift and the drift is silent.

Nothing here validates and nothing here persists. `check_model.py` imports it
and applies its checks; the plugin's reading tools — `model.py` and
`build_brief.py` — import it through `--project` so there is one parse of the
convention, not one per consumer. This module only reads Markdown and returns
what it found.

**Two levels of detail.** `parse_project()` returns what validation needs —
definitions, references, retirements, domains, and the names a restatement is
checked against. `parse_project(detail=True)` additionally reads the remaining
table columns and builds the edges, which a validator has no use for.

**A relationship is read from where it was declared, never from a diagram.**
Two surfaces declare one: a catalogue column whose cell is a list of
identifiers, and a relationship table, recognised by every row holding a bare
identifier in its first cell and in its second (the compact form) or its third
(the full form). Mermaid is not parsed at all —
a diagram is a rendering of what the tables say, and a fact whose only home is
a rendering is the one `P1` forbids. Initiative 6 transcribed the corpus onto
the two surfaces and removed the reader.

**Structure is read from the identifier, never from a heading.** An element's
type comes from its ID prefix, its group from the registry beside this file,
its layer from the numbered folder, its parent from the dot. All four survive
translation, which matters because a model may be written in any language:
`architecture-document-style` fixes the ID grammar and the notation, and fixes
nothing about the words around them. A parser keyed on `Realized by` finds
nothing in a Spanish model. Column headers are therefore carried through as
opaque text and never interpreted.

Deliberately not done here:

- **No inference of relationship semantics.** A relationship's name — a column
  header, or a relationship table's third cell — is carried verbatim:
  `habilita`, `precede a`, `serves`. Mapping those onto ArchiMate's
  relationship vocabulary would be a guess, and a wrong guess in a projection
  is worse than an honest string. `model.py export` reports how many distinct
  ones a corpus uses, which is the honest alternative to a controlled list
  nobody could translate.
- **No parse of the narrative folders.** Same reasoning as `check_model.py`:
  a merged scope document is immutable and will outlive the elements it names.
- **No caching and no incremental parse.** A whole model is a few hundred
  files, and a projection that is regenerated from scratch cannot go stale.
"""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path


def _find_repo_root(start: Path) -> Path:
    """The project this script is reading.

    The scripts ship inside the scaffold, so the same file runs from
    `<project>/scripts/` in a generated project and from the method's own
    `scaffold/scripts/`. Walking up to the enclosing repository gets the
    right answer in both places.
    """
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return start.parent


REPO_ROOT = _find_repo_root(Path(__file__).resolve().parent)
# The layered model's directory name, in every project tree and in the
# scaffold the skills emit.
MODEL_DIR = "architecture"
# What a model consumes from models it does not own. Read by `check_model.py`
# to resolve a foreign reference without cloning anything.
IMPORTS_DOC = "imports.md"
FEDERATION_DOC = "federation.md"
# Narrative folders inside the model directory. They are *about* the model
# rather than part of it, and are deliberately not read.
#
# `reference` is the strongest case of the four: it holds source documents
# exactly as they were provided - transcripts, decks, specifications - and a
# transcript in which somebody says "CAP3" is a person talking, not a model
# defining an element. Parsing it would invent references nobody wrote.
NARRATIVE = {"scope", "decisions", "reviews", "engagements", "reference"}
# Directories that are tooling rather than repository content. `.git` is
# obvious; `.claude`, `.agents`, `.gemini`, `.codex` and `.copilot` hold
# agent-local material — installed and vendored third-party skills, worktrees,
# local settings — one per host the method runs on, and `.aip` is a checkout
# of the pinned AIP release the validators are run from. None is this
# repository's to validate, and none is a downstream project's once these
# scripts ship there.
# `.archreator` is where every generated working surface lands — briefs, the
# portal configuration and whatever it builds — and `.model` is the exported
# model.json; both are the model a second time, which would otherwise read as
# every element being defined twice, or as a built page with links written
# for a rendered site. `.docs` is where the pre-reset tooling staged the same
# things, kept so a stale local copy never fails a fresh checkout's checks.
EXCLUDED_DIRS = {".git", ".claude", ".agents", ".gemini", ".codex", ".copilot",
                 ".aip", ".docs", ".archreator", ".model"}
# See the note in check_links.py: anchored to line starts and matched on fence
# length, so a fence containing a fence does not close early and leak its body
# back into the scanned prose.
FENCE_RE = re.compile(
    r"^(?P<ticks>`{3,})[^\n]*\n.*?^(?P=ticks)`*[ \t]*$",
    re.DOTALL | re.MULTILINE,
)

# Element-ID prefixes, loaded from the file that ships beside this script.
# The human-readable source is the table in the architecture-document-style
# skill; check_skills.py keeps the two in step, so this is not a second place
# to maintain the list.
PREFIX_FILE = Path(__file__).resolve().parent / "element-prefixes.json"
with PREFIX_FILE.open(encoding="utf-8") as handle:
    _GROUPS = json.load(handle)["prefixes"]
# Code -> the element type it names, and code -> the group it belongs to.
# Both are language-independent, which is the whole reason to key on them.
PREFIX_TYPES = {code: name for group in _GROUPS.values() for code, name in group.items()}
PREFIX_GROUPS = {code: label for label, group in _GROUPS.items() for code in group}
# Longest first, so alternation matches `BSVC` before `B`-prefixed neighbours.
PREFIXES = sorted(PREFIX_TYPES, key=len, reverse=True)

# The element itself: a type prefix, its number, then one dotted number per
# level below the top (`CAP1`, `CAP1.2`, `CAP1.2.3`).
_LOCAL = r"(?:" + "|".join(PREFIXES) + r")\d+(?:\.\d+)*"
# A qualifier segment: a domain inside this model (`SALES.`) or the
# federation ID of another model (`ORG.`, `PRD_MTD.`). The underscore joins
# a tier code to its short name, so the dot keeps exactly two meanings —
# ownership before the type prefix, catalogue levels after it.
_QSEG = r"[A-Z][A-Z0-9_]*"
# A full ID prepends the ownership path, when there is one (`SALES.CAP1.2`,
# `ORG.STK1`, `PRD_MTD.SALES.BSVC3`).
_ID = r"(?:" + _QSEG + r"\.)*" + _LOCAL
# A federation ID is a short uppercase code a model declares once on its own
# front door — `**Federation ID:** `ORG`` in `architecture/README.md` — and
# a citing model maps in its `architecture/federation.md`. The mapping is
# what turns the qualifier into a foreign reference, which is the point: a
# model you may reference is a model you have declared you federate with.
# One grammar carries domains and federation alike; which one a qualifier is
# depends on what this model declared, never on how it is spelled.
#
# The retired `model::ID` notation is recognised only to be reported: a
# reference written in it would otherwise stop matching anything and rot
# silently, which is the failure mode this whole file exists to prevent.
LEGACY_FOREIGN_RE = re.compile(r"`([A-Za-z0-9][A-Za-z0-9._/-]*::[^`\s]+)`")

# A backticked ID anywhere in the prose or a table cell is a reference — of
# this model's own elements, or of a model it federates with.
REFERENCE_RE = re.compile(r"`(" + _ID + r")`")
# A table row whose first cell is a bare backticked ID defines that element.
# A *domain-qualified* first cell is a reference instead — that is what a
# domain charter's "Consumed services" table holds. A *leveled* first cell
# (`BPROC7.2`) is a definition like any other: the dot is this element's own
# place in the catalogue, not somebody else's ownership of it.
TABLE_DEF_RE = re.compile(r"^\|\s*`([A-Z][A-Z0-9]*\d+(?:\.\d+)*)`\s*\|", re.M)
# Splits an ID into its domain path and the rest, so the two meanings of the
# dot never get confused for each other.
ID_PARTS_RE = re.compile(r"^((?:" + _QSEG + r"\.)*)(" + _LOCAL + r")$")
# Goals and principles are written as bolded lead-ins rather than table rows.
BULLET_DEF_RE = re.compile(r"\*\*(" + _ID + r")\s+—", re.M)
RETIRED_HEADING_RE = re.compile(r"^##+\s+Retired\s*$", re.M)
TABLE_ROW_RE = re.compile(r"^\|.*\|\s*$", re.M)
ID_HEADER_RE = re.compile(r"^\|\s*(?:Qualified\s+)?ID\s*\|", re.I)
# Splits the prefix off a local ID, so `CAP1.2` yields `CAP`.
LOCAL_PREFIX_RE = re.compile(r"^([A-Z][A-Z0-9]*?)\d")
# A numbered layer folder — `1_strategy`, `1_estrategia`. The digit is the
# layer and survives translation; the slug does not.
LAYER_DIR_RE = re.compile(r"^(\d)_(.+)$")
# Markers that say something is not true yet - an element grounded in nothing on
# purpose, or a relationship that is planned rather than live. The convention is
# the method's, and it is written in whatever language the model is; these are
# the two the corpus uses today. An unrecognised marker degrades to "not
# pending", which is the safe direction to be wrong in.
#
# It lives here rather than in a consumer because there are several of them:
# the plugin's reading tools use it for grounding, and the parse uses it to
# decide whether an edge is live. Two copies of one convention drift silently.
PENDING_MARKERS = ("pending", "pendiente")
# The same marker, anchored to the start of a cell — which is how the grounding
# rule writes it: `**Pending — future initiative**` is the cell, not a remark
# inside one. A row-level read has to be stricter than a cell-level one,
# because a catalogue row is prose as well as data. Unanchored, "a **pending**
# relationship reads as a live one" marks the row that describes the problem,
# and "stops de**pending** on their availability" marks a stakeholder who is
# not pending at all. Both are real rows in these models.
PENDING_MARK_RE = re.compile(r"^\W*(?:" + "|".join(PENDING_MARKERS) + r")\b", re.I)

# The name in a bolded lead-in definition: `**G1 — Legible guidance.**`
BULLET_NAME_RE = re.compile(r"\*\*(" + _ID + r")\s+—\s+(.+?)\*\*", re.S)
# A table's separator row, which is what marks the line above it as headers.
TABLE_SEP_RE = re.compile(r"^\|[\s:|-]+\|\s*$")
# A catalogue cell that leads with the element's name in bold, before any gloss.
NAME_LEAD_RE = re.compile(r"^\*\*(.+?)\*\*")

# The name inside a relationship table's description cell:
# `✦ «Capability» Learn from an engagement` yields `Learn from an engagement`.
#
# A leading run of non-word characters is the glyph, and `\w` is Unicode-aware,
# so a name opening with an accented letter survives and a glyph does not. The
# stereotype is optional and is dropped with its guillemets. Neither the glyph
# nor the stereotype is checked: an archetype cannot drift away from the prefix
# in the cell beside it, and the word for it is language-dependent where the
# prefix is not. See `architecture-document-style` § The relationship table.
NODE_DESC_RE = re.compile(r"^[^\w«]*\s*(?:«[^»]*»\s*)?(.*?)\s*$", re.S)

# How far a document has been validated, declared in its preamble. The glyph
# carries the meaning and the words beside it are prose in whatever language
# the model is written in, which is the same arrangement the element notation
# uses: `architecture-document-style` fixes the glyph and fixes nothing about
# the sentence around it.
STATUS_GLYPHS = {
    "\u25cb": "not started",      # empty circle
    "\u25d0": "draft catalogue",  # half-filled
    "\u25cf": "validated",        # filled
}
# The preamble is everything before the first level-2 heading: the title, the
# nav line, and the metadata lines under it. A status glyph is looked for
# there and nowhere else, so a diagram further down cannot be mistaken for one.
PREAMBLE_END_RE = re.compile(r"^##\s", re.M)

def strip_code(text: str) -> str:
    return FENCE_RE.sub("", text)


def preamble(text: str) -> str:
    """Everything before the first level-2 heading."""
    match = PREAMBLE_END_RE.search(text)
    return text[: match.start()] if match else text


def status_of(text: str) -> tuple[str, int]:
    """(status name, how many status glyphs the preamble carried).

    A count of one is the good case. Zero means the document never declared
    how far it has been validated; more than one means it declared two things
    and a reader cannot tell which. `check_model.py` judges both.
    """
    found = [g for g in preamble(text) if g in STATUS_GLYPHS]
    if len(found) != 1:
        return "", len(found)
    return STATUS_GLYPHS[found[0]], 1


def split_retired(text: str) -> tuple[str, str]:
    """Return (live, retired) halves, split at a `## Retired` heading."""
    match = RETIRED_HEADING_RE.search(text)
    if not match:
        return text, ""
    return text[: match.start()], text[match.start() :]


def definitions_in(text: str) -> set[str]:
    return set(TABLE_DEF_RE.findall(text)) | set(BULLET_DEF_RE.findall(text))


# A federation index row's first cell: a bare backticked uppercase code.
_ALIAS_CELL_RE = re.compile(r"^`([A-Z][A-Z0-9_]*)`$")
# The first backticked model key in a federation index row's second cell.
_MODEL_KEY_RE = re.compile(r"`([A-Za-z0-9][A-Za-z0-9._/-]*)`")
# The federation ID a model declares on its own front door.
FEDERATION_ID_RE = re.compile(r"\*\*Federation ID:\*\*\s*`([A-Z][A-Z0-9_]*)`")


def federation_of(project: Path) -> dict[str, str]:
    """Federation ID -> the model key it maps to, from `federation.md`.

    Read by position: cell 1 the federation ID this model writes for the
    other model, cell 2 that model's key — its tree name, backticked. A row
    counts only when its first cell is a bare backticked uppercase code that
    could not be an element ID. A federation ID whose row carries no readable
    key maps to "", which means a model outside this repository: references
    under it resolve against `architecture/imports.md` instead.
    """
    doc = project / MODEL_DIR / FEDERATION_DOC
    if not doc.is_file():
        return {}
    found: dict[str, str] = {}
    for line in strip_code(doc.read_text(encoding="utf-8")).splitlines():
        if not line.startswith("|"):
            continue
        cells = _cells(line)
        if len(cells) < 2:
            continue
        alias = _ALIAS_CELL_RE.match(cells[0])
        if not alias or re.fullmatch(_LOCAL, alias.group(1)):
            continue
        key = _MODEL_KEY_RE.search(cells[1])
        found[alias.group(1)] = key.group(1) if key else ""
    return found


def federation_id_of(project: Path) -> str:
    """The federation ID this model's own front door declares, or ""."""
    doc = project / MODEL_DIR / "README.md"
    if not doc.is_file():
        return ""
    match = FEDERATION_ID_RE.search(doc.read_text(encoding="utf-8"))
    return match.group(1) if match else ""


def qualifier_of(element: str) -> str:
    """The domain path of an ID, or "" when it is unqualified.

    `SALES.BPROC1.3` is qualified and `BPROC1.3` is not, even though both
    contain a dot: the levels sit after the prefix, the domain before it.
    """
    match = ID_PARTS_RE.match(element)
    return match.group(1).rstrip(".") if match else ""


def local_of(element: str) -> str:
    """The ID with any domain path removed — `SALES.CAP1.2` yields `CAP1.2`."""
    match = ID_PARTS_RE.match(element)
    return match.group(2) if match else element


def prefix_of(element: str) -> str:
    """The type prefix of an ID — `SALES.CAP1.2` yields `CAP`."""
    match = LOCAL_PREFIX_RE.match(local_of(element))
    return match.group(1) if match else ""


def parent_of(element: str) -> str:
    """The ID one level up, or "" for an element that is already top-level.

    A trailing numeric segment is a level, so `SALES.CAP1.2` is a child of
    `SALES.CAP1`, while `SALES.CAP1` is a top-level capability whose leading
    segment names its domain rather than a parent element.
    """
    head, _, tail = element.rpartition(".")
    return head if tail.isdigit() else ""


def unvalidated_tables(text: str) -> int:
    """Count tables whose header row has no ID column."""
    count = 0
    in_table = False
    for line in text.splitlines():
        is_row = TABLE_ROW_RE.match(line) is not None
        if is_row and not in_table:
            in_table = True
            if not ID_HEADER_RE.match(line):
                count += 1
        elif not is_row:
            in_table = False
    return count


def _excluded(path: Path) -> bool:
    return bool(EXCLUDED_DIRS & set(path.parts))


def project_key(project: Path) -> str:
    """How a model is named in a federation index and in a foreign reference.

    Its path from the repository root — `product-archreator`,
    `product-archreator/site` — or `.` for a repository that holds one model at
    its root. The same string `model.py export` writes into `model.json`, so an
    identifier a reader sees and an identifier a document writes are the same
    identifier.
    """
    if project == REPO_ROOT:
        return "."
    return str(project.relative_to(REPO_ROOT)).replace("\\", "/")


def find_projects() -> list[Path]:
    """A project is the directory containing an `architecture/` folder."""
    projects = []
    for model_dir in sorted(REPO_ROOT.rglob(MODEL_DIR)):
        if _excluded(model_dir) or not model_dir.is_dir():
            continue
        projects.append(model_dir.parent)
    return projects


def domain_of(md_file: Path, project: Path) -> str:
    """Upper-cased domain path for a file inside `architecture/domains/<name>/`."""
    parts = md_file.relative_to(project).parts
    segments = [parts[i + 1] for i, part in enumerate(parts[:-1]) if part == "domains"]
    return ".".join(s.upper() for s in segments)


def layer_of(md_file: Path, model_root: Path) -> tuple[str, str]:
    """(number, folder) of the numbered layer a file sits in, or ("", "").

    The first numbered folder on the path wins, which is also the right answer
    inside a domain: `domains/sales/1_strategy/x.md` is the strategy layer.
    """
    for part in md_file.relative_to(model_root).parts[:-1]:
        match = LAYER_DIR_RE.match(part)
        if match:
            return match.group(1), part
    return "", ""


def model_files(project: Path) -> list[Path]:
    """Every Markdown file of the model proper, narrative folders excluded."""
    model_root = project / MODEL_DIR
    return [
        path
        for path in sorted(model_root.rglob("*.md"))
        if not _excluded(path)
        and "scaffold" not in path.parts
        and not (NARRATIVE & set(path.relative_to(model_root).parts))
    ]


def _cells(row: str) -> list[str]:
    """The cells of a Markdown table row, outer pipes dropped."""
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def _plain(cell: str) -> str:
    """A table cell reduced to its text: links, bold and code spans removed."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", cell)
    text = text.replace("**", "").replace("`", "")
    return text.strip()


def _name_of(cell: str) -> str:
    """The element's name from its cell.

    A catalogue cell is either the name alone or the name in bold followed by
    an em-dash gloss — `**Operaciones y transformación** — entregar con…`.
    The bold run is the name in both cases; the gloss stays in `attrs` with
    the rest of the row.
    """
    match = NAME_LEAD_RE.match(cell.strip())
    return _plain(match.group(1)) if match else _plain(cell)


# A table cell that is nothing but one backticked identifier. Anchored at both
# ends, because a cell that *mentions* an identifier in a sentence is prose and
# a cell that *is* one is an end of a relationship.
CELL_ID_RE = re.compile(r"^`(" + _ID + r")`$")
# The separators a list of identifiers is written with. Punctuation only: a
# conjunction is a word, and which word it is depends on the language.
_SEP = r"[\s,;/·+&—–-]*"
# A cell that is a list of identifiers and nothing else — `ASVC1`,
# `PROD1, PROD2`, `ACMP7`, `ACMP8`. **This is what tells a relationship column
# apart from an attribute column**, and the distinction is the difference
# between a graph and a pile of noise: `Realizes` holds identifiers, `Maturity`
# holds the word "Established", and both are columns of the same catalogue. A
# cell of prose that happens to name an identifier is somebody talking about an
# element, which the projection already models as a mention.
CELL_ID_LIST_RE = re.compile(r"^" + _SEP + r"(?:`" + _ID + r"`" + _SEP + r")+$")


def _table_blocks(text: str) -> list[tuple[int, int, list[str], list[list[str]]]]:
    """Every Markdown table in the text, as (first line, last line, headers, rows).

    One walker, because there are now three questions to ask of a table - is it
    a catalogue, is it a relationship table, does it have an ID column - and
    three loops finding tables three ways is how they start disagreeing about
    what a table is.
    """
    blocks = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not TABLE_SEP_RE.match(line) or index == 0:
            continue
        header_line = lines[index - 1]
        if not TABLE_ROW_RE.match(header_line):
            continue
        end = index + 1
        rows = []
        while end < len(lines) and TABLE_ROW_RE.match(lines[end]):
            rows.append(_cells(lines[end]))
            end += 1
        blocks.append((index - 1, end - 1, [_plain(c) for c in _cells(header_line)], rows))
    return blocks


def _is_relationship_table(headers: list[str], rows: list[list[str]]) -> bool:
    """Relationship tables hold bare identifiers at both ends of every row.

    Recognised by **position**, never by a header word: a model may be written
    in any language, and `architecture-document-style` fixes the column order
    exactly as it fixes the name into a catalogue's second cell. Both the full
    form (`Desde | Elemento desde | Hasta | Elemento hasta | Relación`) and the
    compact form (`Desde | Hasta | Relación`) are supported.

    The catalogue test comes first and settles the only ambiguity that matters.
    A catalogue row's first cell is also a bare identifier, and a catalogue with
    a `Realizes` column has a second identifier-bearing column - so without this
    guard a perfectly ordinary catalogue could be read as a relationship table
    and every element in it would stop being defined.
    """
    if not rows or ID_HEADER_RE.match("| " + (headers[0] if headers else "") + " |"):
        return False
    if all(
        len(row) >= 3 and CELL_ID_RE.match(row[0]) and CELL_ID_RE.match(row[1])
        for row in rows
    ):
        return True
    return all(
        len(row) >= 5 and CELL_ID_RE.match(row[0]) and CELL_ID_RE.match(row[2])
        for row in rows
    )


# How much of a paragraph is worth carrying, and how many per document. A panel
# is for orienting a reader, not for reproducing the document — the document is
# one click away and is the thing that must be read when it matters. The cap is
# per document rather than per element because an element discussed in six
# places is being discussed *about* six different things, and dropping the
# sixth would hide a whole document from the reader rather than trimming a
# repetition.
EXCERPT_CHARS = 600
EXCERPTS_PER_DOCUMENT = 6
# A Markdown heading, whatever its level. The heading a paragraph sits under is
# most of what tells a reader where they are.
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*$")


def prose_blocks(text: str) -> list[tuple[str, str]]:
    """(heading, paragraph) for every prose block, tables and fences dropped.

    A paragraph that names an element is what a reader wants when they select
    it: for a goal or a principle it *is* the definition, because the method
    writes those as a bolded lead-in followed by prose rather than as a
    catalogue row. No special case is needed for that — the definition is a
    paragraph like any other, and it arrives because it names the element.

    Table rows are excluded because the projection already carries them, cell
    by cell, under their own headers. Carrying them twice would put the same
    fact in the panel in two shapes.
    """
    blocks: list[tuple[str, str]] = []
    heading = ""
    current: list[str] = []
    # Everything before the first level-2 heading is the document's preamble —
    # its title, its nav line, its status. That is metadata about the document
    # rather than anything it says about an element, and a status line naming
    # eleven identifiers would otherwise become an excerpt on all eleven.
    started = False

    def flush() -> None:
        if current:
            joined = " ".join(line.strip() for line in current).strip()
            if joined:
                blocks.append((heading, joined))
            current.clear()

    for line in text.splitlines():
        match = HEADING_RE.match(line)
        if match:
            flush()
            heading = match.group(2)
            started = started or len(match.group(1)) >= 2
            continue
        if not started:
            continue
        if not line.strip() or line.lstrip().startswith("|"):
            flush()
            continue
        current.append(line)
    flush()
    return blocks


@dataclass
class Excerpt:
    """One paragraph of the model that speaks about one element."""

    element: str
    doc: str
    heading: str
    text: str


@dataclass
class Restatement:
    """One end of a relationship table row, as that row wrote it down.

    The identifier is authoritative; `written` is the reader's copy of a name
    the catalogue owns, and `check_model.py` compares the two. See
    `architecture-document-style` § The relationship table.
    """

    element: str
    written: str


def relationship_rows(
    rows: list[list[str]],
) -> list[tuple[str, str, str, bool, Restatement, Restatement]]:
    """(source, target, relationship, pending, source restatement, target restatement).

    Two forms, told apart by shape. The **compact** form holds the two
    identifiers in cells 1 and 2 and the relationship in cell 3; the **full**
    form holds the identifiers in cells 1 and 3, a `<glyph> «Archetype» <name>`
    description in cells 2 and 4, and the relationship in cell 5. Whatever
    follows the relationship cell is notes, and that is where a pending marker
    is read from. A compact row restates no name, so there is nothing for
    `check_model.py` to hold against the catalogue; the identifier alone is
    the reference.
    """
    found = []
    for row in rows:
        src = CELL_ID_RE.match(row[0]).group(1)
        # The form is read from the row's shape, never from its width: a
        # compact row carries its second identifier in cell 2, a full row
        # carries a description there and the identifier in cell 3. Either
        # form may trail any number of note cells.
        compact = CELL_ID_RE.match(row[1]) is not None
        dst = CELL_ID_RE.match(row[1 if compact else 2]).group(1)
        label_at = 2 if compact else 4
        label = row[label_at] if label_at < len(row) else ""
        # The pending marker lives in the notes, after the relationship cell
        # (`architecture-document-style` § The relationship table). The label
        # itself is never scanned, so a relationship *named* with the word
        # keeps its meaning and only a note makes the edge pending.
        rest = " ".join(row[label_at + 1:]).lower()
        found.append(
            (
                src,
                dst,
                _plain(label) or "relates to",
                any(marker in rest for marker in PENDING_MARKERS),
                Restatement(src, "" if compact else node_name(row[1])),
                Restatement(dst, "" if compact else node_name(row[3])),
            )
        )
    return found


def node_name(cell: str) -> str:
    """The element name out of a description cell, glyph and stereotype dropped."""
    match = NODE_DESC_RE.match(_plain(cell))
    return match.group(1).strip() if match else _plain(cell)


def split_relationship_tables(text: str) -> tuple[str, list[list[list[str]]]]:
    """Return (text with relationship tables blanked out, those tables' rows).

    **This runs before anything else reads the document**, and it has to. A
    relationship table's first cell is a bare backticked identifier, which is
    exactly the shape `TABLE_DEF_RE` treats as a definition - so an unsplit
    relationship table would register every source element as defined a second
    time and fail a valid document on duplicates.

    Blanked rather than deleted, so every line number and every other
    line-oriented reading of the document is left where it was.
    """
    lines = text.splitlines(keepends=True)
    tables: list[list[list[str]]] = []
    for start, end, headers, rows in _table_blocks(text):
        if not _is_relationship_table(headers, rows):
            continue
        tables.append(rows)
        for index in range(start, end + 1):
            lines[index] = "\n" if lines[index].endswith("\n") else ""
    return "".join(lines), tables


def table_definitions(
    text: str,
) -> dict[str, tuple[str, dict[str, str], dict[str, list[str]]]]:
    """Map each ID defined in a catalogue table to its name, columns and references.

    **Only tables whose first header is `ID` are read.** An identifier
    legitimately appears in the first column of other tables — a capability
    area is listed again against the assessments it answers, for instance —
    and those rows say something about the element rather than defining it.
    Taking a name from one produces a confident, wrong answer, which is worse
    than none. `ID_HEADER_RE` is the same test `unvalidated_tables()` uses to
    decide what counts as a catalogue, so there is one rule, not two.

    The name is the second cell, which the notation fixes. Every column from
    the second on is carried under its own header as opaque text — headers are
    prose in whatever language the model is written in, so interpreting them
    here would make this parser monolingual.

    **The third return is what turns a catalogue into a graph.** Every
    backticked identifier in a column other than the ID and the name is a
    relationship this element declares, and the column header is what it is
    called. They are collected from the *raw* cell, because `_plain` strips the
    backticks that tell an identifier apart from a word that looks like one.
    """
    found: dict[str, tuple[str, dict[str, str], dict[str, list[str]]]] = {}
    for _, _, headers, rows in _table_blocks(text):
        if not ID_HEADER_RE.match("| " + (headers[0] if headers else "") + " |"):
            continue
        for cells in rows:
            match = TABLE_DEF_RE.match("| " + " | ".join(cells) + " |")
            if not match or len(cells) < 2:
                continue
            attrs, refs = {}, {}
            for index in range(1, min(len(cells), len(headers))):
                header = headers[index]
                if not header:
                    continue
                attrs[header] = _plain(cells[index])
                if index == 1:
                    # The name cell. An identifier inside it is the element
                    # talking about itself, not a relationship it declares.
                    continue
                cell = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", cells[index])
                if CELL_ID_LIST_RE.match(cell):
                    refs[header] = REFERENCE_RE.findall(cell)
            found[match.group(1)] = (_name_of(cells[1]), attrs, refs)
    return found


def bullet_definitions(text: str) -> dict[str, str]:
    """Map each ID defined as a bolded lead-in to its name."""
    return {
        match.group(1): _plain(match.group(2)).rstrip(".")
        for match in BULLET_NAME_RE.finditer(text)
    }


def imports_of(project: Path) -> dict[str, tuple[str, str]]:
    """Foreign element -> (the name this model writes for it, the revision read).

    Read by position from `architecture/imports.md`: cell 1 the foreign
    identifier, cell 2 its name, cell 3 the revision it was read at. A row
    counts only when its first cell is a bare backticked foreign identifier,
    which is the same test that tells a relationship table's ends apart from
    prose.

    **This is how a reference to another repository resolves, and it resolves
    against the declaration rather than against the truth.** Fetching the
    upstream on every pull request would be slow, would fail when somebody
    else's site was down, and would let their push break this build. What is
    checked here is that the dependency was written down; whether the row is
    still current is asked by a command somebody runs.
    """
    doc = project / MODEL_DIR / IMPORTS_DOC
    if not doc.is_file():
        return {}
    found: dict[str, tuple[str, str]] = {}
    for line in strip_code(doc.read_text(encoding="utf-8")).splitlines():
        if not line.startswith("|"):
            continue
        cells = _cells(line)
        if len(cells) < 3:
            continue
        match = CELL_ID_RE.match(cells[0])
        if not match or not qualifier_of(match.group(1)):
            continue
        found[match.group(1)] = (node_name(cells[1]), _plain(cells[2]))
    return found


@dataclass
class Element:
    """One element of the model, as the documents define it."""

    id: str  # qualified within its project: `SALES.CAP1.2`, or `CAP1.2`
    local: str  # `CAP1.2`
    prefix: str  # `CAP`
    type: str  # `Capability`
    group: str  # `Strategy` — the element's own layer, from its prefix
    domain: str  # `SALES`, or "" outside a domain
    parent: str  # `SALES.CAP1`, or "" when top-level
    name: str
    doc: str  # repository-relative path of the defining document
    layer: str  # the numbered folder it was defined in — `1_strategy`
    layer_no: str  # `1`
    status: str  # its document's declared status - `draft catalogue`, `validated`
    retired: bool
    attrs: dict[str, str] = field(default_factory=dict)


@dataclass
class Edge:
    """One relationship between two elements."""

    src: str
    dst: str
    rel: str  # the column header or the relationship cell, carried verbatim
    doc: str
    # How firmly this was stated. `catalogue` is a column of a catalogue row,
    # `table` a row of a relationship table, `identifier` the decomposition a
    # levelled ID already carries. A consumer that wants only what somebody
    # wrote down on purpose filters the third out; one that wants structure
    # keeps it.
    origin: str = "catalogue"
    # The model the far end belongs to. This edge's own model for all but a
    # reference that crosses a federation boundary, which is why it defaults
    # rather than being passed everywhere.
    dst_project: str = ""
    # The relationship is not true yet. Declared in words with the marker the
    # method already uses for an element grounded in nothing on purpose - never
    # inferred from how an arrow was drawn, because diagrams are renderings.
    pending: bool = False


@dataclass
class ParsedProject:
    """Everything one project's documents say, before anyone judges it."""

    project: Path
    defined: dict[str, Path]
    duplicates: list[str]
    retired: dict[str, Path]
    references: list[tuple[str, Path]]
    # (federation ID, model key, identifier, document) for every reference
    # that names an element in a model this one does not own. The model key
    # is "" when the federation index maps the ID to nothing readable — a
    # model outside this repository.
    foreign: list[tuple[str, str, str, Path]]
    domains: set[str]
    skipped: int
    # References still written in the retired `model::ID` notation, kept so
    # the validator can name them instead of letting them rot unmatched.
    legacy: list[tuple[str, Path]] = field(default_factory=list)
    # Repository-relative document path -> (status name, glyph count). Every
    # document the model parse reads, whether or not it defines anything.
    statuses: dict[str, tuple[str, int]] = field(default_factory=dict)
    elements: dict[str, Element] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    # (document, element) — where each element is spoken about. Kept apart
    # from `edges` because one end is a document rather than an element, and
    # mixing the two would make the element graph untraversable.
    mentions: list[tuple[str, str]] = field(default_factory=list)
    # Element ID -> the name its own catalogue row gives it. Collected whether
    # or not `detail` was asked for, because `check_model.py` needs it to judge
    # a restatement and validation does not run in detail.
    names: dict[str, str] = field(default_factory=dict)
    # What the documents say about each element, in their own words. Read only
    # in detail: a validator has no use for prose.
    excerpts: list[Excerpt] = field(default_factory=list)
    # Every place a document wrote an element's name down beside its
    # identifier. The identifier is authoritative; these are copies, and
    # `check_model.py` is what holds them in step.
    restatements: list[tuple[Restatement, Path]] = field(default_factory=list)


def parse_project(project: Path, *, detail: bool = False) -> ParsedProject:
    """Read one project's model.

    Without `detail` this returns exactly what validation needs. With it,
    element names, the remaining table columns and the Mermaid edges are read
    too — work a validator has no use for and a projection cannot do without.
    """
    defined: dict[str, Path] = {}
    duplicates: list[str] = []
    retired: dict[str, Path] = {}
    references: list[tuple[str, Path]] = []
    # The subset of `references` written in prose or in a catalogue cell —
    # what `mentions` is built from. A relationship-table cell is a reference
    # (it must resolve) and not a mention (it says nothing about the element).
    spoken_references: list[tuple[str, Path]] = []
    foreign: list[tuple[str, str, str, Path]] = []
    legacy: list[tuple[str, Path]] = []
    domains: set[str] = set()
    statuses: dict[str, tuple[str, int]] = {}
    skipped = 0
    elements: dict[str, Element] = {}
    edges: list[Edge] = []
    names: dict[str, str] = {}
    restatements: list[tuple[Restatement, Path]] = []
    excerpts: list[Excerpt] = []

    model_root = project / MODEL_DIR
    # The federation IDs this model maps, read once: they are what tells a
    # qualified reference's head apart from a domain of this model.
    aliases = federation_of(project)

    def alias_split(identifier: str) -> tuple[str, str, str]:
        """(federation ID, model key, rest) — or ("", "", identifier)."""
        head, _, rest = identifier.partition(".")
        if rest and head in aliases:
            return head, aliases[head], rest
        return "", "", identifier

    for md_file in model_files(project):
        stripped = strip_code(md_file.read_text(encoding="utf-8"))
        legacy.extend((found, md_file) for found in LEGACY_FOREIGN_RE.findall(stripped))
        # Relationship tables come out first, before any other reading of the
        # document. Their first cell is a bare backticked identifier, which is
        # what `TABLE_DEF_RE` treats as a definition — left in, every source
        # element would be reported as defined twice.
        text, rel_tables = split_relationship_tables(stripped)
        scope = domain_of(md_file, project)
        if scope:
            domains.add(scope)
        skipped += unvalidated_tables(text)
        statuses[str(md_file.relative_to(REPO_ROOT)).replace("\\", "/")] = status_of(text)
        live_text, retired_text = split_retired(text)

        for element in definitions_in(live_text):
            key = f"{scope}.{element}" if scope else element
            if key in defined:
                duplicates.append(
                    f"{md_file.relative_to(REPO_ROOT)}: duplicate definition of "
                    f"`{key}` (first defined in "
                    f"{defined[key].relative_to(REPO_ROOT)})"
                )
            else:
                defined[key] = md_file
        for element in definitions_in(retired_text):
            retired[f"{scope}.{element}" if scope else element] = md_file

        # A relationship table was blanked out of `text` above, so its
        # identifiers are added back here. They are references — the row points
        # at two elements and defines neither — but they are not *mentions*: a
        # catalogue row declares a relationship, it does not talk about an
        # element, and a model that keeps its relationships in one document
        # would otherwise list that document beside every element it has.
        prose_cited = list(REFERENCE_RE.findall(text))
        table_cited: list[str] = []
        for rows in rel_tables:
            table_cited.extend(REFERENCE_RE.findall(" ".join(" ".join(r) for r in rows)))

        defined_here = definitions_in(text)
        for reference, spoken in [(r, True) for r in prose_cited] + [(r, False) for r in table_cited]:
            alias, model, local = alias_split(reference)
            if alias:
                foreign.append((alias, model, local, md_file))
                continue
            if not qualifier_of(reference) and reference in defined_here:
                continue
            references.append((reference, md_file))
            if spoken:
                spoken_references.append((reference, md_file))

        doc = str(md_file.relative_to(REPO_ROOT)).replace("\\", "/")
        named = table_definitions(live_text)
        bullets = bullet_definitions(live_text)
        retired_here = definitions_in(retired_text)

        # Names and restatements are collected whether or not `detail` was
        # asked for: `check_model.py` holds a written name against the
        # catalogue that owns it, and validation never runs in detail.
        for element, (name, _, _) in named.items():
            names.setdefault(f"{scope}.{element}" if scope else element, name)
        for element, name in bullets.items():
            names.setdefault(f"{scope}.{element}" if scope else element, name)
        for rows in rel_tables:
            for _, _, _, _, src_said, dst_said in relationship_rows(rows):
                for said in (src_said, dst_said):
                    if alias_split(said.element)[0]:
                        # A foreign element's name is checked against the
                        # import row that declares it, not against a catalogue
                        # this model does not have.
                        continue
                    key = f"{scope}.{said.element}" if scope else said.element
                    restatements.append((Restatement(key, said.written), md_file))

        if not detail:
            continue

        layer_no, layer = layer_of(md_file, model_root)

        for element in sorted(definitions_in(live_text) | retired_here):
            key = f"{scope}.{element}" if scope else element
            if key in elements:
                continue
            name, attrs, _ = named.get(element, ("", {}, {}))
            if not name:
                name = bullets.get(element, "")
            prefix = prefix_of(element)
            elements[key] = Element(
                id=key,
                local=element,
                prefix=prefix,
                type=PREFIX_TYPES.get(prefix, ""),
                group=PREFIX_GROUPS.get(prefix, ""),
                domain=scope,
                parent=parent_of(key),
                name=name,
                doc=doc,
                layer=layer,
                layer_no=layer_no,
                status=statuses[doc][0],
                retired=element in retired_here,
                attrs=attrs,
            )

        def qualify(element: str) -> str:
            return f"{scope}.{element}" if scope else element

        # A catalogue column, which is where a relationship across the layers
        # is written: one row per element, and a column naming what it points
        # at. The header is the relationship, carried verbatim for the reason
        # the module docstring gives.
        for element, (_, attrs, refs) in named.items():
            src = qualify(element)
            # **A row that says Pending says it about its relationships too.**
            # An element marked `Pending — future initiative` does not exist
            # yet, so nothing it points at is true today either. The marker
            # cannot live in the relationship cell — a cell stops declaring the
            # moment it holds anything but identifiers, so a word written there
            # deletes the edge rather than qualifying it. It is read from the
            # row instead, in whichever column the grounding rule put it.
            row_pending = any(
                PENDING_MARK_RE.match(value) for value in attrs.values()
            )
            for header, cited in refs.items():
                pending = row_pending or any(
                    marker in attrs.get(header, "").lower()
                    for marker in PENDING_MARKERS
                )
                for target in cited:
                    alias, model, local = alias_split(target)
                    if alias:
                        edges.append(
                            Edge(src=src, dst=local, rel=header, doc=doc,
                                 origin="catalogue", pending=pending,
                                 dst_project=model or alias)
                        )
                        continue
                    dst = qualify(target) if not qualifier_of(target) else target
                    if dst == src:
                        continue
                    edges.append(
                        Edge(src=src, dst=dst, rel=header, doc=doc,
                             origin="catalogue", pending=pending)
                    )

        # A relationship table, which is where everything a row cannot carry
        # goes — above all a relationship between two peers in one layer, for
        # which a catalogue has one row each and no column at all.
        seen_here: dict[str, int] = {}
        for heading, block in prose_blocks(live_text):
            for reference in dict.fromkeys(REFERENCE_RE.findall(block)):
                if alias_split(reference)[0]:
                    continue
                key = f"{scope}.{reference}" if scope else reference
                if seen_here.get(key, 0) >= EXCERPTS_PER_DOCUMENT:
                    continue
                seen_here[key] = seen_here.get(key, 0) + 1
                body = block if len(block) <= EXCERPT_CHARS else (
                    block[:EXCERPT_CHARS].rsplit(" ", 1)[0] + " …")
                excerpts.append(Excerpt(element=key, doc=doc, heading=heading, text=body))

        for rows in rel_tables:
            for source, target, label, pending, _, _ in relationship_rows(rows):
                src_alias, src_model, src_local = alias_split(source)
                dst_alias, dst_model, dst_local = alias_split(target)
                src = src_local if src_alias else (
                    qualify(source) if not qualifier_of(source) else source)
                dst = dst_local if dst_alias else (
                    qualify(target) if not qualifier_of(target) else target)
                edges.append(
                    Edge(src=src, dst=dst, rel=label, doc=doc,
                         origin="table", pending=pending,
                         dst_project=(dst_model or dst_alias) if dst_alias else "")
                )


    mentions: list[tuple[str, str]] = []
    if detail:
        for key, element in elements.items():
            if element.parent:
                edges.append(
                    Edge(src=element.parent, dst=key, rel="decomposes",
                         doc=element.doc, origin="identifier")
                )
        seen: set[tuple[str, str]] = set()
        for reference, md_file in spoken_references:
            scope = domain_of(md_file, project)
            doc = str(md_file.relative_to(REPO_ROOT)).replace("\\", "/")
            qualified = f"{scope}.{reference}" if scope else reference
            target = qualified if qualified in elements else reference
            if (doc, target) in seen:
                continue
            seen.add((doc, target))
            mentions.append((doc, target))
        mentions.sort()

    return ParsedProject(
        project=project,
        defined=defined,
        duplicates=duplicates,
        retired=retired,
        references=references,
        foreign=foreign,
        legacy=legacy,
        domains=domains,
        statuses=statuses,
        skipped=skipped,
        elements=elements,
        edges=edges,
        mentions=mentions,
        names=names,
        restatements=restatements,
        excerpts=excerpts,
    )


# --------------------------------------------------------------------------
# The neighbourhood walk
#
# **The traversal has exactly one copy, and this is it.** `model.py trace` and
# `build_brief.py` both call it, which is why it lives beside the parse rather
# than in either of them: a walk written twice is the drift this module exists
# to prevent.
#
# It replaced a recursive CTE over a SQLite projection. The projection was a
# second representation of the model that had to be rebuilt to stay true, and
# in the one real model where that was checked it had not been - it answered
# from a revision that no longer named a course of action somebody had added.
# A cache that is silently wrong is worse than no cache, and parsing the
# Markdown fresh takes well under a second on the largest model there is.
# --------------------------------------------------------------------------


def qualified(project_key_: str, element: str, dst_project: str = "") -> str:
    """`product-archreator::CAP1` — an identifier that means one thing globally.

    Two models may each own a `CAP1`, so a walk that crosses a federation
    boundary has to carry the model name or start conflating them.
    """
    return f"{dst_project or project_key_}::{element}"


def neighbourhood(
    parsed: "ParsedProject",
    root: str,
    depth: int,
    *,
    extra: "list[ParsedProject] | None" = None,
) -> tuple[dict[str, int], list[tuple[str, str, "Edge"]]]:
    """Everything within `depth` hops of `root`, and the edges among it.

    `root` is qualified. Returns the reached identifiers with the fewest hops
    each was reached in, and every edge whose two ends were both reached.

    **The walk is undirected.** Direction here is a property of the sentence
    rather than of the relationship: a catalogue states a connection from
    whichever end owns the row, so `Provided by` and `Provides` are one
    relationship written from two sides. "What would this change touch" does
    not care which way somebody phrased it.

    **It is also model-blind.** An edge whose far end names another model is
    followed like any other, because a blast radius that stops at a repository
    boundary is a wrong answer rather than a smaller one.
    """
    links: list[tuple[str, str, Edge]] = []
    for source in [parsed, *(extra or [])]:
        key = project_key(source.project)
        for edge in source.edges:
            links.append((
                qualified(key, edge.src),
                qualified(key, edge.dst, edge.dst_project),
                edge,
            ))

    adjacency: dict[str, list[int]] = {}
    for index, (a, b, _) in enumerate(links):
        adjacency.setdefault(a, []).append(index)
        adjacency.setdefault(b, []).append(index)

    # Breadth-first, so the first arrival at an element is the nearest one.
    reached: dict[str, int] = {root: 0}
    frontier = [root]
    for hop in range(1, depth + 1):
        nxt: list[str] = []
        for node in frontier:
            for index in adjacency.get(node, ()):
                a, b, _ = links[index]
                far = b if a == node else a
                if far not in reached:
                    reached[far] = hop
                    nxt.append(far)
        if not nxt:
            break
        frontier = nxt

    edges = [(a, b, e) for a, b, e in links if a in reached and b in reached]
    edges.sort(key=lambda t: (min(reached[t[0]], reached[t[1]]), t[0], t[1]))
    return reached, edges
