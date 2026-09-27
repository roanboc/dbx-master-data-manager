"""The vault and masked views: personal values live only here and in the value columns (owner: SERVICES, B.10).

Subject keys: `src:<system>:<key>` for source-level values, `mid:<master_id>`
for golden-level values no member holds (steward values). A stored document
holds `{"$vault": value_id}` in place of a personal value.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from mdm.backend.ddl import attribute_type
from mdm.backend.store import SqlStore
from mdm.models.authority import Actor
from mdm.models.entity_model import EntityModel
from mdm.models.errors import Forbidden, NotFound
from mdm.models.records import SourceKey
from mdm.services.authority import require
from mdm.services.registry import ModelRegistry
from mdm.services.support import VAULT_REF, token

MASK = "***"


def source_subject(source: SourceKey) -> str:
    """`src:<system>:<key>`."""
    return f"src:{source.system}:{source.key}"


def master_subject(master_id: str) -> str:
    """`mid:<master_id>`."""
    return f"mid:{master_id}"


def vault_ref(value_id: str | None) -> dict[str, str | None]:
    """The document form of a personal value: `{"$vault": value_id}`."""
    return {VAULT_REF: value_id}


def is_vault_ref(value: Any) -> bool:
    return isinstance(value, Mapping) and len(value) == 1 and VAULT_REF in value


def mask_value(attribute_logical: str, value: Any) -> Any:
    """The `mdm_read` rule for one personal value: text -> its first character + "***"; anything else -> None."""
    if value is None:
        return None
    if attribute_logical == "text" and isinstance(value, str):
        return value[:1] + MASK
    return None


class Vault:
    def __init__(self, store: SqlStore) -> None:
        self.store = store

    def protect(
        self, entity: str, subject_key: str, values: Mapping[str, Any], personal: Iterable[str]
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """(the document with {"$vault": id} in place of personal values, attribute -> value ID)."""
        [(doc, ids)] = self.protect_many(entity, [(subject_key, values)], personal)
        return doc, ids

    def protect_many(
        self,
        entity: str,
        documents: Sequence[tuple[str, Mapping[str, Any]]],
        personal: Iterable[str],
    ) -> list[tuple[dict[str, Any], dict[str, str]]]:
        """`protect` for many (subject key, values) documents with one vault write."""
        names = set(personal)
        rows: list[tuple[str, str, str, Any]] = []
        slots: list[tuple[int, str]] = []
        for index, (subject, values) in enumerate(documents):
            for name in sorted(names):
                value = values.get(name)
                if value is None:
                    continue
                rows.append((entity, subject, name, value))
                slots.append((index, name))
        ids = self.store.vault_put(rows) if rows else []
        out: list[tuple[dict[str, Any], dict[str, str]]] = [(dict(values), {}) for _, values in documents]
        for (index, name), value_id in zip(slots, ids, strict=True):
            doc, id_map = out[index]
            doc[name] = vault_ref(value_id)
            id_map[name] = value_id
        return out

    def put(self, rows: Sequence[tuple[str, str, str, Any]]) -> list[str]:
        """(entity, subject_key, attribute, value) -> value IDs, reusing an equal live value."""
        return self.store.vault_put(rows) if rows else []

    def resolve(self, doc: Any) -> Any:
        """The document with every vault reference replaced by its value (None once redacted)."""
        found: list[str] = []
        _collect_refs(doc, found)
        values = self.store.vault_get(sorted(set(found))) if found else {}
        return _replace_refs(doc, values)

    def redact(
        self,
        subject_keys: Sequence[str],
        *,
        owner: Actor,
        administrator: Actor,
        confirmation: str,
        reason: str,
    ) -> list[str]:
        """RULE5: owner.role == "data_owner", administrator.role == "administrator", different names,
        confirmation == f"REDACT {len(subject_keys)}", a reason; else `Forbidden`. Writes redaction_log with both.
        No command-line entry until initiative 4's erasure workflow."""
        require(owner, "redact")
        if administrator.role != "administrator":
            raise Forbidden("administrator_required", action="redact")
        if administrator.name == owner.name:
            raise Forbidden("checker_is_maker", action="redact")
        if not subject_keys:
            raise Forbidden("nothing_to_redact", action="redact")
        if confirmation != f"REDACT {len(subject_keys)}":
            raise Forbidden("confirmation_mismatch", action="redact", expected=len(subject_keys))
        if not reason or not reason.strip():
            raise Forbidden("reason_required", action="redact")
        with self.store.transaction():
            value_ids = self.store.vault_redact(list(subject_keys))
            self.store.append_redaction(
                list(subject_keys),
                value_ids,
                owner,
                administrator,
                f"RULE5: {owner.role}; checker {administrator.role}",
                reason,
            )
        return value_ids


