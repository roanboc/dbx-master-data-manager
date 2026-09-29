# Architecture — Master Data Manager

_The front door of this project's model._

**This folder is what this project knows about itself** — who it is for, what
it does, and which piece of software does each part. It is written in plain
Markdown so that the product owner, the data teams and the coding agent all
read the same thing, and so a change to it shows up in a pull request like any
other change.

Nothing here is generated. Nothing here is a copy of something else. If a
document says a thing, that is what the project claims is true today.

The subject is the **Master Data Manager** (the hub): a Databricks App, with a local
mode on DuckDB, that turns source records into golden records the stewards can
explain and reverse.

## What is modelled, and what is not

**One row per layer, and every row says something.** A layer with no file yet
is a stated fact — `Out of scope`, `External`, or a named `Gap` — not a
silence, so a reader can tell what was decided from what was never looked at.

| # | Layer | The question it answers | Status |
| - | ----- | ----------------------- | ------ |
| 0 | Business design | Who are the customers, and how does each offering pay? | `Out of scope` — this project models an application, not an organisation |
| 1 | Strategy | Why does this exist, and what must it be able to do? | `Local` — [1_strategy/](./1_strategy/README.md): motivation, capabilities and resources, and the value stream |
| 2 | Business | Who does what, and which services are offered? | `Local` — [2_business/](./2_business/README.md): actors and roles, business services, business processes, business objects, and the domain context and rules |
| 3 | Information | What information exists, and where does it live? | `Local` — [3_information/](./3_information/README.md): data domains and objects, the flows with the landing tables, the change feed and listening systems, and where data lives, how sensitive it is and how long it is kept |
| 4 | Application | Which software realizes each business service? | `Local` — [4_application/](./4_application/README.md): application services and components, how they collaborate, the solution design, and the landing and listener interfaces |
| 5 | Technology | What runs it all — runtimes, build, hosting? | `Local` — [5_technology/](./5_technology/README.md): technology services and nodes, deployment and checks, and capacity |
| — | Transition | Where is this going, and in what order? | `Local` — [6_transition/](./6_transition/README.md): plateaus, gaps and the order of initiatives 2 to 7 |

## Around the layers

- Each change to the model is recorded as an initiative in
  [scope/](./scope/README.md).
- Calls smaller than a change are recorded in
  [decisions/](./decisions/README.md).
- What the model was built from is described in
  [reference/](./reference/README.md). The founding documents themselves are
  held privately by the product owner.
- Every relationship between elements is declared in
  [relationships.md](./relationships.md).

## How deeply this project models itself

**Declared depth: 1 — Application.**

| Depth | The subject is | You get | Who confirms |
| ----- | -------------- | ------- | -------- |
| **1 — Application** | one app or tool | a light strategy layer — goals and principles, enough to judge a change against | You, in the conversation |
| **2 — Organization** | a company, department, or service line | the canvases, and the operating model derived from them | You, in the conversation or at a session |
| **3 — Enterprise** | several business lines | the above, plus each line modelled as a domain with its own charter | You, and each affected domain's owner for its own part |

Depth is about the subject, not the effort — a large application is still
Depth 1. It is a starting posture, never a ceiling: deepening is an ordinary
change, decided by whoever asked for this project.

## How far a document has been validated

Every document that defines anything says so in its own preamble, with one of
three marks:

| | Status | What you may do with it |
| - | ------ | ----------------------- |
| `○` | **Not started** | Nothing. It exists so the gap is visible |
| `◐` | **Draft catalogue** | Read it as a list of things somebody said exist. Not confirmed, nothing here to build on |
| `●` | **Validated** | Rely on it. Confirmed by a named person on a named date, and merged |

**A draft catalogue is not an architecture draft.** One is a proposal about how
something should be structured; the other is a list of what somebody said is
there, so it can be checked. `scripts/check_model.py` fails a document that
defines something without a mark.

## Conventions

The numbering, the element identifiers, and the notation the diagrams are
drawn in belong to the method rather than to this project, so they are not
restated here. The coding agent reads them from the
`architecture-document-style` rulebook; a human who wants them reads the same
file.

The one thing worth knowing before reading a diagram: **cyan is always an
automated (AI) actor**, so you never mistake one for a person, and **a dashed
edge means not true yet**.
