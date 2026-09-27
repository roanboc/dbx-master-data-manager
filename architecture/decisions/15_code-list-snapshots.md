# Decision 15 — The hub validates against its own versioned copy of the governed code lists

_[← Decisions index](./README.md)_

**Status:** Accepted
**Date:** 2026-09-27
**Touches:** [master data configuration](../3_information/2_data-objects.md#master-data-configuration)

## Context

The Reference Data Manager keeps the governed code lists ([resource [`RES4`] Governed code lists](../1_strategy/2_capabilities-and-resources.md#resources)) in lakehouse tables, which the operational database cannot join. [Answer 5](../reference/2026-09-26-request-and-answers.md#answers) made syncs between the lakehouse and the operational database a route for analytics, not for operations. Validation must give one answer on both engines ([principle [`P7`] One engine, one answer](../1_strategy/1_motivation.md#principles)). Scope document 1 left open how the hub reads the code lists.

## Options considered

| Option | Why not (or why) |
| ------ | ---------------- |
| A table synced into the operational database | A managed pipeline, and its connections, for every list |
| A live query to the lakehouse for each validation | Latency and cost on every check, and no answer in the local mode |
| A versioned copy, loaded by a job and pinned for each validation run | **Chosen.** Validation reads a table in its own store, and every result names the copy it used |

## Decision

The hub validates against a versioned copy of each governed code list, held in its own store.

## Consequences

- Locally, the copy loads from `models/codelists/`.
- The reader of the Reference Data Manager's tables on the platform comes with initiative 4.
- Each rule result records the version of the copy it used.
- The hub never writes a code list.
