# Decision 6 — One SQL store runs on DuckDB and on Postgres

_[← Decisions index](./README.md)_

**Status:** Accepted
**Date:** 2026-09-27
**Touches:** [one store, two engines](../4_application/4_solution-design.md#one-store-two-engines)

## Context

[Goal [`G6`] One product, locally on DuckDB and as a Databricks App on Lakebase](../1_strategy/1_motivation.md#goals) asks for one product in both places. [Principle [`P7`] One engine, one answer](../1_strategy/1_motivation.md#principles) asks for the same answers in both. The EA Repository settled a pattern for this: one store written in Structured Query Language (SQL), and two engines that differ only in how they connect. The Reference Data Manager keeps two backends instead.

## Options considered

| Option | Why not (or why) |
| ------ | ---------------- |
| One shared SQL store with thin engine hooks | **Chosen.** Every statement is written once, in portable SQL. Each engine adds only how to connect, run a statement, bind a JSON row document and take the commit lock |
| Two backends, one per engine | The Reference Data Manager's pattern. Two implementations drift apart, and [principle [`P7`] One engine, one answer](../1_strategy/1_motivation.md#principles) then rests on keeping them in step by hand |
| An object-relational mapper | Another layer, and the dialects still differ in JavaScript Object Notation (JSON) and in locks |
| Postgres only, on the laptop too | It loses the local mode on one file, which [goal [`G6`] One product, locally on DuckDB and as a Databricks App on Lakebase](../1_strategy/1_motivation.md#goals) asks for |

## Decision

All SQL lives in one store, `src/mdm/backend/store.py`, and two engine modules add only what differs between DuckDB and Postgres.

## Consequences

- Only portable types are used, and the DuckDB suite and the Postgres suite are the same tests.
- Every insert and upsert binds one JSON document per chunk of up to 10,000 rows, and every keyed read joins a JSON document of keys. Both engines run the same statement, except how one value of a row is read, which is an engine hook: DuckDB converts the document to lists of text once, and Postgres reads each value with `->>`.
- On 2026-09-27 an upsert of a six-column table measured 110,000–160,000 rows a second on DuckDB and 65,000–80,000 on Postgres. A source state has 21 columns, and saving one measured about 11,500 rows a second on DuckDB and 15,000 on Postgres.
- JSON is decoded in the store, so both engines return the same Python values. DuckDB needs `pytz` to return times with a time zone.
- Every result set is ordered explicitly.
- A commit runs at read committed on Postgres and takes its lock before it reads ([decision 8](./8_commit-order-lock-and-change-feed.md)).
- A live Lakebase run is gated by `MDM_LIVE_LAKEBASE`, until initiative 4 gives the hub a workspace.
