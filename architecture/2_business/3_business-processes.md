# Business processes

_[← Business layer](./README.md) · [Model home](../README.md)_

**ArchiMate viewpoint:** Business layer: Business Process, with the actor and roles assigned to it, the business services and value stream stages it realizes, and the business objects it handles.

**Status:** ● Validated, 2026-09-28.

## Business processes

```mermaid
flowchart LR
  act6(["⚇ Automated matcher (AI) [ACT6]"]):::actorAI
  role4["⚉ Technical steward [ROLE4]"]:::role
  role1["⚉ Data owner [ROLE1]"]:::role
  role2["⚉ Data steward [ROLE2]"]:::role
  role3["⚉ Coordinating steward [ROLE3]"]:::role
  bproc1{{"⚙ Resolve an arrival [BPROC1]"}}:::process
  bproc2{{"⚙ Commit a change set [BPROC2]"}}:::process
  bproc3{{"⚙ Decide a steward task [BPROC3]"}}:::process
  bsvc2("⬭ Arrival resolution [BSVC2]"):::service
  bsvc3("⬭ Stewardship work [BSVC3]"):::service
  bsvc4("⬭ Change approval [BSVC4]"):::service
  bsvc7("⬭ Published golden records and change feed [BSVC7]"):::service
  vs1_2[["⇉ Arrive [VS1.2]"]]:::stage
  vs1_3[["⇉ Resolve [VS1.3]"]]:::stage
  vs1_4[["⇉ Commit [VS1.4]"]]:::stage

  act6 -->|assigned to| bproc1
  role4 -->|accountable for| bproc1
  role1 -->|accountable for| bproc2
  role2 -->|assigned to| bproc3
  role3 -->|accountable for| bproc3
  bproc1 -->|triggers| bproc2
  bproc3 -->|triggers| bproc2
  bproc3 -->|triggers| bproc1
  bproc1 -->|realizes| bsvc2
  bproc2 -->|realizes| bsvc7
  bproc3 -->|realizes| bsvc3
  bproc3 -->|realizes| bsvc4
  bproc1 -->|realizes| vs1_2
  bproc1 -->|realizes| vs1_3
  bproc2 -->|realizes| vs1_4
  bproc3 -->|realizes| vs1_3
  bproc3 -->|realizes| vs1_4

  classDef actorAI fill:#c2f0ff,stroke:#0288d1,color:#333
  classDef role fill:#f7f099,stroke:#a89400,color:#333
  classDef process fill:#e5d95f,stroke:#8a7a00,color:#333
  classDef service fill:#efe57d,stroke:#9c8a00,color:#333
  classDef stage fill:#eed4a0,stroke:#b08a3a,color:#333
```

