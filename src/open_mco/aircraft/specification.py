"""Editable aircraft specifications and phase-aware planning inputs."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

NASA_STCA_2020_URL = "https://ntrs.nasa.gov/api/citations/20200000513/downloads/20200000513.pdf"
NASA_AURALIZATION_2020_URL = (
    "https://ntrs.nasa.gov/api/citations/20200002602/downloads/20200002602.pdf"
)
NASA_GLOBAL_2021_URL = (
    "https://ntrs.nasa.gov/api/citations/20205009400/downloads/CR-20205009400.pdf"
)
NASA_STCA_INLET_2024_URL = (
    "https://ntrs.nasa.gov/api/citations/20240007797/downloads/Slater_ISABE-2024-Paper-Final.pdf"
)


class EditableModel(BaseModel):
    """Mutable model used by the local aircraft editor."""

    model_config = ConfigDict(validate_assignment=True)


class AircraftField(EditableModel):
    section: str
    parameter: str
    value: str | None = None
    unit: str = "-"
    required: bool = False
    source_name: str | None = None
    source_url: str | None = None
    page_figure: str | None = None
    notes: str | None = None
    evidence_class: str = "UNAVAILABLE"


class PhasePoint(EditableModel):
    sequence: int = Field(ge=1)
    phase: str
    altitude_ft: float = Field(ge=0)
    mach: float = Field(ge=0)
    source_name: str
    source_url: str
    page_figure: str
    notes: str | None = None
    evidence_class: str = "UNVALIDATED_ASSUMPTION"


class PhaseTiming(EditableModel):
    phase: Literal["taxi_out", "climb_acceleration", "cruise", "descent", "approach", "taxi_in"]
    duration_min: float | None = Field(default=None, ge=0)
    basis: Literal[
        "NASA_STCA",
        "NASA_N_PLUS_2_PROXY",
        "PUBLISHED",
        "CALCULATED",
        "UNVALIDATED_ASSUMPTION",
        "MISSING",
    ]
    source_name: str | None = None
    source_url: str | None = None
    page_figure: str | None = None
    notes: str | None = None


class PerformancePoint(EditableModel):
    """One auditable point in the aircraft performance deck."""

    weight_lb: float = Field(gt=0)
    altitude_ft: float = Field(ge=0)
    mach: float = Field(ge=0)
    throttle_percent: float = Field(ge=0, le=100)
    drag_required_lbf: float = Field(ge=0)
    available_thrust_lbf: float = Field(ge=0)
    fuel_flow_lb_hr: float = Field(ge=0)
    sustainable: bool
    evidence_class: str
    source_name: str
    source_url: str
    page_figure: str
    notes: str | None = None


class NearFieldSample(EditableModel):
    """One sample from a condition-specific near-field pressure signature."""

    signature_id: str
    axial_position_ft: float
    delta_pressure_psf: float
    reference_distance_ft: float = Field(gt=0)
    azimuth_deg: float = Field(ge=0, lt=360)
    mach: float = Field(gt=1)
    altitude_ft: float = Field(gt=0)
    weight_lb: float = Field(gt=0)
    angle_of_attack_deg: float
    evidence_class: str
    source_name: str
    source_url: str
    page_figure: str
    notes: str | None = None


class AircraftDefinition(EditableModel):
    schema_version: str = "machlane-aircraft-v1"
    aircraft_id: str
    display_name: str
    revision: int = Field(default=1, ge=1)
    updated_at: datetime
    fields: tuple[AircraftField, ...]
    phase_profile: tuple[PhasePoint, ...]
    phase_timing: tuple[PhaseTiming, ...]
    performance_map: tuple[PerformancePoint, ...] = ()
    nearfield_samples: tuple[NearFieldSample, ...] = ()
    workbook_checksum: str | None = None

    @field_validator("aircraft_id")
    @classmethod
    def validate_aircraft_id(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,63}", value):
            raise ValueError("aircraft_id must be a lowercase file-safe identifier")
        return value

    def value(self, parameter: str) -> str | None:
        for item in self.fields:
            if item.parameter == parameter:
                return item.value
        return None

    def numeric_value(self, parameter: str) -> float | None:
        value = self.value(parameter)
        if value in (None, ""):
            return None
        try:
            return float(value)
        except ValueError:
            return None

    @property
    def missing_required_fields(self) -> tuple[str, ...]:
        return tuple(item.parameter for item in self.fields if item.required and not item.value)

    @property
    def phase_profile_ready(self) -> bool:
        return bool(self.phase_profile) and all(
            timing.duration_min is not None or timing.phase == "cruise"
            for timing in self.phase_timing
            if timing.phase not in {"taxi_out", "taxi_in"}
        )

    @property
    def nearfield_ready(self) -> bool:
        sample_counts: dict[str, int] = {}
        for sample in self.nearfield_samples:
            sample_counts[sample.signature_id] = sample_counts.get(sample.signature_id, 0) + 1
        return bool(self.value("Nearfield Signature File")) or any(
            count >= 3 for count in sample_counts.values()
        )

    @property
    def performance_data_ready(self) -> bool:
        """Return data presence, not calibration or certification status."""

        return bool(self.performance_map)


class AircraftStore:
    """Small, local JSON store. Aircraft data never leaves the workstation."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def list(self) -> tuple[AircraftDefinition, ...]:
        if not self.directory.exists():
            return ()
        definitions: list[AircraftDefinition] = []
        for path in sorted(self.directory.glob("*.json")):
            definitions.append(AircraftDefinition.model_validate_json(path.read_text()))
        return tuple(definitions)

    def load(self, aircraft_id: str) -> AircraftDefinition | None:
        path = self.directory / f"{aircraft_id}.json"
        if not path.exists():
            return None
        return AircraftDefinition.model_validate_json(path.read_text())

    def save(self, definition: AircraftDefinition) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        destination = self.directory / f"{definition.aircraft_id}.json"
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(definition.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
        )
        temporary.replace(destination)
        return destination


