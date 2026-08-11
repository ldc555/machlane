"""Aircraft data adapters."""

from .definition_workbook import (
    export_aircraft_definition_workbook,
    load_aircraft_definition_workbook,
)
from .flight_plan import (
    FlightPhaseEstimate,
    FlightPlanEstimate,
    SceneEnvironment,
    SceneState,
    estimate_flight_plan,
    planned_state_at_progress,
    speed_of_sound_knots,
)
from .loader import AircraftWorkbookError, load_aircraft_workbook
from .specification import (
    AircraftDefinition,
    AircraftField,
    AircraftStore,
    AtmosphereBenchmarkProfile,
    NearFieldSample,
    PerformancePoint,
    PhasePoint,
    PhaseTiming,
    nasa_stca_aircraft_1,
)

__all__ = [
    "AircraftDefinition",
    "AircraftField",
    "AircraftStore",
    "AtmosphereBenchmarkProfile",
    "AircraftWorkbookError",
    "FlightPhaseEstimate",
    "FlightPlanEstimate",
    "NearFieldSample",
    "PerformancePoint",
    "PhasePoint",
    "PhaseTiming",
    "SceneEnvironment",
    "SceneState",
    "estimate_flight_plan",
    "export_aircraft_definition_workbook",
    "load_aircraft_definition_workbook",
    "load_aircraft_workbook",
    "nasa_stca_aircraft_1",
    "planned_state_at_progress",
    "speed_of_sound_knots",
]
