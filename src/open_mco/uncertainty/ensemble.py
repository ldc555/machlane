"""Transparent empirical ensemble summaries without regulatory reliability claims."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class CandidateScenarioSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate_mach: float
    success_rate: float
    conservative_allowable_mach: float | None
    worst_member: int
    nominal_member: int
    number_of_members: int
    reliability_threshold: float
    accepted: bool
    label: str = "EMPIRICAL_SCENARIO_RATE_NOT_VALIDATED_REGULATORY_RELIABILITY"


def summarize_candidate_scenarios(
    candidate_mach: float,
    allowable_mach_by_member: list[float],
    *,
    reliability_threshold: float,
    nominal_member: int = 0,
) -> CandidateScenarioSummary:
    """Summarize finite-member outcomes using a conservative empirical quantile."""

    if not allowable_mach_by_member:
        raise ValueError("at least one scenario member is required")
    if not 0 < reliability_threshold <= 1:
        raise ValueError("reliability threshold must be in (0, 1]")
    if not 0 <= nominal_member < len(allowable_mach_by_member):
        raise ValueError("nominal member index is out of range")
    successes = [candidate_mach <= value for value in allowable_mach_by_member]
    success_rate = sum(successes) / len(successes)
    ordered = sorted(allowable_mach_by_member)
    index = max(0, int((1 - reliability_threshold) * len(ordered)) - 1)
    conservative = ordered[index]
    return CandidateScenarioSummary(
        candidate_mach=candidate_mach,
        success_rate=success_rate,
        conservative_allowable_mach=conservative,
        worst_member=min(range(len(ordered)), key=lambda i: allowable_mach_by_member[i]),
        nominal_member=nominal_member,
        number_of_members=len(allowable_mach_by_member),
        reliability_threshold=reliability_threshold,
        accepted=success_rate >= reliability_threshold,
    )
