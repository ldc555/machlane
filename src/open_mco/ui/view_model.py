"""Small, pure transformations between domain results and the Streamlit presentation."""

from __future__ import annotations

import hashlib
import math
from typing import Any

import numpy as np

from open_mco.atmosphere import project_wind_onto_bearing
from open_mco.models import AtmosphericProfile, PlannerResult, Route
from open_mco.route import corridor_geojson, interpolate_position

METERS_TO_FEET = 3.280839895
METERS_TO_NAUTICAL_MILES = 1 / 1852
METERS_PER_SECOND_TO_KNOTS = 1.943844492


def pressure_color(value_hpa: float, minimum_hpa: float, maximum_hpa: float) -> list[int]:
    """Map flight-level ambient pressure to a readable blue-white-red scale."""

    if maximum_hpa <= minimum_hpa:
        return [148, 163, 184, 220]
    fraction = min(1.0, max(0.0, (value_hpa - minimum_hpa) / (maximum_hpa - minimum_hpa)))
    if fraction <= 0.5:
        blend = fraction * 2
        start, end = (37, 99, 235), (226, 232, 240)
    else:
        blend = (fraction - 0.5) * 2
        start, end = (226, 232, 240), (220, 38, 38)
    return [
        round(left + (right - left) * blend)
        for left, right in zip(start, end, strict=True)
    ] + [225]


def mock_flight_state(
    progress: float,
    *,
    route_distance_nmi: float,
    cruise_mach: float,
    cruise_altitude_ft: float,
) -> dict[str, float | str | bool]:
    """Return a phase-aware synthetic SST state without supersonic takeoff or landing.

    This is a UI fixture, not an aircraft-performance model. It gives the mock feed physically
    sensible phase ordering while the reviewed SST climb, acceleration, and descent schedule is
    still unavailable.
    """

    if route_distance_nmi <= 0:
        raise ValueError("route distance must be positive")
    bounded = min(1.0, max(0.0, progress))
    distance_nmi = bounded * route_distance_nmi
    remaining_nmi = route_distance_nmi - distance_nmi
    transition_end_nmi = min(180.0, route_distance_nmi / 2)
    climb_end_nmi = transition_end_nmi * (2 / 3)
    initial_climb_end_nmi = transition_end_nmi / 9

    def interpolate(start: float, end: float, fraction: float) -> float:
        return start + (end - start) * min(1.0, max(0.0, fraction))

    if distance_nmi < initial_climb_end_nmi:
        local = distance_nmi / initial_climb_end_nmi
        phase = "Takeoff / initial climb"
        mach = interpolate(0.20, 0.45, local)
        altitude_ft = interpolate(0, 10_000, local)
    elif distance_nmi < climb_end_nmi:
        local = (distance_nmi - initial_climb_end_nmi) / (
            climb_end_nmi - initial_climb_end_nmi
        )
        phase = "Climb"
        mach = interpolate(0.45, 0.78, local)
        altitude_ft = interpolate(10_000, 45_000, local)
    elif distance_nmi < transition_end_nmi:
        local = (distance_nmi - climb_end_nmi) / (transition_end_nmi - climb_end_nmi)
        phase = "Transonic acceleration"
        mach = interpolate(0.78, cruise_mach, local)
        altitude_ft = interpolate(45_000, cruise_altitude_ft, local)
    elif remaining_nmi >= transition_end_nmi:
        phase = "Supersonic cruise"
        mach = cruise_mach
        altitude_ft = cruise_altitude_ft
    elif remaining_nmi >= climb_end_nmi:
        local = (transition_end_nmi - remaining_nmi) / (
            transition_end_nmi - climb_end_nmi
        )
        phase = "Supersonic deceleration"
        mach = interpolate(cruise_mach, 0.78, local)
        altitude_ft = interpolate(cruise_altitude_ft, 45_000, local)
    elif remaining_nmi >= initial_climb_end_nmi:
        local = (climb_end_nmi - remaining_nmi) / (
            climb_end_nmi - initial_climb_end_nmi
        )
        phase = "Descent"
        mach = interpolate(0.78, 0.45, local)
        altitude_ft = interpolate(45_000, 10_000, local)
    else:
        local = (initial_climb_end_nmi - remaining_nmi) / initial_climb_end_nmi
        phase = "Approach / landing"
        mach = interpolate(0.45, 0.20, local)
        altitude_ft = interpolate(10_000, 0, local)
    return {
        "phase": phase,
        "mach": mach,
        "altitude_ft": altitude_ft,
        "supersonic": mach >= 1.0,
    }


def display_longitude(longitude: float, reference: float) -> float:
    """Keep a displayed longitude within one half-world of a local reference."""

    return reference + ((longitude - reference + 180) % 360) - 180


def atmosphere_metrics(
    profile: AtmosphericProfile, altitude_m: float, bearing_deg: float
) -> dict[str, float]:
    """Interpolate the visible atmospheric variables at one candidate altitude."""

    temperature_k = float(np.interp(altitude_m, profile.altitude_m, profile.temperature_k))
    pressure_pa = float(np.interp(altitude_m, profile.altitude_m, profile.pressure_pa))
    zonal_mps = float(np.interp(altitude_m, profile.altitude_m, profile.zonal_wind_mps))
    meridional_mps = float(np.interp(altitude_m, profile.altitude_m, profile.meridional_wind_mps))
    return {
        "temperature_c": temperature_k - 273.15,
        "pressure_hpa": pressure_pa / 100,
        "wind_speed_kt": math.hypot(zonal_mps, meridional_mps) * METERS_PER_SECOND_TO_KNOTS,
        "along_wind_kt": project_wind_onto_bearing(
            zonal_mps, meridional_mps, bearing_deg
        )
        * METERS_PER_SECOND_TO_KNOTS,
    }


