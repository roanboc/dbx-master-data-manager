"""★ Portability traps the review found (finding 15, a–e), on both engines (B.5.3, B.17)."""

from __future__ import annotations

import dataclasses
import re
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from mdm.backend import ddl
from mdm.backend import store as store_module
from mdm.backend.store import SqlStore
from mdm.models import canonical_json
from mdm.models.changes import CommitLogRow
from mdm.models.entity_model import EntityModel
from mdm.models.errors import MdmError
from mdm.models.records import Reject
from tests.conftest import THREAD_TIMEOUT, join_all

T0 = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
REPEATABLE_READ = "-c default_transaction_isolation=repeatable\\ read"


def commit_log(version: int) -> CommitLogRow:
    return CommitLogRow(
        version, T0, f"CS-{version}", "automated", "automated-matcher", "rule_version", "r", False, {}, 0, 0
    )


def commit_many(store: SqlStore, threads: int, each: int) -> tuple[list[int], list[BaseException]]:
    """`threads` workers, `each` commits apiece: the versions they got and the errors they met."""
    versions: list[int] = []
    errors: list[BaseException] = []
    lock = threading.Lock()
    start = threading.Barrier(threads, timeout=THREAD_TIMEOUT)

    def work() -> None:
        start.wait()
        for _ in range(each):
            try:
                with store.commit_scope():
                    version = store.next_commit_version()
                    time.sleep(0.001)  # widen the window between the read and the write
                    store.write_commit_log(commit_log(version))
                with lock:
                    versions.append(version)
            except BaseException as exc:  # noqa: BLE001 - the test reports every failure
                with lock:
                    errors.append(exc)

    workers = [threading.Thread(target=work) for _ in range(threads)]
    for worker in workers:
        worker.start()
    join_all(workers)
    return versions, errors


# ------------------------------------------------------------------------------------------ 15a


def test_upsert_refuses_a_repeated_conflict_key_before_binding(store: SqlStore) -> None:
    statements: list[str] = []
    store.add_listener(statements.append)
    rows = [
        {"reader": "arrival", "lo": 5, "hi": 6, "state": "open", "first_seen_at": T0, "lost_at": None},
        {"reader": "arrival", "lo": 5, "hi": 9, "state": "open", "first_seen_at": T0, "lost_at": None},
    ]
    with pytest.raises(ValueError, match="share a conflict key"):
        store._upsert(ddl.table("work", "arrival_gap"), rows, conflict=("reader", "lo"), update=("hi",))
    assert statements == []  # nothing reached the engine


# ------------------------------------------------------------------------------------------ 15b, 15c


def test_the_masked_view_lists_an_added_column_last(store: SqlStore, person_model: EntityModel) -> None:
    store.ensure_entity_tables(person_model)
    doc = person_model.to_dict()
    doc["version"] = 2
    doc["attributes"] = [*doc["attributes"], {"name": "nickname", "type": "text", "masking": "personal"}]
    store.ensure_entity_tables(EntityModel.from_dict(doc))
    view = store.table_columns("read", "person")
    assert view[-1] == "nickname"
    assert view == store.table_columns("core", "person")
    # an unchanged model recreates the same view: Postgres accepts it only when every column keeps its place
    store.ensure_entity_tables(EntityModel.from_dict(doc))
    assert store.table_columns("read", "person") == view


