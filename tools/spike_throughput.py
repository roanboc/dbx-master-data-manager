"""The throughput spike: land and arrive a large invented world, and report rates (owner: CLI, B.15).

    uv run python tools/spike_throughput.py --engine duckdb|postgres [--dsn DSN] --records 100000 \\
        [--seed 7] [--time-limit 1800] [--out .mdm/spike] [--models models]

1. A fresh store: a DuckDB file under --out, or a Postgres prefix `spike`
   (--dsn, else a throwaway server from tests/postgres_server.py; never
   MDM_POSTGRES_DSN), with allow_personas set for that store, which opens only
   on a server on this machine.
2. init, load and publish the starter models and code lists.
3. A world of about --records source records, initial_load=True; time `land`.
4. Time `arrival.run(bulk=True)`; candidates per record at 25/50/100 %, the
   largest and 99th-percentile block per pass, records capped.
5. Land 1,000 updates; time an incremental `arrival.run()`.
6. Evaluate both entities.
7. Write <out>/<engine>-<records>.json and print a Markdown row, with a linear
   extrapolation to 1,000,000; --time-limit stops cleanly and reports what finished.

The bulk arrival runs one landing batch per `run()`, so the time limit is
checked between batches and the candidate counts can be read as the store
fills. The counts come from wrapping, in this process only, the ranking of
candidates (`rank_candidates`, one call per record) and the store's key counts
(one per blocking key looked up): the tool measures, it changes nothing.

The world is sized from --records: the demo generator yields about 2.1
source records per person when there is one organisation for every four
persons (1.66 person records and 0.44 organisation records), so persons =
records / 2.1 and organisations = persons / 4.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import resource
import sys
import time
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # tests.postgres_server, for the throwaway server
    sys.path.insert(0, str(ROOT))

from mdm import capacity  # noqa: E402
from mdm.backend.factory import open_store  # noqa: E402
from mdm.backend.store import SqlStore  # noqa: E402
from mdm.config import Settings  # noqa: E402
from mdm.demo import DemoConfig, DemoWorld, evaluate, generate, land  # noqa: E402
from mdm.services import matching  # noqa: E402
from mdm.services.arrival import ArrivalReport  # noqa: E402
from mdm.services.context import Hub  # noqa: E402

MODELS = ROOT / "models"
RECORDS_PER_PERSON = 2.1
PERSONS_PER_ORGANISATION = 4
UPDATES = 1_000
TARGET = 1_000_000
MILESTONES = (0.25, 0.5, 1.0)
ENTITIES = ("person", "organisation")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Measure landing and arrival throughput on one engine.")
    parser.add_argument("--engine", choices=("duckdb", "postgres"), required=True)
    parser.add_argument(
        "--dsn", default=None, help="Postgres DSN of a server on this machine (else a throwaway server)"
    )
    parser.add_argument("--records", type=int, default=100_000)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--time-limit", type=int, default=1800, help="seconds; stops cleanly and reports")
    parser.add_argument("--out", type=Path, default=Path(".mdm/spike"))
    parser.add_argument(
        "--models",
        type=Path,
        default=MODELS,
        help="entity models and codelists/ (default: the starter models)",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------------------------- measuring


class Probe:
    """Candidate counts and block sizes, gathered by wrapping two calls for the length of the run."""

    def __init__(self) -> None:
        self.records = 0
        self.candidates = 0
        self.blocks: dict[tuple[str, str], int] = {}
        self._undo: list[Callable[[], None]] = []

    def attach(self, store: SqlStore) -> None:
        ranked = matching.rank_candidates

        def rank(shared: Any, cap: int) -> Any:
            kept, dropped = ranked(shared, cap)
            self.records += 1
            self.candidates += len(kept)
            return kept, dropped

        counted = store.key_counts

        def key_counts(entity: str, pass_keys: Any) -> dict[tuple[str, str], int]:
            found = counted(entity, pass_keys)
            for (pass_name, key), n in found.items():
                self.blocks[(f"{entity}.{pass_name}", key)] = n
            return found

        matching.rank_candidates = rank
        store.key_counts = key_counts  # type: ignore[method-assign]
        self._undo = [
            lambda: setattr(matching, "rank_candidates", ranked),
            lambda: delattr(store, "key_counts"),
        ]

    def detach(self) -> None:
        for undo in self._undo:
            undo()
        self._undo = []

    def mark(self) -> tuple[int, int]:
        return self.records, self.candidates

    def block_sizes(self) -> dict[str, dict[str, int]]:
        """Per entity and pass: the largest block and the 99th-percentile block among the keys looked up."""
        by_pass: dict[str, list[int]] = {}
        for (name, _), n in self.blocks.items():
            by_pass.setdefault(name, []).append(n)
        out: dict[str, dict[str, int]] = {}
        for name, sizes in sorted(by_pass.items()):
            sizes.sort()
            p99 = sizes[min(len(sizes) - 1, math.ceil(0.99 * len(sizes)) - 1)]
            out[name] = {"largest": sizes[-1], "p99": p99, "keys": len(sizes)}
        return out


def _mean(records: int, candidates: int) -> float | None:
    return round(candidates / records, 2) if records else None


def _peak_rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(peak / 1024 if sys.platform != "darwin" else peak / 1024 / 1024, 1)


# ---------------------------------------------------------------------------------------------- the run


def _fresh_store(args: argparse.Namespace) -> tuple[Settings, SqlStore, Callable[[], None]]:
    """A store nobody else uses: a new DuckDB file, or the Postgres prefix `spike` dropped first."""
    if args.engine == "duckdb":
        args.out.mkdir(parents=True, exist_ok=True)
        path = args.out / f"spike-{args.records}.duckdb"
        for leftover in (path, path.with_name(path.name + ".wal")):
            leftover.unlink(missing_ok=True)
        settings = Settings(backend="duckdb", duckdb_path=str(path), models_dir=str(MODELS))
        return settings, open_store(settings), lambda: None
    # never MDM_POSTGRES_DSN: the tool drops its prefix, so it takes a server only when named on purpose,
    # and the store refuses personas unless that server is on this machine (decision 13)
    dsn = args.dsn
    let_go: Callable[[], None] = lambda: None  # noqa: E731
    if not dsn:
        from tests.postgres_server import postgres_for_the_run

        found, let_go = postgres_for_the_run()
        if found is None:
            raise SystemExit("no Postgres: pass --dsn, or install Postgres 16 or later")
        dsn = found
    settings = Settings(
        backend="postgres",
        postgres_dsn=dsn,
        schema_prefix="spike",
        allow_personas=True,
        models_dir=str(MODELS),
    )
    store = open_store(settings)
    store.drop_all()
    return settings, store, let_go


def _publish_models(hub: Hub, models: Path) -> None:
    hub.codelists.load_dir(models / "codelists", hub.actor)
    for path in sorted(models.glob("*.yaml")):
        model = hub.registry.load_file(path, hub.actor)
        hub.registry.publish(model.entity, model.version, hub.actor)


def run(args: argparse.Namespace) -> dict[str, object]:
    """The measured figures, also written to <out>/<engine>-<records>.json."""
    started = time.monotonic()
    deadline = started + args.time_limit
    persons = max(1, round(args.records / RECORDS_PER_PERSON))
    organisations = max(1, round(persons / PERSONS_PER_ORGANISATION))
    result: dict[str, Any] = {
        "engine": args.engine,
        "records_asked": args.records,
        "persons": persons,
        "organisations": organisations,
        "seed": args.seed,
        "models": str(args.models),
        "time_limit": args.time_limit,
        "finished": [],
        "stopped": None,
    }
    settings, store, let_go = _fresh_store(args)
    probe = Probe()
    try:
        result["engine_version"] = store.server_version()
        store.init_schema(create_landing=True)
        hub = Hub.open(settings, store=store, as_role="data_owner")
        _publish_models(hub, args.models)
        result["finished"].append("init")

        config = DemoConfig(
            persons=persons,
            organisations=organisations,
            seed=args.seed,
            updates=0.0,
            deletes=0.0,
            initial_load=True,
        )
        clock = time.monotonic()
        world = generate(config)
        result["generate_seconds"] = round(time.monotonic() - clock, 2)
        clock = time.monotonic()
        landed = land(store, settings, world)
        landing_seconds = time.monotonic() - clock
        result.update(
            records_landed=landed,
            source_records=len(world.truth),
            landing_seconds=round(landing_seconds, 2),
            landing_rows_per_second=round(landed / landing_seconds) if landing_seconds else None,
        )
        result["finished"].append("land")

        probe.attach(store)
        # equal batches, at least eight, so the candidate counts can be read at 25, 50 and 100 % of the run
        batches_wanted = max(8, math.ceil(landed / capacity.BULK_BATCH))
        batch_size = max(1, math.ceil(landed / batches_wanted))
        result["batch_size"] = batch_size
        bulk = ArrivalReport()
        milestones: dict[str, float | None] = {}
        batches: list[dict[str, Any]] = []
        clock = time.monotonic()
        while True:
            if time.monotonic() > deadline:
                result["stopped"] = "time_limit during the bulk arrival"
                break
            before = probe.mark()
            batch_clock = time.monotonic()
            part = hub.arrival.run(started_by=hub.actor, max_batches=1, batch_size=batch_size, bulk=True)
            if part.read == 0 and part.settled == 0:
                break
            bulk.add(part)
            after = probe.mark()
            mean = _mean(after[0] - before[0], after[1] - before[1])
            share = bulk.read / landed if landed else 1.0
            batches.append(
                {
                    "read": bulk.read,
                    "seconds": round(time.monotonic() - batch_clock, 2),
                    "mean_candidates": mean,
                }
            )
            print(
                f"bulk arrival: {bulk.read:,} of {landed:,} read, batch {batches[-1]['seconds']} s, "
                f"{mean} candidates per record",
                file=sys.stderr,
                flush=True,
            )
            for milestone in MILESTONES:
                label = f"{round(milestone * 100)}%"
                if share >= milestone - 1e-9 and label not in milestones:
                    milestones[label] = mean
        arrival_seconds = time.monotonic() - clock
        bulk.seconds = arrival_seconds
        result.update(
            arrival_seconds=round(arrival_seconds, 2),
            arrival_records_per_second=round(bulk.read / arrival_seconds) if arrival_seconds else None,
            arrival=_report(bulk),
            mean_candidates=milestones,
            batches=batches,
            blocks=probe.block_sizes(),
            records_capped=bulk.candidates_capped,
        )
        if result["stopped"] is None:
            result["finished"].append("bulk arrival")

        if result["stopped"] is None and time.monotonic() < deadline:
            share = min(1.0, UPDATES / max(1, len(world.truth)))
            updated = generate(DemoConfig(**{**_fields(config), "updates": share}))
            # only the new events: the creations are landed already (a redelivery would be skipped, but
            # would use up landing sequence numbers and leave a gap for the reader to time out)
            news = DemoWorld(rows=updated.rows[len(world.rows) :], truth=updated.truth)
            landed_updates = land(store, settings, news)
            clock = time.monotonic()
            incremental = hub.arrival.run(started_by=hub.actor)
            seconds = time.monotonic() - clock
            result.update(
                updates_landed=landed_updates,
                incremental_seconds=round(seconds, 2),
                incremental_records_per_second=round(incremental.read / seconds) if seconds else None,
                incremental=_report(incremental),
            )
            result["finished"].append("incremental arrival")
            world = updated

        probe.detach()
        result["evaluation"] = {entity: evaluate(store, entity, world) for entity in ENTITIES}
        result["finished"].append("evaluation")
        result["golden_records"] = {entity: _active_golden(store, entity) for entity in ENTITIES}
        result["tasks"] = {
            f"{entity}.{kind}": n for (entity, kind), n in sorted(store.task_counts("open").items())
        }
        result["commits"] = store.last_commit_version()
        hub.close()
    finally:
        probe.detach()
        store.close()
        let_go()
    result.update(
        seconds=round(time.monotonic() - started, 1),
        peak_rss_mb=_peak_rss_mb(),
        python=platform.python_version(),
        cpu_count=os.cpu_count(),
        date=datetime.now(UTC).date().isoformat(),
    )
    result["projection"] = _projection(result)
    args.out.mkdir(parents=True, exist_ok=True)
    target = args.out / f"{args.engine}-{args.records}.json"
    target.write_text(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    result["written_to"] = str(target)
    return result


def _active_golden(store: SqlStore, entity: str) -> int:
    """Active golden records, counted exactly in keyset pages (Postgres's row estimates are estimates)."""
    count, after = 0, None
    while True:
        page = store.golden_page(entity, after, capacity.READ_PAGE, status="active")
        count += len(page)
        if len(page) < capacity.READ_PAGE:
            return count
        after = page[-1].master_id


def _fields(config: DemoConfig) -> dict[str, Any]:
    return {name: getattr(config, name) for name in config.__dataclass_fields__}


def _report(report: ArrivalReport) -> dict[str, Any]:
    fields = {name: getattr(report, name) for name in report.__dataclass_fields__}
    return {**fields, "tasks": dict(sorted(report.tasks.items()))}


def _projection(result: dict[str, Any]) -> dict[str, Any]:
    """Seconds for 1,000,000 records: linear, and linear with the candidate growth per doubling.

    growth = mean candidates per record at 100% of the run / at 50% (one doubling of the store), at least 1;
    projected = seconds x (1,000,000 / records) x min(growth ** log2(1,000,000 / records),
    MAX_CANDIDATES / mean candidates at 100%): a record never keeps more than MAX_CANDIDATES candidates.
    Both are rough: the time of a batch is not only its candidates, and stop keys cut the largest blocks.
    """
    records = result.get("records_landed") or 0
    seconds = result.get("arrival_seconds")
    if not records or not seconds or result.get("stopped"):
        return {"linear_seconds": None, "with_growth_seconds": None, "growth_per_doubling": None}
    scale = TARGET / records
    means = result.get("mean_candidates") or {}
    half, full = means.get("50%"), means.get("100%")
    growth = max(1.0, full / half) if half and full else 1.0  # never project an improvement
    linear = seconds * scale
    ceiling = capacity.MAX_CANDIDATES / full if full else 1.0
    grown = linear * max(1.0, min(growth ** max(0.0, math.log2(scale)), ceiling))
    return {
        "linear_seconds": round(linear),
        "with_growth_seconds": round(grown),
        "growth_per_doubling": round(growth, 3),
    }


# ---------------------------------------------------------------------------------------------- the report


HEADER = (
    "| engine | records landed | landing rows/s | arrival records/s | incremental records/s | commits | "
    "golden records | tasks by kind | largest block per pass | records capped | precision | recall | "
    "seconds | peak RSS MB | Python | engine version | CPU count | date |"
)


def markdown_row(result: dict[str, Any]) -> str:
    evaluation = result.get("evaluation") or {}
    kinds: Counter[str] = Counter()
    for name, n in (result.get("tasks") or {}).items():
        kinds[name.split(".", 1)[1]] += n
    blocks = "; ".join(f"{name} {b['largest']}" for name, b in (result.get("blocks") or {}).items())
    golden = result.get("golden_records") or {}

    def pair(key: str) -> str:
        return " / ".join(f"{evaluation[e][key]:.3f}" for e in ENTITIES if e in evaluation) or "n/a"

    cells = [
        result["engine"],
        f"{result.get('records_landed', 0):,}",
        f"{result.get('landing_rows_per_second') or 0:,}",
        f"{result.get('arrival_records_per_second') or 0:,}",
        f"{result.get('incremental_records_per_second') or 0:,}",
        str(result.get("commits", "n/a")),
        " / ".join(f"{golden.get(e, 0):,}" for e in ENTITIES),
        ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())) or "none",
        blocks or "n/a",
        str(result.get("records_capped", "n/a")),
        pair("precision"),
        pair("recall"),
        str(result.get("seconds")),
        str(result.get("peak_rss_mb")),
        result.get("python", ""),
        result.get("engine_version", ""),
        str(result.get("cpu_count")),
        result.get("date", ""),
    ]
    return "| " + " | ".join(cells) + " |"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = run(args)
    print(HEADER)
    print("|" + " --- |" * (HEADER.count("|") - 1))
    print(markdown_row(result))
    projection = result["projection"]
    if projection["linear_seconds"] is not None:
        print(
            f"\nProjected bulk arrival of {TARGET:,} records: {projection['linear_seconds']:,} s linear, "
            f"{projection['with_growth_seconds']:,} s with the candidate growth "
            f"({projection['growth_per_doubling']} per doubling)."
        )
    if result.get("stopped"):
        print(f"\nStopped: {result['stopped']}; finished: {', '.join(result['finished'])}.")
    print(f"\nWritten to {result['written_to']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
