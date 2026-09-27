"""The DDL: portable types, the fixed tables per schema group, entity tables and views (owner: BACKEND, B.5.1–B.5.2).

| Logical     | DuckDB                   | Postgres                 |
| ----------- | ------------------------ | ------------------------ |
| text        | VARCHAR                  | text                     |
| bigint      | BIGINT                   | bigint                   |
| int         | INTEGER                  | integer                  |
| numeric     | DECIMAL(38,10)           | numeric(38,10)           |
| boolean     | BOOLEAN                  | boolean                  |
| date        | DATE                     | date                     |
| timestamptz | TIMESTAMP WITH TIME ZONE | timestamp with time zone |
| json        | JSON                     | jsonb                    |

Attribute type -> column: text -> text, integer -> bigint, number -> numeric,
boolean, date, timestamp -> timestamptz, json or repeating -> json. A
`reference` attribute has no column: it is published only as a relationship.
No uuid, no arrays, no vectors, no partitions.

Every name here is an unquoted identifier outside both engines' reserved
words, so no statement quotes one; `ident()` refuses any other name at the
point a statement is built from it. The plan's `access_log.at` and
`redaction_log.at` are `accessed_at` and `redacted_at`: `at` is reserved on
DuckDB.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from mdm.models.entity_model import EntityModel
from mdm.models.workbench import LABELS, TRAY_STATUSES

GROUPS = ("model", "landing", "work", "hub", "vault", "core", "read", "audit")
LOGICAL_TYPES = ("text", "bigint", "int", "numeric", "boolean", "date", "timestamptz", "json")
ENGINES = ("duckdb", "postgres")

_SQL_TYPES: Mapping[str, Mapping[str, str]] = {
    "duckdb": {
        "text": "VARCHAR",
        "bigint": "BIGINT",
        "int": "INTEGER",
        "numeric": "DECIMAL(38,10)",
        "boolean": "BOOLEAN",
        "date": "DATE",
        "timestamptz": "TIMESTAMP WITH TIME ZONE",
        "json": "JSON",
    },
    "postgres": {
        "text": "text",
        "bigint": "bigint",
        "int": "integer",
        "numeric": "numeric(38,10)",
        "boolean": "boolean",
        "date": "date",
        "timestamptz": "timestamp with time zone",
        "json": "jsonb",
    },
}
#: `information_schema.columns.data_type` (lower-cased) -> logical type, both engines
DATA_TYPE_LOGICAL: Mapping[str, str] = {
    "varchar": "text",
    "text": "text",
    "character varying": "text",
    "bigint": "bigint",
    "integer": "int",
    "decimal(38,10)": "numeric",
    "numeric": "numeric",
    "boolean": "boolean",
    "date": "date",
    "timestamp with time zone": "timestamptz",
    "json": "json",
    "jsonb": "json",
}
_ATTRIBUTE_LOGICAL: Mapping[str, str] = {
    "text": "text",
    "integer": "bigint",
    "number": "numeric",
    "boolean": "boolean",
    "date": "date",
    "timestamp": "timestamptz",
    "json": "json",
}


@dataclass(frozen=True, slots=True)
class Column:
    name: str
    type: str  # a logical type
    nullable: bool = True
    default: str | None = None  # SQL text; "{prefix}" is replaced by the schema prefix
    check: str | None = None  # SQL text of a CHECK constraint on this column


@dataclass(frozen=True, slots=True)
class Table:
    group: str
    name: str
    columns: tuple[Column, ...]
    primary_key: tuple[str, ...]
    unique: tuple[tuple[str, ...], ...] = ()
    indexes: tuple[tuple[str, ...], ...] = ()

    def column(self, name: str) -> Column:
        for column in self.columns:
            if column.name == name:
                return column
        raise KeyError(name)

    def column_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)


def _c(
    name: str, type_: str, *, null: bool = True, default: str | None = None, check: str | None = None
) -> Column:
    return Column(name, type_, null, default, check)


def _n(name: str, type_: str, *, default: str | None = None, check: str | None = None) -> Column:
    """A NOT NULL column."""
    return Column(name, type_, False, default, check)


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


_LIFECYCLE = ("draft", "published", "retired")

#: every fixed table of B.5.2; entity tables come from `entity_table()`
TABLES: tuple[Table, ...] = (
    # ---------------------------------------------------------------- model
    Table(
        "model",
        "entity_model",
        (
            _n("entity", "text"),
            _n("version", "int"),
            _n("status", "text", check=_in("status", _LIFECYCLE)),
            _n("doc", "json"),
            _n("doc_hash", "text"),
            _n("created_at", "timestamptz"),
            _n("created_by", "text"),
            _c("published_at", "timestamptz"),
            _c("published_by", "text"),
        ),
        ("entity", "version"),
    ),
    Table(
        "model",
        "rule_set",
        (
            _n("entity", "text"),
            _n("kind", "text", check=_in("kind", ("match", "survivorship", "validation"))),
            _n("version", "int"),
            _n("status", "text", check=_in("status", _LIFECYCLE)),
            _n("doc", "json"),
            _n("model_version", "int"),
            _n("created_at", "timestamptz"),
            _n("created_by", "text"),
            _c("published_at", "timestamptz"),
            _c("published_by", "text"),
        ),
        ("entity", "kind", "version"),
    ),
    Table(
        "model",
        "code_list_version",
        (
            _n("name", "text"),
            _n("version", "int"),
            _n("source", "text"),
            _n("loaded_at", "timestamptz"),
            _n("loaded_by", "text"),
            _n("value_count", "int"),
        ),
        ("name", "version"),
    ),
    Table(
        "model",
        "code_list_value",
        (_n("name", "text"), _n("version", "int"), _n("code", "text"), _c("label", "text")),
        ("name", "version", "code"),
    ),
    # ---------------------------------------------------------------- landing (the integration platform's)
    Table(
        "landing",
        "source_change",
        (
            _n("event_id", "text"),
            _n("source_system", "text"),
            _n("source_key", "text"),
            _n("entity", "text"),
            _n("op", "text", check=_in("op", ("upsert", "delete"))),
            _n("occurred_at", "timestamptz"),
            _c("source_version", "bigint"),
            _n("initial_load", "boolean", default="false"),
            _n("payload", "json"),
            _n("landed_at", "timestamptz", default="current_timestamp"),
            _n("landing_seq", "bigint", default="nextval('{prefix}_landing.landing_seq')"),
        ),
        ("event_id",),
        unique=(("landing_seq",),),
    ),
    # ---------------------------------------------------------------- work
    Table(
        "work",
        "source_state",
        (
            _n("entity", "text"),
            _n("source_system", "text"),
            _n("source_key", "text"),
            _n("status", "text", check=_in("status", ("active", "deleted"))),
            _n("std_values", "json"),
            _n("match_forms", "json"),
            _n("ids", "json"),
            _n("refs", "json"),
            _n("value_ids", "json"),
            _n("sample_hash", "bigint"),
            _c("source_version", "bigint"),
            _n("occurred_at", "timestamptz"),
            _n("landing_seq", "bigint"),
            _n("event_id", "text"),
            _n("initial_load", "boolean"),
            _n("held", "boolean", default="false"),
            _c("approved_values", "json"),
            _c("approved_event_id", "text"),
            _n("rules_checked", "int", default="0"),
            _n("rules_failed", "int", default="0"),
            _n("updated_at", "timestamptz"),
        ),
        ("entity", "source_system", "source_key"),
        indexes=(("entity", "sample_hash", "source_system", "source_key"),),
    ),
    Table(
        "work",
        "blocking_key",
        (
            _n("entity", "text"),
            _n("pass_name", "text"),
            _n("key_value", "text"),
            _n("source_system", "text"),
            _n("source_key", "text"),
        ),
        ("entity", "pass_name", "key_value", "source_system", "source_key"),
        indexes=(("entity", "source_system", "source_key"),),
    ),
    Table(
        "work",
        "candidate_pair",
        (
            _n("entity", "text"),
            _n("left_system", "text"),
            _n("left_key", "text"),
            _n("right_system", "text"),
            _n("right_key", "text"),
            _n("rule_version", "int"),
            _n("score", "numeric"),
            _n("band", "text"),
            _n("levels", "json"),
            _n("explanation", "json"),
            _n("signature", "text"),
            _n("scored_at", "timestamptz"),
        ),
        ("entity", "left_system", "left_key", "right_system", "right_key", "rule_version"),
    ),
    Table(
        "work",
        "task",
        (
            _n("task_id", "text"),
            _n("task_key", "text"),
            _n("entity", "text"),
            _n("kind", "text"),
            _n("status", "text", default="'open'"),
            _c("source_system", "text"),
            _c("source_key", "text"),
            _n("master_ids", "json"),
            _n("reason", "text"),
            _n("suggestion", "json"),
            _n("evidence", "json"),
            _c("event_id", "text"),
            _n("created_at", "timestamptz"),
            _n("updated_at", "timestamptz"),
            _c("due_at", "timestamptz"),
            _c("claimed_by", "text"),
            _c("claimed_at", "timestamptz"),
            _c("rank", "numeric"),
            _c("decided_at", "timestamptz"),
            # the workbench (initiative 3): a snooze hides a task from My queue; an escalation marks it
            _c("snoozed_until", "timestamptz"),
            _c("snoozed_by", "text"),
            _c("escalated_at", "timestamptz"),
            _c("escalated_by", "text"),
            _c("escalation", "text"),
        ),
        ("task_id",),
        indexes=(
            ("entity", "status", "kind"),
            ("task_key",),
            ("status", "due_at", "task_id"),  # the inbox's pages and capped counts
            ("source_system", "source_key"),
            ("claimed_by", "claimed_at"),  # "3 claimed by you" under My queue
        ),
    ),
    Table("work", "open_task", (_n("task_key", "text"), _n("task_id", "text")), ("task_key",)),
    # the undo tray (decision 19): a steward's decision waits here until its window passes; no label or
    # reason column, since a tray line's words are built when it is read
    Table(
        "work",
        "tray_entry",
        (
            _n("entry_id", "text"),
            _n("task_id", "text"),
            _n("entity", "text"),
            _n("decision", "text"),  # checked by the service: the list grows story by story
            _c("target", "text"),
            _n("subject", "json"),
            _c("signature", "text"),
            _n("actor", "text"),
            _n("actor_role", "text"),
            _n("persona", "boolean"),
            _c("event_id", "text"),
            _n("planning_version", "bigint"),
            _n("staged_at", "timestamptz"),
            _n("deadline", "timestamptz"),
            _n("status", "text", default="'staged'", check=_in("status", TRAY_STATUSES)),
            _n("attempts", "int", default="0"),
            _c("settled_at", "timestamptz"),
            _c("change_set_id", "text"),
            _c("outcome", "text"),
        ),
        ("entry_id",),
        indexes=(("status", "deadline"), ("actor", "staged_at")),
    ),
    # one row per subject a staged decision holds ("task:<id>", "source:<entity>:<system>:<key>",
    # "golden:<master ID>"): a second decision on the same subject is refused while the first waits
    Table(
        "work",
        "tray_lock",
        (_n("subject", "text"), _n("entry_id", "text")),
        ("subject",),
        indexes=(("entry_id",),),
    ),
    # a steward's label on a pair (decision 22): the latest decision per pair binds the automated matcher
    Table(
        "work",
        "match_label",
        (
            _n("entity", "text"),
            _n("left_ref", "text"),
            _n("right_ref", "text"),
            _n("label", "text", check=_in("label", LABELS)),
            _c("rule_version", "int"),
            _c("score", "numeric"),
            _c("band", "text"),
            _c("signature", "text"),
            _c("task_id", "text"),
            _c("entry_id", "text"),
            _n("decided_by", "text"),
            _n("decided_role", "text"),
            _n("decided_at", "timestamptz"),
        ),
        ("entity", "left_ref", "right_ref"),
        indexes=(("entity", "right_ref"),),
    ),
    Table(
        "work",
        "rule_result",
        (
            _n("entity", "text"),
            _n("source_system", "text"),
            _n("source_key", "text"),
            _n("rule_id", "text"),
            _n("dimension", "text"),
            _n("code", "text"),
            _n("severity", "text"),
            _c("code_list_version", "int"),
            _n("event_id", "text"),
            _n("checked_at", "timestamptz"),
        ),
        ("entity", "source_system", "source_key", "rule_id"),
    ),
    Table(
        "work",
        "arrival_position",
        (
            _n("reader", "text"),
            _n("high_water", "bigint"),
            _n("low_water", "bigint"),
            _c("reconciled_at", "timestamptz"),
            _n("updated_at", "timestamptz"),
        ),
        ("reader",),
    ),
    Table(
        "work",
        "arrival_gap",
        (
            _n("reader", "text"),
            _n("lo", "bigint"),
            _n("hi", "bigint"),
            _n("state", "text", check=_in("state", ("open", "lost"))),
            _n("first_seen_at", "timestamptz"),
            _c("lost_at", "timestamptz"),
        ),
        ("reader", "lo"),
        indexes=(("reader", "state", "lo"),),
    ),
    Table(
        "work",
        "arrival_queue",
        (
            _n("entity", "text"),
            _n("source_system", "text"),
            _n("source_key", "text"),
            _n("event_id", "text"),
            _n("landing_seq", "bigint"),
            _n("queued_at", "timestamptz"),
        ),
        ("entity", "source_system", "source_key"),
        indexes=(("entity", "landing_seq", "source_system", "source_key"),),
    ),
    Table(
        "work",
        "landing_reject",
        (
            _n("event_id", "text"),
            _n("landing_seq", "bigint"),
            _c("entity", "text"),
            _c("source_system", "text"),
            _c("source_key", "text"),
            _n("reason", "text"),
            _n("attributes", "json"),
            _n("rejected_at", "timestamptz"),
            _c("replayed_at", "timestamptz"),
        ),
        ("event_id",),
    ),
    Table(
        "work",
        "pending_reference",
        (
            _n("entity", "text"),
            _n("source_system", "text"),
            _n("source_key", "text"),
            _n("attribute", "text"),
            _n("ref_entity", "text"),
            _n("ref_system", "text"),
            _n("ref_key", "text"),
            _n("first_seen_at", "timestamptz"),
        ),
        ("entity", "source_system", "source_key", "attribute"),
        indexes=(("ref_entity", "ref_system", "ref_key"),),
    ),
    Table(
        "work",
        "job_run",
        (
            _n("run_id", "text"),
            _n("job", "text"),
            _n("status", "text"),
            _n("actor", "text"),
            _n("started_at", "timestamptz"),
            _n("heartbeat_at", "timestamptz"),
            _c("finished_at", "timestamptz"),
            _n("progress", "json"),
            _c("error_code", "text"),
        ),
        ("run_id",),
    ),
    # ---------------------------------------------------------------- hub
    Table(
        "hub",
        "source_version",
        (
            _n("event_id", "text"),
            _n("entity", "text"),
            _n("source_system", "text"),
            _n("source_key", "text"),
            _n("op", "text"),
            _n("occurred_at", "timestamptz"),
            _c("source_version", "bigint"),
            _n("landing_seq", "bigint"),
            _n("landed_at", "timestamptz"),
            _n("initial_load", "boolean"),
            _n("payload", "json"),
            _n("received_at", "timestamptz"),
        ),
        ("event_id",),
        indexes=(("entity", "source_system", "source_key"),),
    ),
    Table(
        "hub",
        "provenance",
        (
            _n("entity", "text"),
            _n("master_id", "text"),
            _n("doc", "json"),
            _n("rule_version", "int"),
            _n("commit_version", "bigint"),
        ),
        ("entity", "master_id"),
    ),
    Table(
        "hub",
        "steward_value",
        (
            _n("entity", "text"),
            _n("master_id", "text"),
            _n("attribute", "text"),
            _c("value", "json"),
            _c("pinned_until", "timestamptz"),
            _n("set_by", "text"),
            _n("set_at", "timestamptz"),
            _n("commit_version", "bigint"),
        ),
        ("entity", "master_id", "attribute"),
    ),
    Table(
        "hub",
        "id_counter",
        (_n("name", "text"), _n("code", "text"), _n("last_value", "bigint")),
        ("name",),
    ),
    Table(
        "hub",
        "merge_member",
        (
            _n("entity", "text"),
            _n("retired_id", "text"),
            _n("merge_version", "bigint"),
            _n("source_system", "text"),
            _n("source_key", "text"),
        ),
        ("retired_id", "merge_version", "source_system", "source_key"),
    ),
    # ---------------------------------------------------------------- vault
    Table(
        "vault",
        "personal_value",
        (
            _n("value_id", "text"),
            _n("entity", "text"),
            _n("subject_key", "text"),
            _n("attribute", "text"),
            _c("value", "text"),
            _n("created_at", "timestamptz"),
            _c("redacted_at", "timestamptz"),
            _c("redaction_id", "text"),
        ),
        ("value_id",),
        indexes=(("subject_key", "attribute"),),
    ),
    # ---------------------------------------------------------------- core (published)
    Table(
        "core",
        "xref",
        (
            _n("entity", "text"),
            _n("source_system", "text"),
            _n("source_key", "text"),
            _n("master_id", "text"),
            _n("status", "text", check=_in("status", ("active", "detached"))),
            _n("linked_at", "timestamptz"),
            _n("_commit_version", "bigint"),
        ),
        ("entity", "source_system", "source_key"),
        indexes=(("entity", "master_id"),),
    ),
    Table(
        "core",
        "retired_id",
        (
            _n("retired_id", "text"),
            _n("entity", "text"),
            _n("merged_into", "text"),
            _n("merge_version", "bigint"),
            _n("survivor_id", "text"),
            _n("active", "boolean"),
            _n("retired_at", "timestamptz"),
            _n("_commit_version", "bigint"),
        ),
        ("retired_id",),
        indexes=(("survivor_id",), ("merged_into",)),
    ),
    Table(
        "core",
        "relationship",
        (
            _n("rel_id", "text"),
            _n("rel_type", "text"),
            _n("from_entity", "text"),
            _n("from_master_id", "text"),
            _n("to_entity", "text"),
            _n("to_master_id", "text"),
            _c("valid_from", "date"),
            _c("valid_to", "date"),
            _n("status", "text", check=_in("status", ("active", "ended"))),
            _n("attributes", "json"),
            _c("origin_system", "text"),
            _c("origin_key", "text"),
            _c("origin_attribute", "text"),
            _n("_commit_version", "bigint"),
            _n("_row_version", "bigint"),
        ),
        ("rel_id",),
        indexes=(("from_master_id",), ("to_master_id",), ("origin_system", "origin_key")),
    ),
    Table(
        "core",
        "change",
        (
            _n("commit_version", "bigint"),
            _n("change_seq", "int"),
            _n("entity", "text"),
            _n("master_id", "text"),
            _n(
                "change_kind",
                "text",
                check=_in(
                    "change_kind",
                    ("created", "updated", "merged", "retired", "unmerged", "reinstated", "remapped"),
                ),
            ),
            _c("survivor_id", "text"),
            _n("parts", "json"),
        ),
        ("commit_version", "change_seq"),
        indexes=(("entity", "commit_version"), ("master_id", "commit_version")),
    ),
    Table(
        "core",
        "commit_log",
        (
            _n("commit_version", "bigint"),
            _n("committed_at", "timestamptz"),
            _n("change_set_id", "text"),
            _n("actor_kind", "text"),
            _n("actor_role", "text"),
            _n("authority_kind", "text"),
            _n("authority_ref", "text"),
            _n("initial_load", "boolean"),
            _n("counts", "json"),
            _n("row_count", "bigint"),
            _n("change_count", "int"),
        ),
        ("commit_version",),
    ),
    # ---------------------------------------------------------------- audit (insert only)
    Table(
        "audit",
        "change_set",
        (
            _n("change_set_id", "text"),
            _n("fingerprint", "text"),
            _n("planning_version", "bigint"),
            _n("entity", "text"),
            _n("action", "text"),
            _c("commit_version", "bigint"),
            _n("actor", "text"),
            _n("actor_kind", "text"),
            _n("actor_role", "text"),
            _n("persona", "boolean"),
            _c("checker", "text"),
            _n("authority_kind", "text"),
            _n("authority_ref", "text"),
            _n("reason", "text"),
            _n("evidence", "json"),
            _n("item_count", "int"),
            _n("created_at", "timestamptz"),
        ),
        ("change_set_id",),
        indexes=(("fingerprint",),),
    ),
    Table(
        "audit",
        "change_log",
        (
            _n("change_id", "text"),
            _c("commit_version", "bigint"),
            _n("change_set_id", "text"),
            _n("table_name", "text"),
            _n("row_key", "text"),
            _n("op", "text", check=_in("op", ("insert", "update", "delete", "summary"))),
            _c("clause", "text"),
            _c("before", "json"),
            _c("after", "json"),
            _n("changed_at", "timestamptz"),
        ),
        ("change_id",),
        indexes=(("commit_version",), ("row_key",)),
    ),
    Table(
        "audit",
        "access_log",
        (
            _n("access_id", "text"),
            _n("actor", "text"),
            _n("actor_role", "text"),
            _n("action", "text"),
            _c("entity", "text"),
            _c("master_id", "text"),
            _c("attribute", "text"),
            _n("reason", "text"),
            _n("detail", "json"),
            _n("accessed_at", "timestamptz"),
        ),
        ("access_id",),
    ),
    Table(
        "audit",
        "redaction_log",
        (
            _n("redaction_id", "text"),
            _n("subject_keys", "json"),
            _n("value_ids", "json"),
            _n("actor", "text"),
            _n("checker", "text"),
            _n("authority_ref", "text"),
            _n("reason", "text"),
            _n("redacted_at", "timestamptz"),
        ),
        ("redaction_id",),
    ),
)
SEQUENCES: tuple[tuple[str, str], ...] = (("landing", "landing_seq"),)
#: the core tables `<p>_read` shows unmasked, every column listed
PASSTHROUGH_VIEWS = ("xref", "retired_id", "relationship")
#: the columns every entity table has besides its attributes: three before them, five after
ENTITY_LEAD_COLUMNS = ("master_id", "status", "survivor_id")
ENTITY_TRAIL_COLUMNS = ("_commit_version", "_row_version", "_initial_load", "_created_at", "_updated_at")
ENTITY_FIXED_COLUMNS = frozenset(ENTITY_LEAD_COLUMNS + ENTITY_TRAIL_COLUMNS)
_BY_NAME: Mapping[tuple[str, str], Table] = {(t.group, t.name): t for t in TABLES}


_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]{0,62}")


def ident(name: str) -> str:
    """`name` when it is an unquoted identifier both engines accept as written; ValueError otherwise.

    No statement here quotes a name, so every name a statement is built from passes through this
    check first, whatever checked it upstream (a model file, a setting).
    """
    if not isinstance(name, str) or not _IDENTIFIER.fullmatch(name):
        raise ValueError("not a plain identifier")
    return name


def schema_name(prefix: str, group: str) -> str:
    """`f"{prefix}_{group}"`: the schema of one group."""
    return f"{ident(prefix)}_{ident(group)}"


def qualified(prefix: str, table: Table) -> str:
    return f"{schema_name(prefix, table.group)}.{table.name}"


def table(group: str, name: str) -> Table:
    """The fixed table `name` of `group` from `TABLES`; KeyError when there is none."""
    return _BY_NAME[(group, name)]


def sql_type(logical: str, engine: str) -> str:
    """The engine's spelling of a logical type (the table above)."""
    try:
        return _SQL_TYPES[engine][logical]
    except KeyError:
        raise ValueError(f"unknown type {logical!r} or engine {engine!r}") from None


