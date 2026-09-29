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
| `OUT1` | `G1` | realizes | Pending — future initiative (initiative 4) |
| `OUT2` | `G2` | realizes | Pending — future initiative (initiative 4) |
| `OUT3` | `G3` | realizes | Pending — future initiative (initiative 4) |
| `OUT4` | `G4` | realizes | Pending — future initiative (initiative 4) |
| `OUT5` | `G5` | realizes | Pending — future initiative (initiative 5) |
| `OUT6` | `G6` | realizes | Pending — future initiative (initiatives 3 and 4) |
| `OUT7` | `G7` | realizes | Pending — future initiative (initiative 4) |
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
| `CAP1` | `VS1.6` | serves | Pending — future initiative (initiative 4) |
| `CAP2` | `VS1.2` | serves | |
| `CAP3` | `VS1.2` | serves | |
| `CAP3` | `VS1.3` | serves | |
| `CAP3` | `VS1.6` | serves | Pending — future initiative (initiative 4) |
| `CAP4` | `VS1.4` | serves | |
| `CAP5` | `VS1.3` | serves | |
| `CAP5` | `VS1.4` | serves | The undo window |
| `CAP6` | `VS1.2` | serves | |
| `CAP6` | `VS1.6` | serves | Pending — future initiative (initiative 4) |
| `CAP7` | `VS1.3` | serves | The record a steward opens from a task |
| `CAP7` | `VS1.4` | serves | |
| `CAP8` | `VS1.3` | serves | Pending — future initiative (initiative 5) |
| `CAP8` | `VS1.6` | serves | Pending — future initiative (initiative 5) |
| `RES1` | `CAP2` | assigned to | |
| `RES2` | `CAP3` | assigned to | Pending — future initiative (initiative 4) |
| `RES2` | `CAP7` | assigned to | Pending — future initiative (initiative 4) |
| `RES3` | `CAP5` | assigned to | |
| `RES4` | `CAP2` | assigned to | Pending — future initiative (initiative 4) |
| `RES4` | `CAP6` | assigned to | Pending — future initiative (initiative 4) |
| `RES5` | `CAP3` | assigned to | The automated matcher honours a not-a-match and a keep-apart label |
| `RES6` | `CAP8` | assigned to | Pending — future initiative (initiative 5) |

### [Value stream](./1_strategy/3_value-stream.md)

| From | To | Relationship | Notes |
| ---- | -- | ------------ | ----- |
| `VS1` | `G1` | realizes | Pending — future initiative (initiatives 3 and 4) |
| `VS1` | `G2` | realizes | Pending — future initiative (initiatives 3 and 4) |
| `VS1` | `G3` | realizes | Pending — future initiative (initiatives 3 and 4) |
| `VS1.1` | `VS1.2` | flows to | Pending — future initiative (initiative 4); the integration platform writes to the agreed landing interface |
| `VS1.2` | `VS1.3` | flows to | |
| `VS1.3` | `VS1.4` | flows to | |
| `VS1.4` | `VS1.5` | flows to | Pending — future initiative (initiative 4); the change notifier reads the change feed |
| `VS1.4` | `VS1.6` | flows to | Pending — future initiative (initiatives 4 and 5); steward labels are kept, and the dry run and label tuning will read them |
| `VS1.6` | `VS1.2` | triggers | Pending — future initiative (initiative 4); a published rule or model re-evaluates records |

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
| `ACT6` | `ROLE2` | assigned to | |
| `ACT6` | `ROLE2` | escalates to | |
| `ACT6` | `ROLE1` | escalates to | Pending — future initiative (initiative 4) |
| `ACT6` | `BPROC1` | assigned to | It performs the process |
| `ACT7` | `ROLE3` | assigned to | Pending — future initiative (initiative 3): the work router, story 3.4 |
| `ACT7` | `ROLE3` | escalates to | Pending — future initiative (initiative 3): breach escalation, story 3.4 |
| `ACT8` | `ROLE3` | assigned to | |
| `ACT8` | `ROLE1` | escalates to | |
| `ACT8` | `BPROC1` | assigned to | It checks agreement and arrivals, and demotes the automatic band |
| `ACT8` | `BPROC3` | assigned to | It withdraws a pattern's bulk rights when blind review of its batches disagrees |
| `ACT9` | `ROLE2` | serves | Pending — future initiative (initiative 5) |
| `ACT9` | `ROLE4` | serves | Pending — future initiative (initiative 5) |
| `ACT9` | `ROLE2` | escalates to | Pending — future initiative (initiative 5) |
| `ACT9` | `ROLE4` | escalates to | Pending — future initiative (initiative 5) |
| `ROLE1` | `BPROC2` | accountable for | |
| `ROLE2` | `BPROC3` | assigned to | |
| `ROLE3` | `BPROC3` | accountable for | |
| `ROLE4` | `BPROC1` | accountable for | |

