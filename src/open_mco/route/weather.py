"""Weather-regime segmentation for route-level planning."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from pyproj import Geod

from open_mco.atmosphere import AtmosphereProvider
from open_mco.models import Route, RouteSegment

from .geometry import route_from_waypoints

_GEOD = Geod(ellps="WGS84")


@dataclass(frozen=True)
class WeatherSegmentationSettings:
    """Thresholds that define when adjacent atmosphere samples stop being uniform."""

    altitude_m: float = 15_240
    temperature_change_k: float = 0.7
    pressure_change_hpa: float = 1.0
    wind_vector_change_mps: float = 2.5
    minimum_samples_per_segment: int = 2

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


def _sample_segment(
    segment: RouteSegment,
    provider: AtmosphereProvider,
    valid_time: datetime,
    altitude_m: float,
) -> _WeatherSample:
    longitude, latitude, _ = _GEOD.fwd(
        segment.start_longitude,
        segment.start_latitude,
        segment.bearing_deg,
        segment.distance_m / 2,
    )
    profile = provider.profile(latitude, longitude, valid_time)
    return _WeatherSample(
        segment=segment,
        temperature_k=float(np.interp(altitude_m, profile.altitude_m, profile.temperature_k)),
        pressure_hpa=float(np.interp(altitude_m, profile.altitude_m, profile.pressure_pa)) / 100,
        zonal_wind_mps=float(np.interp(altitude_m, profile.altitude_m, profile.zonal_wind_mps)),
        meridional_wind_mps=float(
            np.interp(altitude_m, profile.altitude_m, profile.meridional_wind_mps)
        ),
    )


def _mean(samples: list[_WeatherSample], attribute: str) -> float:
    return sum(float(getattr(sample, attribute)) for sample in samples) / len(samples)


def _boundary_reason(
    sample: _WeatherSample,
    regime: list[_WeatherSample],
    settings: WeatherSegmentationSettings,
) -> str | None:
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
    return reason if severity > 1 else None


def segment_route_by_weather(
    sampled_route: Route,
    provider: AtmosphereProvider,
    valid_time: datetime,
    *,
    settings: WeatherSegmentationSettings | None = None,
) -> tuple[Route, tuple[WeatherRegimeSummary, ...]]:
    """Merge fine geodesic samples while atmosphere characteristics remain uniform."""

    active_settings = settings or WeatherSegmentationSettings()
    samples = [
        _sample_segment(
            segment,
            provider,
            valid_time,
            active_settings.altitude_m,
        )
        for segment in sampled_route.segments
    ]
    boundaries = [sampled_route.waypoints[0]]
    grouped_samples: list[list[_WeatherSample]] = []
    boundary_reasons = ["Departure / initial weather regime"]
    regime = [samples[0]]

    for sample in samples[1:]:
        reason = _boundary_reason(sample, regime, active_settings)
        if reason is not None and len(regime) >= active_settings.minimum_samples_per_segment:
            grouped_samples.append(regime)
            boundaries.append((sample.segment.start_latitude, sample.segment.start_longitude))
            boundary_reasons.append(reason)
            regime = [sample]
        else:
            regime.append(sample)
    if len(regime) < active_settings.minimum_samples_per_segment and grouped_samples:
        grouped_samples[-1].extend(regime)
        boundaries.pop()
        boundary_reasons.pop()
    else:
        grouped_samples.append(regime)
    boundaries.append(sampled_route.waypoints[-1])

    route = route_from_waypoints(
        boundaries,
        spacing_m=50_000_000,
        name=f"{sampled_route.name} · weather-regime segmentation",
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
        )
        for index, (segment, group) in enumerate(zip(route.segments, grouped_samples, strict=True))
    )
    return route, summaries
