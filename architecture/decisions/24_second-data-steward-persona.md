# Decision 24 — The local mode offers a second data-steward persona, so a batch's maker, its second steward and its blind reviewer are three stewards

_[← Decisions index](./README.md)_

**Status:** Accepted
**Date:** 2026-09-29
**Touches:** [authority and personas](../4_application/4_solution-design.md#authority-and-personas)

## Context

[Decision 21](./21_workbench-actor.md) keeps the persona in the browser tab as a role name, so the local mode holds one steward per role. [Decision 23](./23_batches-in-the-undo-tray.md) has a second steward, who is not the maker, confirm a batch above 250 decisions, and neither of them may answer the batch's blind reviews. Only the data steward and the coordinating steward decide tasks, so locally nobody was left to answer them. On a shared store every person is a consumer until initiative 4 maps workspace groups to roles, so no batch runs there yet.

## Options considered

| Option | Why not (or why) |
| ------ | ---------------- |
| Leave the gap open until initiative 4 | A confirmed batch's blind reviews stay unanswered locally, so story 3.3 cannot be shown or checked from start to end |
| Let the maker or the second steward answer the blind reviews locally | Blind review would check a steward's links with the same steward locally, and with another steward everywhere else |
| A persona per person, named freely in the header | A free name would reach the browser tab and the audit, where every persona is a code |
| A second data-steward persona, `data_steward_2`, in the data steward's role, refused on a shared store like every persona | **Chosen.** Three stewards locally, with no new role, and nothing changes on a shared store |

## Decision

The local mode offers seven personas: every role once, and a second data steward, `data_steward_2`, who acts as `persona:data_steward_2` in the data steward's role. The product owner chose this option on 29 September 2026 ([the answer](../reference/2026-09-29-persona-and-split-key-answers.md#the-answers)).

## Consequences

- The persona the tab keeps is a code, a role name or `data_steward_2`. Two tabs with the same persona act as the same steward, as decision 21 says, and the two data-steward personas act as two stewards.
- `--as data_steward_2`, `MDM_ROLE=data_steward_2` and "Data steward 2" in the persona menu choose it. A shared store refuses it, as it refuses every persona.
- Locally, a batch above 250 decisions takes one steward persona as its maker and another as its second steward, and a third answers its blind reviews.
- Claims, staged decisions and audit rows name `persona:data_steward_2`, so its work is told apart from the data steward's.
