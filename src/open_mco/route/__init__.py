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
from .opensky import OpenSkyRouteNotFoundError, OpenSkyTrackProvider
from .weather import (
    WeatherRegimeSummary,
    WeatherSegmentationSettings,
    coarsen_route_for_weather,
    segment_route_by_weather,
)

__all__ = [
    "AIRPORT_SOURCE_RETRIEVED",
    "AIRPORT_SOURCE_URL",
    "Airport",
    "MissionDefinition",
    "OpenSkyRouteCache",
    "OpenSkyTrackProvider",
    "OpenSkyRouteNotFoundError",
    "WeatherRegimeSummary",
    "WeatherSegmentationSettings",
    "coarsen_route_for_weather",
    "corridor_geojson",
    "get_mission",
    "interpolate_position",
    "interpolate_segment_position",
    "list_missions",
    "route_distance_m",
    "route_from_waypoints",
    "segment_route_by_weather",
]
