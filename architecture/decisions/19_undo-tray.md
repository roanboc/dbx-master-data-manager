# Decision 19 — A steward's decision waits in a server-side undo tray, and commits through the commit path once its window passes

_[← Decisions index](./README.md)_

**Status:** Accepted
**Date:** 2026-09-27
**Touches:** [data object [`DOBJ3.5`] Staged decision](../3_information/2_data-objects.md#resolution-work)

## Context

Every commit reaches listening systems at once ([driver [`DRV1`] Every commit reaches listening systems automatically](../1_strategy/1_motivation.md#drivers)). [Principle [`P4`] Every decision is reversible and recorded](../1_strategy/1_motivation.md#principles) asks for undo before commit, and [outcome [`OUT2`] Mistakes are caught before listening systems see them](../1_strategy/1_motivation.md#outcomes) counts the mistakes undone in time. Only the commit path writes the published tables ([decision 8](./8_commit-order-lock-and-change-feed.md)). The approved design staged a decision for 60 seconds, and had arrival skip records with a staged decision. Arrival moves a record's state in its own transaction, outside the commit-order lock.

## Options considered

| Option | Why not (or why) |
| ------ | ---------------- |
| Commit at once, and compensate a mistake | Listening systems see every mistake and then its correction |
| Hold the decision in the browser until the window passes | A closed tab loses it, and nobody else can see it |
| A staged row in the published tables | Listening systems would read it, and only the commit path writes there |
| Arrival skips a record with a staged decision | Arrival would depend on the tray, and the decision would still rest on what the steward saw before the new event |
| A staged decision in the hub's work tables, flushed after its deadline through the commit path, whose own transaction checks that the record is at the event the steward saw and the task still open, and settles the staged decision | **Chosen.** An undo never reaches listening systems, a crash neither loses nor repeats a decision, and a record that moved is never decided on stale evidence |

## Decision

Every steward decision is staged for its undo window. Then the tray's flush commits it exactly once, or hands its task back.

## Consequences

- The window is the [declared undo window](../5_technology/3_capacity-and-throughput.md#declared-capacity), set by `MDM_UNDO_SECONDS`.
- A decision is staged only against the case the steward saw: the record's event, or the task's version for a task with no source record. A key pressed while the next case is still loading decides nothing.
- While a decision waits, nobody claims, snoozes or escalates its task, since the flush would close the task under them. A decision that fails releases only its own steward's claim.
- The flush checks the golden records a decision acts on again: still active, at the row version it was staged against, and, for a link, with no cannot-link rule against a member that joined meanwhile.
- The flush takes a lease, so two flushes never overlap, and each decision commits in its own transaction.
- A decision whose record, task or target moved settles failed with its reason, and its task returns to the queue.
- Every decision is audited, even one that publishes nothing, such as "Not a match".
- The workbench's own process flushes on a local store. On the platform a job runs `mdm tray flush`, which initiative 4 schedules.
- A persona's decision flushes only on a local store.
- The flush checks the role the steward held when staging. Once initiative 4 maps workspace groups to roles, it resolves the role again at flush.