### [Business services](./2_business/2_business-services.md)

| From | To | Relationship | Notes |
| ---- | -- | ------------ | ----- |
| `BSVC1` | `ROLE2` | serves | |
| `BSVC1` | `ROLE5` | serves | |
| `BSVC1` | `CAP7.1` | realizes | The record view; search and a record as of a date follow in story 3.5 |
| `BSVC2` | `ROLE2` | serves | |
| `BSVC2` | `CTR1` | governed by | The teams' agreement is sought before go-live |
| `BSVC2` | `CAP2.1` | realizes | |
| `BSVC2` | `CAP3.1` | realizes | |
| `BSVC2` | `CAP4.1` | realizes | |
| `BSVC3` | `ROLE2` | serves | |
| `BSVC3` | `ROLE3` | serves | |
| `BSVC3` | `CAP4.2` | realizes | Link on screen; detach, merge, unmerge, retire and reinstate follow in story 3.6 |
| `BSVC3` | `CAP5.1` | realizes | One task at a time, or by pattern after a forced sample |
| `BSVC3` | `CAP5.3` | realizes | Pending — future initiative (initiative 3): record authoring, story 3.7 |
| `BSVC4` | `ROLE1` | serves | Pending — future initiative (initiative 3): checkers, story 3.6 |
| `BSVC4` | `ROLE2` | serves | |
| `BSVC4` | `ROLE3` | serves | A batch's second steward above 250 decisions; the checkers of other change sets follow in story 3.6 |
| `BSVC4` | `CAP5.2` | realizes | Undo before commit; a batch's second steward and its compensation; change sets with a checker in story 3.6 |
| `BSVC5` | `ROLE1` | serves | |
| `BSVC5` | `ROLE4` | serves | |
| `BSVC5` | `CAP1.1` | realizes | |
| `BSVC5` | `CAP1.2` | realizes | |
| `BSVC5` | `CAP2.2` | realizes | |
| `BSVC5` | `CAP3.2` | realizes | |
| `BSVC5` | `VS1.6` | realizes | Pending — future initiative (initiative 4): the exact dry run and re-evaluation; it defines models, sources and rules already |
| `BSVC6` | `ROLE1` | serves | Pending — future initiative (initiative 4) |
| `BSVC6` | `ROLE3` | serves | Pending — future initiative (initiative 4) |
| `BSVC6` | `CAP6.1` | realizes | Pending — future initiative (initiative 4) |
| `BSVC6` | `CAP6.2` | realizes | Pending — future initiative (initiative 4) |
| `BSVC7` | `ROLE5` | serves | |
| `BSVC7` | `CTR2` | governed by | The teams' agreement is sought before go-live |
| `BSVC7` | `CAP7.2` | realizes | |
| `BSVC8` | `ROLE1` | serves | Pending — future initiative (initiative 4) |
| `BSVC8` | `ROLE6` | serves | Pending — future initiative (initiative 4) |
| `BSVC8` | `CAP7.3` | realizes | |
| `BSVC9` | `ROLE2` | serves | Pending — future initiative (initiative 5) |
| `BSVC9` | `ROLE4` | serves | Pending — future initiative (initiative 5) |
| `BSVC9` | `CAP8.1` | realizes | Pending — future initiative (initiative 5) |
| `BSVC9` | `CAP8.2` | realizes | Pending — future initiative (initiative 5) |

### [Business processes](./2_business/3_business-processes.md)

