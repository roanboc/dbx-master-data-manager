"""Match scores and their explanation: the waterfall, the band and the counterfactuals."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from mdm.models.records import SourceKey

LEVEL_NULL = -1


class Band(StrEnum):
    AUTO = "auto"
    REVIEW = "review"
    DISTINCT = "distinct"


@dataclass(frozen=True, slots=True)
class Contribution:
    comparison: str
    level: int
    label: str
    weight: float  # log2(m/u); 0.0 for LEVEL_NULL


@dataclass(frozen=True, slots=True)
class Counterfactual:
    comparison: str
    from_level: int
    to_level: int
    to_label: str
    score: float
    band: Band
    direction: str  # up | down


@dataclass(frozen=True, slots=True)
class Explanation:
    prior: float
    contributions: tuple[Contribution, ...]
    hard_rule: str | None  # "must_link:person_ref"
    weight: float
    score: float
    band: Band
    #: 0–2: the smallest change up and/or down; none when a hard rule decided
    counterfactuals: tuple[Counterfactual, ...]
    signature: str
    rule_version: int

    def to_dict(self) -> dict[str, Any]:
        """A plain document for `candidate_pair.explanation` and `--json` output."""
        return {
            "prior": self.prior,
            "contributions": [
                {"comparison": c.comparison, "level": c.level, "label": c.label, "weight": c.weight}
                for c in self.contributions
            ],
            "hard_rule": self.hard_rule,
            "weight": self.weight,
            "score": self.score,
            "band": self.band.value,
            "counterfactuals": [
                {
                    "comparison": c.comparison,
                    "from_level": c.from_level,
                    "to_level": c.to_level,
                    "to_label": c.to_label,
                    "score": c.score,
                    "band": c.band.value,
                    "direction": c.direction,
                }
                for c in self.counterfactuals
            ],
            "signature": self.signature,
            "rule_version": self.rule_version,
        }

    @classmethod
    def from_dict(cls, doc: Mapping[str, Any]) -> Explanation:
        """The inverse of `to_dict`, for explanations read back from the store."""
        return cls(
            prior=float(doc["prior"]),
            contributions=tuple(
                Contribution(c["comparison"], int(c["level"]), c["label"], float(c["weight"]))
                for c in doc["contributions"]
            ),
            hard_rule=doc.get("hard_rule"),
            weight=float(doc["weight"]),
            score=float(doc["score"]),
            band=Band(doc["band"]),
            counterfactuals=tuple(
                Counterfactual(
                    c["comparison"],
                    int(c["from_level"]),
                    int(c["to_level"]),
                    c["to_label"],
                    float(c["score"]),
                    Band(c["band"]),
                    c["direction"],
                )
                for c in doc.get("counterfactuals", ())
            ),
            signature=doc["signature"],
            rule_version=int(doc["rule_version"]),
        )


@dataclass(frozen=True, slots=True)
class PairScore:
    left: SourceKey
    right: SourceKey
    explanation: Explanation


@dataclass(frozen=True, slots=True)
class GoldenCandidate:
    """The best-scoring member of one golden record against a record."""

    master_id: str
    best: PairScore
    members_scored: int
    blocked_by: str | None = None  # "cannot_link:person_ref" against any active member
