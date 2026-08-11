from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from open_mco.physics import (
    ExternalRouteSolver,
    PhysicalRouteAnalysis,
    RouteCandidateAnalysis,
    RouteSolverProvenance,
    SurfaceFootprintSample,
    evidence_zip,
    footprint_geojson,
    request_checksum,
)


def _sample(candidate_id: str, family: str, peak: float) -> SurfaceFootprintSample:
    return SurfaceFootprintSample(
        candidate_id=candidate_id,
        segment_id="S0001",
        ray_family=family,
        latitude=32.8,
        longitude=-96.8,
        along_track_m=1_000,
        cross_track_m=0,
        terrain_elevation_m=180,
        time_s=(0.0, 0.01, 0.02),
        overpressure_pa=(-peak / 2, peak, 0.0),
        peak_positive_overpressure_pa=peak,
        peak_negative_overpressure_pa=-peak / 2,
        perceived_level_db=70.0,
        uncertainty_upper_pa=peak + 0.2,
    )


def _result(request: dict[str, object]) -> PhysicalRouteAnalysis:
    families = ("PRIMARY", "SECONDARY_DIRECT", "SECONDARY_INDIRECT")
    samples = tuple(_sample("baseline", family, 4.0) for family in families)
    candidate = RouteCandidateAnalysis(
        candidate_id="baseline",
        label="Observed-route baseline",
        route_coordinates=((32.8, -96.8), (40.6, -73.8)),
        distance_m=2_200_000,
        time_delta_min=0,
        maximum_lateral_offset_m=0,
        requested_ray_families=families,
        completed_ray_families=families,
        surface_samples=samples,
        maximum_nominal_overpressure_pa=4.0,
        maximum_uncertainty_overpressure_pa=4.2,
        classification="WITHIN_LIMIT",
        operational_constraints_checked=("terrain", "weather", "aircraft envelope"),
    )
    return PhysicalRouteAnalysis(
        run_id="physical-test",
        created_at=datetime.now(UTC),
        request_checksum=request_checksum(request),
        solver=RouteSolverProvenance(
            name="test-solver",
            version="1.0",
            executable_checksum="a" * 64,
            configuration_checksum="b" * 64,
            validation_status="VALIDATED",
            reference_cases=("NASA SBPW2 LM1021 Profile 1",),
        ),
        boom_limit_pa=5.27,
        baseline_candidate_id="baseline",
        recommended_candidate_id="baseline",
        candidates=(candidate,),
    )


def test_physical_route_result_exports_machine_readable_evidence() -> None:
    request: dict[str, object] = {"schema": "machlane-route-solver-request-v1"}
    result = _result(request)
    geojson = footprint_geojson(result)
    assert len(geojson["features"]) == 3
    package = evidence_zip(result, request)
    assert package.startswith(b"PK")


def test_recommendation_requires_validated_solver() -> None:
    request: dict[str, object] = {"schema": "machlane-route-solver-request-v1"}
    payload = _result(request).model_dump(mode="json")
    payload["solver"]["validation_status"] = "UNVALIDATED"
    with pytest.raises(ValidationError, match="operational recommendation requires"):
        PhysicalRouteAnalysis.model_validate(payload)


def test_incomplete_ray_coverage_cannot_be_classified_within_limit() -> None:
    sample = _sample("baseline", "PRIMARY", 4.0)
    with pytest.raises(ValidationError, match="incomplete primary/secondary ray coverage"):
        RouteCandidateAnalysis(
            candidate_id="baseline",
            label="Baseline",
            route_coordinates=((32.8, -96.8), (40.6, -73.8)),
            distance_m=2_200_000,
            time_delta_min=0,
            maximum_lateral_offset_m=0,
            requested_ray_families=("PRIMARY", "SECONDARY_DIRECT"),
            completed_ray_families=("PRIMARY",),
            surface_samples=(sample,),
            maximum_nominal_overpressure_pa=4.0,
            maximum_uncertainty_overpressure_pa=4.2,
            classification="WITHIN_LIMIT",
        )


def test_external_solver_uses_checksum_bound_json_contract(tmp_path: Path) -> None:
    wrapper = tmp_path / "wrapper.py"
    result_template = _result({"schema": "placeholder"}).model_dump(mode="json")
    wrapper.write_text(
        """
import argparse, hashlib, json
p = argparse.ArgumentParser()
p.add_argument('--input', required=True)
p.add_argument('--output', required=True)
a = p.parse_args()
request = json.load(open(a.input))
canonical = json.dumps(request, sort_keys=True, separators=(',', ':'), default=str).encode()
result = RESULT
result['request_checksum'] = hashlib.sha256(canonical).hexdigest()
json.dump(result, open(a.output, 'w'))
""".replace("RESULT", repr(result_template)),
        encoding="utf-8",
    )
    request: dict[str, object] = {"schema": "machlane-route-solver-request-v1"}
    result = ExternalRouteSolver((sys.executable, str(wrapper))).run(request)
    assert result.request_checksum == request_checksum(request)


def test_result_json_is_round_trip_stable() -> None:
    request: dict[str, object] = {"schema": "machlane-route-solver-request-v1"}
    result = _result(request)
    assert PhysicalRouteAnalysis.model_validate_json(
        json.dumps(result.model_dump(mode="json"))
    ) == result
