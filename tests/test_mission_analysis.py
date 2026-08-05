from __future__ import annotations

from datetime import UTC, datetime

import pytest

from open_mco.atmosphere import SyntheticAtmosphereProvider, noaa_request_for_time
from open_mco.mission_analysis import build_real_mission_analysis
from open_mco.models import (
    AtmosphericSourceMetadata,
    RouteObservation,
    RouteSourceMetadata,
    TerrainProfile,
    TerrainSourceMetadata,
)
from open_mco.route import route_from_waypoints


def _observed_route(*, international: bool = False, with_timeline: bool = True):
    coordinates = (
        ((51.47, -0.46), (50.8, 1.0), (49.0, 2.55))
        if international
        else ((32.9, -97.0), (33.2, -96.0), (33.5, -95.0))
    )
    timestamps = (
        datetime(2026, 8, 3, 14, 10, tzinfo=UTC),
        datetime(2026, 8, 3, 15, 5, tzinfo=UTC),
        datetime(2026, 8, 3, 16, 10, tzinfo=UTC),
    )
    observations = (
        tuple(
            RouteObservation(
                timestamp=timestamp,
                latitude=latitude,
                longitude=longitude,
                barometric_altitude_m=altitude,
                on_ground=index in {0, 2},
            )
            for index, ((latitude, longitude), timestamp, altitude) in enumerate(
                zip(coordinates, timestamps, (0.0, 10_000.0, 0.0), strict=True)
            )
        )
        if with_timeline
        else ()
    )
    return route_from_waypoints(
        coordinates,
        spacing_m=50_000,
        name="OpenSky integration fixture",
        observations=observations,
        source=RouteSourceMetadata(
            provider="opensky",
            data_kind="observed_track",
            retrieved_at=datetime(2026, 8, 4, tzinfo=UTC),
            label="TEST_OPEN_SKY",
            observed_start=timestamps[0] if with_timeline else None,
            observed_end=timestamps[-1] if with_timeline else None,
            point_count=3,
        ),
    )


class _FakeTimeAlignedNOAA:
    model = "HRRR"
    coverage = "test reviewed coverage"

    def __init__(self) -> None:
        self._plans = set()

    @property
    def plans_used(self):
        return tuple(sorted(self._plans, key=lambda plan: plan.valid_time))

    def profiles_at_times(self, points, sample_times):
        profiles = []
        for (latitude, longitude), sample_time in zip(points, sample_times, strict=True):
            plan = noaa_request_for_time(sample_time, "HRRR")
            self._plans.add(plan)
            base = SyntheticAtmosphereProvider().profile(latitude, longitude, plan.valid_time)
            profiles.append(
                base.model_copy(
                    update={
                        "source": AtmosphericSourceMetadata(
                            provider="hrrr_via_herbie",
                            model_cycle=plan.model_cycle,
                            forecast_hour=plan.forecast_hour,
                            valid_time=plan.valid_time,
                            variables=base.source.variables,
                            horizontal_interpolation="nearest test grid",
                            vertical_interpolation="native test levels",
                            retrieved_at=datetime(2026, 8, 4, tzinfo=UTC),
                            source_url="https://noaa.example/test",
                            checksums={"test.grib2": "abc"},
                            label="REAL_MODEL_TEST_FIXTURE",
                        )
                    }
                )
            )
        return tuple(profiles)


class _FakeUSGS3DEP:
    fail_longitude: float | None = None
    calls = 0

    def __init__(self, **kwargs) -> None:
        del kwargs

    def profile(self, segment):
        type(self).calls += 1
        if self.fail_longitude is not None and segment.start_longitude < self.fail_longitude:
            raise RuntimeError("test 3DEP outage")
        return TerrainProfile(
            distance_m=(0.0, segment.distance_m / 2, segment.distance_m),
            elevation_m=(100.0, 110.0, 105.0),
            latitude=(
                segment.start_latitude,
                (segment.start_latitude + segment.end_latitude) / 2,
                segment.end_latitude,
            ),
            longitude=(
                segment.start_longitude,
                (segment.start_longitude + segment.end_longitude) / 2,
                segment.end_longitude,
            ),
            source=TerrainSourceMetadata(
                provider="usgs_3dep",
                resolution_m=1,
                horizontal_datum="WGS84",
                vertical_datum="test datum",
                interpolation="three EPQS route points; availability preview only",
                retrieved_at=datetime(2026, 8, 4, tzinfo=UTC),
                source_url="https://usgs.example/test",
                checksum="terrain-checksum",
                label="REAL_USGS_DATA_SPARSE_PREVIEW_NOT_FOR_PROPAGATION",
            ),
        )


