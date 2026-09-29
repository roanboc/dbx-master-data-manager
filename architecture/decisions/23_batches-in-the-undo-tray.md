# Decision 23 — A batch of alike reviews is one staged decision, and commits in chunks that each check and settle their own reviews

_[← Decisions index](./README.md)_

**Status:** Accepted
**Date:** 2026-09-29
**Touches:** [data object [`DOBJ3.9`] Signature batch](../3_information/2_data-objects.md#resolution-work)

## Context

[Rule [`RULE7`] Bulk decisions pass a forced sample](../2_business/5_domain-context-and-rules.md#business-rules) decides alike reviews together after a forced sample, and [story 3.3](../scope/3_steward-workbench-stories/3_signature-batches.md) asks for one entry in the undo tray, committed in chunks. [Decision 19](./19_undo-tray.md) stages each decision for its window and commits it in its own transaction. [Decision 8](./8_commit-order-lock-and-change-feed.md) bounds a commit at 500 published rows. A batch of 566 links publishes 1,132 rows.

## Options considered

| Option | Why not (or why) |
| ------ | ---------------- |
| One tray entry per review | A steward undoes 566 entries one by one, and a second steward confirms 566 entries without ever seeing the batch |
| One transaction for the whole batch | It breaks decision 8's bound, and holds the commit-order lock for the whole batch |
| Arrival's chunked commit | It closes every task, writes every label and settles the entry only in the last chunk, and counts items rather than published rows |
| One entry, one chunk a flush pass, each chunk's transaction checking and settling its own reviews, the entry settled by the first chunk, and a batch record of every chunk | **Chosen.** Undo and a chunk wait for each other on the batch and never both win, a crash neither loses nor repeats a chunk, a moved review fails alone, and other stewards' decisions flush between chunks |

## Decision

A batch is one staged decision. The flush commits it one chunk at a time, each in its own transaction, exactly once.

## Consequences

- Every review gets the checks a single decision gets. Before its chunk, the flush checks its task, its record's event and its target's row version. For a link, it also checks that no cannot-link rule holds against the target's members, or against the batch's earlier reviews that join the same target. Inside the chunk's transaction, it checks the events, tasks and rows again.
- A review that moved fails alone, and its task returns to the queue. When every remaining review fails after the first chunk, the batch ends committed with what it linked.
- While the batch waits or commits, nobody claims, snoozes, escalates or decides its reviews.
- Every transaction on a batch takes the batch first, so an Undo, a Stop and a chunk wait for one another rather than deadlock.
- The first chunk settles the entry, so Undo ends there. Stop and a withdrawal of bulk rights end the batch before its next chunk, whatever the throttle. Committed chunks are compensated, never recalled.
- Three failed passes in a row stop the batch. A chunk that commits starts the count again.
- Each chunk is its own change set, whose ID is formed from the batch ID, with its own commit version, so listeners see ordinary commits.
- The throttle paces the chunks without holding the tray.
- A compensation is a batch too, and goes through the tray.
