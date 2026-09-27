# Technology Layer

_[← Model home](../README.md)_

The runtimes, stores and machines the hub is built, tested and run on, and the load they are built for.

**ArchiMate viewpoint:** Technology layer: Technology Service, Node, Artifact.

## Documents

| # | Document | Elements | Question it answers |
| - | -------- | -------- | ------------------- |
| 1 | [1_technology-services.md](./1_technology-services.md) | Technology Services, with their products and versions; Nodes | What does the hub run on, which version, and why that one? |
| 2 | [2_deployment.md](./2_deployment.md) | Artifacts, and the checks that run on every change | How does the code get from the repository to where it runs? |
| 3 | [3_capacity-and-throughput.md](./3_capacity-and-throughput.md) | The declared capacity, and the measured throughput | How much is the hub built for, and how fast does it go? |

## Metamodel

```mermaid
flowchart LR
  %% legend
  node["⬒ «Node» where it runs [NODE#]"]:::node
  tsvc(["⬯ «Technology Service» what the runtime or platform offers [TSVC#]"]):::service
  art[/"⎔ «Artifact» what is built, deployed or created [ART#]"/]:::artifact
  cmp["⊞ «Application Component» what it serves or realizes, from the application layer [ACMP#]"]:::component
  dobj["▦ «Data Object» what it reads or holds, from the information layer [DOBJ#]"]:::dataobject
  ext(["⬯ «Technology Service» run by another team, External [TSVC#]"]):::external

  node -->|realizes| tsvc
  node -->|hosts| art
  art -->|realizes| cmp
  art -->|realizes| dobj
  tsvc -->|serves| cmp
  ext -->|accesses| dobj

  classDef service fill:#c9e7b7,stroke:#558b2f,color:#333
  classDef artifact fill:#dcefd0,stroke:#7cb342,color:#333
  classDef node fill:#a9d68f,stroke:#33691e,color:#333
  classDef component fill:#9adcf0,stroke:#0288d1,color:#333
  classDef dataobject fill:#c2f0ff,stroke:#0288d1,color:#333
  classDef external fill:#f2f2f2,stroke:#999999,color:#333,stroke-dasharray: 4 3
```

Green ramps from service through artifact to node. The application component and the data object are visitors and keep their cyan. A grey dashed service is run by another team. A dashed border or edge is not true yet.

## Layer view

```mermaid
flowchart LR
  node1["⬒ Workstation [NODE1]"]:::node
  node2["⬒ CI runner [NODE2]"]:::node
  node3["⬒ Databricks workspace [NODE3]"]:::pendingnode
  node4["⬒ Lakebase project [NODE4]"]:::pendingnode

  tsvc1(["⬯ Python runtime and packaging [TSVC1]"]):::service
  tsvc2(["⬯ Embedded local store [TSVC2]"]):::service
  tsvc3(["⬯ Operational database [TSVC3]"]):::service
  tsvc4(["⬯ App and job hosting [TSVC4]"]):::pendingservice
  tsvc5(["⬯ Continuous integration [TSVC5]"]):::service
  tsvc6(["⬯ Change notifier [TSVC6]"]):::external

  node1 -->|realizes| tsvc1
  node1 -->|realizes| tsvc2
  node1 -->|realizes| tsvc3
  node2 -->|realizes| tsvc1
  node2 -->|realizes| tsvc3
  node2 -->|realizes| tsvc5
  node3 -.->|realizes| tsvc4
  node4 -.->|realizes| tsvc3

  classDef service fill:#c9e7b7,stroke:#558b2f,color:#333
  classDef pendingservice fill:#c9e7b7,stroke:#558b2f,color:#333,stroke-dasharray: 4 3
  classDef node fill:#a9d68f,stroke:#33691e,color:#333
  classDef pendingnode fill:#a9d68f,stroke:#33691e,color:#333,stroke-dasharray: 4 3
  classDef external fill:#f2f2f2,stroke:#999999,color:#333,stroke-dasharray: 4 3
```

The hub runs today on a workstation and on the continuous integration (CI) runner, against DuckDB and Postgres. The dashed nodes and hosting are the platform, deployed in [initiative 4](../6_transition/2_sequence.md#sequence). The grey change notifier belongs to the data platform team.
