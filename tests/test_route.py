from __future__ import annotations

import pytest

from open_mco.models import SegmentLimit
from open_mco.route import (
    corridor_geojson,
    get_mission,
    interpolate_position,
    list_missions,
    route_distance_m,
    route_from_waypoints,
)


def test_route_segmentation_interpolation_and_corridor() -> None:
    route = route_from_waypoints([(37.0, -97.0), (37.0, -96.0)], spacing_m=30_000)
    assert len(route.segments) == 3
    assert 88 < route.segments[0].bearing_deg < 92
    midpoint = interpolate_position(route, 0.5)
    assert midpoint[0] == pytest.approx(37.001, abs=0.01)
    limits = tuple(
        SegmentLimit(
            segment_id=segment.segment_id,
            selected_mach=1.1,
            selected_altitude_m=13000,
            status="PASS",
            candidate_evaluations=(),
        )
        for segment in route.segments
    )
    geojson = corridor_geojson(route, limits)
    assert len(geojson["features"]) == 3
    assert geojson["features"][0]["properties"]["legal_airspace_approval"] is False


def test_route_rejects_bad_inputs() -> None:
    with pytest.raises(ValueError, match="two waypoints"):
        route_from_waypoints([(0, 0)], spacing_m=1000)
    with pytest.raises(ValueError, match="positive"):
        route_from_waypoints([(0, 0), (0, 1)], spacing_m=0)
    with pytest.raises(ValueError, match="valid WGS-84"):
        route_from_waypoints([(91, 0), (0, 1)], spacing_m=1000)
    with pytest.raises(ValueError, match="distinct"):
        route_from_waypoints([(0, 0), (0, 0)], spacing_m=1000)


def test_mission_catalog_uses_real_endpoints_and_wgs84_distance() -> None:
    missions = list_missions()
    mission_ids = {mission.mission_id for mission in missions}
    assert mission_ids >= {
        "dfw_jfk",
        "dfw_lax",
        "lax_jfk",
        "jfk_lhr",
        "den_nrt",
        "bos_hnl",
    }
    assert "lax_hnl" not in mission_ids

    route = get_mission("dfw_jfk").build_route()
    assert route.waypoints == ((32.896801, -97.038002), (40.639447, -73.779317))
    assert route_distance_m(route) / 1000 == pytest.approx(2235, rel=0.01)


def test_pacific_mission_takes_short_antimeridian_path() -> None:
    route = get_mission("den_nrt").build_route()
    assert route_distance_m(route) / 1000 == pytest.approx(9310, rel=0.03)
    assert any(
        abs(segment.end_longitude - segment.start_longitude) > 180
        for segment in route.segments
    )
