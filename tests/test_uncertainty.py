from __future__ import annotations

import pytest

from open_mco.uncertainty import summarize_candidate_scenarios


def test_empirical_scenario_summary_is_honestly_labeled() -> None:
    summary = summarize_candidate_scenarios(
        1.1, [1.2, 1.15, 1.05, 1.18], reliability_threshold=0.75
    )
    assert summary.success_rate == 0.75
    assert summary.accepted is True
    assert summary.worst_member == 2
    assert "NOT_VALIDATED" in summary.label


@pytest.mark.parametrize(
    ("values", "reliability", "nominal", "message"),
    [([], 0.95, 0, "one scenario"), ([1.1], 0, 0, "reliability"), ([1.1], 0.95, 2, "nominal")],
)
def test_scenario_validation(values, reliability, nominal, message) -> None:
    with pytest.raises(ValueError, match=message):
        summarize_candidate_scenarios(
            1.0, values, reliability_threshold=reliability, nominal_member=nominal
        )
