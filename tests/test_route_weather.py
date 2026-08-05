from datetime import UTC, datetime

import pytest

from open_mco.atmosphere import (
    SyntheticAtmosphereProvider,
    build_noaa_provider,
    build_time_aligned_noaa_provider,
    noaa_request_for_time,
)
from open_mco.models import AtmosphericSourceMetadata, RouteObservation, RouteSourceMetadata
from open_mco.route import (
    AUTOMATIC_WEATHER_SETTINGS,
    route_from_waypoints,
    segment_route_by_weather,
    weather_sample_times,
)


def observed_route(start: datetime, end: datetime):
    return route_from_waypoints(
        [(32.9, -97.0), (40.6, -73.8)],
        spacing_m=500_000,
        name="Observed fixture",
        source=RouteSourceMetadata(
            provider="opensky",
            data_kind="observed_track",
            retrieved_at=datetime(2026, 8, 4, tzinfo=UTC),
            label="TEST_OBSERVATION",
            observed_start=start,
            observed_end=end,
            point_count=2,
        ),
    )


def test_planned_provider_still_requires_explicit_network_authorization(tmp_path) -> None:
    plan = noaa_request_for_time(datetime(2026, 8, 3, 14, tzinfo=UTC), "HRRR")
    provider = build_noaa_provider(plan, cache_dir=tmp_path, network_enabled=False)

    with pytest.raises(RuntimeError, match="network access is disabled"):
        provider.profile(35, -95, plan.valid_time)


def test_noaa_request_matches_each_route_time_without_using_a_flight_midpoint() -> None:
    hrrr = noaa_request_for_time(datetime(2026, 8, 3, 14, 47, tzinfo=UTC), "HRRR")
    gefs = noaa_request_for_time(datetime(2026, 8, 3, 17, 47, tzinfo=UTC), "GEFS")

    assert hrrr.valid_time == datetime(2026, 8, 3, 14, tzinfo=UTC)
    assert hrrr.model_cycle == hrrr.valid_time
    assert hrrr.temporal_match == "preceding hourly HRRR analysis"
    assert gefs.valid_time == datetime(2026, 8, 3, 15, tzinfo=UTC)
    assert gefs.model_cycle == datetime(2026, 8, 3, 12, tzinfo=UTC)
    assert gefs.forecast_hour == 3


def test_weather_sampling_times_follow_real_observation_timestamps() -> None:
    observations = (
        RouteObservation(
            timestamp=datetime(2026, 8, 3, 10, tzinfo=UTC),
            latitude=32.9,
            longitude=-97.0,
            barometric_altitude_m=0,
            on_ground=True,
        ),
        RouteObservation(
            timestamp=datetime(2026, 8, 3, 11, tzinfo=UTC),
            latitude=35.0,
            longitude=-90.0,
            barometric_altitude_m=10_000,
            on_ground=False,
        ),
        RouteObservation(
            timestamp=datetime(2026, 8, 3, 13, tzinfo=UTC),
            latitude=40.6,
            longitude=-73.8,
            barometric_altitude_m=0,
            on_ground=True,
        ),
    )
    route = route_from_waypoints(
        [(item.latitude, item.longitude) for item in observations],
        spacing_m=500_000,
        name="Observed timestamp fixture",
        observations=observations,
        source=RouteSourceMetadata(
            provider="opensky",
            data_kind="observed_track",
            retrieved_at=datetime(2026, 8, 4, tzinfo=UTC),
            label="TEST_OBSERVATION",
            observed_start=observations[0].timestamp,
            observed_end=observations[-1].timestamp,
            point_count=3,
        ),
    )

    sample_times = weather_sample_times(route)

    assert sample_times == sorted(sample_times)
    assert observations[0].timestamp < sample_times[0] < observations[-1].timestamp
    assert len(set(time.hour for time in sample_times)) >= 2


