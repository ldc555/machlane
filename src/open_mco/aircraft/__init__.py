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
    speed_of_sound_knots,
)
from .loader import AircraftWorkbookError, load_aircraft_workbook
from .specification import (
    AircraftDefinition,
    AircraftField,
    AircraftStore,
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
    "speed_of_sound_knots",
]
