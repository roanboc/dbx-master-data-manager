# AGENTS.md

**Master Data Manager** — a Databricks App, with a local mode on DuckDB, in
which data stewards and data owners turn source records into golden records
they can explain and reverse. This file is the standing instruction for any
coding agent, and any person, working here. The model of the project lives in
[architecture/](./architecture/README.md).

## The rule that governs everything else

**A change to what the model claims is aligned through the numbered EA
layers before it is coded, and the Requester's approval is the pull request
merging.** An element added, removed or re-related, a rule it states
contradicted — align it through the layers (`architecture/1_strategy` → … →
`5_technology`), record it in a scope document (`architecture/scope/`), and
build it directly. The agent stops only when the change contradicts a
Principle or a decision already written down, reads two ways, or would
commit the Requester to something they have not agreed. A change inside an
element the model already names — a screen, a filter, a format, a defect —
is coded directly and documents nothing; one that only keeps a row true edits
the row in the same commit.

## Who decides

Every change moves through three roles. Nothing here assumes a human fills the
middle one — an AI agent and a person follow the same steps against the same
documents.

| Role | Who | Does |
| ---- | --- | ---- |
| **Requester** | The product owner — an enterprise's data and analytics unit | Says what should change — a requirement or a problem, not a diff, in plain words |
| **Agent** | The coding agent, or a person | Works the change through the layers, writes the scope document, builds directly from the request, and opens a pull request — stopping only for a contradiction, an ambiguity, or something needing authorisation |
| **Reviewer** | The product owner | Reviews and merges. The merge is the approval; nothing ships without it |

## Modeling depth

**Declared depth: 1 — Application.**

The subject is one application. The master data entities it manages, Person,
Organisation and those that follow, are content inside the application, not
elements of this model.

The six layers describe a weekend app and a twenty-business-line company
alike; the depth says how much of them gets filled in — the ladder is in
[`architecture/README.md`](./architecture/README.md) and is not restated
here. It is a starting posture, never a ceiling: deepening or descoping is a
normal initiative, decided by the Requester.

## Where this project lives