def _collect_refs(doc: Any, found: list[str]) -> None:
    if is_vault_ref(doc):
        value_id = doc[VAULT_REF]
        if isinstance(value_id, str):
            found.append(value_id)
        return
    if isinstance(doc, Mapping):
        for value in doc.values():
            _collect_refs(value, found)
    elif isinstance(doc, (list, tuple)):
        for value in doc:
            _collect_refs(value, found)


def _replace_refs(doc: Any, values: Mapping[str, Any]) -> Any:
    if is_vault_ref(doc):
        return values.get(doc[VAULT_REF]) if isinstance(doc[VAULT_REF], str) else None
    if isinstance(doc, Mapping):
        return {k: _replace_refs(v, values) for k, v in doc.items()}
    if isinstance(doc, (list, tuple)):
        return [_replace_refs(v, values) for v in doc]
    return doc


class PrivacyService:
    def __init__(self, store: SqlStore, registry: ModelRegistry, vault: Vault) -> None:
        self.store = store
        self.registry = registry
        self.vault = vault

    def masked(self, model: EntityModel, values: Mapping[str, Any]) -> dict[str, Any]:
        """The same rule as the `mdm_read` views: personal text -> first character + "***", other personal -> None."""
        personal = {
            a.name: attribute_type(a.type, a.repeating) for a in model.column_attributes() if a.personal
        }
        return {
            name: mask_value(personal[name], value) if name in personal else value
            for name, value in values.items()
        }

    def record_view(
        self, entity: str, master_id: str, *, actor: Actor, reveal: Sequence[str] = (), reason: str = ""
    ) -> dict[str, Any]:
        """Golden row, members and provenance, masked except the revealed attributes; a reveal needs
        ACTIONS["reveal"] and a reason, and writes one access_log row per attribute."""
        require(actor, "read")
        model = self.registry.published(entity)
        revealed = list(dict.fromkeys(reveal))
        personal = set(model.personal_attributes())
        if revealed:
            require(actor, "reveal")
            if not reason or not reason.strip():
                raise Forbidden("reason_required", action="reveal")
            for name in revealed:
                model.attribute(name)  # NotFound for an unknown attribute
        row = self.store.golden(entity, [master_id]).get(master_id)
        if row is None:
            raise NotFound("unknown_master_id", entity=entity, master_id=token(master_id))
        values = self.masked(model, row.values)
        for name in revealed:
            if name in row.values:
                values[name] = row.values[name]
        members = self.store.members(entity, [master_id], 1000).get(master_id, [])
        provenance = self.store.provenance(entity, [master_id]).get(master_id, {})
        shown: dict[str, Any] = {}
        for name, entry in sorted(provenance.items()):
            if name in personal and name not in revealed:
                shown[name] = _masked_provenance(entry)
            elif name in personal:
                shown[name] = self.vault.resolve(entry)
            else:
                shown[name] = entry
        for name in revealed:
            self.store.append_access(
                actor, "reveal", entity, master_id, name, reason, {"attribute": token(name)}
            )
        return {
            "entity": entity,
            "master_id": row.master_id,
            "status": row.status,
            "survivor_id": row.survivor_id,
            "values": values,
            "revealed": revealed,
            "members": [m.text() for m in members],
            "provenance": shown,
            "commit_version": row.commit_version,
            "row_version": row.row_version,
        }


def _masked_provenance(entry: Any) -> Any:
    """A provenance entry with every value left out: the winner's and runners-up's sources only."""
    if not isinstance(entry, Mapping):
        return None
    out = {k: v for k, v in entry.items() if k not in ("winner", "runners_up")}
    winner = entry.get("winner")
    if isinstance(winner, Mapping):
        out["winner"] = {"source": winner.get("source")}
    runners = entry.get("runners_up")
    if isinstance(runners, list):
        out["runners_up"] = [{"source": r.get("source")} for r in runners if isinstance(r, Mapping)]
    return out
