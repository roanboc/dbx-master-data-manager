# Story 3.1 — Inbox, decide pane, undo tray and record view

_[← Scope document](../3_steward-workbench.md)_

**Goal:** a steward opens the workbench locally and decides review, held-update, possible-duplicate and orphan tasks one at a time, with the explanation beside them; undoes a decision within its window; and reads any golden or source record with its provenance, sources, timeline and relationships, masked by role.

## Context

- [Business process [`BPROC3`] Decide a steward task](../../2_business/3_business-processes.md#business-processes)
- [Rule [`RULE1`] Source traffic follows the source's policy](../../2_business/5_domain-context-and-rules.md#business-rules), rule [`RULE2`] A steward decides routine changes alone, rule [`RULE3`] Four eyes on what is hard to reverse, rule [`RULE11`] Least access by default
- [Actor [`ACT6`] Automated matcher](../../2_business/1_business-actors-and-roles.md#automated-matcher)
- [Glossary](../../2_business/5_domain-context-and-rules.md#glossary): Claim, Close call, Counterfactual, Escalate, Impact line, Inbox, Label, Provenance, Reveal, Service level, Snooze, Staged decision, Undo tray, Undo window
- [Data objects [`DOBJ3.2`] Steward task, [`DOBJ3.5`] Staged decision and [`DOBJ3.6`] Steward match label](../../3_information/2_data-objects.md#resolution-work); [data object [`DOBJ4.6`] Provenance](../../3_information/2_data-objects.md#published-master-data); [the stewardship flows](../../3_information/3_data-flows.md#stewardship-flows); [classification](../../3_information/4_data-architecture.md#classification)
- [Application services [`ASVC8`] Steward work, [`ASVC9`] Undo tray and [`ASVC10`] Record lookup](../../4_application/1_application-services.md#application-services)
- [Application components [`ACMP12`] Steward workbench, [`ACMP15`] Stewardship services and [`ACMP16`] Record reader](../../4_application/2_application-components.md#application-components)
- [Deciding a task](../../4_application/3_application-collaborations.md#deciding-a-task); [the workbench](../../4_application/4_solution-design.md#the-workbench); [authority and personas](../../4_application/4_solution-design.md#authority-and-personas)
- Decisions [18](../../decisions/18_workbench-shell-and-keys.md), [19](../../decisions/19_undo-tray.md), [20](../../decisions/20_masking-and-reveal-on-screen.md), [21](../../decisions/21_workbench-actor.md) and [22](../../decisions/22_labels-bind-the-matcher.md)
- [Declared capacity](../../5_technology/3_capacity-and-throughput.md#declared-capacity); [what runs on every change](../../5_technology/2_deployment.md#what-runs-on-every-change)

## Acceptance criteria

### Shell

- [x] `mdm ui` serves the workbench on `http://127.0.0.1:8050`. It listens on a loopback address unless a Databricks App runs it, refuses `MDM_ROLE` on a shared store, and answers only its own host name. `app.py` serves the same application for the platform.
- [x] No page can be framed by another site, a post from another site is refused, and no response is cached by the browser.
- [x] The header shows the product name, the entity selector (All, Person, Organisation), the tray with its count and countdown, the open breaches, the role, the persona switcher on a local store only, the engine and assistant badges ("DuckDB · stub") and the colour-scheme switch.
- [x] The navigation offers the Inbox with its views. A consumer has no Inbox and is told that records open from their links.
- [x] Light and dark schemes follow the browser at first, and the choice persists per browser.
- [x] axe-core finds no serious or critical violation of the Web Content Accessibility Guidelines (WCAG) 2.2 AA in continuous integration. It checks the inbox with the decide pane open and with a notification showing, the key help, the record view with a Why open under its value, and the source record view, in both schemes.

### Inbox and decide pane

- [x] The inbox lists open tasks by due time in keyed pages of 50, with Previous, Next and "51–100". Its views are My queue, with how many of its tasks the steward has claimed, Team, Breaching, Snoozed and Escalated, and it filters by task kind, each with its count. Counts are exact to 999, then "999+", and refresh every 30 seconds.
- [x] A row shows the kind, the entity, the subject's display name masked by role, the band and score, the suggestion, the reason in plain words, the service-level clock, the claimant and a staged marker. Columns neither sort nor filter, because a page is one slice of the queue.
- [x] The health strip shows the last arrival run, the open tasks, the breaching tasks, the tray and the last commit version.
- [x] J and K, or the arrow keys in the list, move the one selection through the inbox, and 1, 2 and 3 choose a candidate, without waiting for the server. Moving claims nothing; the first decision on a task claims it for 10 minutes.
- [x] With the single-key shortcuts off, Tab reaches each row, then leaves the list for its pages and the decide pane, and nothing traps the keyboard; Enter on a row opens its task.
- [x] A review task shows the arriving record and up to three candidates as columns, coloured and marked where they agree. It shows the match-weight waterfall of the chosen candidate to scale, from a zero line, with both band edges marked as scores; what would flip it, in plain words and with the score it would reach; the golden values that linking changes; and one impact line.
- [x] At 1440 × 900 a case with two candidates shows its evidence, what would flip it, the impact line and the actions without scrolling. On a narrow screen the pane stacks under the list with no sideways scroll.
- [x] A candidate that a cannot-link rule keeps apart is shown with the rule that blocks it, and cannot be linked.
- [x] L links to the chosen candidate; in a close call L moves to the choice until 1, 2 or 3 chooses one. N declines every candidate shown. A and R approve or reject a held update, critical or not. S and E open the snooze and escalation menus, and C claims. U undoes the selected task's staged decision, or else the last one. F, `.` and `?` open the full-width pane, the explanation and the key help. Decision keys reach the server one at a time, and a key pressed while the next case loads decides nothing.
- [x] A possible duplicate of two golden records offers "Keep apart", and says that merging needs a checker, which comes later. An orphan offers "Keep as it is", and says the same of retiring. A held new record, an exception and an unresolved reference each say in one line what they wait for.
- [x] An action the role cannot take shows why, in visible text.
- [x] Single-key shortcuts can be turned off in the key help. The listener ignores keys typed in a field, a list, a menu or a dialog, and Enter or Space on a focused button or link.
- [x] When a decision settles in the tray, its row is updated, and so is the pane if it shows that task. Nothing else on the screen is redrawn, and a revealed value or a chosen candidate stays.

### Undo tray

- [x] A decision stages only against the case the steward saw: if the record or the task changed after the pane was drawn, it is refused with a plain reason and the pane shows the case again. While a decision on a task waits in the tray, nobody can claim, snooze or escalate the task; Undo frees it.
- [x] Every decision stages in the tray for 60 seconds (`MDM_UNDO_SECONDS`), with a countdown and Undo. An undone decision never reaches the published tables.
- [x] After its window, the decision commits through the commit path under the steward's role, closes its task, records its label, and writes an audit change set even when it publishes nothing. A second flush commits nothing more.
- [x] If the record changed, the task closed or the target was merged while the decision waited, the decision fails with a plain reason and the task returns to the queue. This holds even when the change falls between the flush's check and its commit.
- [x] "Not a match" writes a label per declined candidate and hands the record back to arrival. Arrival settles it under its source's policy without those candidates, and names the staged decision in its evidence.
- [x] `mdm tray flush` flushes due decisions once, for a job on the platform.

### Record and source record

- [x] `/record/<master ID>` shows the display name masked by role, the master ID, the status and the IDs retired into it. Its tabs are Golden, Sources and cross-references, Timeline and Relationships.
- [x] Each golden value carries a provenance chip with its source, the strategy that decided and its age. The chip opens the Why under its value, with the survivorship sentence, the runners-up and the rule version.
- [x] A retired ID resolves to its survivor with a notice, a source key opens the source record view, and an unknown reference says so.
- [x] Both views only read. One line says that editing, pinning, detaching, merging and retiring come later.

### Personal data

- [x] Personal values are masked by role everywhere. A reveal asks one of four reasons and writes one access-log row per attribute and record. A consumer cannot reveal.
- [x] No personal value appears in an address, a component ID, a browser store, a tooltip built for another role, a notification, an error page or a log line.

## Definition of done

```bash
uv run ruff check . && uv run ruff format --check .
MDM_REQUIRE_POSTGRES=1 make test
make test-gui                      # the browser checks with axe; also their own CI job
python3 scripts/check_links.py && python3 scripts/check_model.py && python3 scripts/check_prose.py
python3 scripts/scan_public_safe.py --root . --terms .public-safe-terms.txt
make screenshots                   # docs/screenshots/, from the invented demo world with the stub
```

- Documents: [scope document 3](../3_steward-workbench.md) and its index row; the model changes of this story; decisions 18 to 22; `AGENTS.md` and `README.md`; the relationship rows.

## Out of scope

- Blind review and the quality breaker — [story 3.2](./2_matcher-checkpoint.md)
- Deciding by pattern — [story 3.3](./3_signature-batches.md)
- Explained ranks, breach escalation, routing again and "Take the next 25" — [story 3.4](./4_work-routing.md)
- Search, the command palette, a lookup by typed ID and a record as of a date — [story 3.5](./5_search-and-history.md)
- Detach, merge, unmerge, retire, critical edits and the cluster view — [story 3.6](./6_record-actions-maker-and-checker.md)
- Create, from a form or from a held new record; edit, pin, reinstate, run again and ignore — [story 3.7](./7_authoring-and-source-actions.md)
