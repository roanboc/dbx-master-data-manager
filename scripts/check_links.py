#!/usr/bin/env python3
"""Check that relative links in this repo resolve to real targets.

Two file kinds are validated:

**Markdown** (`*.md`) — relative `[text](target)` links must resolve to a
real file, and a fragment on a Markdown target (`page.md#a-heading`) must
match a heading in it. The anchor test is deliberately **permissive**: a
fragment passes if it matches the heading slug under either the portal's
rule or GitHub's, an explicit `{#id}`, or an `id=` on embedded HTML. A
validator that rejects a working link teaches people to ignore it, and the
two renderers of these documents do not agree on how a heading with a glyph
or a backtick becomes a slug.

Two categories are deliberately not flagged:

- Links inside fenced code blocks or inline code spans — skill files quote
  illustrative link syntax (e.g. `./<n>_*.md`) as examples, not real links.
- Everything under `plugins/archreator/assets/`, which holds templates whose
  relative links are written for the project they are emitted into rather than
  for where they sit. `check_skills.py` checks those files instead.
- Links inside a skill's `scaffold/architecture/` scaffold whose target is
  a numbered EA content file (`<n>_kebab-name.md`) that doesn't exist yet —
  layer READMEs deliberately forward-reference the numbered docs a
  downstream project will write, so an unfilled template is expected to have
  every one of these unresolved.

**HTML** (`*.html`) — relative `href`/`src` targets must resolve to a real
file, and any fragment (`#id`, or `page.html#id`) must resolve to an element
`id` in the target HTML file. This catches broken page-to-page links and
stale in-page anchors in the guidance site. A target a template engine fills
in at build time (`{{ page.edit_url }}`) names no file on disk, so it is not
checked — a template is not a page.

Absolute or non-file targets (http, https, mailto, tel, data, javascript)
are never checked in either kind.
"""
import re
import sys
import unicodedata
from pathlib import Path

