"""Assistance plumbing: provider choice, masked prompts, the stub, a call log (owner: SERVICES, B.11).

Nothing a provider returns is committed (P6). Personal values never enter a
prompt (RULE10); `prompt.assert_masked` runs before every call (decision 17).
`mdm.agent` may import `mdm.models`, `mdm.config`, `mdm.capacity`,
`mdm.backend` and `mdm.services.privacy`.
"""

from mdm.agent.narrative import case_narrative
from mdm.agent.provider import choose_provider

__all__ = ["case_narrative", "choose_provider"]
