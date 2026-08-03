"""Small, pure transformations between domain results and the Streamlit presentation."""

from __future__ import annotations

from typing import Any

from open_mco.models import PlannerResult, Route
from open_mco.route import corridor_geojson

METERS_TO_FEET = 3.280839895


def _unwrap_longitude(longitude: float, reference: float) -> float:
    """Keep a displayed longitude within one half-world of a local reference."""

    return reference + ((longitude - reference + 180) % 360) - 180


def active_segment_index(route: Route, progress: float) -> int:
    """Resolve a bounded route-progress fraction to a stable segment index."""

    bounded = min(1.0, max(0.0, progress))
    target = sum(segment.distance_m for segment in route.segments) * bounded
    elapsed = 0.0
    for index, segment in enumerate(route.segments):
        elapsed += segment.distance_m
        if target <= elapsed:
            return index
    return len(route.segments) - 1


def segment_rows(route: Route, result: PlannerResult) -> list[dict[str, Any]]:
    """Create the one canonical UI table for map, inspector, plots, and downloads."""

    rows: list[dict[str, Any]] = []
    cumulative_m = 0.0
    for segment, limit in zip(route.segments, result.segment_limits, strict=True):
        start_km = cumulative_m / 1000
        cumulative_m += segment.distance_m
        accepted = sum(candidate.accepted for candidate in limit.candidate_evaluations)
        rows.append(
            {
                "segment": segment.segment_id,
                "start_km": round(start_km, 1),
                "end_km": round(cumulative_m / 1000, 1),
                "path": [
                    [segment.start_longitude, segment.start_latitude],
                    [
                        _unwrap_longitude(segment.end_longitude, segment.start_longitude),
                        segment.end_latitude,
                    ],
                ],
                "bearing_deg": round(segment.bearing_deg, 1),
                "mach": limit.selected_mach,
                "altitude_ft": None
                if limit.selected_altitude_m is None
                else round(limit.selected_altitude_m * METERS_TO_FEET),
                "decision": "SYNTHETIC ELIGIBLE" if limit.status == "PASS" else "NO CANDIDATE",
                "boom": "NOT MODELED",
                "accepted_candidates": accepted,
                "total_candidates": len(limit.candidate_evaluations),
                "color": [45, 212, 191, 210] if limit.status == "PASS" else [248, 113, 113, 220],
            }
        )
    return rows


def corridor_rows(route: Route, result: PlannerResult) -> list[dict[str, Any]]:
    """Flatten corridor GeoJSON into the minimal records PyDeck needs."""

    features = corridor_geojson(route, result.segment_limits)["features"]
    rows: list[dict[str, Any]] = []
    for feature in features:
        polygon = feature["geometry"]["coordinates"][0]
        reference = polygon[0][0]
        rows.append(
            {
                "segment": feature["properties"]["segment_id"],
                "polygon": [
                    [_unwrap_longitude(longitude, reference), latitude]
                    for longitude, latitude in polygon
                ],
                "color": [20, 184, 166, 55]
                if feature["properties"]["status"] == "PASS"
                else [239, 68, 68, 70],
            }
        )
    return rows
