from __future__ import annotations

from datetime import UTC, datetime

import pytest

from open_mco.atmosphere import SyntheticAtmosphereProvider, project_wind_onto_bearing
from open_mco.demo import synthetic_aircraft
from open_mco.models import PropagationRequest
from open_mco.optimization import GridSearchPlanner
from open_mco.physics import FastMCOEngine, MockMCOEngine
from open_mco.route import route_from_waypoints
from open_mco.terrain import FlatTerrainProvider


def test_wind_projection() -> None:
    assert project_wind_onto_bearing(10, 5, 90) == pytest.approx(10)
    assert project_wind_onto_bearing(10, 5, 0) == pytest.approx(5)


def test_grid_planner_preserves_rejections_and_selects_fastest() -> None:
    aircraft = synthetic_aircraft()
    route = route_from_waypoints([(37, -97), (37, -96.9)], spacing_m=20_000)
    planner = GridSearchPlanner(
        atmosphere_provider=SyntheticAtmosphereProvider(),
        terrain_provider=FlatTerrainProvider(),
        propagation_engine=MockMCOEngine(),
    )
    result = planner.plan(
        aircraft,
        route,
        mach_values=[0.9, 1.05, 1.5],
        altitude_m=[9000, 13000, 15000],
        reliability_level=0.95,
        valid_time=datetime(2026, 1, 1, tzinfo=UTC),
    )
    limit = result.segment_limits[0]
    assert limit.selected_mach == 1.05
    assert any(
        item.reason == "outside aircraft Mach limits" for item in limit.candidate_evaluations
    )
    assert any(
        item.reason == "outside aircraft altitude limits" for item in limit.candidate_evaluations
    )
    assert result.label == "SYNTHETIC_NOT_FOR_ENGINEERING_USE"


def test_fast_engine_refuses_unimplemented_physics() -> None:
    aircraft = synthetic_aircraft()
    segment = route_from_waypoints([(37, -97), (37, -96.9)], spacing_m=20_000).segments[0]
    atmosphere = SyntheticAtmosphereProvider().profile(37, -97, datetime(2026, 1, 1, tzinfo=UTC))
    terrain = FlatTerrainProvider().profile(segment)
    request = PropagationRequest(
        aircraft=aircraft,
        segment=segment,
        atmosphere=atmosphere,
        terrain=terrain,
        mach=1.1,
        altitude_m=13000,
    )
    with pytest.raises(NotImplementedError):
        FastMCOEngine().evaluate(request)


def test_planner_validates_settings() -> None:
    planner = GridSearchPlanner(
        atmosphere_provider=SyntheticAtmosphereProvider(),
        terrain_provider=FlatTerrainProvider(),
        propagation_engine=MockMCOEngine(),
    )
    route = route_from_waypoints([(37, -97), (37, -96.9)], spacing_m=20_000)
    with pytest.raises(ValueError, match="reliability"):
        planner.plan(
            synthetic_aircraft(),
            route,
            mach_values=[1.1],
            altitude_m=[13000],
            reliability_level=0,
            valid_time=datetime.now(UTC),
        )
