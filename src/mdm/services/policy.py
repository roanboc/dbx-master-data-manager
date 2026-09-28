"""Rule RULE1, the source policies and the architecture styles, as pure decisions (owner: SERVICES, B.10).

Clauses (decision 16): `<source>.<policy>=<value>` for the five policy fields
(`new`, `update`, `critical_update`, `end_date`, `master_id`), and
`rule1:<case>` for the cases RULE1 makes automatic whatever the policy
(`auto_band`, `delete`, `retired_id`).

- A deletion marker detaches automatically (`rule1:delete`); an orphan becomes
  an `orphan` task.
- An update from a linked record commits automatically (`<source>.update=auto`)
  unless (a) a critical attribute changed and `critical_update` is `hold`, (b)
  an end-date attribute changed and `end_date` is `hold`, (c) it no longer
  scores auto against its golden record's other members, (d) it now scores auto
  against another golden record, or (e) its valid strong ID conflicts with
  another active member's — each a task with `hold = true`, the
  cross-reference kept, the golden record not recomputed.
- A `master_id` hint that is retired routes to its survivor
  (`rule1:retired_id`). One that is active links only when the source's
  `master_id` policy is `auto` (default `hold`: a `review` task) and no valid
  strong ID of the record conflicts with the golden record's active members
  (else an `exception` task); one the hub does not know is an `exception`
  task (`<source>.master_id=<value>`).
- One auto golden links (`link` mode) or consolidates (`consolidate` mode)
  (`rule1:auto_band`); two or more -> `possible_duplicate`; review band or a
  blocked auto candidate -> `review`.
- A new cluster is created in `consolidated` and `coexistence` styles unless
  `new` is `hold` (-> `held`), held in `authored` style, created without values
  in `registry` style (`<source>.new=<value>`); `identify` mode records scores
  only.

Every function also takes `record=`, the source record the plan is for (the
documented signatures name only its source's specification).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from mdm.engine.cluster import Resolution
from mdm.models.authority import RULE1_CASES
from mdm.models.entity_model import EntityModel, SourceSpec
from mdm.models.match import Band, Explanation
from mdm.models.records import SourceKey, SourceState
from mdm.models.safety import safe_detail
from mdm.services.support import changed_attributes, column_types, source_token, token

PLAN_KINDS = ("link", "consolidate", "create", "update", "detach", "task", "noop", "identify")
#: plan kinds whose effect is a commit
COMMITTING = frozenset({"link", "consolidate", "create", "update", "detach"})


@dataclass(frozen=True, slots=True)
class Plan:
    kind: str  # link | consolidate | create | update | detach | task | noop | identify
    source: SourceKey
    master_id: str | None
    task_kind: str | None
    reason: str  # a code
    clause: str  # the clause that allowed or held it (decision 16)
    evidence: Mapping[str, Any] = field(default_factory=dict)  # safe_detail only
    hold: bool = False
    # the best pair's comparison signature and rule version, for the quality samples (story 3.2); never in
    # the evidence, since a task's evidence refuses a signature's spaces and marks
    signature: str = ""
    rule_version: int | None = None


def rule1_clause(case: str) -> str:
    """`rule1:<case>`, case in RULE1_CASES."""
    if case not in RULE1_CASES:
        raise ValueError(f"not a RULE1 case: {case}")
    return f"rule1:{case}"


def policy_clause(source: SourceSpec, policy_field: str) -> str:
    """`<source>.<field>=<value>` from the source's policy."""
    return source.policy.clause(source.system, policy_field)


def _key(source: SourceSpec, record: SourceKey | None) -> SourceKey:
    return record if record is not None else SourceKey(source.system, "")


def decide_delete(
    model: EntityModel,
    source: SourceSpec,
    linked_to: str | None,
    remaining_members: int,
    *,
    record: SourceKey | None = None,
) -> list[Plan]:
    """A deletion marker: detach automatically; an orphan task when no member remains; nothing when unlinked."""
    key = _key(source, record)
    clause = rule1_clause("delete")
    if linked_to is None:
        return [Plan("noop", key, None, None, "not_linked", clause)]
    plans = [Plan("detach", key, linked_to, None, "delete_marker", clause, safe_detail(master_id=linked_to))]
    if remaining_members <= 0:
        plans.append(
            Plan(
                "task",
                key,
                linked_to,
                "orphan",
                "no_active_member",
                clause,
                safe_detail(master_id=linked_to, source=source_token(key)),
            )
        )
    return plans


