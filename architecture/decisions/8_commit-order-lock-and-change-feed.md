# Decision 8 — Each commit takes the commit-order lock and writes the change feed itself

_[← Decisions index](./README.md)_

**Status:** Accepted
**Date:** 2026-09-27
**Touches:** [listener interface](../4_application/5_interface-contracts.md#listener-interface)

## Context

[Answer 5](../reference/2026-09-26-request-and-answers.md#answers) puts the change feed inside the operational database, with commit versions. A change notifier the platform runs announces new versions, and each consumer reads from its own watermark. A sequence number is taken when a row is inserted, not when its transaction commits, so a plain counter lets a reader skip a version that commits late. [Principle [`P1`] The hub never propagates](../1_strategy/1_motivation.md#principles) forbids the hub notifying or tracking a listening system. Scope document 1 left open who writes the change feed.

## Options considered

| Option | Why not (or why) |
| ------ | ---------------- |
| The commit path writes the change rows and a commit-log row in its own transaction, after taking a transaction-level lock and then the next version | **Chosen.** Versions are gap-free and become visible in version order, and the feed can never disagree with the published rows |
| Triggers on the published tables | DuckDB has none, so principle [`P7`] One engine, one answer fails, and the logic hides in the database |
| The platform's change data feed as the operational route | Answer 5 rules it out; it is in preview and works per schema |
| An outbox and a relay run by the hub | The hub would carry changes to listening systems, against [principle [`P1`] The hub never propagates](../1_strategy/1_motivation.md#principles) |
| Versions from a sequence, without a lock | A reader can skip a version that commits late |
| `pg_notify` as a wake-up call | The hub would notify listening systems, against principle [`P1`] The hub never propagates |

## Decision

Every commit takes the commit-order lock, then the next commit version, and writes its change rows and one commit-log row in the same transaction as the published rows.

## Consequences

- Versions are gap-free and visible in version order. A commit runs at read committed, so it reads the latest version after the lock.
- Commits are serialised, and their size is bounded: 500 published rows a commit interactively, 10,000 in bulk.
- Retired and merged records stay as tombstones. A retired ID whose survivor changes gets a `remapped` change row.
- Each commit-log row counts its change rows. A reader pages by commit version and change sequence, and moves its watermark only past a commit it read to the end.
- The commit log names a role, never a person; the person is in the audit record.
- The change notifier needs only a read grant on `mdm_core.commit_log`.
