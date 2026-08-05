"""Fail-closed real-data mission analysis used by the production workspace."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from open_mco.atmosphere import (
    NOAAAtmospherePlan,
    build_time_aligned_noaa_provider,
    profiles_at_spacetime,
)
from open_mco.models import AtmosphericProfile, Route, RouteSegment, TerrainProfile
from open_mco.route import (
    AUTOMATIC_WEATHER_SAMPLE_SPACING_M,
    AUTOMATIC_WEATHER_SETTINGS,
    WeatherRegimeSummary,
    coarsen_route_for_weather,
    interpolate_segment_position,
    observation_time_at_progress,
    route_from_waypoints,
    segment_route_by_weather,
    weather_sample_times,
)
from open_mco.terrain import USGS3DEPProvider


@dataclass(frozen=True)
class TerrainRegionResult:
    """Terrain availability for one atmospheric region."""

    segment_id: str
    status: Literal["LOADED", "OUTSIDE_REVIEWED_COVERAGE", "UNAVAILABLE"]
    profile: TerrainProfile | None
    reason: str


@dataclass(frozen=True)
class RealMissionAnalysis:
    """Normalized real inputs; deliberately contains no synthetic planner result."""

    observed_route: Route
    atmospheric_route: Route
    weather_regimes: tuple[WeatherRegimeSummary, ...]
    segment_atmospheres: tuple[AtmosphericProfile, ...]
    noaa_model: Literal["HRRR", "GEFS"]
    noaa_coverage: str
    noaa_requests: tuple[NOAAAtmospherePlan, ...]
    terrain_regions: tuple[TerrainRegionResult, ...]
    policy_version: str


def _inside_us_territory_envelope(latitude: float, longitude: float) -> bool:
    """Conservative request guard for areas where 3DEP may have U.S. land coverage."""

    envelopes = (
        (24.0, 50.0, -125.0, -66.0),  # CONUS
        (51.0, 72.0, -180.0, -129.0),  # Alaska
        (18.5, 22.5, -161.5, -154.0),  # Hawaii
        (17.5, 18.7, -67.5, -64.3),  # Puerto Rico and U.S. Virgin Islands
        (13.0, 14.0, 144.0, 145.0),  # Guam
    )
    return any(
        south <= latitude <= north and west <= longitude <= east
        for south, north, west, east in envelopes
    )


def _segment_may_have_3dep(segment: RouteSegment) -> bool:
    path = segment.path or (
        (segment.start_latitude, segment.start_longitude),
        (segment.end_latitude, segment.end_longitude),
    )
    midpoint = interpolate_segment_position(segment)
    return any(_inside_us_territory_envelope(*point) for point in (*path, midpoint))


def _region_sample_times(route: Route) -> list[datetime]:
    total = sum(segment.distance_m for segment in route.segments)
    if total <= 0:
        raise ValueError("atmospheric region route distance must be positive")
    elapsed = 0.0
    values: list[datetime] = []
    for segment in route.segments:
        values.append(observation_time_at_progress(route, (elapsed + segment.distance_m / 2) / total))
        elapsed += segment.distance_m
    return values


def build_real_mission_analysis(
    observed_route: Route,
    domain: Literal["conus", "us_oceanic", "global_oceanic"],
    *,
    weather_cache_dir: str | Path,
    terrain_cache_dir: str | Path,
    network_enabled: bool = True,
) -> RealMissionAnalysis:
    """Load timestamp-aligned NOAA and available 3DEP data for one OpenSky track.

    OpenSky and NOAA are hard requirements. Terrain is attempted only inside a conservative U.S.
    coverage guard and retains an explicit unavailable state for every region that cannot be
    sampled. No atmosphere, route, terrain, aircraft, or propagation fallback is created.
    """

    source = observed_route.source
    if source is None or source.provider != "opensky" or source.data_kind != "observed_track":
        raise ValueError("real mission analysis requires a normalized OpenSky observed track")
    if not observed_route.observations and not (source.observed_start and source.observed_end):
        raise ValueError("OpenSky route is missing its UTC observation timeline")

    densified = route_from_waypoints(
        observed_route.waypoints,
        spacing_m=AUTOMATIC_WEATHER_SAMPLE_SPACING_M,
        name=observed_route.name,
        source=source,
        observations=observed_route.observations,
    )
    sampled_route = coarsen_route_for_weather(
        densified,
        AUTOMATIC_WEATHER_SAMPLE_SPACING_M,
    )
    route_times = weather_sample_times(sampled_route)
    noaa = build_time_aligned_noaa_provider(
        observed_route,
        domain,
        cache_dir=weather_cache_dir,
        network_enabled=network_enabled,
    )
    atmospheric_route, regimes = segment_route_by_weather(
        sampled_route,
        noaa,
        settings=AUTOMATIC_WEATHER_SETTINGS,
        sample_times=route_times,
    )

    region_points = [interpolate_segment_position(segment) for segment in atmospheric_route.segments]
    region_times = _region_sample_times(atmospheric_route)
    segment_atmospheres = profiles_at_spacetime(noaa, region_points, region_times)

    terrain_provider = USGS3DEPProvider(
        network_enabled=network_enabled,
        resolution_m=10.0,
        sample_spacing_m=1_000.0,
        cache_dir=terrain_cache_dir,
        sampling_mode="route_points",
        timeout_seconds=15.0,
    )
    terrain_regions: list[TerrainRegionResult | None] = [None] * len(atmospheric_route.segments)
    terrain_requests: dict[Future[TerrainProfile], tuple[int, RouteSegment]] = {}
    with ThreadPoolExecutor(max_workers=6) as executor:
        for index, segment in enumerate(atmospheric_route.segments):
            if not _segment_may_have_3dep(segment):
                terrain_regions[index] = (
                    TerrainRegionResult(
                        segment_id=segment.segment_id,
                        status="OUTSIDE_REVIEWED_COVERAGE",
                        profile=None,
                        reason="Outside MachLane's conservative U.S. 3DEP request envelope",
                    )
                )
                continue
            terrain_requests[executor.submit(terrain_provider.profile, segment)] = (index, segment)
        for future in as_completed(terrain_requests):
            index, segment = terrain_requests[future]
            try:
                profile = future.result()
            except (RuntimeError, ValueError, OSError, KeyError) as exc:
                terrain_regions[index] = (
                    TerrainRegionResult(
                        segment_id=segment.segment_id,
                        status="UNAVAILABLE",
                        profile=None,
                        reason=str(exc),
                    )
                )
            else:
                terrain_regions[index] = (
                    TerrainRegionResult(
                        segment_id=segment.segment_id,
                        status="LOADED",
                        profile=profile,
                        reason="Real USGS 3DEP sparse availability preview loaded",
                    )
                )
    if any(result is None for result in terrain_regions):
        raise RuntimeError("terrain availability sampling returned an incomplete region set")

    return RealMissionAnalysis(
        observed_route=observed_route,
        atmospheric_route=atmospheric_route,
        weather_regimes=regimes,
        segment_atmospheres=segment_atmospheres,
        noaa_model=noaa.model,
        noaa_coverage=noaa.coverage,
        noaa_requests=noaa.plans_used,
        terrain_regions=tuple(result for result in terrain_regions if result is not None),
        policy_version=AUTOMATIC_WEATHER_SETTINGS.policy_version,
    )
