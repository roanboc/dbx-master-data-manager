# Project Scope — Strategy discovery

_[← Scope index](./README.md) · [Model home](../README.md)_

**ArchiMate viewpoint:** Implementation & Migration.
**Delivered as:** branch `claude/master-data-management-app-mhba1t`, [pull request #1](https://github.com/roanboc/dbx-master-data-manager/pull/1).

On 26 September 2026 the product owner asked for a Master Data Manager (the
hub). It is a Databricks App, with a local mode on DuckDB, that manages master
data in Lakebase. A later answer the same day,
[answer 5](../reference/2026-09-26-request-and-answers.md#answers), fixed how
it connects. The repository held only the archreator 0.6.0 scaffold, with no
model and no code. So this initiative establishes the project and discovers the
strategy and the key business elements. It delivers documents only, and the hub
itself is still unbuilt. The hub follows as initiative 2 (Foundations), then 3
(Steward workbench) and 4 (Tune, govern and deploy), each with its own scope
document and pull request. Declared depth: 1, Application.

## EA alignment (assessed top-down before implementing)

| Layer | Impact |
| ----- | ------ |
| 0_business-design | Not used: an application project, `Out of scope` on the front door |
| 1_strategy | New: 8 stakeholders, 6 drivers, 3 assessments, 7 goals, 7 outcomes, 8 principles; 8 capability areas and 19 capabilities; 6 resources; one value stream with 6 stages, two of them outside the hub |
| 2_business | New key elements: 9 actors (4 automated, each with its autonomy, decision rights and escalation), 6 roles, 2 contracts (landing and listener, both External and not yet agreed), 9 business services, 11 business objects, 11 business rules (the approval matrix's defaults and the hard rules), and a glossary of about 90 terms. Business processes: not started, opens with initiative 2 |
| 3_information | Not started: initiative 2 (data objects; flows with the landing tables, the change feed and listening systems marked External; classification; retention) |
| 4_application | Not started: initiative 2 (services, components, and the landing and listener interface contracts) |
| 5_technology | Not started: initiative 2, which records the platform the product owner fixed as decisions. [Goal [`G6`] One product, locally on DuckDB and as a Databricks App on Lakebase](../1_strategy/1_motivation.md#goals) names that platform; nothing else about the stack is decided here |
| Transition | Not started: initiative 2 |
| Decisions | New: four records on the automated actors' autonomy (Proposed) |
| Relationships | New: `architecture/relationships.md`, 153 rows |
| Reference | New: `architecture/reference/README.md`, and a facts-only summary of the request and the answers; the Blueprint and the unit's two platform proposals are held privately |

## Plateaus

| Plateau | State |
| ------- | ----- |
| **Baseline** (before) | The archreator 0.6.0 scaffold as emitted, the GitHub assets and the public-safety scanner; no model and no code |
| **Target** (delivered) | A named project at Depth 1 on a public GitHub repository, whose strategy and key business elements a change can be judged against; still no code |

## Work packages and deliverables

### WP1 — Establish the project

- **Deliverables:** `AGENTS.md`, `README.md`, `architecture/README.md`,
  `architecture/scope/README.md` and this document, `.gitignore`,
  `.public-safe-allow.txt`, `LICENSE`, `NOTICE`, `scripts/README.md`,
  `.github/workflows/checks.yml`, `.github/pull_request_template.md`.
- **Outcome:** the project names itself and declares Depth 1, a public GitHub
  home and English. It states the public-safety rule, and continuous
  integration (CI) runs the three validators and the scan.

### WP2 — Strategy and key business elements

- **Deliverables:** `architecture/1_strategy/README.md`, `1_motivation.md`,
  `2_capabilities-and-resources.md`, `3_value-stream.md`;
  `architecture/2_business/README.md`, `1_business-actors-and-roles.md`,
  `2_business-services.md`, `4_business-objects.md`,
  `5_domain-context-and-rules.md`; `architecture/decisions/README.md` and
  records 1–4; `architecture/relationships.md`;
  `architecture/reference/README.md` and `2026-09-26-request-and-answers.md`.
- **Outcome:** a strategy, a capability map and the key business elements that
  every later initiative is aligned against.

## Consolidation

| Catalogue | Candidates | Kept | What was merged |
| --------- | ---------- | ---- | --------------- |
| Stakeholders | 13 | 8 | Business, coordinating and technical stewards into data stewards; listening-system teams and people who look records up into consumers; the platform owner and the team that runs the operational database into the data platform team; adopting organisations dropped, because neutrality is [principle [`P8`] Entity models are data; the product is neutral](../1_strategy/1_motivation.md#principles) |
| Drivers | 11 | 6 | The four drivers of the Data Management Body of Knowledge (DAMA-DMBOK2 Revised), namely data requirements, quality, integration cost and risk, into [driver [`DRV2`] The same party is recorded differently in many systems](../1_strategy/1_motivation.md#drivers)<br>Explainability into driver [`DRV6`] The market expects explainable, assisted stewardship<br>The local mode into goal [`G6`] One product, locally on DuckDB and as a Databricks App on Lakebase |
| Assessments | 4 | 3 | How changes leave the operational database folded into [driver [`DRV1`] Every commit reaches listening systems automatically](../1_strategy/1_motivation.md#drivers), once answer 5 decided it |
| Goals | 14 | 7 | Trusted, reconciled and explained golden records into [goal [`G1`] Trusted golden records](../1_strategy/1_motivation.md#goals)<br>Approved and reversible into goal [`G2`] Only approved, reversible changes reach listening systems<br>Steward productivity and pattern decisions into goal [`G3`] Stewardship keeps pace with arrivals<br>Local and platform parity with configuration into goal [`G6`] One product, locally on DuckDB and as a Databricks App on Lakebase, with neutrality left to principle [`P8`] Entity models are data; the product is neutral<br>Personal data kept as its own goal, [`G7`] Personal data protected by role<br>Lower integration cost left as a driver; parity with the incumbent hub left to the migration (initiative 6) |
| Outcomes | 9 | 7 | One outcome per goal, each a result a stakeholder can observe by a stated time; the declared volume into [outcome [`OUT1`] Person and Organisation mastered end to end at Release 1 volume](../1_strategy/1_motivation.md#outcomes); outcomes that repeated a principle's test were rewritten; the targets and dates of outcomes [`OUT2`] Mistakes are caught before listening systems see them, [`OUT3`] Stewards keep review work within its service levels and [`OUT4`] Every quality issue closes with a root cause and a remediation adopted, for the product owner to change |
| Principles | 8 | 8 | None merged; all reworded so none names a stack. [Principle [`P1`] The hub never propagates](../1_strategy/1_motivation.md#principles) says propagates rather than publishes, so it cannot be read against the published tables. Principle [`P5`] The inbox is home: decide by pattern, verify by sample uses the glossary's word, inbox |
| Capabilities | 14 groups, 53 rows in the Blueprint | 8 areas, 19 capabilities | Grouped by the processing steps of DAMA-DMBOK2 Revised ch. 10, with data quality after ch. 13. Entity model and domain style merged; master ID, record lifecycle and relationships merged; steward work and pattern decisions merged; quality rules and measurement merged; search, record view and history merged. Design parameters (windows, thresholds, comparators, gestures) left to governance policy defaults in initiative 2. Migration kept as [capability [`CAP2.3`] Migration from the incumbent hub](../1_strategy/2_capabilities-and-resources.md#capabilities), for initiative 6; external enrichment and a web interface for other systems, in the representational state transfer (REST) style, named as gaps |
| Resources | 7 | 6 | The sibling products' reusable code dropped: reuse is an initiative-2 design call |
| Value stream stages | 7 | 6 | Define folded into Govern |
| Actors | 6 roles' holders and 9 acting services in Blueprint §4 | 5 human, 4 automated | Arrival matching, pin expiry and approved re-evaluation into one automated matcher. The undo tray, throttle and duplicate check are mechanisms. The triage suggestion, weight estimator, signature grouping, impact line, listener preview and steward-hours forecast suggest or show and decide nothing, so they are capabilities, not actors. External agents (Release 3) get an actor when they exist |
| Roles | DMBOK2 Revised's steward types and the Blueprint's 6 | 6 | Checker into the coordinating steward (any steward other than the maker may check) |
| Business services | 13 screens and 19 innovative services | 9 | History into record lookup; reveal and erasure into access and privacy administration; the published tables and change feed into [business service [`BSVC7`] Published golden records and change feed](../2_business/2_business-services.md#business-services) |
| Business rules | 13 matrix rows, the never-automatic list and 6 others | 11 | The matrix's rows into five rules, the first three being its defaults:<br>[rule [`RULE1`] Source traffic follows the source's policy](../2_business/5_domain-context-and-rules.md#business-rules)<br>rule [`RULE2`] A steward decides routine changes alone<br>rule [`RULE3`] Four eyes on what is hard to reverse<br>rule [`RULE4`] Governance changes are proven before they are published<br>rule [`RULE5`] Destruction needs an owner and an administrator<br>The handling of data sent to a language model into rule [`RULE10`] Personal values are held apart and redacted by rule |

Capabilities stop at level 2, which is enough for an application.

## Stop check

No stop fired.

- **Contradiction:** none. Nothing contradicts a Principle or a recorded
  decision, and none existed before.
- **Ambiguity:** none. No two readings of the request build different
  documents. Answer 5 replaced the Blueprint's route for landing source
  changes and propagating committed ones, and the model follows answer 5. No
  element cites a superseded part for anything answer 5 decided. Answer 5
  supersedes these parts of the Blueprint:
  - the Interfaces row of §2, and steps 1–2 of flow (a) in §3;
  - in §5.4, the listener contract's grouping and partial-arrival rule, the
    per-commit row counts that served it, and the reasoning on sync-safe
    types;
  - in §5.6, the grants on the synced source schema and the sync
    prerequisites;
  - in §7, the sync and table-trigger checks on the initiative-2 spike list;
  - concerns 1, 2, 6 and 12 of §8, the last being the sync route for code
    lists;
  - option A of question 2.
- **Authorization:** none now. The Blueprint (§7), on which the product owner
  answered and asked to proceed, listed three adopted calls: publishing the
  model in a public repository, the Apache-2.0 licence, and
  `.claude/settings.json` registering a plugin marketplace hosted under a
  personal GitHub account for everyone who trusts the repository. Merging this
  pull request confirms them. Three Authorization stops lie ahead, named for
  initiative 2:
  - the landing and listener contracts commit the integration team and the
    data platform team;
  - compute at declared capacity is spend;
  - erasure of downstream copies and retention periods must be agreed before
    real Person data loads.

## In scope / out of scope

| In scope | Out of scope (gaps, candidate future work) |
| -------- | ------------------------------------------- |
| Establishing the project (WP1) | The hub itself: initiatives 2, 3 and 4 (Release 1) |
| The strategy layer | Layers 3–5 and the transition roadmap: initiative 2 |
| Key business elements, the glossary and the rules | Business processes: from initiative 2 |
| Four decisions on the automated actors' autonomy | Agreement of the landing and listener contracts: initiative 2 (Authorization) |
| The relationship catalogue, the reference index and the facts summary | Downstream erasure and retention periods: before real Person data loads |
| | Who writes the change feed: initiative 2 design |
| | A CI job for code (lint, tests on both stores): initiative 2, with the first code |
| | A pre-push hook running the scan with the private terms: initiative 2 |
| | `CONTRIBUTING.md`: when a second contributor arrives |
| | Release 2 (language-model services, hierarchies, trends): initiative 5; migration from the incumbent hub: initiative 6; Release 3: initiative 7 |
| | External enrichment and a REST interface: gaps |

## Gap notes

- **The hub.** Initiative 2 starts with documents: `3_information/`,
  `4_application/` with `5_interface-contracts.md`, `5_technology/`, decisions
  and `6_transition/`. Then come code and two spikes: throughput at the
  declared volume, and a platform check in the development workspace, which is
  spend.
- **Contracts.** The route is decided by answer 5. The integration platform
  writes the landing tables, and the platform's change notifier announces the
  change feed. Only the two teams' agreement and the workspace region remain
  open. The contracts are easy to write, and hard because they commit others.
- **Who writes the change feed.** Either the commit path writes it in the same
  transaction, or a database mechanism does. Initiative 2 decides. Principle
  [`P1`] The hub never propagates holds either way, because only the commit
  path changes the published tables.
- **Personal data.** Three things are open: who erases downstream copies, how
  long history is kept (Blueprint question 4), and who signs off both. Demo
  data stays invented until they are settled. The owner of retention becomes a
  stakeholder once the product owner names it. Prompts to a language model mask
  a person's name, which tightens Blueprint §5.6 to agree with rule [`RULE10`]
  Personal values are held apart and redacted by rule.
- **Automatic decisions.** The automated matcher's decisions commit without
  waiting in the undo tray, as in Blueprint §5.5; blind review, the quality
  breaker and detach are their checkpoint. Only a steward's decision waits out
  an undo window.
- **Governance roles.** A technical steward drafts model and source changes,
  and an administrator drafts operational settings; a data owner proposes both,
  as Blueprint §5.5 requires.
- **Pending relationships.** A relationship is marked Pending, and drawn
  dashed, whenever either end does not exist yet. That covers the outcomes, the
  value stream, the business objects and the rules, as well as the services
  and the automated actors.
- **Volume.** Release 1 is built for under a million golden records per
  entity, and the path to five million stays open. Raising the figure is the
  product owner's call.
- **Spend.** Compute beyond the smallest size is raised on the pull request
  that incurs it.
- **Code lists.** Initiative 2 settles how the hub reads the Reference Data
  Manager's code lists. The hub never writes them.
- **External enrichment.** It needs a licensed register and a decision on data
  leaving the platform.
- **REST interface.** The command line, masked read views and batch matching
  cover the need today. Opening one needs an authentication decision.
- **Migration from the incumbent hub.** Initiative 6, with a private mapping
  and a parallel run.
- **Business processes.** The processes the hub's flows need, unlevelled, come
  from initiative 2 (arrival and commit) and initiative 3 (the steward
  workbench).
- **Deviation from Blueprint §7.** The pre-push hook and the code CI job move
  from the establishing commit to initiative 2. A code job without code would
  fail, and the hook is tooling. `scripts/prose-denylist.json` stays as the
  scaffold shipped it, because no check failed on the subject's own words.
