# Story 3.6 — Record actions with maker and checker

_[← Scope document](../3_steward-workbench.md)_

**Goal:** a steward detaches a record through the undo tray, and merge, unmerge, retire and critical edits pass a checker who is not the maker and sees what the maker saw, as rule [`RULE3`] Four eyes on what is hard to reverse asks.

## Context

- [Rule [`RULE2`] A steward decides routine changes alone](../../2_business/5_domain-context-and-rules.md#business-rules) and rule [`RULE3`] Four eyes on what is hard to reverse
- [Glossary](../../2_business/5_domain-context-and-rules.md#glossary): Checker, Maker, Detach, Merge, Unmerge, Retire, Impact line, Critical attribute
- [Business service [`BSVC4`] Change approval](../../2_business/2_business-services.md#business-services)
- [Application service [`ASVC5`] Record lifecycle](../../4_application/1_application-services.md#application-services) and application service [`ASVC9`] Undo tray
- [Deciding a task](../../4_application/3_application-collaborations.md#deciding-a-task)
- [Decision 19](../../decisions/19_undo-tray.md) and [decision 22](../../decisions/22_labels-bind-the-matcher.md)
- [Story 3.1](./1_inbox-decide-tray-and-record.md)

## Acceptance criteria

- [ ] The record view's Sources tab offers Detach, with its preview and impact line. The detach waits in the undo tray, writes a not-a-match label and hands the record back to arrival.
- [ ] A held update whose link is in doubt offers Detach too.
- [ ] A possible duplicate, or the record view, proposes a merge as a change set with its impact line. A "Needs my approval" view lists what waits for a checker.
- [ ] The checker sees the maker's frozen case, preview and impact line. Nobody approves their own change set, and the approved change set waits in the undo tray.
- [ ] The cluster view lists the pairwise scores of every member pair of a cluster, each with its band. A graph is added only if a browser check shows a steward finds the weakest link faster with it than with the list.
- [ ] Unmerge, retire and critical edits follow the same path. A create proposed in a coexistence or authored domain reaches the "Needs my approval" view, and a test on both engines commits one only after a checker approves it.
- [ ] Business service [`BSVC4`] Change approval serves the data owner and the coordinating steward.

## Definition of done

```bash
uv run ruff check . && uv run ruff format --check .
MDM_REQUIRE_POSTGRES=1 make test
make test-gui
python3 scripts/check_links.py && python3 scripts/check_model.py && python3 scripts/check_prose.py
python3 scripts/scan_public_safe.py --root . --terms .public-safe-terms.txt
```

- Kept true: `Enforced by` of rules [`RULE2`] and [`RULE3`]; `Realized by` of business service [`BSVC4`] and of [capability [`CAP5.2`] Change approval and undo](../../1_strategy/2_capabilities-and-resources.md#capabilities); the `Realized by` cells of [application service [`ASVC5`] Record lifecycle](../../4_application/1_application-services.md#application-services) and business service [`BSVC4`], which name detach and makers and checkers on screen.

## Out of scope

- Creating and editing records — [story 3.7](./7_authoring-and-source-actions.md)
- The approval matrix screen and the audit screens — initiative 4
- Hierarchies — Release 2, initiative 5
