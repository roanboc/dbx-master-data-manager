# Decision 2 — The work router ranks and escalates, and decides nothing

_[← Decisions index](./README.md)_

**Status:** Proposed
**Date:** 2026-09-26
**Touches:** [actor [`ACT7`] Work router](../2_business/1_business-actors-and-roles.md#work-router)

## Context

Steward tasks arrive from many sources, each with a service level. Stewards need the next most urgent task without choosing it themselves. A routing mistake is cheap and visible: a task sits lower in the inbox, and nothing commits.

## Options considered

| Option | Why not (or why) |
| ------ | ---------------- |
| Advisory: it suggests an order, and the coordinating steward assigns every task | A person routes work all day |
| Autonomous with checkpoint: it ranks tasks, escalates breaches and releases lapsed claims, and the coordinating steward can re-route any task | **Chosen.** Stewards always see the next most urgent task, and a person can correct any rank |
| A language model routes tasks | Nobody could explain its ranks, against [principle [`P3`] Nothing unexplained](../1_strategy/1_motivation.md) and [principle [`P6`] Arithmetic decides, the language model advises](../1_strategy/1_motivation.md) |

## Decision

The work router is an automated, deterministic actor that uses no language model, at the autonomy level **autonomous with checkpoint**. It orders the stewards' work and decides no task. Its decision rights, limits and escalation are in [its profile](../2_business/1_business-actors-and-roles.md#work-router).

## Consequences

- Every rank shows its reasons.
- Every escalation is logged.
- Service-level calendars are part of [business object [`BOBJ7`] Governance policy](../2_business/4_business-objects.md), which a data owner approves.
- The coordinating steward can override any rank or route.
