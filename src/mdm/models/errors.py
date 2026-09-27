"""The errors every package raises.

`MdmError` carries a `code` and safe fields, checked by `safety.safe` when the
error is made, so its text (`safety.safe_message`) never carries a personal
value: `MdmError("stale_row", master_id="PER-000012")` reads
`stale_row master_id=PER-000012`.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from mdm.models.safety import safe_detail, safe_message


class MdmError(Exception):
    """A refusal or failure with a code and safe fields; `str()` is `safe_message(code, **fields)`."""

    def __init__(self, code: str, **fields: Any) -> None:
        self.code = code
        self.fields: dict[str, Any] = safe_detail(**fields)
        self.message = safe_message(code, **self.fields)
        super().__init__(self.message)

    def __str__(self) -> str:
        return self.message

    def detail(self) -> dict[str, Any]:
        """The code and fields as a safe document, for `--json` output and job errors."""
        return {"code": self.code, **self.fields}


class ConfigError(MdmError):
    """A setting is missing or malformed (`mdm.config.Settings.from_env`)."""


class ModelError(MdmError):
    """An entity model or rule set is invalid; `problems` lists every problem found, each a safe token."""

    def __init__(self, problems: Iterable[str], code: str = "model_invalid") -> None:
        self.problems: list[str] = list(problems)
        super().__init__(code, count=len(self.problems), problems=tuple(self.problems))


class NotFound(MdmError):
    """A named model, version, record, task or attribute does not exist."""


class Conflict(MdmError):
    """An optimistic check failed; `keys` are the master IDs or source keys whose expected state moved."""

    def __init__(self, keys: Iterable[str], code: str = "conflict", **fields: Any) -> None:
        self.keys: tuple[str, ...] = tuple(keys)
        super().__init__(code, keys=self.keys, **fields)


class Forbidden(MdmError):
    """The actor's role, the checker, or the authority does not allow the action."""


class ContractViolation(MdmError):
    """A landing row breaks the landing interface; its reason is recorded as a reject."""


class CapacityError(MdmError):
    """An unbounded read, or a request over a declared limit."""


class GuardError(MdmError):
    """A write the store refuses (core outside the commit scope, landing outside the simulator, audit updated)."""


class PlatformRefused(MdmError):
    """A local-only action (persona, simulator, reset, landing DDL) asked of a shared store."""


class Busy(MdmError):
    """A run lease is held by another run."""


class EstimationError(MdmError):
    """Weight estimation did not converge; the fields carry the iterations and deltas per pass."""
