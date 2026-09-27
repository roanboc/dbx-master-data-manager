# Decision 9 — Source changes land in one shared table, read from a high-water mark that probes every gap again

_[← Decisions index](./README.md)_

**Status:** Proposed
**Date:** 2026-09-27
**Touches:** [landing interface](../4_application/5_interface-contracts.md#landing-interface)

## Context

[Answer 5](../reference/2026-09-26-request-and-answers.md#answers) has the integration platform write source changes straight into landing tables in the operational database. Delivery is at least once. Several writers can commit a lower landing sequence after a higher one. Both engines use up a sequence number on a rejected duplicate and on a rolled-back insert, so the sequence has gaps in normal running. [Principle [`P8`] Entity models are data; the product is neutral](../1_strategy/1_motivation.md#principles) says adding an entity needs no code change.

## Options considered

The shape of the landing tables:

| Option | Why not (or why) |
| ------ | ---------------- |
| A table per entity | A table and a grant for every new entity, against [principle [`P8`] Entity models are data; the product is neutral](../1_strategy/1_motivation.md#principles) |
| A database function the integration platform calls | The hub would own the write path into its own input |
| One shared table, keyed by event ID, with a landing sequence | **Chosen.** One table and one grant serve every entity and every source |

How the hub reads it:

| Option | Why not (or why) |
| ------ | ---------------- |
| A plain watermark | It loses a row that commits after a higher one was read |
| An overlap window below a watermark | It loses a commit slower than the window, or stalls every row above a gap if the watermark waits for it |
| Transaction IDs in the landing row, read below the oldest running transaction | Exact, but Postgres only, and a column in a table another team owns. Kept as the upgrade if a reconciliation ever finds a late row |
| A high-water mark with gap ranges | **Chosen.** New rows are read above the highest number taken, and each missing range is a gap probed again on every run. A gap older than 10 minutes is declared lost only once a run's probe has read it to the end and found it empty, and is probed again daily until the 14-day landing retention has passed |

## Decision

Every source change lands in `mdm_landing.source_change`, and the hub reads it from a high-water mark, probing every gap below it again.

## Consequences

- The reader never stalls, and a jump in the sequence costs one gap row.
- The integration platform commits each insert within 60 seconds, takes `landing_seq` only from the column default, and never resets the sequence, which is created with `CACHE 1`.
- A versioned source carries its version on every row, deletes included.
- Delivery is at least once, and the effect is exactly once. The event ID, versions applied only forward, and a queue that the commit transaction clears make it so.
- A row that breaks the interface is rejected with a reason, never dropped.
- On the platform, the landing table belongs to the integration platform, and the hub's role only reads it.
