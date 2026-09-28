# Story 3.2 — The automated matcher's checkpoint

_[← Scope document](../3_steward-workbench.md)_

**Goal:** blind review samples the automated matcher's decisions and the stewards', and the quality breaker demotes the automatic band when agreement falls or arrivals spike, so the matcher may meet real data. **It must merge before any real data loads.**

## Context

- [Actor [`ACT6`] Automated matcher](../../2_business/1_business-actors-and-roles.md#automated-matcher) and [actor [`ACT8`] Quality breaker](../../2_business/1_business-actors-and-roles.md#quality-breaker)
- [Decision 1](../../decisions/1_automated-matcher-autonomy.md) and [decision 3](../../decisions/3_quality-breaker-autonomy.md)
- [Glossary](../../2_business/5_domain-context-and-rules.md#glossary): Blind review, Breaker, Band, Signature
- [Business process [`BPROC3`] Decide a steward task](../../2_business/3_business-processes.md#business-processes)
- [Data object [`DOBJ3.6`] Steward match label](../../3_information/2_data-objects.md#resolution-work)
- [Story 3.1](./1_inbox-decide-tray-and-record.md), which built the decide pane this story reuses
- [Gap [`GAP3`] No steward workbench](../../6_transition/1_target-state.md#gaps); the [stop check](../3_steward-workbench.md#stop-check) of scope document 3

## Acceptance criteria

- [x] A share of automated links and creates, and of committed steward decisions, opens a `quality_sample` task. The share is a setting, 2% by default (Blueprint §3; adopted when built), chosen by a hash of the record so both engines agree.
  Sampled (adopted): the matcher's links and creates, and a steward's link, keep apart, and "Not a match" that declined a golden record; approving or rejecting a held update and keeping an orphan are not sampled, because blind review cannot ask them again without showing the answer.
- [x] A quality sample is decided in the decide pane without showing the first decision or the suggestion.
- [x] Agreement is kept per entity, signature and band. A disagreement opens a review of the original decision.
- [x] The quality breaker demotes an entity's automatic band when agreement falls below a threshold, or when arrivals per hour exceed a multiple of the trailing mean. It never widens a band, and each trip is logged with its reason.
- [x] While the band is demoted, arrivals in the automatic band become review tasks naming the breaker. Only a data owner restores the band, with `mdm breaker restore`, on record.
- [x] The thresholds are settings until initiative 4's governance policy holds them.
- [x] Tests run on both engines. The checkpoint in the automated matcher's profile reads as existing, and gap [`GAP3`] narrows.

## Definition of done

```bash
uv run ruff check . && uv run ruff format --check .
MDM_REQUIRE_POSTGRES=1 make test
make test-gui
python3 scripts/check_links.py && python3 scripts/check_model.py && python3 scripts/check_prose.py
python3 scripts/scan_public_safe.py --root . --terms .public-safe-terms.txt
```

- Kept true: the `State` of actors [`ACT6`] and [`ACT8`]; the consequences of decisions 1 and 3; [gap [`GAP3`] No steward workbench](../../6_transition/1_target-state.md#gaps) in the target state, and initiative 3's row in the [sequence](../../6_transition/2_sequence.md#sequence).
- New data objects for the quality sample and the breaker's state, each [classified](../../3_information/4_data-architecture.md#classification), with their relationship rows.
- The figures marked adopted join the [declared capacity](../../5_technology/3_capacity-and-throughput.md#declared-capacity) as the story builds them.

## Out of scope

- Withdrawing bulk rights from a signature — [story 3.3](./3_signature-batches.md)
- Agreement figures on the operations board — initiative 4
- Keeping the blind reviewer from looking the record up elsewhere: the decide pane offers no way to its placement, and the reviewer is trusted not to search for it
