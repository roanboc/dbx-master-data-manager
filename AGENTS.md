# AGENTS.md

**Master Data Manager** — a Databricks App, with a local mode on DuckDB, in
which data stewards and data owners turn source records into golden records
they can explain and reverse. This file is the standing instruction for any
coding agent, and any person, working here. The model of the project lives in
[architecture/](./architecture/README.md).

## The rule that governs everything else

**A change to what the model claims is aligned through the numbered EA
layers before it is coded, the Requester confirms what it claims in a preview
in the conversation, and the pull request merging lands what they
confirmed.** An element added, removed or re-related, a rule it states
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
| **Requester** | The product owner — an enterprise's data and analytics unit | Says what should change — a requirement or a problem, not a diff, in plain words — and confirms what the change claims when the agent previews it |
| **Agent** | The coding agent, or a person | Works the change through the layers, writes the scope document, builds directly from the request, and opens a pull request — stopping only for a contradiction, an ambiguity, or something needing authorisation |
| **Reviewer** | The product owner | Reviews and merges. The merge lands what the Requester confirmed; nothing ships without it |

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

## Delivery

**Delivery framework: _none yet_.**

The model owns why this project exists, who does what, and which information
it holds; it registers what realizes each business service. How the software
is designed and built belongs to a delivery framework once one is named here,
and the principles reach it through its standing file. With none named, the
scope documents are the specification, and the design already written in
`architecture/4_application/` and `architecture/5_technology/` stays there
while it is true; a page that goes stale collapses into the register rather
than being repaired.

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
when a document is edited. The other fourteen are out of the agent's listing:
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
  said exist, or `●` validated — confirmed by the Requester, on a named date. A draft
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
  `check_model.py`, and `scan_public_safe.py`, all run before every push;
  `scripts/hooks/pre-push` runs them, the scan and ruff's lint and format
  checks, once `make hooks` has pointed git at it. Everything else the method can do runs from the
  plugin rather than from a copy in here.
- `src/mdm/` — the hub: one Python package, run through the `mdm` command
  line. Its layers and invariants are in § Code below.
- `src/mdm/ui/` — the steward workbench, a Dash application over the
  services, with its assets; `app.py` serves it on the platform, and
  `mdm ui` locally.
- `models/` — the starter entity models, `person.yaml` and
  `organisation.yaml`, and `codelists/`, the code-list copies they validate
  against. All invented. An entity is added here, never in code.
- `tests/` — pytest. Every store test runs on DuckDB and on Postgres: a
  throwaway Postgres the run starts for itself (`tests/postgres_server.py`,
  which needs `initdb` and `pg_ctl`), or the one `MDM_TEST_POSTGRES` names.
  `tests/conftest.py` holds the fixtures, and `tests/helpers.py` the invented
  mini world and the landing and read-back helpers the service tests share.
  `tests/ui/` holds the browser checks with axe-core, run in their own CI job
  and on demand with `make test-gui`; the rest of `tests/` runs without a
  browser, the workbench's callbacks and pages included.
- `tools/spike_throughput.py` — the throughput spike; its results live in
  `architecture/5_technology/3_capacity-and-throughput.md`.
- `tools/workbench_live.py` and `tools/screenshots.py` — a seeded demo store
  served for the browser checks, and the README's screenshots in
  `docs/screenshots/`, from the invented demo world with the stub.
- `pyproject.toml`, `uv.lock`, `Makefile` — the package, its locked
  dependencies (uv), and the targets below.
- `.github/` — the checks workflow, with its browser job, and the
  pull-request template.
- [`LICENSE`](./LICENSE), [`NOTICE`](./NOTICE) — Apache-2.0, with the MIT
  notice for the files copied from the method's scaffold.

## Code

The package is layered, and a module imports only from layers to its left:
`models` → `config`, `capacity` → `backend` | `engine` → `services` → `ui` →
`cli`. The assistant and the simulator sit beside the services: of the
services, `agent` uses only `services.privacy`, and `demo` uses none. Tests in
`tests/test_services_capacity.py` check the import rule, that no SQL is
written outside `src/mdm/backend/`, and that the workbench imports services
only and reads no store.