def _field(
    section: str,
    parameter: str,
    value: str | float | int | None,
    unit: str,
    *,
    required: bool = False,
    source_name: str | None = None,
    source_url: str | None = None,
    page_figure: str | None = None,
    notes: str | None = None,
    evidence_class: str | None = None,
) -> AircraftField:
    return AircraftField(
        section=section,
        parameter=parameter,
        value=None if value is None else str(value),
        unit=unit,
        required=required,
        source_name=source_name,
        source_url=source_url,
        page_figure=page_figure,
        notes=notes,
        evidence_class=evidence_class
        or ("PUBLISHED" if source_name and value is not None else "UNAVAILABLE"),
    )


def nasa_stca_aircraft_1() -> AircraftDefinition:
    """Return a source-backed draft for NASA's public 55-tonne STCA.

    Blank required values are deliberate: the public reports describe a concept, but do not
    publish every operating-limit field required by MachLane's workbook contract.
    """

    primary = "Berton et al., Supersonic Technology Concept Aeroplanes (2020)"
    crosscheck = "Speth et al., Global Environmental Impact (2021)"
    fields = (
        _field(
            "General",
            "Aircraft Name",
            "NASA 55t STCA - Aircraft One",
            "-",
            required=True,
            source_name=primary,
            source_url=NASA_STCA_2020_URL,
            page_figure="p. 4, Table 2",
        ),
        _field(
            "General",
            "Manufacturer",
            "NASA",
            "-",
            required=True,
            source_name=primary,
            source_url=NASA_STCA_2020_URL,
            page_figure="p. 1",
        ),
        _field(
            "General",
            "Variant",
            "8-passenger trijet concept",
            "-",
            source_name=primary,
            source_url=NASA_STCA_2020_URL,
            page_figure="pp. 1, 4",
        ),
        _field("General", "ICAO Code", None, "-", notes="Not assigned to a notional concept."),
        _field(
            "General",
            "Length",
            135,
            "ft",
            required=True,
            source_name=primary,
            source_url=NASA_STCA_2020_URL,
            page_figure="p. 4, Table 2",
        ),
        _field(
            "General",
            "Wingspan",
            67,
            "ft",
            required=True,
            source_name=primary,
            source_url=NASA_STCA_2020_URL,
            page_figure="p. 4, Table 2",
        ),
        _field("General", "Height", None, "ft"),
        _field(
            "General",
            "Wing Area",
            1619,
            "ft2",
            required=True,
            source_name=primary,
            source_url=NASA_STCA_2020_URL,
            page_figure="p. 4, Table 2",
        ),
        _field(
            "General",
            "Number of Engines",
            3,
            "count",
            required=True,
            source_name=primary,
            source_url=NASA_STCA_2020_URL,
            page_figure="p. 4, Table 2",
        ),
        _field(
            "General",
            "Engine Model",
            "CFM56-derived",
            "-",
            source_name=primary,
            source_url=NASA_STCA_2020_URL,
            page_figure="p. 4, Table 2",
        ),
        _field(
            "Operating Limits",
            "MTOW",
            121000,
            "lb",
            required=True,
            source_name=primary,
            source_url=NASA_STCA_2020_URL,
            page_figure="p. 4, Table 2",
        ),
        _field(
            "Operating Limits",
            "OEW",
            51000,
            "lb",
            required=True,
            source_name=primary,
            source_url=NASA_STCA_2020_URL,
            page_figure="p. 5, Table 3",
        ),
        _field(
            "Operating Limits",
            "Maximum Fuel Weight",
            68343,
            "lb",
            source_name=crosscheck,
            source_url=NASA_GLOBAL_2021_URL,
            page_figure="p. 7, Table 1",
            notes="31 tonnes; cross-checks 61 klb block + 8 klb reserve in the primary report.",
        ),
        _field(
            "Operating Limits",
            "Maximum Payload",
            1640,
            "lb",
            source_name=primary,
            source_url=NASA_STCA_2020_URL,
            page_figure="p. 5, Table 3",
        ),
        _field(
            "Operating Limits",
            "Maximum Operating Mach",
            None,
            "Mach",
            required=True,
            notes="The reports publish design cruise Mach, not an approved maximum operating Mach.",
        ),
        _field(
            "Operating Limits",
            "Maximum Cruise Mach",
            1.4,
            "Mach",
            required=True,
            source_name=primary,
            source_url=NASA_STCA_2020_URL,
            page_figure="p. 4, Table 2",
        ),
        _field(
            "Operating Limits", "Minimum Sustained Supersonic Mach", None, "Mach", required=True
        ),
        _field(
            "Operating Limits",
            "Service Ceiling",
            None,
            "ft",
            required=True,
            notes="51,000 ft is a design-mission cruise altitude, not a published service ceiling.",
        ),
        _field(
            "Operating Limits",
            "Minimum Cruise Altitude",
            44000,
            "ft",
            source_name=primary,
            source_url=NASA_STCA_2020_URL,
            page_figure="p. 5, Table 3",
            notes="Lower bound of the published 44-51 kft design-mission cruise band.",
        ),
        _field("Operating Limits", "Maximum Bank Angle", None, "deg"),
        _field("Operating Limits", "Maximum Load Factor", None, "g"),
        _field(
            "Mission Config",
            "Preferred Cruise Mach",
            1.4,
            "Mach",
            source_name=primary,
            source_url=NASA_STCA_2020_URL,
            page_figure="p. 4, Table 2",
        ),
        _field(
            "Mission Config",
            "Preferred Cruise Altitude",
            50000,
            "ft",
            source_name=primary,
            source_url=NASA_STCA_2020_URL,
            page_figure="p. 3, Table 1; p. 5, Table 3",
            notes="Nominal point inside a published 44-51 kft cruise-climb band.",
        ),
        _field("Mission Config", "Required Reliability", None, "probability"),
        _field("Mission Config", "Boom Limit", None, "psf"),
        _field("Sonic Boom", "Nearfield Peak Overpressure", None, "psf"),
        _field("Sonic Boom", "Reference Distance", None, "ft"),
        _field("Sonic Boom", "Equivalent Area File", None, "file"),
        _field("Sonic Boom", "Nearfield Signature File", None, "file"),
        _field(
            "Sonic Boom",
            "Body Length",
            135,
            "ft",
            source_name=primary,
            source_url=NASA_STCA_2020_URL,
            page_figure="p. 4, Table 2",
            notes="Overall aircraft length; not separately published as sonic-boom body length.",
        ),
        _field("Sonic Boom", "Nose Length", None, "ft"),
    )
    phase_source = "Slater, External-Compression Inlets for Supersonic Aircraft (2024)"
    phase_points = tuple(
        PhasePoint(
            sequence=index,
            phase=phase,
            altitude_ft=altitude,
            mach=mach,
            source_name=phase_source,
            source_url=NASA_STCA_INLET_2024_URL,
            page_figure="p. 5, Table 1",
            notes="Altitude/Mach condition derived from the NASA STCA mission profile.",
        )
        for index, (phase, altitude, mach) in enumerate(
            (
                ("Takeoff", 0, 0.3),
                ("Climb", 20000, 0.6),
                ("Climb", 35000, 0.8),
                ("Transonic acceleration", 40000, 0.9),
                ("Transonic acceleration", 45000, 1.0),
                ("Transonic acceleration", 48000, 1.1),
                ("Supersonic climb", 49000, 1.2),
                ("Supersonic climb", 50000, 1.3),
                ("Supersonic cruise", 50000, 1.4),
                ("Approach", 5000, 0.4),
            ),
            start=1,
        )
    )
    timings = (
        PhaseTiming(
            phase="taxi_out",
            duration_min=9,
            basis="NASA_STCA",
            source_name=primary,
            source_url=NASA_STCA_2020_URL,
            page_figure="p. 5, Figure 3",
        ),
        PhaseTiming(
            phase="climb_acceleration",
            duration_min=47,
            basis="NASA_STCA",
            source_name=primary,
            source_url=NASA_STCA_2020_URL,
            page_figure="p. 5, Table 3",
        ),
        PhaseTiming(
            phase="cruise",
            duration_min=None,
            basis="CALCULATED",
            notes="Calculated from remaining route distance and NOAA-derived local speed of sound/wind.",
        ),
        PhaseTiming(
            phase="descent",
            duration_min=20.1,
            basis="NASA_N_PLUS_2_PROXY",
            source_name=crosscheck,
            source_url=NASA_GLOBAL_2021_URL,
            page_figure="p. 10, Table 3",
            notes="Reference NASA N+2 descent duration; editable proxy, not STCA-specific validation data.",
        ),
        PhaseTiming(
            phase="approach",
            duration_min=4,
            basis="NASA_STCA",
            source_name=primary,
            source_url=NASA_STCA_2020_URL,
            page_figure="p. 5, Figure 3",
        ),
        PhaseTiming(
            phase="taxi_in",
            duration_min=5,
            basis="NASA_STCA",
            source_name=primary,
            source_url=NASA_STCA_2020_URL,
            page_figure="p. 5, Figure 3",
        ),
    )
    return AircraftDefinition(
        aircraft_id="aircraft_one",
        display_name="Aircraft One",
        updated_at=datetime.now(UTC),
        fields=fields,
        phase_profile=phase_points,
        phase_timing=timings,
    )
