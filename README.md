# \<project-name\>

<!--
  TEMPLATE — `establish-project` replaces this whole file with the project's
  own front door. Keep it short: what this is, who it's for, and where the
  model lives. The completion check looks for the `<placeholder>` marker
  below, so remove it once the real content is written.
-->

**\<placeholder> — one sentence saying what this project is and who it serves.**

## The model

What this project knows about itself lives in
[`architecture/`](./architecture/README.md). Start there: its front page says
which parts are modeled, which belong to somebody else, and which are still
missing.

Folders appear as they earn their place. A layer with nothing to say yet is a
row on that page, not an empty directory.

## How changes are made

A requirement is worked through the model and built directly from it, layer
by layer. The Requester's approval is the pull request merging — the agent
stops earlier only for a contradiction, an ambiguity, or something needing
authorization. [`AGENTS.md`](./AGENTS.md) states the rule and the declared
modeling depth; the `align-change-through-layers` skill runs the process.

## Built with

[archreator](https://github.com/roanboc/archreator) — an enterprise
architecture method that lives in git as markdown, with humans owning the
strategy and approving at merge, and AI agents doing the modeling and the
building in between.
