# Relationships

_[← Model home](./README.md)_

**Read by agents and validators, never drawn.** Every relationship a catalogue
column does not carry is declared here, once and nowhere else. A human page
draws its relationships in a diagram and names them in prose; it never
declares them (`architecture-document-style` § Relationships are declared,
never only drawn). This file defines no element, so it carries no status line
and no diagram.

A row is `From | To | Relationship | Notes`: bare identifiers in the first two
cells, the relationship as the diagram labels it, and notes after it. The full
form, with each end's `<glyph> «Archetype» <name>` between the identifiers, is
read too, by shape. A relationship that is not true yet opens its notes cell
with `Pending — future initiative`. Decomposition is never a row — the dotted
identifier says it — and nothing relates an element to itself.

One `##` per layer; under it one `###` per document that defines the source
element, linked; under that one table, ordered by source identifier. A row is
unique by its first three cells, so a pair drawn in two diagrams appears once,
under the source's document. Nothing above the first `##` names an identifier.

## Layer 1 — Strategy

### [Motivation](./1_strategy/1_motivation.md)

| From | To | Relationship | Notes |
| ---- | -- | ------------ | ----- |
| `STK1` | `DRV3` | concerned with | |
| `STK1` | `DRV6` | concerned with | |
| `STK2` | `DRV1` | concerned with | |
| `STK2` | `DRV2` | concerned with | |
| `STK3` | `DRV4` | concerned with | |
| `STK4` | `DRV2` | concerned with | |
| `STK5` | `DRV1` | concerned with | |
| `STK6` | `DRV1` | concerned with | |
| `STK6` | `DRV3` | concerned with | |
| `STK7` | `DRV5` | concerned with | |
| `STK8` | `DRV2` | concerned with | |
| `DRV1` | `G2` | influences | |
| `DRV2` | `G1` | influences | |
| `DRV2` | `G4` | influences | |
| `DRV3` | `G6` | influences | |
| `DRV4` | `G3` | influences | |
| `DRV5` | `G7` | influences | |
| `DRV5` | `P4` | influences | |
| `DRV6` | `G5` | influences | |
| `ASM1` | `G1` | influences | |
| `ASM2` | `P4` | influences | |
| `ASM3` | `G1` | influences | |
| `OUT1` | `G1` | realizes | Pending — future initiative (initiatives 2 and 4) |
| `OUT2` | `G2` | realizes | Pending — future initiative (initiative 4) |
| `OUT3` | `G3` | realizes | Pending — future initiative (initiative 4) |
| `OUT4` | `G4` | realizes | Pending — future initiative (initiative 4) |
| `OUT5` | `G5` | realizes | Pending — future initiative (initiative 5) |
| `OUT6` | `G6` | realizes | Pending — future initiative (initiatives 3 and 4) |
| `OUT7` | `G7` | realizes | Pending — future initiative (initiatives 2 and 4) |
| `P1` | `G2` | realizes | |
| `P2` | `G2` | realizes | |
| `P3` | `G1` | realizes | |
| `P3` | `G4` | realizes | |
| `P4` | `G2` | realizes | |
| `P5` | `G3` | realizes | |
| `P6` | `G5` | realizes | |
| `P7` | `G6` | realizes | |
| `P8` | `G6` | realizes | |

### [Capabilities and resources](./1_strategy/2_capabilities-and-resources.md)

| From | To | Relationship | Notes |
| ---- | -- | ------------ | ----- |
| `CAP1` | `VS1.6` | serves | Pending — future initiative (initiative 2) |
| `CAP2` | `VS1.2` | serves | Pending — future initiative (initiative 2) |
| `CAP3` | `VS1.2` | serves | Pending — future initiative (initiative 2) |
| `CAP3` | `VS1.3` | serves | Pending — future initiative (initiative 2) |
| `CAP3` | `VS1.6` | serves | Pending — future initiative (initiative 4) |
| `CAP4` | `VS1.4` | serves | Pending — future initiative (initiative 2) |
| `CAP5` | `VS1.3` | serves | Pending — future initiative (initiative 3) |
| `CAP5` | `VS1.4` | serves | Pending — future initiative (initiative 2) |
| `CAP6` | `VS1.2` | serves | Pending — future initiative (initiative 2) |
| `CAP6` | `VS1.6` | serves | Pending — future initiative (initiative 4) |
| `CAP7` | `VS1.3` | serves | Pending — future initiative (initiative 3) |
| `CAP7` | `VS1.4` | serves | Pending — future initiative (initiative 2) |
| `CAP8` | `VS1.3` | serves | Pending — future initiative (initiative 5) |
| `CAP8` | `VS1.6` | serves | Pending — future initiative (initiative 5) |
| `RES1` | `CAP2` | assigned to | Pending — future initiative (initiative 2) |
| `RES2` | `CAP3` | assigned to | Pending — future initiative (initiative 2) |
| `RES2` | `CAP7` | assigned to | Pending — future initiative (initiative 2) |
| `RES3` | `CAP5` | assigned to | Pending — future initiative (initiative 3) |
| `RES4` | `CAP2` | assigned to | Pending — future initiative (initiative 2) |
| `RES4` | `CAP6` | assigned to | Pending — future initiative (initiative 2) |
| `RES5` | `CAP3` | assigned to | Pending — future initiative (initiative 3) |
| `RES6` | `CAP8` | assigned to | Pending — future initiative (initiative 5) |

