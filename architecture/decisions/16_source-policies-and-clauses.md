# Decision 16 — Each source's policy is part of its entity model, and every automated change names the clause that allowed it

_[← Decisions index](./README.md)_

**Status:** Proposed
**Date:** 2026-09-27
**Touches:** [authority and personas](../4_application/4_solution-design.md#authority-and-personas)

## Context

[Rule [`RULE1`] Source traffic follows the source's policy](../2_business/5_domain-context-and-rules.md#business-rules) says what each source may commit on its own. [Principle [`P2`] Approval in proportion to what a change can break](../1_strategy/1_motivation.md#principles) asks every commit to name the matrix row, source policy or rule version that allowed it. The approval matrix, which includes the source policies, arrives with the steward workbench in initiative 3, but arrivals need the policies now.

## Options considered

| Option | Why not (or why) |
| ------ | ---------------- |
| Policies as a versioned object of their own, beside the matrix | It waits for initiative 3 |
| A configuration file per source, outside the store | Not versioned with the model, and not audited |
| One automated change set per source and clause | It splits a new golden record from the links of its members from two sources |
| Policies inside the entity model version, and the clause recorded on every automated item | **Chosen.** A policy changes only by publishing a model version, and every automated change says which clause let it through |

## Decision

Each source's policy is part of the entity model version, and every automated item of a change set records the clause that allowed or held it.

## Consequences

- A clause reads `<source>.<policy>=<value>`, such as `crm.critical_update=hold`. A policy has five fields: `new`, `update`, `critical_update` and `end_date`, each `auto` unless the model says `hold`, and `master_id`, `hold` unless the model says `auto`.
- What rule [`RULE1`] Source traffic follows the source's policy lets through on its own reads `rule1:<case>`. The cases are an arrival in the automatic band (`auto_band`), a deletion marker (`delete`) and an arrival carrying a retired ID (`retired_id`).
- That rule does not make an arrival carrying an active master ID automatic, so the `master_id` field decides it: by default the record waits for a steward as a review task, and only a data owner's `auto` links it. Even then a valid strong ID that breaks a cannot-link rule against the golden record's members makes it an exception task. An arrival carrying an ID the hub does not know becomes an exception task.
- The commit log's authority names every rule version and every clause the commit used, and each change-log row names the clause of its item.
- Changing a policy is publishing a model version. From initiative 4, the dry run of [rule [`RULE4`] Governance changes are proven before they are published](../2_business/5_domain-context-and-rules.md#business-rules) applies to it.
- The approval matrix of initiative 3 refers to these clauses rather than copying them.