| Layer | Module | Role |
| ----- | ------ | ---- |
| Domain model | `src/mdm/models/` | Frozen dataclasses, the errors (`errors.py`), `canonical_json`, and the safety helpers (`safety.py`). No SQL, no input or output |
| Settings and capacity | `src/mdm/config.py`, `src/mdm/capacity.py` | `Settings.from_env` reads `MDM_*` variables; the declared figures, and the paging helpers `pages`, `chunks` and `require_limit` |
| Store | `src/mdm/backend/` | Every SQL statement, once, in `store.py`, over two engines, `duckdb_engine.py` and `postgres_engine.py`; the DDL (`ddl.py`); the write guard (`guard.py`); Lakebase credentials (`lakebase_auth.py`); `factory.open_store(settings)` |
| Matching engine | `src/mdm/engine/` | Standardise, key, compare, score and explain, estimate, cluster, survive, check quality, draw quality samples and forced samples, pack a batch's chunks. Pure and deterministic |
| Services | `src/mdm/services/` | The landing reader, arrival, the commit path and the feed reader, lifecycle, the registry, authority and privacy; the inbox, decisions and the undo tray; blind review and the quality breaker; signature batches; the record reader; display helpers; `Hub.open` in `context.py` wires them |
| Workbench | `src/mdm/ui/` | The Dash shell, pages and components; `ids.py` for every component ID; `assets/` for the key listener and the styles. It calls services through `ui/context.py`, never the store |
| Assistant | `src/mdm/agent/` | Provider choice, masked prompts, the stub and the case narrative |
| Simulator | `src/mdm/demo/` | Invented source changes, landed as the integration platform would |
| Entry points | `src/mdm/cli.py`, `app.py` | The Typer app `mdm`, with `mdm ui`, `mdm tray flush`, `mdm breaker` and `mdm batch`; `app.py` for the platform |

### Invariants (do not violate)

1. **No SQL outside `src/mdm/backend/`.** Services, the engine and the
   command line call `SqlStore` methods.
2. **`backend` and `engine` never import each other.** The engine is pure
   Python with no input or output; the store knows nothing of matching.
3. **Only `src/mdm/services/commit.py` writes `mdm_core`, inside the
   commit-order lock** (`store.commit_scope()`). The store's guard refuses
   any other write, and the change feed is written in the same transaction
   (decision 8).
4. **The hub never writes the landing tables**, except the local simulator,
   `src/mdm/demo/`, inside `guard.simulating_integration_platform`.
5. **No personal value in a detail, reason, suggestion, evidence, message or
   log line** — attribute names, codes, IDs, source keys and counts only.
   Build every one through `src/mdm/models/safety.py`; the store checks each
   again where it writes a task, a reject, a staged decision, a label, a
   quality sample, a batch, a change set or an access row, and
   `tests/test_services_personal_data.py` is the backstop.
6. **Personas, the simulator, `mdm demo reset` and the breaker's demo trip
   and demo withdrawal (`hub.breaker.demo_trip` and
   `hub.breaker.demo_withdraw`, for the browser checks and tests) run only on
   a local store**: DuckDB, or a test Postgres on this machine
   marked `MDM_ALLOW_PERSONAS=1` (the store refuses to open that setting on
   any other server).
   `Settings.local_mode` is the one test, and a Lakebase endpoint or a
   Databricks App or runtime variable always makes the store shared
   (decision 13). `mdm ui` listens on a loopback address unless a Databricks
   App runs it, answers only its own host name, refuses posts from another
   site and cannot be framed (decision 21).
7. **The workbench calls services only**, through `src/mdm/ui/context.py`,
   with the actor of each request. It imports no module of `backend` or
   `engine`, touches no `.store`, writes no SQL and never writes `mdm_core`.
8. **A steward's decision goes through the undo tray** (`hub.tray.stage`),
   never straight to `lifecycle`. Only `tray.flush` commits it, audited even
   when nothing publishes; the commit's own transaction checks the record's
   event and the open task, and settles the staged decision (decision 19).
   A batch of alike reviews is one staged decision: it locks every review,
   and the flush commits one chunk a pass, each in its own transaction that
   checks and settles its own reviews. Every transaction on a batch takes
   the batch row first. A compensation goes through the tray too
   (decision 23).
9. **No personal value leaves a service except as the actor's role allows.**
   A revealed value is rendered once, and kept in no `dcc.Store`, address,
   component ID, browser storage or cache, tooltip, notification, error page
   or log line. A failure is logged by its type only, and the workbench's
   reveal reason is a code (decision 20).
10. **The quality breaker only reduces automation.** Only `mdm breaker
    restore`, by a data owner, lifts a demotion or restores a pattern's bulk
    rights. The commit refuses an automatic-band item while the band is
    demoted, and no code widens a band or changes a rule set to do it
    (decision 3).

### Established idioms (copy these; do not invent new ones)

