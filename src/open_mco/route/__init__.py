"""Route construction, mission references, and corridor geometry."""

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
from .weather import (
    WeatherRegimeSummary,
    WeatherSegmentationSettings,
    segment_route_by_weather,
)

__all__ = [
    "AIRPORT_SOURCE_RETRIEVED",
    "AIRPORT_SOURCE_URL",
    "Airport",
    "MissionDefinition",
    "WeatherRegimeSummary",
    "WeatherSegmentationSettings",
    "corridor_geojson",
    "get_mission",
    "interpolate_position",
    "interpolate_segment_position",
    "list_missions",
    "route_distance_m",
    "route_from_waypoints",
    "segment_route_by_weather",
]