def logical_of(data_type: str) -> str:
    """The logical type of an `information_schema.columns.data_type` value on either engine."""
    try:
        return DATA_TYPE_LOGICAL[data_type.strip().lower()]
    except KeyError:
        raise ValueError(f"not a portable type: {data_type!r}") from None


def attribute_type(attribute_type: str, repeating: bool) -> str:
    """The logical column type of an attribute type (text -> text, integer -> bigint, …, repeating -> json)."""
    if repeating:
        return "json"
    if attribute_type == "reference":
        raise ValueError("a reference attribute has no column")
    try:
        return _ATTRIBUTE_LOGICAL[attribute_type]
    except KeyError:
        raise ValueError(f"unknown attribute type {attribute_type!r}") from None


def index_name(table: Table, columns: Sequence[str]) -> str:
    """`<table>_<columns>_ix`, at most 63 characters (the Postgres identifier limit)."""
    name = f"{table.name}_{'_'.join(c.lstrip('_') for c in columns)}"
    return name[:60] + "_ix"


def _column_sql(column: Column, prefix: str, engine: str, in_key: bool) -> str:
    parts = [ident(column.name), sql_type(column.type, engine)]
    if not column.nullable or in_key:
        parts.append("NOT NULL")
    if column.default is not None:
        parts.append("DEFAULT " + column.default.replace("{prefix}", prefix))
    if column.check is not None:
        parts.append(f"CHECK ({column.check})")
    return " ".join(parts)


