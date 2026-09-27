"""The domain model: dataclasses and pure rules, no I/O, no SQL.

`mdm.models` imports nothing else from `mdm`; every other package imports it.
The submodules hold the types; this module re-exports the few helpers every
package uses.
"""

from mdm.models.canonical import canonical_json, iso, utcnow
from mdm.models.errors import (
    Busy,
    CapacityError,
    ConfigError,
    Conflict,
    ContractViolation,
    EstimationError,
    Forbidden,
    GuardError,
    MdmError,
    ModelError,
    NotFound,
    PlatformRefused,
)
from mdm.models.safety import safe, safe_detail, safe_message

__all__ = [
    "Busy",
    "CapacityError",
    "ConfigError",
    "Conflict",
    "ContractViolation",
    "EstimationError",
    "Forbidden",
    "GuardError",
    "MdmError",
    "ModelError",
    "NotFound",
    "PlatformRefused",
    "canonical_json",
    "iso",
    "safe",
    "safe_detail",
    "safe_message",
    "utcnow",
]
