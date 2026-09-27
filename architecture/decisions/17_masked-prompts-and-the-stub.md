# Decision 17 — Prompts carry masked values only, and the stub answers until an entity enables an endpoint

_[← Decisions index](./README.md)_

**Status:** Proposed
**Date:** 2026-09-27
**Touches:** [application service [`ASVC7`] Assistance plumbing](../4_application/1_application-services.md#application-services)

## Context

[Rule [`RULE10`] Personal values are held apart and redacted by rule](../2_business/5_domain-context-and-rules.md#business-rules) lets a prompt to a language model carry masked values only. [Principle [`P6`] Arithmetic decides, the language model advises](../1_strategy/1_motivation.md#principles) keeps the language model to suggestions. [Answer 2](../reference/2026-09-26-request-and-answers.md#answers) puts language-model services in Release 2, and the plumbing and a complete stub in Release 1. [Goal [`G7`] Personal data protected by role](../1_strategy/1_motivation.md#goals) applies to every prompt.

## Options considered

| Option | Why not (or why) |
| ------ | ---------------- |
| Send clear values to an endpoint under a data agreement | Personal data leaves the store, and no such agreement exists |
| No plumbing until Release 2 | Answer 2 asks for the plumbing and the stub now |
| Masked prompts, the stub by default, and an endpoint only when an entity model enables it and a setting names it | **Chosen.** The plumbing runs and is tested today, and no personal value can reach a provider |

## Decision

Every prompt is masked, the stub answers by default, and an endpoint is called only when an entity model enables it and `MDM_AGENT_ENDPOINT` names it.

## Consequences

- A check that no personal value is in the prompt runs before every call.
- Every call writes an access-log row with the provider the prompt goes to, the purpose and a hash of the prompt, never the prompt itself. The row is written before the call, so no prompt leaves the process unrecorded.
- Nothing a provider returns is committed.
- An error from the endpoint falls back to the stub.
- Release 2 revisits which masked fields a prompt may carry.
