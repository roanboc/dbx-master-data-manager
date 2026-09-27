# Target state

_[← Roadmap](./README.md) · [Model home](../README.md)_

**ArchiMate viewpoint:** Implementation & Migration: Plateau, Gap.

**Status:** ◐ Draft catalogue — written for initiative 2, Foundations; not yet validated.

## Plateaus

```mermaid
flowchart LR
  plat1[["≡ Documented baseline [PLAT1]"]]:::plateau
  plat2[["≡ Release 1 serves Person and Organisation on the platform [PLAT2]"]]:::plateau
  plat3[["≡ One assisted hub for parties [PLAT3]"]]:::plateau
  plat4[["≡ Assisted configuration [PLAT4]"]]:::plateau

  g1("◎ Trusted golden records [G1]"):::goal
  g2("◎ Only approved, reversible changes reach listening systems [G2]"):::goal
  g3("◎ Stewardship keeps pace with arrivals [G3]"):::goal
  g4("◎ Quality that is measured and acted on [G4]"):::goal
  g5("◎ Assistance that speeds stewardship without deciding [G5]"):::goal
  g6("◎ One product, locally on DuckDB and as a Databricks App on Lakebase [G6]"):::goal
  g7("◎ Personal data protected by role [G7]"):::goal

  plat1 -->|must be true before| plat2
  plat2 -->|must be true before| plat3
  plat3 -->|must be true before| plat4

  plat2 -->|serves| g1
  plat2 -->|serves| g2
  plat2 -->|serves| g3
  plat2 -->|serves| g4
  plat2 -->|serves| g6
  plat2 -->|serves| g7
  plat3 -->|serves| g1
  plat3 -->|serves| g4
  plat3 -->|serves| g5
  plat4 -->|serves| g5

  classDef plateau fill:#ffe8e8,stroke:#d99b9b,color:#333
  classDef goal fill:#c6aae9,stroke:#5e35b1,color:#333
```

