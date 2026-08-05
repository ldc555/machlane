"""Route-aligned NOAA model selection without performing network access."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from open_mco.models import Route

from .providers import HerbieGEFSProvider, HerbieHRRRProvider

MissionDomain = Literal["conus", "us_oceanic", "global_oceanic"]


@dataclass(frozen=True)
class NOAAAtmospherePlan:
    """A deterministic NOAA request aligned to one observed OpenSky flight."""

    model: Literal["HRRR", "GEFS"]
    valid_time: datetime
    model_cycle: datetime
    forecast_hour: int
    member: int | None
    coverage: str

    @property
    def label(self) -> str:
        member = "" if self.member is None else f" · member {self.member}"
        return (
            f"{self.model} · {self.model_cycle:%Y-%m-%d %H:%M}Z + {self.forecast_hour:02d}h{member}"
        )


def observed_route_valid_time(route: Route) -> datetime:
    """Return the flight midpoint rounded down to the preceding whole UTC hour."""

    source = route.source
    if source is None or source.data_kind != "observed_track":
        raise ValueError("NOAA alignment requires an observed route")
    if source.observed_start is None or source.observed_end is None:
        raise ValueError("observed route has no UTC time window")
    start = source.observed_start.astimezone(UTC)
    end = source.observed_end.astimezone(UTC)
    if end < start:
        raise ValueError("observed route ends before it starts")
    midpoint = start + (end - start) / 2
    return midpoint.replace(minute=0, second=0, microsecond=0)


def plan_noaa_atmosphere(route: Route, domain: MissionDomain) -> NOAAAtmospherePlan:
    """Choose a coherent archived NOAA snapshot for an observed route.

    HRRR is used only for fully-CONUS missions. GEFS supplies the global grid for
    oceanic routes. The GEFS cycle is the most recent six-hour cycle at or before
    the route-valid hour, and its forecast lead restores that exact valid time.
    """

    valid_time = observed_route_valid_time(route)
    if domain == "conus":
        return NOAAAtmospherePlan(
            model="HRRR",
            valid_time=valid_time,
            model_cycle=valid_time,
            forecast_hour=0,
            member=None,
            coverage="CONUS regional grid",
        )
    cycle = valid_time.replace(hour=(valid_time.hour // 6) * 6)
    forecast_hour = int((valid_time - cycle) / timedelta(hours=1))
    return NOAAAtmospherePlan(
        model="GEFS",
        valid_time=valid_time,
        model_cycle=cycle,
        forecast_hour=forecast_hour,
        member=0,
        coverage="Global grid · control member",
    )


def build_noaa_provider(
    plan: NOAAAtmospherePlan,
    *,
    cache_dir: str | Path,
    network_enabled: bool,
) -> HerbieHRRRProvider | HerbieGEFSProvider:
    """Build the planned Herbie provider; callers explicitly authorize networking."""

    if plan.model == "HRRR":
        return HerbieHRRRProvider(
            network_enabled=network_enabled,
            forecast_hour=plan.forecast_hour,
            cache_dir=cache_dir,
        )
    return HerbieGEFSProvider(
        network_enabled=network_enabled,
        forecast_hour=plan.forecast_hour,
        cache_dir=cache_dir,
        member=plan.member,
    )
