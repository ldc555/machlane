from __future__ import annotations

import pytest

from open_mco.models import SegmentLimit
from open_mco.route import corridor_geojson, interpolate_position, route_from_waypoints


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
