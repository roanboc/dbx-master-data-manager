# Decision 11 — Repeating groups are stored as JSON documents

_[← Decisions index](./README.md)_

**Status:** Proposed
**Date:** 2026-09-27
**Touches:** [portable types](../3_information/4_data-architecture.md#portable-types)

## Context

An entity can hold repeating groups, such as the addresses of a person or of an organisation. The published tables must stay the same on both engines ([principle [`P7`] One engine, one answer](../1_strategy/1_motivation.md#principles)), and copyable to the lakehouse for analytics. Listening systems read the published rows directly.

## Options considered

| Option | Why not (or why) |
| ------ | ---------------- |
| A child table per group | More published tables for every entity, and a join for every listening system |
| Arrays | Not portable between the engines, and not copyable to the lakehouse |
| JavaScript Object Notation (JSON): `jsonb` on Postgres and `JSON` on DuckDB | **Chosen.** One column per group, the same on both engines |

## Decision

Each repeating group is one JSON column in the published row, and in the hub's own tables.

## Consequences

- An entity table has one column per group, however many entries the group holds.
- Survivorship takes the winning source's whole group, or a union keyed on the group's key, in Python.
- A group holding personal values is masked as a whole.
