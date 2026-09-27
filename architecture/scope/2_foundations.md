# Project Scope — Foundations

_[← Scope index](./README.md) · [Model home](../README.md)_

**ArchiMate viewpoint:** Implementation & Migration.
**Delivered as:** branch `claude/master-data-management-app-mhba1t`; pull request to be opened.

Initiative 1, strategy discovery, merged on 27 September 2026 as
[pull request #1](https://github.com/roanboc/dbx-master-data-manager/pull/1).
It left a validated strategy and business layer, and no code. Foundations
builds the hub's first code and the three lower layers of its model, and
states the roadmap. It is aligned with the validated strategy, at Depth 1
(Application), and adds, removes or re-relates no stakeholder, driver, goal,
outcome, principle, capability or stage. It delivers documents and code in one
pull request. It builds no user interface, which is initiative 3, and deploys
nothing to the platform, which is initiative 4.

## EA alignment (assessed top-down before implementing)

| Layer | Impact |
| ----- | ------ |
| 0_business-design | Not used: an application project, `Out of scope` on the front door |
| 1_strategy | **No change** to any element. Foundations serves goals [`G1`] Trusted golden records, [`G2`] Only approved, reversible changes reach listening systems, [`G6`] One product, locally on DuckDB and as a Databricks App on Lakebase and [`G7`] Personal data protected by role ([motivation](../1_strategy/1_motivation.md#goals)), and the stages [`VS1.2`] Arrive and [`VS1.4`] Commit, with the automatic part of [`VS1.3`] Resolve ([value stream](../1_strategy/3_value-stream.md#value-stream)). Cells kept true: `Realized by` of 14 capabilities, of value stream [`VS1`] From source record to trusted golden record, and of its stages [`VS1.2`] Arrive, [`VS1.3`] Resolve, [`VS1.4`] Commit and [`VS1.6`] Govern; `Checked by` of outcomes [`OUT1`] Person and Organisation mastered end to end at Release 1 volume and [`OUT7`] An erasure request is carried out and reported; dashed edges made solid where the relationship is now true; each shortened Pending marker written in full. The documents stay validated |
| 2_business | **New:** 2 business processes, [`BPROC1`] Resolve an arrival and [`BPROC2`] Commit a change set ([business processes](../2_business/3_business-processes.md#business-processes)), and 8 glossary terms. **Kept true:** `Realized by` of seven [business services](../2_business/2_business-services.md#business-services), every one but [`BSVC1`] Record lookup and history and [`BSVC6`] Quality and operations insight; `Held in` of ten [business objects](../2_business/4_business-objects.md#business-objects), every one but [`BOBJ11`] Quality issue; `Enforced by` of ten [business rules](../2_business/5_domain-context-and-rules.md#business-rules), every one but [`RULE2`] A steward decides routine changes alone and [`RULE7`] Bulk decisions pass a forced sample; `State` of actors [`ACT6`] Automated matcher and [`ACT9`] AI assistant; `Realized by` of contracts [`CTR1`] Landing contract and [`CTR2`] Listener contract. The business processes and the domain context, which gained the 8 glossary terms, are a draft catalogue (◐); the other business documents stay validated (●) |
| 3_information | **New layer** ([information layer](../3_information/README.md)): 5 data domains and 21 data objects; the arrival and commit flows, with the landing tables, the change notifier and the listening systems marked External; the schema groups and portable types; classification, with personal data by masking class and the fields that never hold a personal value; retention, pending agreement |
| 4_application | **New layer** ([application layer](../4_application/README.md)): 7 application services and 14 application components, 2 of them Pending; the arrival and commit collaborations; the solution design; the landing and listener interfaces |
| 5_technology | **New layer** ([technology layer](../5_technology/README.md)): 6 technology services, 1 Pending and 1 External; 4 nodes, 2 Pending; 7 artifacts; stack choices dated 2026-09-27, with their alternatives and sources; the checks on every change; the declared capacity and the measured throughput |
| Transition | **New** ([roadmap](../6_transition/README.md)): 4 plateaus, 1 of them the baseline; 13 gaps, 1 closed by this initiative; the sequence of initiatives 2 to 7. The baseline holds for planning: the strategy and business layers are filled and validated, and nothing existed below them because no code did, so each gap is measured from plateau [`PLAT1`] Documented baseline, within Depth 1 |
| Decisions | **New:** records 5 to 17, Proposed. The index rows of records 1 to 4 read Accepted, as the records themselves already did |
| Relationships | **New:** the rows of every new element. Of the rows marked Pending before, 43 become true and 69 stay Pending, 22 of them re-marked to the initiative that now delivers them |
| Reference | The `Derived into` cells of answers 1–4, answer 5 and the Blueprint name the declared capacity, the two interfaces, the data objects and decisions 5 to 17 |
| Code | **New:** the `mdm` package, its command line, the starter models, a test suite that runs on both engines, the CI job "Lint and tests", the pre-push hook, the `Makefile` and the throughput spike |

## Plateaus

| Plateau | State |
| ------- | ----- |
| **Baseline** (before) | A validated strategy and business layer; no information, application or technology layer, no roadmap, and no code |
| **Target** (delivered) | From the command line, on DuckDB and on Postgres, the hub reads landing rows, resolves them automatically under published rules and source policies, and commits golden records with a gap-free change feed, on invented data. The landing and listener interfaces are written, and the roadmap is stated |

This initiative closes
[gap [`GAP1`] No arrival, matching or commit path](../6_transition/1_target-state.md#gaps),
and moves toward
[plateau [`PLAT2`] Release 1 serves Person and Organisation on the platform](../6_transition/1_target-state.md#plateaus).

## Work packages and deliverables

### WP1 — Information and application model

- **Deliverables:** `architecture/2_business/3_business-processes.md`;
  `architecture/3_information/README.md`, `1_data-domains.md`,
  `2_data-objects.md`, `3_data-flows.md`, `4_data-architecture.md`;
  `architecture/4_application/README.md`, `1_application-services.md`,
  `2_application-components.md`, `3_application-collaborations.md`,
  `4_solution-design.md`, `5_interface-contracts.md`; the kept-true cells in
  `architecture/1_strategy/` and `architecture/2_business/`.
- **Outcome:** every data object names the table that holds it, every
  component names its path, and the two interfaces say exactly what the
  integration platform and the change notifier can rely on.

### WP2 — Technology, decisions and roadmap

- **Deliverables:** `architecture/5_technology/README.md`,
  `1_technology-services.md`, `2_deployment.md`,
  `3_capacity-and-throughput.md`; `architecture/decisions/5_python-dash-and-typer.md`
  to `17_masked-prompts-and-the-stub.md` and the index;
  `architecture/6_transition/README.md`, `1_target-state.md`,
  `2_sequence.md`; `architecture/relationships.md`; `architecture/README.md`;
  `architecture/reference/README.md`; this document and its index row;
  `AGENTS.md`; `README.md`.
- **Outcome:** the stack is chosen and dated, each consequential call has a
  record, and the roadmap orders the work to Release 3.

### WP3 — Project skeleton

- **Deliverables:** `pyproject.toml`, `uv.lock`, `Makefile`,
  `.github/workflows/checks.yml` (the job "Lint and tests"),
  `.github/pull_request_template.md`, `scripts/hooks/pre-push`,
  `scripts/README.md`, `.gitignore`, `src/mdm/__init__.py`,
  `src/mdm/config.py`, `src/mdm/models/`, `tests/conftest.py`,
  `tests/postgres_server.py`.
- **Outcome:** `uv sync` builds the environment, and CI lints and tests every
  change on both engines.

### WP4 — Store on two engines

- **Deliverables:** `src/mdm/backend/`, `tests/test_backend_*.py`.
- **Outcome:** every SQL statement is written once and runs on DuckDB and on
  Postgres, with the write guard, the commit-order lock, the run lease and the
  Lakebase credentials.

### WP5 — Matching engine

- **Deliverables:** `src/mdm/engine/`, `tests/test_engine_*.py`.
- **Outcome:** standardisation, blocking, comparison, scoring with its
  explanation, weight estimation, clustering, survivorship and quality checks,
  in pure Python.

### WP6 — Services

- **Deliverables:** `src/mdm/services/`, `src/mdm/capacity.py`,
  `src/mdm/agent/`, `tests/test_services_*.py`, `tests/helpers.py`,
  `tests/test_agent.py`.
- **Outcome:** arrival from the landing tables, the commit path with its
  change feed, the feed reader, record lifecycle, authority, masking and the
  vault, the registry, estimation, profiling, and the assistant's stub.

### WP7 — Command line, starter models and demonstration

- **Deliverables:** `src/mdm/cli.py`, `src/mdm/demo/`, `models/person.yaml`,
  `models/organisation.yaml`, `models/codelists/`, `tests/test_cli*.py`,
  `tests/test_demo.py`.
- **Outcome:** `make demo` lands invented Person and Organisation changes,
  arrives them, commits them and reads the change feed, on a laptop.

### WP8 — Throughput spike

- **Deliverables:** `tools/spike_throughput.py`; its results in
  [measured throughput](../5_technology/3_capacity-and-throughput.md#measured-throughput).
- **Outcome:** a measured rate on both engines, and an extrapolation to the
  declared volume.

## Consolidation

| Catalogue | Candidates | Kept | What was merged, or left |
| --------- | ---------- | ---- | ------------------------ |
| Business processes | The Blueprint's flows | 2 | Arrival and bulk loading are one process, [`BPROC1`] Resolve an arrival, in two batch sizes. The steward's processes wait for initiative 3 |
| Data objects | The Blueprint's data model, §6: 28 objects | 5 data domains, 21 data objects | Search tokens, value frequencies, simulations, quality-assurance samples, the breaker's state, scorecards, labels, issues, contracts, consumers and role grants left to initiatives 3 and 4 |
| Application services | One per command group of the command line | 7 | Grouped by the business service each realizes |
| Application components | The Blueprint's 7 layers, §5.1 | 14, 2 of them Pending | The user interface and the deployment bundle are the two Pending; the helpers every service shares are a component of their own, so the entry points serve nothing below them |
| Technology services | 7 | 6 | The language-model endpoint stays [resource [`RES6`] Language-model endpoint](../1_strategy/2_capabilities-and-resources.md#resources), rather than a technology service |
| Decisions | The Blueprint's twelve decision topics, §7 | Records 5 to 13, 16 and 17 | The interface stack, one store, the schema groups, commit serialisation with the listener interface, the landing interface, the scorer, JSON for groups, the vault, identity as authority with personas, source policies, and AI with personal data. Records 14 (capacity) and 15 (code lists) were added, which the Blueprint did not list. The approval matrix and the undo tray are left to initiative 3, and the mapping of groups to roles and the platform grants to initiative 4 |
| Plateaus | — | 4 | One baseline, and three targets for a two-year horizon |
| Gaps | The Pending rows and the gap notes of scope document 1 | 13 | One gap per missing state, grouped by the plateau that closes it |

## Stop check

No stop blocks the build.

- **Contradiction:** two commitments already written down are knowingly not
  met yet. Neither stops the build, for the same reason: on a shared store
  every person resolves to the consumer role until initiative 4 maps
  workspace groups to roles (`src/mdm/services/authority.py`), so arrival and
  publishing are refused there, and no real data can reach the hub before
  then.
  - The automated matcher's checkpoint, the quality breaker and the blind
    review that [decision 1](../decisions/1_automated-matcher-autonomy.md)
    accepted, does not exist yet. Initiative 3 builds it, before initiative 4
    opens the platform ([gap note](#gap-notes)).
  - [Rule [`RULE4`] Governance changes are proven before they are published](../2_business/5_domain-context-and-rules.md#business-rules)
    asks for a dry run and a second data owner. A model or rule set is
    published instead only while its entity holds no golden record, under a
    flagged bootstrap authority; the dry run arrives with initiative 4.

  Twelve points of the initial design were corrected before merging, so
  that none contradicts a principle, a rule or a validated row:
  1. On the platform the integration platform's role, not the hub's, creates
     and owns the landing table, as [rule [`RULE9`] The hub never writes a source record](../2_business/5_domain-context-and-rules.md#business-rules)
     and [contract [`CTR1`] Landing contract](../2_business/1_business-actors-and-roles.md#contracts) require.
     The hub publishes its definition, and creates it only in the local mode.
  2. The published commit log names a role, never a person; the person is in
     the audit record.
  3. The assistant's plumbing and stub are built now, as validated rows for
     [capability [`CAP8.1`] Assisted stewardship](../1_strategy/2_capabilities-and-resources.md#capabilities),
     [actor [`ACT9`] AI assistant](../2_business/1_business-actors-and-roles.md#actors) and
     [business service [`BSVC9`] Assisted stewardship and configuration](../2_business/2_business-services.md#business-services) promise.
  4. Quality rules on arrival, profiling, the code-list copies, masking with
     logged reveals, the record lifecycle functions and a read-only task list
     are built, because validated rows promise them for this initiative.
  5. The hub never calls `pg_notify`, under
     [principle [`P1`] The hub never propagates](../1_strategy/1_motivation.md#principles);
     the change notifier polls the commit log.
  6. DuckDB needs `pytz` to return times with a time zone, so it is a runtime
     dependency.
  7. The landing reader keeps a high-water mark and gap ranges, because an
     overlap window either loses a slow commit or stalls
     ([decision 9](../decisions/9_landing-table-and-watermark.md)).
  8. Personas, the simulator and the reset are allowed by the store, not by
     where the process runs, because a laptop pointed at Lakebase carries no
     platform variables ([decision 13](../decisions/13_authority-and-personas.md)).
  9. A change kind `remapped` tells consumers when a retired ID resolves to a
     different survivor.
  10. Weights are estimated per blocking pass, with u for exact levels taken
      from value frequencies, because one estimation over all blocked pairs is
      biased ([decision 10](../decisions/10_explainable-scorer-and-weight-estimation.md)).
  11. An arrival naming an active master ID follows its source's `master_id`
      policy, held for a steward by default, because rule
      [`RULE1`] Source traffic follows the source's policy makes only a
      retired ID automatic. A data owner who sets it to `auto` changes one of
      the approval matrix's defaults, which that rule leaves to the data
      owners, and even then no cannot-link rule is passed
      ([decision 16](../decisions/16_source-policies-and-clauses.md)).
  12. The change notifier's role reads `mdm_core.commit_log` alone, and the
      match test is refused to a consumer and logged, as rule
      [`RULE11`] Least access by default asks.
- **Ambiguity:** none. No two readings of the request build different
  things.
- **Authorization:** four items, none of which blocks building. Each stays
  Pending, and is named again on the pull request:
  - **A1 — The two interfaces commit other teams.** The landing and listener
    interfaces commit the integration team and the data platform team.
    Merging approves them as the hub's side and as its proposal. The two
    teams' agreement, and the workspace region, are needed before go-live, in
    initiative 4.
  - **A2 — Spend and workspace access for the platform check.** Running the
    throughput spike on a Databricks development workspace, with Lakebase
    compute, an app and jobs, is spend and access the product owner has not
    granted. It is raised on initiative 4's pull request.
  - **A3 — Downstream erasure and retention.** Somebody has to decide who
    erases downstream copies and how long history is kept, and sign both off,
    before real Person data loads. Demo data stays invented, and nothing is
    deleted meanwhile.
  - **A4 — The roadmap's destination and order.** The
    [roadmap](../6_transition/README.md) names three target plateaus and the
    order of initiatives 2 to 7. The order restates answer 4 and scope
    document 1. The plateau "One assisted hub for parties", with the
    switch-over from the incumbent hub, is stated as a destination for the
    first time. **Merging approves the destination and the order, not the
    work**: each initiative on the sequence still runs through the layers and
    its own stop check. External enrichment and a web interface for other
    systems are deliberately not on it, and neither is propagation by the hub
    ([outside the roadmap](../6_transition/2_sequence.md#outside-the-roadmap)).

## In scope / out of scope

| In scope | Out of scope (gaps, candidate future work) |
| -------- | ------------------------------------------- |
| The store on DuckDB and Postgres, with its write guard | The user interface: initiative 3 |
| The matching engine, weight estimation, profiling and the match test | Maker and checker, and the undo tray, on screen: initiative 3 |
| Arrival from the landing tables, and bulk loading | The quality breaker and the blind review: initiative 3 |
| The commit path and the change feed | The dry run and the re-evaluation of existing records: initiative 4 |
| The vault, masking and logged reveals | Mapping workspace groups to roles: initiative 4 |
| Authority on every commit, and local personas | Deployment, the jobs and the live Lakebase run: initiative 4, spend (A2) |
| The record lifecycle functions | The erasure workflow and retention periods: initiative 4, needs agreement (A3) |
| The command line, the starter models and the demonstration | The two teams' agreement to the interfaces: initiative 4 (A1) |
| The model registry, the code-list copies, quality rules on arrival, profiling and weight estimation | Publishing over existing golden records, after a dry run: initiative 4 |
| The assistant's plumbing, masked prompts and the stub | The language-model endpoint and case narratives from it: initiative 5 |
| The throughput spike | The reader of the Reference Data Manager's tables: initiative 4 |
| The CI job and the pre-push hook | Term-frequency adjustment in scoring: initiative 4 |
| Layers 3 to 5, the roadmap and decisions 5 to 17 | Language-model services and hierarchies: initiative 5; migration from the incumbent hub: initiative 6 |

## Gap notes

- **The two interfaces (A1).** Closing the gap takes the integration team's
  and the data platform team's agreement to the
  [landing and listener interfaces](../4_application/5_interface-contracts.md),
  and the workspace region. The interfaces are easy to change before
  anything depends on them, and hard to change after.
- **The platform check (A2).** It needs a development workspace, Lakebase
  compute, the app and the jobs, all of them spend. Until then the throughput
  is measured on a workstation only.
- **Erasure and retention (A3).** The vault and a guarded redaction exist. An
  erasure workflow and retention periods need the product owner's agreement
  on who erases downstream copies and how long history is kept.
- **The roadmap (A4).** A later word from the product owner can move a
  plateau or reorder the initiatives; the sequence depends on dependencies,
  not on dates.
- **The automated matcher's checkpoint.** [Actor [`ACT6`] Automated matcher](../2_business/1_business-actors-and-roles.md#actors)
  settles arrivals today. Its checkpoint, the quality breaker and the blind
  review of [decision 1](../decisions/1_automated-matcher-autonomy.md),
  comes with initiative 3. The matcher must not run on real data before its
  checkpoint exists, and no real data loads before initiative 4.
- **Landing ownership.** The integration platform's role creates and owns the
  landing table on the platform, from `mdm ddl --group landing`, and grants
  the hub's role `USAGE` and `SELECT`. The hub creates the table itself only in
  the local mode.
- **The throughput extrapolation.** The daily figure holds on a
  workstation; the volume figure is a projection, not a measurement
  ([measured throughput](../5_technology/3_capacity-and-throughput.md#measured-throughput)).
  The run at the declared volume on the operational database stays in the
  check of [outcome [`OUT1`] Person and Organisation mastered end to end at Release 1 volume](../1_strategy/1_motivation.md#outcomes),
  for initiative 4, and the platform run is spend (A2).
- **The starter person weights.** The demo world draws names from a Zipf
  distribution, so two invented persons share a given or a family name about
  one time in twenty. The first starter weights assumed far rarer agreement,
  and at 100,000 records linked persons with a precision of 0.717. The given
  name, family name and birth date weights were set again from 300,000 random
  pairs of the demo world, which gives 0.978. Real sources need weights of
  their own, which `mdm estimate` drafts for a data owner to publish.
- **Relationships per source assertion.** Two sources that assert the same
  employer give two relationship rows, each with its origin. Whether consumers
  need one survived relationship is decided in initiative 3, with the record
  view.
- **The exact landing method on Postgres.** Reading below the oldest running
  transaction is kept as the upgrade, not built
  ([decision 9](../decisions/9_landing-table-and-watermark.md)). It is built
  if a reconciliation ever finds a late row.
- **Code lists.** The hub validates against its own versioned copies, loaded
  from files today ([decision 15](../decisions/15_code-list-snapshots.md)).
  The reader of the Reference Data Manager's tables comes with initiative 4.

## Roadmap binding

This initiative closes gap [`GAP1`] No arrival, matching or commit path,
marked `Closed — initiative 2` in the
[target state](../6_transition/1_target-state.md#gaps), and moves plateau
[`PLAT2`] Release 1 serves Person and Organisation on the platform to In
flight. Its row in the
[sequence](../6_transition/2_sequence.md#sequence) reads Delivered.
