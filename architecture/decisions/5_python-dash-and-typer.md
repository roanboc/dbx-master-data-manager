# Decision 5 — The hub is written in Python, with a Dash interface and a Typer command line

_[← Decisions index](./README.md)_

**Status:** Accepted
**Date:** 2026-09-27
**Touches:** [technology service [`TSVC1`] Python runtime and packaging](../5_technology/1_technology-services.md#technology-services)

## Context

One language has to hold the matching engine, the store, the command line (CLI) and the web interface that initiative 3 adds. It has to run as a Databricks App and on a laptop ([goal [`G6`] One product, locally on DuckDB and as a Databricks App on Lakebase](../1_strategy/1_motivation.md#goals)). The sibling Reference Data Manager and Enterprise Architecture (EA) Repository both run Python with Dash, so their shell and idioms can be reused. A request to a Databricks App is cut off after 120 seconds.

## Options considered

| Option | Why not (or why) |
| ------ | ---------------- |
| Python throughout: Dash 4 with dash-mantine-components, AG Grid and Cytoscape for the interface, and Typer for the command line | **Chosen.** One language end to end, the siblings' shell and idioms, and a framework Databricks Apps runs as it is |
| Streamlit | It reruns the whole script on every interaction, and the Reference Data Manager moved away from it |
| A TypeScript app framework with the Python engine behind it | Two languages, and the engine hidden behind an interface of its own |
| FastAPI with a React front end | Two languages and a front-end build chain, for one small team |

## Decision

The hub is one Python package, `mdm`, with a Typer command line now and a Dash interface from initiative 3.

## Consequences

- Dependencies are locked with uv in `uv.lock`. The versions checked on 2026-09-27 live in the [technology services](../5_technology/1_technology-services.md#technology-services), with their sources.
- A request to a Databricks App is cut off after 120 seconds, so bulk work runs as jobs through the same command line (initiative 4).
- The interface libraries arrive in initiative 3, at the versions the siblings lock then, and every version is checked again at that point.
- The Databricks SDK is an optional extra, imported lazily and only where the platform needs it, so the local mode installs nothing from the platform.
