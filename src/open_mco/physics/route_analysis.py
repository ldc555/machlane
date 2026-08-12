"""Strict interchange for route-level physical sonic-boom solver results.

MachLane does not implement or approximate the propagation physics in this module.  It validates,
ranks, visualizes, and exports results produced by a separately reviewed solver such as sBOOM or
PCBoom.  The boundary is deliberately strict so an incomplete result can never become a compliant
operating-corridor recommendation.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import shlex
import subprocess
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from open_mco.models import FrozenModel

RayFamily = Literal["PRIMARY", "SECONDARY_DIRECT", "SECONDARY_INDIRECT"]
RouteClassification = Literal["WITHIN_LIMIT", "EXCEEDS_LIMIT", "UNKNOWN"]
ValidationStatus = Literal["UNVALIDATED", "COMPARISON_ONLY", "VALIDATED"]


class RouteSolverProvenance(FrozenModel):
    """Identity and validation evidence for the external propagation implementation."""

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    executable_checksum: str = Field(min_length=8)
    configuration_checksum: str = Field(min_length=8)
    validation_status: ValidationStatus
    reference_cases: tuple[str, ...] = ()
    source_url: str | None = None


class SurfaceFootprintSample(FrozenModel):
    """One terrain-intersected ground waveform at a declared ray-family location."""

    candidate_id: str = Field(min_length=1)
    segment_id: str = Field(min_length=1)
    ray_family: RayFamily
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    along_track_m: float = Field(ge=0)
    cross_track_m: float
    terrain_elevation_m: float
    time_s: tuple[float, ...] = Field(min_length=3)
    overpressure_pa: tuple[float, ...] = Field(min_length=3)
    peak_positive_overpressure_pa: float = Field(ge=0)
    peak_negative_overpressure_pa: float = Field(le=0)
    perceived_level_db: float | None = None
    a_weighted_exposure_db: float | None = None
    ray_arrival_time_s: float | None = Field(default=None, ge=0)
    ground_incidence_deg: float | None = Field(default=None, ge=0, le=90)
    uncertainty_upper_pa: float | None = Field(default=None, ge=0)
    launch_roll_deg: float | None = Field(default=None, ge=-90, le=90)
    reflection_factor: float | None = Field(default=None, gt=0)
    source_altitude_ft: float | None = Field(default=None, ge=0)
    ray_path_horizontal_m: tuple[float, ...] = ()
    ray_path_altitude_m: tuple[float, ...] = ()

    @model_validator(mode="after")
    def validate_waveform(self) -> SurfaceFootprintSample:
        if len(self.time_s) != len(self.overpressure_pa):
            raise ValueError("ground waveform time and pressure arrays must have equal lengths")
        if any(b <= a for a, b in zip(self.time_s, self.time_s[1:], strict=False)):
            raise ValueError("ground waveform time samples must be strictly increasing")
        if abs(max(self.overpressure_pa) - self.peak_positive_overpressure_pa) > 1e-8:
            raise ValueError("positive ground-waveform peak does not match the samples")
        if abs(min(self.overpressure_pa) - self.peak_negative_overpressure_pa) > 1e-8:
            raise ValueError("negative ground-waveform peak does not match the samples")
        if (
            self.uncertainty_upper_pa is not None
            and self.uncertainty_upper_pa < self.peak_positive_overpressure_pa
        ):
            raise ValueError("uncertainty upper bound cannot be below the nominal peak")
        if len(self.ray_path_horizontal_m) != len(self.ray_path_altitude_m):
            raise ValueError("ray-path horizontal and altitude arrays must have equal lengths")
        if self.ray_path_horizontal_m:
            if len(self.ray_path_horizontal_m) < 2:
                raise ValueError("ray path requires at least two points")
            if any(
                b < a
                for a, b in zip(
                    self.ray_path_horizontal_m,
                    self.ray_path_horizontal_m[1:],
                    strict=False,
                )
            ):
                raise ValueError("ray-path horizontal distance must be nondecreasing")
            if any(value < 0 for value in self.ray_path_horizontal_m):
                raise ValueError("ray-path horizontal distance cannot be negative")
        return self


class RouteCandidateAnalysis(FrozenModel):
    """One fully propagated baseline or strategic route candidate."""

    candidate_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    route_coordinates: tuple[tuple[float, float], ...] = Field(min_length=2)
    distance_m: float = Field(gt=0)
    time_delta_min: float
    maximum_lateral_offset_m: float = Field(ge=0)
    altitude_offset_ft: float = 0.0
    altitude_profile_ft: tuple[tuple[str, float], ...] = ()
    requested_ray_families: tuple[RayFamily, ...] = Field(min_length=1)
    completed_ray_families: tuple[RayFamily, ...]
    surface_samples: tuple[SurfaceFootprintSample, ...] = Field(min_length=1)
    maximum_nominal_overpressure_pa: float = Field(ge=0)
    maximum_uncertainty_overpressure_pa: float = Field(ge=0)
    classification: RouteClassification
    operational_constraints_checked: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_summary(self) -> RouteCandidateAnalysis:
        if any(sample.candidate_id != self.candidate_id for sample in self.surface_samples):
            raise ValueError("surface sample candidate IDs must match their route candidate")
        nominal = max(sample.peak_positive_overpressure_pa for sample in self.surface_samples)
        upper = max(
            sample.uncertainty_upper_pa
            if sample.uncertainty_upper_pa is not None
            else sample.peak_positive_overpressure_pa
            for sample in self.surface_samples
        )
        if abs(nominal - self.maximum_nominal_overpressure_pa) > 1e-8:
            raise ValueError("candidate nominal maximum does not match its surface samples")
        if abs(upper - self.maximum_uncertainty_overpressure_pa) > 1e-8:
            raise ValueError("candidate uncertainty maximum does not match its surface samples")
        complete = set(self.requested_ray_families).issubset(self.completed_ray_families)
        if not complete and self.classification != "UNKNOWN":
            raise ValueError("incomplete primary/secondary ray coverage requires UNKNOWN")
        return self


class PhysicalRouteAnalysis(FrozenModel):
    """Auditable route-level output from a separately installed physical solver."""

    schema_version: Literal["machlane-physical-route-v1"] = "machlane-physical-route-v1"
    run_id: str = Field(min_length=1)
    created_at: datetime
    request_checksum: str = Field(min_length=8)
    solver: RouteSolverProvenance
    boom_limit_pa: float = Field(gt=0)
    baseline_candidate_id: str = Field(min_length=1)
    recommended_candidate_id: str | None = None
    candidates: tuple[RouteCandidateAnalysis, ...] = Field(min_length=1)
    source_checksums: dict[str, str] = Field(default_factory=dict)
    assumptions: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_candidates(self) -> PhysicalRouteAnalysis:
        by_id = {candidate.candidate_id: candidate for candidate in self.candidates}
        if len(by_id) != len(self.candidates):
            raise ValueError("route candidate IDs must be unique")
        if self.baseline_candidate_id not in by_id:
            raise ValueError("baseline candidate is absent from the result")
        for candidate in self.candidates:
            expected = (
                "UNKNOWN"
                if not set(candidate.requested_ray_families).issubset(
                    candidate.completed_ray_families
                )
                else (
                    "EXCEEDS_LIMIT"
                    if candidate.maximum_uncertainty_overpressure_pa > self.boom_limit_pa
                    else "WITHIN_LIMIT"
                )
            )
            if candidate.classification != expected:
                raise ValueError(
                    f"candidate {candidate.candidate_id} classification does not match "
                    "uncertainty-bounded overpressure and ray coverage"
                )
        if self.recommended_candidate_id is not None:
            recommended = by_id.get(self.recommended_candidate_id)
            if recommended is None:
                raise ValueError("recommended candidate is absent from the result")
            if recommended.classification != "WITHIN_LIMIT":
                raise ValueError("only an uncertainty-bounded WITHIN_LIMIT route may be recommended")
            if self.solver.validation_status != "VALIDATED":
                raise ValueError("an operational recommendation requires a VALIDATED solver")
        return self

    @property
    def baseline(self) -> RouteCandidateAnalysis:
        return next(
            candidate
            for candidate in self.candidates
            if candidate.candidate_id == self.baseline_candidate_id
        )

    @property
    def recommended(self) -> RouteCandidateAnalysis | None:
        if self.recommended_candidate_id is None:
            return None
        return next(
            candidate
            for candidate in self.candidates
            if candidate.candidate_id == self.recommended_candidate_id
        )


def canonical_request_bytes(request: dict[str, Any]) -> bytes:
    """Serialize a solver request deterministically for hashing and evidence exchange."""

    return json.dumps(request, sort_keys=True, separators=(",", ":"), default=str).encode()


def request_checksum(request: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_request_bytes(request)).hexdigest()


def build_physical_route_request(
    *,
    aircraft: Any,
    analysis: Any,
    flight_plan: Any,
    boom_limit_psf: float,
) -> dict[str, Any]:
    """Build the complete normalized input bundle for an external route solver.

    ``Any`` is intentional at this serialization boundary: callers pass the validated
    ``AircraftDefinition``, ``RealMissionAnalysis``, and ``FlightPlanEstimate`` models.  Keeping
    imports out of this low-level interchange module avoids a physics/mission dependency cycle.
    """

    from open_mco.aircraft import planned_state_at_progress

    if boom_limit_psf <= 0:
        raise ValueError("boom limit must be positive")
    total_distance = sum(segment.distance_m for segment in analysis.atmospheric_route.segments)
    if total_distance <= 0:
        raise ValueError("physical route request requires positive route distance")
    terrain_by_id = {item.segment_id: item for item in analysis.terrain_regions}
    nearfield_points = {
        (sample.mach, sample.altitude_ft, sample.weight_lb)
        for sample in aircraft.nearfield_samples
    }
    regions: list[dict[str, Any]] = []
    elapsed = 0.0
    for segment, atmosphere in zip(
        analysis.atmospheric_route.segments,
        analysis.segment_atmospheres,
        strict=True,
    ):
        progress = (elapsed + segment.distance_m / 2) / total_distance
        elapsed += segment.distance_m
        state = planned_state_at_progress(progress, aircraft, flight_plan)
        mach = float(state["mach"])
        altitude_ft = float(state["altitude_ft"])
        matching_signatures = sorted(
            {
                sample.signature_id
                for sample in aircraft.nearfield_samples
                if abs(sample.mach - mach) <= 0.005
                and abs(sample.altitude_ft - altitude_ft) <= 50
            }
        )
        if mach <= 1:
            eligibility = "NOT_APPLICABLE_SUBSONIC"
        elif matching_signatures:
            eligibility = "READY"
        else:
            eligibility = "MISSING_NEAR_FIELD_OPERATING_POINT"
        terrain = terrain_by_id[segment.segment_id]
        regions.append(
            {
                "segment": segment.model_dump(mode="json"),
                "progress": progress,
                "planned_state": state,
                "propagation_eligibility": eligibility,
                "matching_signature_ids": matching_signatures,
                "atmosphere": atmosphere.model_dump(mode="json"),
                "terrain": {
                    "status": terrain.status,
                    "reason": terrain.reason,
                    "profile": None
                    if terrain.profile is None
                    else terrain.profile.model_dump(mode="json"),
                },
            }
        )
    request = {
        "schema": "machlane-route-solver-request-v1",
        # Bind the request to the immutable real-route retrieval snapshot.  Using wall-clock time
        # here made an otherwise identical request checksum change on every Streamlit rerun.
        "created_at": analysis.observed_route.source.retrieved_at.isoformat(),
        "aircraft": aircraft.model_dump(mode="json"),
        "nearfield_operating_points": [
            {"mach": mach, "altitude_ft": altitude, "weight_lb": weight}
            for mach, altitude, weight in sorted(nearfield_points)
        ],
        "route": analysis.observed_route.model_dump(mode="json"),
        "flight_plan": flight_plan.model_dump(mode="json"),
        "regions": regions,
        "acceptance": {
            "boom_limit_psf": boom_limit_psf,
            "boom_limit_pa": boom_limit_psf * 47.88025898033584,
            "requested_ray_families": [
                "PRIMARY",
                "SECONDARY_DIRECT",
                "SECONDARY_INDIRECT",
            ],
            "classification_basis": "uncertainty_upper_bound",
        },
        "strategic_rerouting": {
            "enabled": True,
            "lateral_offset_candidates_nmi": [-20, -10, -5, 0, 5, 10, 20],
            "altitude_offset_candidates_ft": [-4_000, -2_000, 0, 2_000, 4_000],
            "departure_time_offsets_min": [-60, -30, 0, 30, 60],
            "requirements": [
                "resample NOAA at every candidate position and UTC time",
                "reload terrain for every candidate footprint",
                "stay within the reviewed aircraft performance and near-field envelope",
                "retain primary, secondary-direct, and secondary-indirect ray coverage",
                "report operational constraints separately from acoustic classification",
            ],
        },
        "data_policy": {
            "no_synthetic_substitution": True,
            "atmosphere_benchmarks_are_validation_only": True,
            "operational_atmosphere": f"NOAA {analysis.noaa_model}",
            "terrain": "USGS 3DEP where available; explicit unavailable elsewhere",
        },
    }
    request["coverage_summary"] = {
        "region_count": len(regions),
        "ready": sum(region["propagation_eligibility"] == "READY" for region in regions),
        "subsonic": sum(
            region["propagation_eligibility"] == "NOT_APPLICABLE_SUBSONIC"
            for region in regions
        ),
        "missing_nearfield": sum(
            region["propagation_eligibility"] == "MISSING_NEAR_FIELD_OPERATING_POINT"
            for region in regions
        ),
    }
    return request


def load_physical_route_analysis(source: bytes | str | Path) -> PhysicalRouteAnalysis:
    """Load and strictly validate a physical solver result JSON document."""

    if isinstance(source, bytes):
        payload = source.decode("utf-8")
    elif isinstance(source, Path):
        payload = source.read_text(encoding="utf-8")
    elif source.lstrip().startswith("{"):
        payload = source
    else:
        payload = Path(source).read_text(encoding="utf-8")
    return PhysicalRouteAnalysis.model_validate_json(payload)


class ExternalRouteSolver:
    """Run a user-installed solver wrapper through a versioned JSON command contract.

    The wrapper receives ``--input REQUEST.json --output RESULT.json``.  MachLane never bundles
    NASA binaries and never invokes a shell.  The returned result must satisfy
    :class:`PhysicalRouteAnalysis` and match the exact request checksum.
    """

    def __init__(self, command: str | tuple[str, ...], *, timeout_seconds: float = 900) -> None:
        parsed = tuple(shlex.split(command)) if isinstance(command, str) else command
        if not parsed:
            raise ValueError("external propagation command cannot be empty")
        if timeout_seconds <= 0:
            raise ValueError("external propagation timeout must be positive")
        self.command = parsed
        self.timeout_seconds = timeout_seconds

    def run(self, request: dict[str, Any]) -> PhysicalRouteAnalysis:
        checksum = request_checksum(request)
        with tempfile.TemporaryDirectory(prefix="machlane-propagation-") as directory:
            workspace = Path(directory)
            input_path = workspace / "request.json"
            output_path = workspace / "result.json"
            input_path.write_text(
                json.dumps(request, indent=2, default=str), encoding="utf-8"
            )
            try:
                completed = subprocess.run(
                    [*self.command, "--input", str(input_path), "--output", str(output_path)],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise RuntimeError(f"physical propagation wrapper failed: {exc}") from exc
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout).strip()[-2_000:]
                raise RuntimeError(
                    f"physical propagation wrapper exited {completed.returncode}: {detail}"
                )
            if not output_path.exists():
                raise RuntimeError("physical propagation wrapper created no result.json")
            result = load_physical_route_analysis(output_path)
            if result.request_checksum != checksum:
                raise ValueError("physical result checksum does not match the submitted request")
            return result


def surface_sample_rows(result: PhysicalRouteAnalysis) -> list[dict[str, Any]]:
    """Flatten all solver surface samples for tables, plots, and CSV export."""

    rows: list[dict[str, Any]] = []
    for candidate in result.candidates:
        for sample in candidate.surface_samples:
            rows.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "candidate_label": candidate.label,
                    "classification": candidate.classification,
                    "segment_id": sample.segment_id,
                    "ray_family": sample.ray_family,
                    "latitude": sample.latitude,
                    "longitude": sample.longitude,
                    "along_track_m": sample.along_track_m,
                    "cross_track_m": sample.cross_track_m,
                    "terrain_elevation_m": sample.terrain_elevation_m,
                    "peak_positive_overpressure_pa": sample.peak_positive_overpressure_pa,
                    "peak_negative_overpressure_pa": sample.peak_negative_overpressure_pa,
                    "uncertainty_upper_pa": sample.uncertainty_upper_pa,
                    "perceived_level_db": sample.perceived_level_db,
                    "a_weighted_exposure_db": sample.a_weighted_exposure_db,
                    "ray_arrival_time_s": sample.ray_arrival_time_s,
                    "ground_incidence_deg": sample.ground_incidence_deg,
                    "launch_roll_deg": sample.launch_roll_deg,
                    "reflection_factor": sample.reflection_factor,
                    "source_altitude_ft": getattr(sample, "source_altitude_ft", None),
                    "candidate_altitude_offset_ft": float(
                        getattr(candidate, "altitude_offset_ft", 0.0)
                    ),
                    "candidate_altitude_profile_ft": getattr(
                        candidate, "altitude_profile_ft", ()
                    ),
                }
            )
    return rows


def footprint_geojson(result: PhysicalRouteAnalysis) -> dict[str, Any]:
    """Export every terrain-intersected solver sample without inventing polygon coverage."""

    features = []
    for row in surface_sample_rows(result):
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [row["longitude"], row["latitude"]],
                },
                "properties": {
                    key: value
                    for key, value in row.items()
                    if key not in {"latitude", "longitude"}
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


def evidence_zip(result: PhysicalRouteAnalysis, request: dict[str, Any]) -> bytes:
    """Create a self-contained, checksummed physical-analysis evidence bundle."""

    result_bytes = result.model_dump_json(indent=2).encode()
    request_bytes = json.dumps(request, indent=2, default=str).encode()
    footprint_bytes = json.dumps(footprint_geojson(result), indent=2).encode()
    csv_buffer = io.StringIO()
    rows = surface_sample_rows(result)
    writer = csv.DictWriter(csv_buffer, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    files = {
        "request.json": request_bytes,
        "physical_route_analysis.json": result_bytes,
        "surface_samples.csv": csv_buffer.getvalue().encode(),
        "footprint.geojson": footprint_bytes,
    }
    manifest = {
        "schema": "machlane-physical-evidence-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "run_id": result.run_id,
        "solver": result.solver.model_dump(mode="json"),
        "files": {
            name: hashlib.sha256(content).hexdigest() for name, content in files.items()
        },
    }
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for name, content in files.items():
            package.writestr(name, content)
        package.writestr("manifest.json", json.dumps(manifest, indent=2, default=str))
    return archive.getvalue()