def render_schema(prefix: str, group: str) -> str:
    return f"CREATE SCHEMA IF NOT EXISTS {schema_name(prefix, group)}"


def render_table(table: Table, prefix: str, engine: str) -> list[str]:
    """CREATE TABLE IF NOT EXISTS, then its indexes (CREATE INDEX IF NOT EXISTS)."""
    ident(table.name)
    key = set(table.primary_key)
    lines = [_column_sql(c, prefix, engine, c.name in key) for c in table.columns]
    lines.append("PRIMARY KEY (" + ", ".join(table.primary_key) + ")")
    lines.extend("UNIQUE (" + ", ".join(u) + ")" for u in table.unique)
    name = qualified(prefix, table)
    statements = [f"CREATE TABLE IF NOT EXISTS {name} (\n    " + ",\n    ".join(lines) + "\n)"]
    statements.extend(
        f"CREATE INDEX IF NOT EXISTS {index_name(table, cols)} ON {name} ({', '.join(cols)})"
        for cols in table.indexes
    )
    return statements


def render_sequence(prefix: str, group: str, name: str, engine: str) -> str:
    """CREATE SEQUENCE IF NOT EXISTS; Postgres: CACHE 1."""
    text = f"CREATE SEQUENCE IF NOT EXISTS {schema_name(prefix, group)}.{name}"
    return text + " CACHE 1" if engine == "postgres" else text


