# Strategy & Motivation Layer

_[← Model home](../README.md)_

Who has a stake in the Master Data Manager, why it exists, what it must be able to do, and how value flows to listening systems.

**ArchiMate viewpoint:** Motivation and Strategy: Stakeholder, Driver, Assessment, Goal, Outcome, Principle, Value Stream, Capability, Resource.

## Documents

| # | Document | Elements | Question it answers |
| - | -------- | -------- | ------------------- |
| 1 | [1_motivation.md](./1_motivation.md) | Stakeholders, Drivers, Assessments, Goals, Outcomes, Principles | Who cares, what pressures them, and what must always be true? |
| 2 | [2_capabilities-and-resources.md](./2_capabilities-and-resources.md) | Capability areas and capabilities, Resources | What must the hub be able to do, and with what? |
| 3 | [3_value-stream.md](./3_value-stream.md) | Value Stream and its stages | How does value flow from a source record to a listening system? |

## Metamodel

```mermaid
flowchart LR
  %% legend
  subgraph MOT["Motivation"]
    stk(["◍ «Stakeholder» who cares [STK#]"]):::stakeholder
    drv{{"✳ «Driver» what pressures them [DRV#]"}}:::driver
    asm>"⌕ «Assessment» what was found on looking [ASM#]"]:::assessment
    g("◎ «Goal» what must become true [G#]"):::goal
    out[\"◉ «Outcome» how success is seen, and when [OUT#]"/]:::outcome
    p[/"⚑ «Principle» what every change is checked against [P#]"/]:::principle
  end
  subgraph STR["Strategy"]
    vs[["⇉ «Value Stream» how value flows end to end [VS#]"]]:::stage
    stage[["⇉ «Value Stream» a stage of it [VS#.#]"]]:::stage
    cap1["✦ «Capability» level 1, an area [CAP#]"]:::capability
    cap2["✦ «Capability» level 2, what composes it [CAP#.#]"]:::capability
    res[("▤ «Resource» what it is built with [RES#]")]:::resource
  end

  stk -->|concerned with| drv
  drv -->|influences| g
  drv -->|influences| p
  asm -->|influences| g
  out -->|realizes| g
  p -->|realizes| g
  vs -->|realizes| g
  vs -->|composed of| stage
  cap1 -->|composed of| cap2
  cap1 -->|serves| stage
  res -->|assigned to| cap1

  classDef stakeholder fill:#f4ecfc,stroke:#9575cd,color:#333
  classDef driver fill:#e6d6f5,stroke:#7e57c2,color:#333
  classDef assessment fill:#d8c3f0,stroke:#6a45b0,color:#333
  classDef goal fill:#c6aae9,stroke:#5e35b1,color:#333
  classDef outcome fill:#b493e0,stroke:#512da8,color:#333
  classDef principle fill:#a37cd8,stroke:#4527a0,color:#333
  classDef resource fill:#faf0d5,stroke:#c8a24a,color:#333
  classDef capability fill:#f5deaa,stroke:#c8a24a,color:#333
  classDef stage fill:#eed4a0,stroke:#b08a3a,color:#333
```

Purple is Motivation and tan is Strategy. Within each, the shade darkens along the chain: from stakeholder to principle, and from resource to value stream.

## Layer view

```mermaid
flowchart TB
  stk2(["◍ Data owners [STK2]"]):::stakeholder
  stk5(["◍ Integration team [STK5]"]):::stakeholder
  drv1{{"✳ Every commit reaches listening systems automatically [DRV1]"}}:::driver
  g2("◎ Only approved, reversible changes reach listening systems [G2]"):::goal
  p1[/"⚑ The hub never propagates [P1]"/]:::principle
  p2[/"⚑ Approval in proportion to what a change can break [P2]"/]:::principle

  subgraph vs1["⇉ From source record to trusted golden record [VS1]"]
    vs1_3[["⇉ Resolve [VS1.3]"]]:::stage
    vs1_4[["⇉ Commit [VS1.4]"]]:::stage
  end

  cap5["✦ Data stewardship [CAP5]"]:::capability
  cap7["✦ Master data access and sharing [CAP7]"]:::capability
  res3[("▤ Steward time [RES3]")]:::resource

  stk2 -->|concerned with| drv1
  stk5 -->|concerned with| drv1
  drv1 -->|influences| g2
  p1 -->|realizes| g2
  p2 -->|realizes| g2
  vs1 -.->|realizes| g2
  vs1_3 -->|flows to| vs1_4
  cap5 -->|serves| vs1_3
  cap7 -->|serves| vs1_4
  res3 -->|assigned to| cap5

  classDef stakeholder fill:#f4ecfc,stroke:#9575cd,color:#333
  classDef driver fill:#e6d6f5,stroke:#7e57c2,color:#333
  classDef goal fill:#c6aae9,stroke:#5e35b1,color:#333
  classDef principle fill:#a37cd8,stroke:#4527a0,color:#333
  classDef resource fill:#faf0d5,stroke:#c8a24a,color:#333
  classDef capability fill:#f5deaa,stroke:#c8a24a,color:#333
  classDef stage fill:#eed4a0,stroke:#b08a3a,color:#333
  style vs1 fill:#fbf4e2,stroke:#b08a3a,color:#333
```

Solid edges are true: stewards give their time to resolving what the rules leave, and resolved arrivals reach Commit, which the sharing capability serves. The dashed edge waits for the rest of the steward workbench and the platform deployment.
