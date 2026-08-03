"""Typed, SI-normalized domain models shared by every adapter."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FrozenModel(BaseModel):
    """Immutable value object used in reproducible run records."""

    model_config = ConfigDict(frozen=True)


class SourcedValue(FrozenModel):
    """An external value together with its original representation and provenance."""

    original_value: float | int | str | bool
    original_unit: str
    value_si: float | int | str | bool
    si_unit: str
    source_name: str
    source_document: str | None = None
    page_figure: str | None = None
    retrieved_at: datetime
    checksum: str | None = None


class AircraftOperatingLimits(FrozenModel):
    mtow: SourcedValue
    oew: SourcedValue
    maximum_operating_mach: SourcedValue
    maximum_cruise_mach: SourcedValue
    minimum_sustained_supersonic_mach: SourcedValue
    service_ceiling: SourcedValue
    minimum_cruise_altitude: SourcedValue | None = None


class AircraftPerformancePoint(FrozenModel):
    weight_kg: float
    altitude_m: float
    mach: float
    cruise_allowed: bool
    fuel_burn_kg_s: float | None = None
    available_thrust_n: float | None = None
    source: str
    page_figure: str | None = None
    notes: str | None = None


class AircraftModel(FrozenModel):
    name: SourcedValue
    manufacturer: SourcedValue
    variant: SourcedValue | None = None
    dimensions: dict[str, SourcedValue] = Field(default_factory=dict)
    operating_limits: AircraftOperatingLimits
    performance_map: tuple[AircraftPerformancePoint, ...] = ()
    workbook_checksum: str


class AtmosphericSourceMetadata(FrozenModel):
    provider: str
    model_cycle: datetime | None = None
    forecast_hour: int | None = None
    ensemble_member: str | None = None
    valid_time: datetime
    variables: tuple[str, ...]
    horizontal_interpolation: str
    vertical_interpolation: str
    retrieved_at: datetime
    source_url: str | None = None
    checksums: dict[str, str] = Field(default_factory=dict)
    label: str | None = None


class AtmosphericProfile(FrozenModel):
    altitude_m: tuple[float, ...]
    temperature_k: tuple[float, ...]
    pressure_pa: tuple[float, ...]
    zonal_wind_mps: tuple[float, ...]
    meridional_wind_mps: tuple[float, ...]
    humidity_fraction: tuple[float, ...] | None = None
    latitude: float
    longitude: float
    valid_time: datetime
    source: AtmosphericSourceMetadata

    @model_validator(mode="after")
    def validate_levels(self) -> AtmosphericProfile:
        lengths = {
            len(self.altitude_m),
            len(self.temperature_k),
            len(self.pressure_pa),
            len(self.zonal_wind_mps),
            len(self.meridional_wind_mps),
        }
        if self.humidity_fraction is not None:
            lengths.add(len(self.humidity_fraction))
        if len(lengths) != 1 or not self.altitude_m:
            raise ValueError("atmospheric arrays must be non-empty and have equal lengths")
        if any(b <= a for a, b in zip(self.altitude_m, self.altitude_m[1:], strict=False)):
            raise ValueError("altitude levels must be strictly increasing")
        return self


class TerrainSourceMetadata(FrozenModel):
    provider: str
    resolution_m: float
    horizontal_datum: str
    vertical_datum: str
    interpolation: str
    retrieved_at: datetime
    source_url: str | None = None
    checksum: str | None = None
    label: str | None = None


class TerrainProfile(FrozenModel):
    distance_m: tuple[float, ...]
    elevation_m: tuple[float, ...]
    latitude: tuple[float, ...]
    longitude: tuple[float, ...]
    source: TerrainSourceMetadata

    @model_validator(mode="after")
    def validate_samples(self) -> TerrainProfile:
        if (
            len(
                {
                    len(self.distance_m),
                    len(self.elevation_m),
                    len(self.latitude),
                    len(self.longitude),
                }
            )
            != 1
        ):
            raise ValueError("terrain arrays must have equal lengths")
        return self


class RouteSegment(FrozenModel):
    segment_id: str
    start_latitude: float
    start_longitude: float
    end_latitude: float
    end_longitude: float
    distance_m: float
    bearing_deg: float


class Route(FrozenModel):
    name: str
    waypoints: tuple[tuple[float, float], ...]
    segments: tuple[RouteSegment, ...]


class PropagationRequest(FrozenModel):
    aircraft: AircraftModel
    segment: RouteSegment
    atmosphere: AtmosphericProfile
    terrain: TerrainProfile
    mach: float
    altitude_m: float


class PropagationResult(FrozenModel):
    engine_name: str
    engine_version: str
    classification: Literal["SAFE", "UNSAFE", "UNKNOWN"]
    allowable: bool | None
    label: str
    metrics: dict[str, float | str] = Field(default_factory=dict)
    assumptions: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


class CandidateEvaluation(FrozenModel):
    segment_id: str
    mach: float
    altitude_m: float
    accepted: bool
    reason: str
    propagation: PropagationResult | None = None


class SegmentLimit(FrozenModel):
    segment_id: str
    selected_mach: float | None
    selected_altitude_m: float | None
    status: Literal["PASS", "FAIL", "UNKNOWN"]
    candidate_evaluations: tuple[CandidateEvaluation, ...]


class PlannerResult(FrozenModel):
    run_id: str
    created_at: datetime
    engine_name: str
    engine_version: str
    segment_limits: tuple[SegmentLimit, ...]
    reliability_level: float
    label: str


class ComplianceStatus(StrEnum):
    SUPPORTED = "SUPPORTED"
    PARTIAL = "PARTIAL"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    VALIDATION_REQUIRED = "VALIDATION_REQUIRED"


class RunManifest(FrozenModel):
    run_id: str
    package_version: str
    git_commit_sha: str
    configuration_checksum: str
    aircraft_workbook_checksum: str
    source_data_checksums: dict[str, str] = Field(default_factory=dict)
    weather_source: AtmosphericSourceMetadata
    terrain_source: TerrainSourceMetadata
    propagation_engine: str
    propagation_engine_version: str
    reliability_setting: float
    assumptions: tuple[str, ...]
    limitations: tuple[str, ...]
    compliance_statuses: dict[str, ComplianceStatus]
    started_at: datetime
    completed_at: datetime
