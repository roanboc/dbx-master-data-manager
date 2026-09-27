# Decision 22 — A steward's label binds the automated matcher, and "Not a match" hands the record back to arrival

_[← Decisions index](./README.md)_

**Status:** Proposed
**Date:** 2026-09-27
**Touches:** [actor [`ACT6`] Automated matcher](../2_business/1_business-actors-and-roles.md#automated-matcher)

## Context

[Resource [`RES5`] Steward match labels](../1_strategy/2_capabilities-and-resources.md#resources) keeps every match decision for tuning. If the matcher ignored a label, a record a steward separated would link back automatically at its next update. [Rule [`RULE1`] Source traffic follows the source's policy](../2_business/5_domain-context-and-rules.md#business-rules) settles a distinct arrival under its source's policy. [Rule [`RULE3`] Four eyes on what is hard to reverse](../2_business/5_domain-context-and-rules.md#business-rules) gives a steward's create in a coexistence or authored domain a checker.

## Options considered

| Option | Why not (or why) |
| ------ | ---------------- |
| Labels kept for tuning only | The matcher links again what a steward separated |
| "Not a match" creates the golden record as the steward's own create | In a coexistence domain that needs a checker, and it bypasses the source's policy |
| A not-a-match label the matcher honours, with the record queued again for arrival, which settles it under its source's policy without the declined golden records and names the steward's decision | **Chosen.** The steward decides identity, and the source's policy decides creation, as for any distinct arrival |

## Decision

Steward labels bind the automated matcher. A declined record is settled again by arrival.

## Consequences

- Arrival drops a declined golden record's members from a record's candidates. Its change set's evidence names the staged decisions whose labels it honoured (`declined_by`).
- A possible duplicate kept apart is not opened again.
- Each label keeps the rule version, score, band and signature the steward saw, for label tuning in Release 2.
- A later link on the same pair replaces the label.
- A label only narrows automation, so it needs no data owner.
- The stop check of [scope document 3](../scope/3_steward-workbench.md#stop-check) records that an automated create after a steward's "Not a match" is not a steward's create.
