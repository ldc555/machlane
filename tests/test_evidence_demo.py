from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from open_mco.compliance import compliance_matrix
from open_mco.demo import run_demo
from open_mco.models import ComplianceStatus


def test_demo_writes_complete_honest_evidence_package(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(Path(__file__).parents[1])
    run_dir = run_demo(results_root=tmp_path)
    expected = {
        "manifest.json",
        "route.json",
        "segment_limits.csv",
        "candidate_evaluations.parquet",
        "corridor.geojson",
        "report.html",
        "figures",
    }
    assert {path.name for path in run_dir.iterdir()} == expected
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["propagation_engine"] == "mock_mco"
    route = json.loads((run_dir / "route.json").read_text())
    assert route["source"]["provider"] == "OurAirports + pyproj.Geod"
    assert manifest["compliance_statuses"]["route_provenance"] == "SUPPORTED"
    assert manifest["compliance_statuses"]["primary_boom_0_11_psf"] == "NOT_IMPLEMENTED"
    assert "NOT FAA APPROVED" in (run_dir / "report.html").read_text()
    assert not pd.read_parquet(run_dir / "candidate_evaluations.parquet").empty


def test_compliance_matrix_never_claims_primary_support() -> None:
    matrix = compliance_matrix()
    assert matrix["primary_boom_0_11_psf"] is ComplianceStatus.NOT_IMPLEMENTED
    assert matrix["faa_approval"] is ComplianceStatus.VALIDATION_REQUIRED
