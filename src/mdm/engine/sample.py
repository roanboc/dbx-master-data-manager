"""The draw of quality samples and the bound on agreement (the matcher's checkpoint, story 3.2).

A decision is drawn for blind review when a keyed hash of what it decided, and when, falls under the share:
the same on both engines, the same when a page is planned again, and monotone in the share, so raising the
share only ever adds draws. The key (`MDM_SAMPLE_KEY`, empty on a local store) keeps a steward from computing
which of their decisions would be drawn from what the workbench shows them. The quality breaker trips when the one-sided upper confidence bound on agreement
(Wilson's score interval) falls below its threshold, so a few unlucky disagreements in a small window do not
trip it. Pure and deterministic: no input or output, no clock.
"""

from __future__ import annotations

import hashlib
import hmac
import math

from mdm.capacity import SAMPLE_BASIS


def draw_value(entity: str, subject: str, decision: str, occasion: str, key: str = "") -> int:
    """A 63-bit integer from `HMAC-SHA256(key, entity|subject|decision|occasion)`: its first 8 bytes, shifted
    right once.

    The subject is the record's source key (a keep-apart pair: its two master IDs, sorted and comma-joined);
    the occasion is the record's event ID (a keep-apart pair: its task ID)."""
    message = f"{entity}|{subject}|{decision}|{occasion}".encode()
    digest = hmac.new(key.encode(), message, hashlib.sha256).digest()
    return int.from_bytes(digest[:8], "big") >> 1


def drawn(value: int, share: float) -> bool:
    """`value % SAMPLE_BASIS < round(share * SAMPLE_BASIS)`: a share of 0 draws nothing, 1 draws everything."""
    return value % SAMPLE_BASIS < round(share * SAMPLE_BASIS)


def agreement_upper(agreed: int, reviewed: int, z: float) -> float:
    """The upper end of Wilson's score interval for `agreed` of `reviewed` at `z` (1.645: one-sided 95%);
    1.0 when nothing was reviewed."""
    if reviewed <= 0:
        return 1.0
    p = agreed / reviewed
    z2 = z * z
    centre = p + z2 / (2 * reviewed)
    spread = z * math.sqrt(p * (1 - p) / reviewed + z2 / (4 * reviewed * reviewed))
    return min(1.0, (centre + spread) / (1 + z2 / reviewed))


def below(agreed: int, reviewed: int, threshold: float, z: float) -> bool:
    """True when agreement is confidently below `threshold`: its upper bound is under it."""
    return agreement_upper(agreed, reviewed, z) < threshold
