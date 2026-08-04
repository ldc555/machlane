"""Typed, SI-normalized domain models shared by every adapter."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from math import isclose, isfinite
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
        if any(value <= 0 or not isfinite(value) for value in self.temperature_k):
            raise ValueError("atmospheric temperatures must be finite and positive")
        if any(value <= 0 or not isfinite(value) for value in self.pressure_pa):
            raise ValueError("atmospheric pressures must be finite and positive")
        if self.humidity_fraction is not None and any(
            value < 0 or value > 1 or not isfinite(value) for value in self.humidity_fraction
        ):
            raise ValueError("relative humidity must be finite and between zero and one")
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
    path: tuple[tuple[float, float], ...] = ()

    @model_validator(mode="after")
    def validate_path(self) -> RouteSegment:
        """Require optional polyline geometry to agree with the segment endpoints."""

        if not self.path:
            return self
        if len(self.path) < 2:
            raise ValueError("route segment path requires at least two points")
        if self.path[0] != (self.start_latitude, self.start_longitude):
            raise ValueError("route segment path must begin at the segment start")
        if self.path[-1] != (self.end_latitude, self.end_longitude):
            raise ValueError("route segment path must end at the segment end")
        for latitude, longitude in self.path:
            if not isfinite(latitude) or not isfinite(longitude):
                raise ValueError("route segment path coordinates must be finite")
            if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
                raise ValueError("route segment path coordinates must be valid WGS-84")
        return self


class RouteSourceMetadata(FrozenModel):
    """Provenance for conceptual, imported, or observed route geometry."""

    provider: str
    data_kind: Literal["conceptual_geodesic", "observed_track", "imported_waypoints"]
    retrieved_at: datetime
    source_url: str | None = None
    label: str
    flight_id: str | None = None
    callsign: str | None = None
    origin_icao: str | None = None
    destination_icao: str | None = None
    observed_start: datetime | None = None
    observed_end: datetime | None = None
    point_count: int | None = Field(default=None, ge=2)
    checksum: str | None = None
    limitations: tuple[str, ...] = ()


class RouteObservation(FrozenModel):
    """One timestamped point retained from an observed trajectory source."""

    timestamp: datetime
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    barometric_altitude_m: float | None = None
    true_track_deg: float | None = Field(default=None, ge=0, lt=360)
    on_ground: bool


class Route(FrozenModel):
    name: str
    waypoints: tuple[tuple[float, float], ...]
    segments: tuple[RouteSegment, ...]
    source: RouteSourceMetadata | None = None
    observations: tuple[RouteObservation, ...] = ()


class NearFieldSourceMetadata(FrozenModel):
    """Provenance for an aircraft pressure signature before atmospheric propagation."""

    provider: str
    solver_version: str | None = None
    source_document: str
    retrieved_at: datetime
    checksum: str
    configuration_checksum: str | None = None
    label: str = "UNVALIDATED_NEAR_FIELD_SIGNATURE"


class NearFieldSignature(FrozenModel):
    """SI-normalized pressure perturbation sampled on one near-field cut."""

    distance_m: tuple[float, ...]
    overpressure_pa: tuple[float, ...]
    reference_distance_m: float = Field(gt=0)
    azimuth_deg: float = Field(ge=0, lt=360)
    flight_mach: float = Field(gt=1)
    flight_altitude_m: float = Field(gt=0)
    source: NearFieldSourceMetadata

    @model_validator(mode="after")
    def validate_waveform(self) -> NearFieldSignature:
        if len(self.distance_m) != len(self.overpressure_pa) or len(self.distance_m) < 3:
            raise ValueError("near-field arrays must have equal lengths and at least three samples")
        if not all(isfinite(value) for value in (*self.distance_m, *self.overpressure_pa)):
            raise ValueError("near-field arrays must contain only finite values")
        if any(b <= a for a, b in zip(self.distance_m, self.distance_m[1:], strict=False)):
            raise ValueError("near-field distances must be strictly increasing")
        return self


class PropagationPhysicsOptions(FrozenModel):
    """Effects a real solver must explicitly include or reject for a declared case."""

    ray_families: tuple[Literal["PRIMARY", "SECONDARY_DIRECT", "SECONDARY_INDIRECT"], ...] = Field(
        default=("PRIMARY", "SECONDARY_DIRECT", "SECONDARY_INDIRECT"),
        min_length=1,
    )
    include_nonlinearity: bool = True
    include_thermoviscous_absorption: bool = True
    include_molecular_relaxation: bool = True
    include_wind: bool = True
    include_geometric_spreading: bool = True
    include_terrain_intersection: bool = True
    include_ground_reflection: bool = True
    earth_model: Literal["WGS84", "FLAT"] = "WGS84"


class SonicBoomCase(FrozenModel):
    """Complete normalized boundary passed to a future physical propagation engine."""

    aircraft: AircraftModel
    segment: RouteSegment
    atmosphere: AtmosphericProfile
    terrain: TerrainProfile
    near_field_signature: NearFieldSignature
    mach: float = Field(gt=1)
    altitude_m: float = Field(gt=0)
    boom_limit_pa: float = Field(gt=0)
    physics: PropagationPhysicsOptions = Field(default_factory=PropagationPhysicsOptions)

    @model_validator(mode="after")
    def validate_operating_point(self) -> SonicBoomCase:
        if abs(self.near_field_signature.flight_mach - self.mach) > 1e-6:
            raise ValueError("near-field signature Mach does not match the propagation case")
        if abs(self.near_field_signature.flight_altitude_m - self.altitude_m) > 1e-3:
            raise ValueError("near-field signature altitude does not match the propagation case")
        return self


class GroundSignature(FrozenModel):
    """One solver-produced ground waveform and its acoustical metrics."""

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    ray_family: Literal["PRIMARY", "SECONDARY_DIRECT", "SECONDARY_INDIRECT"]
    time_s: tuple[float, ...]
    overpressure_pa: tuple[float, ...]
    peak_positive_overpressure_pa: float = Field(ge=0)
    peak_negative_overpressure_pa: float = Field(le=0)
    perceived_level_db: float | None = None

    @model_validator(mode="after")
    def validate_samples(self) -> GroundSignature:
        if len(self.time_s) != len(self.overpressure_pa) or len(self.time_s) < 3:
            raise ValueError("ground-signature arrays must have equal lengths and three samples")
        if any(b <= a for a, b in zip(self.time_s, self.time_s[1:], strict=False)):
            raise ValueError("ground-signature times must be strictly increasing")
        if not all(isfinite(value) for value in (*self.time_s, *self.overpressure_pa)):
            raise ValueError("ground-signature arrays must contain only finite values")
        if not isclose(
            self.peak_positive_overpressure_pa,
            max(self.overpressure_pa),
            rel_tol=1e-9,
            abs_tol=1e-12,
        ):
            raise ValueError("positive peak metric does not match the ground waveform")
        if not isclose(
            self.peak_negative_overpressure_pa,
            min(self.overpressure_pa),
            rel_tol=1e-9,
            abs_tol=1e-12,
        ):
            raise ValueError("negative peak metric does not match the ground waveform")
        return self


class SonicBoomPrediction(FrozenModel):
    """Physical-engine result; classification remains unknown unless all requested rays ran."""

    engine_name: str
    engine_version: str
    signatures: tuple[GroundSignature, ...]
    requested_ray_families: tuple[
        Literal["PRIMARY", "SECONDARY_DIRECT", "SECONDARY_INDIRECT"], ...
    ] = Field(min_length=1)
    boom_limit_pa: float = Field(gt=0)
    classification: Literal["WITHIN_LIMIT", "EXCEEDS_LIMIT", "UNKNOWN"]
    assumptions: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    validation_status: Literal["UNVALIDATED", "COMPARISON_ONLY", "VALIDATED"] = "UNVALIDATED"

    @model_validator(mode="after")
    def validate_classification(self) -> SonicBoomPrediction:
        completed = {signature.ray_family for signature in self.signatures}
        requested = set(self.requested_ray_families)
        if not requested.issubset(completed):
            if self.classification != "UNKNOWN":
                raise ValueError("incomplete ray-family coverage requires UNKNOWN classification")
            return self
        expected = (
            "EXCEEDS_LIMIT"
            if any(
                signature.peak_positive_overpressure_pa > self.boom_limit_pa
                for signature in self.signatures
                if signature.ray_family in requested
            )
            else "WITHIN_LIMIT"
        )
        if self.classification != expected:
            raise ValueError("classification does not match the ground-waveform peak metrics")
        return self


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
