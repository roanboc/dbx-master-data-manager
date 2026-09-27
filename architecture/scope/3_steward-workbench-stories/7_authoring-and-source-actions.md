# Story 3.7 — Authoring and the source record's actions

_[← Scope document](../3_steward-workbench.md)_

**Goal:** stewards create records, from a form or from a held new record, and edit them with a live duplicate check; pin values; reinstate records; and resolve exceptions from the source record.

## Context

- [Capability [`CAP5.3`] Record authoring](../../1_strategy/2_capabilities-and-resources.md#capabilities)
- [Actor [`ACT6`] Automated matcher](../../2_business/1_business-actors-and-roles.md#automated-matcher), for the recompute when a pin expires
- [Rule [`RULE2`] A steward decides routine changes alone](../../2_business/5_domain-context-and-rules.md#business-rules) and rule [`RULE3`] Four eyes on what is hard to reverse
- [Glossary](../../2_business/5_domain-context-and-rules.md#glossary): Architecture style, Pin, Reinstate, Critical attribute
- [Business service [`BSVC3`] Stewardship work](../../2_business/2_business-services.md#business-services)
- [Data object [`DOBJ4.7`] Steward value](../../3_information/2_data-objects.md#published-master-data)
- [Declared capacity](../../5_technology/3_capacity-and-throughput.md#declared-capacity)
- [Story 3.1](./1_inbox-decide-tray-and-record.md) and [story 3.6](./6_record-actions-maker-and-checker.md)

## Acceptance criteria

- [ ] Forms come from the entity model, with inline validation. The duplicate panel lists golden records, held arrivals and tasks within 300 ms at the 95th percentile (Blueprint §3; adopted when built).
- [ ] An equal, valid registered ID turns Save into Open or Propose a correction. "Create anyway" needs a reason, and a create in a coexistence or authored domain goes to a checker.
- [ ] A held new record is created from its task: alone in a consolidated domain, with a checker in a coexistence or authored one.
- [ ] A pin carries a reason and an expiry. Reinstating a retired record runs again the arrivals held for it.
- [ ] When a pin expires, the automated matcher recomputes the golden record's values, audited with its rule version.
- [ ] The source record view runs an exception again, as it is, edited, or waiving a rule with a reason, or ignores it.
- [ ] Capability [`CAP5.3`] Record authoring reads realized, and gap [`GAP3`] closes with this story.

## Definition of done

```bash
uv run ruff check . && uv run ruff format --check .
MDM_REQUIRE_POSTGRES=1 make test
make test-gui
python3 scripts/check_links.py && python3 scripts/check_model.py && python3 scripts/check_prose.py
python3 scripts/scan_public_safe.py --root . --terms .public-safe-terms.txt
```

- The figures marked adopted join the [declared capacity](../../5_technology/3_capacity-and-throughput.md#declared-capacity) as the story builds them.
- Kept true: `Realized by` of capability [`CAP5.3`] and of business service [`BSVC3`]; the `State` of actor [`ACT6`]; `Enforced by` of rules [`RULE2`] and [`RULE3`]; [gap [`GAP3`] No steward workbench](../../6_transition/1_target-state.md#gaps) marked `Closed — initiative 3`, and initiative 3 Delivered on the [sequence](../../6_transition/2_sequence.md#sequence).

## Out of scope

- File import of records — Release 2, initiative 5
- The model and source screens — initiative 4
