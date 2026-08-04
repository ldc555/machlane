"""Write auditable run artifacts while explicitly preserving unsupported claims."""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pandas as pd
from jinja2 import Template

from open_mco import __version__
from open_mco.models import (
    AircraftModel,
    AtmosphericSourceMetadata,
    ComplianceStatus,
    PlannerResult,
    Route,
    RunManifest,
    TerrainSourceMetadata,
)
from open_mco.route import corridor_geojson


def compliance_matrix() -> dict[str, ComplianceStatus]:
    """Return the honest initial status of every major FAA-oriented outcome."""

    return {
        "aircraft_configuration_traceability": ComplianceStatus.SUPPORTED,
        "route_provenance": ComplianceStatus.SUPPORTED,
        "weather_provenance": ComplianceStatus.SUPPORTED,
        "terrain_provenance": ComplianceStatus.SUPPORTED,
        "propagation_model_identification": ComplianceStatus.SUPPORTED,
        "segment_operational_limits": ComplianceStatus.PARTIAL,
        "uncertainty_reliability": ComplianceStatus.VALIDATION_REQUIRED,
        "primary_boom_0_11_psf": ComplianceStatus.NOT_IMPLEMENTED,
        "secondary_direct_boom": ComplianceStatus.NOT_IMPLEMENTED,
        "secondary_indirect_boom": ComplianceStatus.NOT_IMPLEMENTED,
        "pcboom_or_flight_test_validation": ComplianceStatus.VALIDATION_REQUIRED,
        "faa_approval": ComplianceStatus.VALIDATION_REQUIRED,
    }


def _sha256(path: Path) -> str:
    if not path.exists():
        return "MISSING"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], check=True, text=True, capture_output=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "UNCOMMITTED"


def _report_html(manifest: RunManifest, result: PlannerResult) -> str:
    template = Template("""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>MachLane {{ manifest.run_id }}</title>
<style>body{font-family:system-ui;max-width:1000px;margin:2rem auto;padding:0 1rem;color:#172033}
.banner{background:#7f1d1d;color:white;padding:1rem;font-weight:700}.card{border:1px solid #ccd5e0;padding:1rem;margin:1rem 0}
table{border-collapse:collapse;width:100%}td,th{padding:.45rem;border-bottom:1px solid #ddd;text-align:left}</style></head>
<body><div class="banner">RESEARCH PROTOTYPE — NOT FAA APPROVED</div>
<h1>MachLane evidence report</h1><p>Run <code>{{ manifest.run_id }}</code></p>
<div class="card"><h2>Method-of-compliance evidence</h2><p>Sources, checksums, engine identity,
assumptions and limitations are captured. The mock engine is synthetic and produces no regulatory finding.</p></div>
<div class="card"><h2>Operational means-of-compliance evidence</h2><p>Segment recommendations and rejected
candidates are exportable, but no dispatch/FMS or airborne control authority is implemented.</p></div>
<h2>Segment recommendations</h2><table><tr><th>Segment</th><th>Status</th><th>Mach</th><th>Altitude m</th></tr>
{% for item in result.segment_limits %}<tr><td>{{ item.segment_id }}</td><td>{{ item.status }}</td>
<td>{{ item.selected_mach }}</td><td>{{ item.selected_altitude_m }}</td></tr>{% endfor %}</table>
<h2>Compliance and validation status</h2><table><tr><th>Outcome</th><th>Status</th></tr>
{% for key, value in manifest.compliance_statuses.items() %}<tr><td>{{ key }}</td><td>{{ value }}</td></tr>{% endfor %}</table>
<div class="card"><h2>Unsupported claims</h2><ul><li>Absolute surface overpressure ≤ 0.11 psf: NOT_IMPLEMENTED</li>
<li>Primary propagation compliance: NOT_IMPLEMENTED</li><li>Secondary-direct: NOT_IMPLEMENTED</li>
<li>Secondary-indirect: NOT_IMPLEMENTED</li><li>FAA approval and flight-test validation: VALIDATION_REQUIRED</li></ul></div>
</body></html>""")
    return cast(str, template.render(manifest=manifest, result=result))


def write_evidence_package(
    *,
    aircraft: AircraftModel,
    route: Route,
    result: PlannerResult,
    atmosphere_source: AtmosphericSourceMetadata,
    terrain_source: TerrainSourceMetadata,
    configuration_path: Path,
    results_root: Path,
) -> Path:
    """Create a non-overwriting evidence package with human- and machine-readable artifacts."""

    run_dir = results_root / result.run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "figures").mkdir()
    started = result.created_at
    manifest = RunManifest(
        run_id=result.run_id,
        package_version=__version__,
        git_commit_sha=_git_sha(),
        configuration_checksum=_sha256(configuration_path),
        aircraft_workbook_checksum=aircraft.workbook_checksum,
        source_data_checksums={
            **atmosphere_source.checksums,
            **({"terrain": terrain_source.checksum} if terrain_source.checksum else {}),
            **(
                {"route": route.source.checksum}
                if route.source is not None and route.source.checksum
                else {}
            ),
        },
        weather_source=atmosphere_source,
        terrain_source=terrain_source,
        propagation_engine=result.engine_name,
        propagation_engine_version=result.engine_version,
        reliability_setting=result.reliability_level,
        assumptions=("synthetic weather and terrain in the default demo",),
        limitations=(
            "mock propagation is arbitrary and not engineering physics",
            "finite scenario rates are not validated regulatory reliability",
            "corridor is not legal airspace approval",
        ),
        compliance_statuses=compliance_matrix(),
        started_at=started,
        completed_at=datetime.now(UTC),
    )
    (run_dir / "manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "route.json").write_text(route.model_dump_json(indent=2), encoding="utf-8")
    with (run_dir / "segment_limits.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["segment_id", "status", "selected_mach", "selected_altitude_m"]
        )
        writer.writeheader()
        for limit in result.segment_limits:
            writer.writerow(
                {
                    "segment_id": limit.segment_id,
                    "status": limit.status,
                    "selected_mach": limit.selected_mach,
                    "selected_altitude_m": limit.selected_altitude_m,
                }
            )
    candidate_rows: list[dict[str, Any]] = []
    for limit in result.segment_limits:
        for item in limit.candidate_evaluations:
            candidate_rows.append(
                {
                    "segment_id": item.segment_id,
                    "mach": item.mach,
                    "altitude_m": item.altitude_m,
                    "accepted": item.accepted,
                    "reason": item.reason,
                    "classification": None
                    if item.propagation is None
                    else item.propagation.classification,
                    "engine_label": None if item.propagation is None else item.propagation.label,
                }
            )
    pd.DataFrame(candidate_rows).to_parquet(run_dir / "candidate_evaluations.parquet", index=False)
    (run_dir / "corridor.geojson").write_text(
        json.dumps(corridor_geojson(route, result.segment_limits), indent=2), encoding="utf-8"
    )
    (run_dir / "report.html").write_text(_report_html(manifest, result), encoding="utf-8")
    return run_dir
