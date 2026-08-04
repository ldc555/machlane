from __future__ import annotations

import pytest

from open_mco.atmosphere import SyntheticAtmosphereProvider
from open_mco.demo import build_demo_scenario
from open_mco.route import interpolate_position, route_from_waypoints
from open_mco.ui.view_model import (
    active_segment_index,
    aircraft_view,
    atmosphere_metrics,
    corridor_rows,
    display_longitude,
    mock_flight_state,
    mock_live_metrics,
    pressure_color,
    segment_rows,
)


def test_pressure_color_is_blue_low_and_red_high() -> None:
    low = pressure_color(110, 110, 125)
    middle = pressure_color(117.5, 110, 125)
    high = pressure_color(125, 110, 125)

    assert low[:3] == [37, 99, 235]
    assert middle[:3] == [226, 232, 240]
    assert high[:3] == [220, 38, 38]
    assert low[3] == middle[3] == high[3] == 225


def test_mock_flight_state_is_supersonic_only_in_cruise_corridor() -> None:
    inputs = {
        "route_distance_nmi": 1_200,
        "cruise_mach": 1.1,
        "cruise_altitude_ft": 50_000,
    }
    departure = mock_flight_state(0, **inputs)
    acceleration = mock_flight_state(0.125, **inputs)
    cruise = mock_flight_state(0.5, **inputs)
    arrival = mock_flight_state(1, **inputs)

    assert departure == {
        "phase": "Takeoff / initial climb",
        "mach": 0.2,
        "altitude_ft": 0.0,
        "supersonic": False,
    }
    assert float(acceleration["mach"]) < 1
    assert cruise["phase"] == "Supersonic cruise"
    assert cruise["mach"] == 1.1
    assert cruise["supersonic"] is True
    assert arrival["phase"] == "Approach / landing"
    assert arrival["mach"] == pytest.approx(0.2)
    assert arrival["altitude_ft"] == pytest.approx(0)
    assert arrival["supersonic"] is False


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


def test_demo_accepts_injected_atmosphere_provider() -> None:
    class RecordingAtmosphereProvider:
        name = "recording"

        def __init__(self) -> None:
            self.calls: list[tuple[float, float]] = []
            self.delegate = SyntheticAtmosphereProvider()

        def profile(self, latitude, longitude, valid_time):
            self.calls.append((latitude, longitude))
            return self.delegate.profile(latitude, longitude, valid_time)

    provider = RecordingAtmosphereProvider()
    scenario = build_demo_scenario("jfk_lhr", atmosphere_provider=provider)

    assert provider.calls
    assert len(scenario.weather_regimes) == len(scenario.route.segments)


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
    observed_track = route_from_waypoints([(40, -170), (40, 170)], spacing_m=200_000)
    scenario = build_demo_scenario(route_override=observed_track)
    route = scenario.route
    _, reference = interpolate_position(route, 0.5)
    samples = [aircraft_view(route, index / 240, reference) for index in range(241)]

    for current, following in zip(samples, samples[1:], strict=False):
        # A failed antimeridian unwrap would show up as a ~360° jump between adjacent samples.
        assert abs(following["display_longitude"] - current["display_longitude"]) < 30.0
        assert 0.0 <= current["bearing_deg"] < 360.0


def test_pacific_route_display_does_not_span_the_long_way_around() -> None:
    observed_track = route_from_waypoints([(40, -170), (40, 170)], spacing_m=200_000)
    scenario = build_demo_scenario(route_override=observed_track)
    rows = segment_rows(scenario.route, scenario.result)
    corridors = corridor_rows(scenario.route, scenario.result)

    assert all(
        abs(following[0] - current[0]) <= 180
        for row in rows
        for current, following in zip(row["path"], row["path"][1:], strict=False)
    )
    assert all(
        current["path"][-1][0] == following["path"][0][0]
        for current, following in zip(rows, rows[1:], strict=False)
    )
    route_longitudes = [point[0] for row in rows for point in row["path"]]
    assert max(route_longitudes) - min(route_longitudes) < 120
    assert all(
        max(point[0] for point in row["polygon"]) - min(point[0] for point in row["polygon"]) <= 180
        for row in corridors
    )


def test_weather_segments_are_variable_and_explain_their_boundaries() -> None:
    scenario = build_demo_scenario("jfk_lhr")
    lengths_nmi = [round(segment.distance_m / 1852) for segment in scenario.route.segments]

    assert len(scenario.weather_regimes) == len(scenario.route.segments) >= 4
    assert len(set(lengths_nmi)) > 1
    assert scenario.weather_regimes[0].boundary_reason.startswith("Departure")
    assert any("changed" in regime.boundary_reason for regime in scenario.weather_regimes[1:])
    assert len(scenario.segment_atmospheres) == len(scenario.route.segments)


def test_weather_segments_retain_observed_polyline_without_shortcuts() -> None:
    observed_track = route_from_waypoints(
        [(34.0, -118.0), (42.0, -140.0), (49.0, -165.0), (45.0, 175.0), (36.0, 140.0)],
        spacing_m=50_000,
    )
    scenario = build_demo_scenario(route_override=observed_track)
    rendered_points = [point for segment in scenario.route.segments for point in segment.path]

    assert scenario.route.waypoints == observed_track.waypoints
    assert (
        abs(
            sum(segment.distance_m for segment in scenario.route.segments)
            - sum(segment.distance_m for segment in observed_track.segments)
        )
        < 1.0
    )
    assert all(waypoint in rendered_points for waypoint in observed_track.waypoints)
    assert all(segment.path for segment in scenario.route.segments)


def test_atmosphere_metrics_distinguish_ambient_pressure_and_wind() -> None:
    scenario = build_demo_scenario()
    metrics = atmosphere_metrics(scenario.atmosphere, 15_240, 90)

    assert 100 < metrics["pressure_hpa"] < 130
    assert metrics["temperature_c"] < -50
    assert metrics["wind_speed_kt"] > 40
    assert metrics["along_wind_kt"] > 40


def test_mock_live_metrics_are_smooth_reproducible_and_route_specific() -> None:
    baseline = {
        "pressure_hpa": 118.0,
        "temperature_c": -56.2,
        "wind_speed_kt": 49.0,
        "along_wind_kt": 48.0,
    }

    first = mock_live_metrics(baseline, "dfw_jfk", 0.42)
    repeated = mock_live_metrics(baseline, "dfw_jfk", 0.42)
    adjacent = mock_live_metrics(baseline, "dfw_jfk", 0.43)
    other_route = mock_live_metrics(baseline, "jfk_lhr", 0.42)

    assert first == repeated
    assert first != adjacent
    assert first != other_route
    assert abs(first["pressure_hpa"] - adjacent["pressure_hpa"]) < 1.0
    assert abs(first["temperature_c"] - adjacent["temperature_c"]) < 1.0