### [Value stream](./1_strategy/3_value-stream.md)

| From | To | Relationship | Notes |
| ---- | -- | ------------ | ----- |
| `VS1` | `G1` | realizes | Pending — future initiative (initiatives 2 to 4) |
| `VS1` | `G2` | realizes | Pending — future initiative (initiatives 2 to 4) |
| `VS1` | `G3` | realizes | Pending — future initiative (initiatives 2 to 4) |
| `VS1.1` | `VS1.2` | flows to | Pending — future initiative (initiative 2) |
| `VS1.2` | `VS1.3` | flows to | Pending — future initiative (initiative 2) |
| `VS1.3` | `VS1.4` | flows to | Pending — future initiative (initiatives 2 and 3) |
| `VS1.4` | `VS1.5` | flows to | Pending — future initiative (initiative 2) |
| `VS1.4` | `VS1.6` | flows to | Pending — future initiative (initiative 2); decisions and labels feed rule tuning |
| `VS1.6` | `VS1.2` | triggers | Pending — future initiative (initiatives 2 and 4); a published rule or model re-evaluates records |

## Layer 2 — Business

### [Business actors and roles](./2_business/1_business-actors-and-roles.md)

| From | To | Relationship | Notes |
| ---- | -- | ------------ | ----- |
| `ACT1` | `ROLE1` | assigned to | |
| `ACT1` | `STK2` | associated with | The people behind the stakeholder |
| `ACT2` | `ROLE2` | assigned to | |
| `ACT2` | `ROLE3` | assigned to | The senior stewards |
| `ACT2` | `STK3` | associated with | The people behind the stakeholder, with the data engineers |
| `ACT2` | `RES3` | realizes | Their working hours |
| `ACT3` | `ROLE4` | assigned to | |
| `ACT3` | `STK3` | associated with | The people behind the stakeholder, with the data stewards |
| `ACT4` | `ROLE6` | assigned to | Pending — future initiative (initiative 4) |
| `ACT5` | `ROLE5` | assigned to | |
| `ACT5` | `STK4` | associated with | Among the consumers of master data |
| `ACT6` | `ROLE2` | assigned to | Pending — future initiative (initiative 2) |
| `ACT6` | `ROLE2` | escalates to | Pending — future initiative (initiative 2) |
| `ACT6` | `ROLE1` | escalates to | Pending — future initiative (initiative 2) |
| `ACT7` | `ROLE3` | assigned to | Pending — future initiative (initiative 3) |
| `ACT7` | `ROLE3` | escalates to | Pending — future initiative (initiative 3) |
| `ACT8` | `ROLE3` | assigned to | Pending — future initiative (initiative 3) |
| `ACT8` | `ROLE1` | escalates to | Pending — future initiative (initiative 3) |
| `ACT9` | `ROLE2` | serves | Pending — future initiative (initiative 5) |
| `ACT9` | `ROLE4` | serves | Pending — future initiative (initiative 5) |
| `ACT9` | `ROLE2` | escalates to | Pending — future initiative (initiative 5) |
| `ACT9` | `ROLE4` | escalates to | Pending — future initiative (initiative 5) |

### [Business services](./2_business/2_business-services.md)

