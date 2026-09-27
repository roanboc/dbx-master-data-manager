# Decision 12 — History refers to personal values in a vault, so they can be redacted

_[← Decisions index](./README.md)_

**Status:** Proposed
**Date:** 2026-09-27
**Touches:** [classification](../3_information/4_data-architecture.md#classification)

## Context

[Principle [`P4`] Every decision is reversible and recorded](../1_strategy/1_motivation.md#principles) says history only grows. [Rule [`RULE10`] Personal values are held apart and redacted by rule](../2_business/5_domain-context-and-rules.md#business-rules) asks for personal values to be held apart and redacted by rule. Person is mastered from Release 1 ([answer 1](../reference/2026-09-26-request-and-answers.md#answers)), so history holds personal values from the start.

## Options considered

| Option | Why not (or why) |
| ------ | ---------------- |
| Keep the values in history, and delete rows on erasure | It breaks [principle [`P4`] Every decision is reversible and recorded](../1_strategy/1_motivation.md#principles) and loses the audit |
| Encrypt per data subject, and destroy the key on erasure | Key management the platform does not offer the app |
| History holds references into a vault | **Chosen.** Source versions, the change log's before and after, and provenance hold `{"$vault": id}`. A redaction empties the value, keeps the row and is recorded |

## Decision

History refers to personal values by vault reference, and a redaction empties the value in the vault alone.

## Consequences

- The published golden rows keep personal values, because listening systems need them. An erasure, from initiative 4, redacts them with a new commit version.
- The working state and the landing rows hold values in clear, and the erasure report names them.
- A source record's standardised values are vaulted once, at arrival. A golden value's history refers to its winning member's value, so each value is held once per data subject and attribute.
- Details, messages and log lines never carry a personal value.
- A redaction needs a data owner, a different administrator and a typed confirmation. It is the only update the store lets reach the vault, and no statement deletes from it.
- A new model version may not make a personal attribute non-personal: that would open the masked view, stop vaulting its values and put them into prompts.
