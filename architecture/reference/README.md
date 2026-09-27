# Reference documents

_[← Model home](../README.md) · [Scope documents](../scope/README.md)_

The material this model was built from. The founding documents are held
privately by the product owner; what is filed here is a facts-only summary.

**This is not the model.** Nothing here defines an element or carries an
identifier, and the validators do not read it — a transcript in which somebody
says an element identifier out loud is a person talking, not a definition.
Neither is it published: the portal and the PDF hand a reader the model, and a
raw transcript carries everything else that was in the room that day, to an
audience that was not.

## What it is for

One question, asked late and hard to answer without it: **where did this come
from?**

A figure a Requester queries eighteen months on is answerable from the deck it
was read off, and an element in a draft catalogue names its source here — which
is what lets the merge that validates it be a review rather than an act of
faith.

## Naming

`YYYY-MM-DD-<short-description>.<ext>`, plain ASCII with hyphens.

The date is, in order of preference:

1. **When the meeting happened** — for a transcript, minutes, or a recording.
2. **When the document was shared** — for anything sent, presented or handed
   over.
3. **When it was added here** — the fallback, when neither of the first two
   can be established.

Take the first that can be established, and where it is not the first, the
index row says which one the date is.

**The original filename lives in the index, not on disk.** A file arriving as
`Strategy Review FINAL v3.pptx` keeps that name in the table below, where it
still matches the sender's copy; on disk it becomes a dated slug, because
spaces and capitals in a path break links and tooling.

## Founding material

The product owner provided these. The Blueprint and the two platform
proposals are held privately by the product owner and are not in this public
repository. The request and answers are filed here as a facts-only summary.

| Date | Fixed by | File | Original name | Provided by | Derived into |
| ---- | -------- | ---- | ------------- | ----------- | ------------ |
| 2026-09-26 | meeting | [The request](./2026-09-26-request-and-answers.md#the-request) | none (a conversation, summarised the same day) | The product owner | [Stakeholders, drivers and goals](../1_strategy/1_motivation.md); [the value stream](../1_strategy/3_value-stream.md) |
| 2026-09-26 | meeting | [Answers 1–4](./2026-09-26-request-and-answers.md#answers) | none (a conversation, summarised the same day) | The product owner | [Goals and outcomes](../1_strategy/1_motivation.md#goals) on Release 1's entities, volume and personal data; [AI assistance](../1_strategy/2_capabilities-and-resources.md#capabilities) in Release 2; [declared capacity](../5_technology/3_capacity-and-throughput.md#declared-capacity) for answer 3 |
| 2026-09-26 | meeting | [Answer 5, the decision on operational integration](./2026-09-26-request-and-answers.md#answers) | none (a conversation, summarised the same day) | The product owner | [The assessment on how source changes arrive and the driver on automatic propagation](../1_strategy/1_motivation.md); [the landing tables](../1_strategy/2_capabilities-and-resources.md#resources); [the Land and Propagate stages](../1_strategy/3_value-stream.md); [the landing and listener contracts](../2_business/1_business-actors-and-roles.md#contracts); [the landing and listener interfaces](../4_application/5_interface-contracts.md) |
| 2026-09-26 | shared | Held privately, not filed | *Master Data Manager Blueprint*, the approved product proposal, and its research notes | The product owner (written with the coding agent) | [Principles](../1_strategy/1_motivation.md#principles); [the capability map](../1_strategy/2_capabilities-and-resources.md#capabilities); [the automated actors](../2_business/1_business-actors-and-roles.md#actors); [the default approval matrix, written as rules](../2_business/5_domain-context-and-rules.md#business-rules); [the data objects](../3_information/2_data-objects.md) and [decisions 5–17](../decisions/README.md) |
| 2026-09-26 | added | Held privately, not filed | The data and analytics unit's platform proposal on master data management and data quality | The product owner | [The driver on moving master data management onto the platform](../1_strategy/1_motivation.md#drivers) |
| 2026-09-26 | added | Held privately, not filed | The data and analytics unit's platform proposal on event integration paths | The product owner | [The landing and listener contracts](../2_business/1_business-actors-and-roles.md#contracts) |

- **Fixed by** — which of the three rules gave the date: *meeting*, *shared*
  or *added*.
- **Derived into** — the documents or elements that came out of it, or
  *nothing yet*. This is the column that makes the folder worth keeping.

Answer 5 came after the Blueprint was approved. It replaced the Blueprint's
route for landing source changes and propagating committed ones, and where
the two differ, the model follows answer 5.

## Public references

These are cited, never copied or filed here. They propose; an element's
`Source` names what decided it.

- DAMA-DMBOK2 Revised (2024), chapters 3, 7, 10 and 13.
- The Gartner Magic Quadrant for Master Data Management Solutions (2026), its
  market definition only, naming no vendor.
- ISO 8000-115 (quality identifiers).
- ISO 8000-120 (provenance).
- ISO/IEC 25012 (data quality characteristics).
- The Open Data Contract Standard (ODCS) v3.
- The platform's public documentation, read 26 Sep 2026.

## What does not belong here

| Not this | Where it goes |
| -------- | ------------- |
| Your reading of what a document means | The layer document it informs, cited back here |
| A decision taken in the meeting | [`architecture/decisions/`](../decisions/README.md), or a scope document |
| Anything with an element identifier | The model. If it has identifiers, it is not a reference document |
| Credentials, personal data, or anything shared in confidence that the model does not need | Nowhere in the repository |

The last row is the one worth checking before committing: people say things in
meetings that they did not intend to put under version control.
