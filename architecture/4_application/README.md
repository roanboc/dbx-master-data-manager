# Application Layer

_[← Model home](../README.md)_

The software that turns landing rows into published golden records, and the two interfaces the teams around the hub build against.

**ArchiMate viewpoint:** Application layer: Application Service, Application Component; collaborations drawn as interaction sequences, and interfaces written as contracts.

## Documents

| # | Document | Elements | Question it answers |
| - | -------- | -------- | ------------------- |
| 1 | [1_application-services.md](./1_application-services.md) | Application Services | What does the software offer the business layer? |
| 2 | [2_application-components.md](./2_application-components.md) | Application Components, each named by its source path | Which components provide those services, and where is each in the code? |
| 3 | [3_application-collaborations.md](./3_application-collaborations.md) | The arrival and commit sequences | How do the components work together, and what happens when a step fails? |
| 4 | [4_solution-design.md](./4_solution-design.md) | The layers, the store on two engines, matching, authority and personas | How is the code structured, and why? |
| 5 | [5_interface-contracts.md](./5_interface-contracts.md) | The landing and listener interfaces | What exactly does each interface promise the integration platform and the change notifier? |

## Metamodel

```mermaid
flowchart LR
  %% legend
  cmp["⊞ «Application Component» what provides it, named by its path [ACMP#]"]:::component
  cmp2["⊞ «Application Component» another component it relies on [ACMP#]"]:::component
  svc(["⬮ «Application Service» what the software offers [ASVC#]"]):::appservice
  svc2(["⬮ «Application Service» another service it relies on [ASVC#]"]):::appservice
  bsvc("⬭ «Business Service» what it realizes, from the business layer [BSVC#]"):::bservice
  cap["✦ «Capability» what it realizes, from the strategy layer [CAP#.#]"]:::capability
  dobj["▦ «Data Object» what it reads or writes, from the information layer [DOBJ#.#]"]:::dataobject
  ext("An External party, with no ID"):::external

  cmp -->|realizes| svc
  cmp2 -->|serves| cmp
  svc2 -->|serves| svc
  svc -->|realizes| bsvc
  svc -->|realizes| cap
  svc -->|accesses| dobj
  ext -->|writes or reads| dobj

  classDef appservice fill:#c2f0ff,stroke:#0288d1,color:#333
  classDef component fill:#9adcf0,stroke:#0288d1,color:#333
  classDef bservice fill:#efe57d,stroke:#9c8a00,color:#333
  classDef capability fill:#f5deaa,stroke:#c8a24a,color:#333
  classDef dataobject fill:#c2f0ff,stroke:#0288d1,color:#333
  classDef external fill:#f2f2f2,stroke:#999999,color:#333,stroke-dasharray: 4 3
```

Cyan ramps from service to component. The business service, the capability and the data object are visitors from their own layers and keep their own shape and colour. A grey dashed box is an External party, such as the integration platform, and carries no ID. An element of this model that another team runs, such as the change notifier or the landing row, keeps its ID and is drawn grey dashed too.

## Layer view

```mermaid
flowchart TB
  acmp1["⊞ Entry points [ACMP1]"]:::component
  acmp5["⊞ Arrival and matching services [ACMP5]"]:::component
  acmp8["⊞ Record lifecycle [ACMP8]"]:::component
  acmp12["⊞ Steward workbench [ACMP12]"]:::pending
  acmp6["⊞ Commit service [ACMP6]"]:::component
  acmp4["⊞ Matching engine [ACMP4]"]:::component
  acmp9["⊞ Authority and privacy [ACMP9]"]:::component
  acmp3["⊞ SQL store [ACMP3]"]:::component

  asvc1(["⬮ Arrival resolution [ASVC1]"]):::appservice
  asvc2(["⬮ Explainable matching [ASVC2]"]):::appservice
  asvc4(["⬮ Golden record commit [ASVC4]"]):::appservice
  asvc5(["⬮ Record lifecycle [ASVC5]"]):::appservice

  acmp5 -->|realizes| asvc1
  acmp5 -->|realizes| asvc2
  acmp4 -->|realizes| asvc2
  acmp6 -->|realizes| asvc4
  acmp8 -->|realizes| asvc5
  acmp12 -.->|realizes| asvc5
  asvc2 -->|serves| asvc1
  asvc4 -->|serves| asvc1
  asvc4 -->|serves| asvc5

  acmp5 -->|serves| acmp1
  acmp8 -->|serves| acmp1
  acmp6 -->|serves| acmp5
  acmp6 -->|serves| acmp8
  acmp4 -->|serves| acmp5
  acmp9 -->|serves| acmp6
  acmp3 -->|serves| acmp5
  acmp3 -->|serves| acmp6

  classDef appservice fill:#c2f0ff,stroke:#0288d1,color:#333
  classDef component fill:#9adcf0,stroke:#0288d1,color:#333
  classDef pending fill:#9adcf0,stroke:#0288d1,color:#333,stroke-dasharray: 4 3
```

Arrivals and the record actions both reach the published tables through the commit service alone, which checks each change set's authority again before it commits. The dashed box and edge are the steward workbench, which arrives with [initiative 3](../6_transition/2_sequence.md#sequence).
