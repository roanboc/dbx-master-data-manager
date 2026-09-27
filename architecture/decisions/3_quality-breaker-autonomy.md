# Decision 3 — The quality breaker may only reduce automation

_[← Decisions index](./README.md)_

**Status:** Proposed
**Date:** 2026-09-26
**Touches:** [actor [`ACT8`] Quality breaker](../2_business/1_business-actors-and-roles.md#quality-breaker)

## Context

Pattern decisions and the automatic band can repeat one error many times. Blind review measures how often a second steward agrees with the first decision. A source that reloads its history can flood the automatic band. Every commit reaches listening systems at once ([driver [`DRV1`] Every commit reaches listening systems automatically](../1_strategy/1_motivation.md)).

## Options considered

| Option | Why not (or why) |
| ------ | ---------------- |
| Advisory: it alerts a person, who withdraws the rights | Errors keep committing until someone reads the alert |
| Autonomous with checkpoint, in one direction: it withdraws rights and demotes the band, and never widens either | **Chosen.** Automation stops as soon as agreement falls, and only a person can widen it again |
| Autonomous both ways: it also restores rights and widens bands | It would widen automation without a data owner, against [principle [`P2`] Approval in proportion to what a change can break](../1_strategy/1_motivation.md) and [rule [`RULE4`] Governance changes are proven before they are published](../2_business/5_domain-context-and-rules.md) |

## Decision

The quality breaker is an automated, deterministic actor that uses no language model, at the autonomy level **autonomous with checkpoint**, and it can only reduce automation. A data owner restores what it withdrew. Its decision rights, limits and escalation are in [its profile](../2_business/1_business-actors-and-roles.md#quality-breaker).

## Consequences

- Every trip is logged with its reason.
- Its thresholds are part of [business object [`BOBJ7`] Governance policy](../2_business/4_business-objects.md), which a data owner approves.
- A volume spike demotes only the automatic band. It leaves bulk rights alone, because only blind review withdraws them.
