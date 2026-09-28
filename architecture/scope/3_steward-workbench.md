# Project Scope — Steward workbench

_[← Scope index](./README.md) · [Model home](../README.md)_

**ArchiMate viewpoint:** Implementation & Migration.
**Delivered as:** branch `claude/master-data-management-app-mhba1t`; story 3.1 in [pull request #3](https://github.com/roanboc/dbx-master-data-manager/pull/3); story 3.2 in [pull request #4](https://github.com/roanboc/dbx-master-data-manager/pull/4); stories 3.3 to 3.7 in later pull requests, each on its own.

Initiative 2, Foundations, merged on 27 September 2026 as
[pull request #2](https://github.com/roanboc/dbx-master-data-manager/pull/2).
It left arrival, matching and the commit path on both engines, and no
screen. The product owner asked on 27 September 2026 to start initiative 3
with the inbox screens ([the request](../reference/2026-09-27-workbench-request.md#the-request)).
The steward workbench puts every steward decision on screen: one inbox,
decided one task at a time or by pattern after a sample, with an undo tray
before commit. It adds maker and checker where
[rule [`RULE3`] Four eyes on what is hard to reverse](../2_business/5_domain-context-and-rules.md#business-rules)
asks, search and a record view, and the automated matcher's checkpoint. It is aligned with the
validated strategy, at Depth 1 (Application). It is delivered in seven
stories: story 3.1 is built with this scope document, and each later story in
a pull request of its own. Initiative 4 deploys the workbench.

**The automated matcher's checkpoint is owed.** The blind review and the
quality breaker that [decision 1](../decisions/1_automated-matcher-autonomy.md)
and [decision 3](../decisions/3_quality-breaker-autonomy.md) accepted do not
exist after story 3.1. [Story 3.2](./3_steward-workbench-stories/2_matcher-checkpoint.md)
builds them, and it must merge before any real data loads.

## EA alignment (assessed top-down before implementing)

| Layer | Impact |
| ----- | ------ |
| 0_business-design | Not used: an application project, `Out of scope` on the front door |
| 1_strategy | **No change** to any element. The workbench serves goals [`G1`] Trusted golden records, [`G2`] Only approved, reversible changes reach listening systems, [`G3`] Stewardship keeps pace with arrivals and [`G7`] Personal data protected by role ([motivation](../1_strategy/1_motivation.md#goals)), and the value stream stages [`VS1.3`] Resolve and [`VS1.4`] Commit ([value stream](../1_strategy/3_value-stream.md#value-stream)). Story 3.1 keeps these cells true: `Realized by` of [capability [`CAP3.1`] Matching and clustering](../1_strategy/2_capabilities-and-resources.md#capabilities), capability [`CAP4.1`] Survivorship and provenance, capability [`CAP4.2`] Identity and relationship management, capability [`CAP5.1`] Steward work and pattern decisions, capability [`CAP5.2`] Change approval and undo and capability [`CAP7.1`] Record search, view and history; `Held by` of resource [`RES5`] Steward match labels; `Realized by` of stages `VS1.3` and `VS1.4`; dashed edges made solid where the relationship is now true. The documents stay validated |
| 2_business | **New:** business process [`BPROC3`] Decide a steward task ([business processes](../2_business/3_business-processes.md#business-processes)), and 7 glossary terms. **Amended:** rule [`RULE2`] A steward decides routine changes alone names approving an update its source's policy held for a steward (Blueprint §5.5; the [stop check](#stop-check) gives the reading). **Kept true:** `Realized by` of [business service [`BSVC1`] Record lookup and history](../2_business/2_business-services.md#business-services), business service [`BSVC3`] Stewardship work and business service [`BSVC4`] Change approval; `Held in` of [business object [`BOBJ8`] Steward task](../2_business/4_business-objects.md#business-objects) and business object [`BOBJ9`] Change set; `Enforced by` of rule `RULE2`, [rule [`RULE3`] Four eyes on what is hard to reverse](../2_business/5_domain-context-and-rules.md#business-rules), rule [`RULE7`] Bulk decisions pass a forced sample and rule [`RULE11`] Least access by default. Rule `RULE2` also names "Not a match", keeping apart, keeping a record with no source record and rejecting a held update, which story 3.1 puts on screen. The profile of actor [`ACT6`] Automated matcher gains what a steward's label forbids it. Actor [`ACT7`] Work router stays Pending (story 3.4), and actor [`ACT8`] Quality breaker too (story 3.2). The actors, the business processes and the domain context are a draft catalogue (◐); the business services and business objects stay validated |
| 3_information | **New:** data objects [`DOBJ3.5`] Staged decision and [`DOBJ3.6`] Steward match label ([resolution work](../3_information/2_data-objects.md#resolution-work)); the [stewardship flows](../3_information/3_data-flows.md#stewardship-flows). Data object [`DOBJ3.2`] Steward task gains its due time, claim, snooze and escalation, and becomes Confidential. Data object [`DOBJ4.6`] Provenance names the strategy that decided each value. The data objects, flows and data architecture are a draft catalogue (◐); the data domains stay validated |
| 4_application | **New:** application services [`ASVC8`] Steward work, [`ASVC9`] Undo tray and [`ASVC10`] Record lookup ([application services](../4_application/1_application-services.md#application-services)); components [`ACMP15`] Stewardship services and [`ACMP16`] Record reader; component [`ACMP12`] Steward workbench exists ([application components](../4_application/2_application-components.md#application-components)); the [decision sequence](../4_application/3_application-collaborations.md#deciding-a-task); [the workbench](../4_application/4_solution-design.md#the-workbench) in the solution design. No interface contract changes. All four documents are a draft catalogue (◐) |
| 5_technology | **New:** artifact [`ART8`] Workbench screenshots ([deployment](../5_technology/2_deployment.md#from-build-to-runtime)). Technology service [`TSVC1`] Python runtime and packaging names the interface libraries and the browser checks' tools, checked on 2026-09-27. The declared capacity gains the workbench's figures, and the deployment names the browser checks' own CI job. The technology services and the deployment are a draft catalogue (◐). Hosting stays initiative 4 |
| Transition | Gap [`GAP3`] No steward workbench narrowed, not closed ([target state](../6_transition/1_target-state.md#gaps)); initiative 3 in progress on the [sequence](../6_transition/2_sequence.md#sequence) |
| Decisions | **New:** records [18](../decisions/18_workbench-shell-and-keys.md) to [22](../decisions/22_labels-bind-the-matcher.md), Proposed |
| Relationships | **New:** the rows of every new element, 56 of them, 1 Pending. Of the rows marked Pending before, 19 become true and 9 stay Pending, re-marked to the story or initiative that delivers them; 2 true rows name their stories |
| Reference | The [request of 27 September 2026](../reference/2026-09-27-workbench-request.md), filed as a facts-only summary; the Blueprint's `Derived into` names this scope document and decisions 18 to 22 |
| Code | **New:** the `mdm.ui` package and `app.py`; the stewardship and record-reading services; the store's workbench tables; `mdm ui` and `mdm tray flush`; the simulator's hard cases; the browser checks and their CI job; the screenshots |

## Plateaus

| Plateau | State |
| ------- | ----- |
| **Baseline** (before) | Arrival, matching and the commit path run from the command line on both engines; tasks are listed by `mdm task list`; no person decides anything on screen |
| **Story 3.1** (delivered here) | Locally, a steward opens the inbox and decides review, held-update, possible-duplicate and orphan tasks one at a time, with the explanation beside them. A decision can be undone within its window. Any golden or source record reads with its provenance, sources, timeline and relationships, masked by role |
| **Target** (initiative 3 delivered) | Every task kind is decided on screen, one at a time or by pattern after a forced sample. The automated matcher's checkpoint samples its decisions and can demote the automatic band. Ranks explain themselves, and breaches escalate. Search, a record as of a date, record actions with maker and checker, and record authoring are on screen |

This initiative closes nothing yet. It moves toward
[plateau [`PLAT2`] Release 1 serves Person and Organisation on the platform](../6_transition/1_target-state.md#plateaus),
and narrows [gap [`GAP3`] No steward workbench](../6_transition/1_target-state.md#gaps).

## Work packages and deliverables

One work package per story. Each names its deliverables and its outcome, and
links its story, which carries the detail.

### WP1 — Inbox, decide pane, undo tray and record view

- **Story:** [3.1](./3_steward-workbench-stories/1_inbox-decide-tray-and-record.md), in this pull request.
- **Deliverables:** the workbench's shell, run locally with `mdm ui`; the
  inbox with its decide pane; the undo tray with its flush, in the
  workbench's process and as `mdm tray flush`; the read-only record and
  source record views; the simulator's hard cases; the browser checks with
  their CI job; the screenshots; the model changes and decisions 18 to 22.
- **Outcome:** a steward decides one task at a time, undoes within the
  window, and reads any record, locally.

### WP2 — The automated matcher's checkpoint

- **Story:** [3.2](./3_steward-workbench-stories/2_matcher-checkpoint.md).
- **Deliverables:** the `quality_sample` task kind and the decide pane's
  blind mode; the quality breaker in `src/mdm/services/`, with
  `mdm breaker restore`; the data objects for the review sample and the
  breaker's state; the thresholds as settings.
- **Outcome:** blind review samples automated and steward decisions, and the
  quality breaker demotes the automatic band. The matcher may meet real data
  from here.

### WP3 — Signature batches

- **Story:** [3.3](./3_steward-workbench-stories/3_signature-batches.md).
- **Deliverables:** the signature group view (G) and its forced sample; the
  batch commit under one batch ID.
- **Outcome:** alike review tasks are decided together after a unanimous
  forced sample.

### WP4 — Work routing

- **Story:** [3.4](./3_steward-workbench-stories/4_work-routing.md).
- **Deliverables:** the explained rank and the escalation of a breach on
  record; assign and return; "Take the next 25".
- **Outcome:** ranks show their reasons, breaches escalate on record, and a
  coordinating steward routes work again.

### WP5 — Search and history

- **Story:** [3.5](./3_steward-workbench-stories/5_search-and-history.md).
- **Deliverables:** the command palette and its search tokens; the Timeline
  as of a date.
- **Outcome:** records, held arrivals and tasks are found by name or ID, and
  a record reads as of a date.

### WP6 — Record actions with maker and checker

- **Story:** [3.6](./3_steward-workbench-stories/6_record-actions-maker-and-checker.md).
- **Deliverables:** Detach on the Sources tab; the change-set proposal and
  the "Needs my approval" view; the cluster view.
- **Outcome:** a steward detaches a record through the undo tray. Merge,
  unmerge, retire and critical edits pass a checker who sees what the maker
  saw.

### WP7 — Authoring and the source record's actions

- **Story:** [3.7](./3_steward-workbench-stories/7_authoring-and-source-actions.md).
- **Deliverables:** the create and edit forms with their duplicate panel;
  pins and reinstatement; the source record's exception actions.
- **Outcome:** records are created, from a form or from a held new record,
  and edited with a live duplicate check. Values are pinned, and exceptions
  are run again, edited or ignored.

## Consolidation

| Catalogue | Candidates | Kept | What was merged, or left |
| --------- | ---------- | ---- | ------------------------ |
| Business processes | Blueprint §3 flows (a), (c) and (d), and create | 1 new, [`BPROC3`] Decide a steward task | One process for every decision on a task. A pattern decision (story 3.3) and a blind review (story 3.2) are the same process over a sample, and join it when built. Creating a record (story 3.7) stays a record action |
| Data objects | Blueprint §6: tray entry, claim, label, quality-assurance sample, breaker state, search token | 2 new: [`DOBJ3.5`] Staged decision, [`DOBJ3.6`] Steward match label | A claim, a snooze and an escalation are columns of [`DOBJ3.2`] Steward task. The strategy that decided a value is a field of [`DOBJ4.6`] Provenance. The review sample and the breaker's state wait for story 3.2, search tokens for story 3.5 |
| Application services | One per screen | 3: Steward work, Undo tray, Record lookup | The inbox and the decide pane are one service. The tray realizes change approval's undo, apart from its makers and checkers (story 3.6) |
| Application components | Blueprint §5.1: `mdm.ui` and the services | [`ACMP12`] exists; 2 new | The inbox, the decisions and the tray share their callers and tables, so they are one component. The record reader only reads and stands apart. The display helpers join [`ACMP14`] Service helpers |
| Decisions | Blueprint §7: the tray; the interface stack was decision 5 | Records 18 to 22 | The shell and keys; the tray; masking and reveal on screen; the actor and local web safety; labels that bind the matcher |
| Glossary | — | 7 terms | Claim, Close call, Escalate, Label, Service level, Snooze, Staged decision |

## Stop check

No stop blocks the build.

- **Contradiction:** none. One commitment already written down stays
  knowingly unmet: **the automated matcher's checkpoint**, the blind review
  and the quality breaker of
  [decision 1](../decisions/1_automated-matcher-autonomy.md) and
  [decision 3](../decisions/3_quality-breaker-autonomy.md).
  [Story 3.2](./3_steward-workbench-stories/2_matcher-checkpoint.md) builds it.
  **The matcher must not run on real data before its checkpoint exists.** No
  real data can load before initiative 4, because on a shared store every
  person is a consumer and arrival is refused.

  Nine points of the approved direction were corrected before building, so
  that none contradicts a principle, a rule or a validated row:
  1. Comments are not in initiative 3. The validated row of
     [capability [`CAP5.1`] Steward work and pattern decisions](../1_strategy/2_capabilities-and-resources.md#capabilities)
     places them in Release 2, initiative 5, and so does the Blueprint.
  2. The inbox reads keyed pages of 50 tasks with Previous and Next, not the
     grid's server-side model, which is not in its free edition and would
     read by offset. Every count stops at 999 and shows "999+", so no count
     reads a whole table ([decision 18](../decisions/18_workbench-shell-and-keys.md)).
  3. Single-key shortcuts can be turned off, as success criterion 2.1.4 of
     the Web Content Accessibility Guidelines (WCAG) 2.2 asks, and the key
     listener runs on the inbox only.
  4. No figure is faked. The record's health waits for initiative 4's
     scorecards, and the consumers in the impact line for its consumer
     registry.
  5. Arrival never waits for the undo tray. The commit that settles a staged
     decision checks, in its own transaction, that the record is still as the
     steward saw it ([decision 19](../decisions/19_undo-tray.md)).
  6. Claims name a steward, so data object
     [`DOBJ3.2`] Steward task becomes Confidential
     ([classification](../3_information/4_data-architecture.md#classification)).
  7. `mdm ui` listens on a loopback address unless a Databricks App runs it.
     It answers only its own host name, refuses posts from another site and
     cannot be framed ([decision 21](../decisions/21_workbench-actor.md)).
  8. Every decision from the tray writes its audit change set, even one that
     publishes nothing, such as "Not a match" or "Keep apart".
  9. The published relationships keep one row per source assertion, so the
     listener interface is unchanged. The record view groups them, as the
     [gap notes](#gap-notes) record.
- **Ambiguity:** none stops the build. "The inbox screens" is read as the
  Blueprint draws the Inbox: the queue, the decide pane and the tray, and the
  record the pane links to (adopted — the Inbox screen as the Blueprint draws
  it). Two places could read two ways. The approved Blueprint settles each,
  and this is the one reading the merge approves:
  1. Approving a held critical update is a steward's decision alone, under
     [rule [`RULE2`] A steward decides routine changes alone](../2_business/5_domain-context-and-rules.md#business-rules).
     It is not a critical edit under rule [`RULE3`] Four eyes on what is hard
     to reverse. The Blueprint's matrix (§5.5) holds such an update "for a
     steward", and lists a steward's critical edit apart. The steward
     releases a value the source asserted, and types nothing. `RULE2`'s
     statement now says so.
  2. A steward's "Not a match" in a coexistence domain may end in an
     automated create. The record goes back to arrival, which settles it
     under its source's policy as any distinct arrival, under rule
     [`RULE1`] Source traffic follows the source's policy
     ([decision 22](../decisions/22_labels-bind-the-matcher.md)). It is not a
     steward's create, which `RULE3` would give a checker. The automated
     change set names the steward's staged decision in its evidence.
- **Authorization:** nothing new. A1 to A4 of
  [scope document 2](./2_foundations.md#stop-check) stand as they were. The
  screenshots in the public repository show only the invented demo world,
  taken with the stub. The browser checks run on the public runners the
  repository already uses. No spend, no workspace access and no other team is
  committed.

## In scope / out of scope

| In scope (initiative 3) | Out of scope (gaps, candidate future work) |
| ----------------------- | ------------------------------------------- |
| Story 3.1: the shell, the inbox, the decide pane, the undo tray, and the read-only record and source record views | Comments, work balancing and automation grants: Release 2, initiative 5 ([capability `CAP5.1`](../1_strategy/2_capabilities-and-resources.md#capabilities)) |
| Story 3.2: blind review and the quality breaker | Ranking by dependent consumers and health: initiative 4, with the consumer registry and scorecards |
| Story 3.3: signature batches with forced samples | The record's health and consumers tabs, and consumers in the impact line: initiative 4 |
| Story 3.4: explained ranks, breach escalation, routing again | The audit screens, the operations board and the approval matrix screen: initiative 4 |
| Story 3.5: search, the command palette, a record as of a date | Mapping workspace groups to roles, deploying the app and scheduling `mdm tray flush`: initiative 4 |
| Story 3.6: detach, merge, unmerge, retire and critical edits, with a checker where rule `RULE3` asks | Label tuning from steward labels: Release 2, initiative 5 |
| Story 3.7: create, edit, pin, reinstate, create from a held new record, and the source record's actions | Case narratives from a language model in the decide pane: Release 2 |
| The model changes, and decisions 18 to 22 | |

## Gap notes

- **The automated matcher's checkpoint (story 3.2).**
  [Actor [`ACT6`] Automated matcher](../2_business/1_business-actors-and-roles.md#actors)
  settles arrivals with no sample and no breaker. Story 3.2 must merge before
  initiative 4 loads any real data. It is easy while every automated decision
  is already audited with its rule version. It is hard only in choosing the
  thresholds, which a data owner approves.
- **The workbench on the platform.** On a shared store every person resolves
  to a consumer until initiative 4 maps workspace groups to roles. The
  deployed workbench is read-only and masked until then
  ([decision 21](../decisions/21_workbench-actor.md)).
- **Flushing on the platform.** Locally the workbench's own process flushes
  the tray. On the platform a job runs `mdm tray flush`, which initiative 4
  schedules ([decision 19](../decisions/19_undo-tray.md)). Until then the
  command is refused on a shared store, because its caller resolves to a
  consumer there, as every person does.
- **Keep apart.** A keep-apart label binds arrival's later checks for a
  possible duplicate. Arrival raises one only between two golden records it
  has just created, so the label rarely has anything to bind yet.
- **Service levels are settings** until initiative 4 publishes the governance
  policy that holds them, with calendars.
- **Relationships per source assertion.** Scope document 2 left this to the
  record view, and it is decided: the published table keeps one row per
  assertion, so the listener interface is unchanged. The record view groups
  the rows by type and other end, and names every asserting source.
- **Ranks.** Story 3.1 orders the inbox by due time. Story 3.4 explains
  ranks, and initiative 4 adds dependent consumers and health.
- **Consumers in the impact line.** The impact line names cross-references,
  golden values, retired IDs and relationships. The consumers that rely on
  them join it with initiative 4's consumer registry.
- **Reversing a committed decision on screen.** Until story 3.6 puts detach
  on screen, a decision that has left the undo tray is reversed through the
  record lifecycle's service functions only.
- **Reveal reasons.** The workbench asks one of four reason codes. The
  command line's `--reason` stays free text, as initiative 2 left it.
- **Comments and work balancing** are not in initiative 3. The validated row
  of [capability [`CAP5.1`] Steward work and pattern decisions](../1_strategy/2_capabilities-and-resources.md#capabilities)
  places both in Release 2, initiative 5. Until then a steward writes no note
  on a task, and a coordinating steward routes work again only by hand
  (story 3.4).
- **Undoing a whole batch after commit** (the Blueprint's 30 days) is
  compensation. It is built with the batches of story 3.3, and shown on
  initiative 4's audit screen.

## Roadmap binding

This initiative narrows
[gap [`GAP3`] No steward workbench](../6_transition/1_target-state.md#gaps),
marked "Open — narrowed by initiative 3" in the target state. Its row in the
[sequence](../6_transition/2_sequence.md#sequence) reads "In progress — story
3.1 delivered". The last story's pull request marks the gap `Closed —
initiative 3` and the row Delivered.

Later story pull requests leave this document as merged. Each updates its
row in the scope index, the sequence row and gap `GAP3`, and ticks its own
story. A correction to this document is a new numbered scope document.