def decide_update(
    model: EntityModel,
    source: SourceSpec,
    before: SourceState,
    after: SourceState,
    linked_to: str,
    rescore: Explanation | None,
    other_auto: Sequence[str],
    member_conflict: str | None,
    *,
    record: SourceKey | None = None,
) -> Plan:
    """An update from a linked record. `before.values` are its approved values, `after.values` the new ones.

    Held (a task, `hold = True`) under the source's policy or when the arithmetic no longer supports the
    link; otherwise an automatic update naming the clause that allowed it; `noop` when no value changed.
    """
    key = _key(source, record or after.source)
    changed = changed_attributes(column_types(model), before.values, after.values)
    critical = [a for a in changed if model.attribute(a).criticality == "critical"]
    ends = [a for a in changed if model.attribute(a).end_date]
    policy = source.policy

    def held(reason: str, clause: str, **detail: Any) -> Plan:
        evidence = safe_detail(master_id=linked_to, changed=changed, **detail)
        return Plan("task", key, linked_to, "held", reason, clause, evidence, hold=True)

    if not changed:
        return Plan("noop", key, linked_to, None, "unchanged", policy_clause(source, "update"))
    if policy.update == "hold":
        return held("update_held", policy_clause(source, "update"))
    if critical and policy.critical_update == "hold":
        return held("critical_update_held", policy_clause(source, "critical_update"), critical=critical)
    if ends and policy.end_date == "hold":
        return held("end_date_held", policy_clause(source, "end_date"), end_dates=ends)
    if rescore is not None and rescore.band != Band.AUTO:
        return held(
            "no_longer_auto",
            rule1_clause("auto_band"),
            score=round(rescore.score, 6),
            band=rescore.band.value,
        )
    if other_auto:
        return held("auto_elsewhere", rule1_clause("auto_band"), other=sorted(other_auto))
    if member_conflict:
        return held("member_conflict", rule1_clause("auto_band"), rule=token(member_conflict))
    if critical:
        clause = policy_clause(source, "critical_update")
    elif ends:
        clause = policy_clause(source, "end_date")
    else:
        clause = policy_clause(source, "update")
    return Plan("update", key, linked_to, None, "update", clause, safe_detail(changed=changed))


def _explained(resolution: Resolution) -> dict[str, Any]:
    best = resolution.best
    if best is None:
        return {}
    detail: dict[str, Any] = {"score": round(best.score, 6), "band": best.band.value}
    if best.hard_rule:
        detail["hard_rule"] = token(best.hard_rule)
    if best.counterfactuals:
        # what the decision hinges on, so the inbox can say so without scoring the pair again
        detail["hinge"] = token(best.counterfactuals[0].comparison)
    return detail


def decide_new(
    model: EntityModel,
    source: SourceSpec,
    resolution: Resolution,
    hint: str | None,
    retired: Mapping[str, str],
    *,
    record: SourceKey | None = None,
    hint_active: bool = False,
    hint_blocked: str | None = None,
    breaker: str | None = None,
) -> Plan:
    """A record not linked yet, from its clustering resolution (never a cluster-pair resolution).

    `retired` maps retired master IDs to their survivors; `hint_active` says the hint names an active
    golden record of this entity, and `hint_blocked` names the cannot-link rule a valid strong ID of the
    record breaks against that record's active members. `breaker` is the change set of the quality breaker's
    trip while the entity's automatic band is demoted: a review it causes names it in its evidence.
    """
    key = _key(source, record or (resolution.sources[0] if resolution.sources else None))
    mode = model.match.mode
    if mode == "identify":
        return Plan(
            "identify", key, None, None, "identify_mode", rule1_clause("auto_band"), _explained(resolution)
        )
    if hint:
        if hint in retired:
            survivor = retired[hint]
            return Plan(
                "link",
                key,
                survivor,
                None,
                "retired_id",
                rule1_clause("retired_id"),
                safe_detail(hint=token(hint), survivor=survivor),
            )
        clause = policy_clause(source, "master_id")
        if not hint_active:
            return Plan(
                "task", key, None, "exception", "unknown_master_id", clause, safe_detail(hint=token(hint))
            )
        if hint_blocked:
            return Plan(
                "task",
                key,
                None,
                "exception",
                "master_id_conflict",
                clause,
                safe_detail(hint=token(hint), master_ids=[hint], rule=token(hint_blocked)),
            )
        if source.policy.master_id == "auto":
            return Plan("link", key, hint, None, "master_id", clause, safe_detail(hint=token(hint)))
        return Plan(
            "task",
            key,
            None,
            "review",
            "master_id_held",
            clause,
            safe_detail(hint=token(hint), master_ids=[hint]),
        )
    evidence = _explained(resolution)
    best = resolution.best
    signature = best.signature if best is not None else ""
    rule_version = best.rule_version if best is not None else None
    if resolution.kind == "link":
        kind = "link" if mode == "link" else "consolidate"
        return Plan(
            kind,
            key,
            resolution.master_ids[0],
            None,
            resolution.reason or "auto_band",
            rule1_clause("auto_band"),
            evidence,
            signature=signature,
            rule_version=rule_version,
        )
    if resolution.kind == "ambiguous":
        return Plan(
            "task",
            key,
            None,
            "possible_duplicate",
            resolution.reason or "ambiguous_auto",
            rule1_clause("auto_band"),
            {**evidence, **safe_detail(master_ids=list(resolution.master_ids))},
            signature=signature,
            rule_version=rule_version,
        )
    if resolution.kind == "review":
        waits = safe_detail(breaker=breaker) if resolution.reason == "breaker_demoted" and breaker else {}
        return Plan(
            "task",
            key,
            None,
            "review",
            resolution.reason or "review_band",
            rule1_clause("auto_band"),
            {
                **evidence,
                **safe_detail(
                    master_ids=list(resolution.master_ids),
                    review_with=[source_token(s) for s in resolution.review_with],
                ),
                **waits,
            },
            signature=signature,
            rule_version=rule_version,
        )
    # a new cluster
    clause = policy_clause(source, "new")
    if model.style == "authored":
        return Plan("task", key, None, "held", "authored_style", clause, evidence, hold=True)
    if source.policy.new == "hold":
        return Plan("task", key, None, "held", "new_held", clause, evidence, hold=True)
    return Plan("create", key, None, None, "new_cluster", clause, evidence)