def entity_table(model: EntityModel) -> Table:
    """`<p>_core.<entity>`: master_id, status, survivor_id, the column attributes, then the `_` columns.

    A table only ever gains columns at the end: an attribute a later model version adds is appended
    after `_updated_at` (`add_column_sql`), so the order here is the order of a table created fresh.
    """
    ident(model.entity)
    columns = [
        _n("master_id", "text"),
        _n("status", "text", check=_in("status", ("active", "retired", "merged"))),
        _c("survivor_id", "text"),
    ]
    columns.extend(_c(ident(a.name), attribute_type(a.type, a.repeating)) for a in model.column_attributes())
    columns.extend(
        (
            _n("_commit_version", "bigint"),
            _n("_row_version", "bigint"),
            _n("_initial_load", "boolean"),
            _n("_created_at", "timestamptz"),
            _n("_updated_at", "timestamptz"),
        )
    )
    return Table("core", model.entity, tuple(columns), ("master_id",))


def masked_view_sql(
    model: EntityModel, prefix: str, ordered_columns: Sequence[str], engine: str = "postgres"
) -> str:
    """CREATE OR REPLACE VIEW <p>_read.<entity>, every column listed explicitly in the table's ordinal order.

    Postgres can only append view columns, so a model-order list breaks after ADD COLUMN. Personal text
    columns become `left(c, 1) || '***'`; every other personal type `CAST(NULL AS <type>)`. `engine`
    spells the type of a masked non-text column (JSON on DuckDB, jsonb on Postgres).
    """
    ident(model.entity)
    personal = {a.name: a for a in model.column_attributes() if a.personal}
    expressions = []
    for name in ordered_columns:
        ident(name)
        attribute = personal.get(name)
        if attribute is None:
            expressions.append(name)
            continue
        logical = attribute_type(attribute.type, attribute.repeating)
        if logical == "text":
            expressions.append(
                f"CASE WHEN {name} IS NULL THEN NULL ELSE left({name}, 1) || '***' END AS {name}"
            )
        else:
            expressions.append(f"CAST(NULL AS {sql_type(logical, engine)}) AS {name}")
    return (
        f"CREATE OR REPLACE VIEW {schema_name(prefix, 'read')}.{model.entity} AS SELECT "
        + ", ".join(expressions)
        + f" FROM {schema_name(prefix, 'core')}.{model.entity}"
    )


