# Master Data Manager

The Master Data Manager (the hub) is a Databricks App, with a local mode on
DuckDB, in which data stewards and data owners turn source records into golden
records they can explain and reverse.

## Status

Foundations, initiative 2, has built the hub's core. From the command line,
on DuckDB and on Postgres, it reads the source changes the integration
platform lands, matches them with explainable rules, and commits golden
records with an ordered change feed. It masks personal values, keeps their
history in a vault, and names the authority behind every commit. The same test
suite runs on both engines locally and in continuous integration (CI), on
invented data only.

Nothing is deployed yet, and there is no user interface. The steward
workbench follows in initiative 3, and tuning, governance and the deployment
to Databricks in initiative 4. Release 1 proves Person and Organisation end to
end. The [roadmap](./architecture/6_transition/2_sequence.md) orders the rest.

## Try it

With Python 3.11 and [uv](https://docs.astral.sh/uv/) installed:

```bash
make install        # the environment, from uv.lock
make demo           # a fresh local store: invented changes landed, arrived, committed, and the change feed read
uv run mdm --help   # every command
make test           # the suite across the cores, on DuckDB, and on Postgres when initdb and pg_ctl are installed
```

The local store is one DuckDB file, `.mdm/mdm.duckdb`, and every name in it is
invented.

## What it does

- Matches the source changes the integration platform writes into landing
  tables, with published, explainable rules.
- Commits minimal change sets to one set of published tables, under a named
  authority, with an ordered change feed. The integration platform carries
  them to listening systems; the hub never propagates.
- Gives the same answers on DuckDB and on Postgres.

## What it will do

- Give stewards one keyboard-driven inbox, where they decide alike tasks
  together after a forced sample.
- Run on the platform, on Lakebase, with the same answers as locally.

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