| From | To | Relationship | Notes |
| ---- | -- | ------------ | ----- |
| `BPROC1` | `BPROC2` | triggers | An automatic change set |
| `BPROC1` | `BSVC2` | realizes | |
| `BPROC1` | `VS1.2` | realizes | |
| `BPROC1` | `VS1.3` | realizes | What the published rules settle; stewards decide the rest |
| `BPROC1` | `BOBJ2` | accesses | |
| `BPROC1` | `BOBJ8` | accesses | Writes the tasks the rules cannot settle, and the quality samples it draws |
| `BPROC2` | `BSVC7` | realizes | |
| `BPROC2` | `VS1.4` | realizes | |
| `BPROC2` | `BOBJ3` | accesses | |
| `BPROC2` | `BOBJ9` | accesses | |
| `BPROC2` | `BOBJ10` | accesses | |
| `BPROC3` | `BPROC1` | triggers | A declined record is settled again |
| `BPROC3` | `BPROC2` | triggers | After the undo window |
| `BPROC3` | `BSVC3` | realizes | |
| `BPROC3` | `BSVC4` | realizes | Undo before commit; a batch's second steward above 250 decisions |
| `BPROC3` | `VS1.3` | realizes | The stewards' decisions, one task at a time or by pattern |
| `BPROC3` | `VS1.4` | realizes | The undo window |
| `BPROC3` | `BOBJ8` | accesses | Claims and closes the tasks it decides |
| `BPROC3` | `BOBJ9` | accesses | Stages the change sets that `BPROC2` commits, a batch as one |

### [Business objects](./2_business/4_business-objects.md)

| From | To | Relationship | Notes |
| ---- | -- | ------------ | ----- |
| `BOBJ2` | `BOBJ1` | conforms to | |
| `BOBJ3` | `BOBJ1` | typed by | |
| `BOBJ3` | `BOBJ4` | aggregates | |
| `BOBJ4` | `BOBJ2` | refers to | |
| `BOBJ5` | `BOBJ3` | links | |
| `BOBJ6` | `BOBJ1` | applies to | |
| `BOBJ7` | `BOBJ9` | authorises | |
| `BOBJ8` | `BOBJ9` | decided in | |
| `BOBJ9` | `BOBJ3` | changes | |
| `BOBJ10` | `BOBJ9` | records | |
| `BOBJ11` | `BOBJ2` | raised on | Pending — future initiative (initiative 4) |

### [Domain context and rules](./2_business/5_domain-context-and-rules.md)

| From | To | Relationship | Notes |
| ---- | -- | ------------ | ----- |
| `RULE1` | `BOBJ2` | constrains | |
| `RULE2` | `BOBJ9` | constrains | A link, a batch of up to 250 links after its forced sample, and approving or rejecting a held update, through the undo tray; detach in story 3.6; create, edits and pins in story 3.7 |
| `RULE3` | `BOBJ9` | constrains | Merge, unmerge, retirement and a batch above 250 decisions need a checker at commit; critical edits and showing the checker what the maker saw follow in story 3.6, bulk edits in stories 3.6 and 3.7, and creates in coexistence and authored domains in story 3.7 |
| `RULE4` | `BOBJ1` | constrains | Pending — future initiative (initiative 4) |
| `RULE4` | `BOBJ6` | constrains | Pending — future initiative (initiative 4) |
| `RULE4` | `BOBJ7` | constrains | Pending — future initiative (initiative 4) |
| `RULE5` | `BOBJ3` | constrains | Pending — future initiative (initiative 4) |
| `RULE6` | `BOBJ9` | constrains | |
| `RULE7` | `BOBJ8` | constrains | Tasks decided together pass a unanimous forced sample first |
| `RULE8` | `BOBJ3` | constrains | |
| `RULE8` | `BOBJ4` | constrains | |
| `RULE9` | `BOBJ2` | constrains | |
| `RULE10` | `BOBJ10` | constrains | |
| `RULE11` | `BOBJ3` | constrains | |

## Layer 3 — Information

### [Data domains](./3_information/1_data-domains.md)

| From | To | Relationship | Notes |
| ---- | -- | ------------ | ----- |
| `DOBJ1` | `ROLE1` | associated with | Owner |
| `DOBJ2` | `ROLE4` | associated with | Owner |
| `DOBJ3` | `ROLE2` | associated with | Owner |
| `DOBJ4` | `ROLE1` | associated with | Owner |
| `DOBJ5` | `ROLE1` | associated with | Owner |

### [Data objects](./3_information/2_data-objects.md)

