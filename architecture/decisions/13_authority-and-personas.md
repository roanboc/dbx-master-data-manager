# Decision 13 — Every commit names its authority, and local personas run only on a local store

_[← Decisions index](./README.md)_

**Status:** Proposed
**Date:** 2026-09-27
**Touches:** [authority and personas](../4_application/4_solution-design.md#authority-and-personas)

## Context

[Principle [`P2`] Approval in proportion to what a change can break](../1_strategy/1_motivation.md#principles) says every commit names what allowed it. [Rule [`RULE6`] Some actions are never automatic](../2_business/5_domain-context-and-rules.md#business-rules) keeps some actions from the automated matcher. [Rule [`RULE11`] Least access by default](../2_business/5_domain-context-and-rules.md#business-rules) gives the consumer role when a role lookup fails. Roles from workspace groups arrive with the interface and the deployment. The local mode needs a way to act as each role with no platform at all.

## Options considered

| Option | Why not (or why) |
| ------ | ---------------- |
| A database role per person | A role for every user, and row security that the owning role bypasses |
| No authority recorded on a commit | Against principle [`P2`] Approval in proportion to what a change can break |
| Personas refused only when platform variables are present | A laptop pointed at the operational database carries none, so personas and a reset would reach shared data |
| `MDM_ALLOW_PERSONAS=1` honoured for any Postgres | The same laptop, with a connection string that carries a token as its password, would count as local |
| An actor and an authority on every change set, checked before the commit and again inside its transaction, with personas allowed only on a local store | **Chosen.** Every commit names what allowed it, and a persona can never act on shared data |

## Decision

Every change set carries its actor and its authority, checked twice. Personas, the simulator and `mdm demo reset` run only when the store is local.

## Consequences

- An automated commit's authority names its rule versions and the source-policy clauses of its items ([decision 16](./16_source-policies-and-clauses.md)). A person's authority is their role.
- Merge, unmerge and retire need a checker other than the maker.
- The local mode is DuckDB, or a test Postgres marked with `MDM_ALLOW_PERSONAS=1`. It takes a persona from `--as` or `MDM_ROLE`.
- A store marked that way opens only when its server is on this machine, over a unix socket or a loopback address, and its database is not Lakebase's `databricks_postgres`; otherwise it refuses to open.
- Personas, the simulator and the reset are refused whenever a Lakebase endpoint is set, or a Databricks App or runtime variable is present. The live test switch, `MDM_LIVE_LAKEBASE`, is never honoured inside an App or a job.
- Until initiative 4 maps workspace groups to roles, every person on a shared store is a consumer.
- A model or rule set is published only before any golden record exists, under a flagged bootstrap authority. The dry run of [rule [`RULE4`] Governance changes are proven before they are published](../2_business/5_domain-context-and-rules.md#business-rules) arrives in initiative 4.
