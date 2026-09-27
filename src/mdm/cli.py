"""The command line `mdm` (owner: CLI, B.13).

Exit codes: 0 ok; 1 refused, invalid or a contract error (one sentence on
stderr, built from the error's code and safe fields, never a value); 2 usage.
Global options: `--as ROLE` (a persona; refused unless the store is local) and
`--json` (machine output: `canonical_json` of the dataclasses). Each command
opens `Hub.open(...)`. A record to match is read from a file or standard input,
never from an argument, so personal values stay out of shell history and the
process list.

What a person reads here is what `mdm_read` shows them: golden values and feed
rows are masked, unless `mdm record show --reveal` names an attribute and a
reason, which the access log records. An unexpected failure prints its type
only, since a driver's message may quote a value; call `mdm.cli.app()` from
Python to see the traceback.
"""

from __future__ import annotations

import json
import re
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml

from mdm import capacity
from mdm.backend import ddl as ddl_sql
from mdm.backend.factory import open_store
from mdm.config import LOCAL_HOSTS, Settings
from mdm.demo import DemoConfig, evaluate, generate, land
from mdm.models.canonical import canonical_json
from mdm.models.entity_model import NAME_RE
from mdm.models.errors import MdmError, NotFound, PlatformRefused
from mdm.models.records import SourceKey
from mdm.models.safety import SAFE_TEXT_RE
from mdm.models.tasks import TASK_KINDS
from mdm.services.context import Hub
from mdm.services.support import token

app = typer.Typer(
    name="mdm",
    help="Master Data Manager: explainable matching and golden records on DuckDB and Lakebase.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,  # a traceback's locals could hold a personal value
)
model_app = typer.Typer(help="Entity models: load, publish, list, show.", no_args_is_help=True)
rules_app = typer.Typer(help="Rule sets: show and publish.", no_args_is_help=True)
codelists_app = typer.Typer(help="Code-list copies.", no_args_is_help=True)
demo_app = typer.Typer(help="The demo world (local store only).", no_args_is_help=True)
task_app = typer.Typer(help="Tasks for people.", no_args_is_help=True)
record_app = typer.Typer(help="Golden records.", no_args_is_help=True)
feed_app = typer.Typer(help="The change feed, as a consumer reads it.", no_args_is_help=True)
tray_app = typer.Typer(help="The undo tray.", no_args_is_help=True)
app.add_typer(model_app, name="model")
app.add_typer(rules_app, name="rules")
app.add_typer(codelists_app, name="codelists")
app.add_typer(demo_app, name="demo")
app.add_typer(task_app, name="task")
app.add_typer(record_app, name="record")
app.add_typer(feed_app, name="feed")
app.add_typer(tray_app, name="tray")

RULE_KINDS = ("match", "survivorship", "validation")
ESTIMATION_METHODS = ("auto", "em", "identifier")
#: the reader the arrival job keeps its position under (services.landing.LandingReader.READER)
ARRIVAL_READER = "arrival"
#: at most this many tasks, candidates or feed rows in one answer
MAX_TASKS = capacity.READ_PAGE
MAX_CANDIDATES_SHOWN = 50
_MASTER_ID = re.compile(r"^[A-Z][A-Z0-9]{0,9}-[0-9]{1,12}\Z")
_NAME = re.compile(NAME_RE)
_CURSOR = re.compile(r"^([0-9]+):([0-9]+)\Z")
#: fixed columns of an entity table, shown apart from the attributes
_FIXED = ("master_id", "status", "survivor_id")


def _checked_name(value: str | None) -> str | None:
    """An entity, source, attribute or role name: lower case, digits, underscores. Checked before anything
    runs, so no free text can reach a refusal's fields."""
    if value is not None and not _NAME.match(value):
        raise typer.BadParameter("a name is lower-case letters, digits and underscores")
    return value


def _checked_names(values: list[str] | None) -> list[str] | None:
    for value in values or ():
        _checked_name(value)
    return values


def _checked_key(value: str | None) -> str | None:
    """A source key: letters, digits and . _ : = / + - only."""
    if value is not None and not (value and SAFE_TEXT_RE.match(value)):
        raise typer.BadParameter("a source key is letters, digits and . _ : = / + - only")
    return value


@dataclass
class CliState:
    as_role: str | None = None
    json: bool = False