def _patch_real_sources(monkeypatch, *, fail_longitude: float | None = None) -> None:
    fake_noaa = _FakeTimeAlignedNOAA()
    _FakeUSGS3DEP.fail_longitude = fail_longitude
    _FakeUSGS3DEP.calls = 0
    monkeypatch.setattr(
        "open_mco.mission_analysis.build_time_aligned_noaa_provider",
        lambda *args, **kwargs: fake_noaa,
    )
    monkeypatch.setattr("open_mco.mission_analysis.USGS3DEPProvider", _FakeUSGS3DEP)


def test_real_mission_coordinator_aligns_times_and_loads_terrain(tmp_path, monkeypatch) -> None:
    _patch_real_sources(monkeypatch)

    analysis = build_real_mission_analysis(
        _observed_route(),
        "conus",
        weather_cache_dir=tmp_path / "weather",
        terrain_cache_dir=tmp_path / "terrain",
    )

    assert analysis.noaa_model == "HRRR"
    assert len(analysis.noaa_requests) >= 2
    assert len(analysis.atmospheric_route.segments) == len(analysis.weather_regimes)
    assert len(analysis.segment_atmospheres) == len(analysis.weather_regimes)
    assert {item.status for item in analysis.terrain_regions} == {"LOADED"}
    assert all(
        item.reason == "Real USGS 3DEP sparse availability preview loaded"
        for item in analysis.terrain_regions
    )
    assert all(item.policy_version == analysis.policy_version for item in analysis.weather_regimes)


def test_real_mission_coordinator_records_terrain_outage_without_fallback(
    tmp_path, monkeypatch
) -> None:
    _patch_real_sources(monkeypatch, fail_longitude=-96.5)

    analysis = build_real_mission_analysis(
        _observed_route(),
        "conus",
        weather_cache_dir=tmp_path / "weather",
        terrain_cache_dir=tmp_path / "terrain",
    )

    assert "UNAVAILABLE" in {item.status for item in analysis.terrain_regions}
    assert any(item.reason == "test 3DEP outage" for item in analysis.terrain_regions)
    assert all(
        item.profile is None for item in analysis.terrain_regions if item.status == "UNAVAILABLE"
    )


def test_real_mission_skips_3dep_outside_us_coverage(tmp_path, monkeypatch) -> None:
    _patch_real_sources(monkeypatch)

    analysis = build_real_mission_analysis(
        _observed_route(international=True),
        "global_oceanic",
        weather_cache_dir=tmp_path / "weather",
        terrain_cache_dir=tmp_path / "terrain",
    )

    assert {item.status for item in analysis.terrain_regions} == {"OUTSIDE_REVIEWED_COVERAGE"}
    assert _FakeUSGS3DEP.calls == 0


def test_real_mission_rejects_non_opensky_or_untimed_routes(tmp_path) -> None:
    conceptual = route_from_waypoints(
        [(32.9, -97.0), (33.5, -95.0)],
        spacing_m=50_000,
        name="concept route",
    )
    with pytest.raises(ValueError, match="OpenSky observed track"):
        build_real_mission_analysis(
            conceptual,
            "conus",
            weather_cache_dir=tmp_path / "weather",
            terrain_cache_dir=tmp_path / "terrain",
        )
    with pytest.raises(ValueError, match="UTC observation timeline"):
        build_real_mission_analysis(
            _observed_route(with_timeline=False),
            "conus",
            weather_cache_dir=tmp_path / "weather",
            terrain_cache_dir=tmp_path / "terrain",
        )