def _find_repo_root(start: Path) -> Path:
    """The project this script is validating.

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
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
# A fenced code block: an opening run of three or more backticks at the start of
# a line, closed by a run at least as long, per CommonMark. Both halves of that
# rule carry weight. An unanchored, fixed-length pattern pairs the opening fence
# with the first `` ``` `` it meets — so a fence that *contains* a fence closes
# early, and the remainder of the block gets scanned as prose. That is the exact
# shape of a skill whose body is one `yaml` fence wrapping a document template.
FENCE_RE = re.compile(
    r"^(?P<ticks>`{3,})[^\n]*\n.*?^(?P=ticks)`*[ \t]*$",
    re.DOTALL | re.MULTILINE,
)
INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
NUMBERED_EA_DOC_RE = re.compile(r"^\d+_[\w.-]+\.md$")
# href/src attribute values; the lookbehind avoids matching data-src, xlink:href, etc.
HREF_RE = re.compile(r"""(?<![\w:-])(?:href|src)\s*=\s*["']([^"']+)["']""")
# element ids; the lookbehind avoids matching data-id, grid=, etc.
ID_RE = re.compile(r"""(?<![\w-])id\s*=\s*["']([^"']+)["']""")

EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "tel:", "data:", "javascript:")
# A target the template engine fills in when the page is built, rather than a
# path — `href="{{ page.edit_url }}"` in a theme template. There is nothing on
# disk for it to point at until something renders it.
TEMPLATE_RE = re.compile(r"\{[{%]")

_ids_cache: dict[Path, set[str]] = {}
_anchor_cache: dict[Path, set[str]] = {}
# An ATX heading. Trailing `#`s are decoration and are not part of the text.
HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*$", re.MULTILINE)
# An `attr_list` identifier written onto a heading: `## Title {#custom-id}`.
ATTR_ID_RE = re.compile(r"\s*\{#([^}\s]+)[^}]*\}\s*$")


def is_external(target: str) -> bool:
    return target.startswith(EXTERNAL_PREFIXES)


def strip_code(text: str) -> str:
    text = FENCE_RE.sub("", text)
    text = INLINE_CODE_RE.sub("", text)
    return text


def is_template_asset(path: Path) -> bool:
    """A file under `plugins/archreator/assets/`, checked where it lands instead.

    An asset is a template a skill emits into a project's `architecture/`. Its
    relative links are written for that destination - `../README.md` means the
    project's architecture front door - so resolving them here would be asking
    whether they work in the one place they were never meant to. They are
    checked by the project's own `check_links.py`, on the copy that matters.

    What could rot silently instead - an asset no skill emits, or a skill
    naming an asset that does not exist - is caught by `check_skills.py`, which
    is a stronger guarantee than link resolution was giving.
    """
    parts = path.parts
    return any(
        parts[i] == "archreator" and parts[i + 1] == "assets"
        for i in range(len(parts) - 1)
    )


def is_expected_forward_reference(resolved: Path) -> bool:
    """A numbered EA doc the scaffold points at but no project has written yet.

    The scaffold ships inside the skill that emits it, so the exemption is
    keyed on `scaffold/architecture/` rather than on any one project path.
    """
    parts = resolved.parts
    in_template_scaffold = any(
        parts[i] == "scaffold" and parts[i + 1] == "architecture"
        for i in range(len(parts) - 1)
    )
    if not in_template_scaffold:
        return False
    return bool(NUMBERED_EA_DOC_RE.match(resolved.name))


def _slug(text: str, keep_unicode: bool) -> str:
    """One heading, as a fragment.

    `keep_unicode=False` is Python-Markdown's `toc` slugify, which the portal
    uses: fold to ASCII, drop everything that is not a word character, space or
    hyphen, lowercase, and join on hyphens. `keep_unicode=True` is GitHub's,
    which keeps the accented and non-Latin characters instead of dropping them.
    Both are computed and either satisfies the check.
    """
    if not keep_unicode:
        text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip().lower()
    return re.sub(r"[\s]+", "-", text)


def anchors_in(md_file: Path) -> set[str]:
    """Every fragment a reader could reach in one Markdown document."""
    if md_file in _anchor_cache:
        return _anchor_cache[md_file]
    try:
        text = FENCE_RE.sub("", md_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        _anchor_cache[md_file] = set()
        return _anchor_cache[md_file]

    found: set[str] = set(ID_RE.findall(text))
    seen: dict[str, int] = {}
    for _, heading in HEADING_RE.findall(text):
        explicit = ATTR_ID_RE.search(heading)
        if explicit:
            found.add(explicit.group(1))
            heading = ATTR_ID_RE.sub("", heading)
        # The heading's rendered text: code spans keep their contents, links
        # keep their label, emphasis markers go.
        heading = re.sub(r"`([^`]*)`", r"\1", heading)
        heading = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading)
        heading = re.sub(r"[*_]{1,3}", "", heading)
        for slug in (_slug(heading, False), _slug(heading, True)):
            if not slug:
                continue
            found.add(slug)
            # A repeated heading is disambiguated by a counter, and the two
            # renderers spell it differently. Accept both.
            count = seen.get(slug, 0)
            seen[slug] = count + 1
            if count:
                found.add(f"{slug}_{count}")
                found.add(f"{slug}-{count}")
    _anchor_cache[md_file] = found
    return found


def ids_in(html_file: Path) -> set[str]:
    if html_file not in _ids_cache:
        try:
            _ids_cache[html_file] = set(ID_RE.findall(html_file.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError):
            _ids_cache[html_file] = set()
    return _ids_cache[html_file]


def check_markdown(md_file: Path) -> list[str]:
    errors = []
    text = strip_code(md_file.read_text(encoding="utf-8"))
    for match in LINK_RE.finditer(text):
        target = match.group(1).strip()
        if not target or is_external(target) or target.startswith("#"):
            continue
        path_part, _, fragment = target.partition("#")
        if not path_part:
            continue
        resolved = (md_file.parent / path_part).resolve()
        if is_expected_forward_reference(resolved):
            continue
        if not resolved.exists():
            errors.append(f"{md_file.relative_to(REPO_ROOT)}: broken link -> {target}")
            continue
        if fragment and resolved.suffix == ".md" and fragment not in anchors_in(resolved):
            errors.append(f"{md_file.relative_to(REPO_ROOT)}: missing anchor -> {target}")
    return errors


def check_html(html_file: Path) -> list[str]:
    errors = []
    rel = html_file.relative_to(REPO_ROOT)
    for target in HREF_RE.findall(html_file.read_text(encoding="utf-8")):
        target = target.strip()
        if not target or is_external(target) or TEMPLATE_RE.search(target):
            continue
        path_part, _, fragment = target.partition("#")
        if not path_part:
            # Same-page anchor, e.g. href="#main".
            if fragment and fragment not in ids_in(html_file):
                errors.append(f"{rel}: missing anchor -> #{fragment}")
            continue
        resolved = (html_file.parent / path_part).resolve()
        if not resolved.exists():
            errors.append(f"{rel}: broken link -> {target}")
            continue
        if fragment and resolved.suffix == ".html" and fragment not in ids_in(resolved):
            errors.append(f"{rel}: missing anchor -> {target}")
    return errors


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


def _excluded(path: Path) -> bool:
    return bool(EXCLUDED_DIRS & set(path.parts))


def main() -> int:
    # Findings carry em-dashes, notation glyphs and whatever a heading is
    # named in. A console that cannot encode them should show a replacement
    # character, not raise and take the whole run down with it.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover - older or wrapped streams
            pass
    all_errors = []
    for path in REPO_ROOT.rglob("*"):
        if _excluded(path) or is_template_asset(path) or not path.is_file():
            continue
        if path.suffix == ".md":
            all_errors.extend(check_markdown(path))
        elif path.suffix == ".html":
            all_errors.extend(check_html(path))
    if all_errors:
        print("Broken relative links found:")
        for error in all_errors:
            print(f"  {error}")
        return 1
    print("All relative Markdown and HTML links resolve.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
