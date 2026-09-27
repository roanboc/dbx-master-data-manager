# Value stream

_[← Strategy layer](./README.md) · [Model home](../README.md)_

**ArchiMate viewpoint:** Strategy: Value Stream.

**Status:** ◐ Draft catalogue — identified from the product owner's request and answers of 26 September 2026 and the approved Master Data Manager Blueprint; not yet validated.

## Value stream

```mermaid
flowchart LR
  subgraph vs1["⇉ From source record to trusted golden record [VS1]"]
    vs1_1[["⇉ Land [VS1.1]"]]:::external
    vs1_2[["⇉ Arrive [VS1.2]"]]:::stage
    vs1_3[["⇉ Resolve [VS1.3]"]]:::stage
    vs1_4[["⇉ Commit [VS1.4]"]]:::stage
    vs1_5[["⇉ Propagate [VS1.5]"]]:::external
    vs1_6[["⇉ Govern [VS1.6]"]]:::stage

    vs1_1 -.->|flows to| vs1_2
    vs1_2 -.->|flows to| vs1_3
    vs1_3 -.->|flows to| vs1_4
    vs1_4 -.->|flows to| vs1_5
    vs1_4 -.->|flows to| vs1_6
    vs1_6 -.->|triggers| vs1_2
  end

  g1("◎ Trusted golden records [G1]"):::motivation
  g2("◎ Only approved, reversible changes reach listening systems [G2]"):::motivation
  g3("◎ Stewardship keeps pace with arrivals [G3]"):::motivation

  vs1 -.->|realizes| g1
  vs1 -.->|realizes| g2
  vs1 -.->|realizes| g3

  classDef stage fill:#eed4a0,stroke:#b08a3a,color:#333
  classDef external fill:#f2f2f2,stroke:#999999,color:#333,stroke-dasharray: 4 3
  classDef motivation fill:#e6d6f5,stroke:#7e57c2,color:#333
  style vs1 fill:#fbf4e2,stroke:#b08a3a,color:#333
```

Grey stages with a dashed border happen outside the hub. Dashed edges are not true yet, because the hub is not built.

| ID | Value stream or stage | Value added | Realized by | Source | Notes |
| -- | --------------------- | ----------- | ----------- | ------ | ----- |
| `VS1` | **From source record to trusted golden record** — how a change in a source system becomes a trusted golden record that listening systems receive | Every system that uses master data relies on one reconciled version of each person and organisation | **Pending — future initiative** (initiatives 2 to 4), through its stages | [Blueprint](../reference/README.md#founding-material) §7; [Request](../reference/2026-09-26-request-and-answers.md#the-request) | |
| `VS1.1` | **Land** — the integration platform writes a source change into the landing tables | The change is in the operational database, ready for the hub to read | External — the integration platform, run by the integration team | [Answer 5](../reference/2026-09-26-request-and-answers.md#answers) | |
| `VS1.2` | **Arrive** — the hub reads the change from its watermark, checks it against the landing contract, standardises and scores it, and keeps its version | Every arrival can be compared with every golden record | **Pending — future initiative** (initiative 2): the arrival business process | adopted — stages drawn from the Blueprint's arrival flow (§3, flow (a)); [Answer 5](../reference/2026-09-26-request-and-answers.md#answers) | |
| `VS1.3` | **Resolve** — the automated matcher settles what the published rules allow, and stewards decide the rest by pattern | Every arrival has a decided place | **Pending — future initiative** (initiatives 2 and 3) | adopted — stages drawn from the Blueprint's arrival and sweep flows (§3, flows (a) and (c)) | |
| `VS1.4` | **Commit** — an approved change set reaches the published tables with its authority; a steward's decision first waits out its undo window | The golden record changes, explained and reversible | **Pending — future initiative** (initiative 2): the commit business process | adopted — stages drawn from the Blueprint's arrival flow (§3, flow (a)) | |
| `VS1.5` | **Propagate** — the platform's change notifier and the integration platform carry committed changes to listening systems | Every listening system sees the same committed change within seconds | External — the platform's change notifier and the integration platform | [Answer 5](../reference/2026-09-26-request-and-answers.md#answers) | |
| `VS1.6` | **Govern** — data owners and technical stewards define models, sources and rules, tune them with an exact dry run, and send quality issues back to sources | Fewer arrivals need a person next time | **Pending — future initiative** (initiatives 2 and 4) | adopted — stage for the Blueprint's rule change flow (§3, flow (b)) | |
