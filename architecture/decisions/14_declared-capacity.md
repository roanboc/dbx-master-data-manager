# Decision 14 — Release 1 is built for under a million golden records per entity

_[← Decisions index](./README.md)_

**Status:** Accepted
**Date:** 2026-09-27
**Touches:** [declared capacity](../5_technology/3_capacity-and-throughput.md#declared-capacity)

## Context

[Answer 3](../reference/2026-09-26-request-and-answers.md#answers) sizes Release 1 for under a million golden records per entity, provided the path to five million stays easy. [Assessment [`ASM3`] Release 1 volumes stay under a million golden records per entity](../1_strategy/1_motivation.md#assessments) records it. A figure nobody wrote down lets unbounded reads creep in, one convenient query at a time.

## Options considered

| Option | Why not (or why) |
| ------ | ---------------- |
| Five million now | Longer spikes and more compute, for a volume Release 1 does not hold |
| Under one million, with the path to five million kept easy | **Chosen.** The design holds at the expected volume, and nothing in it blocks the larger one |
| No declared figure | Unbounded reads creep in, and nothing tests against them |

## Decision

Release 1 is built and tested for under one million golden records per entity. The figures live in `src/mdm/capacity.py`.

## Consequences

- Every scan is paged by key, and blocking keys are stored rather than recomputed.
- A record keeps the 200 candidates that share the most blocking passes.
- Jobs resume from their queue and their position.
- Writes go in chunks of 10,000 rows.
- An initial load writes one summary audit row per commit, and stores only the quality rules that failed.
- A test fails any read of a large table that is neither keyed nor paged.
- The throughput spike measures locally. The run on the platform is spend, and belongs to initiative 4.