def passthrough_views_sql(prefix: str, columns: Mapping[str, Sequence[str]]) -> list[str]:
    """<p>_read.xref, retired_id and relationship with explicit column lists (Postgres freezes SELECT *)."""
    return [
        f"CREATE OR REPLACE VIEW {schema_name(prefix, 'read')}.{name} AS SELECT "
        + ", ".join(columns[name])
        + f" FROM {schema_name(prefix, 'core')}.{name}"
        for name in PASSTHROUGH_VIEWS
        if name in columns
    ]


def add_column_sql(table: Table, column: Column, prefix: str, engine: str) -> str:
    """ALTER TABLE … ADD COLUMN IF NOT EXISTS: a column is only ever appended.

    An added column is nullable whatever its declaration, since the rows already there have no value.
    """
    ident(table.name)
    definition = _column_sql(
        Column(column.name, column.type, True, column.default, column.check), prefix, engine, False
    )
    return f"ALTER TABLE {qualified(prefix, table)} ADD COLUMN IF NOT EXISTS {definition}"


def quote_role(name: str) -> str:
    """A role name as a quoted identifier (a service principal's role is its application ID)."""
    if not name or "\x00" in name:
        raise ValueError("a role name must be a non-empty text")
    return '"' + name.replace('"', '""') + '"'