def test_time_aligned_noaa_batches_points_by_model_valid_time(tmp_path, monkeypatch) -> None:
    route = observed_route(
        datetime(2026, 8, 3, 14, tzinfo=UTC),
        datetime(2026, 8, 3, 16, tzinfo=UTC),
    )
    calls: list[tuple[datetime, int]] = []

    class FakeHerbieProvider:
        def __init__(self, plan) -> None:
            self.plan = plan

        def profiles(self, points, valid_time):
            calls.append((valid_time, len(points)))
            values = []
            for latitude, longitude in points:
                base = SyntheticAtmosphereProvider().profile(latitude, longitude, valid_time)
                values.append(
                    base.model_copy(
                        update={
                            "source": AtmosphericSourceMetadata(
                                provider="hrrr_via_herbie",
                                model_cycle=self.plan.model_cycle,
                                forecast_hour=self.plan.forecast_hour,
                                valid_time=self.plan.valid_time,
                                variables=base.source.variables,
                                horizontal_interpolation="test",
                                vertical_interpolation="test",
                                retrieved_at=datetime(2026, 8, 4, tzinfo=UTC),
                            )
                        }
                    )
                )
            return tuple(values)

    def fake_builder(plan, **kwargs):
        del kwargs
        return FakeHerbieProvider(plan)

    monkeypatch.setattr("open_mco.atmosphere.route_weather.build_noaa_provider", fake_builder)
    provider = build_time_aligned_noaa_provider(
        route,
        "conus",
        cache_dir=tmp_path,
        network_enabled=True,
    )
    times = [
        datetime(2026, 8, 3, 14, 10, tzinfo=UTC),
        datetime(2026, 8, 3, 14, 50, tzinfo=UTC),
        datetime(2026, 8, 3, 15, 10, tzinfo=UTC),
    ]

    profiles = provider.profiles_at_times([(35, -95), (36, -94), (37, -93)], times)

    assert [profile.valid_time.hour for profile in profiles] == [14, 14, 15]
    assert sorted(calls) == [
        (datetime(2026, 8, 3, 14, tzinfo=UTC), 2),
        (datetime(2026, 8, 3, 15, tzinfo=UTC), 1),
    ]
    assert len(provider.plans_used) == 2


def test_automatic_regions_force_noaa_time_boundaries(tmp_path, monkeypatch) -> None:
    route = observed_route(
        datetime(2026, 8, 3, 14, tzinfo=UTC),
        datetime(2026, 8, 3, 16, tzinfo=UTC),
    )

    class FakeHerbieProvider:
        def __init__(self, plan) -> None:
            self.plan = plan

        def profiles(self, points, valid_time):
            values = []
            for latitude, longitude in points:
                base = SyntheticAtmosphereProvider().profile(latitude, longitude, valid_time)
                values.append(
                    base.model_copy(
                        update={
                            "source": AtmosphericSourceMetadata(
                                provider="hrrr_via_herbie",
                                model_cycle=self.plan.model_cycle,
                                forecast_hour=0,
                                valid_time=self.plan.valid_time,
                                variables=base.source.variables,
                                horizontal_interpolation="test",
                                vertical_interpolation="test",
                                retrieved_at=datetime(2026, 8, 4, tzinfo=UTC),
                            )
                        }
                    )
                )
            return tuple(values)

    monkeypatch.setattr(
        "open_mco.atmosphere.route_weather.build_noaa_provider",
        lambda plan, **kwargs: FakeHerbieProvider(plan),
    )
    provider = build_time_aligned_noaa_provider(
        route,
        "conus",
        cache_dir=tmp_path,
        network_enabled=True,
    )
    sample_times = [datetime(2026, 8, 3, 14, 30, tzinfo=UTC)] + [
        datetime(2026, 8, 3, 15, 30, tzinfo=UTC)
    ] * (len(route.segments) - 1)

    segmented, regimes = segment_route_by_weather(
        route,
        provider,
        settings=AUTOMATIC_WEATHER_SETTINGS,
        sample_times=sample_times,
    )

    assert len(segmented.segments) >= 2
    assert any("model cycle changed" in regime.boundary_reason.lower() for regime in regimes[1:])
    assert all(regime.policy_version == "automatic-atmospheric-regions-v1" for regime in regimes)
