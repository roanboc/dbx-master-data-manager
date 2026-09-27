"""Code-list copies: loaded from YAML in the local mode, from the Reference Data Manager from initiative 4
(owner: SERVICES, B.10).

Each load is a new version (decision: a versioned copy pinned per validation
run); a rule result records the version it was checked against. The hub never
writes a code list back.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

import yaml

from mdm.backend.store import SqlStore
from mdm.models.authority import Actor, Authority
from mdm.models.changes import new_change_set
from mdm.models.entity_model import EntityModel
from mdm.models.errors import ModelError
from mdm.services.authority import require
from mdm.services.support import token


def code_lists_of(model: EntityModel) -> dict[str, str]:
    """Code list name -> the first attribute or rule that reads it, for the lists a model's validation uses."""
    out: dict[str, str] = {}
    for attribute in model.attributes:
        if attribute.code_list:
            out.setdefault(attribute.code_list, attribute.name)
    for rule in model.validation.rules:
        name = rule.params.get("code_list") if rule.kind == "code_list" else None
        if isinstance(name, str):
            out.setdefault(name, rule.attribute)
    return out


class CodeListService:
    def __init__(self, store: SqlStore) -> None:
        self.store = store

    def load_file(self, path: Path, actor: Actor) -> tuple[str, int]:
        """One `name/source/values` YAML file as a new version; (name, version). Requires `load_code_lists`."""
        require(actor, "load_code_lists")
        with Path(path).open(encoding="utf-8") as handle:
            doc = yaml.safe_load(handle)
        problems: list[str] = []
        if not isinstance(doc, Mapping):
            raise ModelError([f"not_a_mapping:{token(Path(path).name)}"], code="code_list_invalid")
        name = doc.get("name")
        source = doc.get("source") or "local"
        values = doc.get("values")
        if not isinstance(name, str) or not name.strip():
            problems.append("missing:name")
        if not isinstance(values, list) or not values:
            problems.append("missing:values")
        pairs: list[tuple[str, str | None]] = []
        for index, item in enumerate(values if isinstance(values, list) else []):
            code = item.get("code") if isinstance(item, Mapping) else None
            if not isinstance(code, (str, int)) or not str(code).strip():
                problems.append(f"missing:values.{index}.code")
                continue
            label = item.get("label")
            pairs.append((str(code).strip(), str(label) if label is not None else None))
        if problems:
            raise ModelError(problems, code="code_list_invalid")
        version = self.store.save_code_list(str(name), str(source), pairs, actor.name)
        cs = new_change_set(
            str(name),
            "load_code_lists",
            actor,
            Authority("role", actor.role),
            (),
            planning_version=self.store.last_commit_version(),
            reason="load_code_lists",
            evidence={"code_list": token(str(name)), "version": version, "values": len(pairs)},
        )
        self.store.append_change_set(cs, None, 0)
        return str(name), version

    def load_dir(self, directory: Path, actor: Actor) -> dict[str, int]:
        """Every `*.yaml` in `directory`: name -> the version loaded."""
        require(actor, "load_code_lists")
        loaded: dict[str, int] = {}
        for path in sorted(Path(directory).glob("*.yaml")):
            name, version = self.load_file(path, actor)
            loaded[name] = version
        return loaded

    def snapshot(self, names: Iterable[str]) -> tuple[Mapping[str, frozenset[str]], Mapping[str, int]]:
        """(name -> codes, name -> version) of the latest copies; a list never loaded is absent."""
        codes: dict[str, frozenset[str]] = {}
        versions: dict[str, int] = {}
        for name in sorted(set(names)):
            found = self.store.code_list(name)
            if found is None:
                continue
            versions[name], codes[name] = found
        return codes, versions
