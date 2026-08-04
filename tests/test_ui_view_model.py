from __future__ import annotations

from open_mco.demo import build_demo_scenario
from open_mco.ui.view_model import (
    active_segment_index,
    atmosphere_metrics,
    corridor_rows,
    segment_rows,
)


def test_ui_state_is_consistent_and_honestly_labeled() -> None:
    scenario = build_demo_scenario()
    rows = segment_rows(scenario.route, scenario.result)
    corridors = corridor_rows(scenario.route, scenario.result)

    assert len(rows) == len(scenario.route.segments) == len(corridors)
    assert {row["decision"] for row in rows} == {"SYNTHETIC ELIGIBLE"}
    assert {row["boom"] for row in rows} == {"NOT MODELED"}
    assert all(row["end_km"] > row["start_km"] for row in rows)
    assert all(row["end_nmi"] > row["start_nmi"] for row in rows)
    assert all(row["altitude_ft"] is not None for row in rows)
    assert all(row["synthetic_score"] is not None for row in rows)


def test_active_segment_bounds_progress() -> None:
    scenario = build_demo_scenario()
    last_index = len(scenario.route.segments) - 1

    assert active_segment_index(scenario.route, -1) == 0
    assert active_segment_index(scenario.route, 0) == 0
    assert active_segment_index(scenario.route, 1) == last_index
    assert active_segment_index(scenario.route, 2) == last_index


def test_pacific_route_display_does_not_span_the_long_way_around() -> None:
    scenario = build_demo_scenario("sfo_hnd")
    rows = segment_rows(scenario.route, scenario.result)
    corridors = corridor_rows(scenario.route, scenario.result)

    assert all(abs(row["path"][1][0] - row["path"][0][0]) <= 180 for row in rows)
    assert all(
        current["path"][1][0] == following["path"][0][0]
        for current, following in zip(rows, rows[1:], strict=False)
    )
    route_longitudes = [point[0] for row in rows for point in row["path"]]
    assert max(route_longitudes) - min(route_longitudes) < 120
    assert all(
        max(point[0] for point in row["polygon"])
        - min(point[0] for point in row["polygon"])
        <= 180
        for row in corridors
    )


def test_atmosphere_metrics_distinguish_ambient_pressure_and_wind() -> None:
    scenario = build_demo_scenario()
    metrics = atmosphere_metrics(scenario.atmosphere, 15_240, 90)

    assert 100 < metrics["pressure_hpa"] < 130
    assert metrics["temperature_c"] < -50
    assert metrics["wind_speed_kt"] > 40
    assert metrics["along_wind_kt"] > 40
