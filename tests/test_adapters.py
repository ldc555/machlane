from __future__ import annotations

import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import requests

from open_mco.atmosphere import ERA5Provider, HerbieGEFSProvider, HerbieHRRRProvider
from open_mco.demo import demo_route
from open_mco.models import RouteSourceMetadata
from open_mco.route import (
    OpenSkyRouteCache,
    OpenSkyRouteNotFoundError,
    OpenSkyTrackProvider,
    get_mission,
    route_from_waypoints,
)
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


def test_opensky_adapter_does_not_call_network_by_default() -> None:
    mission = get_mission("dfw_jfk")
    provider = OpenSkyTrackProvider(client_id="client", client_secret="secret")
    with pytest.raises(RuntimeError, match="disabled"):
        provider.route_for_airports(
            mission.origin,
            mission.destination,
            begin=datetime(2026, 8, 3, tzinfo=UTC),
            end=datetime(2026, 8, 4, tzinfo=UTC),
        )


def test_opensky_adapter_imports_latest_matching_observed_track(monkeypatch) -> None:
    monkeypatch.delenv("MACHLANE_NETWORK_DISABLED", raising=False)

    class FakeResponse:
        def __init__(self, payload, status_code: int = 200) -> None:
            self.payload = payload
            self.status_code = status_code
            self.headers: dict[str, str] = {}

        def json(self):
            return self.payload

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise requests.HTTPError(f"HTTP {self.status_code}")

    class FakeSession:
        def __init__(self) -> None:
            self.get_calls: list[tuple[str, dict[str, str | int], dict[str, str]]] = []

        def post(self, url, *, data, timeout):
            assert data["client_id"] == "client"
            assert data["client_secret"] == "secret"
            assert timeout == 30
            return FakeResponse({"access_token": "token", "expires_in": 1800})

        def get(self, url, *, params, headers, timeout):
            self.get_calls.append((url, params, headers))
            assert headers["Authorization"] == "Bearer token"
            if url.endswith("/flights/departure"):
                return FakeResponse(
                    [
                        {
                            "icao24": "abc123",
                            "callsign": "OLD1",
                            "firstSeen": 1_785_715_200,
                            "lastSeen": 1_785_744_000,
                            "estArrivalAirport": "KJFK",
                        },
                        {
                            "icao24": "def456",
                            "callsign": "TEST42 ",
                            "firstSeen": 1_785_772_800,
                            "lastSeen": 1_785_801_600,
                            "estArrivalAirport": "KJFK",
                        },
                        {
                            "icao24": "ffffff",
                            "firstSeen": 1_785_772_800,
                            "lastSeen": 1_785_801_600,
                            "estArrivalAirport": "KBOS",
                        },
                    ]
                )
            assert url.endswith("/tracks/all")
            assert params["icao24"] == "def456"
            return FakeResponse(
                {
                    "icao24": "def456",
                    "callsign": "TEST42 ",
                    "startTime": 1_785_772_800,
                    "endTime": 1_785_801_600,
                    "path": [
                        [1_785_772_800, 32.90, -97.04, 0, 0, True],
                        [1_785_772_900, 32.90, -97.04, 100, 0, False],
                        [1_785_773_200, 33.10, -96.70, 5_000, 65, False],
                        [1_785_777_200, 36.50, -88.00, 11_000, 70, False],
                        [1_785_801_200, 40.60, -73.80, 2_000, 75, False],
                    ],
                }
            )

    session = FakeSession()
    mission = get_mission("dfw_jfk")
    provider = OpenSkyTrackProvider(
        network_enabled=True,
        client_id="client",
        client_secret="secret",
        session=session,  # type: ignore[arg-type]
    )
    route = provider.route_for_airports(
        mission.origin,
        mission.destination,
        begin=datetime(2026, 8, 3, tzinfo=UTC),
        end=datetime(2026, 8, 4, tzinfo=UTC),
    )

    assert route.waypoints == (
        (32.9, -97.04),
        (33.1, -96.7),
        (36.5, -88.0),
        (40.6, -73.8),
    )
    assert route.source is not None
    assert route.source.data_kind == "observed_track"
    assert route.source.callsign == "TEST42"
    assert route.source.flight_id == "def456:1785772800"
    assert route.source.point_count == 5
    assert route.source.checksum
    assert len(route.observations) == 5
    assert route.observations[0].on_ground is True
    assert route.observations[1].on_ground is False
    assert route.observations[2].barometric_altitude_m == 5_000
    assert route.observations[-1].true_track_deg == 75
    assert len(session.get_calls) == 2


def test_opensky_recent_lookup_searches_newest_day_first(monkeypatch) -> None:
    provider = OpenSkyTrackProvider()
    mission = get_mission("dfw_jfk")
    expected = mission.build_route()
    begins: list[date] = []

    def fake_route(self, origin, destination, *, begin, end, spacing_m=185_200):
        del self, origin, destination, end, spacing_m
        begins.append(begin.date())
        if len(begins) < 3:
            raise OpenSkyRouteNotFoundError("no matching flight")
        return expected

    monkeypatch.setattr(OpenSkyTrackProvider, "route_for_airports", fake_route)
    route = provider.recent_route_for_airports(
        mission.origin,
        mission.destination,
        on_or_before=datetime(2026, 8, 3, 23, 59, tzinfo=UTC),
        lookback_days=7,
    )

    assert route is expected
    assert begins == [date(2026, 8, 3), date(2026, 8, 2), date(2026, 8, 1)]


