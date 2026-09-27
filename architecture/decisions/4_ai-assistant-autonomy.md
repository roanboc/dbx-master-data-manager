# Decision 4 — The AI assistant is advisory

_[← Decisions index](./README.md)_

**Status:** Accepted
**Date:** 2026-09-26
**Touches:** [actor [`ACT9`] AI assistant](../2_business/1_business-actors-and-roles.md#ai-assistant)

## Context

The product owner asked for innovative services assisted by artificial intelligence (AI), and [answer 2](../reference/2026-09-26-request-and-answers.md#answers) places the language-model services in Release 2. Language-model output is not deterministic, can leak personal data, and depends on endpoints that change. [Principle [`P6`] Arithmetic decides, the language model advises](../1_strategy/1_motivation.md) lets only deterministic services act.

## Options considered

| Option | Why not (or why) |
| ------ | ---------------- |
| Co-pilot: it stages changes into the undo tray for a one-key commit | It invites rubber-stamping, and a staged change is close to an action, against principle [`P6`] Arithmetic decides, the language model advises |
| Advisory: it suggests, labelled and cited, with a deterministic stub; it is off until an administrator enables it per master data domain, and its prompts carry masked values only | **Chosen.** Stewards get the drafts and answers, and a person decides every change |
| Autonomous, for example deciding review-band matches | Nobody could explain its decisions, against [principle [`P3`] Nothing unexplained](../1_strategy/1_motivation.md), and it breaks principle [`P6`] Arithmetic decides, the language model advises |

## Decision

The AI assistant is an AI actor that uses a language model, at the autonomy level **advisory**. It holds no decision right that commits. What it may and may not do is in [its profile](../2_business/1_business-actors-and-roles.md#ai-assistant).

## Consequences

- Every call is logged.
- Prompts carry identifiers (IDs), scores and masked values only; a person's name is masked like any other personal value, under [rule [`RULE10`] Personal values are held apart and redacted by rule](../2_business/5_domain-context-and-rules.md#business-rules).
- The stub is complete, and it is the default in the local mode.
- Nothing the assistant produces commits, so a steward stays accountable for every change.
- Release 1 ships only the plumbing and the stub.
