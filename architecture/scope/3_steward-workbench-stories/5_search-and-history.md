# Story 3.5 — Search, the command palette and the record as of a date

_[← Scope document](../3_steward-workbench.md)_

**Goal:** golden records, held arrivals and tasks are found by name, master ID, retired ID or source key, and a record reads as it was on any date.

## Context

- [Business service [`BSVC1`] Record lookup and history](../../2_business/2_business-services.md#business-services)
- [Application service [`ASVC10`] Record lookup](../../4_application/1_application-services.md#application-services); [application component [`ACMP16`] Record reader](../../4_application/2_application-components.md#application-components)
- [Rule [`RULE11`] Least access by default](../../2_business/5_domain-context-and-rules.md#business-rules); [decision 20](../../decisions/20_masking-and-reveal-on-screen.md)
- [Classification](../../3_information/4_data-architecture.md#classification), for what a search token may hold
- [Declared capacity](../../5_technology/3_capacity-and-throughput.md#declared-capacity)
- [Story 3.1](./1_inbox-decide-tray-and-record.md)

## Acceptance criteria

- [ ] Ctrl-K opens the command palette. Typed text never reaches an address, a log line or browser storage.
- [ ] Search tolerates typing errors and is faceted. It retrieves through stored tokens, ranked by the one shared scorer, and a token found in more than 50,000 records (Blueprint §3; adopted when built) is never used alone.
- [ ] A typed master ID, retired ID or source key opens its record directly.
- [ ] Results are masked by role. Selected results open in a grid, for later bulk edits.
- [ ] The timeline shows the record as of a date, and compares two dates.
- [ ] Business service [`BSVC1`] Record lookup and history reads realized.

## Definition of done

```bash
uv run ruff check . && uv run ruff format --check .
MDM_REQUIRE_POSTGRES=1 make test
make test-gui
python3 scripts/check_links.py && python3 scripts/check_model.py && python3 scripts/check_prose.py
python3 scripts/scan_public_safe.py --root . --terms .public-safe-terms.txt
```

- The figures marked adopted join the [declared capacity](../../5_technology/3_capacity-and-throughput.md#declared-capacity) as the story builds them.
- Kept true: `Realized by` of business service [`BSVC1`] and of [capability [`CAP7.1`] Record search, view and history](../../1_strategy/2_capabilities-and-resources.md#capabilities); a search-token data object, classified, if one is added.

## Out of scope

- Bulk edits from the result grid — [story 3.6](./6_record-actions-maker-and-checker.md) and [story 3.7](./7_authoring-and-source-actions.md)
- Quality and consumers on the record — initiative 4