def test_opensky_cache_accepts_only_matching_observed_routes(tmp_path: Path) -> None:
    mission = get_mission("dfw_jfk")
    search_date = date(2026, 8, 3)
    route = route_from_waypoints(
        [(32.9, -97.04), (40.6, -73.8)],
        spacing_m=185_200,
        name="OpenSky observed DFW to JFK",
        source=RouteSourceMetadata(
            provider="opensky",
            data_kind="observed_track",
            retrieved_at=datetime(2026, 8, 4, tzinfo=UTC),
            label="OBSERVED_OPENSKY_TRACK_EXPERIMENTAL_NOT_A_FILED_ROUTE",
            origin_icao=mission.origin.icao,
            destination_icao=mission.destination.icao,
            point_count=2,
        ),
    )
    cache = OpenSkyRouteCache(tmp_path)

    path = cache.save(mission.mission_id, search_date, route)
    loaded = cache.load(
        mission.mission_id,
        search_date,
        origin_icao=mission.origin.icao,
        destination_icao=mission.destination.icao,
    )

    assert loaded == route
    assert path.exists()
    assert (
        cache.load(
            mission.mission_id,
            search_date,
            origin_icao=mission.origin.icao,
            destination_icao="KLAX",
        )
        is None
    )
    path.write_text("not json", encoding="utf-8")
    assert (
        cache.load(
            mission.mission_id,
            search_date,
            origin_icao=mission.origin.icao,
            destination_icao=mission.destination.icao,
        )
        is None
    )


