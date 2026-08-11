"""Route construction, mission references, and corridor geometry."""

from .cache import OpenSkyRouteCache
from .geometry import (
    corridor_geojson,
    interpolate_position,
    interpolate_segment_position,
    route_distance_m,
    route_from_waypoints,
)
from .missions import (
    AIRPORT_SOURCE_RETRIEVED,
    AIRPORT_SOURCE_URL,
    Airport,
    MissionDefinition,
    get_mission,
    list_missions,
)
from .opensky import OpenSkyObservedFlight, OpenSkyRouteNotFoundError, OpenSkyTrackProvider
from .weather import (
    AUTOMATIC_WEATHER_POLICY_VERSION,
    AUTOMATIC_WEATHER_SAMPLE_SPACING_M,
    AUTOMATIC_WEATHER_SETTINGS,
    WeatherRegimeSummary,
    WeatherSegmentationSettings,
    coarsen_route_for_weather,
    observation_time_at_progress,
    segment_route_by_weather,
    weather_sample_times,
)

__all__ = [
    "AIRPORT_SOURCE_RETRIEVED",
    "AIRPORT_SOURCE_URL",
    "Airport",
    "MissionDefinition",
    "OpenSkyRouteCache",
    "OpenSkyObservedFlight",
    "OpenSkyTrackProvider",
    "OpenSkyRouteNotFoundError",
    "AUTOMATIC_WEATHER_POLICY_VERSION",
    "AUTOMATIC_WEATHER_SAMPLE_SPACING_M",
    "AUTOMATIC_WEATHER_SETTINGS",
    "WeatherRegimeSummary",
    "WeatherSegmentationSettings",
    "coarsen_route_for_weather",
    "corridor_geojson",
    "get_mission",
    "interpolate_position",
    "interpolate_segment_position",
    "list_missions",
    "observation_time_at_progress",
    "route_distance_m",
    "route_from_waypoints",
    "segment_route_by_weather",
    "weather_sample_times",
]
