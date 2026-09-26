# AGENTS.md

**This project has not been bootstrapped yet.** It is a fresh copy of the
[archreator](./README.md) template: the method works, the model is empty.
Run `/archreator:establish-project` before anything else — it names the
project, declares the modeling depth, fills in this file, and hands off to
discovery. Everything in this file below the rule is a placeholder it will
replace.

<!--
  TEMPLATE — establish-project replaces this comment block, the line above,
  and the placeholders below. Keep "The rule that governs everything else"
  and "Modeling depth"; they are the whole point of this template.
-->

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
| **Requester** | \<who owns the product> | Says what should change — a requirement or a problem, not a diff, in plain words |
| **Agent** | An AI agent (or a person) | Works the change through the layers, writes the scope document, builds directly from the request, and opens a pull request — stopping only for a contradiction, an ambiguity, or something needing authorization |
| **Reviewer** | \<who reviews and merges> | Reviews and merges. The merge is the approval; nothing ships without it |

## Modeling depth

**Declared depth: _not yet declared_** — `establish-project` sets this.

The six layers describe a weekend app and a twenty-business-line company
alike; the depth says how much of them gets filled in — the ladder is in
[`architecture/README.md`](./architecture/README.md) and is not restated
here. It is a starting posture, never a ceiling: deepening or descoping is a
normal initiative, decided by the Requester.

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

The catalogue lives with the skills, in the plugin, and is not restated here.

## Layout

<!-- Replace with the real source layout once the project has code, e.g.:
- `src/` — ...
- `tests/` — ...
-->

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
  its relationships and never declares them.
- [`scripts/`](./scripts/README.md) — the three validators, run before every
  push. Everything else the method can do runs from the plugin rather than
  from a copy in here.

## Commands

<!-- Replace with the project's real commands once they exist, e.g.:
```bash
npm run lint
npm run typecheck
npm test
```
All of them must be green before pushing; CI runs the same.
-->

```bash
python3 scripts/check_links.py    # relative links and HTML anchors resolve
python3 scripts/check_model.py    # element-ID references resolve
python3 scripts/check_prose.py    # every model page speaks about its subject
```

All three must be green before pushing. They need nothing but Python — no network,
no plugin installed — so this project can check itself.

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

<!-- Project-specific conventions go here as they're established —
     glossary location, code language, naming rules, single point of
     enforcement for business rules, etc. Keep this section short; link to
     the EA docs for anything that has a canonical home there instead of
     restating it. -->

- Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`, …).
- Documentation language: **English** (change during bootstrap; see
  `document-style`).
- Inside the page that defines an element, cite its bare identifier; from any
  other page, its type, identifier and name — the process [`BPROC#.#`]
  <name> — with the identifier first only in a definition (`document-style`).
- A model page speaks about its subject. Who approves what and when lives in
  this file; how the method works, in the plugin and `CONTRIBUTING.md`; how far
  a page is validated, in its status line. `scripts/check_prose.py` fails a
  page on the vocabulary that gives a sentence about governance, the method or
  the page itself away; its list, `scripts/prose-denylist.json`, follows the
  documentation language.
- A layer README has one shape: title, one sentence, the viewpoint line,
  `## Documents`, `## Metamodel`, `## Layer view` (`architecture-document-style`).
