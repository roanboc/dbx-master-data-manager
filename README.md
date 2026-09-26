# Master Data Manager

The Master Data Manager (the hub) is a Databricks App, with a local mode on
DuckDB, in which data stewards and data owners turn source records into golden
records they can explain and reverse.

## Status

Nothing is built yet. Initiative 1, strategy discovery, writes the strategy
and the key business elements as documents. The application follows as three
initiatives (Foundations, Steward workbench, and Tune, govern and deploy), each
its own pull request. Release 1 proves Person and Organisation end to end.

## What it will do

- Match the source changes the integration platform writes into landing
  tables, with published, explainable rules.
- Give stewards one keyboard-driven inbox, where they decide alike tasks
  together after a forced sample.
- Commit approved, minimal change sets to one set of published tables. The
  platform and the integration platform carry them to listening systems; the
  hub never propagates.
- Give the same answers locally and on the platform.

Language-model assistance only suggests, and arrives in Release 2;
deterministic automation acts only under published rules.

Its siblings are the Reference Data Manager, which manages code lists, and the
Enterprise Architecture (EA) Repository.

## The model

What this project knows about itself lives in
[`architecture/`](./architecture/README.md). Start there: its front page says
which parts are modelled, which belong to somebody else, and which are still
missing.

Folders appear as they earn their place. A layer with nothing to say yet is a
row on that page, not an empty directory.

## How changes are made

A requirement is worked through the model and built directly from it, layer
by layer. The Requester's approval is the pull request merging — the agent
stops earlier only for a contradiction, an ambiguity, or something needing
authorisation. [`AGENTS.md`](./AGENTS.md) states the rule and the declared
modelling depth; the `align-change-through-layers` skill runs the process.

## Public safety

The repository is public, and names no institution, no master data management
vendor and no person. `scripts/scan_public_safe.py` runs in continuous
integration (CI) on every pull request to keep it so.

## Licence

Apache-2.0: see [LICENSE](./LICENSE) and [NOTICE](./NOTICE).

## Built with

[archreator](https://github.com/roanboc/archreator) — an enterprise
architecture method that lives in git as Markdown, with humans owning the
strategy and approving at merge, and AI agents doing the modelling and the
building in between.
