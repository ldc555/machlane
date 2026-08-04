"""Geodesic route segmentation and result corridor generation."""

from __future__ import annotations

import math
from typing import Any

from pyproj import Geod

from open_mco.models import Route, RouteSegment, SegmentLimit

_GEOD = Geod(ellps="WGS84")


def route_from_waypoints(
    waypoints: list[tuple[float, float]] | tuple[tuple[float, float], ...],
    *,
    spacing_m: float,
    name: str = "route",
) -> Route:
    """Split ordered WGS84 latitude/longitude waypoints at approximately equal spacing."""

    if len(waypoints) < 2:
        raise ValueError("a route requires at least two waypoints")
    if spacing_m <= 0:
        raise ValueError("segment spacing must be positive")
    for latitude, longitude in waypoints:
        if not math.isfinite(latitude) or not math.isfinite(longitude):
            raise ValueError("waypoint coordinates must be finite")
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError("waypoint coordinates must be valid WGS-84 latitude/longitude")
    segments: list[RouteSegment] = []
    for start, end in zip(waypoints, waypoints[1:], strict=False):
        lat1, lon1 = start
        lat2, lon2 = end
        bearing, _, distance = _GEOD.inv(lon1, lat1, lon2, lat2)
        if distance <= 0:
            raise ValueError("consecutive route waypoints must be distinct")
        pieces = max(1, math.ceil(distance / spacing_m))
        intermediate = [] if pieces == 1 else _GEOD.npts(lon1, lat1, lon2, lat2, pieces - 1)
        points = [(lon1, lat1), *intermediate, (lon2, lat2)]
        for point_start, point_end in zip(points, points[1:], strict=False):
            seg_bearing, _, seg_distance = _GEOD.inv(*point_start, *point_end)
            segments.append(
                RouteSegment(
                    segment_id=f"S{len(segments) + 1:04d}",
                    start_latitude=point_start[1],
                    start_longitude=point_start[0],
                    end_latitude=point_end[1],
                    end_longitude=point_end[0],
                    distance_m=seg_distance,
                    bearing_deg=seg_bearing % 360,
                )
            )
    return Route(name=name, waypoints=tuple(waypoints), segments=tuple(segments))


def interpolate_position(route: Route, progress: float) -> tuple[float, float]:
    """Interpolate aircraft latitude/longitude by distance fraction along the route."""

    bounded = min(1.0, max(0.0, progress))
    total = sum(segment.distance_m for segment in route.segments)
    target = total * bounded
    elapsed = 0.0
    for segment in route.segments:
        if elapsed + segment.distance_m >= target:
            offset = target - elapsed
            lon, lat, _ = _GEOD.fwd(
                segment.start_longitude, segment.start_latitude, segment.bearing_deg, offset
            )
            return lat, lon
        elapsed += segment.distance_m
    last = route.segments[-1]
    return last.end_latitude, last.end_longitude


def interpolate_segment_position(
    segment: RouteSegment, progress: float = 0.5
) -> tuple[float, float]:
    """Interpolate one route segment by bounded distance fraction."""

    bounded = min(1.0, max(0.0, progress))
    longitude, latitude, _ = _GEOD.fwd(
        segment.start_longitude,
        segment.start_latitude,
        segment.bearing_deg,
        segment.distance_m * bounded,
    )
    return latitude, longitude


def route_distance_m(route: Route) -> float:
    """Return the WGS-84 ellipsoidal distance along every route segment."""

    return sum(segment.distance_m for segment in route.segments)


def corridor_geojson(
    route: Route, limits: tuple[SegmentLimit, ...], *, half_width_m: float = 10_000
) -> dict[str, Any]:
    """Create a modeled corridor GeoJSON; this does not represent legal airspace approval."""

    by_id = {limit.segment_id: limit for limit in limits}
    features: list[dict[str, Any]] = []
    for segment in route.segments:
        limit = by_id[segment.segment_id]
        left_bearing = (segment.bearing_deg - 90) % 360
        right_bearing = (segment.bearing_deg + 90) % 360
        slon, slat, _ = _GEOD.fwd(
            segment.start_longitude, segment.start_latitude, left_bearing, half_width_m
        )
        elon, elat, _ = _GEOD.fwd(
            segment.end_longitude, segment.end_latitude, left_bearing, half_width_m
        )
        erlon, erlat, _ = _GEOD.fwd(
            segment.end_longitude, segment.end_latitude, right_bearing, half_width_m
        )
        srlon, srlat, _ = _GEOD.fwd(
            segment.start_longitude, segment.start_latitude, right_bearing, half_width_m
        )
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "segment_id": segment.segment_id,
                    "status": limit.status,
                    "selected_mach": limit.selected_mach,
                    "selected_altitude_m": limit.selected_altitude_m,
                    "legal_airspace_approval": False,
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[slon, slat], [elon, elat], [erlon, erlat], [srlon, srlat], [slon, slat]]
                    ],
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}
