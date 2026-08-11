"""Route-level adapter for MachLane's clean-room open research solver."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import numpy as np
from pyproj import Geod

from open_mco.physics.route_analysis import (
    PhysicalRouteAnalysis,
    RayFamily,
    RouteCandidateAnalysis,
    RouteSolverProvenance,
    SurfaceFootprintSample,
    request_checksum,
)

from .atmosphere import AtmosphereCoverageError, column_from_mapping
from .burgers import propagate_primary_waveform
from .rays import RayTraceError, trace_primary_ray

FEET_TO_METERS = 0.3048
PSF_TO_PASCALS = 47.88025898033584
GEOD = Geod(ellps="WGS84")


class ResearchSolverUnavailableError(RuntimeError):
    """No honest research result can be produced from the supplied request."""


def _source_checksum() -> str:
    digest = hashlib.sha256()
    directory = Path(__file__).resolve().parent
    for path in sorted(directory.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _configuration_checksum() -> str:
    configuration = {
        "schema": "machlane-open-primary-v2",
        "ray_model": "3D-heading stratified Snell primary ray",
        "nonlinearity": "conservative Rusanov finite volume",
        "absorption": "ISO-9613-style classical plus O2/N2 relaxation",
        "spreading": "cylindrical ray-tube proxy plus impedance stratification",
        "ground": "rigid pressure doubling",
        "launch_rolls_deg": [-60, -30, 0, 30, 60],
        "altitude_sensitivity_ft": [-4000, -2000, 2000, 4000],
        "altitude_sensitivity_source_signature": "frozen baseline signature",
        "secondary_rays": "not implemented",
        "loudness": "not implemented",
    }
    return hashlib.sha256(
        json.dumps(configuration, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _signed_roll(azimuth_deg: float) -> float:
    return azimuth_deg - 360.0 if azimuth_deg > 180.0 else azimuth_deg


def _segment_midpoint(segment: dict[str, Any]) -> tuple[float, float]:
    longitude, latitude, _ = GEOD.fwd(
        float(segment["start_longitude"]),
        float(segment["start_latitude"]),
        float(segment["bearing_deg"]),
        float(segment["distance_m"]) / 2.0,
    )
    return latitude, longitude


def _terrain_elevation(region: dict[str, Any]) -> float:
    terrain = region["terrain"]
    if terrain.get("status") != "LOADED" or terrain.get("profile") is None:
        raise ResearchSolverUnavailableError(
            f"{region['segment']['segment_id']} has no propagation-grade terrain sample"
        )
    profile = terrain["profile"]
    distances = np.asarray(profile["distance_m"], dtype=np.float64)
    elevations = np.asarray(profile["elevation_m"], dtype=np.float64)
    if distances.size < 1 or distances.size != elevations.size:
        raise ResearchSolverUnavailableError("terrain profile is empty or inconsistent")
    midpoint = float(region["segment"]["distance_m"]) / 2.0
    return float(np.interp(midpoint, distances, elevations))


def _signature_groups(aircraft: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sample in aircraft.get("nearfield_samples", []):
        groups[str(sample["signature_id"])].append(sample)
    for samples in groups.values():
        samples.sort(key=lambda item: float(item["axial_position_ft"]))
    return groups


def _calculate_candidate(
    request: dict[str, Any],
    *,
    signature_groups: dict[str, list[dict[str, Any]]],
    requested_families: tuple[RayFamily, ...],
    candidate_id: str,
    label: str,
    altitude_offset_ft: float,
) -> RouteCandidateAnalysis:
    route_coordinates = tuple(
        (float(latitude), float(longitude))
        for latitude, longitude in request["route"]["waypoints"]
    )
    distance_m = sum(float(region["segment"]["distance_m"]) for region in request["regions"])
    along_track_m = 0.0
    samples: list[SurfaceFootprintSample] = []
    skipped: list[str] = []
    numerical_records: list[str] = []
    for region in request["regions"]:
        segment = region["segment"]
        segment_distance = float(segment["distance_m"])
        sample_along_track = along_track_m + segment_distance / 2.0
        along_track_m += segment_distance
        if region.get("propagation_eligibility") != "READY":
            continue
        matching_ids = [
            identifier
            for identifier in region.get("matching_signature_ids", [])
            if identifier in signature_groups
        ]
        if not matching_ids:
            skipped.append(f"{segment['segment_id']}: no matching signature")
            continue
        source_altitude_ft = (
            float(region["planned_state"]["altitude_ft"]) + altitude_offset_ft
        )
        try:
            terrain_elevation = _terrain_elevation(region)
            column = column_from_mapping(region["atmosphere"])
            mach = float(region["planned_state"]["mach"])
            altitude_m = source_altitude_ft * FEET_TO_METERS
        except (AtmosphereCoverageError, ResearchSolverUnavailableError, ValueError) as exc:
            skipped.append(f"{segment['segment_id']}: {exc}")
            continue
        target_rolls = (-60.0, -30.0, 0.0, 30.0, 60.0)
        selected_ids: list[str] = []
        for target_roll in target_rolls:
            selected_id = min(
                matching_ids,
                key=lambda identifier: abs(
                    _signed_roll(float(signature_groups[identifier][0]["azimuth_deg"]))
                    - target_roll
                ),
            )
            selected_roll = _signed_roll(
                float(signature_groups[selected_id][0]["azimuth_deg"])
            )
            if abs(selected_roll - target_roll) <= 5 and selected_id not in selected_ids:
                selected_ids.append(selected_id)

        latitude, longitude = _segment_midpoint(segment)
        for selected_id in selected_ids:
            signature = signature_groups[selected_id]
            roll = _signed_roll(float(signature[0]["azimuth_deg"]))
            try:
                ray = trace_primary_ray(
                    column,
                    source_altitude_m=altitude_m,
                    ground_altitude_m=terrain_elevation,
                    mach=mach,
                    route_bearing_deg=float(segment["bearing_deg"]),
                    launch_roll_deg=roll,
                )
                waveform = propagate_primary_waveform(
                    axial_position_m=np.asarray(
                        [
                            float(item["axial_position_ft"]) * FEET_TO_METERS
                            for item in signature
                        ]
                    ),
                    nearfield_overpressure_pa=np.asarray(
                        [
                            float(item["delta_pressure_psf"]) * PSF_TO_PASCALS
                            for item in signature
                        ]
                    ),
                    reference_distance_m=float(signature[0]["reference_distance_ft"])
                    * FEET_TO_METERS,
                    mach=mach,
                    ray=ray,
                )
            except (AtmosphereCoverageError, RayTraceError, ValueError) as exc:
                skipped.append(f"{segment['segment_id']} roll {roll:+g}°: {exc}")
                continue

            receiver_longitude, receiver_latitude, _ = GEOD.fwd(
                longitude,
                latitude,
                ray.bearing_deg,
                ray.horizontal_distance_m,
            )
            pressure = waveform.overpressure_pa
            positive_peak = max(0.0, float(np.max(pressure)))
            negative_peak = min(0.0, float(np.min(pressure)))
            samples.append(
                SurfaceFootprintSample(
                    candidate_id=candidate_id,
                    segment_id=str(segment["segment_id"]),
                    ray_family="PRIMARY",
                    latitude=receiver_latitude,
                    longitude=receiver_longitude,
                    along_track_m=sample_along_track,
                    cross_track_m=ray.cross_track_offset_m,
                    terrain_elevation_m=terrain_elevation,
                    time_s=tuple(float(value) for value in waveform.time_s),
                    overpressure_pa=tuple(float(value) for value in pressure),
                    peak_positive_overpressure_pa=positive_peak,
                    peak_negative_overpressure_pa=negative_peak,
                    perceived_level_db=None,
                    a_weighted_exposure_db=None,
                    ray_arrival_time_s=ray.travel_time_s,
                    ground_incidence_deg=ray.ground_incidence_deg,
                    uncertainty_upper_pa=None,
                    launch_roll_deg=roll,
                    reflection_factor=waveform.reflection_factor,
                    source_altitude_ft=source_altitude_ft,
                    ray_path_horizontal_m=tuple(
                        float(value) for value in ray.horizontal_from_source_m
                    ),
                    ray_path_altitude_m=tuple(
                        float(value) for value in ray.altitude_from_source_m
                    ),
                )
            )
            numerical_records.append(
                f"{segment['segment_id']} roll {roll:+g}°: {waveform.step_count} steps, "
                f"CFL {waveform.maximum_cfl:.3f}"
            )

    if not samples:
        detail = "; ".join(skipped[:5]) or "no propagation-eligible supersonic region"
        raise ResearchSolverUnavailableError(
            f"open research solver produced no receiver for {candidate_id}: {detail}"
        )
    nominal_maximum = max(sample.peak_positive_overpressure_pa for sample in samples)
    sensitivity_limitations: tuple[str, ...] = ()
    if altitude_offset_ft:
        sensitivity_limitations = (
            "altitude sensitivity holds the baseline near-field signature fixed",
            "aircraft performance, weight, angle of attack, and fuel feasibility are not recomputed",
            "this sensitivity is not a recommended or compliant operating corridor",
        )
    return RouteCandidateAnalysis(
        candidate_id=candidate_id,
        label=label,
        route_coordinates=route_coordinates,
        distance_m=distance_m,
        time_delta_min=0.0,
        maximum_lateral_offset_m=0.0,
        altitude_offset_ft=altitude_offset_ft,
        requested_ray_families=requested_families,
        completed_ray_families=("PRIMARY",),
        surface_samples=tuple(samples),
        maximum_nominal_overpressure_pa=nominal_maximum,
        maximum_uncertainty_overpressure_pa=nominal_maximum,
        classification="UNKNOWN",
        operational_constraints_checked=(
            "exact OpenSky baseline geometry",
            "route/time-matched NOAA column",
            "available route-aligned USGS 3DEP terrain",
            (
                "LM1021 near-field operating-point match"
                if not altitude_offset_ft
                else "baseline near-field signature frozen for altitude sensitivity"
            ),
        ),
        limitations=(
            "UNVALIDATED primary-ray research estimate",
            "secondary-direct and secondary-indirect rays are not implemented",
            "model-form and atmospheric uncertainty are not bounded",
            "terrain is sampled from the route-aligned 3DEP preview, not a full receiver raster",
            "off-track intersections reuse the region's route-aligned terrain elevation",
            "rigid-ground pressure doubling is a declared conservative approximation",
            "PLdB and ASEL are not implemented",
            *sensitivity_limitations,
            *skipped,
            *numerical_records,
        ),
    )


class OpenResearchRouteSolver:
    """Compute unvalidated primary-ray route estimates from public equations and real inputs."""

    name = "machlane-open-primary"
    version = "0.3.0-unvalidated"

    def run(self, request: dict[str, Any]) -> PhysicalRouteAnalysis:
        if request.get("schema") != "machlane-route-solver-request-v1":
            raise ResearchSolverUnavailableError("unsupported physical request schema")
        requested_families = cast(
            tuple[RayFamily, ...],
            tuple(request["acceptance"]["requested_ray_families"]),
        )
        if "PRIMARY" not in requested_families:
            raise ResearchSolverUnavailableError("request does not include primary rays")
        signature_groups = _signature_groups(request["aircraft"])
        if not signature_groups:
            raise ResearchSolverUnavailableError("aircraft workbook contains no near-field signature")

        candidate = _calculate_candidate(
            request,
            signature_groups=signature_groups,
            requested_families=requested_families,
            candidate_id="baseline",
            label="OpenSky baseline · primary-ray research estimate",
            altitude_offset_ft=0.0,
        )
        candidates = [candidate]
        boom_limit_pa = float(request["acceptance"]["boom_limit_pa"])
        if candidate.maximum_nominal_overpressure_pa > boom_limit_pa:
            for altitude_offset_ft in (-4_000.0, -2_000.0, 2_000.0, 4_000.0):
                try:
                    sensitivity = _calculate_candidate(
                        request,
                        signature_groups=signature_groups,
                        requested_families=requested_families,
                        candidate_id=(
                            f"altitude-{'plus' if altitude_offset_ft > 0 else 'minus'}-"
                            f"{abs(int(altitude_offset_ft))}"
                        ),
                        label=f"Altitude sensitivity {altitude_offset_ft:+,.0f} ft",
                        altitude_offset_ft=altitude_offset_ft,
                    )
                except ResearchSolverUnavailableError:
                    continue
                candidates.append(sensitivity)
        source_checksums: dict[str, str] = {}
        workbook_checksum = request["aircraft"].get("workbook_checksum")
        if workbook_checksum:
            source_checksums["aircraft_workbook"] = str(workbook_checksum)
        return PhysicalRouteAnalysis(
            run_id=f"open-primary-{request_checksum(request)[:12]}",
            created_at=datetime.now(UTC),
            request_checksum=request_checksum(request),
            solver=RouteSolverProvenance(
                name=self.name,
                version=self.version,
                executable_checksum=_source_checksum(),
                configuration_checksum=_configuration_checksum(),
                validation_status="UNVALIDATED",
                reference_cases=(
                    "NASA SBPW2 LM1021 public near-field input loaded; output not yet benchmarked",
                ),
                source_url="https://ntrs.nasa.gov/citations/20230005332",
            ),
            boom_limit_pa=boom_limit_pa,
            baseline_candidate_id="baseline",
            recommended_candidate_id=None,
            candidates=tuple(candidates),
            source_checksums=source_checksums,
            assumptions=(
                "horizontally stratified NOAA atmosphere within each route region",
                "primary geometrical-acoustics ray uses a fixed horizontal heading",
                "conservative nonlinear waveform march with ISO-style absorption",
                "rigid locally horizontal ground",
            ),
            limitations=candidate.limitations,
        )
