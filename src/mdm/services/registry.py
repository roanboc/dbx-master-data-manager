"""The model registry: entity models and rule sets, drafted and published (owner: SERVICES, B.10).

Publishing is refused ("re-evaluating existing records needs an approved dry
run, from initiative 4") whenever the entity holds a golden record; otherwise
it is published under a bootstrap authority, flagged in the audit (RULE4),
retires the previous version, creates or extends the entity table
(`ensure_entity_tables`) and writes an audit change set with no commit version.

A model version is stored as its whole document; its match, survivorship and
validation rule sets are stored beside it as their own versions, drafted with
it (`rule_set.model_version`). The published model is the published model
document with the published rule set of each kind, and every rule set carries
its stored version number, so an explanation's `rule_version` is the match rule
set's version.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from mdm.backend.store import SqlStore
from mdm.engine.score import CompiledRules, compile_rules
from mdm.models.authority import Actor, Authority
from mdm.models.canonical import utcnow
from mdm.models.changes import new_change_set
from mdm.models.entity_model import EntityModel, MatchRules, check_compatible
from mdm.models.errors import Forbidden, ModelError, NotFound
from mdm.services.authority import bootstrap_authority, require
from mdm.services.support import plain, token

RULE_KINDS = ("match", "survivorship", "validation")


def _rule_doc(model: EntityModel, kind: str) -> dict[str, Any]:
    return plain(getattr(model, kind))


def _with_versions(model: EntityModel, version: int, rules: Mapping[str, int]) -> EntityModel:
    return replace(
        model,
        version=version,
        match=replace(model.match, version=rules["match"]),
        survivorship=replace(model.survivorship, version=rules["survivorship"]),
        validation=replace(model.validation, version=rules["validation"]),
    )


class ModelRegistry:
    def __init__(self, store: SqlStore, clock: Callable[[], datetime] = utcnow) -> None:
        self.store = store
        self.clock = clock
        self._models: dict[tuple[str, int, int, int, int], EntityModel] = {}
        self._compiled: dict[tuple[str, int, int], CompiledRules] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ loading drafts

    def load_file(self, path: Path, actor: Actor) -> EntityModel:
        """A YAML model as the next draft version; its match, survivorship and validation rule sets are
        drafted with it. Requires `load_model`."""
        with Path(path).open(encoding="utf-8") as handle:
            doc = yaml.safe_load(handle)
        return self.load_doc(doc, actor)

    def load_doc(self, doc: Mapping[str, Any], actor: Actor) -> EntityModel:
        require(actor, "load_model")
        parsed = EntityModel.from_dict(doc)
        entity = parsed.entity
        with self.store.transaction():
            versions = self.store.entity_model_versions(entity)
            version = max((v for v, _, _ in versions), default=0) + 1
            rules = {kind: self.store.next_rule_set_version(entity, kind) for kind in RULE_KINDS}
            model = _with_versions(parsed, version, rules)
            self.store.save_entity_model(entity, version, "draft", plain(model.to_dict()), actor.name)
            for kind in RULE_KINDS:
                self.store.save_rule_set(
                    entity, kind, rules[kind], "draft", _rule_doc(model, kind), version, actor.name
                )
            self._audit(
                entity,
                "load_model",
                actor,
                Authority("role", actor.role),
                evidence={"model_version": version, **{kind: rules[kind] for kind in RULE_KINDS}},
            )
        return model

    # ------------------------------------------------------------------ publishing

    def _refuse_when_golden_records(self, entity: str, kind: str) -> None:
        try:
            held = self.store.golden_page(entity, None, 1)
        except NotFound:
            return
        if held:
            raise Forbidden(
                "publish_refused",
                entity=entity,
                kind=kind,
                reason="golden_records_exist",
                needs="approved-dry-run",
                arrives_in="initiative-4",
            )

    def publish(self, entity: str, version: int | None, actor: Actor) -> EntityModel:
        """Publish a draft model (the latest when `version` is None) and the rule sets drafted with it.

        Requires `publish_model`; compatible with the published version (`check_compatible`); refused once
        the entity holds a golden record; under `bootstrap_authority`.
        """
        require(actor, "publish_model")
        versions = self.store.entity_model_versions(entity)
        if not versions:
            raise NotFound("unknown_entity", entity=token(entity))
        if version is None:
            drafts = [v for v, status, _ in versions if status == "draft"]
            if not drafts:
                raise NotFound("no_draft", entity=token(entity))
            version = max(drafts)
        status = {v: s for v, s, _ in versions}.get(version)
        if status is None:
            raise NotFound("unknown_version", entity=token(entity), version=version)
        if status == "published":
            return self.published(entity)
        self._refuse_when_golden_records(entity, "model")
        candidate = self.version(entity, version)
        current = self._published_version(versions)
        if current is not None:
            problems = check_compatible(self.version(entity, current), candidate)
            if problems:
                raise ModelError(problems, code="incompatible_model")
        authority = bootstrap_authority(entity, "model", version)
        with self.store.transaction():
            if current is not None:
                self.store.set_entity_model_status(entity, current, "retired", actor.name)
            self.store.set_entity_model_status(entity, version, "published", actor.name)
            published_rules: dict[str, int] = {}
            for kind in RULE_KINDS:
                drafted = [
                    v
                    for v, s, model_version, _ in self.store.rule_set_versions(entity, kind)
                    if model_version == version and s == "draft"
                ]
                if drafted:
                    self._publish_rule_version(entity, kind, max(drafted), actor)
                    published_rules[kind] = max(drafted)
            self.store.ensure_entity_tables(candidate)
            self._audit(
                entity,
                "publish_model",
                actor,
                authority,
                evidence={"model_version": version, "flag": "bootstrap", **published_rules},
            )
        self._forget(entity)
        return self.published(entity)

    def _publish_rule_version(self, entity: str, kind: str, version: int, actor: Actor) -> None:
        for v, s, _, _ in self.store.rule_set_versions(entity, kind):
            if s == "published" and v != version:
                self.store.set_rule_set_status(entity, kind, v, "retired", actor.name)
        self.store.set_rule_set_status(entity, kind, version, "published", actor.name)

    def publish_rules(self, entity: str, kind: str, version: int, actor: Actor) -> None:
        """Requires `publish_rules`; the same refusal and bootstrap rule as `publish`."""
        require(actor, "publish_rules")
        if kind not in RULE_KINDS:
            raise NotFound("unknown_rule_kind", kind=token(kind))
        found = {v: s for v, s, _, _ in self.store.rule_set_versions(entity, kind)}
        if version not in found:
            raise NotFound("unknown_version", entity=token(entity), kind=kind, version=version)
        if found[version] == "published":
            return
        self._refuse_when_golden_records(entity, kind)
        model = self.published(entity)
        # the rule set must read against the published model's attributes
        self._assemble(model.to_dict(), model.version, {**self._rule_versions(model), kind: version})
        with self.store.transaction():
            self._publish_rule_version(entity, kind, version, actor)
            self._audit(
                entity,
                "publish_rules",
                actor,
                bootstrap_authority(entity, kind, version),
                evidence={"kind": kind, "version": version, "flag": "bootstrap"},
            )
        self._forget(entity)

    # ------------------------------------------------------------------ reading

    @staticmethod
    def _published_version(versions: list[tuple[int, str, datetime]]) -> int | None:
        published = [v for v, s, _ in versions if s == "published"]
        return max(published) if published else None

    @staticmethod
    def _rule_versions(model: EntityModel) -> dict[str, int]:
        return {
            "match": model.match.version,
            "survivorship": model.survivorship.version,
            "validation": model.validation.version,
        }

    def _assemble(self, model_doc: Mapping[str, Any], version: int, rules: Mapping[str, int]) -> EntityModel:
        entity = str(model_doc.get("entity"))
        key = (entity, version, rules["match"], rules["survivorship"], rules["validation"])
        with self._lock:
            cached = self._models.get(key)
        if cached is not None:
            return cached
        doc = dict(model_doc)
        for kind in RULE_KINDS:
            found = self.store.rule_set_doc(entity, kind, rules[kind])
            if found is None:
                raise NotFound("unknown_rule_set", entity=token(entity), kind=kind, version=rules[kind])
            doc[kind] = found[1]
        model = _with_versions(EntityModel.from_dict(doc), version, rules)
        with self._lock:
            self._models[key] = model
        return model

    def published(self, entity: str) -> EntityModel:
        """The published model with its published rule sets; cached per version. `NotFound` when none."""
        version = self._published_version(self.store.entity_model_versions(entity))
        if version is None:
            raise NotFound("no_published_model", entity=token(entity))
        rules: dict[str, int] = {}
        for kind in RULE_KINDS:
            published = [v for v, s, _, _ in self.store.rule_set_versions(entity, kind) if s == "published"]
            if not published:
                raise NotFound("no_published_rule_set", entity=token(entity), kind=kind)
            rules[kind] = max(published)
        cached_key = (entity, version, rules["match"], rules["survivorship"], rules["validation"])
        with self._lock:
            cached = self._models.get(cached_key)
        if cached is not None:
            return cached
        found = self.store.entity_model_doc(entity, version)
        if found is None:
            raise NotFound("no_published_model", entity=token(entity))
        return self._assemble(found[1], version, rules)

    def version(self, entity: str, version: int) -> EntityModel:
        """A model version with the rule sets drafted with it (the latest of each kind)."""
        found = self.store.entity_model_doc(entity, version)
        if found is None:
            raise NotFound("unknown_version", entity=token(entity), version=version)
        rules: dict[str, int] = {}
        for kind in RULE_KINDS:
            drafted = [v for v, _, mv, _ in self.store.rule_set_versions(entity, kind) if mv == version]
            if not drafted:
                raise NotFound("unknown_rule_set", entity=token(entity), kind=kind, version=version)
            rules[kind] = max(drafted)
        return self._assemble(found[1], version, rules)

    def published_entities(self) -> list[str]:
        return self.store.published_entities()

    def versions(self, entity: str) -> list[tuple[int, str, datetime]]:
        """(version, status, created_at)."""
        return self.store.entity_model_versions(entity)

    def match_rules(self, entity: str, version: int | None = None) -> MatchRules:
        """A match rule set: the published one when `version` is None."""
        model = self.published(entity)
        if version is None or version == model.match.version:
            return model.match
        return self._assemble(
            model.to_dict(), model.version, {**self._rule_versions(model), "match": version}
        ).match

    def compiled(self, entity: str, version: int | None = None) -> CompiledRules:
        """`compile_rules` of a match rule set; cached per rule version."""
        model = self.published(entity)
        rules = self.match_rules(entity, version)
        key = (entity, model.version, rules.version)
        with self._lock:
            cached = self._compiled.get(key)
        if cached is not None:
            return cached
        compiled = compile_rules(rules, model)
        with self._lock:
            self._compiled[key] = compiled
        return compiled

    def save_match_draft(self, entity: str, rules: MatchRules, actor: Actor) -> int:
        """A new draft match rule set (from estimation); returns its version."""
        require(actor, "estimate")
        model = self.published(entity)
        with self.store.transaction():
            version = self.store.next_rule_set_version(entity, "match")
            drafted = replace(rules, version=version)
            # the draft must read against the published model's attributes before it is stored
            doc = model.to_dict()
            doc["match"] = plain(drafted)
            EntityModel.from_dict(doc)
            self.store.save_rule_set(
                entity, "match", version, "draft", plain(drafted), model.version, actor.name
            )
            self._audit(
                entity,
                "estimate",
                actor,
                Authority("role", actor.role),
                evidence={"kind": "match", "version": version, "model_version": model.version},
            )
        return version

    # ------------------------------------------------------------------ helpers

    def _forget(self, entity: str) -> None:
        with self._lock:
            for key in [k for k in self._models if k[0] == entity]:
                del self._models[key]
            for key in [k for k in self._compiled if k[0] == entity]:
                del self._compiled[key]

    def _audit(
        self, entity: str, action: str, actor: Actor, authority: Authority, *, evidence: Mapping[str, Any]
    ) -> None:
        """A governance event: an audit change set with no commit version and no items."""
        cs = new_change_set(
            entity,
            action,
            actor,
            authority,
            (),
            planning_version=self.store.last_commit_version(),
            reason=action,
            evidence=dict(evidence),
        )
        self.store.append_change_set(cs, None, 0)
