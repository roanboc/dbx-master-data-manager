"""Authority: who the actor is, what the role allows, and what allowed a change set (owner: SERVICES, B.10).

Local mode (decision 13): the actor is a persona (`--as ROLE`, else
`MDM_ROLE`, else `data_owner`), `Actor("persona:<role>", "person", role,
persona=True)`. Otherwise a persona asked for raises `PlatformRefused`, and the
actor is the SDK's current user as a consumer (groups: initiative 4), or the
database user as a consumer without the SDK.

A change set is checked twice (B.7 steps 0 and 2): before the commit
transaction, to fail fast, and again inside it under the commit lock, so a rule
version unpublished meanwhile refuses the commit.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from typing import TYPE_CHECKING, Any

from mdm.backend.store import SqlStore
from mdm.config import Settings
from mdm.models.authority import (
    ACTIONS,
    AUTOMATIC_ITEMS,
    NEEDS_CHECKER,
    NEVER_AUTOMATIC,
    ROLES,
    RULE1_CASES,
    Actor,
    Authority,
)
from mdm.models.changes import ChangeSet, item_clause, item_kind
from mdm.models.entity_model import POLICY_FIELDS, EntityModel
from mdm.models.errors import Forbidden, NotFound, PlatformRefused
from mdm.services.support import token

if TYPE_CHECKING:
    from mdm.services.registry import ModelRegistry

__all__ = [
    "RULE1_CASES",
    "AuthorityService",
    "bootstrap_authority",
    "clauses_of",
    "require",
    "versions_text",
]

PERSONA_PREFIX = "persona:"
DEFAULT_PERSONA = "data_owner"
CLAUSES_MARK = "; clauses "


def require(actor: Actor, action: str) -> None:
    """`Forbidden` ("forbidden action=publish_model role=consumer") unless `ACTIONS[action]` allows the actor.

    For "arrival" the automated actor's name is checked, for every other action the role.
    """
    allowed = ACTIONS.get(action)
    if allowed is None:
        raise Forbidden("unknown_action", action=token(action))
    if action == "arrival":
        if actor.kind != "automated" or actor.name not in allowed:
            raise Forbidden("forbidden", action=action, role=token(actor.role))
        return
    if actor.role not in allowed:
        raise Forbidden("forbidden", action=action, role=token(actor.role))


def bootstrap_authority(entity: str, kind: str, version: int) -> Authority:
    """Authority("bootstrap", "first publication of <entity> <kind> v<version>; no golden record; the dry run
    arrives in initiative 4"): flagged in the audit (RULE4)."""
    return Authority(
        "bootstrap",
        f"first publication of {entity} {kind} v{version}; no golden record; the dry run arrives in initiative 4",
    )


def versions_text(model: EntityModel) -> str:
    """`person: model v1, match v1, survivorship v1, validation v1`: the rule versions behind an automated commit."""
    return (
        f"{model.entity}: model v{model.version}, match v{model.match.version}, "
        f"survivorship v{model.survivorship.version}, validation v{model.validation.version}"
    )


def clauses_of(cs: ChangeSet) -> list[str]:
    """The distinct clauses the change set's items carry, sorted."""
    return sorted({item_clause(item) for item in cs.items if item_clause(item)})


def role_authority(actor: Actor, checker: Actor | None = None) -> Authority:
    """Authority("role", "data_steward; checker data_owner") for a person's change set."""
    ref = actor.role if checker is None else f"{actor.role}; checker {checker.role}"
    return Authority("role", ref)


class AuthorityService:
    def __init__(
        self,
        settings: Settings,
        store: SqlStore,
        registry: ModelRegistry,
        workspace: Callable[[], Any] | None = None,
    ) -> None:
        """`workspace` returns an SDK WorkspaceClient (lazy; a fake in tests); None uses the SDK when installed."""
        self.settings = settings
        self.store = store
        self.registry = registry
        self._workspace = workspace

    # ------------------------------------------------------------------ the actor

    def resolve_actor(self, as_role: str | None = None) -> Actor:
        """A persona in the local mode; otherwise the signed-in user as a consumer, never a persona."""
        asked = (as_role or "").strip() or self.settings.role.strip()
        if self.settings.local_mode:
            role = asked or DEFAULT_PERSONA
            if role not in ROLES:
                raise Forbidden("unknown_role", role=token(role))
            return Actor(f"{PERSONA_PREFIX}{role}", "person", role, persona=True)
        if asked:
            raise PlatformRefused("persona_refused", role=token(asked))
        return Actor(self._user_name(), "person", "consumer")

    def _user_name(self) -> str:
        workspace = self._workspace
        if workspace is None:
            try:
                from databricks.sdk import WorkspaceClient  # the SDK is optional
            except ImportError:
                return self._database_user()
            workspace = WorkspaceClient
        try:
            name = workspace().current_user.me().user_name
        except Exception:  # a failed lookup gives the consumer role (RULE11), never more
            return self._database_user()
        return str(name) if name else self._database_user()

    def _database_user(self) -> str:
        return self.settings.pg_user or "database-user"

    # ------------------------------------------------------------------ actions

    def require(self, actor: Actor, action: str) -> None:
        """`require(actor, action)`."""
        require(actor, action)

    def check(self, cs: ChangeSet, *, in_transaction: bool = False) -> None:
        """B.7 steps 0 and 2: rule versions still published, every automated item's clause still held by the
        published model (or a `rule1:` case), the role allows the action, an automated actor carries only
        AUTOMATIC_ITEMS, a NEEDS_CHECKER action has a checker other than the maker whose role allows it.
        `Forbidden` otherwise."""
        actor = cs.actor
        require(actor, cs.action)
        if actor.kind == "automated":
            if cs.action in NEVER_AUTOMATIC:
                raise Forbidden("never_automatic", action=token(cs.action))
            model = self.registry.published(cs.entity)
            prefix = versions_text(model)
            ref = cs.authority.ref
            if cs.authority.kind != "rule_version" or not (
                ref == prefix or ref.startswith(prefix + CLAUSES_MARK)
            ):
                raise Forbidden("rule_version_not_published", entity=cs.entity)
            named = set(ref[len(prefix) + len(CLAUSES_MARK) :].split(", ")) if ref != prefix else set()
            for item in cs.items:
                kind = item_kind(item)
                if kind not in AUTOMATIC_ITEMS:
                    raise Forbidden("not_automatic", action=token(cs.action), item=kind)
                clause = item_clause(item)
                if not clause:
                    raise Forbidden("missing_clause", item=kind)
                if clause not in named:
                    raise Forbidden("clause_not_in_authority", clause=token(clause))
                if not self.clause_held(model, clause):
                    raise Forbidden("clause_not_held", clause=token(clause))
        else:
            if cs.authority.kind not in ("role", "bootstrap"):
                raise Forbidden("bad_authority", kind=token(cs.authority.kind))
        if cs.action in NEEDS_CHECKER:
            checker = cs.checker
            if checker is None:
                raise Forbidden("checker_required", action=cs.action)
            if checker.name == actor.name or checker.kind != "person":
                raise Forbidden("checker_is_maker", action=cs.action)
            require(checker, cs.action)
        del in_transaction  # the same checks hold before and inside the transaction

    @staticmethod
    def clause_held(model: EntityModel, clause: str) -> bool:
        """True for a `rule1:` case, or for `<source>.<field>=<value>` that the model's source policy holds."""
        if clause.startswith("rule1:"):
            return clause[len("rule1:") :] in RULE1_CASES
        system, dot, rest = clause.partition(".")
        policy_field, eq, value = rest.partition("=")
        if not dot or not eq or policy_field not in POLICY_FIELDS:
            return False
        try:
            source = model.source(system)
        except NotFound:
            return False
        return getattr(source.policy, policy_field) == value == "auto"

    def automated_authority(self, model: EntityModel, clauses: Collection[str]) -> Authority:
        """Authority("rule_version", f"{entity}: model v{m}, match v{a}, survivorship v{s}, validation v{q};
        clauses {sorted, comma-joined}")."""
        named = sorted({c for c in clauses if c})
        ref = versions_text(model)
        if named:
            ref += CLAUSES_MARK + ", ".join(named)
        return Authority("rule_version", ref)

    def bootstrap_authority(self, entity: str, kind: str, version: int) -> Authority:
        return bootstrap_authority(entity, kind, version)
