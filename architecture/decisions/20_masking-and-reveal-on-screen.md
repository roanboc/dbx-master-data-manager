# Decision 20 — The workbench shows personal values masked by role, and a reveal asks a reason and is logged per attribute

_[← Decisions index](./README.md)_

**Status:** Accepted
**Date:** 2026-09-27
**Touches:** [application service [`ASVC6`] Privacy protection](../4_application/1_application-services.md#application-services)

## Context

[Rule [`RULE11`] Least access by default](../2_business/5_domain-context-and-rules.md#business-rules) masks every personal value unless a role allows more, and [goal [`G7`] Personal data protected by role](../1_strategy/1_motivation.md#goals) asks the same of every screen. Stewards decide Person matches, and some cases need the values. The command line already masks and logs reveals. A browser keeps what a page stores, and a free-text reason is a place a name can be typed.

## Options considered

| Option | Why not (or why) |
| ------ | ---------------- |
| Stewards see every value in clear | Against rule [`RULE11`] Least access by default; a steward's screen is a copy nobody logged |
| Masked everywhere, with no reveal | Twins and typing errors cannot be told apart from first letters alone |
| A reveal that lasts the whole session | One reason would cover records the steward never meant to open |
| Masked by role, with agreement shown by colour and mark; a reveal per task or record names one of four reasons, writes one access-log row per attribute and record, and lasts until the pane or page changes | **Chosen.** Most decisions need no reveal, every reveal is on record, and the log holds codes only |

## Decision

Values on screen are masked by role. A reveal is explicit, reasoned, logged and short-lived.

## Consequences

- A reveal names one of the four reason codes that [application service [`ASVC6`] Privacy protection](../4_application/1_application-services.md#application-services) lists, never free text.
- A value is rendered on the server into the page. It is kept in no browser store, address, component ID, tooltip, notification, log line or browser cache.
- A failure is logged by its type only, never with its message.
- A consumer never reveals.
- Agreement colours tell whether two masked values agree, so only the roles that may see tasks see a task's case.
- Opening a case is not logged, because it cannot be probed with chosen inputs, unlike the match test.
- Prepared cases are masked and never hold a revealed value.