def test_passthrough_views_show_a_column_added_to_a_fixed_table(
    store: SqlStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    xref = ddl.table("core", "xref")
    grown = dataclasses.replace(xref, columns=(*xref.columns, ddl.Column("note", "text")))
    monkeypatch.setattr(ddl, "TABLES", tuple(grown if t is xref else t for t in ddl.TABLES))
    store.init_schema(create_landing=True)
    assert store.table_columns("core", "xref")[-1] == "note"
    assert store.table_columns("read", "xref") == [*xref.column_names(), "note"]


# ------------------------------------------------------------------------------------------ 15d


def test_commit_versions_are_gap_free_under_threads(store: SqlStore) -> None:
    versions, errors = commit_many(store, threads=3, each=10)
    assert errors == []
    assert sorted(versions) == list(range(1, 31))
    assert store.last_commit_version() == 30


@pytest.mark.postgres
def test_repeatable_read_by_default_still_gives_gap_free_versions(
    make_store: Callable[..., SqlStore], request: pytest.FixtureRequest
) -> None:
    import psycopg.conninfo

    dsn = psycopg.conninfo.make_conninfo(request.getfixturevalue("postgres_dsn"), options=REPEATABLE_READ)
    strict = make_store("postgres", postgres_dsn=dsn)
    assert strict._fetch_all("/*mdm:small*/ SHOW default_transaction_isolation") == [("repeatable read",)]
    versions, errors = commit_many(strict, threads=3, each=10)
    assert errors == []
    assert sorted(versions) == list(range(1, 31))


def test_a_commit_scope_inside_a_transaction_is_refused(store: SqlStore) -> None:
    with pytest.raises(MdmError, match="nested_commit_scope"), store.transaction():
        with store.commit_scope():
            pass
    with pytest.raises(MdmError, match="nested_commit_scope"), store.commit_scope():
        with store.commit_scope():
            pass
    with store.commit_scope(), store.transaction():  # a transaction inside the commit joins it
        assert store.next_commit_version() == 1


# ------------------------------------------------------------------------------------------ 15e


def test_a_statement_never_runs_inside_another_threads_transaction(store: SqlStore) -> None:
    opened = threading.Event()
    done = threading.Event()
    errors: list[BaseException] = []

    class Boom(Exception):
        pass

    def rolled_back() -> None:
        try:
            with store.transaction():
                store.put_rejects([Reject("ev-a", 1, None, None, "bad_op")])
                opened.set()
                done.wait(timeout=0.3)
                raise Boom
        except Boom:
            pass
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def outside() -> None:
        try:
            opened.wait(timeout=5)
            store.put_rejects([Reject("ev-b", 2, None, None, "bad_op")])
            done.set()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    a = threading.Thread(target=rolled_back)
    b = threading.Thread(target=outside)
    a.start()
    b.start()
    join_all([a, b])
    assert errors == []
    assert [r.event_id for r in store.rejects(10)] == ["ev-b"]


def test_a_transaction_rolls_back_as_a_whole(store: SqlStore) -> None:
    with pytest.raises(RuntimeError), store.transaction():
        store.put_rejects([Reject("ev-1", 1, None, None, "bad_op")])
        with store.transaction():  # joins
            store.put_rejects([Reject("ev-2", 2, None, None, "bad_op")])
        raise RuntimeError("stop")
    assert store.rejects(10) == []


# ------------------------------------------------------------------------------------------ the rest


def test_canonical_json_refuses_nan() -> None:
    with pytest.raises(ValueError):
        canonical_json({"score": float("nan")})
    with pytest.raises(ValueError):
        canonical_json([float("inf")])


def test_the_store_uses_no_jsonb_question_mark_operator() -> None:
    source = Path(store_module.__file__).read_text(encoding="utf-8")
    assert not re.search(r"\?[|&]", source)
    assert not re.search(r"\?\s*'", source)  # `doc ? 'key'`
    assert not re.search(r"\b(?:r|doc|payload|std_values)\s*\?", source)


def test_every_statement_is_tagged(store: SqlStore, person_model: EntityModel) -> None:
    seen: list[str] = []
    store.add_listener(seen.append)
    store.ensure_entity_tables(person_model)
    store.put_rejects([Reject("ev-1", 1, None, None, "bad_op")])
    store.rejects(10)
    store.source_states("person", [])
    store.last_commit_version()
    store.golden("person", ["PER-000001"])
    tags = re.compile(r"^/\*mdm:(keyed|paged|aggregate|small)\*/ ")
    dml = [s for s in seen if not re.match(r"^(CREATE|ALTER|DROP)\b", s)]
    assert dml and all(tags.match(s) for s in dml), [s[:60] for s in dml if not tags.match(s)]
    assert all("LIMIT" in s for s in dml if s.startswith("/*mdm:paged*/"))


def test_a_row_document_keeps_its_types(store: SqlStore) -> None:
    sql = (
        f"/*mdm:small*/ SELECT {store._cast(0, 'bigint')}, {store._cast(1, 'text')}, {store._cast(2, 'json')}, "
        f"{store._cast(3, 'boolean')}, {store._cast(4, 'numeric')}, {store._cast(5, 'text')} FROM {store._row_source()}"
    )
    rows: Any = store._fetch_all(
        sql, ['[[9007199254740993, "a?b", "{\\"k\\":\\"it\'s\\"}", true, "1.5", null]]']
    )
    value, text, doc, flag, number, missing = rows[0]
    assert value == 9007199254740993  # above 2**53: exact
    assert text == "a?b"  # a question mark inside a bound value is data
    assert store._decode_json(doc) == {"k": "it's"}
    assert flag is True and str(number) == "1.5000000000" and missing is None