def test_hrrr_adapter_extracts_and_labels_real_pressure_profile(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("MACHLANE_NETWORK_DISABLED", raising=False)

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
                "r": Variable([np.nan, 50, 60]),
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
            assert "TMP" in search and "RH" in search and kwargs["remove_grib"] is False
            return Dataset()

        def get_localFilePath(self, search):
            return tmp_path / "not-written.grib2"

    monkeypatch.setitem(sys.modules, "herbie", SimpleNamespace(Herbie=FakeHerbie))
    provider = HerbieHRRRProvider(network_enabled=True, forecast_hour=2, cache_dir=tmp_path)
    profile = provider.profile(39.0, -98.0, datetime(2026, 8, 3, 14, tzinfo=UTC))

    assert profile.altitude_m == (1000.0, 5000.0)
    assert profile.pressure_pa == (90000.0, 50000.0)
    assert profile.humidity_fraction == (0.6, 0.5)
    assert profile.source.model_cycle == datetime(2026, 8, 3, 12, tzinfo=UTC)
    assert profile.source.forecast_hour == 2
    assert profile.source.provider == "hrrr_via_herbie"
    assert profile.source.label == "REAL_MODEL_DATA_UNVALIDATED_FOR_OPERATIONAL_USE"


def test_gefs_adapter_intersects_variable_pressure_levels_before_merge() -> None:
    xr = pytest.importorskip("xarray")
    full_levels = [100, 150, 200, 250, 300]
    humidity_levels = [200, 250, 300]
    datasets = [
        xr.Dataset(
            {"gh": ("isobaricInhPa", [16_000, 14_000, 12_000, 10_000, 8_000])},
            coords={"isobaricInhPa": full_levels},
        ),
        xr.Dataset(
            {"t": ("isobaricInhPa", [220, 225, 230, 235, 240])},
            coords={"isobaricInhPa": full_levels},
        ),
        xr.Dataset(
            {"r": ("isobaricInhPa", [30, 40, 50])}, coords={"isobaricInhPa": humidity_levels}
        ),
    ]

    merged = HerbieGEFSProvider._merge_pressure_datasets(datasets, xr)

    assert merged.isobaricInhPa.values.tolist() == humidity_levels
    assert merged.gh.values.tolist() == [12_000, 10_000, 8_000]
    assert merged.t.values.tolist() == [230, 235, 240]
    assert merged.r.values.tolist() == [30, 40, 50]


def test_3dep_adapter_samples_a_route_leg(monkeypatch) -> None:
    monkeypatch.delenv("MACHLANE_NETWORK_DISABLED", raising=False)
    segment = route_from_waypoints([(39.0, -98.0), (39.0, -97.9)], spacing_m=100_000).segments[0]
    provider = USGS3DEPProvider(network_enabled=True, sample_spacing_m=2_000)

    def fake_request(url, *, data=None, headers=None) -> bytes:
        if "xsatendpts" in url:
            body = json.loads(data)
            count = int(next(item["value"] for item in body["inputs"] if item["id"] == "numpts"))
            return json.dumps(
                {
                    "features": [
                        {
                            "geometry": {"coordinates": [-98.0 + index * 0.01, 39.0]},
                            "properties": {"elevation": float(index)},
                        }
                        for index in range(count)
                    ]
                }
            ).encode()
        return json.dumps({"value": "9.5"}).encode()

    monkeypatch.setattr(provider, "_request", fake_request)
    profile = provider.profile(segment)

    assert len(profile.distance_m) == len(profile.elevation_m)
    assert profile.distance_m[0] == 0
    assert profile.distance_m[-1] == pytest.approx(segment.distance_m)
    assert profile.elevation_m[-1] == 9.5
    assert profile.source.checksum
    assert profile.source.label == "REAL_USGS_DATA_UNVALIDATED_FOR_OPERATIONAL_USE"


def test_3dep_route_point_preview_is_real_sparse_and_cacheable(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("MACHLANE_NETWORK_DISABLED", raising=False)
    segment = route_from_waypoints([(39.0, -98.0), (39.0, -97.9)], spacing_m=100_000).segments[0]
    provider = USGS3DEPProvider(
        network_enabled=True,
        sampling_mode="route_points",
        cache_dir=tmp_path,
    )
    calls: list[str] = []

    def fake_request(url, *, data=None, headers=None) -> bytes:
        del data, headers
        calls.append(url)
        return json.dumps({"value": "12.5", "resolution": 1}).encode()

    monkeypatch.setattr(provider, "_request", fake_request)

    profile = provider.profile(segment)
    cached = provider.profile(segment)

    assert len(profile.distance_m) == 3
    assert profile.elevation_m == (12.5, 12.5, 12.5)
    assert profile.source.label == "REAL_USGS_DATA_SPARSE_PREVIEW_NOT_FOR_PROPAGATION"
    assert profile.source.interpolation == "three EPQS route points; availability preview only"
    assert cached == profile
    assert len(calls) == 3


def test_era5_adapter_retrieves_cached_pressure_profile(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("MACHLANE_NETWORK_DISABLED", raising=False)

    class Variable:
        def __init__(self, values) -> None:
            self.values = np.asarray(values)

    class Point:
        variables = {
            "z": Variable(np.asarray([10_000, 5_000, 1_000]) * 9.80665),
            "t": Variable([220, 250, 280]),
            "u": Variable([30, 20, 10]),
            "v": Variable([5, 4, 3]),
            "r": Variable([40, 50, 60]),
        }
        coords = {"pressure_level": Variable([250, 500, 900])}

        def __contains__(self, name: str) -> bool:
            return name in self.variables

        def __getitem__(self, name: str):
            return self.variables[name]

        def squeeze(self):
            return self

    class Dataset(Point):
        def sel(self, **kwargs):
            assert kwargs["method"] == "nearest"
            return self

    class Client:
        def retrieve(self, dataset, request, target) -> None:
            assert dataset == "reanalysis-era5-pressure-levels"
            assert len(request["pressure_level"]) == 37
            Path(target).write_bytes(b"fake-era5-grib")

    monkeypatch.setitem(sys.modules, "cdsapi", SimpleNamespace(Client=Client))
    monkeypatch.setitem(
        sys.modules,
        "xarray",
        SimpleNamespace(open_dataset=lambda *args, **kwargs: Dataset()),
    )
    profile = ERA5Provider(network_enabled=True, cache_dir=tmp_path).profile(
        39.0, -98.0, datetime(2024, 3, 28, 0, tzinfo=UTC)
    )

    assert profile.altitude_m == pytest.approx((1000.0, 5000.0, 10000.0))
    assert profile.pressure_pa == (90000.0, 50000.0, 25000.0)
    assert profile.humidity_fraction == (0.6, 0.5, 0.4)
    assert profile.source.provider == "era5_via_cdsapi"
    assert profile.source.checksums


def test_live_adapters_honor_network_kill_switch(monkeypatch) -> None:
    monkeypatch.setenv("MACHLANE_NETWORK_DISABLED", "1")
    with pytest.raises(RuntimeError, match="MACHLANE_NETWORK_DISABLED"):
        HerbieHRRRProvider(network_enabled=True).profile(39, -98, datetime.now(UTC))
    with pytest.raises(RuntimeError, match="MACHLANE_NETWORK_DISABLED"):
        ERA5Provider(network_enabled=True).profile(39, -98, datetime.now(UTC))
    with pytest.raises(RuntimeError, match="MACHLANE_NETWORK_DISABLED"):
        USGS3DEPProvider(network_enabled=True).profile(demo_route().segments[0])
    mission = get_mission("dfw_jfk")
    with pytest.raises(RuntimeError, match="MACHLANE_NETWORK_DISABLED"):
        OpenSkyTrackProvider(
            network_enabled=True, client_id="client", client_secret="secret"
        ).route_for_airports(
            mission.origin,
            mission.destination,
            begin=datetime(2026, 8, 3, tzinfo=UTC),
            end=datetime(2026, 8, 4, tzinfo=UTC),
        )


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
