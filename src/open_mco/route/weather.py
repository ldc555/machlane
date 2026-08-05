"""Weather-regime segmentation for route-level planning."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from pyproj import Geod

from open_mco.atmosphere import AtmosphereProvider, profiles_at_spacetime
from open_mco.models import AtmosphericProfile, Route, RouteSegment

_GEOD = Geod(ellps="WGS84")

AUTOMATIC_WEATHER_POLICY_VERSION = "automatic-atmospheric-regions-v1"
AUTOMATIC_WEATHER_SAMPLE_SPACING_M = 15 * 1609.344


@dataclass(frozen=True)
class WeatherSegmentationSettings:
    """Thresholds that define when adjacent atmosphere samples stop being uniform."""

    altitude_m: float = 15_240
    temperature_change_k: float = 0.7
    pressure_change_hpa: float = 1.0
    wind_vector_change_mps: float = 2.5
    minimum_samples_per_segment: int = 2
    policy_version: str = "custom"

    def __post_init__(self) -> None:
        values = (
            self.altitude_m,
            self.temperature_change_k,
            self.pressure_change_hpa,
            self.wind_vector_change_mps,
        )
        if any(value <= 0 for value in values):
            raise ValueError("weather segmentation thresholds must be positive")
        if self.minimum_samples_per_segment < 1:
            raise ValueError("minimum samples per weather segment must be positive")


AUTOMATIC_WEATHER_SETTINGS = WeatherSegmentationSettings(
    altitude_m=15_240.0,
    temperature_change_k=5 / 9,
    pressure_change_hpa=0.02 * 33.86389,
    wind_vector_change_mps=3 / 1.943844492,
    minimum_samples_per_segment=1,
    policy_version=AUTOMATIC_WEATHER_POLICY_VERSION,
)


@dataclass(frozen=True)
class WeatherRegimeSummary:
    """Atmospheric summary and boundary rationale for one output route segment."""

    segment_id: str
    boundary_reason: str
    sample_count: int
    temperature_k: float
    pressure_hpa: float
    zonal_wind_mps: float
    meridional_wind_mps: float
    sample_start_time: datetime
    sample_end_time: datetime
    model_valid_start: datetime
    model_valid_end: datetime
    model_cycles: tuple[datetime, ...]
    providers: tuple[str, ...]
    policy_version: str

    @property
    def wind_speed_mps(self) -> float:
        return math.hypot(self.zonal_wind_mps, self.meridional_wind_mps)


@dataclass(frozen=True)
class _WeatherSample:
    segment: RouteSegment
    temperature_k: float
    pressure_hpa: float
    zonal_wind_mps: float
    meridional_wind_mps: float
    sample_time: datetime
    profile: AtmosphericProfile


def observation_time_at_progress(route: Route, progress: float) -> datetime:
    """Interpolate UTC time through every retained OpenSky observation.

    Distance, rather than observation index, determines interpolation so reception gaps do not
    make the aircraft appear to spend equal time on unequal legs.
    """

    bounded = min(1.0, max(0.0, progress))
    observations = route.observations
    if len(observations) >= 2:
        if any(
            following.timestamp < current.timestamp
            for current, following in zip(observations, observations[1:], strict=False)
        ):
            raise ValueError("OpenSky observations are not ordered by UTC timestamp")
        leg_distances: list[float] = []
        for current, following in zip(observations, observations[1:], strict=False):
            _, _, distance = _GEOD.inv(
                current.longitude,
                current.latitude,
                following.longitude,
                following.latitude,
            )
            leg_distances.append(max(0.0, distance))
        total = sum(leg_distances)
        if total > 0:
            target = total * bounded
            elapsed = 0.0
            for current, following, distance in zip(
                observations,
                observations[1:],
                leg_distances,
                strict=True,
            ):
                if elapsed + distance >= target:
                    fraction = 0.0 if distance == 0 else (target - elapsed) / distance
                    return current.timestamp + (following.timestamp - current.timestamp) * fraction
                elapsed += distance
            return observations[-1].timestamp
    source = route.source
    if source and source.observed_start and source.observed_end:
        return source.observed_start + (source.observed_end - source.observed_start) * bounded
    raise ValueError("observed route has no usable UTC observation timeline")


def weather_sample_times(route: Route) -> list[datetime]:
    """Return exact route-timeline times at every weather-cell midpoint."""

    total = sum(segment.distance_m for segment in route.segments)
    if total <= 0:
        raise ValueError("weather route distance must be positive")
    elapsed = 0.0
    sample_times: list[datetime] = []
    for segment in route.segments:
        midpoint_progress = (elapsed + segment.distance_m / 2) / total
        sample_times.append(observation_time_at_progress(route, midpoint_progress))
        elapsed += segment.distance_m
    return sample_times


def coarsen_route_for_weather(route: Route, sample_spacing_m: float) -> Route:
    """Group dense observed-track legs into sampling cells while retaining every bend."""

    if sample_spacing_m <= 0:
        raise ValueError("weather sample spacing must be positive")
    groups: list[list[RouteSegment]] = []
    active: list[RouteSegment] = []
    active_distance = 0.0
    for segment in route.segments:
        if active and active_distance + segment.distance_m > sample_spacing_m:
            groups.append(active)
            active = []
            active_distance = 0.0
        active.append(segment)
        active_distance += segment.distance_m
    if active:
        groups.append(active)

    def retained_path(group: list[RouteSegment]) -> tuple[tuple[float, float], ...]:
        points: list[tuple[float, float]] = []
        for segment in group:
            segment_points = segment.path or (
                (segment.start_latitude, segment.start_longitude),
                (segment.end_latitude, segment.end_longitude),
            )
            points.extend(segment_points if not points else segment_points[1:])
        return tuple(points)

    cells = tuple(
        RouteSegment(
            segment_id=f"W{index + 1:04d}",
            start_latitude=group[0].start_latitude,
            start_longitude=group[0].start_longitude,
            end_latitude=group[-1].end_latitude,
            end_longitude=group[-1].end_longitude,
            distance_m=sum(segment.distance_m for segment in group),
            bearing_deg=group[0].bearing_deg,
            path=retained_path(group),
        )
        for index, group in enumerate(groups)
    )
    return Route(
        name=f"{route.name} · weather sampling cells",
        waypoints=route.waypoints,
        segments=cells,
        source=route.source,
        observations=route.observations,
    )


def _sample_segment(
    segment: RouteSegment,
    profile: AtmosphericProfile,
    altitude_m: float,
    sample_time: datetime,
) -> _WeatherSample:
    return _WeatherSample(
        segment=segment,
        temperature_k=float(np.interp(altitude_m, profile.altitude_m, profile.temperature_k)),
        pressure_hpa=float(np.interp(altitude_m, profile.altitude_m, profile.pressure_pa)) / 100,
        zonal_wind_mps=float(np.interp(altitude_m, profile.altitude_m, profile.zonal_wind_mps)),
        meridional_wind_mps=float(
            np.interp(altitude_m, profile.altitude_m, profile.meridional_wind_mps)
        ),
        sample_time=sample_time,
        profile=profile,
    )


def _mean(samples: list[_WeatherSample], attribute: str) -> float:
    return sum(float(getattr(sample, attribute)) for sample in samples) / len(samples)


def _boundary_reason(
    sample: _WeatherSample,
    regime: list[_WeatherSample],
    settings: WeatherSegmentationSettings,
) -> tuple[str | None, bool]:
    reference = regime[-1].profile.source
    current = sample.profile.source
    if current.provider != reference.provider:
        return (
            f"Weather-model coverage changed from {reference.provider} to {current.provider}",
            True,
        )
    if current.model_cycle != reference.model_cycle:
        previous_cycle = "unknown" if reference.model_cycle is None else reference.model_cycle.isoformat()
        next_cycle = "unknown" if current.model_cycle is None else current.model_cycle.isoformat()
        return (f"Weather-model cycle changed from {previous_cycle} to {next_cycle}", True)
    if current.valid_time != reference.valid_time:
        return (
            "Weather-model valid time changed from "
            f"{reference.valid_time.isoformat()} to {current.valid_time.isoformat()}",
            True,
        )
    temperature_delta = abs(sample.temperature_k - _mean(regime, "temperature_k"))
    pressure_delta = abs(sample.pressure_hpa - _mean(regime, "pressure_hpa"))
    zonal_delta = sample.zonal_wind_mps - _mean(regime, "zonal_wind_mps")
    meridional_delta = sample.meridional_wind_mps - _mean(regime, "meridional_wind_mps")
    wind_delta = math.hypot(zonal_delta, meridional_delta)
    triggers = [
        (
            temperature_delta / settings.temperature_change_k,
            f"Temperature regime changed by {temperature_delta:.1f} K",
        ),
        (
            pressure_delta / settings.pressure_change_hpa,
            f"Pressure regime changed by {pressure_delta:.1f} hPa",
        ),
        (
            wind_delta / settings.wind_vector_change_mps,
            f"Wind vector changed by {wind_delta:.1f} m/s",
        ),
    ]
    severity, reason = max(triggers, key=lambda item: item[0])
    return (reason if severity > 1 else None, False)


def segment_route_by_weather(
    sampled_route: Route,
    provider: AtmosphereProvider,
    valid_time: datetime | None = None,
    *,
    settings: WeatherSegmentationSettings | None = None,
    sample_times: list[datetime] | None = None,
) -> tuple[Route, tuple[WeatherRegimeSummary, ...]]:
    """Merge fine geodesic samples while atmosphere characteristics remain uniform."""

    active_settings = settings or WeatherSegmentationSettings()
    sample_points = []
    for segment in sampled_route.segments:
        longitude, latitude, _ = _GEOD.fwd(
            segment.start_longitude,
            segment.start_latitude,
            segment.bearing_deg,
            segment.distance_m / 2,
        )
        sample_points.append((latitude, longitude))
    if sample_times is None:
        if valid_time is None:
            raise ValueError("weather segmentation requires a valid time or route sample times")
        sample_times = [valid_time] * len(sample_points)
    if len(sample_times) != len(sample_points):
        raise ValueError("weather sample times must align with route sampling cells")
    profiles = profiles_at_spacetime(provider, sample_points, sample_times)
    samples = [
        _sample_segment(
            segment,
            profile,
            active_settings.altitude_m,
            sample_time,
        )
        for segment, profile, sample_time in zip(
            sampled_route.segments, profiles, sample_times, strict=True
        )
    ]
    grouped_samples: list[list[_WeatherSample]] = []
    boundary_reasons = ["Departure / initial weather regime"]
    regime = [samples[0]]

    for sample in samples[1:]:
        reason, forced = _boundary_reason(sample, regime, active_settings)
        if reason is not None and (
            forced or len(regime) >= active_settings.minimum_samples_per_segment
        ):
            grouped_samples.append(regime)
            boundary_reasons.append(reason)
            regime = [sample]
        else:
            regime.append(sample)
    if len(regime) < active_settings.minimum_samples_per_segment and grouped_samples:
        grouped_samples[-1].extend(regime)
        boundary_reasons.pop()
    else:
        grouped_samples.append(regime)
    def retained_path(group: list[_WeatherSample]) -> tuple[tuple[float, float], ...]:
        points: list[tuple[float, float]] = []
        for sample in group:
            path = sample.segment.path or (
                (sample.segment.start_latitude, sample.segment.start_longitude),
                (sample.segment.end_latitude, sample.segment.end_longitude),
            )
            points.extend(path if not points else path[1:])
        return tuple(points)

    segments = tuple(
        RouteSegment(
            segment_id=f"S{index + 1:04d}",
            start_latitude=group[0].segment.start_latitude,
            start_longitude=group[0].segment.start_longitude,
            end_latitude=group[-1].segment.end_latitude,
            end_longitude=group[-1].segment.end_longitude,
            distance_m=sum(sample.segment.distance_m for sample in group),
            bearing_deg=group[0].segment.bearing_deg,
            path=retained_path(group),
        )
        for index, group in enumerate(grouped_samples)
    )
    route = Route(
        name=f"{sampled_route.name} · weather-regime segmentation",
        waypoints=sampled_route.waypoints,
        segments=segments,
        source=sampled_route.source,
        observations=sampled_route.observations,
    )
    summaries = tuple(
        WeatherRegimeSummary(
            segment_id=segment.segment_id,
            boundary_reason=boundary_reasons[index],
            sample_count=len(group),
            temperature_k=_mean(group, "temperature_k"),
            pressure_hpa=_mean(group, "pressure_hpa"),
            zonal_wind_mps=_mean(group, "zonal_wind_mps"),
            meridional_wind_mps=_mean(group, "meridional_wind_mps"),
            sample_start_time=group[0].sample_time,
            sample_end_time=group[-1].sample_time,
            model_valid_start=min(sample.profile.source.valid_time for sample in group),
            model_valid_end=max(sample.profile.source.valid_time for sample in group),
            model_cycles=tuple(
                sorted(
                    {
                        sample.profile.source.model_cycle
                        for sample in group
                        if sample.profile.source.model_cycle is not None
                    }
                )
            ),
            providers=tuple(sorted({sample.profile.source.provider for sample in group})),
            policy_version=active_settings.policy_version,
        )
        for index, (segment, group) in enumerate(zip(route.segments, grouped_samples, strict=True))
    )
    return route, summaries
