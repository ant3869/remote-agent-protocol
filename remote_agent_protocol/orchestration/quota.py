"""QuotaStrategy -- degrade toward local as reported cloud quota falls.

Never fabricates a quota number: today ``CopilotProvider.quota()`` always
returns ``None`` (the SDK documents no usage/cost endpoint), so this module
treats quota as "unknown" and falls back to a conservative, strategy-specific
baseline instead of inventing a percentage. If a provider later exposes real
quota data, the ``remaining_pct`` branch below starts degrading on real
numbers with no other change required.
"""

from __future__ import annotations

from dataclasses import dataclass

_STRATEGIES = ("economy", "balanced", "performance", "cloud_preferred")

# Whether cloud is allowed when quota is simply unknown, per strategy --
# conservative (favor local) for economy, permissive for cloud_preferred.
_UNKNOWN_QUOTA_BASELINE = {
    "economy": False,
    "balanced": True,
    "performance": True,
    "cloud_preferred": True,
}

# Remaining-quota floor (percent) below which cloud is withheld, per strategy.
_REMAINING_PCT_FLOOR = {
    "economy": 50.0,
    "balanced": 20.0,
    "performance": 5.0,
    "cloud_preferred": 0.0,
}


@dataclass
class QuotaDecision:
    """Whether cloud is allowed right now under a given quota strategy."""

    favor_local: bool
    cloud_allowed: bool
    reason: str


def evaluate(strategy: str, quota: dict | None) -> QuotaDecision:
    """Decide whether cloud is allowed under ``strategy`` given a quota snapshot.

    ``quota`` is whatever a provider's ``quota()`` returned -- ``None`` when
    the provider exposes nothing, or a dict that may carry ``remaining_pct``.
    """
    strategy = strategy if strategy in _STRATEGIES else "balanced"
    remaining_pct = quota.get("remaining_pct") if isinstance(quota, dict) else None
    if not isinstance(remaining_pct, (int, float)):
        allowed = _UNKNOWN_QUOTA_BASELINE[strategy]
        reason = "quota unavailable -- provider reports no usage data"
        return QuotaDecision(favor_local=not allowed, cloud_allowed=allowed, reason=reason)
    floor = _REMAINING_PCT_FLOOR[strategy]
    allowed = remaining_pct > floor
    return QuotaDecision(
        favor_local=not allowed,
        cloud_allowed=allowed,
        reason=f"{remaining_pct:.0f}% quota remaining vs {floor:.0f}% floor ({strategy})",
    )