| From | To | Relationship | Notes |
| ---- | -- | ------------ | ----- |
| `DOBJ1.1` | `BOBJ1` | realizes | |
| `DOBJ1.1` | `BOBJ7` | realizes | Source policies; the approval matrix and the other policies follow in initiative 4 |
| `DOBJ1.2` | `BOBJ6` | realizes | |
| `DOBJ1.2` | `DOBJ3.1` | flows to | Weights, bands and blocking passes |
| `DOBJ1.3` | `RES4` | associated with | A versioned copy |
| `DOBJ1.3` | `DOBJ3.3` | flows to | Code-list membership |
| `DOBJ2.1` | `BOBJ2` | realizes | As the integration platform delivered it |
| `DOBJ2.1` | `DOBJ2.2` | flows to | Every version, read above the high-water mark and from the gaps |
| `DOBJ2.1` | `DOBJ5.3` | flows to | Personal values, vaulted at intake |
| `DOBJ2.2` | `BOBJ2` | realizes | |
| `DOBJ2.2` | `DOBJ2.3` | flows to | Standardised and keyed |
| `DOBJ2.3` | `BOBJ2` | realizes | |
| `DOBJ2.3` | `DOBJ3.1` | flows to | Blocking and scoring |
| `DOBJ2.3` | `DOBJ3.3` | flows to | Validation rules |
| `DOBJ2.3` | `DOBJ3.4` | flows to | Records to settle, queued with the state |
| `DOBJ3.1` | `DOBJ3.2` | flows to | Review band, holds, possible duplicates |
| `DOBJ3.1` | `DOBJ3.7` | flows to | A share of the automated links and creates, drawn in the transaction that settles them |
| `DOBJ3.1` | `DOBJ5.1` | flows to | Automatic change sets |
| `DOBJ3.2` | `BOBJ8` | realizes | The tasks arrival writes, with due times, claims, snoozes and escalations; explained ranks follow in story 3.4 |
| `DOBJ3.2` | `DOBJ3.5` | flows to | A steward's decision, or a batch, staged |
| `DOBJ3.2` | `DOBJ3.9` | flows to | Open reviews of one pattern, grouped; the forced sample and the batch drawn from them |
| `DOBJ3.4` | `DOBJ3.8` | flows to | Arrivals per entity and hour |
| `DOBJ3.5` | `BOBJ9` | realizes | A change set while it waits out its undo window |
| `DOBJ3.5` | `DOBJ3.4` | flows to | A declined record, or a compensated batch's record, queued again |
| `DOBJ3.5` | `DOBJ3.6` | flows to | The match decision, in the same transaction; a compensation's chunk withdraws the labels its original wrote |
| `DOBJ3.5` | `DOBJ3.7` | flows to | A blind answer, a sampled steward decision, and 2% of the links each batch stages, rounded up, in the same transaction |
| `DOBJ3.5` | `DOBJ5.1` | flows to | After the deadline, through the commit path, a batch one chunk at a time; audited even when nothing is published |
| `DOBJ3.6` | `DOBJ3.1` | flows to | A declined golden record's members leave the record's candidates |
| `DOBJ3.6` | `RES5` | associated with | The hub's copy, kept as stewards decide |
| `DOBJ3.7` | `BOBJ8` | realizes | A quality sample, and the task that asks for it |
| `DOBJ3.7` | `DOBJ3.2` | flows to | A sample's task; a review when it disagrees |
| `DOBJ3.7` | `DOBJ3.8` | flows to | Agreement of the latest automated samples, and of each pattern's batch samples |
| `DOBJ3.8` | `DOBJ3.2` | flows to | While demoted, automatic-band arrivals become review tasks; handed back after a restore |
| `DOBJ3.8` | `DOBJ3.9` | flows to | Withdrawn bulk rights: no batch drawn or staged, and a committing one stops before its next chunk |
| `DOBJ3.8` | `DOBJ5.1` | flows to | Each trip, withdrawal and restore, audited |
| `DOBJ3.9` | `BOBJ9` | realizes | A change set in chunks under one batch ID |
| `DOBJ3.9` | `DOBJ3.5` | flows to | A batch staged as one decision, locking every review |
| `DOBJ4.1` | `BOBJ3` | realizes | |
| `DOBJ4.1` | `DOBJ5.2` | flows to | Before and after, in the commit transaction |
| `DOBJ4.2` | `BOBJ4` | realizes | |
| `DOBJ4.3` | `BOBJ3` | realizes | |
| `DOBJ4.4` | `BOBJ5` | realizes | |
| `DOBJ4.5` | `BOBJ9` | realizes | The committed change set, as listening systems read it |
| `DOBJ4.6` | `BOBJ3` | realizes | |
| `DOBJ4.7` | `BOBJ3` | realizes | |
| `DOBJ5.1` | `BOBJ9` | realizes | |
| `DOBJ5.1` | `DOBJ4.1` | flows to | The commit path |
| `DOBJ5.1` | `DOBJ4.2` | flows to | The commit path |
| `DOBJ5.1` | `DOBJ4.3` | flows to | The commit path |
| `DOBJ5.1` | `DOBJ4.4` | flows to | The commit path |
| `DOBJ5.1` | `DOBJ4.5` | flows to | The commit path |
| `DOBJ5.1` | `DOBJ4.6` | flows to | The commit path |
| `DOBJ5.2` | `BOBJ10` | realizes | |
| `DOBJ5.3` | `BOBJ10` | realizes | |
| `DOBJ5.4` | `BOBJ10` | realizes | |

