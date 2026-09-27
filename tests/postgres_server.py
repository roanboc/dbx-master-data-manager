"""A Postgres for the test run, so the Lakebase engine is proved on the real dialect.

Lakebase is Postgres, so nothing plays it. `MDM_TEST_POSTGRES` names a running
server (a service container in CI); otherwise the run starts one of its own —
`initdb` and `pg_ctl` from the PATH or the usual install locations, on a unix
socket in a temporary directory, as an unprivileged user when the run is root,
since Postgres refuses to run as root — and stops it at the end. Without
either, the Postgres half of the suite is skipped with a warning, or fails when
`MDM_REQUIRE_POSTGRES=1` (CI), and the run says so.

Adapted from the EA Repository's `tests/postgres_server.py`.
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

ENV_DSN = "MDM_TEST_POSTGRES"
ENV_REQUIRE = "MDM_REQUIRE_POSTGRES"
NO_POSTGRES = (
    "no Postgres for the Postgres engine: install PostgreSQL 16 or later (initdb and pg_ctl) "
    "or point MDM_TEST_POSTGRES at a server; the store tests then run on DuckDB only"
)
BIN_GLOBS = [
    "/usr/lib/postgresql/*/bin",
    "/usr/pgsql-*/bin",
    "/usr/local/pgsql/bin",
    "/opt/homebrew/opt/postgresql@*/bin",
    "/usr/local/opt/postgresql@*/bin",
    "/opt/homebrew/bin",
    "/Applications/Postgres.app/Contents/Versions/*/bin",
]
PORT = 5432  # only names the socket file: the server listens on no TCP port
USER = "mdm"
UNPRIVILEGED_USER = "nobody"


def _version_of(bin_dir: Path) -> tuple[int, ...]:
    found = re.search(r"(\d+)(?:\.(\d+))?", bin_dir.parent.name)
    return tuple(int(n) for n in found.groups() if n) if found else (0,)


def find_pg_bin() -> Path | None:
    """The directory holding initdb and pg_ctl: the PATH first, then the newest installed version."""
    if sys.platform.startswith("win"):
        return None
    on_path = shutil.which("pg_ctl")
    if on_path and shutil.which("initdb"):
        return Path(on_path).resolve().parent
    candidates = [
        Path(p) for pattern in BIN_GLOBS for p in glob.glob(pattern) if (Path(p) / "initdb").exists()
    ]
    return max(candidates, key=_version_of, default=None)


class EphemeralPostgres:
    """One server in a temporary directory, started with `start()` and gone after `stop()`."""

    def __init__(self, bin_dir: Path):
        self.bin_dir = bin_dir
        self.root = Path(tempfile.mkdtemp(prefix="mdm-pg-"))
        self.data = self.root / "data"
        self._as_user: dict[str, object] = {}
        if getattr(os, "geteuid", lambda: 1)() == 0:
            import pwd

            account = pwd.getpwnam(UNPRIVILEGED_USER)
            os.chown(self.root, account.pw_uid, account.pw_gid)
            self._as_user = {"user": account.pw_uid, "group": account.pw_gid, "extra_groups": []}
        self.root.chmod(0o700)

    @property
    def dsn(self) -> str:
        return f"host={self.root} port={PORT} user={USER} dbname=postgres"

    def _run(self, *args: str) -> None:
        subprocess.run(
            [str(self.bin_dir / args[0]), *args[1:]],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "LC_ALL": "C", "LANG": "C"},
            **self._as_user,  # type: ignore[arg-type]
        )

    def start(self) -> str:
        self._run("initdb", "-D", str(self.data), "-U", USER, "--auth=trust", "-E", "UTF8", "--no-locale")
        options = (
            f"-k {self.root} -p {PORT} -c listen_addresses='' "
            "-c fsync=off -c synchronous_commit=off -c full_page_writes=off -c max_connections=200"
        )
        self._run(
            "pg_ctl",
            "-D",
            str(self.data),
            "-l",
            str(self.root / "log"),
            "-w",
            "-t",
            "60",
            "-o",
            options,
            "start",
        )
        self.root.chmod(0o755)  # the client is whoever runs the tests
        return self.dsn

    def stop(self) -> None:
        try:
            self._run("pg_ctl", "-D", str(self.data), "-m", "immediate", "-w", "stop")
        except (subprocess.CalledProcessError, OSError):
            pass
        shutil.rmtree(self.root, ignore_errors=True)


def postgres_for_the_run() -> tuple[str | None, Callable[[], None]]:
    """(a DSN, how to let it go): the server the environment names, else one started here, else nothing."""
    named = os.environ.get(ENV_DSN, "").strip()
    if named:
        return named, lambda: None
    bin_dir = find_pg_bin()
    if bin_dir is None:
        return None, lambda: None
    try:
        server = EphemeralPostgres(bin_dir)
    except (KeyError, OSError):  # no unprivileged account to run as, or no temporary directory
        return None, lambda: None
    try:
        return server.start(), server.stop
    except (subprocess.CalledProcessError, OSError):
        server.stop()
        return None, lambda: None


def postgres_required() -> bool:
    """True when a missing Postgres must fail the run rather than skip it (CI sets MDM_REQUIRE_POSTGRES=1)."""
    return os.environ.get(ENV_REQUIRE, "") == "1"
