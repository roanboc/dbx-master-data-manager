# Business Layer

_[← Model home](../README.md)_

Who works with master data in the hub, what the hub offers them, what it handles, and the vocabulary and rules that bind it.

**ArchiMate viewpoint:** Business layer: Business Actor, Business Role, Contract, Business Service, Business Process, Business Object, Business Rule.

## Documents

| # | Document | Elements | Question it answers |
| - | -------- | -------- | ------------------- |
| 1 | [1_business-actors-and-roles.md](./1_business-actors-and-roles.md) | Business Actors, human and automated; Business Roles; Contracts | Who does the work, and who does the hub depend on? |
| 2 | [2_business-services.md](./2_business-services.md) | Business Services | What does the hub offer, and to whom? |
| 3 | [3_business-processes.md](./3_business-processes.md) | Business Processes | How does a source change become a published golden record? |
| 4 | [4_business-objects.md](./4_business-objects.md) | Business Objects | What things do the services handle? |
| 5 | [5_domain-context-and-rules.md](./5_domain-context-and-rules.md) | Glossary, Business Rules | What vocabulary and constraints bind everything? |

## Metamodel

```mermaid
flowchart LR
  %% legend
  actorH(["⚇ «Business Actor (Human)» people who do the work [ACT#]"]):::actor
  actorAI(["⚇ «Business Actor (AI)» an automated actor with delegated authority [ACT#]"]):::actorAI
  role["⚉ «Business Role» the responsibility somebody holds [ROLE#]"]:::role
  svc("⬭ «Business Service» what the hub offers [BSVC#]"):::service
  ctr[/"❒ «Contract» what was agreed with another team [CTR#]"/]:::contract
  proc{{"⚙ «Business Process» what the hub does, in order [BPROC#]"}}:::process
  obj[["▧ «Business Object» what a service handles [BOBJ#]"]]:::object
  obj2[["▧ «Business Object» another thing it handles [BOBJ#]"]]:::object
  rule[/"※ «Business Rule» what must always hold [RULE#]"\]:::rule

  actorH -->|assigned to| role
  actorAI -->|assigned to| role
  actorAI -->|escalates to| role
  svc -->|serves| role
  svc -->|governed by| ctr
  proc -->|realizes| svc
  actorAI -->|assigned to| proc
  obj -->|associated with| obj2
  rule -->|constrains| obj

  classDef actor fill:#fffbb5,stroke:#b8a200,color:#333
  classDef actorAI fill:#c2f0ff,stroke:#0288d1,color:#333
  classDef role fill:#f7f099,stroke:#a89400,color:#333
  classDef service fill:#efe57d,stroke:#9c8a00,color:#333
  classDef contract fill:#d9cc4a,stroke:#7a6c00,color:#333
  classDef process fill:#e5d95f,stroke:#8a7a00,color:#333
  classDef object fill:#fffbb5,stroke:#b8a200,color:#333
  classDef rule fill:#e5d95f,stroke:#8a7a00,color:#333
```

Cyan marks an automated actor, so it is never mistaken for a person. Each other type has its own shape: a service is the rounded box, and a process the hexagon.

## Layer view

```mermaid
flowchart TB
  act2(["⚇ Data stewards (Human) [ACT2]"]):::actor
  act6(["⚇ Automated matcher (AI) [ACT6]"]):::actorAI
  role2["⚉ Data steward [ROLE2]"]:::role
  role5["⚉ Consumer [ROLE5]"]:::role
  bsvc2("⬭ Arrival resolution [BSVC2]"):::service
  bsvc3("⬭ Stewardship work [BSVC3]"):::service
  bsvc7("⬭ Published golden records and change feed [BSVC7]"):::service
  bproc1{{"⚙ Resolve an arrival [BPROC1]"}}:::process
  bproc2{{"⚙ Commit a change set [BPROC2]"}}:::process
  ctr1[/"❒ Landing contract [CTR1]"/]:::contract
  ctr2[/"❒ Listener contract [CTR2]"/]:::contract
  rule6[/"※ Some actions are never automatic [RULE6]"\]:::rule
  bobj9[["▧ Change set [BOBJ9]"]]:::object

  act2 -->|assigned to| role2
  act6 -->|assigned to| role2
  act6 -->|escalates to| role2
  act6 -->|assigned to| bproc1
  bproc1 -->|triggers| bproc2
  bproc1 -->|realizes| bsvc2
  bproc2 -->|realizes| bsvc7
  bsvc2 -->|serves| role2
  bsvc3 -.->|serves| role2
  bsvc7 -->|serves| role5
  bsvc2 -->|governed by| ctr1
  bsvc7 -->|governed by| ctr2
  rule6 -->|constrains| bobj9

  classDef actor fill:#fffbb5,stroke:#b8a200,color:#333
  classDef actorAI fill:#c2f0ff,stroke:#0288d1,color:#333
  classDef role fill:#f7f099,stroke:#a89400,color:#333
  classDef service fill:#efe57d,stroke:#9c8a00,color:#333
  classDef contract fill:#d9cc4a,stroke:#7a6c00,color:#333
  classDef process fill:#e5d95f,stroke:#8a7a00,color:#333
  classDef object fill:#fffbb5,stroke:#b8a200,color:#333
  classDef rule fill:#e5d95f,stroke:#8a7a00,color:#333
```

Cyan marks an automated actor, never a person. Solid edges are true: the automated matcher resolves arrivals, and the commit path publishes golden records and the change feed. The dashed edge waits for the steward workbench.