def grants_sql(
    prefix: str,
    *,
    hub_role: str,
    reader_roles: Sequence[str],
    notifier_role: str | None,
    people_roles: Sequence[str] = (),
) -> list[str]:
    """Postgres only: the listener interface's grants (B.8.1).

    USAGE and SELECT on <p>_core for the reader roles; USAGE on <p>_core and SELECT on
    <p>_core.commit_log alone for the change notifier, which announces versions and never reads a
    record (least access), with no default privileges; USAGE and SELECT on <p>_read for people; and
    ALTER DEFAULT PRIVILEGES FOR ROLE <hub_role> IN SCHEMA <p>_core GRANT SELECT ON TABLES TO <reader
    roles>, so a new entity's table is readable without a manual grant. `people_roles` (an addition
    to the plan's signature) are the roles people read `<p>_read` through; the same default
    privileges cover the views a new entity adds there.
    """
    core = schema_name(prefix, "core")
    read = schema_name(prefix, "read")
    hub = quote_role(hub_role)
    statements: list[str] = []
    for role in reader_roles:
        statements.append(f"GRANT USAGE ON SCHEMA {core} TO {quote_role(role)}")
        statements.append(f"GRANT SELECT ON ALL TABLES IN SCHEMA {core} TO {quote_role(role)}")
    if notifier_role:
        statements.append(f"GRANT USAGE ON SCHEMA {core} TO {quote_role(notifier_role)}")
        statements.append(f"GRANT SELECT ON {core}.commit_log TO {quote_role(notifier_role)}")
    for role in reader_roles:
        statements.append(
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {hub} IN SCHEMA {core} GRANT SELECT ON TABLES TO {quote_role(role)}"
        )
    for role in people_roles:
        statements.append(f"GRANT USAGE ON SCHEMA {read} TO {quote_role(role)}")
        statements.append(f"GRANT SELECT ON ALL TABLES IN SCHEMA {read} TO {quote_role(role)}")
        statements.append(
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {hub} IN SCHEMA {read} GRANT SELECT ON TABLES TO {quote_role(role)}"
        )
    return statements


