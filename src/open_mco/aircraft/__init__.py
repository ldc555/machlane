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
from .library import (
    BLANK_AIRCRAFT_TEMPLATE_PATH,
    BUNDLED_LM1021_PATH,
    blank_aircraft_template_bytes,
    load_bundled_lm1021,
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
    "BLANK_AIRCRAFT_TEMPLATE_PATH",
    "BUNDLED_LM1021_PATH",
    "FlightPhaseEstimate",
    "FlightPlanEstimate",
    "NearFieldSample",
    "PerformancePoint",
    "PhasePoint",
    "PhaseTiming",
    "SceneEnvironment",
    "SceneState",
    "estimate_flight_plan",
    "blank_aircraft_template_bytes",
    "export_aircraft_definition_workbook",
    "load_aircraft_definition_workbook",
    "load_aircraft_workbook",
    "load_bundled_lm1021",
    "nasa_stca_aircraft_1",
    "planned_state_at_progress",
    "speed_of_sound_knots",
]
