"""Source-controlled aircraft workbooks exposed by the local MachLane UI."""

from __future__ import annotations

from pathlib import Path

from .definition_workbook import load_aircraft_definition_workbook
from .loader import AircraftWorkbookError
from .specification import AircraftDefinition

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BUNDLED_LM1021_PATH = PROJECT_ROOT / "aircraft_database/LM1021/LM1021.xlsx"
BLANK_AIRCRAFT_TEMPLATE_PATH = (
    PROJECT_ROOT / "aircraft_database/templates/MachLane_Aircraft_Template.xlsx"
)


def load_bundled_lm1021() -> AircraftDefinition:
    """Load the reviewed repository copy of the NASA LM1021 research workbook."""

    if not BUNDLED_LM1021_PATH.is_file():
        raise AircraftWorkbookError(
            f"bundled LM1021 workbook is missing at {BUNDLED_LM1021_PATH}"
        )
    return load_aircraft_definition_workbook(BUNDLED_LM1021_PATH)


def blank_aircraft_template_bytes() -> bytes:
    """Return the current blank workbook contract for browser download."""

    if not BLANK_AIRCRAFT_TEMPLATE_PATH.is_file():
        raise AircraftWorkbookError(
            f"blank aircraft template is missing at {BLANK_AIRCRAFT_TEMPLATE_PATH}"
        )
    return BLANK_AIRCRAFT_TEMPLATE_PATH.read_bytes()
