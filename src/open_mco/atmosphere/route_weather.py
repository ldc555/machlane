"""Route-aligned NOAA model selection without performing network access."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from open_mco.models import AtmosphericProfile, Route

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
    temporal_match: str = "model valid time selected at or before route sample"

    @property
    def label(self) -> str:
        member = "" if self.member is None else f" · member {self.member}"
        return (
            f"{self.model} · {self.model_cycle:%Y-%m-%d %H:%M}Z + {self.forecast_hour:02d}h{member}"
        )


def noaa_request_for_time(
    sample_time: datetime,
    model: Literal["HRRR", "GEFS"],
) -> NOAAAtmospherePlan:
    """Resolve one route-sample timestamp to an archived NOAA model time.

    HRRR is matched to the preceding whole hour. GEFS is conservatively matched to the preceding
    three-hour output on its six-hour cycle. The exact OpenSky timestamp remains in route/weather
    evidence; this object records the model time actually sampled.
    """

    if sample_time.tzinfo is None:
        raise ValueError("NOAA route sample time must be timezone-aware")
    sample_utc = sample_time.astimezone(UTC)
    hour = sample_utc.replace(minute=0, second=0, microsecond=0)
    if model == "HRRR":
        return NOAAAtmospherePlan(
            model="HRRR",
            valid_time=hour,
            model_cycle=hour,
            forecast_hour=0,
            member=None,
            coverage="Reviewed MachLane v1 CONUS envelope",
            temporal_match="preceding hourly HRRR analysis",
        )
    valid_time = hour.replace(hour=(hour.hour // 3) * 3)
    cycle = valid_time.replace(hour=(valid_time.hour // 6) * 6)
    return NOAAAtmospherePlan(
        model="GEFS",
        valid_time=valid_time,
        model_cycle=cycle,
        forecast_hour=int((valid_time - cycle) / timedelta(hours=1)),
        member=0,
        coverage="Global grid · control member",
        temporal_match="preceding three-hour GEFS output",
    )


def _inside_reviewed_hrrr_envelope(latitude: float, longitude: float) -> bool:
    """Return whether a point is inside MachLane's conservative v1 HRRR envelope."""

    return 24.0 <= latitude <= 50.0 and -125.0 <= longitude <= -66.0


def _route_model(route: Route, domain: MissionDomain) -> Literal["HRRR", "GEFS"]:
    fully_inside = all(
        _inside_reviewed_hrrr_envelope(latitude, longitude)
        for latitude, longitude in route.waypoints
    )
    return "HRRR" if domain == "conus" and fully_inside else "GEFS"


class TimeAlignedNOAAProvider:
    """Batch route columns by archived model cycle and valid time."""

    name = "time_aligned_noaa"

    def __init__(
        self,
        route: Route,
        domain: MissionDomain,
        *,
        cache_dir: str | Path,
        network_enabled: bool,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> None:
        if route.source is None or route.source.data_kind != "observed_track":
            raise ValueError("time-aligned NOAA requires an observed OpenSky route")
        self.model = _route_model(route, domain)
        self.coverage = (
            "Reviewed MachLane v1 CONUS envelope"
            if self.model == "HRRR"
            else "Global grid · control member"
        )
        self.cache_dir = Path(cache_dir)
        self.network_enabled = network_enabled
        self.progress_callback = progress_callback
        self._providers: dict[NOAAAtmospherePlan, HerbieHRRRProvider | HerbieGEFSProvider] = {}
        self._plans_used: set[NOAAAtmospherePlan] = set()

    @property
    def plans_used(self) -> tuple[NOAAAtmospherePlan, ...]:
        return tuple(
            sorted(
                self._plans_used,
                key=lambda plan: (plan.valid_time, plan.model_cycle, plan.forecast_hour),
            )
        )

    def _provider(self, plan: NOAAAtmospherePlan) -> HerbieHRRRProvider | HerbieGEFSProvider:
        provider = self._providers.get(plan)
        if provider is None:
            provider = build_noaa_provider(
                plan,
                cache_dir=self.cache_dir,
                network_enabled=self.network_enabled,
            )
            self._providers[plan] = provider
        self._plans_used.add(plan)
        return provider

    def profiles_at_times(
        self,
        points: list[tuple[float, float]],
        sample_times: list[datetime],
    ) -> tuple[AtmosphericProfile, ...]:
        if len(points) != len(sample_times):
            raise ValueError("NOAA points and sample times must have equal lengths")
        grouped: dict[NOAAAtmospherePlan, list[int]] = {}
        for index, sample_time in enumerate(sample_times):
            plan = noaa_request_for_time(sample_time, self.model)
            grouped.setdefault(plan, []).append(index)
        output: list[AtmosphericProfile | None] = [None] * len(points)
        total_groups = len(grouped)
        for completed_groups, (plan, indices) in enumerate(grouped.items(), start=1):
            selected_points = [points[index] for index in indices]
            profiles = self._provider(plan).profiles(selected_points, plan.valid_time)
            for index, profile in zip(indices, profiles, strict=True):
                output[index] = profile
            if self.progress_callback is not None:
                self.progress_callback(
                    completed_groups,
                    total_groups,
                    f"NOAA {plan.model} {plan.valid_time:%H:%M UTC}",
                )
        if any(profile is None for profile in output):
            raise RuntimeError("NOAA returned an incomplete route-time atmosphere")
        return tuple(profile for profile in output if profile is not None)

    def profiles(
        self,
        points: list[tuple[float, float]],
        valid_time: datetime,
    ) -> tuple[AtmosphericProfile, ...]:
        return self.profiles_at_times(points, [valid_time] * len(points))

    def profile(
        self,
        latitude: float,
        longitude: float,
        valid_time: datetime,
    ) -> AtmosphericProfile:
        return self.profiles_at_times([(latitude, longitude)], [valid_time])[0]


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


def build_time_aligned_noaa_provider(
    route: Route,
    domain: MissionDomain,
    *,
    cache_dir: str | Path,
    network_enabled: bool,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> TimeAlignedNOAAProvider:
    """Build the NOAA coordinator used by the production mission workspace."""

    return TimeAlignedNOAAProvider(
        route,
        domain,
        cache_dir=cache_dir,
        network_enabled=network_enabled,
        progress_callback=progress_callback,
    )