## Layer 4 — Application

### [Application services](./4_application/1_application-services.md)

| From | To | Relationship | Notes |
| ---- | -- | ------------ | ----- |
| `ASVC1` | `ASVC8` | serves | The quality breaker's pause, named in the inbox and the decide pane |
| `ASVC1` | `ASVC9` | serves | Settles a declined record again; checks agreement after a blind answer |
| `ASVC1` | `BSVC2` | realizes | |
| `ASVC1` | `BSVC5` | realizes | Bulk loading of source history |
| `ASVC1` | `CAP6.1` | realizes | Quality rules on arrival |
| `ASVC1` | `DOBJ2.1` | accesses | Reads, never writes |
| `ASVC2` | `ASVC1` | serves | |
| `ASVC2` | `ASVC8` | serves | Scores and explanations |
| `ASVC2` | `BSVC5` | realizes | The match test |
| `ASVC3` | `BSVC5` | realizes | |
| `ASVC4` | `ASVC1` | serves | |
| `ASVC4` | `ASVC5` | serves | |
| `ASVC4` | `ASVC9` | serves | Commits each staged decision, and each chunk of a batch |
| `ASVC4` | `BSVC4` | realizes | The authority check at commit, a batch's second steward included; makers and checkers of other change sets on screen in story 3.6 |
| `ASVC4` | `BSVC7` | realizes | |
| `ASVC4` | `CAP5.2` | realizes | The commit path and its authority check |
| `ASVC4` | `CAP7.1` | realizes | The audit log |
| `ASVC4` | `DOBJ4.5` | accesses | The only writer |
| `ASVC5` | `ASVC9` | serves | Plans a link, a batch's links and their compensation, and approving a held update |
| `ASVC5` | `BSVC3` | realizes | A link on screen, through the undo tray; detach in story 3.6 |
| `ASVC5` | `CAP4.2` | realizes | As service functions |
| `ASVC6` | `ASVC8` | serves | Masking and reveals |
| `ASVC6` | `ASVC10` | serves | Masking and reveals |
| `ASVC6` | `BSVC8` | realizes | |
| `ASVC6` | `DOBJ5.4` | accesses | One row per attribute and record revealed, with its reason code |
| `ASVC7` | `BSVC9` | realizes | Pending — future initiative (initiative 5) |
| `ASVC7` | `CAP8.1` | realizes | Pending — future initiative (initiative 5): the plumbing and the stub exist, the capability arrives with the language model |
| `ASVC8` | `BSVC3` | realizes | |
| `ASVC8` | `CAP5.1` | realizes | |
| `ASVC8` | `CAP3.1` | realizes | The decision view |
| `ASVC8` | `DOBJ5.4` | accesses | One row per record a batch split takes out of the batch on a personal comparison, reason `batch_split` |
| `ASVC9` | `BSVC4` | realizes | Undo before commit; a batch's second steward |
| `ASVC9` | `CAP5.2` | realizes | |
| `ASVC10` | `BSVC1` | realizes | |
| `ASVC10` | `CAP4.1` | realizes | The Why of each value |
| `ASVC10` | `CAP7.1` | realizes | |

### [Application components](./4_application/2_application-components.md)

