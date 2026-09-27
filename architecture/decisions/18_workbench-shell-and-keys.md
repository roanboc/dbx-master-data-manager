# Decision 18 — The workbench is one Dash application driven by keys the steward can turn off

_[← Decisions index](./README.md)_

**Status:** Proposed
**Date:** 2026-09-27
**Touches:** [application component [`ACMP12`] Steward workbench](../4_application/2_application-components.md#application-components)

## Context

[Decision 5](./5_python-dash-and-typer.md) chose Dash for the interface. A steward decides many tasks in a row, and [driver [`DRV4`] Steward attention is the scarce resource](../1_strategy/1_motivation.md#drivers) makes every wasted move count. The workbench must meet the World Wide Web Consortium (W3C) Web Content Accessibility Guidelines (WCAG) 2.2 at level AA. Success criterion 2.1.4, Character Key Shortcuts, asks that single-key shortcuts can be turned off. The sibling products' shell, with a Mantine application shell, AG Grid and light and dark schemes, is known to the product owner's staff. AG Grid's server-side model is not in its free edition, and its infinite model reads by offset, which the declared capacity forbids. An exact count reads a whole table.

## Options considered

| Option | Why not (or why) |
| ------ | ---------------- |
| Mouse only, with buttons | Every decision costs several pointer moves, and stewards stall |
| Every key a round trip to the server that rebuilds the task on screen | Moving through the inbox waits on the server; fast keys queue, and a late answer can overwrite an early one |
| Pages from Dash's page registry | The siblings route with one callback and a module per page; one idiom serves three products |
| A small key listener in the browser: moving and choosing a candidate happen there, and a decision goes to the server one at a time. It yields to fields, lists, menus, dialogs and focused buttons, works on the inbox only, and can be switched off; every action also has a button | **Chosen.** Moving is instant, success criterion 2.1.4 is met by the switch, and keyboard users without shortcuts use the buttons |

## Decision

The workbench is one Dash application with the siblings' shell, one routing callback and a module per page. The inbox lists keyed pages of 50 tasks, with every count capped at 999. A listener in the browser turns keys into actions.

## Consequences

- Every component ID lives in `src/mdm/ui/ids.py`, and holds IDs and codes only.
- The grid neither takes cell focus nor sorts or filters, so its keys never fight the listener, and a page never pretends to be the whole queue.
- A task's case is prepared once per task version and role, and the next task's case while the steward reads.
- "?" lists the keys and holds the switch that turns single-key shortcuts off, kept per browser.
- The callbacks, pages and privacy checks run in every test run without a browser. The browser checks with axe-core run in a job of their own in continuous integration, where a pinned Chromium is installed, and on demand with `make test-gui`, outside `make check`.
