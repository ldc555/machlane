from datetime import UTC, datetime

import pytest

from open_mco.atmosphere import build_noaa_provider, plan_noaa_atmosphere
from open_mco.models import RouteSourceMetadata
from open_mco.route import route_from_waypoints


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


def test_conus_route_uses_hrrr_at_observed_midpoint_hour() -> None:
    route = observed_route(
        datetime(2026, 8, 3, 14, 20, tzinfo=UTC),
        datetime(2026, 8, 3, 17, 10, tzinfo=UTC),
    )

    plan = plan_noaa_atmosphere(route, "conus")

    assert plan.model == "HRRR"
    assert plan.valid_time == datetime(2026, 8, 3, 15, tzinfo=UTC)
    assert plan.model_cycle == plan.valid_time
    assert plan.forecast_hour == 0
    assert plan.member is None


def test_oceanic_route_uses_gefs_cycle_and_lead_for_same_valid_hour() -> None:
    route = observed_route(
        datetime(2026, 8, 3, 8, 20, tzinfo=UTC),
        datetime(2026, 8, 3, 15, 10, tzinfo=UTC),
    )

    plan = plan_noaa_atmosphere(route, "global_oceanic")

    assert plan.model == "GEFS"
    assert plan.valid_time == datetime(2026, 8, 3, 11, tzinfo=UTC)
    assert plan.model_cycle == datetime(2026, 8, 3, 6, tzinfo=UTC)
    assert plan.forecast_hour == 5
    assert plan.model_cycle.hour + plan.forecast_hour == plan.valid_time.hour
    assert plan.member == 0


def test_planned_provider_still_requires_explicit_network_authorization(tmp_path) -> None:
    route = observed_route(
        datetime(2026, 8, 3, 14, tzinfo=UTC),
        datetime(2026, 8, 3, 16, tzinfo=UTC),
    )
    plan = plan_noaa_atmosphere(route, "conus")
    provider = build_noaa_provider(plan, cache_dir=tmp_path, network_enabled=False)

    with pytest.raises(RuntimeError, match="network access is disabled"):
        provider.profile(35, -95, plan.valid_time)
