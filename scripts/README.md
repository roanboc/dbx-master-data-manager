# Scripts

_[← Project home](../README.md)_

**Three validators, the parse behind `check_model.py`, and the public-safety scan.**
The validators came with the scaffold, so this project has had them since its
first commit. CI runs all three, and the scan, on every pull request.

```bash
python3 scripts/check_links.py    # relative links and HTML anchors resolve
python3 scripts/check_model.py    # element-ID references resolve
python3 scripts/check_prose.py    # every model page speaks about its subject, not about its governance or the method
python3 scripts/scan_public_safe.py --root . --terms .public-safe-terms.txt   # nothing unsafe to publish
```

All four exit `0` when everything passes and `1` otherwise, printing what failed.
They need nothing but Python — no network, no plugin installed, no packages —
which is the point: a project has to be able to check itself on its own.

`make hooks` points git at `scripts/hooks/`, so the checks CI runs, all but the
test suite, run before every push.

| File | What it is |
| ---- | ---------- |
| `check_links.py` | Executable. Every relative Markdown link and every HTML `href`, `src` and `#fragment` points at something that exists. It skips `.venv`, the one line this project adds to the scaffold's copy: the browser checks' driver installs HTML pages of its own there |
| `check_model.py` | Executable. Every backticked element ID resolves to a definition, none is defined twice, none is both live and retired, a levelled ID has its parent defined, every document that defines an element declares how far it has been validated, no relationship table in its full form restates an element's name differently from the catalogue that defines it (the compact form, `From \| To \| Relationship \| Notes`, restates no name), and every reference that names another model either resolves in this repository or is declared in `architecture/imports.md` |
| `check_prose.py` | Executable. No model page carries a sentence about its governance (who approves, at which session), about the method (ArchiMate, validators, identifiers, folders) or about itself ("this table", "this layer is derived"). It skips the narrative folders, the relationship catalogue, the front door, the contract pages, a layer not yet started, the status and viewpoint lines, headings, tables, fenced code, HTML comments and the `## Metamodel` section of a layer README. `--report` lists without failing |
| `prose-denylist.json` | Data, read by `check_prose.py`. Regular expressions grouped by what they betray — governance, method, the document itself — and the labels the script skips, so a project in another language translates this file and keeps the script identical to the scaffold's |
| `model_graph.py` | Library, imported by `check_model.py` and by the method's reading tools. The single parse of the document convention — element IDs, catalogue tables, relationship tables in either form (compact `From \| To \| Relationship \| Notes`, or full with each end's name), the resolution of a bare identifier inside a domain, and the neighbourhood walk the reading tools use |
| `element-prefixes.json` | Data, read by `model_graph.py`. The element-ID prefixes and what each stands for |
| `scan_public_safe.py` | Executable, from the Reference Data Manager. Not a model check: it scans every path and line for what must never reach this public repository (workspace hosts, storage URIs, e-mail addresses, UUIDs) and the private denylist, matched inside words, when `--terms` names it or `.public-safe-terms.txt` is present. `.public-safe-allow.txt` at the root lists literals it may ignore. CI runs it with the built-in patterns only |
| `hooks/pre-push` | Executable, POSIX shell. Runs the three validators, the scan (with the private denylist when `MDM_PUBLIC_SAFE_TERMS` or `.public-safe-terms.txt` names one, the built-in patterns otherwise) and ruff's lint rules and formatting check, from the lockfile as it stands (`uv run --frozen`), before every push, and refuses the push on any failure. The test suite stays with `make test` |

## Everything else runs from the method

Reading tools are not copied in here. They live in the archreator plugin and
read this project, which keeps one copy of each rather than a copy per project
that drifts from the method it came from:

```bash
model.py --project . trace BSVC1     # what a change here would touch
model.py --project . coverage        # what is not grounded, and what is not approved
model.py --project . portal          # a stock MkDocs config, for a reader outside the repo
build_brief.py --project . --element BSVC1 --focus impact
```

They import `model_graph.py` from **this** folder, so there is one parse of the
document convention and not two. Run one without a project and it says so.

## What they cannot do

`check_model.py` verifies that a *reference* resolves. `check_links.py`
verifies that a *link* resolves. **Neither reads what a "Realized by" cell
claims about a path**, so a cell naming a directory that no longer exists
passes both silently. `model.py coverage` finds the cell that is *empty*;
whether a path it names still exists is a step in the change process, not
something these scripts can do for you.

`check_prose.py` reads vocabulary, not intent. A sentence about governance in
the subject's own words passes, and a subject whose own words are on the list
fails until the list is tuned. Tune `prose-denylist.json`; never exempt the
page.

`coverage` prints and **always exits 0**. There is no `--strict`: telling a
repository path from a team name is fuzzy, and a check that fails wrongly
teaches people to ignore the checks that do not.

## Nothing is cached

There is no database and no projection anybody reads. Every tool parses the
Markdown fresh, which takes well under a second on the largest model built on
this method. There was a persisted graph once; in that same model it had gone
stale, and answered a question about the architecture from a revision that no
longer described it. A cache that is silently wrong is worse than no cache.

`model.py export` still writes `.model/model.json` for a consumer that
genuinely cannot read Markdown — a dashboard, a report — but nothing in the
method reads it back. Delete it and nothing is lost.

## The folders the validators skip

`architecture/reference/` holds source documents as they were provided. A
transcript in which somebody says an element identifier is a person talking,
not a definition.

`architecture/scope/`, `architecture/decisions/`, and any `reviews/` or
`engagements/` folder are skipped for the older reason: a merged scope
document is immutable and will outlive the elements it names, so
reference-checking it is incoherent rather than merely awkward.
