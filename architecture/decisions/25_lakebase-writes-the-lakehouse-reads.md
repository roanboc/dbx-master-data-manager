# Decision 25 — Lakebase is the hub's one write path, and the lakehouse reads what it commits

_[← Decisions index](./README.md)_

**Status:** Accepted
**Date:** 2026-09-29
**Touches:** [operational database](../5_technology/1_technology-services.md#technology-services)

## Context

[Answer 5](../reference/2026-09-26-request-and-answers.md#answers) put the landing tables, the published master data and the change feed in Lakebase, and kept a sync to the lakehouse for analytics. A [later conversation](../reference/2026-09-29-engine-conversation.md) asked whether reconciliation belongs in the lakehouse instead, with Delta as the master, as a packaged product on the platform does. The incumbent hub delivers updates to listening systems every five minutes, and the product owner wants a platform that stays fit as needs grow. The platform's LTAP architecture puts transactions through Lakebase Postgres and analytics through Delta readers over one copy; today the copy is kept by Lakehouse Sync, and the single copy is announced as coming soon.

## Options considered

| Option | Why not (or why) |
| ------ | ---------------- |
| Lakebase leads: landing, decisions, commits and the change feed are written through Lakebase Postgres, and the lakehouse reads a copy | **Chosen.** Small transactional writes and reads within seconds, the commit-order lock and the undo tray as built, and the write path LTAP keeps when the copy becomes one |
| Delta leads, and Lakebase serves a copy through synced tables | Reads are fast but writes are batches: listening systems learn of a change only after the sync, and each steward decision becomes a lakehouse write |
| Split now: matching in a lakehouse job, decisions and commits in Lakebase | Sound at scale, but the need is unproven, and reading landed records through the lakehouse puts a sync on the inbound route that answer 5 kept for analytics |
| A packaged product that keeps Delta as the master | The second option's limits, plus a vendor dependency; masking by role, reveal with a reason, and undo were not shown |

## Decision

Lakebase Postgres is the hub's only write path. The lakehouse reads what the hub commits, through Lakehouse Sync today and through LTAP's single copy once it is generally available, for analytics and AI.

## Consequences

- Nothing in the code or in answer 5 changes: a sync to the lakehouse is still never the route to listening systems, which read the change feed within seconds, well inside the incumbent hub's five minutes.
- Bulk matching moves to a lakehouse job only if [initiative 4](../6_transition/2_sequence.md#sequence)'s throughput test on the platform shows the commits cannot keep up. The engine imports no store, so it can run in a job; the job would write proposals, and only the commit path would write `mdm_core`. That move gets a decision of its own.
- Initiative 4 checks that Lakehouse Sync, and later LTAP, are available in the workspace's region, and that the Lakebase project runs Postgres 17.
- `mdm_core` stays in portable types, so a copy in the lakehouse needs no reshaping.