| From | To | Relationship | Notes |
| ---- | -- | ------------ | ----- |
| `ACMP2` | `ACMP3` | serves | |
| `ACMP2` | `ACMP4` | serves | |
| `ACMP3` | `ACMP1` | serves | `mdm ddl` prints the DDL and the grants; every command opens the store |
| `ACMP3` | `ACMP5` | serves | |
| `ACMP3` | `ACMP6` | serves | |
| `ACMP3` | `ACMP7` | serves | |
| `ACMP3` | `ACMP8` | serves | |
| `ACMP3` | `ACMP9` | serves | |
| `ACMP3` | `ACMP10` | serves | The access log of every assistant call |
| `ACMP3` | `ACMP11` | serves | The simulator writes landing rows through the store |
| `ACMP3` | `ACMP14` | serves | Column types and the stored form of a value |
| `ACMP3` | `ACMP15` | serves | |
| `ACMP3` | `ACMP16` | serves | |
| `ACMP4` | `ACMP5` | serves | |
| `ACMP4` | `ACMP7` | serves | |
| `ACMP4` | `ACMP8` | serves | Survivorship recomputed after a lifecycle action |
| `ACMP4` | `ACMP11` | serves | Check digits for invented registered IDs |
| `ACMP4` | `ACMP15` | serves | The sample draw, the agreement bound, and a batch's forced sample, split and chunks |
| `ACMP4` | `ASVC2` | realizes | |
| `ACMP5` | `ACMP1` | serves | |
| `ACMP5` | `ACMP15` | serves | Candidates and explanations, a blind review's golden records and a batch's live check among them, a batch's cannot-link walk, and arrival for a declined record or a compensated batch's record |
| `ACMP5` | `ASVC1` | realizes | |
| `ACMP5` | `ASVC2` | realizes | |
| `ACMP6` | `ACMP1` | serves | |
| `ACMP6` | `ACMP5` | serves | |
| `ACMP6` | `ACMP8` | serves | |
| `ACMP6` | `ACMP15` | serves | Commits each chunk of a batch |
| `ACMP6` | `ASVC4` | realizes | |
| `ACMP7` | `ACMP1` | serves | |
| `ACMP7` | `ACMP5` | serves | Published models and rule sets |
| `ACMP7` | `ACMP6` | serves | The rule versions an automated authority names |
| `ACMP7` | `ACMP8` | serves | Survivorship rules |
| `ACMP7` | `ACMP9` | serves | The published model an authority check reads |
| `ACMP7` | `ACMP15` | serves | Published models |
| `ACMP7` | `ACMP16` | serves | Published models |
| `ACMP7` | `ASVC3` | realizes | |
| `ACMP8` | `ACMP1` | serves | |
| `ACMP8` | `ACMP15` | serves | Plans and commits a link and a held update's approval; commits a blind answer; a golden record's values without the sampled record; plans a batch's links and a compensation's detaches |
| `ACMP8` | `ASVC5` | realizes | |
| `ACMP9` | `ACMP1` | serves | |
| `ACMP9` | `ACMP5` | serves | The vault for arriving personal values |
| `ACMP9` | `ACMP6` | serves | The authority check at commit |
| `ACMP9` | `ACMP7` | serves | The role check and the bootstrap authority of every load and publish |
| `ACMP9` | `ACMP8` | serves | The role check and the role authority of every record action |
| `ACMP9` | `ACMP10` | serves | Masking before every prompt |
| `ACMP9` | `ACMP12` | serves | The actor of each request |
| `ACMP9` | `ACMP15` | serves | |
| `ACMP9` | `ACMP16` | serves | |
| `ACMP9` | `ASVC6` | realizes | |
| `ACMP10` | `ACMP1` | serves | |
| `ACMP10` | `ASVC7` | realizes | |
| `ACMP11` | `ACMP1` | serves | |
| `ACMP11` | `DOBJ2.1` | accesses | Writes landing rows as the integration platform would, in the local mode only |
| `ACMP12` | `ACMP1` | serves | `mdm ui` |
| `ACMP12` | `ASVC5` | realizes | Link, approving a held update and a batch's links, through the undo tray; detach and the other record actions follow in story 3.6 |
| `ACMP13` | `ACMP1` | serves | Pending — future initiative (initiative 4): scheduled jobs run the command line |
| `ACMP14` | `ACMP5` | serves | |
| `ACMP14` | `ACMP6` | serves | |
| `ACMP14` | `ACMP7` | serves | |
| `ACMP14` | `ACMP8` | serves | |
| `ACMP14` | `ACMP9` | serves | |
| `ACMP14` | `ACMP12` | serves | The tray's labels |
| `ACMP14` | `ACMP15` | serves | |
| `ACMP14` | `ACMP16` | serves | |
| `ACMP15` | `ACMP1` | serves | `mdm tray flush`, `mdm breaker`, `mdm batch` |
| `ACMP15` | `ACMP5` | serves | The breaker's state and the sample draws |
| `ACMP15` | `ACMP12` | serves | |
| `ACMP15` | `ASVC8` | realizes | |
| `ACMP15` | `ASVC9` | realizes | |
| `ACMP15` | `ASVC1` | realizes | The draw of quality samples and the quality breaker, with `mdm breaker` |
| `ACMP16` | `ACMP12` | serves | |
| `ACMP16` | `ASVC10` | realizes | |

