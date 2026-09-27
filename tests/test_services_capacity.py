"""Every read bounded, the import rule kept, the declared figures consistent (owner: SERVICES, B.16)."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import mdm.capacity as capacity
from mdm.backend import ddl
from mdm.models.errors import CapacityError
from tests.test_services_fixtures import arrive, land, mini_world

SRC = Path(__file__).resolve().parents[1] / "src" / "mdm"
_TAG = re.compile(r"^\s*/\*mdm:(keyed|paged|aggregate|small)\*/")
#: what each package may import from `mdm` (B.1)
IMPORT_RULE = {
    "models": set(),
    "config": {"models"},
    "capacity": {"models"},
    "engine": {"models", "capacity"},
    "backend": {"models", "config", "capacity"},
    "services": {"models", "config", "capacity", "engine", "backend"},
    "agent": {"models", "config", "capacity", "backend", "services.privacy"},
    "demo": {"models", "config", "capacity", "engine", "backend"},
}


def test_every_statement_of_an_arrival_is_tagged_and_large_tables_are_read_keyed_or_paged(hub) -> None:
    statements: list[str] = []
    remove = hub.store.add_listener(statements.append)
    try:
        land(hub, mini_world(persons=10, organisations=3).rows)
        arrive(hub)
        land(hub, mini_world(persons=4, organisations=1).rows)
        arrive(hub)
        hub.feed.read(0)
    finally:
        remove()
    assert statements
    large = "|".join(sorted(capacity.LARGE_TABLES) + ["person", "organisation"])
    on_large = re.compile(
        rf"\b{re.escape(hub.store.prefix)}_(?:work|hub|vault|core|landing|audit)\.(?:{large})\b"
    )
    problems = []
    for sql in statements:
        if sql.lstrip().upper().startswith(("CREATE", "ALTER", "DROP", "BEGIN", "COMMIT", "ROLLBACK", "SET")):
            continue
        tag = _TAG.match(sql)
        if tag is None:
            problems.append(("untagged", sql[:80]))
            continue
        body = sql[tag.end() :].lstrip().upper()
        if body.startswith("SELECT") and on_large.search(sql) and tag.group(1) not in ("keyed", "paged"):
            problems.append(("unbounded read", sql[:120]))
        if tag.group(1) == "paged" and " LIMIT " not in sql.upper():
            problems.append(("paged without limit", sql[:120]))
    assert problems == []


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("mdm"):
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names if alias.name.startswith("mdm"))
    return found


def test_the_import_rule() -> None:
    problems = []
    for path in sorted(SRC.rglob("*.py")):
        relative = path.relative_to(SRC)
        package = relative.parts[0] if len(relative.parts) > 1 else relative.stem
        if package not in IMPORT_RULE:
            continue  # the command line and the package root may import everything
        allowed = IMPORT_RULE[package]
        for module in _imports(path):
            parts = module.split(".")
            if len(parts) < 2 or parts[1] == package:
                continue
            target = parts[1]
            if target in allowed or ".".join(parts[1:3]) in allowed:
                continue
            if target in IMPORT_RULE or target == "cli":
                problems.append((str(relative), module))
    assert problems == []


def test_the_declared_figures_are_consistent() -> None:
    assert capacity.DECLARED_GOLDEN_PER_ENTITY < capacity.PATH_TO_GOLDEN_PER_ENTITY
    assert capacity.ARRIVAL_BATCH <= capacity.BULK_BATCH
    assert capacity.COMMIT_CHUNK_ROWS <= capacity.BULK_COMMIT_CHUNK_ROWS
    assert capacity.KEY_CHUNK <= capacity.WRITE_CHUNK_ROWS
    assert capacity.MAX_MEMBERS_CHECKED <= capacity.STOP_KEY_RECORDS
    assert capacity.EM_SAMPLE_RECORDS <= capacity.FREQUENCY_SAMPLE_RECORDS
    assert capacity.DECLARED_ARRIVALS_PER_DAY <= capacity.PROPOSED_BULK_ROWS_PER_HOUR * 24
    names = {t.name for t in ddl.TABLES}
    assert set(capacity.LARGE_TABLES) <= names


def test_the_paging_helpers() -> None:
    rows = list(range(1, 12))

    def fetch(after, limit):
        start = 0 if after is None else rows.index(after) + 1
        return rows[start : start + limit]

    assert [page for page in capacity.pages(fetch, key=lambda r: r, page=5)] == [
        rows[:5],
        rows[5:10],
        rows[10:],
    ]
    assert list(capacity.chunks(rows, 4)) == [rows[:4], rows[4:8], rows[8:]]
    assert capacity.require_limit(5, 10) == 5
    for bad in (0, 11):
        try:
            capacity.require_limit(bad, 10)
        except CapacityError as exc:
            assert exc.code == "limit_out_of_range"
        else:  # pragma: no cover
            raise AssertionError("no refusal")
