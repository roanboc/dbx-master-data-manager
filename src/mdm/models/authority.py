"""Who may do what: roles, actors, the authority a change set carries, and the action table."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

ROLES = (
    "data_owner",
    "data_steward",
    "coordinating_steward",
    "technical_steward",
    "consumer",
    "administrator",
)
ACTOR_KINDS = ("person", "automated")
AUTHORITY_KINDS = ("rule_version", "role", "bootstrap")


@dataclass(frozen=True, slots=True)
class Actor:
    name: str
    kind: str  # person | automated
    role: str
    persona: bool = False


AUTOMATED_MATCHER = Actor(name="automated-matcher", kind="automated", role="data_steward")


@dataclass(frozen=True, slots=True)
class Authority:
    """What allowed a change set.

    rule_version: "person: model v1, match v1, survivorship v1, validation v1; clauses crm.update=auto, hr.new=auto"
    role:         "data_steward; checker data_owner"
    bootstrap:    "first publication of person model v1; no golden record; the dry run arrives in initiative 4"
    """

    kind: str
    ref: str


_STEWARDS = frozenset({"data_steward", "coordinating_steward"})
#: who may see the inbox: a consumer has none, and a data owner or technical steward reads without deciding
_TASK_READERS = frozenset({"data_steward", "coordinating_steward", "data_owner", "technical_steward"})

ACTIONS: Mapping[str, frozenset[str]] = {
    "read": frozenset(ROLES),
    "reveal": frozenset({"data_steward", "coordinating_steward", "data_owner"}),
    # an automated actor's NAME, not a role: only the matcher commits arrivals
    "arrival": frozenset({AUTOMATED_MATCHER.name}),
    "run_arrival": frozenset({"technical_steward", "data_owner", "administrator"}),
    "link": _STEWARDS,
    "detach": _STEWARDS,
    "reinstate": _STEWARDS,
    "merge": _STEWARDS,  # + a checker, not the maker
    "unmerge": _STEWARDS,
    "retire": _STEWARDS,
    "load_model": frozenset({"technical_steward", "data_owner"}),
    "publish_model": frozenset({"data_owner"}),
    "estimate": frozenset({"technical_steward", "data_owner"}),
    "publish_rules": frozenset({"data_owner"}),
    "profile": frozenset({"technical_steward", "data_owner", "data_steward"}),
    # the per-comparison levels could tell a masked value: never a consumer, and every call is logged
    "match_test": frozenset({"data_steward", "coordinating_steward", "technical_steward", "data_owner"}),
    "load_code_lists": frozenset({"technical_steward", "data_owner"}),
    "redact": frozenset({"data_owner"}),  # + an administrator as checker, and a typed confirmation (RULE5)
    # the steward workbench (initiative 3)
    "view_tasks": _TASK_READERS,
    "work_tasks": _STEWARDS,  # claim, release, snooze, escalate, stage, undo
    "not_a_match": _STEWARDS,
    "keep_apart": _STEWARDS,
    "keep_orphan": _STEWARDS,
    # a steward's decision alone: it releases a value the source asserted and its policy held (RULE2)
    "approve_update": _STEWARDS,
    "reject_update": _STEWARDS,
    "flush_tray": frozenset(ROLES) - {"consumer"},
}
NEVER_AUTOMATIC = frozenset(
    {
        "merge",
        "unmerge",
        "retire",
        "purge",
        "erase",
        "redact",
        "publish_model",
        "publish_rules",
        "change_policy",
        "grant",
    }
)
NEEDS_CHECKER = frozenset({"merge", "unmerge", "retire", "redact"})
#: the cases rule RULE1 makes automatic whatever the source policy; a clause cites one as `rule1:<case>`
RULE1_CASES = ("auto_band", "delete", "retired_id")
#: item kinds an automated change set may carry
AUTOMATIC_ITEMS = frozenset({"create", "update", "link", "detach", "relationship"})
#: what a person reads for each role
ROLE_LABELS: Mapping[str, str] = {
    "data_owner": "Data owner",
    "data_steward": "Data steward",
    "coordinating_steward": "Coordinating steward",
    "technical_steward": "Technical steward",
    "consumer": "Consumer",
    "administrator": "Administrator",
}


def allowed(actor: Actor, action: str) -> bool:
    """Whether the action table lets `actor` take `action`: `services.authority.require` without raising, so
    the workbench can show what a role cannot do, with the reason. For "arrival" the automated actor's name is
    checked, for every other action the role; an unknown action is never allowed."""
    permitted = ACTIONS.get(action)
    if permitted is None:
        return False
    if action == "arrival":
        return actor.kind == "automated" and actor.name in permitted
    return actor.role in permitted