| From | To | Relationship | Notes |
| ---- | -- | ------------ | ----- |
| `BSVC1` | `ROLE2` | serves | Pending — future initiative (initiative 3) |
| `BSVC1` | `ROLE5` | serves | Pending — future initiative (initiative 3) |
| `BSVC1` | `CAP7.1` | realizes | Pending — future initiative (initiative 3) |
| `BSVC2` | `ROLE2` | serves | Pending — future initiative (initiative 2) |
| `BSVC2` | `CTR1` | governed by | Pending — future initiative (initiative 2) |
| `BSVC2` | `CAP2.1` | realizes | Pending — future initiative (initiative 2) |
| `BSVC2` | `CAP3.1` | realizes | Pending — future initiative (initiative 2) |
| `BSVC2` | `CAP4.1` | realizes | Pending — future initiative (initiative 2) |
| `BSVC3` | `ROLE2` | serves | Pending — future initiative (initiative 3) |
| `BSVC3` | `ROLE3` | serves | Pending — future initiative (initiative 3) |
| `BSVC3` | `CAP4.2` | realizes | Pending — future initiative (initiative 3) |
| `BSVC3` | `CAP5.1` | realizes | Pending — future initiative (initiative 3) |
| `BSVC3` | `CAP5.3` | realizes | Pending — future initiative (initiative 3) |
| `BSVC4` | `ROLE1` | serves | Pending — future initiative (initiative 2) |
| `BSVC4` | `ROLE2` | serves | Pending — future initiative (initiative 2) |
| `BSVC4` | `ROLE3` | serves | Pending — future initiative (initiative 2) |
| `BSVC4` | `CAP5.2` | realizes | Pending — future initiative (initiative 2) |
| `BSVC5` | `ROLE1` | serves | Pending — future initiative (initiative 2) |
| `BSVC5` | `ROLE4` | serves | Pending — future initiative (initiative 2) |
| `BSVC5` | `CAP1.1` | realizes | Pending — future initiative (initiative 2) |
| `BSVC5` | `CAP1.2` | realizes | Pending — future initiative (initiative 2) |
| `BSVC5` | `CAP2.2` | realizes | Pending — future initiative (initiative 2) |
| `BSVC5` | `CAP3.2` | realizes | Pending — future initiative (initiative 2) |
| `BSVC6` | `ROLE1` | serves | Pending — future initiative (initiative 4) |
| `BSVC6` | `ROLE3` | serves | Pending — future initiative (initiative 4) |
| `BSVC6` | `CAP6.1` | realizes | Pending — future initiative (initiative 4) |
| `BSVC6` | `CAP6.2` | realizes | Pending — future initiative (initiative 4) |
| `BSVC7` | `ROLE5` | serves | Pending — future initiative (initiative 2) |
| `BSVC7` | `CTR2` | governed by | Pending — future initiative (initiative 2) |
| `BSVC7` | `CAP7.2` | realizes | Pending — future initiative (initiative 2) |
| `BSVC8` | `ROLE1` | serves | Pending — future initiative (initiative 2) |
| `BSVC8` | `ROLE6` | serves | Pending — future initiative (initiative 2) |
| `BSVC8` | `CAP7.3` | realizes | Pending — future initiative (initiative 2) |
| `BSVC9` | `ROLE2` | serves | Pending — future initiative (initiative 5) |
| `BSVC9` | `ROLE4` | serves | Pending — future initiative (initiative 5) |
| `BSVC9` | `CAP8.1` | realizes | Pending — future initiative (initiative 5) |
| `BSVC9` | `CAP8.2` | realizes | Pending — future initiative (initiative 5) |

### [Business objects](./2_business/4_business-objects.md)

| From | To | Relationship | Notes |
| ---- | -- | ------------ | ----- |
| `BOBJ2` | `BOBJ1` | conforms to | Pending — future initiative (initiative 2) |
| `BOBJ3` | `BOBJ1` | typed by | Pending — future initiative (initiative 2) |
| `BOBJ3` | `BOBJ4` | aggregates | Pending — future initiative (initiative 2) |
| `BOBJ4` | `BOBJ2` | refers to | Pending — future initiative (initiative 2) |
| `BOBJ5` | `BOBJ3` | links | Pending — future initiative (initiative 2) |
| `BOBJ6` | `BOBJ1` | applies to | Pending — future initiative (initiative 2) |
| `BOBJ7` | `BOBJ9` | authorises | Pending — future initiative (initiative 2) |
| `BOBJ8` | `BOBJ9` | decided in | Pending — future initiative (initiative 3) |
| `BOBJ9` | `BOBJ3` | changes | Pending — future initiative (initiative 2) |
| `BOBJ10` | `BOBJ9` | records | Pending — future initiative (initiative 2) |
| `BOBJ11` | `BOBJ2` | raised on | Pending — future initiative (initiative 4) |

### [Domain context and rules](./2_business/5_domain-context-and-rules.md)

| From | To | Relationship | Notes |
| ---- | -- | ------------ | ----- |
| `RULE1` | `BOBJ2` | constrains | Pending — future initiative (initiative 2) |
| `RULE2` | `BOBJ9` | constrains | Pending — future initiative (initiative 3) |
| `RULE3` | `BOBJ9` | constrains | Pending — future initiative (initiative 3) |
| `RULE4` | `BOBJ1` | constrains | Pending — future initiative (initiative 4) |
| `RULE4` | `BOBJ6` | constrains | Pending — future initiative (initiative 4) |
| `RULE4` | `BOBJ7` | constrains | Pending — future initiative (initiative 4) |
| `RULE5` | `BOBJ3` | constrains | Pending — future initiative (initiative 4) |
| `RULE6` | `BOBJ9` | constrains | Pending — future initiative (initiative 2) |
| `RULE7` | `BOBJ8` | constrains | Pending — future initiative (initiative 3) |
| `RULE8` | `BOBJ3` | constrains | Pending — future initiative (initiative 2) |
| `RULE8` | `BOBJ4` | constrains | Pending — future initiative (initiative 2) |
| `RULE9` | `BOBJ2` | constrains | Pending — future initiative (initiative 2) |
| `RULE10` | `BOBJ10` | constrains | Pending — future initiative (initiatives 2 and 4) |
| `RULE11` | `BOBJ3` | constrains | Pending — future initiative (initiative 2) |
