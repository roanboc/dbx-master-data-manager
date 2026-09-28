# Project Scope Documents

_[← Repository README](../../README.md) · [Model home](../README.md)_

One document per delivered (or in-flight) initiative, numbered
chronologically. While the
[enterprise architecture (EA) documents](../README.md) describe the
**current** state of the system, each scope document describes one
**change**: what plateau it started from, what it delivered, and what it
deliberately left out.

**ArchiMate viewpoint:** Implementation & Migration (Work Package,
Deliverable, Plateau, Gap).

## The EA-first change process

Every change in requirements follows the same order — the same order the EA
folders are numbered in:

1. **Align the EA first.** Walk the layers top-down and record what the
   change means for each: [1_strategy](../1_strategy/README.md) (does it
   serve an existing goal, or introduce a new driver?) →
   [2_business](../2_business/README.md) (new/changed services, processes,
   rules?) → 3_information (new/changed data objects, flows, storage?) →
   4_application (which services, components, ports change?) → 5_technology
   (any runtime, build, or hosting impact?). Update the affected EA
   documents in the same change. If the strategy layer is still template
   placeholders, or the change adds/modifies a stakeholder, driver, goal, or
   principle, the initiative becomes **strategy discovery** first — a
   docs-only, question-driven initiative built directly and opened as its
   own pull request (see the `discover-strategy` skill); implementation
   follows as a separate initiative. If the subject is an **organization**
   rather than an application, the walk starts one layer earlier, at
   0_business-design — the value proposition and business model canvases
   (see the `discover-business-model` skill) before layers 1–2 are derived
   from them.
2. **Document the scope.** Add the next-numbered file to this folder
   describing plateaus, work packages, in/out of scope and gaps — before
   implementation starts, refined as it proceeds.
3. **Check for a stop.** Before building, check whether the change
   contradicts a Principle or a decision already written down, reads two
   ways, or would commit the Requester to something they have not agreed —
   see the `align-change-through-layers` skill § Where this stops, which also
   says how a stop is named — in the conversation, or a reply on the pull
   request for a Requester who doesn't work in a terminal. If none fire,
   build directly.
4. **Implement, and open the pull request.** The Requester's merge is the
   approval; nothing before it is. Keep the scope document and EA docs in
   sync with what is actually delivered.

Agent guidance for this process lives in the `align-change-through-layers`,
`discover-strategy`, and `write-scope-document` skills; PR descriptions follow
`.github/pull_request_template.md` (see the `write-pr-description` skill) and
must cover the whole branch.

If a work package is too large or long-running to implement in one sitting,
shard it into self-contained story files instead of leaving it as one
inline task list — see the `shard-stories` skill.

For a single consequential call smaller than a full initiative — most
often why an AI actor's autonomy level or decision rights were set the way
they were — see [the decisions index](../decisions/README.md) (optional) and
the `record-decision` skill.

Scope documents accumulate, and after a run of initiatives the EA can be
accurate line by line and still not read as a description of *today* —
shipped work still marked "Pending", elements replaced but never retired. The
`restate-current-state` skill compacts that, as its own initiative. It
changes the current-state documents only: **a merged scope document is never
rewritten**.

## Initiatives

| #   | Scope document | Delivered as | Summary |
| --- | --------------- | ------------ | ------- |
| 1 | [Strategy discovery](./1_strategy-discovery.md) | [Pull request #1](https://github.com/roanboc/dbx-master-data-manager/pull/1), merged 2026-09-27 | Establishes the project, then writes the strategy layer and the key business elements; documents only |
| 2 | [Foundations](./2_foundations.md) | [Pull request #2](https://github.com/roanboc/dbx-master-data-manager/pull/2), merged 2026-09-27 | The information, application and technology layers, the roadmap, and the first code: the store on both engines, the matching engine, arrival, the commit path and change feed, and the command line |
| 3 | [Steward workbench](./3_steward-workbench.md) | Story 3.1 in [pull request #3](https://github.com/roanboc/dbx-master-data-manager/pull/3), merged 2026-09-28; story 3.2 in [pull request #4](https://github.com/roanboc/dbx-master-data-manager/pull/4), merged 2026-09-28; stories 3.3 to 3.7 to follow | The inbox, the decide pane, the undo tray and the record view first; then the matcher's checkpoint, batches, routing, search, record actions with maker and checker, and authoring, in seven stories |
