"""The landing simulator: writes a demo world as the integration platform would (owner: CLI, B.14).

The only code in the hub that writes the landing tables, and only through
`guard.simulating_integration_platform`, which refuses a shared store: the
local mode, or the live suite's own run prefix. Rows are inserted as the
landing interface says the integration platform inserts them: `ON CONFLICT
(event_id) DO NOTHING`, one short transaction per batch, never an update.
"""

from __future__ import annotations

from mdm import capacity
from mdm.backend import guard
from mdm.backend.store import SqlStore
from mdm.config import Settings
from mdm.demo.generator import DemoWorld


def land(store: SqlStore, settings: Settings, world: DemoWorld, batch: int = 10_000) -> int:
    """Inside `guard.simulating_integration_platform`; refused on a shared store. Returns the rows landed.

    A row whose event ID is already landed is skipped, so landing the same world twice lands nothing
    the second time, and a world generated again with more updates lands only the new events.
    """
    landed = 0
    with guard.simulating_integration_platform(settings, store.prefix):
        for rows in capacity.chunks(world.rows, batch):
            with store.transaction():
                landed += store.landing_insert(rows)
    return landed
