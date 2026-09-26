# Architecture — <Project Name>

_The front door of this project's model._

**This folder is what your project knows about itself** — who it is for, what
it does, and which piece of software does each part. It is written in plain
Markdown so that you, your colleagues and your coding agent all read the same
thing, and so a change to it shows up in a pull request like any other change.

Nothing here is generated. Nothing here is a copy of something else. If a
document says a thing, that is what the project claims is true today.

## What is modeled, and what is not

**One row per layer, and every row says something.** A layer with no file yet
is a stated fact — `Out of scope`, `External`, or a named `Gap` — not a
silence, so a reader can tell what was decided from what was never looked at.

| # | Layer | The question it answers | Status |
| - | ----- | ----------------------- | ------ |
| 0 | Business design | Who are the customers, and how does each offering pay? | `Out of scope` — this project models an application, not an organization |
| 1 | Strategy | Why does this exist, and what must it be able to do? | `Gap` — not yet started |
| 2 | Business | Who does what, and which services are offered? | `Gap` — not yet started |
| 3 | Information | What information exists, and where does it live? | `Gap` — not yet started |
| 4 | Application | Which software realizes each business service? | `Gap` — not yet started |
| 5 | Technology | What runs it all — runtimes, build, hosting? | `Gap` — not yet started |
| — | Transition | Where is this going, and in what order? | `Gap` — not yet started |

<!--
  TEMPLATE — replace each Status cell as the model fills in. The four values:

  `Local`        — a folder in this repository holds it; link the folder.
  `External`     — another model owns it; name that model, e.g.
                   `External — owned by acme-platform`.
  `Out of scope` — deliberately not modeled here, with the reason.
  `Gap`          — it should exist and does not, with what is missing.

  A folder is created when it has something to say. An empty folder is not a
  plan; this row is.
-->

## How deeply this project models itself

**Declared depth: _not yet declared_.**

| Depth | The subject is | You get | Approval |
| ----- | -------------- | ------- | -------- |
| **1 — Application** | one app or tool | a light strategy layer — goals and principles, enough to judge a change against | Your merge of the pull request |
| **2 — Organization** | a company, department, or service line | the canvases, and the operating model derived from them | Your merge of the pull request |
| **3 — Enterprise** | several business lines | the above, plus each line modeled as a domain with its own charter | Your merge, plus each affected domain's owner told at the same pull request |

Depth is about the subject, not the effort — a large application is still
Depth 1. It is a starting posture, never a ceiling: deepening is an ordinary
change, decided by whoever asked for this project.

## How far a document has been validated

Every document that defines anything says so in its own preamble, with one of
three marks:

| | Status | What you may do with it |
| - | ------ | ----------------------- |
| `○` | **Not started** | Nothing. It exists so the gap is visible |
| `◐` | **Draft catalogue** | Read it as a list of things somebody said exist. Not approved, nothing here to build on |
| `●` | **Validated** | Rely on it. Confirmed on a named date, when the pull request that changed it merged |

**A draft catalogue is not an architecture draft.** One is a proposal about how
something should be structured; the other is a list of what somebody said is
there, so it can be checked. `scripts/check_model.py` fails a document that
defines something without a mark.

## Conventions

The numbering, the element identifiers, and the notation the diagrams are
drawn in belong to the method rather than to this project, so they are not
restated here. Your agent reads them from the `architecture-document-style`
rulebook; a human who wants them reads the same file.

The one thing worth knowing before reading a diagram: **cyan is always an AI
actor**, so you never mistake one for a person, and **a dashed edge means not
true yet**.