## Layer 5 — Technology

### [Technology services](./5_technology/1_technology-services.md)

| From | To | Relationship | Notes |
| ---- | -- | ------------ | ----- |
| `TSVC2` | `ACMP3` | serves | |
| `TSVC3` | `ACMP3` | serves | |
| `TSVC4` | `ACMP1` | serves | Pending — future initiative (initiative 4) |
| `TSVC4` | `ACMP12` | serves | Pending — future initiative (initiative 4) |
| `TSVC6` | `DOBJ4.5` | accesses | Pending — future initiative (initiative 4): External; the change notifier will read the commit log and announce new versions |
| `NODE1` | `TSVC1` | realizes | |
| `NODE1` | `TSVC2` | realizes | |
| `NODE1` | `TSVC3` | realizes | A throwaway Postgres 16 server the tests start, where one is installed |
| `NODE1` | `ART1` | hosts | |
| `NODE1` | `ART3` | hosts | The local store file |
| `NODE1` | `ART5` | hosts | |
| `NODE1` | `ART6` | hosts | |
| `NODE1` | `ART7` | hosts | |
| `NODE1` | `ART8` | hosts | |
| `NODE2` | `TSVC1` | realizes | |
| `NODE2` | `TSVC3` | realizes | A `postgres:17` service container |
| `NODE2` | `TSVC5` | realizes | |
| `NODE2` | `ART1` | hosts | Installed from the lockfile for the lint and the tests |
| `NODE2` | `ART4` | hosts | |
| `NODE2` | `ART6` | hosts | |
| `NODE3` | `TSVC4` | realizes | Pending — future initiative (initiative 4) |
| `NODE3` | `ART1` | hosts | Pending — future initiative (initiative 4) |
| `NODE4` | `TSVC3` | realizes | Pending — future initiative (initiative 4) |
| `NODE4` | `ART3` | hosts | Pending — future initiative (initiative 4) |

### [Deployment](./5_technology/2_deployment.md)

| From | To | Relationship | Notes |
| ---- | -- | ------------ | ----- |
| `ART1` | `ACMP1` | realizes | |
| `ART1` | `ACMP2` | realizes | |
| `ART1` | `ACMP3` | realizes | |
| `ART1` | `ACMP4` | realizes | |
| `ART1` | `ACMP5` | realizes | |
| `ART1` | `ACMP6` | realizes | |
| `ART1` | `ACMP7` | realizes | |
| `ART1` | `ACMP8` | realizes | |
| `ART1` | `ACMP9` | realizes | |
| `ART1` | `ACMP10` | realizes | |
| `ART1` | `ACMP11` | realizes | |
| `ART1` | `ACMP12` | realizes | |
| `ART1` | `ACMP14` | realizes | |
| `ART1` | `ACMP15` | realizes | |
| `ART1` | `ACMP16` | realizes | |
| `ART2` | `DOBJ1.1` | realizes | Loaded as entity model versions |
| `ART3` | `DOBJ1` | realizes | |
| `ART3` | `DOBJ2` | realizes | |
| `ART3` | `DOBJ3` | realizes | |
| `ART3` | `DOBJ4` | realizes | |
| `ART3` | `DOBJ5` | realizes | |

## Transition

### [Target state](./6_transition/1_target-state.md)

