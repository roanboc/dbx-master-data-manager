# Decision 1 — The automated matcher acts in the automatic band

_[← Decisions index](./README.md)_

**Status:** Proposed
**Date:** 2026-09-26
**Touches:** [actor [`ACT6`] Automated matcher](../2_business/1_business-actors-and-roles.md#automated-matcher)

## Context

Every commit reaches listening systems at once, through the platform's change notifier and the integration platform ([driver [`DRV1`] Every commit reaches listening systems automatically](../1_strategy/1_motivation.md)). Stewards cannot review every arrival ([driver [`DRV4`] Steward attention is the scarce resource](../1_strategy/1_motivation.md)). Matching is arithmetic under a published, versioned rule set, so the same input always gives the same decision ([principle [`P6`] Arithmetic decides, the language model advises](../1_strategy/1_motivation.md); [principle [`P7`] One engine, one answer](../1_strategy/1_motivation.md)). Release 1 is sized for under a million golden records per entity ([assessment [`ASM3`] Release 1 volumes stay under a million golden records per entity](../1_strategy/1_motivation.md)).

## Options considered

| Option | Why not (or why) |
| ------ | ---------------- |
| Advisory: every link waits for a steward | The inbox grows with every arrival, and stewards stall |
| Autonomous with checkpoint: it acts only in the automatic band, under the published rules, the source's policy and the breaker, audited and sampled | **Chosen.** Routine arrivals commit without a person, and every automated decision can still be reversed, traced and checked |
| Fully autonomous, including merges and review-band decisions | A wrong merge reaches every listening system and is the costliest error to reverse ([assessment [`ASM2`] A wrong merge is the costliest error to reverse](../1_strategy/1_motivation.md)). It contradicts [principle [`P2`] Approval in proportion to what a change can break](../1_strategy/1_motivation.md) and [rule [`RULE6`] Some actions are never automatic](../2_business/5_domain-context-and-rules.md) |
| A language model decides matches | Nobody could explain its scores, against [principle [`P3`] Nothing unexplained](../1_strategy/1_motivation.md), and it breaks principle [`P6`] Arithmetic decides, the language model advises |

## Decision

The automated matcher is an automated, deterministic actor that uses no language model, at the autonomy level **autonomous with checkpoint**. It commits only what [rule [`RULE1`] Source traffic follows the source's policy](../2_business/5_domain-context-and-rules.md) lets source traffic commit automatically. It also recomputes golden values when a pin expires, and applies approved rule versions. Everything else becomes a steward's task. Its decision rights, limits and escalation are in [its profile](../2_business/1_business-actors-and-roles.md#automated-matcher).

## Consequences

- Every automated decision leaves an audit record that names its rule version.
- Blind review re-decides a share of automated decisions.
- The quality breaker can demote the automatic band ([Decision 3](./3_quality-breaker-autonomy.md)).
- A wrong automatic link is reversed by detach, which is recorded like any other decision.
- A rule version re-evaluates existing records only after a dry run whose fingerprint was approved, under [rule [`RULE4`] Governance changes are proven before they are published](../2_business/5_domain-context-and-rules.md).
- The steward inbox absorbs the review-band work.
- Automation grants, planned for Release 2, would widen the matcher's automatic band, so they need a superseding decision record.
