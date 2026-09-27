# Decision 7 — The tables sit in schema groups, and one published schema only the commit path writes

_[← Decisions index](./README.md)_

**Status:** Proposed
**Date:** 2026-09-27
**Touches:** [schema groups](../3_information/4_data-architecture.md#schema-groups)

## Context

Listening systems, people and an analytics copy each need a different read of the hub. Postgres grants are given per schema, and a sync to the lakehouse copies whole schemas. [Principle [`P1`] The hub never propagates](../1_strategy/1_motivation.md#principles) says only the commit path changes the published tables. [Principle [`P8`] Entity models are data; the product is neutral](../1_strategy/1_motivation.md#principles) says a new entity needs no code change, and so no manual grant either.

## Options considered

| Option | Why not (or why) |
| ------ | ---------------- |
| One schema for everything | Grants per table, and anything added later is exposed by default |
| A schema per entity | Listening systems chase a new schema for every entity |
| Eight groups, `model`, `landing`, `work`, `hub`, `vault`, `core`, `read` and `audit`, under the prefix `MDM_SCHEMA_PREFIX` | **Chosen.** The integration platform's role reads only `core`, people read only `read`, the landing group belongs to the integration platform, and the vault stays out of every copy |

## Decision

The hub's tables sit in eight schema groups, and `core` is the one published schema, written only by the commit path.

## Consequences

- Only `src/mdm/services/commit.py` writes `core`, inside the commit-order lock. The store's guard refuses any other write the hub attempts, and a test proves it; against anyone else, the database grants are the boundary.
- `core` holds only portable types, no partitioned tables, and columns only added at the end, so a copy to the lakehouse for analytics stays possible.
- Default privileges on `core` give the listener role every new entity table without a manual grant. The change-notifier role reads the commit log alone.
- The tests run each in a prefix of their own, so they never touch the hub's schemas.
