# Story 3.4 — Work routing

_[← Scope document](../3_steward-workbench.md)_

**Goal:** the work router explains every rank and escalates breached tasks on record, and a coordinating steward routes work again, as decision 2 decides.

## Context

- [Actor [`ACT7`] Work router](../../2_business/1_business-actors-and-roles.md#work-router); [decision 2](../../decisions/2_work-router-autonomy.md)
- [Role [`ROLE3`] Coordinating steward](../../2_business/1_business-actors-and-roles.md#roles)
- [Glossary](../../2_business/5_domain-context-and-rules.md#glossary): Claim, Escalate, Service level, Inbox
- [Data object [`DOBJ3.2`] Steward task](../../3_information/2_data-objects.md#resolution-work)
- [Application service [`ASVC8`] Steward work](../../4_application/1_application-services.md#application-services)
- [Story 3.1](./1_inbox-decide-tray-and-record.md)

## Acceptance criteria

- [ ] Each task's rank shows its reasons: service-level urgency now, and dependent consumers and health once initiative 4 supplies them.
- [ ] A breached task escalates to the coordinating steward automatically, and the escalation is logged.
- [ ] A coordinating steward assigns a task to a steward, or returns it to the team.
- [ ] "Take the next 25" claims the next tasks in rank order.
- [ ] Actor [`ACT7`] Work router reads as existing, with its checkpoint.

## Definition of done

```bash
uv run ruff check . && uv run ruff format --check .
MDM_REQUIRE_POSTGRES=1 make test
make test-gui
python3 scripts/check_links.py && python3 scripts/check_model.py && python3 scripts/check_prose.py
python3 scripts/scan_public_safe.py --root . --terms .public-safe-terms.txt
```

- Kept true: the `State` of actor [`ACT7`]; `Held in` of [business object [`BOBJ8`] Steward task](../../2_business/4_business-objects.md#business-objects); the service levels in the [declared capacity](../../5_technology/3_capacity-and-throughput.md#declared-capacity).

## Out of scope

- Search and the command palette — [story 3.5](./5_search-and-history.md)
- Ranking by dependent consumers and health — initiative 4, with the consumer registry and scorecards
- Work balancing — Release 2, initiative 5
