"""Network-free synthetic vertical slice shared by the CLI, UI and tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from open_mco.atmosphere import SyntheticAtmosphereProvider
from open_mco.compliance import write_evidence_package
from open_mco.models import (
    AircraftModel,
    AircraftOperatingLimits,
    AtmosphericProfile,
    PlannerResult,
    Route,
    SourcedValue,
    TerrainProfile,
)
from open_mco.optimization import GridSearchPlanner
from open_mco.physics import MockMCOEngine
from open_mco.route import (
    WeatherRegimeSummary,
    WeatherSegmentationSettings,
    get_mission,
    interpolate_position,
    interpolate_segment_position,
    route_from_waypoints,
    segment_route_by_weather,
)
from open_mco.terrain import FlatTerrainProvider

DEMO_VALID_TIME = datetime(2026, 8, 3, 12, tzinfo=UTC)
DEMO_MACH_VALUES = (1.02, 1.05, 1.08, 1.10, 1.12, 1.15)
DEMO_ALTITUDES_M = (12_192, 12_802, 13_411, 14_021, 14_630, 15_240)
DEMO_WEATHER_SAMPLE_SPACING_M = 185_200
DEMO_WEATHER_SETTINGS = WeatherSegmentationSettings()


@dataclass(frozen=True)
class DemoScenario:
    """The single normalized scenario shared by CLI, UI, reports, and tests."""

    aircraft: AircraftModel
    route: Route
    result: PlannerResult
    atmosphere: AtmosphericProfile
    terrain: TerrainProfile
    weather_regimes: tuple[WeatherRegimeSummary, ...]
    segment_atmospheres: tuple[AtmosphericProfile, ...]


def _synthetic_value(value: float | str, unit: str) -> SourcedValue:
    return SourcedValue(
        original_value=value,
        original_unit=unit,
        value_si=value,
        si_unit=unit,
        source_name="MachLane synthetic demo",
        source_document="generated in src/open_mco/demo.py",
        retrieved_at=datetime.now(UTC),
        checksum="SYNTHETIC",
    )


def synthetic_aircraft() -> AircraftModel:
    """Return a fictional aircraft labeled so it cannot be mistaken for NASA data."""

    return AircraftModel(
        name=_synthetic_value("MachLane Demo Aircraft", "dimensionless"),
        manufacturer=_synthetic_value("Synthetic", "dimensionless"),
        dimensions={},
        operating_limits=AircraftOperatingLimits(
            mtow=_synthetic_value(55_000, "kg"),
            oew=_synthetic_value(30_000, "kg"),
            maximum_operating_mach=_synthetic_value(1.8, "dimensionless"),
            maximum_cruise_mach=_synthetic_value(1.6, "dimensionless"),
            minimum_sustained_supersonic_mach=_synthetic_value(1.0, "dimensionless"),
            service_ceiling=_synthetic_value(17_000, "m"),
            minimum_cruise_altitude=_synthetic_value(10_000, "m"),
        ),
        workbook_checksum="SYNTHETIC_NOT_A_WORKBOOK",
    )


def demo_route(
    csv_path: str | Path | None = None,
    *,
    mission_id: str = "dfw_jfk",
    spacing_m: float = DEMO_WEATHER_SAMPLE_SPACING_M,
) -> Route:
    """Build a weather-regime route or an explicitly supplied waypoint file."""

    if csv_path is None:
        sampled_route = get_mission(mission_id).build_route(spacing_m=spacing_m)
        route, _ = segment_route_by_weather(
            sampled_route,
            SyntheticAtmosphereProvider(),
            DEMO_VALID_TIME,
            settings=DEMO_WEATHER_SETTINGS,
        )
        return route
    frame = pd.read_csv(csv_path)
    waypoints = list(zip(frame["latitude"], frame["longitude"], strict=True))
    return route_from_waypoints(waypoints, spacing_m=spacing_m, name="Imported research route")


def build_demo_scenario(
    mission_id: str = "dfw_jfk", *, route_override: Route | None = None
) -> DemoScenario:
    """Build the deterministic scenario on a conceptual or explicitly imported route."""

    aircraft = synthetic_aircraft()
    weather = SyntheticAtmosphereProvider()
    if route_override is None:
        sampled_route = get_mission(mission_id).build_route(
            spacing_m=DEMO_WEATHER_SAMPLE_SPACING_M
        )
    else:
        sampled_route = route_from_waypoints(
            route_override.waypoints,
            spacing_m=DEMO_WEATHER_SAMPLE_SPACING_M,
            name=route_override.name,
            source=route_override.source,
        )
    route, weather_regimes = segment_route_by_weather(
        sampled_route,
        weather,
        DEMO_VALID_TIME,
        settings=DEMO_WEATHER_SETTINGS,
    )
    terrain_provider = FlatTerrainProvider()
    planner = GridSearchPlanner(
        atmosphere_provider=weather,
        terrain_provider=terrain_provider,
        propagation_engine=MockMCOEngine(),
    )
    result = planner.plan(
        aircraft,
        route,
        mach_values=list(DEMO_MACH_VALUES),
        altitude_m=list(DEMO_ALTITUDES_M),
        reliability_level=0.95,
        valid_time=DEMO_VALID_TIME,
    )
    midpoint_latitude, midpoint_longitude = interpolate_position(route, 0.5)
    segment_atmospheres = tuple(
        weather.profile(*interpolate_segment_position(segment), DEMO_VALID_TIME)
        for segment in route.segments
    )
    return DemoScenario(
        aircraft=aircraft,
        route=route,
        result=result,
        atmosphere=weather.profile(midpoint_latitude, midpoint_longitude, DEMO_VALID_TIME),
        terrain=terrain_provider.profile(route.segments[0]),
        weather_regimes=weather_regimes,
        segment_atmospheres=segment_atmospheres,
    )


def run_demo(*, results_root: str | Path = "results", mission_id: str = "dfw_jfk") -> Path:
    """Run the complete synthetic path and return its evidence-package directory."""

    scenario = build_demo_scenario(mission_id)
    return write_evidence_package(
        aircraft=scenario.aircraft,
        route=scenario.route,
        result=scenario.result,
        atmosphere_source=scenario.atmosphere.source,
        terrain_source=scenario.terrain.source,
        configuration_path=Path("configs/baseline.yml"),
        results_root=Path(results_root),
    )