def mock_live_metrics(
    baseline: dict[str, float], mission_id: str, progress: float
) -> dict[str, float]:
    """Return smooth, deterministic pseudo-live variations around a synthetic profile.

    The values deliberately look like a moving model feed without depending on a network or
    pretending to be observations. Mission-derived phase offsets make each route distinct, while
    continuous sine waves keep slider motion fluid and reproducible for tests and demonstrations.
    """

    bounded = min(1.0, max(0.0, progress))
    phases = [
        byte / 255 * 2 * math.pi
        for byte in hashlib.sha256(mission_id.encode()).digest()[:8]
    ]

    def variation(primary: int, secondary: int, cycles: float) -> float:
        angle = bounded * cycles * 2 * math.pi
        return math.sin(angle + phases[primary]) + 0.35 * math.sin(
            angle * 2.3 + phases[secondary]
        )

    return {
        "pressure_hpa": max(
            0.0, baseline["pressure_hpa"] + 1.6 * variation(0, 1, 2.4)
        ),
        "temperature_c": baseline["temperature_c"] + 0.9 * variation(2, 3, 1.7),
        "wind_speed_kt": max(
            0.0, baseline["wind_speed_kt"] + 3.8 * variation(4, 5, 2.8)
        ),
        "along_wind_kt": baseline["along_wind_kt"] + 3.2 * variation(6, 7, 2.1),
    }


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


def aircraft_view(
    route: Route, progress: float, longitude_reference: float
) -> dict[str, float]:
    """Resolve the aircraft marker position and WGS-84 track bearing at a progress fraction.

    Position and bearing are taken from the existing geodesic route interpolation, so this never
    substitutes approximate latitude/longitude interpolation. ``display_longitude`` keeps the marker
    continuous across the antimeridian when unwrapped against the same reference the route uses.
    """

    latitude, longitude = interpolate_position(route, progress)
    bearing_deg = route.segments[active_segment_index(route, progress)].bearing_deg
    return {
        "latitude": latitude,
        "longitude": longitude,
        "display_longitude": display_longitude(longitude, longitude_reference),
        "bearing_deg": bearing_deg,
    }


def segment_rows(route: Route, result: PlannerResult) -> list[dict[str, Any]]:
    """Create the one canonical UI table for map, inspector, plots, and downloads."""

    rows: list[dict[str, Any]] = []
    cumulative_m = 0.0
    _, longitude_reference = interpolate_position(route, 0.5)
    for segment, limit in zip(route.segments, result.segment_limits, strict=True):
        start_km = cumulative_m / 1000
        start_nmi = cumulative_m * METERS_TO_NAUTICAL_MILES
        cumulative_m += segment.distance_m
        accepted = sum(candidate.accepted for candidate in limit.candidate_evaluations)
        selected = next(
            (
                candidate
                for candidate in limit.candidate_evaluations
                if candidate.accepted
                and candidate.mach == limit.selected_mach
                and candidate.altitude_m == limit.selected_altitude_m
            ),
            None,
        )
        metrics = {} if selected is None or selected.propagation is None else selected.propagation.metrics
        rows.append(
            {
                "segment": segment.segment_id,
                "start_km": round(start_km, 1),
                "end_km": round(cumulative_m / 1000, 1),
                "start_nmi": round(start_nmi, 1),
                "end_nmi": round(cumulative_m * METERS_TO_NAUTICAL_MILES, 1),
                "path": [
                    [display_longitude(longitude, longitude_reference), latitude]
                    for latitude, longitude in (
                        segment.path
                        or (
                            (segment.start_latitude, segment.start_longitude),
                            (segment.end_latitude, segment.end_longitude),
                        )
                    )
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
                "synthetic_score": metrics.get("synthetic_cutoff_score"),
                "along_wind_kt": None
                if metrics.get("along_route_wind_mps") is None
                else float(metrics["along_route_wind_mps"]) * METERS_PER_SECOND_TO_KNOTS,
                "color": [45, 212, 191, 210] if limit.status == "PASS" else [248, 113, 113, 220],
            }
        )
    return rows


def corridor_rows(route: Route, result: PlannerResult) -> list[dict[str, Any]]:
    """Flatten corridor GeoJSON into the minimal records PyDeck needs."""

    features = corridor_geojson(route, result.segment_limits)["features"]
    _, longitude_reference = interpolate_position(route, 0.5)
    rows: list[dict[str, Any]] = []
    for feature in features:
        polygon = feature["geometry"]["coordinates"][0]
        rows.append(
            {
                "segment": feature["properties"]["segment_id"],
                "polygon": [
                    [display_longitude(longitude, longitude_reference), latitude]
                    for longitude, latitude in polygon
                ],
                "color": [20, 184, 166, 55]
                if feature["properties"]["status"] == "PASS"
                else [239, 68, 68, 70],
            }
        )
    return rows
