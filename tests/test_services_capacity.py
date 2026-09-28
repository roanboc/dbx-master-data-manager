"""Every read bounded, the import rule kept, the declared figures consistent (owner: SERVICES, B.16)."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import mdm.capacity as capacity
from mdm.backend import ddl
from mdm.models.errors import CapacityError
from tests.helpers import arrive, land, mini_world

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
    # the workbench reaches the hub through the services only (services.context for Hub)
    "ui": {"models", "config", "capacity", "services"},
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


def _tag_problems(statements: list[str], prefix: str) -> list[tuple[str, str]]:
    """The capacity rule: every statement tagged; a read of a large table keyed or paged; a paged one limited."""
    large = "|".join(sorted(capacity.LARGE_TABLES) + ["person", "organisation"])
    on_large = re.compile(rf"\b{re.escape(prefix)}_(?:work|hub|vault|core|landing|audit)\.(?:{large})\b")
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
    return problems


def test_every_statement_of_the_workbench_is_tagged_and_no_count_reads_a_whole_table(hub) -> None:
    """An inbox page, the counts, the health strip, a case, a reveal, a stage, a flush and each record read."""
    from datetime import timedelta

    from mdm.models.canonical import utcnow
    from tests.helpers import STEWARD, person_review, seen, task_of, workbench_world

    workbench_world(hub)
    source = person_review(hub)
    task = task_of(hub, kind="review", source=source)
    statements: list[str] = []
    remove = hub.store.add_listener(statements.append)
    try:
        hub.inbox.page("mine", actor=STEWARD)
        hub.inbox.counts(actor=STEWARD)
        hub.inbox.health(actor=STEWARD)
        case = hub.decisions.case(task.task_id, actor=STEWARD)
        hub.decisions.reveal(task.task_id, actor=STEWARD, reason="deciding_task")
        entry = hub.tray.stage(task.task_id, "link", actor=STEWARD, **seen(hub, task.task_id))
        hub.tray.entries(actor=STEWARD)
        moment = utcnow() + timedelta(seconds=hub.settings.undo_seconds + 1)
        hub.tray.clock = lambda: moment
        assert hub.tray.flush().committed == 1
        master = case.candidates[0].master_id
        hub.lookup.resolve(master, actor=STEWARD)
        hub.lookup.header("person", master, actor=STEWARD)
        hub.lookup.golden("person", master, actor=STEWARD)
        hub.lookup.why("person", master, "given_name", actor=STEWARD)
        hub.lookup.members("person", master, actor=STEWARD)
        hub.lookup.timeline("person", master, actor=STEWARD)
        hub.lookup.relationships("person", master, actor=STEWARD)
        hub.lookup.source(source, actor=STEWARD)
        hub.inbox.backfill_due_times()
        assert entry.entry_id
    finally:
        remove()
    assert statements
    assert _tag_problems(statements, hub.store.prefix) == []
    # a count on screen never aggregates over the task or tray tables: it counts a limited subquery
    counts = [
        s for s in statements if "COUNT(" in s.upper() and ("_work.task" in s or "_work.tray_entry" in s)
    ]
    assert counts and all(s.startswith("/*mdm:paged*/") and " LIMIT " in s.upper() for s in counts)


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


#: what gives SQL away: a statement, or a call that runs one
_SQL = re.compile(r"_fetch_all|_execute\b|\.execute\(|\bSELECT\b|\bINSERT INTO\b")


def test_no_sql_outside_the_backend() -> None:
    """Invariant 1: every statement lives in `src/mdm/backend/`; the other layers call `SqlStore` methods."""
    outside = [p for p in sorted(SRC.rglob("*.py")) if p.relative_to(SRC).parts[0] != "backend"]
    assert outside
    found = [
        f"{path.relative_to(SRC)}:{number}"
        for path in outside
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if _SQL.search(line)
    ]
    assert found == []


#: what the workbench never holds: the store (callbacks call services), and anything that renders HTML
_UI_FORBIDDEN = re.compile(r"\.store\b|dangerously_allow_html|dcc\.Markdown|dangerously_allow_code|innerHTML")


def test_the_workbench_reaches_the_hub_through_the_services_and_renders_no_html() -> None:
    """No file of `src/mdm/ui/` reads a `.store` attribute, and none renders raw HTML (decision 20)."""
    files = sorted(p for p in (SRC / "ui").rglob("*") if p.suffix in (".py", ".js", ".css"))
    assert files
    found = [
        f"{path.relative_to(SRC)}:{number}"
        for path in files
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if _UI_FORBIDDEN.search(line)
    ]
    assert found == []


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
    assert capacity.CANDIDATES_SHOWN <= 3  # the keys 1, 2 and 3 choose one
    assert capacity.INBOX_PAGE <= capacity.COUNT_CAP
    assert capacity.TRAY_SHOWN <= capacity.COUNT_CAP
    assert capacity.FLUSH_ATTEMPTS >= 1 and capacity.FLUSH_BATCH <= capacity.READ_PAGE


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


def test_every_statement_of_the_checkpoint_is_tagged_and_reads_samples_keyed_or_paged(hub) -> None:
    """A blind case, a blind stage and its flush, an arrival that counts and checks, a trip, the status, a
    restore and the hand-back that follows it (story 3.2)."""
    from datetime import timedelta

    from mdm.models.canonical import utcnow
    from mdm.services.context import Hub
    from tests.helpers import COORDINATOR, OWNER, STEWARD, T0, crm_person_key, person_payload, row, seen

    sampled = Hub.open(hub.settings.with_(sample_share=1.0), store=hub.store, as_role="data_owner")
    statements: list[str] = []
    remove = hub.store.add_listener(statements.append)
    try:
        land(sampled, mini_world(persons=8, organisations=3).rows)
        arrive(sampled)
        task = next(t for t in sampled.store.tasks("person", "quality_sample", "open", 100, None) if t.source)
        case = sampled.decisions.case(task.task_id, actor=COORDINATOR)
        sampled.decisions.reveal(task.task_id, actor=COORDINATOR, reason="deciding_task")
        target = case.choices[0].master_id if case.choices else None
        decision = "blind_link" if target else "blind_none"
        sampled.tray.stage(
            task.task_id, decision, actor=COORDINATOR, target=target, **seen(sampled, task.task_id)
        )
        moment = utcnow() + timedelta(seconds=sampled.settings.undo_seconds + 1)
        sampled.tray.clock = lambda: moment
        assert sampled.tray.flush().committed == 1
        sampled.inbox.page("samples", actor=STEWARD)
        sampled.inbox.counts(actor=STEWARD)
        sampled.inbox.health(actor=STEWARD)
        sampled.breaker.demo_trip("person", figures={"agreed": 30, "reviewed": 40, "threshold": 0.95})
        land(
            sampled, [row("crm", crm_person_key(1), "person", person_payload(1), at=T0 + timedelta(hours=2))]
        )
        arrive(sampled)
        sampled.breaker.status(["person", "organisation"], actor=STEWARD)
        sampled.breaker.restore("person", actor=OWNER, reason="cause_fixed")
        assert arrive(sampled).handed_back == 1
    finally:
        remove()
        sampled.close()
    assert statements
    assert _tag_problems(statements, hub.store.prefix) == []
    on_samples = [s for s in statements if "_work.quality_sample" in s and "SELECT" in s.upper()]
    assert on_samples and all(s.startswith(("/*mdm:keyed*/", "/*mdm:paged*/")) for s in on_samples)
