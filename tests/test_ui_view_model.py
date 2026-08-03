from __future__ import annotations

from open_mco.demo import build_demo_scenario
from open_mco.ui.view_model import active_segment_index, corridor_rows, segment_rows


def test_ui_state_is_consistent_and_honestly_labeled() -> None:
    scenario = build_demo_scenario()
    rows = segment_rows(scenario.route, scenario.result)
    corridors = corridor_rows(scenario.route, scenario.result)

    assert len(rows) == len(scenario.route.segments) == len(corridors)
    assert {row["decision"] for row in rows} == {"SYNTHETIC ELIGIBLE"}
    assert {row["boom"] for row in rows} == {"NOT MODELED"}
    assert all(row["end_km"] > row["start_km"] for row in rows)
    assert all(row["altitude_ft"] is not None for row in rows)


def test_active_segment_bounds_progress() -> None:
    scenario = build_demo_scenario()
    last_index = len(scenario.route.segments) - 1

    assert active_segment_index(scenario.route, -1) == 0
    assert active_segment_index(scenario.route, 0) == 0
    assert active_segment_index(scenario.route, 1) == last_index
    assert active_segment_index(scenario.route, 2) == last_index
