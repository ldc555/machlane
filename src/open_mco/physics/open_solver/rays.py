"""Stratified-atmosphere primary-ray integration for the open research solver.

The implementation applies Snell-law geometrical acoustics along a three-dimensional WGS-84
heading with route-projected wind.  It does not yet solve eigenrays, caustics, diffraction, or
secondary paths.  NASA's public discussion of the missing secondary-ray requirements is:
https://ntrs.nasa.gov/citations/20210024772
"""

from __future__ import annotations

from dataclasses import dataclass
from math import asin, atan2, cos, degrees, radians, sin, sqrt

import numpy as np
from numpy.typing import NDArray

from .atmosphere import (
    AtmosphericColumn,
    density_kg_m3,
    project_wind_mps,
    require_vertical_coverage,
    sound_speed_mps,
)


class RayTraceError(ValueError):
    """A primary ray cannot be traced through the supplied atmospheric column."""


@dataclass(frozen=True)
class PrimaryRay:
    launch_roll_deg: float
    bearing_deg: float
    path_length_m: float
    horizontal_distance_m: float
    along_track_offset_m: float
    cross_track_offset_m: float
    travel_time_s: float
    ground_incidence_deg: float
    altitude_m: NDArray[np.float64]
    path_increment_m: NDArray[np.float64]
    temperature_k: NDArray[np.float64]
    pressure_pa: NDArray[np.float64]
    humidity_fraction: NDArray[np.float64]
    sound_speed_mps: NDArray[np.float64]
    density_kg_m3: NDArray[np.float64]
    horizontal_from_source_m: NDArray[np.float64]
    altitude_from_source_m: NDArray[np.float64]


def _trapz(values: NDArray[np.float64], coordinate: NDArray[np.float64]) -> float:
    return float(np.sum((values[:-1] + values[1:]) * 0.5 * np.diff(coordinate)))


def trace_primary_ray(
    column: AtmosphericColumn,
    *,
    source_altitude_m: float,
    ground_altitude_m: float,
    mach: float,
    route_bearing_deg: float,
    launch_roll_deg: float = 0.0,
    vertical_samples: int = 241,
) -> PrimaryRay:
    """Trace one downward primary ray through a horizontally stratified atmosphere."""

    if mach <= 1:
        raise RayTraceError("primary sonic-boom propagation requires Mach greater than one")
    if vertical_samples < 16:
        raise ValueError("primary ray requires at least 16 vertical samples")
    if abs(launch_roll_deg) >= 89:
        raise RayTraceError("near-horizontal launch cannot reach terrain in this primary model")
    require_vertical_coverage(column, ground_altitude_m, source_altitude_m)

    mach_angle = asin(1.0 / mach)
    roll = radians(launch_roll_deg)
    vertical_down = sin(mach_angle) * cos(roll)
    along = cos(mach_angle)
    cross = sin(mach_angle) * sin(roll)
    horizontal = sqrt(along * along + cross * cross)
    if vertical_down <= 0:
        raise RayTraceError("ray launch has no downward component")
    theta_source = atan2(horizontal, vertical_down)
    relative_bearing = degrees(atan2(cross, along))
    ray_bearing = (route_bearing_deg + 180.0 + relative_bearing) % 360.0

    altitude = np.linspace(
        ground_altitude_m, source_altitude_m, vertical_samples, dtype=np.float64
    )
    temperature = np.interp(altitude, column.altitude_m, column.temperature_k)
    pressure = np.interp(altitude, column.altitude_m, column.pressure_pa)
    humidity = np.interp(altitude, column.altitude_m, column.humidity_fraction)
    zonal = np.interp(altitude, column.altitude_m, column.zonal_wind_mps)
    meridional = np.interp(altitude, column.altitude_m, column.meridional_wind_mps)
    acoustic_speed = sound_speed_mps(temperature)
    effective_speed = acoustic_speed + project_wind_mps(zonal, meridional, ray_bearing)
    if np.any(effective_speed <= 0):
        raise RayTraceError("effective sound speed became non-positive")

    source_effective_speed = float(effective_speed[-1])
    horizontal_slowness = sin(theta_source) / source_effective_speed
    sine_theta = horizontal_slowness * effective_speed
    if np.any(sine_theta >= 1.0):
        raise RayTraceError("primary ray encounters a refractive cutoff before reaching terrain")
    cosine_theta = np.sqrt(1.0 - sine_theta**2)
    tangent_theta = sine_theta / cosine_theta
    horizontal_distance = _trapz(tangent_theta, altitude)
    path_length = _trapz(1.0 / cosine_theta, altitude)
    travel_time = _trapz(1.0 / (effective_speed * cosine_theta), altitude)
    altitude_steps = np.diff(altitude)
    horizontal_increment = altitude_steps * 0.5 * (
        tangent_theta[:-1] + tangent_theta[1:]
    )
    path_increment = altitude_steps / (
        0.5 * (cosine_theta[:-1] + cosine_theta[1:])
    )
    horizontal_from_ground = np.concatenate(
        (np.asarray([0.0], dtype=np.float64), np.cumsum(horizontal_increment))
    )
    horizontal_from_source = np.asarray(
        horizontal_distance - horizontal_from_ground[::-1], dtype=np.float64
    )
    horizontal_from_source[0] = 0.0
    altitude_from_source = np.asarray(altitude[::-1], dtype=np.float64)
    bearing_difference = radians(relative_bearing)
    return PrimaryRay(
        launch_roll_deg=launch_roll_deg,
        bearing_deg=ray_bearing,
        path_length_m=path_length,
        horizontal_distance_m=horizontal_distance,
        along_track_offset_m=-horizontal_distance * cos(bearing_difference),
        cross_track_offset_m=horizontal_distance * sin(bearing_difference),
        travel_time_s=travel_time,
        ground_incidence_deg=degrees(asin(float(sine_theta[0]))),
        altitude_m=altitude,
        path_increment_m=path_increment,
        temperature_k=temperature,
        pressure_pa=pressure,
        humidity_fraction=humidity,
        sound_speed_mps=acoustic_speed,
        density_kg_m3=density_kg_m3(pressure, temperature),
        horizontal_from_source_m=horizontal_from_source,
        altitude_from_source_m=altitude_from_source,
    )
