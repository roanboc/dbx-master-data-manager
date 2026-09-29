"""Authority: who the actor is, what the role allows, and what allowed a change set (owner: SERVICES, B.10).

Local mode (decision 13): the actor is a persona (`--as PERSONA`, else
`MDM_ROLE`, else `data_owner`), `Actor("persona:<persona>", "person", role,
persona=True)`, each persona acting in one role (`PERSONAS`: every role once, and
`data_steward_2`, a second data steward). Otherwise a persona asked for raises `PlatformRefused`, and the
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
    PERSONAS,
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
#: the change-set actions of a signature batch's chunks (story 3.3), whose batch the check reads
BATCH_ACTIONS = frozenset({"batch_link", "batch_compensate"})
DEFAULT_PERSONA = "data_owner"
#: the workbench's persona when none is asked for (adopted): the command line keeps the data owner
WORKBENCH_PERSONA = "data_steward"
CLAUSES_MARK = "; clauses "


def persona_actor(code: str) -> Actor:
    """The persona `code` names (`PERSONAS`): `Actor("persona:<code>", "person", <its role>, persona=True)`;
    `Forbidden(unknown_role)` for any other code. Only the local mode calls it."""
    role = PERSONAS.get(code)
    if role is None:
        raise Forbidden("unknown_role", role=token(code))
    return Actor(f"{PERSONA_PREFIX}{code}", "person", role, persona=True)


def require(actor: Actor, action: str) -> None:
    """`Forbidden` ("forbidden action=publish_model role=consumer") unless `ACTIONS[action]` allows the actor.

    An automated actor passes only the actions whose set names it: its name is checked for every action, never
    its role, so the automated matcher takes only "arrival" and the quality breaker only "trip_breaker". A
    person passes by role.
    """
    allowed = ACTIONS.get(action)
    if allowed is None:
        raise Forbidden("unknown_action", action=token(action))
    if actor.kind == "automated":
        if actor.name not in allowed:
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
            return persona_actor(asked or DEFAULT_PERSONA)
        if asked:
            raise PlatformRefused("persona_refused", role=token(asked))
        return Actor(self._user_name(), "person", "consumer")

    def actor_for_request(self, *, persona: str | None, forwarded_user: str | None) -> Actor:
        """The workbench's actor for one request (decision 21).

        On a local store: the persona asked for, else `MDM_ROLE`, else the data steward (the command line
        keeps the data owner); an unknown persona is `Forbidden(unknown_role)`. Two tabs with the same persona
        are the same actor; the two data-steward personas are two. Inside a Databricks App on a shared store: the user the platform forwards, as a
        consumer until initiative 4 maps groups to roles, and `PlatformRefused(no_forwarded_user)` when it
        names nobody; a persona is never honoured there. On a shared
        store outside an App (a laptop pointed at Lakebase): the signed-in user as a consumer.
        """
        if self.settings.local_mode:
            return persona_actor((persona or "").strip() or self.settings.role.strip() or WORKBENCH_PERSONA)
        if self.settings.in_databricks_app:
            name = (forwarded_user or "").strip()
            if not name:
                # never one shared identity for every request the platform did not name
                raise PlatformRefused("no_forwarded_user")
            return Actor(name, "person", "consumer")
        return self.resolve_actor(None)

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
        if cs.action in BATCH_ACTIONS:
            self._check_batch(cs)
        del in_transaction  # the same checks hold before and inside the transaction

    def _check_batch(self, cs: ChangeSet) -> None:
        """A signature batch's chunk (story 3.3): the batch its evidence names exists and is the actor's
        (`Forbidden(batch_unknown)`), and above `batch_checker_above` decisions the chunk names the batch's
        recorded second steward, a person other than the maker whose role may confirm a batch
        (`checker_required`, `checker_is_maker`, `checker_not_recorded`). RULE3's large bulk change."""
        named = (cs.evidence or {}).get("batch_id")
        batch = self.store.batches([named]).get(named) if isinstance(named, str) else None
        if batch is None or batch.maker != cs.actor.name:
            raise Forbidden("batch_unknown", action=cs.action)
        if batch.decisions <= self.settings.batch_checker_above:
            return
        checker = cs.checker
        if checker is None or checker.kind != "person":
            raise Forbidden("checker_required", action=cs.action)
        if checker.name == batch.maker:
            raise Forbidden("checker_is_maker", action=cs.action)
        if checker.name != batch.checker:
            raise Forbidden("checker_not_recorded", action=cs.action)
        require(checker, "confirm_batch")

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
