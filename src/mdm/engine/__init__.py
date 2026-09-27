"""The matching engine: pure, deterministic functions, no I/O and no clock (owner: ENGINE, B.9).

`mdm.engine` may import `mdm.models` and `mdm.capacity`, never `mdm.backend`.
The names below are the interface SERVICES and CLI use (C.3), plus a few
helpers the B.9.5 procedure and the arrival's fast path need.
"""

from mdm.engine.blocking import (
    blocking_keys,
    fixed_comparisons,
    key_attributes,
    keys_from_forms,
    rank_candidates,
)
from mdm.engine.cluster import ClusterInput, Resolution, resolve_batch
from mdm.engine.compare import compare
from mdm.engine.estimate import (
    EMResult,
    Levels,
    combine_passes,
    em,
    estimate_u,
    estimated_rules,
    global_prior,
    m_from_identifier,
    pair_levels,
    posteriors,
    scale_to_population,
    u_pairs,
    union_expected_matches,
    value_frequencies,
)
from mdm.engine.quality import check
from mdm.engine.score import (
    CompiledRules,
    band_of,
    band_of_weight,
    compile_rules,
    explain,
    fast_weight,
    probability,
    score_pair,
    worth_explaining,
)
from mdm.engine.standardise import sample_hash, standardise_record
from mdm.engine.survive import Member, survive

__all__ = [
    "ClusterInput",
    "CompiledRules",
    "EMResult",
    "Levels",
    "Member",
    "Resolution",
    "band_of",
    "band_of_weight",
    "blocking_keys",
    "check",
    "combine_passes",
    "compare",
    "compile_rules",
    "em",
    "estimate_u",
    "estimated_rules",
    "explain",
    "fast_weight",
    "fixed_comparisons",
    "global_prior",
    "key_attributes",
    "keys_from_forms",
    "m_from_identifier",
    "pair_levels",
    "posteriors",
    "probability",
    "rank_candidates",
    "resolve_batch",
    "sample_hash",
    "scale_to_population",
    "score_pair",
    "standardise_record",
    "survive",
    "u_pairs",
    "union_expected_matches",
    "value_frequencies",
    "worth_explaining",
]