| From | To | Relationship | Notes |
| ---- | -- | ------------ | ----- |
| `PLAT1` | `PLAT2` | must be true before | |
| `PLAT1` | `GAP1` | differs by | |
| `PLAT1` | `GAP2` | differs by | |
| `PLAT1` | `GAP3` | differs by | |
| `PLAT1` | `GAP4` | differs by | |
| `PLAT1` | `GAP5` | differs by | |
| `PLAT1` | `GAP6` | differs by | |
| `PLAT1` | `GAP7` | differs by | |
| `PLAT1` | `GAP8` | differs by | |
| `PLAT1` | `GAP9` | differs by | |
| `PLAT1` | `GAP10` | differs by | |
| `PLAT1` | `GAP11` | differs by | |
| `PLAT1` | `GAP12` | differs by | |
| `PLAT1` | `GAP13` | differs by | |
| `PLAT2` | `PLAT3` | must be true before | |
| `PLAT2` | `G1` | serves | |
| `PLAT2` | `G2` | serves | |
| `PLAT2` | `G3` | serves | |
| `PLAT2` | `G4` | serves | |
| `PLAT2` | `G6` | serves | |
| `PLAT2` | `G7` | serves | |
| `PLAT3` | `PLAT4` | must be true before | |
| `PLAT3` | `G1` | serves | |
| `PLAT3` | `G4` | serves | |
| `PLAT3` | `G5` | serves | |
| `PLAT4` | `G5` | serves | |
| `GAP1` | `PLAT2` | closed, reaches | |
| `GAP1` | `BSVC2` | associated with | |
| `GAP1` | `BSVC7` | associated with | |
| `GAP1` | `ACT6` | associated with | |
| `GAP2` | `PLAT2` | closed, reaches | |
| `GAP2` | `CTR1` | associated with | |
| `GAP2` | `CTR2` | associated with | |
| `GAP3` | `PLAT2` | closed, reaches | |
| `GAP3` | `BSVC1` | associated with | |
| `GAP3` | `BSVC3` | associated with | |
| `GAP3` | `BSVC4` | associated with | |
| `GAP3` | `ACT7` | associated with | |
| `GAP3` | `ACT8` | associated with | |
| `GAP3` | `ACMP12` | associated with | |
| `GAP4` | `PLAT2` | closed, reaches | |
| `GAP4` | `CAP3.2` | associated with | |
| `GAP4` | `RULE4` | associated with | |
| `GAP4` | `VS1.6` | associated with | |
| `GAP5` | `PLAT2` | closed, reaches | |
| `GAP5` | `BSVC6` | associated with | |
| `GAP5` | `CAP6.2` | associated with | |
| `GAP5` | `BOBJ11` | associated with | |
| `GAP6` | `PLAT2` | closed, reaches | |
| `GAP6` | `RES2` | associated with | |
| `GAP6` | `ACT4` | associated with | |
| `GAP6` | `TSVC4` | associated with | |
| `GAP6` | `NODE3` | associated with | |
| `GAP6` | `NODE4` | associated with | |
| `GAP6` | `ACMP13` | associated with | |
| `GAP6` | `BSVC8` | associated with | Mapping workspace groups to roles |
| `GAP7` | `PLAT2` | closed, reaches | |
| `GAP7` | `RULE5` | associated with | |
| `GAP7` | `OUT7` | associated with | |
| `GAP8` | `PLAT2` | closed, reaches | |
| `GAP8` | `OUT1` | associated with | |
| `GAP8` | `ASM3` | associated with | |
| `GAP9` | `PLAT2` | closed, reaches | |
| `GAP9` | `RES4` | associated with | |
| `GAP9` | `DOBJ1.3` | associated with | |
| `GAP10` | `PLAT3` | closed, reaches | |
| `GAP10` | `BSVC9` | associated with | |
| `GAP10` | `ACT9` | associated with | |
| `GAP10` | `RES6` | associated with | |
| `GAP10` | `OUT5` | associated with | |
| `GAP11` | `PLAT3` | closed, reaches | |
| `GAP11` | `CAP4.2` | associated with | |
| `GAP11` | `CAP6.1` | associated with | |
| `GAP11` | `CAP3.2` | associated with | |
| `GAP12` | `PLAT3` | closed, reaches | |
| `GAP12` | `CAP2.3` | associated with | |
| `GAP13` | `PLAT4` | closed, reaches | |
| `GAP13` | `CAP8.2` | associated with | |
| `GAP13` | `CAP3.1` | associated with | |
