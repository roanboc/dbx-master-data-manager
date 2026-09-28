# Information Layer

_[← Model home](../README.md)_

What the hub holds, who owns it, how it flows from the landing tables to the change feed and through a steward's decisions, how it reaches the screens, and how it is stored and kept.

**ArchiMate viewpoint:** Information: Data Object, with the data domain as its level 1 and the object as its level 2, and Representation; the Business Object each one stands for and the Business Role that owns each domain visit from the business layer.

## Documents

| # | Document | Elements | Question it answers |
| - | -------- | -------- | ------------------- |
| 1 | [1_data-domains.md](./1_data-domains.md) | Data domains and their owners | Who owns which information? |
| 2 | [2_data-objects.md](./2_data-objects.md) | Data objects per domain, and the tables that hold them | What information exists, and in which domain? |
| 3 | [3_data-flows.md](./3_data-flows.md) | Flows between data objects and the parties outside the hub; the stewardship flows; representations, the workbench screens among them | How does information move from the landing tables to listening systems, and through a steward's decisions to the screens? |
| 4 | [4_data-architecture.md](./4_data-architecture.md) | Schema groups, portable types, classification, retention | Where does it live, how sensitive is it, and how long is it kept? |

## Metamodel

```mermaid
flowchart LR
  %% legend
  domain["▦ «Data Object» a data domain, who owns this information [DOBJ#]"]:::domain
  obj["▦ «Data Object» what information exists [DOBJ#.#]"]:::object
  obj2["▦ «Data Object» where it flows next [DOBJ#.#]"]:::object
  bobj[["▧ «Business Object» what the business calls it [BOBJ#]"]]:::business
  role["⚉ «Business Role» who owns the domain [ROLE#]"]:::role
  res[("▤ «Resource» what the hub keeps a copy of [RES#]")]:::resource
  cmp["⊞ «Application Component» what writes the tables [ACMP#]"]:::component
  store[/"⎔ «Artifact» a schema group of the store [ART#]"/]:::technology
  ext["a party outside the hub, with no ID"]:::external

  domain -->|aggregates| obj
  domain -->|associated with| role
  obj -->|realizes| bobj
  obj -->|flows to| obj2
  obj -->|associated with| res
  cmp -->|writes| store
  ext -.->|flows to| obj

  classDef domain fill:#9adcf0,stroke:#0288d1,color:#333
  classDef object fill:#c2f0ff,stroke:#0288d1,color:#333
  classDef business fill:#fffbb5,stroke:#b8a200,color:#333
  classDef role fill:#f7f099,stroke:#a89400,color:#333
  classDef resource fill:#faf0d5,stroke:#c8a24a,color:#333
  classDef component fill:#9adcf0,stroke:#0288d1,color:#333
  classDef technology fill:#dcefd0,stroke:#558b2f,color:#333
  classDef external fill:#f2f2f2,stroke:#999999,color:#333,stroke-dasharray: 4 3
```

The darker cyan marks a data domain, and grey with a dashed border a party outside the hub or a data object it owns. The business object, role, resource, component and artifact visit from their own layers and keep their own shape and colour.

## Layer view

```mermaid
flowchart LR
  ip["Integration platform"]:::external
  cn["Change notifier"]:::external

  subgraph dobj1["▦ Master data configuration [DOBJ1]"]
    dobj1_2["▦ Rule set version [DOBJ1.2]"]:::object
  end
  subgraph dobj2["▦ Source intake [DOBJ2]"]
    dobj2_1["▦ Landing row [DOBJ2.1]"]:::external
    dobj2_2["▦ Source record version [DOBJ2.2]"]:::object
    dobj2_3["▦ Standardised source state [DOBJ2.3]"]:::object
  end
  subgraph dobj3["▦ Resolution work [DOBJ3]"]
    dobj3_1["▦ Candidate pair [DOBJ3.1]"]:::object
    dobj3_2["▦ Steward task [DOBJ3.2]"]:::object
  end
  subgraph dobj5["▦ Audit and privacy [DOBJ5]"]
    dobj5_1["▦ Change set record [DOBJ5.1]"]:::object
  end
  subgraph dobj4["▦ Published master data [DOBJ4]"]
    dobj4_1["▦ Golden record [DOBJ4.1]"]:::object
    dobj4_5["▦ Change feed [DOBJ4.5]"]:::object
  end

  ip -.->|flows to| dobj2_1
  dobj2_1 -->|flows to| dobj2_2
  dobj2_2 -->|flows to| dobj2_3
  dobj2_3 -->|flows to| dobj3_1
  dobj1_2 -->|flows to| dobj3_1
  dobj3_1 -->|flows to| dobj3_2
  dobj3_1 -->|flows to| dobj5_1
  dobj5_1 -->|flows to| dobj4_1
  dobj5_1 -->|flows to| dobj4_5
  dobj4_5 -.->|flows to| cn

  classDef object fill:#c2f0ff,stroke:#0288d1,color:#333
  classDef external fill:#f2f2f2,stroke:#999999,color:#333,stroke-dasharray: 4 3
  style dobj1 fill:#9adcf0,stroke:#0288d1,color:#333
  style dobj2 fill:#9adcf0,stroke:#0288d1,color:#333
  style dobj3 fill:#9adcf0,stroke:#0288d1,color:#333
  style dobj4 fill:#9adcf0,stroke:#0288d1,color:#333
  style dobj5 fill:#9adcf0,stroke:#0288d1,color:#333
```

The view follows one source change through the five domains, from the landing tables to the change feed; [data objects](./2_data-objects.md) lists all twenty-five. The dashed edges to the two outside parties wait for both interfaces to be agreed and deployed, **Pending — future initiative** ([initiative 4](../6_transition/2_sequence.md#sequence)).
