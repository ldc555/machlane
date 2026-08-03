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
from open_mco.route import route_from_waypoints
from open_mco.terrain import FlatTerrainProvider

DEMO_VALID_TIME = datetime(2026, 8, 3, 12, tzinfo=UTC)
DEMO_MACH_VALUES = (1.02, 1.05, 1.08, 1.10, 1.12, 1.15)
DEMO_ALTITUDES_M = (12_192, 12_802, 13_411, 14_021, 14_630, 15_240)


@dataclass(frozen=True)
class DemoScenario:
    """The single normalized scenario shared by CLI, UI, reports, and tests."""

    aircraft: AircraftModel
    route: Route
    result: PlannerResult
    atmosphere: AtmosphericProfile
    terrain: TerrainProfile


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
    csv_path: str | Path = "data/examples/route.csv", *, spacing_m: float = 100_000
) -> Route:
    frame = pd.read_csv(csv_path)
    waypoints = list(zip(frame["latitude"], frame["longitude"], strict=True))
    return route_from_waypoints(
        waypoints, spacing_m=spacing_m, name="Transcontinental U.S. research route"
    )


def build_demo_scenario() -> DemoScenario:
    """Build the deterministic scenario once for any presentation or export surface."""

    aircraft = synthetic_aircraft()
    route = demo_route()
    weather = SyntheticAtmosphereProvider()
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
    return DemoScenario(
        aircraft=aircraft,
        route=route,
        result=result,
        atmosphere=weather.profile(37.0, -96.0, DEMO_VALID_TIME),
        terrain=terrain_provider.profile(route.segments[0]),
    )


def run_demo(*, results_root: str | Path = "results") -> Path:
    """Run the complete synthetic path and return its evidence-package directory."""

    scenario = build_demo_scenario()
    return write_evidence_package(
        aircraft=scenario.aircraft,
        route=scenario.route,
        result=scenario.result,
        atmosphere_source=scenario.atmosphere.source,
        terrain_source=scenario.terrain.source,
        configuration_path=Path("configs/baseline.yml"),
        results_root=Path(results_root),
    )