GitHub,
[roanboc/dbx-master-data-manager](https://github.com/roanboc/dbx-master-data-manager),
public. That answer activates `.github/workflows/checks.yml` and
`.github/pull_request_template.md`. Publishing the model anywhere else, such as
a portal or a PDF, stays a separate decision.

## Public safety

The repository is public, so everything written here is published. Follow
these rules in every file, commit message and pull request.

- Never name the institution or its city, the incumbent hub's product or
  vendor, any other master data management vendor or product, or any person.
  Never write an e-mail address, a workspace host or an internal system name.
- Use the neutral words instead: "the product owner", "an enterprise's data
  and analytics unit", "the incumbent hub", "the integration platform", "the
  landing tables written by the integration platform", "the operational
  database", "the change notifier", "the data platform team" and "the
  integration team".
- Cite public references by name: DAMA-DMBOK2 Revised by chapter, ISO 8000-115,
  ISO 8000-120 and ISO/IEC 25012 by number, the Open Data Contract Standard
  (ODCS), and the Gartner Magic Quadrant for Master Data Management Solutions
  (2026). Never name a vendor from the Magic Quadrant.
- Cite a chapter and paraphrase it. Never copy text from a standard or a book.
- Databricks product names (Databricks Apps, Lakebase, Zerobus, Lakehouse Sync,
  Unity Catalog) and DuckDB may appear, because the product owner fixed the
  platform. No principle names a stack or a product.
- Invent all demo data and examples. Never use a real organisation or person.
- The founding documents are held privately by the product owner.
  `architecture/reference/README.md` says what was derived from each.
- The pull-request description is public too, and follows the same rules.

`scripts/scan_public_safe.py` checks every path and line. Continuous
integration (CI) runs it with its built-in patterns only. Locally, run it with
the private denylist `.public-safe-terms.txt`, which the product owner
supplies; it is gitignored and never committed. `.public-safe-allow.txt` lists
the literals the scan may ignore. The denylist is matched inside words, so the
scan can flag an innocent word; reword it rather than allow-listing it.

## The skills

Three archreator skills surface on their own — `align-change-through-layers`
when a requirement arrives, `architecture-document-style` and `document-style`
when a document is edited. The other fifteen are out of the agent's listing:
a person invokes one by name, `/archreator:<skill>`, and typing `/archreator:`
lists them, while the agent reaches one by reading its file — never by
selecting it, because it cannot see one. Three kinds: `⚙` a procedure it runs,
`▤` a document it writes, `※` a rulebook it consults.

**Where no plugin loaded, the skills are not there.** The agent says so rather
than improvising one from memory or reconstructing it from a repository
nothing named. `.agents/skills/` is the path every host reads: fill it by
running the method's `install_skills.py --repo` from a checkout, then read
`.agents/skills/<skill>/SKILL.md`. It is gitignored — a local installation,
not a copy of the method kept in this project.

`.claude/settings.json` enables the archreator plugin for every Claude Code
session. It registers a plugin marketplace hosted under a personal GitHub
account, the one that hosts this repository, for everyone who clones and
trusts the repository. It is an adopted call the product owner can drop;
dropping it means documenting the install step instead.

The catalogue lives with the skills, in the plugin, and is not restated here.

## Layout

- `architecture/` — what this project knows about itself. Its `README.md` is
  the front door and says, per layer, whether this model owns it, another
  model does, it is out of scope, or it is a named gap. **A folder exists only
  once it holds something**; the skills emit the one they need when they need
  it, so an empty directory is never a substitute for saying what is missing.
- **Every document that defines an element says how far it has been
  validated**, with `○` not started, `◐` a draft catalogue of things somebody
  said exist, or `●` validated, on a named date. A draft
  catalogue is not an architecture draft and must never be read as one;
  `scripts/check_model.py` fails a defining document that declares nothing,
  one that carries no view or a section whose diagram follows its own first
  table, and one whose node labels carry a stereotype. **Each
  section opens with its own diagram and its own tables follow it** — never
  every diagram stacked at the top with the prose underneath.
- `architecture/relationships.md` — the relationship catalogue: every
  relationship a catalogue column does not carry, as rows of
  `From | To | Relationship | Notes` grouped by the document that defines the
  source element. Agents and validators read it; a human page draws and names
  its relationships and never declares them. Every relationship of this model
  is declared there; no catalogue carries a relationship column, so the human
  tables hold no bare IDs.
- [`architecture/scope/`](./architecture/scope/README.md) — one scope document
  per initiative, numbered in the order the initiatives open.
- `architecture/decisions/` — the calls smaller than an initiative, one record
  each.
- `architecture/reference/` — what the model was built from, and what was
  derived from each source.
- [`scripts/`](./scripts/README.md) — the three validators, the parse behind
  `check_model.py`, and `scan_public_safe.py`, all run before every push.
  Everything else the method can do runs from the plugin rather than from a
  copy in here.
- `.github/` — the checks workflow and the pull-request template.
- [`LICENSE`](./LICENSE), [`NOTICE`](./NOTICE) — Apache-2.0, with the MIT
  notice for the files copied from the method's scaffold.

There is no code yet; the application arrives with initiative 2.

## Commands

```bash
python3 scripts/check_links.py    # relative links and HTML anchors resolve
python3 scripts/check_model.py    # element-ID references resolve
python3 scripts/check_prose.py    # every model page speaks about its subject
python3 scripts/scan_public_safe.py --root . --terms .public-safe-terms.txt   # nothing unsafe to publish
```

All four must be green before pushing. They need nothing but Python — no
network, no plugin installed — so this project can check itself. Without the
terms file the scan applies its built-in patterns only, as CI does.

Everything else the method can do runs from the plugin against this project,
so there is one copy of each tool rather than one per project:

```bash
model.py --project . trace BSVC1     # what a change here would touch
model.py --project . coverage        # what names no realizing artifact
model.py --project . names src/x.py  # which elements name this path — is a change here inside the model?
model.py --project . health          # how much is validated, and whether a merged pull request moved a status line
model.py --project . portal          # the model as a website, for a reader outside the repo
build_brief.py --project . --element BSVC1 --focus impact
```

Everything they generate lands under `.archreator/`, which is gitignored.
Delete it and nothing is lost.

## Conventions

- Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`, …).
- Documentation language: **English**, with British spelling (organisation,
  standardise). Relationship labels and column headers keep the method's
  spelling (`realizes`, `Realized by`).
- Inside the page that defines an element, cite its bare identifier; from any
  other page, its type, identifier and name — ``stakeholder [`STK6`] Data
  platform team`` — with the identifier first only in a definition
  (`document-style`).
- A model page speaks about its subject. Who approves what and when lives in
  this file; how the method works, in the plugin; how far
  a page is validated, in its status line. `scripts/check_prose.py` fails a
  page on the vocabulary that gives a sentence about governance, the method or
  the page itself away; its list, `scripts/prose-denylist.json`, follows the
  documentation language.
- A layer README has one shape: title, one sentence, the viewpoint line,
  `## Documents`, `## Metamodel`, `## Layer view` (`architecture-document-style`).
- All relationships live in `architecture/relationships.md` (see Layout).
- Decision records are `architecture/decisions/<n>_<slug>.md`, one flat
  sequence.
- Diagram shapes follow the method's defaults, except that an outcome is an
  inverted trapezoid, and in the business layer a service is a rounded box, a
  business object a subroutine box and a business rule a trapezoid, so no two
  types share a shape.
- `scripts/prose-denylist.json` is the scaffold's list, untuned.
