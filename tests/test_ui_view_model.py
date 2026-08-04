from __future__ import annotations

import pytest

from open_mco.demo import build_demo_scenario
from open_mco.route import interpolate_position
from open_mco.ui.view_model import (
    active_segment_index,
    aircraft_view,
    atmosphere_metrics,
    corridor_rows,
    display_longitude,
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


def test_aircraft_view_matches_geodesic_position_and_segment_bearing() -> None:
    scenario = build_demo_scenario()
    route = scenario.route
    _, reference = interpolate_position(route, 0.5)
    for progress in (0.0, 0.1, 0.37, 0.5, 0.83, 1.0):
        view = aircraft_view(route, progress, reference)
        latitude, longitude = interpolate_position(route, progress)
        index = active_segment_index(route, progress)
        assert view["latitude"] == latitude
        assert view["longitude"] == longitude
        assert view["bearing_deg"] == route.segments[index].bearing_deg
        assert 0.0 <= view["bearing_deg"] < 360.0
        assert view["display_longitude"] == display_longitude(longitude, reference)
        assert abs(view["display_longitude"] - reference) <= 180.0


def test_aircraft_view_endpoints_align_with_route() -> None:
    scenario = build_demo_scenario()
    route = scenario.route
    _, reference = interpolate_position(route, 0.5)
    start = aircraft_view(route, 0.0, reference)
    end = aircraft_view(route, 1.0, reference)

    assert start["latitude"] == pytest.approx(route.segments[0].start_latitude, abs=1e-6)
    assert start["longitude"] == pytest.approx(route.segments[0].start_longitude, abs=1e-6)
    assert end["latitude"] == pytest.approx(route.segments[-1].end_latitude, abs=1e-6)


def test_aircraft_view_is_continuous_across_the_dateline() -> None:
    scenario = build_demo_scenario("den_nrt")
    route = scenario.route
    _, reference = interpolate_position(route, 0.5)
    samples = [aircraft_view(route, index / 240, reference) for index in range(241)]

    for current, following in zip(samples, samples[1:], strict=False):
        # A failed antimeridian unwrap would show up as a ~360° jump between adjacent samples.
        assert abs(following["display_longitude"] - current["display_longitude"]) < 30.0
        assert 0.0 <= current["bearing_deg"] < 360.0


def test_pacific_route_display_does_not_span_the_long_way_around() -> None:
    scenario = build_demo_scenario("den_nrt")
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


def test_weather_segments_are_variable_and_explain_their_boundaries() -> None:
    scenario = build_demo_scenario("den_nrt")
    lengths_nmi = [round(segment.distance_m / 1852) for segment in scenario.route.segments]

    assert len(scenario.weather_regimes) == len(scenario.route.segments) >= 4
    assert len(set(lengths_nmi)) > 1
    assert scenario.weather_regimes[0].boundary_reason.startswith("Departure")
    assert any("changed" in regime.boundary_reason for regime in scenario.weather_regimes[1:])
    assert len(scenario.segment_atmospheres) == len(scenario.route.segments)


def test_atmosphere_metrics_distinguish_ambient_pressure_and_wind() -> None:
    scenario = build_demo_scenario()
    metrics = atmosphere_metrics(scenario.atmosphere, 15_240, 90)

    assert 100 < metrics["pressure_hpa"] < 130
    assert metrics["temperature_c"] < -50
    assert metrics["wind_speed_kt"] > 40
    assert metrics["along_wind_kt"] > 40