def tables_of(groups: Sequence[str]) -> list[Table]:
    """The fixed tables of `groups`, in `TABLES` order."""
    wanted = set(groups)
    return [t for t in TABLES if t.group in wanted]


def all_ddl(prefix: str, engine: str, groups: Sequence[str] | None = None) -> list[str]:
    """Every statement of `groups` (default all), in dependency order: for `mdm ddl`.

    Schemas, then sequences, then tables with their indexes, then the passthrough views of `<p>_read`
    (which need the core tables). Entity tables come from the published models, not from here.
    """
    chosen = list(GROUPS) if groups is None else [g for g in GROUPS if g in set(groups)]
    unknown = set(groups or ()) - set(GROUPS)
    if unknown:
        raise ValueError(f"unknown groups: {sorted(unknown)}")
    if engine not in ENGINES:
        raise ValueError(f"unknown engine {engine!r}")
    statements = [render_schema(prefix, g) for g in chosen]
    statements.extend(render_sequence(prefix, g, name, engine) for g, name in SEQUENCES if g in chosen)
    for fixed in tables_of(chosen):
        statements.extend(render_table(fixed, prefix, engine))
    if "read" in chosen:
        statements.extend(
            passthrough_views_sql(
                prefix, {name: table("core", name).column_names() for name in PASSTHROUGH_VIEWS}
            )
        )
    return statements
