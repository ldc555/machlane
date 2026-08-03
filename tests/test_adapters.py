from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from open_mco.atmosphere import ERA5Provider, HerbieGEFSProvider, HerbieHRRRProvider
from open_mco.demo import demo_route
from open_mco.route import route_from_waypoints
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


def test_hrrr_adapter_extracts_and_labels_real_pressure_profile(
    tmp_path: Path, monkeypatch
) -> None:
    class Variable:
        def __init__(self, values) -> None:
            self.values = np.asarray(values)

    class Point:
        def __init__(self) -> None:
            self.variables = {
                "gh": Variable([10_000, 5_000, 1_000]),
                "t": Variable([220, 250, 280]),
                "u": Variable([30, 20, 10]),
                "v": Variable([5, 4, 3]),
            }
            self.coords = {"isobaricInhPa": Variable([250, 500, 900])}

        def __contains__(self, name: str) -> bool:
            return name in self.variables

        def __getitem__(self, name: str):
            return self.variables[name]

    point = Point()

    class Accessor:
        def pick_points(self, frame):
            assert frame.iloc[0]["stid"] == "MACHLANE"
            return self

        def isel(self, *, point: int):
            assert point == 0
            return globals_point

    globals_point = point

    class Dataset:
        herbie = Accessor()

    class FakeHerbie:
        grib = "https://noaa.example/hrrr.grib2"

        def __init__(self, cycle, **kwargs) -> None:
            assert kwargs["model"] == "hrrr"
            assert kwargs["product"] == "prs"

        def xarray(self, search, **kwargs):
            assert "TMP" in search and kwargs["remove_grib"] is False
            return Dataset()

        def get_localFilePath(self, search):
            return tmp_path / "not-written.grib2"

    monkeypatch.setitem(sys.modules, "herbie", SimpleNamespace(Herbie=FakeHerbie))
    provider = HerbieHRRRProvider(network_enabled=True, forecast_hour=2, cache_dir=tmp_path)
    profile = provider.profile(39.0, -98.0, datetime(2026, 8, 3, 14, tzinfo=UTC))

    assert profile.altitude_m == (1000.0, 5000.0, 10000.0)
    assert profile.pressure_pa == (90000.0, 50000.0, 25000.0)
    assert profile.source.model_cycle == datetime(2026, 8, 3, 12, tzinfo=UTC)
    assert profile.source.forecast_hour == 2
    assert profile.source.provider == "hrrr_via_herbie"
    assert profile.source.label == "REAL_MODEL_DATA_UNVALIDATED_FOR_OPERATIONAL_USE"


def test_3dep_adapter_samples_a_route_leg(monkeypatch) -> None:
    monkeypatch.setitem(
        sys.modules,
        "py3dep",
        SimpleNamespace(
            elevation_bycoords=lambda coordinates, crs: np.arange(len(coordinates), dtype=float)
        ),
    )
    segment = route_from_waypoints([(39.0, -98.0), (39.0, -97.9)], spacing_m=100_000).segments[0]
    profile = USGS3DEPProvider(network_enabled=True, sample_spacing_m=2_000).profile(segment)

    assert len(profile.distance_m) == len(profile.elevation_m)
    assert profile.distance_m[0] == 0
    assert profile.distance_m[-1] == pytest.approx(segment.distance_m)
    assert profile.source.label == "REAL_USGS_DATA_UNVALIDATED_FOR_OPERATIONAL_USE"


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
