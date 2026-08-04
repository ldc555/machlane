from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from openpyxl import load_workbook

from open_mco.atmosphere import SyntheticAtmosphereProvider
from open_mco.demo import synthetic_aircraft
from open_mco.models import GroundSignature, SonicBoomCase, SonicBoomPrediction
from open_mco.physics import FastMCOEngine, assess_boom_readiness, load_near_field_signature
from open_mco.physics.atmospheric import prepare_moist_thermodynamics
from open_mco.physics.signatures import NearFieldSignatureError, read_near_field_samples
from open_mco.route import route_from_waypoints
from open_mco.terrain import FlatTerrainProvider
from open_mco.validation import SU2NearFieldAdapter


def _write_signature(path: Path) -> Path:
    path.write_text(
        "distance_m,overpressure_pa\n0,-1.5\n1,3.0\n2,-0.5\n",
        encoding="utf-8",
    )
    return path


def test_near_field_signature_is_strict_and_provenanced(tmp_path: Path) -> None:
    path = _write_signature(tmp_path / "signature.csv")
    signature = load_near_field_signature(
        path,
        reference_distance_m=100,
        azimuth_deg=0,
        flight_mach=1.4,
        flight_altitude_m=15_000,
        provider="test_cfd",
        solver_version="1.0",
    )
    assert signature.overpressure_pa == (-1.5, 3.0, -0.5)
    assert len(signature.source.checksum) == 64

    template = tmp_path / "template.csv"
    template.write_text("distance_m,overpressure_pa\n0,0\n1,0\n2,0\n")
    with pytest.raises(NearFieldSignatureError, match="all-zero"):
        read_near_field_samples(template)


def test_metpy_prepares_moist_atmosphere_without_calculating_boom() -> None:
    profile = SyntheticAtmosphereProvider().profile(
        37, -97, datetime(2026, 1, 1, tzinfo=UTC)
    )
    with pytest.raises(ValueError, match="relative humidity"):
        prepare_moist_thermodynamics(profile)
    humid_profile = profile.model_copy(
        update={"humidity_fraction": tuple(0.4 for _ in profile.altitude_m)}
    )
    prepared = prepare_moist_thermodynamics(humid_profile)
    assert len(prepared.density_kg_m3) == len(profile.altitude_m)
    assert all(value > 0 for value in prepared.density_kg_m3)
    assert all(value > 0 for value in prepared.water_vapor_mixing_ratio)


def test_sonic_boom_case_refuses_signature_from_another_operating_point(tmp_path: Path) -> None:
    signature = load_near_field_signature(
        _write_signature(tmp_path / "signature.csv"),
        reference_distance_m=100,
        azimuth_deg=0,
        flight_mach=1.4,
        flight_altitude_m=15_000,
        provider="test_cfd",
    )
    segment = route_from_waypoints([(37, -97), (37, -96.9)], spacing_m=20_000).segments[0]
    atmosphere = SyntheticAtmosphereProvider().profile(
        37, -97, datetime(2026, 1, 1, tzinfo=UTC)
    )
    terrain = FlatTerrainProvider().profile(segment)
    with pytest.raises(ValueError, match="Mach does not match"):
        SonicBoomCase(
            aircraft=synthetic_aircraft(),
            segment=segment,
            atmosphere=atmosphere,
            terrain=terrain,
            near_field_signature=signature,
            mach=1.3,
            altitude_m=15_000,
            boom_limit_pa=5.27,
        )

    case = SonicBoomCase(
        aircraft=synthetic_aircraft(),
        segment=segment,
        atmosphere=atmosphere,
        terrain=terrain,
        near_field_signature=signature,
        mach=1.4,
        altitude_m=15_000,
        boom_limit_pa=5.27,
    )
    with pytest.raises(NotImplementedError, match="near-field-to-ground"):
        FastMCOEngine().predict(case)


