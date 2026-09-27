# Decision 21 — The workbench's actor is the user the platform forwards, and a persona only on a local store

_[← Decisions index](./README.md)_

**Status:** Proposed
**Date:** 2026-09-27
**Touches:** [authority and personas](../4_application/4_solution-design.md#authority-and-personas)

## Context

[Decision 13](./13_authority-and-personas.md) allows personas only on a local store. A Databricks App forwards the signed-in user in request headers, behind a proxy that authenticates every request. Inside an App, the software development kit's current user is the App's service principal, not the person. Until initiative 4 maps workspace groups to roles, every person on a shared store is a consumer. A web page the steward visits can post to, or frame, an address on the steward's own machine, and a renamed host can reach a loopback address.

## Options considered

| Option | Why not (or why) |
| ------ | ---------------- |
| A sign-in page of the workbench's own | A second identity beside the platform's |
| The software development kit's current user | Inside an App that is the service principal, not the person |
| Forwarded headers trusted wherever the workbench runs | Anyone can send them to a workbench on a laptop |
| On the platform, the user the App forwards, as a consumer until groups map to roles. On a local store, a persona chosen in the header, the data steward by default. `mdm ui` listens on a loopback address unless an App runs it, answers only its own host name, refuses posts from another site and is never framed | **Chosen.** One rule per store, and no persona reaches shared data, the network or another site |

## Decision

The actor of each request comes from the platform's forwarded user on the platform, and from the header's persona only on a local store.

## Consequences

- Forwarded headers are read only when a Databricks App variable is present, and a groups header is never trusted. A request inside an App that names no user is refused; it never shares one identity with others.
- The persona is a role name kept in the browser tab, so two tabs with the same persona act as the same steward.
- `MDM_ROLE` on a shared store stops `mdm ui` at start.
- The workbench refuses a post whose origin is another site, on every store. Dash posts JavaScript Object Notation (JSON), which a browser already checks with the server before a cross-site send.
- Every response carries a content security policy: scripts load only from the workbench and from the inline scripts Dash writes, each named by its hash, and no other site can frame it.
- The request log keeps a path only when it is one the workbench serves, with an ID or a source key where a record is named, so a name typed into the address bar never reaches it.
- Werkzeug's interactive debugger never runs, even with `mdm ui --dev`.
- Claims, staged decisions and audit rows name the actor.
- The deployed workbench is read-only and masked until initiative 4 maps groups to roles.
