from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from open_mco.atmosphere import ERA5Provider, HerbieGEFSProvider, HerbieHRRRProvider
from open_mco.demo import demo_route
from open_mco.terrain import RasterTerrainProvider, USGS3DEPProvider
from open_mco.validation import PCBoomAdapter


@pytest.mark.parametrize("provider", [HerbieHRRRProvider(), HerbieGEFSProvider(), ERA5Provider()])
def test_weather_adapters_do_not_call_network_by_default(provider) -> None:
    with pytest.raises(RuntimeError, match="disabled"):
        provider.profile(37, -97, datetime.now(UTC))


def test_terrain_adapters_validate_configuration(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="disabled"):
        USGS3DEPProvider().profile(demo_route().segments[0])
    with pytest.raises(FileNotFoundError):
        RasterTerrainProvider(tmp_path / "missing.tif")


def test_pcboom_offline_exchange_and_comparison(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="version"):
        PCBoomAdapter(version="", configuration={})
    adapter = PCBoomAdapter(version="7.5-user-supplied", configuration={"mode": "comparison"})
    case = adapter.export_case({"mach": 1.1}, tmp_path / "stage")
    assert json.loads(case.read_text())["normalized_case"]["mach"] == 1.1
    supplied = tmp_path / "result.json"
    supplied.write_text(json.dumps({"classification": "SAFE", "metric": 1.0}))
    result = adapter.import_results(supplied)
    comparison = adapter.compare({"classification": "SAFE"}, result)
    assert comparison["classification_match"] is True
    assert comparison["validation_status"] == "REVIEW_REQUIRED"


def test_pcboom_rejects_bad_result(tmp_path: Path) -> None:
    adapter = PCBoomAdapter(version="7.5", configuration={})
    invalid = tmp_path / "invalid.json"
    invalid.write_text("[]")
    with pytest.raises(ValueError, match="classification"):
        adapter.import_results(invalid)