@app.callback()
def _global(
    ctx: typer.Context,
    as_role: Annotated[
        str | None,
        typer.Option("--as", help="Act as a persona role (local store only).", callback=_checked_name),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Machine output (canonical JSON).")] = False,
) -> None:
    ctx.obj = CliState(as_role=as_role, json=json_output)


# ---------------------------------------------------------------------------------------------- plumbing


def _state(ctx: typer.Context) -> CliState:
    return ctx.obj if isinstance(ctx.obj, CliState) else CliState()


def _fail(state: CliState, error: MdmError) -> typer.Exit:
    """One sentence on stderr, from the error's code and safe fields; exit code 1."""
    if state.json:
        typer.echo(canonical_json({"error": error.detail()}), err=True)
    else:
        typer.echo(f"mdm: {error}", err=True)
    return typer.Exit(1)


@contextmanager
def _refusals(state: CliState) -> Iterator[None]:
    """An `MdmError`, or a file the command could not read, becomes one safe sentence and exit code 1."""
    try:
        yield
    except MdmError as error:
        raise _fail(state, error) from None
    except BrokenPipeError:  # the reader stopped reading (`mdm … | head`): the command line exits quietly
        raise
    except OSError as error:
        raise _fail(state, MdmError("file_unreadable", error=type(error).__name__)) from None
    except yaml.YAMLError:
        raise _fail(state, MdmError("bad_yaml")) from None


@contextmanager
def _hub(ctx: typer.Context, *, initialised: bool = True) -> Iterator[Hub]:
    """The hub over the store the environment names, as the persona `--as` (local store) or the signed-in
    user; closed afterwards. Refusals become exit code 1; a store `mdm init` never prepared is one."""
    state = _state(ctx)
    with _refusals(state):
        hub = Hub.open(Settings.from_env(), as_role=state.as_role)
        try:
            if initialised and not hub.store.table_columns("model", "entity_model"):
                raise MdmError("store_not_initialised", prefix=hub.store.prefix)
            yield hub
        finally:
            hub.close()


def _plain(value: Any) -> Any:
    """The JSON form of a value: dataclasses as fields, dates as ISO text, tuples as lists."""
    return json.loads(canonical_json(value))


def _emit(state: CliState, data: Any, lines: Callable[[], Sequence[str]]) -> None:
    """Canonical JSON with `--json`, else the human lines."""
    if state.json:
        typer.echo(canonical_json(data))
        return
    for line in lines():
        typer.echo(line)


def _yaml(doc: Any) -> str:
    return yaml.safe_dump(_plain(doc), sort_keys=False, allow_unicode=True, default_flow_style=False).rstrip()


def _counts(counts: Mapping[str, int]) -> str:
    return ", ".join(f"{k} {v}" for k, v in counts.items() if v) or "none"


def _store_line(settings: Settings, engine: str, prefix: str) -> str:
    where = f" {settings.duckdb_path}" if engine == "duckdb" else ""
    return f"store {engine}{where}, prefix {prefix}, {'local' if settings.local_mode else 'shared'}"


def _usage(message: str, param: str) -> typer.BadParameter:
    return typer.BadParameter(message, param_hint=param)


def _entity_of(hub: Hub, master_id: str) -> str:
    """The published entity whose model code starts the master ID (`PER-000012` -> person)."""
    code = master_id.split("-", 1)[0]
    for entity in hub.registry.published_entities():
        if hub.registry.published(entity).code == code:
            return entity
    raise NotFound("unknown_master_id_code", code=code)


def _masked_rows(hub: Hub, entity: str, rows: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Published rows as a person may read them: `PrivacyService.masked` of each."""
    model = hub.registry.published(entity)
    return {master_id: hub.privacy.masked(model, row) for master_id, row in rows.items()}


def _brief(values: Mapping[str, Any], limit: int = 8) -> str:
    """A row's attributes on one line, fixed and underscore columns left out."""
    shown = [
        f"{k}={_plain(v)}"
        for k, v in values.items()
        if k not in _FIXED and not k.startswith("_") and v not in (None, "", [], {})
    ]
    more = f" (+{len(shown) - limit})" if len(shown) > limit else ""
    return ", ".join(shown[:limit]) + more


# ---------------------------------------------------------------------------------------------- store


@app.command()
def init(
    ctx: typer.Context,
    models: Annotated[
        Path | None, typer.Option("--models", help="Load every *.yaml and DIR/codelists.")
    ] = None,
    publish: Annotated[
        bool, typer.Option("--publish/--no-publish", help="Publish the loaded models.")
    ] = True,
) -> None:
    """Create schemas and tables (landing only in the local mode); optionally load and publish models."""
    state = _state(ctx)
    if models is not None and not models.is_dir():
        raise _usage("--models names a directory of entity model files", "--models")
    with _hub(ctx, initialised=False) as hub:
        settings, store = hub.settings, hub.store
        store.init_schema(create_landing=settings.local_mode)
        due_times = hub.inbox.backfill_due_times()
        lists: dict[str, int] = {}
        loaded: list[dict[str, Any]] = []
        if models is not None:
            if (models / "codelists").is_dir():
                lists = hub.codelists.load_dir(models / "codelists", hub.actor)
            # file-name order: organisation before person, which references it
            for path in sorted(models.glob("*.yaml")):
                model = hub.registry.load_file(path, hub.actor)
                if publish:
                    model = hub.registry.publish(model.entity, model.version, hub.actor)
                loaded.append({"entity": model.entity, "version": model.version, "published": publish})
        data = {
            "engine": store.engine,
            "prefix": store.prefix,
            "local": settings.local_mode,
            "landing": settings.local_mode,
            "code_lists": lists,
            "models": loaded,
            "due_times": due_times,
        }

        def lines() -> list[str]:
            out = [_store_line(settings, store.engine, store.prefix)]
            if settings.local_mode:
                out.append("schemas and tables ready, landing table included")
            else:
                out.append(
                    "schemas and tables ready; the landing table belongs to the integration platform, "
                    "whose role creates it from `mdm ddl --group landing`"
                )
            out.extend(f"code list {name} v{version}" for name, version in lists.items())
            out.extend(
                f"model {m['entity']} v{m['version']} {'published' if m['published'] else 'draft'}"
                for m in loaded
            )
            if due_times:
                out.append(f"due times given to {due_times} open tasks")
            return out

        _emit(state, data, lines)


@app.command()
def whoami(ctx: typer.Context) -> None:
    """Actor, role, persona, engine, prefix, and whether the store is local or shared."""
    state = _state(ctx)
    with _hub(ctx) as hub:
        actor = hub.actor
        data = {
            "actor": _plain(actor),
            "engine": hub.store.engine,
            "prefix": hub.store.prefix,
            "store": "local" if hub.settings.local_mode else "shared",
        }
        _emit(
            state,
            data,
            lambda: [
                f"actor {actor.name} ({actor.kind}, role {actor.role}{', persona' if actor.persona else ''})",
                _store_line(hub.settings, hub.store.engine, hub.store.prefix),
            ],
        )


@app.command()
def status(ctx: typer.Context) -> None:
    """Row estimates, reader position and gaps, queue size, last commit version, open tasks, rejects."""
    state = _state(ctx)
    with _hub(ctx) as hub:
        hub.authority.require(hub.actor, "read")
        store = hub.store
        reader = store.reader_state(ARRIVAL_READER)
        landing = store.landing_max_seq() if store.table_columns("landing", "source_change") else None
        data = {
            "engine": store.engine,
            "prefix": store.prefix,
            "local": hub.settings.local_mode,
            "last_commit_version": store.last_commit_version(),
            "landing_max_seq": landing,
            "reader": {
                "high_water": reader.high_water,
                "low_water": reader.low_water,
                "reconciled_at": reader.reconciled_at,
                "gaps": store.gap_counts(ARRIVAL_READER),
            },
            "queue": store.queue_size(),
            "rejects": store.reject_count(),
            "tasks": {f"{entity}.{kind}": n for (entity, kind), n in store.task_counts("open").items()},
            "rows": store.row_estimates(),
        }

        def lines() -> list[str]:
            gaps = data["reader"]["gaps"]
            reconciled = _plain(reader.reconciled_at) if reader.reconciled_at else "never"
            return [
                _store_line(hub.settings, store.engine, store.prefix),
                f"last commit version {data['last_commit_version']}",
                f"landing: highest sequence {landing if landing is not None else 'unknown (no landing table)'}; "
                f"read up to {reader.high_water}, complete up to {reader.low_water}; "
                f"gaps open {gaps.get('open', 0)}, lost {gaps.get('lost', 0)}; reconciled {reconciled}",
                f"queued records {data['queue']}, rejects not replayed {data['rejects']}",
                f"open tasks: {_counts(data['tasks'])}",
                "rows: " + _counts({k: v for k, v in data["rows"].items() if v}),
            ]

        _emit(state, data, lines)


@app.command()
def ddl(
    ctx: typer.Context,
    group: Annotated[list[str] | None, typer.Option("--group", help="A schema group; repeatable.")] = None,
    grants: Annotated[bool, typer.Option("--grants", help="Print the Postgres grants instead.")] = False,
    hub_role: Annotated[str | None, typer.Option("--hub-role")] = None,
    reader_role: Annotated[list[str] | None, typer.Option("--reader-role", help="Repeatable.")] = None,
    notifier_role: Annotated[str | None, typer.Option("--notifier-role")] = None,
    people_role: Annotated[
        list[str] | None,
        typer.Option("--people-role", help="A role people read the masked views through; repeatable."),
    ] = None,
) -> None:
    """Print the DDL (e.g. the landing table for the integration platform's role) or the grants.

    Rendered from the settings alone: nothing connects to the store, so the integration team can print
    the landing table without access to it. Entity tables come from published models (`mdm init`).
    """
    state = _state(ctx)
    with _refusals(state):
        settings = Settings.from_env()
        if grants:
            if not hub_role or not reader_role:
                raise _usage("--grants needs --hub-role and at least one --reader-role", "--grants")
            statements = ddl_sql.grants_sql(
                settings.schema_prefix,
                hub_role=hub_role,
                reader_roles=reader_role,
                notifier_role=notifier_role,
                people_roles=people_role or (),
            )
        else:
            unknown = sorted(set(group or ()) - set(ddl_sql.GROUPS))
            if unknown:
                raise _usage(f"a group is one of {', '.join(ddl_sql.GROUPS)}", "--group")
            statements = ddl_sql.all_ddl(settings.schema_prefix, settings.backend, group or None)
        _emit(state, statements, lambda: [f"{s};" for s in statements])


# ---------------------------------------------------------------------------------------------- models and rules


@model_app.command("load")
def model_load(
    ctx: typer.Context,
    path: Annotated[Path, typer.Argument(help="An entity model YAML file.")],
    publish: Annotated[bool, typer.Option("--publish", help="Publish it after loading.")] = False,
) -> None:
    """Load a model as the next draft version."""
    state = _state(ctx)
    with _hub(ctx) as hub:
        model = hub.registry.load_file(path, hub.actor)
        if publish:
            model = hub.registry.publish(model.entity, model.version, hub.actor)
        data = {"entity": model.entity, "version": model.version, "published": publish}
        _emit(
            state,
            data,
            lambda: [f"model {model.entity} v{model.version} {'published' if publish else 'draft'}"],
        )


@model_app.command("publish")
def model_publish(
    ctx: typer.Context,
    entity: Annotated[str, typer.Argument(callback=_checked_name)],
    version: Annotated[int | None, typer.Option("--version")] = None,
) -> None:
    """Publish a draft model and its rule sets."""
    state = _state(ctx)
    with _hub(ctx) as hub:
        model = hub.registry.publish(entity, version, hub.actor)
        data = {"entity": model.entity, "version": model.version, "published": True}
        _emit(state, data, lambda: [f"model {model.entity} v{model.version} published"])


@model_app.command("list")
def model_list(ctx: typer.Context) -> None:
    """Entities and their model versions."""
    state = _state(ctx)
    with _hub(ctx) as hub:
        hub.authority.require(hub.actor, "read")
        data = {
            entity: [
                {"version": v, "status": s, "created_at": created}
                for v, s, created in hub.registry.versions(entity)
            ]
            for entity in hub.store.model_entities()
        }

        def lines() -> list[str]:
            if not data:
                return ["no entity models loaded"]
            return [
                f"{entity}: " + ", ".join(f"v{v['version']} {v['status']}" for v in versions)
                for entity, versions in data.items()
            ]

        _emit(state, _plain(data), lines)


@model_app.command("show")
def model_show(
    ctx: typer.Context,
    entity: Annotated[str, typer.Argument(callback=_checked_name)],
    version: Annotated[int | None, typer.Option("--version")] = None,
) -> None:
    """One model version (the published one by default)."""
    state = _state(ctx)
    with _hub(ctx) as hub:
        hub.authority.require(hub.actor, "read")
        model = hub.registry.published(entity) if version is None else hub.registry.version(entity, version)
        doc = model.to_dict()
        _emit(state, doc, lambda: [_yaml(doc)])


@rules_app.command("show")
def rules_show(
    ctx: typer.Context,
    entity: Annotated[str, typer.Argument(callback=_checked_name)],
    kind: Annotated[str, typer.Option("--kind", help="match | survivorship | validation")] = "match",
    version: Annotated[int | None, typer.Option("--version")] = None,
) -> None:
    """One rule set (the published one by default)."""
    state = _state(ctx)
    if kind not in RULE_KINDS:
        raise _usage(f"a kind is one of {', '.join(RULE_KINDS)}", "--kind")
    with _hub(ctx) as hub:
        hub.authority.require(hub.actor, "read")
        found = hub.store.rule_set_doc(entity, kind, version)
        if found is None:
            raise NotFound("rule_set_not_found", entity=entity, kind=kind, version=version)
        number, doc = found
        statuses = {v: s for v, s, _, _ in hub.store.rule_set_versions(entity, kind)}
        data = {
            "entity": entity,
            "kind": kind,
            "version": number,
            "status": statuses.get(number),
            "rules": doc,
        }
        _emit(
            state,
            data,
            lambda: [f"{entity} {kind} rules v{number} ({statuses.get(number, 'unknown')})", _yaml(doc)],
        )


@rules_app.command("publish")
def rules_publish(
    ctx: typer.Context,
    entity: Annotated[str, typer.Argument(callback=_checked_name)],
    kind: Annotated[str, typer.Option("--kind", help="match | survivorship | validation")],
    version: Annotated[int, typer.Option("--version")],
) -> None:
    """Publish a draft rule set (refused once the entity holds golden records)."""
    state = _state(ctx)
    if kind not in RULE_KINDS:
        raise _usage(f"a kind is one of {', '.join(RULE_KINDS)}", "--kind")
    with _hub(ctx) as hub:
        hub.registry.publish_rules(entity, kind, version, hub.actor)
        data = {"entity": entity, "kind": kind, "version": version, "published": True}
        _emit(state, data, lambda: [f"{entity} {kind} rules v{version} published"])


@codelists_app.command("load")
def codelists_load(ctx: typer.Context, directory: Annotated[Path, typer.Argument()]) -> None:
    """Load every code-list YAML in a directory as new versions."""
    state = _state(ctx)
    with _hub(ctx) as hub:
        if not directory.is_dir():
            raise NotFound("code_list_directory_not_found")
        loaded = hub.codelists.load_dir(directory, hub.actor)
        _emit(state, loaded, lambda: [f"code list {name} v{v}" for name, v in loaded.items()] or ["none"])


# ---------------------------------------------------------------------------------------------- demo


@demo_app.command("land")
def demo_land(
    ctx: typer.Context,
    persons: Annotated[int, typer.Option("--persons")] = 2000,
    organisations: Annotated[int, typer.Option("--organisations")] = 500,
    seed: Annotated[int, typer.Option("--seed")] = 7,
    initial_load: Annotated[bool, typer.Option("--initial-load")] = False,
    updates: Annotated[float, typer.Option("--updates")] = 0.1,
    deletes: Annotated[float, typer.Option("--deletes")] = 0.01,
    hard_cases: Annotated[
        float, typer.Option("--hard-cases", help="Share of records given an invented hard case, 0 to 1.")
    ] = 0.0,
    creations_only: Annotated[
        bool, typer.Option("--creations-only", help="Land the creations only, no later event.")
    ] = False,
) -> None:
    """Land an invented world into the landing table, as the integration platform would."""
    state = _state(ctx)
    config = _config(persons, organisations, seed, updates, deletes, initial_load, hard_cases, creations_only)
    with _hub(ctx) as hub:
        if hub.settings.local_mode and not hub.store.table_columns("landing", "source_change"):
            raise NotFound("landing_table_missing", prefix=hub.store.prefix)
        world = generate(config)
        landed = land(hub.store, hub.settings, world)
        data = {
            "rows": len(world.rows),
            "landed": landed,
            "already_landed": len(world.rows) - landed,
            "records": len(world.truth),
            "config": _plain(config),
            "counts": world.counts(),
        }
        _emit(
            state,
            data,
            lambda: [
                f"landed {landed} of {len(world.rows)} rows ({len(world.rows) - landed} already landed): "
                f"{persons} persons, {organisations} organisations, seed {seed}",
                "rows: " + _counts(data["counts"]),
            ],
        )


def _config(
    persons: int,
    organisations: int,
    seed: int,
    updates: float,
    deletes: float,
    initial_load: bool = False,
    hard_cases: float = 0.0,
    creations_only: bool = False,
) -> DemoConfig:
    try:
        return DemoConfig(
            persons=persons,
            organisations=organisations,
            seed=seed,
            updates=updates,
            deletes=deletes,
            initial_load=initial_load,
            hard_cases=hard_cases,
            creations_only=creations_only,
        )
    except ValueError:
        raise _usage(
            "counts are not negative; updates, deletes and hard cases are shares from 0 to 1", "--updates"
        ) from None


@demo_app.command("reset")
def demo_reset(
    ctx: typer.Context,
    yes: Annotated[bool, typer.Option("--yes", help="Confirm dropping the local store.")] = False,
) -> None:
    """Drop the local store."""
    state = _state(ctx)
    if not yes:
        raise _usage("dropping the store needs --yes", "--yes")
    with _refusals(state):
        settings = Settings.from_env()
        if not settings.local_mode:
            raise PlatformRefused("reset_refused", prefix=settings.schema_prefix)
        store = open_store(settings)
        try:
            store.drop_all()
        finally:
            store.close()
        data = {"dropped": True, "engine": settings.backend, "prefix": settings.schema_prefix}
        _emit(state, data, lambda: [f"dropped every schema of prefix {settings.schema_prefix}"])


@demo_app.command("evaluate")
def demo_evaluate(
    ctx: typer.Context,
    entity: Annotated[str, typer.Option("--entity", callback=_checked_name)],
    seed: Annotated[int, typer.Option("--seed")] = 7,
    persons: Annotated[int, typer.Option("--persons")] = 2000,
    organisations: Annotated[int, typer.Option("--organisations")] = 500,
    updates: Annotated[float, typer.Option("--updates")] = 0.1,
    deletes: Annotated[float, typer.Option("--deletes")] = 0.01,
) -> None:
    """Pairwise precision, recall and F1 against the regenerated truth."""
    state = _state(ctx)
    config = _config(persons, organisations, seed, updates, deletes)
    with _hub(ctx) as hub:
        hub.authority.require(hub.actor, "read")
        scores = evaluate(hub.store, entity, generate(config))
        _emit(
            state,
            {"entity": entity, **scores},
            lambda: [
                f"{entity}: precision {scores['precision']:.4f}, recall {scores['recall']:.4f}, "
                f"F1 {scores['f1']:.4f} ({scores['pairs']} pairs found, {scores['true_pairs']} true, "
                f"{scores['records']} records, {scores['linked']} linked)"
            ],
        )


# ---------------------------------------------------------------------------------------------- arrival and matching


def _report_lines(label: str, report: Any) -> list[str]:
    versions = (
        f" (versions {report.first_version}-{report.last_version})"
        if report.first_version is not None
        else ""
    )
    return [
        f"{label}: read {report.read}, rejected {report.rejected}, versions {report.versions}, "
        f"stale {report.stale}, queued {report.queued}, settled {report.settled}",
        f"  committed: created {report.created}, linked {report.linked}, updated {report.updated}, "
        f"detached {report.detached} in {report.commits} commits{versions}",
        f"  tasks: {_counts(dict(sorted(report.tasks.items())))}",
        f"  position: read up to {report.high_water}, complete up to {report.low_water}; "
        f"gaps open {report.gaps_open}, lost {report.gaps_lost}; records capped {report.candidates_capped}",
        f"  {report.seconds:.1f} s, {report.records_per_second:.0f} records/s",
    ]


def _report_data(report: Any) -> dict[str, Any]:
    return {**_plain(report), "records_per_second": report.records_per_second}


def _run_arrival(
    ctx: typer.Context,
    *,
    max_batches: int | None,
    batch_size: int | None,
    bulk: bool,
    replay_rejects: bool = False,
    reconcile: bool = False,
) -> None:
    state = _state(ctx)
    with _hub(ctx) as hub:
        reports: list[tuple[str, Any]] = []
        if replay_rejects:
            reports.append(("replay", hub.arrival.replay_rejects(started_by=hub.actor)))
        if reconcile:
            reports.append(("reconcile", hub.arrival.reconcile(started_by=hub.actor)))
        run = hub.arrival.run(started_by=hub.actor, max_batches=max_batches, batch_size=batch_size, bulk=bulk)
        if run.skipped_busy:
            _emit(state, {"skipped_busy": True}, lambda: ["arrival skipped: another run holds the lease"])
            return
        reports.append(("load" if bulk else "arrival", run))
        data = {label: _report_data(report) for label, report in reports}
        _emit(state, data, lambda: [line for label, r in reports for line in _report_lines(label, r)])


@app.command()
def arrive(
    ctx: typer.Context,
    max_batches: Annotated[int | None, typer.Option("--max-batches")] = None,
    batch_size: Annotated[int | None, typer.Option("--batch-size")] = None,
    bulk: Annotated[bool, typer.Option("--bulk")] = False,
    replay_rejects: Annotated[bool, typer.Option("--replay-rejects")] = False,
    reconcile: Annotated[bool, typer.Option("--reconcile")] = False,
) -> None:
    """Run the arrival job; a held lease prints one line and exits 0."""
    _run_arrival(
        ctx,
        max_batches=max_batches,
        batch_size=batch_size,
        bulk=bulk,
        replay_rejects=replay_rejects,
        reconcile=reconcile,
    )


@app.command()
def load(
    ctx: typer.Context,
    max_batches: Annotated[int | None, typer.Option("--max-batches")] = None,
) -> None:
    """`arrive --bulk`, for initial loads."""
    _run_arrival(ctx, max_batches=max_batches, batch_size=None, bulk=True)


def _read_record(state: CliState, record_file: str) -> dict[str, Any]:
    """A JSON object from a file, or from standard input for "-"; never from an argument."""
    with _refusals(state):
        text = sys.stdin.read() if record_file == "-" else Path(record_file).read_text(encoding="utf-8")
        try:
            record = json.loads(text)
        except ValueError:
            raise MdmError("bad_record_file", expected="json_object") from None
        if not isinstance(record, dict):
            raise MdmError("bad_record_file", expected="json_object")
        return record


def _explanation_lines(candidate: Any, masked: Mapping[str, Any]) -> list[str]:
    best = candidate.best
    ex = best.explanation
    out = [
        f"{candidate.master_id}  score {ex.score:.1f}  {ex.band}  "
        f"best member {best.right.text()} (members scored {candidate.members_scored})"
    ]
    if candidate.blocked_by:
        out.append(f"  blocked by {candidate.blocked_by}: never linked automatically")
    if ex.hard_rule:
        out.append(f"  decided by {ex.hard_rule}")
    out.append(f"  {'prior':<16} {'':<12} {ex.prior:+7.2f}")
    for c in ex.contributions:
        out.append(f"  {c.comparison:<16} {c.label:<12} {c.weight:+7.2f}")
    out.append(f"  {'total':<16} {'':<12} {ex.weight:+7.2f}   {ex.signature}")
    for cf in ex.counterfactuals:
        out.append(
            f"  would be {cf.band} ({cf.score:.1f}) if {cf.comparison} were {cf.to_label} ({cf.direction})"
        )
    if masked:
        out.append(f"  golden (masked): {_brief(masked)}")
    return out


@app.command()
def match(
    ctx: typer.Context,
    entity: Annotated[str, typer.Option("--entity", callback=_checked_name)],
    record_file: Annotated[
        str | None, typer.Option("--record-file", help="A JSON record file, or - for standard input.")
    ] = None,
    source: Annotated[str | None, typer.Option("--source", callback=_checked_name)] = None,
    key: Annotated[str | None, typer.Option("--key", callback=_checked_key)] = None,
    rules_version: Annotated[int | None, typer.Option("--rules-version")] = None,
    top: Annotated[int, typer.Option("--top")] = 5,
    narrative: Annotated[bool, typer.Option("--narrative")] = False,
) -> None:
    """Match test: score one record against the golden records, masked; writes one access-log row."""
    state = _state(ctx)
    stored = source is not None or key is not None
    if (record_file is None) == (not stored) or (stored and (source is None or key is None)):
        raise _usage(
            "give --record-file PATH (or - for standard input), or --source and --key", "--record-file"
        )
    if not 1 <= top <= MAX_CANDIDATES_SHOWN:
        raise _usage(f"--top is from 1 to {MAX_CANDIDATES_SHOWN}", "--top")
    payload = _read_record(state, record_file) if record_file is not None else None
    the_source = SourceKey(source, key) if stored and source and key else None
    with _hub(ctx) as hub:
        candidates = hub.matching.match_test(
            entity, actor=hub.actor, payload=payload, source=the_source, rules_version=rules_version, top=top
        )
        masked = hub.store.masked_rows(entity, [c.master_id for c in candidates]) if candidates else {}
        story = (
            _narrative(hub, entity, candidates[0], payload, the_source) if narrative and candidates else None
        )
        data = {
            "entity": entity,
            "candidates": [
                {
                    "master_id": c.master_id,
                    "members_scored": c.members_scored,
                    "blocked_by": c.blocked_by,
                    "best": {
                        "left": c.best.left.text(),
                        "right": c.best.right.text(),
                        "explanation": c.best.explanation.to_dict(),
                    },
                    "golden": masked.get(c.master_id, {}),
                }
                for c in candidates
            ],
            "narrative": _plain(story) if story else None,
        }

        def lines() -> list[str]:
            if not candidates:
                return ["no golden record scores above the distinct band's floor"]
            out = [line for c in candidates for line in _explanation_lines(c, masked.get(c.master_id, {}))]
            if story is not None:
                out.append(f"suggestion from {story.provider}, not a decision: {story.text}")
            return out

        _emit(state, data, lines)


def _narrative(
    hub: Hub, entity: str, candidate: Any, payload: Mapping[str, Any] | None, source: SourceKey | None
) -> Any:
    """The case narrative of the best candidate: masked prompt, labelled answer, logged call."""
    from mdm.agent import case_narrative, choose_provider

    model = hub.registry.published(entity)
    if payload is not None:
        left: Mapping[str, Any] = payload
    else:
        assert source is not None
        state = hub.store.source_states(entity, [source]).get(source)
        left = dict(state.values) if state is not None else {}
    golden = hub.store.golden(entity, [candidate.master_id]).get(candidate.master_id)
    right = dict(golden.values) if golden is not None else {}
    provider = choose_provider(hub.settings, model)
    return case_narrative(hub.store, provider, model, candidate, left, right, hub.actor)


@app.command()
def estimate(
    ctx: typer.Context,
    entity: Annotated[str, typer.Option("--entity", callback=_checked_name)],
    method: Annotated[str, typer.Option("--method", help="auto | em | identifier")] = "auto",
    sample: Annotated[int | None, typer.Option("--sample")] = None,
) -> None:
    """Estimate match weights into a draft rule set."""
    state = _state(ctx)
    if method not in ESTIMATION_METHODS:
        raise _usage(f"a method is one of {', '.join(ESTIMATION_METHODS)}", "--method")
    if sample is not None and sample < 2:
        raise _usage("--sample is at least 2 records", "--sample")
    with _hub(ctx) as hub:
        options: dict[str, Any] = {"actor": hub.actor, "method": method}
        if sample is not None:
            options["sample_records"] = sample
        version, rules = hub.estimation.estimate(entity, **options)
        data = {"entity": entity, "version": version, "status": "draft", "rules": _plain(rules)}

        def lines() -> list[str]:
            out = [f"{entity} match rules v{version} drafted; publish with `mdm rules publish {entity} "
                   f"--kind match --version {version}`", f"prior {rules.prior:.3g}"]  # fmt: skip
            for c in rules.comparisons:
                m = ", ".join(f"{x:.4f}" for x in c.m)
                u = ", ".join(f"{x:.3g}" for x in c.u)
                out.append(f"  {c.name:<16} m [{m}]  u [{u}]")
            if rules.estimation:
                out.append(_yaml({"estimation": rules.estimation}))
            return out

        _emit(state, data, lines)


@app.command()
def profile(
    ctx: typer.Context,
    entity: Annotated[str, typer.Option("--entity", callback=_checked_name)],
    source: Annotated[str | None, typer.Option("--source", callback=_checked_name)] = None,
) -> None:
    """Profile source records: counts, patterns, masked top values."""
    state = _state(ctx)
    with _hub(ctx) as hub:
        result = hub.profiling.profile(entity, source, actor=hub.actor)

        def lines() -> list[str]:
            out = [f"{result.entity}{' from ' + result.source_system if result.source_system else ''}: "
                   f"{result.records} records"]  # fmt: skip
            for a in result.attributes:
                distinct = f"{a.distinct}{'+' if a.distinct_capped else ''}"
                lengths = f", length {a.min_length}-{a.max_length}" if a.min_length is not None else ""
                out.append(f"  {a.name:<16} filled {a.filled}, empty {a.empty}, distinct {distinct}{lengths}")
                if a.patterns:
                    out.append("    patterns: " + ", ".join(f"{p} {n}" for p, n in a.patterns[:5]))
                if a.top:
                    out.append("    top: " + ", ".join(f"{v} {n}" for v, n in a.top[:5]))
            return out

        _emit(state, _plain(result), lines)


# ---------------------------------------------------------------------------------------------- tasks, records, feed


@task_app.command("list")
def task_list(
    ctx: typer.Context,
    entity: Annotated[str | None, typer.Option("--entity", callback=_checked_name)] = None,
    kind: Annotated[str | None, typer.Option("--kind")] = None,
    limit: Annotated[int, typer.Option("--limit")] = 50,
) -> None:
    """Open tasks."""
    state = _state(ctx)
    if kind is not None and kind not in TASK_KINDS:
        raise _usage(f"a kind is one of {', '.join(TASK_KINDS)}", "--kind")
    if not 1 <= limit <= MAX_TASKS:
        raise _usage(f"--limit is from 1 to {MAX_TASKS}", "--limit")
    with _hub(ctx) as hub:
        hub.authority.require(hub.actor, "read")
        tasks = hub.store.tasks(entity, kind, "open", limit, None)

        def lines() -> list[str]:
            if not tasks:
                return ["no open task"]
            return [
                f"{t.task_id}  {t.entity} {t.kind}  {t.source.text() if t.source else '-'}  "
                f"{','.join(t.master_ids) or '-'}  {t.reason}"
                for t in tasks
            ]

        _emit(state, _plain(tasks), lines)


@record_app.command("show")
def record_show(
    ctx: typer.Context,
    master_id: Annotated[str, typer.Argument()],
    entity: Annotated[str | None, typer.Option("--entity", callback=_checked_name)] = None,
    reveal: Annotated[
        list[str] | None,
        typer.Option("--reveal", help="An attribute to reveal; repeatable.", callback=_checked_names),
    ] = None,
    reason: Annotated[str | None, typer.Option("--reason")] = None,
) -> None:
    """Golden row, members and provenance; masked unless revealed with a reason."""
    state = _state(ctx)
    if not _MASTER_ID.match(master_id):
        raise _usage("a master ID looks like PER-000012", "MASTER_ID")
    with _hub(ctx) as hub:
        the_entity = entity or _entity_of(hub, master_id)
        view = hub.privacy.record_view(
            the_entity, master_id, actor=hub.actor, reveal=tuple(reveal or ()), reason=reason or ""
        )
        _emit(state, _plain(view), lambda: [_yaml(view)])


@feed_app.command("read")
def feed_read(
    ctx: typer.Context,
    since: Annotated[int, typer.Option("--since")] = 0,
    cursor: Annotated[str | None, typer.Option("--cursor", help="V:S from the previous page.")] = None,
    entity: Annotated[str | None, typer.Option("--entity", callback=_checked_name)] = None,
    limit: Annotated[int, typer.Option("--limit")] = 5000,
) -> None:
    """A page of the change feed; prints the --since and --cursor to pass next."""
    state = _state(ctx)
    position: tuple[int, int] | None = None
    if cursor is not None:
        found = _CURSOR.match(cursor)
        if not found:
            raise _usage("a cursor is V:S, e.g. 12:40", "--cursor")
        position = (int(found.group(1)), int(found.group(2)))
    if since < 0:
        raise _usage("--since is a commit version, 0 or more", "--since")
    if not 1 <= limit <= capacity.FEED_PAGE_ROWS:
        raise _usage(f"--limit is from 1 to {capacity.FEED_PAGE_ROWS}", "--limit")
    with _hub(ctx) as hub:
        hub.authority.require(hub.actor, "read")
        page = hub.feed.read(since, cursor=position, entity=entity, max_rows=limit)
        rows = {kind: _masked_rows(hub, kind, current) for kind, current in page.rows.items()}
        following = ["--since", str(page.next_watermark)]
        if page.cursor is not None:
            following += ["--cursor", f"{page.cursor[0]}:{page.cursor[1]}"]
        data = {
            "commits": _plain(page.commits),
            "changes": _plain(page.changes),
            "rows": _plain(rows),
            "next_watermark": page.next_watermark,
            "cursor": list(page.cursor) if page.cursor is not None else None,
            "next": following,
        }

        def lines() -> list[str]:
            out: list[str] = []
            changes_of: dict[int, list[Any]] = {}
            for change in page.changes:
                changes_of.setdefault(change.commit_version, []).append(change)
            for commit in page.commits:
                load = ", initial load" if commit.initial_load else ""
                out.append(
                    f"commit {commit.commit_version}  {_plain(commit.committed_at)}  {commit.actor_kind} "
                    f"{commit.actor_role}{load}  {commit.change_count} changes  [{commit.authority_kind}]"
                )
                for c in changes_of.get(commit.commit_version, []):
                    row = rows.get(c.entity, {}).get(c.master_id, {})
                    survivor = f" -> {c.survivor_id}" if c.survivor_id else ""
                    out.append(
                        f"  {c.change_seq:>4} {c.entity} {c.master_id} {c.change_kind}{survivor} "
                        f"[{', '.join(c.parts)}]  {_brief(row, 4)}"
                    )
            if not out:
                out.append(f"no change after version {since}")
            out.append("next: mdm feed read " + " ".join(following))
            return out

        _emit(state, data, lines)


# ---------------------------------------------------------------------------------------------- the workbench


def _refuse(state: CliState, error: MdmError, sentence: str) -> typer.Exit:
    """One plain sentence on stderr (the error's code and fields with `--json`); exit code 1."""
    if state.json:
        typer.echo(canonical_json({"error": error.detail()}), err=True)
    else:
        typer.echo(f"mdm: {sentence}", err=True)
    return typer.Exit(1)


@app.command()
def ui(
    ctx: typer.Context,
    host: Annotated[
        str, typer.Option("--host", help="The address to listen on: loopback only.")
    ] = "127.0.0.1",
    port: Annotated[int | None, typer.Option("--port", min=1, max=65535, help="Default MDM_UI_PORT.")] = None,
    dev: Annotated[bool, typer.Option("--dev", help="Dash's development tools (local store only).")] = False,
    worker: Annotated[
        bool | None,
        typer.Option(
            "--worker/--no-worker", help="Flush the undo tray in this process (default: local store)."
        ),
    ] = None,
) -> None:
    """Serve the steward workbench in a browser. It listens on a loopback address unless a Databricks App runs it."""
    state = _state(ctx)
    with _refusals(state):
        settings = Settings.from_env()
        if state.as_role:
            settings = settings.with_(role=state.as_role)
        if settings.role and not settings.local_mode:
            raise _refuse(
                state,
                PlatformRefused("persona_refused", role=token(settings.role)),
                f"{'--as' if state.as_role else 'MDM_ROLE'} names a persona, and personas work only on a local store.",
            )
        if host not in LOCAL_HOSTS and not settings.in_databricks_app:
            raise _refuse(
                state,
                PlatformRefused("workbench_needs_loopback", host=token(host)),
                "The workbench listens on 127.0.0.1 only, because whoever can reach it acts as you "
                "(or as any persona on a local store).",
            )
        if dev and not settings.local_mode:
            raise _refuse(
                state,
                PlatformRefused("dev_needs_local_store"),
                "Dash's development tools work only on a local store.",
            )
        engine = _workbench_store(state, settings)
    port = port or settings.ui_port
    who = f"persona {settings.role or 'data_steward'}" if settings.local_mode else "the signed-in user"
    typer.echo(f"the workbench is on http://{host}:{port} ({engine}, {who}); Ctrl-C stops it")
    from mdm.ui.server import serve  # here, so the command line starts without Dash when not asked

    serve(settings, host=host, port=port, dev=dev, worker=worker)


def _workbench_store(state: CliState, settings: Settings) -> str:
    """The engine's name when the store holds the workbench's tables; else one sentence and exit 1."""
    import duckdb  # the driver's own error for a file another process holds

    try:
        hub = Hub.open(settings)
    except duckdb.IOException:
        if settings.backend == "duckdb" and Path(settings.duckdb_path).exists():
            raise _refuse(
                state,
                MdmError("store_in_use"),
                "The store is in use by another process; stop it, or point MDM_DUCKDB_PATH elsewhere.",
            ) from None
        raise
    try:
        if not hub.store.table_columns("work", "tray_entry"):
            raise _refuse(
                state,
                MdmError("workbench_tables_missing", prefix=hub.store.prefix),
                "The store has no workbench tables yet; run `mdm init --models models` first.",
            )
        return hub.badges().engine
    finally:
        hub.close()


def _flush_line(report: Any) -> str:
    if report.skipped_busy:
        return "another flush holds the tray; nothing done"
    failures = {code: n for code, n in sorted(report.outcomes.items()) if code != "committed" and n}
    why = f" ({_counts(failures)})" if failures else ""
    return (
        f"flushed: committed {report.committed}, failed {report.failed}{why}, "
        f"records queued again {report.requeued}"
    )


@tray_app.command("flush")
def tray_flush(
    ctx: typer.Context,
    watch: Annotated[
        float | None,
        typer.Option("--watch", min=0.5, help="Seconds between passes; runs until stopped."),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=capacity.READ_PAGE)] = capacity.FLUSH_BATCH,
) -> None:
    """Commit the staged decisions whose undo window has passed (a job runs this on the platform)."""
    state = _state(ctx)
    with _hub(ctx) as hub:
        try:
            while True:
                report = hub.tray.flush(started_by=hub.actor, limit=limit)
                _emit(state, _plain(report), lambda: [_flush_line(report)])  # noqa: B023 - called at once
                if watch is None:
                    return
                time.sleep(watch)
        except KeyboardInterrupt:  # --watch runs until stopped
            return


def main() -> None:
    """The console script: an `MdmError` becomes one sentence on stderr and exit code 1; any other failure
    prints its type only (a driver's message may quote a value)."""
    try:
        app()
    except MdmError as error:
        typer.echo(f"mdm: {error}", err=True)
        raise SystemExit(1) from None
    except Exception as error:  # noqa: BLE001 - the last line of defence for personal values
        typer.echo(f"mdm: unexpected_error type={type(error).__name__}", err=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