Cyan marks the automated matcher, never a person, and the tan stages visit from the value stream. `BPROC1` realizes the part of [value stream stage [`VS1.3`] Resolve](../1_strategy/3_value-stream.md#value-stream) that the published rules settle. `BPROC3` realizes the stewards' part, one task at a time, and the undo window of [value stream stage [`VS1.4`] Commit](../1_strategy/3_value-stream.md#value-stream). A record a steward declines goes back to `BPROC1`. Decisions by pattern join `BPROC3` with story 3.3, **Pending — future initiative** ([initiative 3](../6_transition/2_sequence.md#sequence)).

| ID | Business process | Trigger | Supplier | Input | Output | Customer | Owner role | Realized by | Source | Notes |
| -- | ---------------- | ------- | -------- | ----- | ------ | -------- | ---------- | ----------- | ------ | ----- |
| `BPROC1` | **Resolve an arrival** — turns a source change the integration platform landed into a kept source version, then settles it under the published rules and the source's policy, or hands it to a steward | The arrival job runs: `mdm arrive` for new landing rows, `mdm load` for a source's history in bulk. It runs on demand in the local mode, and on a schedule once the hub is deployed in [initiative 4](../6_transition/2_sequence.md#sequence) | The integration platform (External), through the landing tables | A landing row | A kept source version, its standardised state, scored candidate pairs and quality results; then an automatic change set, or a steward task | `BPROC2`, for automatic change sets<br>[role [`ROLE2`] Data steward](./1_business-actors-and-roles.md#roles), for tasks | [role [`ROLE4`] Technical steward](./1_business-actors-and-roles.md#roles) | `src/mdm/services/arrival.py`, performed by [actor [`ACT6`] Automated matcher](./1_business-actors-and-roles.md#actors) | [Blueprint](../reference/README.md#founding-material) §3 flow (a); [Answer 5](../reference/2026-09-26-request-and-answers.md#answers); adopted — arrival and bulk loading are one process in two batch sizes | |
| `BPROC2` | **Commit a change set** — turns an approved change set into published rows under one commit version, with its change-feed rows, commit-log row and audit records | A change set reaches the commit path: an automatic one from `BPROC1`, a steward's decision from `BPROC3` after its undo window, or a record action such as a detach or a merge | `BPROC1`<br>`BPROC3`<br>stewards, through the record actions as service functions | A change set with its authority | Golden records, cross-references, relationships and change-feed rows under one commit version; an audit record | Listening systems (External), through the change notifier and the integration platform<br>[role [`ROLE5`] Consumer](./1_business-actors-and-roles.md#roles) | [role [`ROLE1`] Data owner](./1_business-actors-and-roles.md#roles) | `src/mdm/services/commit.py` | Blueprint §5.4, §5.5; [Answer 5](../reference/2026-09-26-request-and-answers.md#answers); adopted — the commit path writes the change feed in its own transaction ([decision 8](../decisions/8_commit-order-lock-and-change-feed.md)) | |
| `BPROC3` | **Decide a steward task** — a steward opens a task in the inbox, weighs the explanation beside it and decides; the decision waits out its undo window in the undo tray, then commits through `BPROC2`, or goes back to the task | A steward presses a decision key, or its button, on a task | `BPROC1`, whose tasks the inbox orders by due time | A task with its records, candidates, explanation, golden preview and impact line | A staged decision; after its undo window, a change set for `BPROC2` and a steward match label, or the task back in the inbox | `BPROC2`<br>`BPROC1`, for a record a steward declined, which arrival settles again | [role [`ROLE3`] Coordinating steward](./1_business-actors-and-roles.md#roles) | `src/mdm/services/decisions.py` and `src/mdm/services/tray.py`, on screen in `src/mdm/ui/pages/inbox.py`, performed by [role [`ROLE2`] Data steward](./1_business-actors-and-roles.md#roles) | Blueprint §3 flow (a); adopted — one process for every decision on a task; [decision 19](../decisions/19_undo-tray.md) | |

### What the processes handle

```mermaid
flowchart LR
  bproc1{{"⚙ Resolve an arrival [BPROC1]"}}:::process
  bproc2{{"⚙ Commit a change set [BPROC2]"}}:::process
  bproc3{{"⚙ Decide a steward task [BPROC3]"}}:::process
  bobj2[["▧ Source record [BOBJ2]"]]:::object
  bobj8[["▧ Steward task [BOBJ8]"]]:::object
  bobj9[["▧ Change set [BOBJ9]"]]:::object
  bobj3[["▧ Golden record [BOBJ3]"]]:::object
  bobj10[["▧ Audit record [BOBJ10]"]]:::object

  bproc1 -->|accesses| bobj2
  bproc1 -->|accesses| bobj8
  bproc2 -->|accesses| bobj9
  bproc2 -->|accesses| bobj3
  bproc2 -->|accesses| bobj10
  bproc3 -->|accesses| bobj8
  bproc3 -->|accesses| bobj9

  classDef process fill:#e5d95f,stroke:#8a7a00,color:#333
  classDef object fill:#fffbb5,stroke:#b8a200,color:#333
```

`BPROC1` keeps every version of a [business object [`BOBJ2`] Source record](./4_business-objects.md#business-objects) and writes the tasks the rules cannot settle. `BPROC3` claims and closes the task it decides, and prepares the change set `BPROC2` commits. `BPROC2` alone changes a golden record, and records each change it makes in the same transaction.