def test_ground_results_fail_closed_on_metrics_and_missing_ray_families() -> None:
    primary = GroundSignature(
        latitude=37,
        longitude=-97,
        ray_family="PRIMARY",
        time_s=(0.0, 0.1, 0.2),
        overpressure_pa=(-1.0, 4.0, 0.0),
        peak_positive_overpressure_pa=4.0,
        peak_negative_overpressure_pa=-1.0,
    )
    with pytest.raises(ValueError, match="incomplete ray-family"):
        SonicBoomPrediction(
            engine_name="test",
            engine_version="1",
            signatures=(primary,),
            requested_ray_families=("PRIMARY", "SECONDARY_DIRECT"),
            boom_limit_pa=5.27,
            classification="WITHIN_LIMIT",
        )
    prediction = SonicBoomPrediction(
        engine_name="test",
        engine_version="1",
        signatures=(primary,),
        requested_ray_families=("PRIMARY", "SECONDARY_DIRECT"),
        boom_limit_pa=5.27,
        classification="UNKNOWN",
    )
    assert prediction.classification == "UNKNOWN"
    with pytest.raises(ValueError, match="does not match"):
        GroundSignature(
            latitude=37,
            longitude=-97,
            ray_family="PRIMARY",
            time_s=(0.0, 0.1, 0.2),
            overpressure_pa=(-1.0, 4.0, 0.0),
            peak_positive_overpressure_pa=3.0,
            peak_negative_overpressure_pa=-1.0,
        )


def test_readiness_reports_all_blockers_without_network(
    aircraft_workbook: Path, tmp_path: Path
) -> None:
    workbook = load_workbook(aircraft_workbook)
    mission = workbook["Mission_Config"]
    mission.append(["Boom Limit", 0.11, "psf", "No", "Test", "p. 7", None])
    workbook.save(aircraft_workbook)

    report = assess_boom_readiness(aircraft_workbook)
    status = {check.key: check.status.value for check in report.checks}
    assert status["aircraft_required_data"] == "READY"
    assert status["aircraft_performance_map"] == "READY"
    assert status["mission_acceptance_inputs"] == "READY"
    assert status["near_field_signature"] == "MISSING_INPUT"
    assert status["physical_propagation_engine"] == "NOT_IMPLEMENTED"
    assert report.ready_for_physical_prediction is False
    assert len(report.blockers) >= 3

    report_with_signature = assess_boom_readiness(
        aircraft_workbook, near_field_path=_write_signature(tmp_path / "signature.csv")
    )
    signature_check = next(
        check for check in report_with_signature.checks if check.key == "near_field_signature"
    )
    assert signature_check.status.value == "READY"


def test_su2_adapter_stages_checksummed_inputs(tmp_path: Path, monkeypatch) -> None:
    config = tmp_path / "case.cfg"
    mesh = tmp_path / "mesh.su2"
    config.write_text("MACH_NUMBER= 1.4\n")
    mesh.write_text("NDIME= 3\n")
    monkeypatch.setattr("shutil.which", lambda executable: "/opt/su2/SU2_CFD")
    adapter = SU2NearFieldAdapter(version="8.5.0")
    manifest = adapter.stage_case(
        config_path=config,
        mesh_path=mesh,
        staging_directory=tmp_path / "stage",
        operating_point={
            "mach": 1.4,
            "altitude_m": 15_000,
            "reference_distance_m": 100,
            "azimuth_deg": 0,
        },
    )
    payload = json.loads(manifest.read_text())
    assert payload["execution_status"] == "NOT_RUN"
    assert payload["executable"] == "/opt/su2/SU2_CFD"
    assert len(payload["mesh"]["sha256"]) == 64

    with pytest.raises(ValueError, match="azimuth_deg"):
        adapter.stage_case(
            config_path=config,
            mesh_path=mesh,
            staging_directory=tmp_path / "bad-stage",
            operating_point={"mach": 1.4, "altitude_m": 15_000, "reference_distance_m": 100},
        )
