# Decision 10 — Matching scores pairs by Fellegi–Sunter arithmetic in Python, with weights estimated from the data

_[← Decisions index](./README.md)_

**Status:** Accepted
**Date:** 2026-09-27
**Touches:** [matching](../4_application/4_solution-design.md#matching)

## Context

[Principle [`P3`] Nothing unexplained](../1_strategy/1_motivation.md#principles) asks every score to show its weights. [Principle [`P6`] Arithmetic decides, the language model advises](../1_strategy/1_motivation.md#principles) asks arithmetic to decide. [Principle [`P7`] One engine, one answer](../1_strategy/1_motivation.md#principles) asks for the same scores on both engines. The two engines offer different fuzzy and phonetic functions: Postgres has no Jaro-Winkler, and DuckDB has no phonetic codes.

## Options considered

| Option | Why not (or why) |
| ------ | ---------------- |
| Scoring written in SQL for each engine | The functions differ between engines, so the scores would too |
| An external record-linkage library that drives SQL | The same dialect problem, and it creates functions inside the database |
| A trained classifier | Its scores cannot be explained weight by weight |
| Fixed deterministic rules only | No probabilities, so no bands and no automatic band to govern |
| Fellegi–Sunter arithmetic in Python, with rapidfuzz and jellyfish, and blocking keys computed in Python and stored as text | **Chosen.** One implementation gives identical candidates and scores on both engines, and every score is a sum a person can read |

## Decision

The matching engine scores each candidate pair in Python, as a prior plus one weight per comparison. It estimates those weights from the data, into a draft rule set.

## Consequences

- Candidates and scores are identical on both engines.
- Each score explains itself exactly: the prior, one weight per comparison and the hard rules applied. It also shows the smallest change that would move it a band up or down.
- Scoring uses precomputed weight tables, and builds a full explanation only for pairs at or above the lower band.
- Estimation runs expectation maximisation for each blocking pass, with that pass's own key comparisons held fixed. It takes m from pairs sharing a valid strong registered ID where there are enough of them. Expectation maximisation then estimates only that ID's own m, reading the other comparisons with the m already taken, because the ID alone cannot tell its m from the share of matches.
- It takes u for an exact level from value frequencies, and for the other levels from pairs chosen by hashing keys. It records the global prior apart from each pass's in-block proportion.
- Estimation refuses to save a draft that did not converge. A draft's publication over existing records needs the dry run of initiative 4.
- Term-frequency adjustment waits for initiative 4.