- **A new setting** goes in `Settings` and `Settings.from_env`
  (`src/mdm/config.py`), with the prefix `MDM_`. No `.env` file is read.
- **An error** is an `MdmError` subclass from `src/mdm/models/errors.py`,
  raised with a code and safe fields; the command line prints one sentence
  built from them, never a value.
- **A read of a large table** is keyed or paged with `capacity.pages()`,
  never with an offset. `tests/test_services_capacity.py` fails an unbounded
  read.
- **A write of many rows** binds one JSON row document per chunk, the same
  statement on both engines (decision 6). Stored JSON is `canonical_json`.
- **A store contract change** updates `store.py`, both engine hooks where
  they differ, and a test that runs on both engines.
- **Demo data is invented**: names from `src/mdm/demo/names.py`, e-mail
  addresses at `example.org`, sources named `hr`, `student_records`, `crm`
  and `finance`. Never a real organisation or person.
- **A new screen** is a module in `src/mdm/ui/pages/` with `skeleton()`,
  `layout(ctx, …)` and `register(app)`. Its component IDs go in
  `src/mdm/ui/ids.py`. View functions take the dataclasses of
  `src/mdm/models/workbench.py` and return components, so they are tested
  without services.
- **A callback** is registered with `app.callback` or
  `app.clientside_callback`, never the global `dash.callback`. It gets
  `ctx = context.current(persona)`, calls a service with `actor=ctx.actor`,
  and turns an `MdmError` into a notice with `context.notice(error)`, or runs
  the call through `context.guarded`. An output shared with another callback
  uses `allow_duplicate=True` with `prevent_initial_call=True`. A list
  changes by `rowTransaction`, not by replacing `rowData`, unless the whole
  page changed.
- **Copy** speaks from the steward's side, in British English. A control
  says what it does ("Link to ORG-000123", "Not a match", "Undo"); an error
  says what went wrong and how to fix it (`src/mdm/ui/messages.py`).
  Something not on screen yet is said in one line, never offered as a dead
  button.

## Commands

```bash
python3 scripts/check_links.py    # relative links and HTML anchors resolve
python3 scripts/check_model.py    # element-ID references resolve
python3 scripts/check_prose.py    # every model page speaks about its subject
python3 scripts/scan_public_safe.py --root . --terms .public-safe-terms.txt   # nothing unsafe to publish
```

They need nothing but Python — no network, no plugin installed — so this
project can check itself. Without the terms file the scan applies its
built-in patterns only, as CI does.

The code runs through uv and the `Makefile`:

```bash
make install     # uv sync: the runtime and development dependencies, from uv.lock
make hooks       # point git at scripts/hooks, so the checks run before every push
make lint        # ruff check and ruff format --check, as CI runs them
make test        # pytest across the cores, on DuckDB and on Postgres (a throwaway server per worker, or MDM_TEST_POSTGRES)
make test-fast   # DuckDB only, without the slow, live and browser tests, while iterating
make demo        # a fresh local store: land invented changes and hard cases, arrive, commit, read the feed
make ui          # the steward workbench on http://127.0.0.1:8050, over the local store (uv run mdm ui)
make test-gui    # the browser checks with axe-core, on DuckDB; CI runs them in their own job
make screenshots # docs/screenshots/, from the invented demo world with the stub
make spike       # the throughput spike at 100,000 records on DuckDB
make check       # the four checks above, the lint and the tests; not the browser checks
uv run mdm --help
```

All of them — the four checks, the lint and the tests — must be green before
pushing. CI sets `MDM_REQUIRE_POSTGRES=1`, so a missing Postgres fails the
run rather than skipping it. The DuckDB file is single-writer: stop other
processes on `.mdm/mdm.duckdb`, or point `MDM_DUCKDB_PATH` elsewhere. `mdm ui`
holds the file while it runs, so stop it before other `mdm` commands on the
same file, or run it on a local Postgres. `make test-gui` runs with the
`gui` dependency group and needs a Chromium build for the pinned Playwright,
which CI's browser job installs, and `uv run --group gui playwright install
chromium` fetches once on a workstation; `make check` never runs it.

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
  business object a subroutine box, a business rule a trapezoid and a business
  process a hexagon, so no two types share a shape. An External party, such as
  the integration platform, is a grey dashed box with no ID. An element of
  this model that another team runs, such as the landing row or the change
  notifier, keeps its ID and is drawn grey dashed too.
- `scripts/prose-denylist.json` is the scaffold's list, untuned.
