"""Assistance plumbing: provider choice, masked prompts, the stub, a call log (owner: SERVICES, B.11).

Nothing a provider returns is committed (P6). Personal values never enter a
prompt (RULE10); `prompt.assert_masked` runs before every call (decision 17).
`mdm.agent` may import `mdm.models`, `mdm.config`, `mdm.capacity`,
`mdm.backend` and `mdm.services.privacy`.
"""

from mdm.agent.narrative import case_narrative
from mdm.agent.prompt import MaskedPrompt, assert_masked, build_prompt
from mdm.agent.provider import (
    Provider,
    ServingEndpointProvider,
    StubProvider,
    Suggestion,
    choose_provider,
)

__all__ = [
    "MaskedPrompt",
    "Provider",
    "ServingEndpointProvider",
    "StubProvider",
    "Suggestion",
    "assert_masked",
    "build_prompt",
    "case_narrative",
    "choose_provider",
]