Every goal of the [strategy](../1_strategy/1_motivation.md#goals) is served by at least one target. Release 1 carries six of the seven goals; [goal [`G5`] Assistance that speeds stewardship without deciding](../1_strategy/1_motivation.md#goals) waits for the language-model services of Release 2 and Release 3.

| ID | Plateau | Serves | Status | Reached by | Source | Notes |
| -- | ------- | ------ | ------ | ---------- | ------ | ----- |
| `PLAT1` | **Documented baseline** — the strategy and the key business elements are written and validated, and no code exists | — | Reached | [Initiative 1](../scope/1_strategy-discovery.md), strategy discovery | [Scope document 1](../scope/1_strategy-discovery.md) | |
| `PLAT2` | **Release 1 serves Person and Organisation on the platform** — sources land, arrive and commit on Lakebase; stewards decide in the workbench; rule changes are proven by a dry run; quality is reported and acted on; the landing and listener contracts are agreed; erasure is ready | [Goal [`G1`] Trusted golden records](../1_strategy/1_motivation.md#goals)<br>goal [`G2`] Only approved, reversible changes reach listening systems<br>goal [`G3`] Stewardship keeps pace with arrivals<br>goal [`G4`] Quality that is measured and acted on<br>goal [`G6`] One product, locally on DuckDB and as a Databricks App on Lakebase<br>goal [`G7`] Personal data protected by role | In flight | Initiatives 2, 3 and 4 ([sequence](./2_sequence.md#sequence)) | [Answers 1 and 4](../reference/2026-09-26-request-and-answers.md#answers); [Blueprint](../reference/README.md#founding-material) §7 | Initiative 2 closes `GAP1` |
| `PLAT3` | **One assisted hub for parties** — case narratives, questions answered with cited records, onboarding from a sample file, hierarchies, trends, label tuning and automation grants; the incumbent hub's IDs, history and open exceptions carried over, and the switch-over made | Goal [`G1`] Trusted golden records<br>goal [`G4`] Quality that is measured and acted on<br>goal [`G5`] Assistance that speeds stewardship without deciding | Planned | Initiatives 5 and 6 | [Answer 2](../reference/2026-09-26-request-and-answers.md#answers); [driver [`DRV3`] Master data management moves onto the data platform](../1_strategy/1_motivation.md#drivers); adopted — the switch-over from the incumbent hub as a destination | The switch-over as a destination awaits the product owner's confirmation |
| `PLAT4` | **Assisted configuration** — rules drafted with assistance, agent tools and semantic candidates, as Release 3 | Goal [`G5`] Assistance that speeds stewardship without deciding | Planned | Initiative 7 | Blueprint §4; [answer 2](../reference/2026-09-26-request-and-answers.md#answers) | |

## Gaps

```mermaid
flowchart LR
  plat1[["≡ Documented baseline [PLAT1]"]]:::plateau

  subgraph to2 ["Closed at Release 1 on the platform"]
    gap1(("⊘ No arrival, matching or commit path [GAP1]")):::closed
    gap2(("⊘ The landing and listener contracts are written, not agreed [GAP2]")):::gap
    gap3(("⊘ No steward workbench [GAP3]")):::gap
    gap4(("⊘ Rule changes are not proven before publication [GAP4]")):::gap
    gap5(("⊘ Quality is measured on arrival but not reported or acted on [GAP5]")):::gap
    gap6(("⊘ The hub is not deployed [GAP6]")):::gap
    gap7(("⊘ Erasure and retention are not settled [GAP7]")):::gap
    gap8(("⊘ Throughput is unproven on the platform [GAP8]")):::gap
    gap9(("⊘ Governed code lists are not read from the Reference Data Manager [GAP9]")):::gap
  end
  subgraph to3 ["Closed at one assisted hub"]
    gap10(("⊘ No language-model assistance [GAP10]")):::gap
    gap11(("⊘ No hierarchies, trends, label tuning or automation grants [GAP11]")):::gap
    gap12(("⊘ The incumbent hub still masters parties [GAP12]")):::gap
  end
  subgraph to4 ["Closed at assisted configuration"]
    gap13(("⊘ No assisted configuration, agent tools or semantic candidates [GAP13]")):::gap
  end

  plat2[["≡ Release 1 serves Person and Organisation on the platform [PLAT2]"]]:::plateau
  plat3[["≡ One assisted hub for parties [PLAT3]"]]:::plateau
  plat4[["≡ Assisted configuration [PLAT4]"]]:::plateau

  plat1 -->|differs by| gap1
  plat1 -->|differs by| gap2
  plat1 -->|differs by| gap3
  plat1 -->|differs by| gap4
  plat1 -->|differs by| gap5
  plat1 -->|differs by| gap6
  plat1 -->|differs by| gap7
  plat1 -->|differs by| gap8
  plat1 -->|differs by| gap9
  plat1 -->|differs by| gap10
  plat1 -->|differs by| gap11
  plat1 -->|differs by| gap12
  plat1 -->|differs by| gap13

  gap1 -->|closed, reaches| plat2
  gap2 -->|closed, reaches| plat2
  gap3 -->|closed, reaches| plat2
  gap4 -->|closed, reaches| plat2
  gap5 -->|closed, reaches| plat2
  gap6 -->|closed, reaches| plat2
  gap7 -->|closed, reaches| plat2
  gap8 -->|closed, reaches| plat2
  gap9 -->|closed, reaches| plat2
  gap10 -->|closed, reaches| plat3
  gap11 -->|closed, reaches| plat3
  gap12 -->|closed, reaches| plat3
  gap13 -->|closed, reaches| plat4

  classDef plateau fill:#ffe8e8,stroke:#d99b9b,color:#333
  classDef gap fill:#ffd6d6,stroke:#d99b9b,color:#333
  classDef closed fill:#ffffff,stroke:#d99b9b,color:#333
  style to2 fill:#fffafa,stroke:#d99b9b,color:#333
  style to3 fill:#fffafa,stroke:#d99b9b,color:#333
  style to4 fill:#fffafa,stroke:#d99b9b,color:#333
```

Each gap sits with the plateau that closes it. `GAP1` is drawn white because initiative 2 closed it. Most gaps close at `PLAT2`, because Release 1 is where the hub first meets real data, other teams and spend.

| ID | Gap | Baseline | Concerns | Closed by | Status | Source | Notes |
| -- | --- | -------- | -------- | --------- | ------ | ------ | ----- |
| `GAP1` | **No arrival, matching or commit path** | Absent at `PLAT1`: no code | [Business service [`BSVC2`] Arrival resolution](../2_business/2_business-services.md#business-services)<br>business service [`BSVC7`] Published golden records and change feed<br>[actor [`ACT6`] Automated matcher](../2_business/1_business-actors-and-roles.md#actors) | `PLAT2`, through [initiative 2](./2_sequence.md#sequence) | Closed — initiative 2 | [Scope document 1](../scope/1_strategy-discovery.md) | |
| `GAP2` | **The landing and listener contracts are written, not agreed** | Absent at `PLAT1`: no interface was written | [Contract [`CTR1`] Landing contract](../2_business/1_business-actors-and-roles.md#contracts)<br>contract [`CTR2`] Listener contract | `PLAT2`, through initiative 4 | Open — narrowed by initiative 2: both interfaces are written, as the hub's proposal to the two teams | [Answer 5](../reference/2026-09-26-request-and-answers.md#answers) | Closing it needs the integration team and the data platform team |
| `GAP3` | **No steward workbench** | Absent at `PLAT1`: stewards have no screens; three business services and the workbench component wait for initiative 3 | [Business service [`BSVC1`] Record lookup and history](../2_business/2_business-services.md#business-services)<br>business service [`BSVC3`] Stewardship work<br>business service [`BSVC4`] Change approval<br>[actor [`ACT7`] Work router](../2_business/1_business-actors-and-roles.md#actors)<br>actor [`ACT8`] Quality breaker<br>[application component [`ACMP12`] Steward workbench](../4_application/2_application-components.md#application-components) | `PLAT2`, through initiative 3 | Open | [Scope document 1](../scope/1_strategy-discovery.md); Blueprint §3 | |
| `GAP4` | **Rule changes are not proven before publication** | Absent at `PLAT1`: no model or rule set could be published | [Capability [`CAP3.2`] Rule tuning and impact preview](../1_strategy/2_capabilities-and-resources.md#capabilities)<br>[rule [`RULE4`] Governance changes are proven before they are published](../2_business/5_domain-context-and-rules.md#business-rules)<br>[value stream stage [`VS1.6`] Govern](../1_strategy/3_value-stream.md#value-stream) | `PLAT2`, through initiative 4 | Open — narrowed by initiative 2: a model or rule set is published only before any golden record, under a bootstrap authority; no dry run exists | Blueprint §5.5 | |
| `GAP5` | **Quality is measured on arrival but not reported or acted on** | Absent at `PLAT1`: no quality rule ran | [Business service [`BSVC6`] Quality and operations insight](../2_business/2_business-services.md#business-services)<br>[capability [`CAP6.2`] Issue and contract management](../1_strategy/2_capabilities-and-resources.md#capabilities)<br>[business object [`BOBJ11`] Quality issue](../2_business/4_business-objects.md#business-objects) | `PLAT2`, through initiative 4 | Open — narrowed by initiative 2: rule failures are stored per record; no scorecard or issue exists | Blueprint §2; DAMA-DMBOK2 Revised ch. 13 | |
| `GAP6` | **The hub is not deployed** | Absent at `PLAT1`: no code existed to deploy | [Resource [`RES2`] Platform hosting, store and compute](../1_strategy/2_capabilities-and-resources.md#resources)<br>[actor [`ACT4`] Hub administrators](../2_business/1_business-actors-and-roles.md#actors)<br>[technology service [`TSVC4`] App and job hosting](../5_technology/1_technology-services.md#technology-services)<br>[node [`NODE3`] Databricks workspace](../5_technology/1_technology-services.md#nodes)<br>node [`NODE4`] Lakebase project<br>[application component [`ACMP13`] Deployment bundle and jobs](../4_application/2_application-components.md#application-components)<br>[business service [`BSVC8`] Access and privacy administration](../2_business/2_business-services.md#business-services): workspace groups mapped to roles | `PLAT2`, through initiative 4 | Open — narrowed by initiative 2: it runs on a workstation and in continuous integration only, where every person on a shared store is a consumer | Blueprint §5.6 | Closing it is spend, which the product owner grants |
| `GAP7` | **Erasure and retention are not settled** | Absent at `PLAT1`: no store held a personal value | [Rule [`RULE5`] Destruction needs an owner and an administrator](../2_business/5_domain-context-and-rules.md#business-rules)<br>[outcome [`OUT7`] An erasure request is carried out and reported](../1_strategy/1_motivation.md#outcomes) | `PLAT2`, through initiative 4 | Open — narrowed by initiative 2: the vault and a guarded redaction exist; no erasure workflow or retention periods do | [Answer 1](../reference/2026-09-26-request-and-answers.md#answers) | Closing it needs an agreement on who erases downstream copies and how long history is kept |
| `GAP8` | **Throughput is unproven on the platform** | Absent at `PLAT1`: nothing was measured | [Outcome [`OUT1`] Person and Organisation mastered end to end at Release 1 volume](../1_strategy/1_motivation.md#outcomes)<br>[assessment [`ASM3`] Release 1 volumes stay under a million golden records per entity](../1_strategy/1_motivation.md#assessments) | `PLAT2`, through initiative 4 | Open — narrowed by initiative 2: measured on a workstation, on DuckDB and Postgres | [Answer 3](../reference/2026-09-26-request-and-answers.md#answers) | Closing it is spend |
| `GAP9` | **Governed code lists are not read from the Reference Data Manager** | Absent at `PLAT1`: no code list was read | [Resource [`RES4`] Governed code lists](../1_strategy/2_capabilities-and-resources.md#resources)<br>[data object [`DOBJ1.3`] Code-list copy](../3_information/2_data-objects.md#master-data-configuration) | `PLAT2`, through initiative 4 | Open — narrowed by initiative 2: code-list copies load from files | [Decision 15](../decisions/15_code-list-snapshots.md) | |
| `GAP10` | **No language-model assistance** | Absent at `PLAT1`: no assistance of any kind | [Business service [`BSVC9`] Assisted stewardship and configuration](../2_business/2_business-services.md#business-services)<br>[actor [`ACT9`] AI assistant](../2_business/1_business-actors-and-roles.md#actors)<br>[resource [`RES6`] Language-model endpoint](../1_strategy/2_capabilities-and-resources.md#resources)<br>[outcome [`OUT5`] Case narratives shorten review decisions](../1_strategy/1_motivation.md#outcomes) | `PLAT3`, through initiative 5 | Open — narrowed by initiative 2: the stub answers; the endpoint and the AI assistant wait for Release 2 | [Answer 2](../reference/2026-09-26-request-and-answers.md#answers) | |
| `GAP11` | **No hierarchies, trends, label tuning or automation grants** | Absent: the Release 2 parts of three capabilities wait for initiative 5, and no automation grant exists | [Capability [`CAP4.2`] Identity and relationship management](../1_strategy/2_capabilities-and-resources.md#capabilities)<br>capability [`CAP6.1`] Quality measurement and monitoring<br>capability [`CAP3.2`] Rule tuning and impact preview | `PLAT3`, through initiative 5 | Open | Blueprint §4; [answer 2](../reference/2026-09-26-request-and-answers.md#answers) | An automation grant widens the automated matcher's band, and needs a decision of its own |
| `GAP12` | **The incumbent hub still masters parties** | Absent at `PLAT1`, and still: the incumbent hub holds the parties' IDs and history | [Capability [`CAP2.3`] Migration from the incumbent hub](../1_strategy/2_capabilities-and-resources.md#capabilities) | `PLAT3`, through initiative 6 | Open | [Request](../reference/2026-09-26-request-and-answers.md#the-request); [driver [`DRV3`] Master data management moves onto the data platform](../1_strategy/1_motivation.md#drivers) | Closing it needs a private mapping and a parallel run |
| `GAP13` | **No assisted configuration, agent tools or semantic candidates** | Absent: assisted configuration and the semantic candidates of matching wait for Release 3 | [Capability [`CAP8.2`] Assisted configuration](../1_strategy/2_capabilities-and-resources.md#capabilities)<br>capability [`CAP3.1`] Matching and clustering | `PLAT4`, through initiative 7 | Open | Blueprint §4 | |
