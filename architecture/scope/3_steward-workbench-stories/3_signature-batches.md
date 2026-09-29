# Story 3.3 — Signature batches with forced samples

_[← Scope document](../3_steward-workbench.md)_

**Goal:** alike review tasks are grouped by signature and decided together after a unanimous forced sample, as rule [`RULE7`] Bulk decisions pass a forced sample asks.

## Context

- [Rule [`RULE7`] Bulk decisions pass a forced sample](../../2_business/5_domain-context-and-rules.md#business-rules) and rule [`RULE3`] Four eyes on what is hard to reverse, for large bulk changes
- [Actor [`ACT8`] Quality breaker](../../2_business/1_business-actors-and-roles.md#quality-breaker)
- [Glossary](../../2_business/5_domain-context-and-rules.md#glossary): Signature, Forced sample, Blind review, Staged decision
- [Business process [`BPROC3`] Decide a steward task](../../2_business/3_business-processes.md#business-processes)
- [Application service [`ASVC9`] Undo tray](../../4_application/1_application-services.md#application-services); [decision 19](../../decisions/19_undo-tray.md)
- [Declared capacity](../../5_technology/3_capacity-and-throughput.md#declared-capacity), for the commit chunk
- [Story 3.1](./1_inbox-decide-tray-and-record.md) and [story 3.2](./2_matcher-checkpoint.md)

## Acceptance criteria

- [x] G groups review tasks by signature, with the count, the label history and the blind-review agreement.
- [x] The forced sample draws 5 tasks plus 1 per 150 (Blueprint §3; adopted when built), stratified. One disagreement splits the disagreeing pattern off, and bulk decisions unlock only after a unanimous sample.
  Adopted: bulk decisions are links; "Not a match", keep apart and merge are decided one by one. Answered by the product owner on 2026-09-28: a disagreement splits off the reviews whose record shares the disagreeing record's value on the comparison the steward names ([the answer](../../reference/2026-09-28-forced-sample-split-answer.md#the-answer)). Adopted, from Blueprint §3 flow (c): the steward who decides the disagreeing sample review names the comparison with that decision.
- [x] A batch shows every row's change, and commits in chunks of at most 500 published rows under one batch ID, with Stop. Above 250 decisions a second steward confirms (Blueprint §3; adopted when built).
- [x] The batch waits in the undo tray as one entry, and 2% of it (Blueprint §3; adopted when built) goes to blind review.
  Adopted: 2% of the links the batch stages, rounded up and at least one, drawn at staging; a drawn review that fails before its chunk passes its draw to the next planned review. Adopted: the local mode offers a second data-steward persona, so a batch's maker, its second steward and whoever answers its blind reviews are three stewards ([decision 24](../../decisions/24_second-data-steward-persona.md)).
- [x] The breaker can withdraw a signature's bulk rights, and only a data owner restores them.
- [x] Undoing a whole batch after commit is compensation, a new change set per chunk.
  Adopted: on the command line, within 30 days of the batch's last chunk; the compensation is itself a batch through the undo tray, and keeps any link changed since.
- [x] Rule [`RULE7`] reads enforced.

## Definition of done

```bash
uv run ruff check . && uv run ruff format --check .
MDM_REQUIRE_POSTGRES=1 make test
make test-gui
python3 scripts/check_links.py && python3 scripts/check_model.py && python3 scripts/check_prose.py
python3 scripts/scan_public_safe.py --root . --terms .public-safe-terms.txt
```

- The figures marked adopted join the [declared capacity](../../5_technology/3_capacity-and-throughput.md#declared-capacity) as the story builds them.
- Kept true: `Enforced by` of rule [`RULE7`]; `Realized by` of [capability [`CAP5.1`] Steward work and pattern decisions](../../1_strategy/2_capabilities-and-resources.md#capabilities) and of [value stream stage [`VS1.3`] Resolve](../../1_strategy/3_value-stream.md#value-stream).

## Out of scope

- Explained ranks and "Take the next 25" — [story 3.4](./4_work-routing.md)
- Undoing a batch from the audit screen — initiative 4
- Automation grants that widen the automatic band — Release 2, initiative 5
