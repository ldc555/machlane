"""Atmospheric primitives used by the open research solver.

Sound speed and density use ideal-gas relations.  The frequency-dependent attenuation expression
is the commonly published ISO 9613-1 form for classical and oxygen/nitrogen relaxation losses.
It is used here as a research component, not as validation of the complete propagation model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

AIR_GAMMA = 1.4
AIR_GAS_CONSTANT_J_KG_K = 287.05287
REFERENCE_PRESSURE_PA = 101_325.0
REFERENCE_TEMPERATURE_K = 293.15
TRIPLE_POINT_TEMPERATURE_K = 273.16


class AtmosphereCoverageError(ValueError):
    """A requested propagation altitude is outside the supplied real atmosphere."""


@dataclass(frozen=True)
class AtmosphericColumn:
    altitude_m: NDArray[np.float64]
    temperature_k: NDArray[np.float64]
    pressure_pa: NDArray[np.float64]
    zonal_wind_mps: NDArray[np.float64]
    meridional_wind_mps: NDArray[np.float64]
    humidity_fraction: NDArray[np.float64]


def column_from_mapping(profile: dict[str, Any]) -> AtmosphericColumn:
    """Validate and normalize one serialized ``AtmosphericProfile`` mapping."""

    required = (
        "altitude_m",
        "temperature_k",
        "pressure_pa",
        "zonal_wind_mps",
        "meridional_wind_mps",
        "humidity_fraction",
    )
    missing = [name for name in required if profile.get(name) is None]
    if missing:
        raise AtmosphereCoverageError(
            "research propagation requires complete NOAA columns including humidity: "
            + ", ".join(missing)
        )
    arrays = {
        name: np.asarray(profile[name], dtype=np.float64)
        for name in required
    }
    lengths = {values.size for values in arrays.values()}
    if len(lengths) != 1 or not lengths or next(iter(lengths)) < 2:
        raise AtmosphereCoverageError("atmospheric columns must have equal lengths and two levels")
    altitude = arrays["altitude_m"]
    if not np.all(np.isfinite(np.concatenate(tuple(arrays.values())))):
        raise AtmosphereCoverageError("atmospheric columns contain non-finite values")
    if np.any(np.diff(altitude) <= 0):
        raise AtmosphereCoverageError("atmospheric altitude must be strictly increasing")
    if np.any(arrays["temperature_k"] <= 0) or np.any(arrays["pressure_pa"] <= 0):
        raise AtmosphereCoverageError("atmospheric temperature and pressure must be positive")
    if np.any((arrays["humidity_fraction"] < 0) | (arrays["humidity_fraction"] > 1)):
        raise AtmosphereCoverageError("relative humidity must remain between zero and one")
    return AtmosphericColumn(
        altitude_m=altitude,
        temperature_k=arrays["temperature_k"],
        pressure_pa=arrays["pressure_pa"],
        zonal_wind_mps=arrays["zonal_wind_mps"],
        meridional_wind_mps=arrays["meridional_wind_mps"],
        humidity_fraction=arrays["humidity_fraction"],
    )


def sound_speed_mps(temperature_k: NDArray[np.float64] | float) -> NDArray[np.float64]:
    return np.asarray(
        np.sqrt(
            AIR_GAMMA
            * AIR_GAS_CONSTANT_J_KG_K
            * np.asarray(temperature_k, dtype=np.float64)
        ),
        dtype=np.float64,
    )


def density_kg_m3(
    pressure_pa: NDArray[np.float64] | float,
    temperature_k: NDArray[np.float64] | float,
) -> NDArray[np.float64]:
    return np.asarray(
        np.asarray(pressure_pa, dtype=np.float64)
        / (
            AIR_GAS_CONSTANT_J_KG_K
            * np.asarray(temperature_k, dtype=np.float64)
        ),
        dtype=np.float64,
    )


def project_wind_mps(
    zonal_wind_mps: NDArray[np.float64],
    meridional_wind_mps: NDArray[np.float64],
    bearing_deg: float,
) -> NDArray[np.float64]:
    bearing = np.deg2rad(bearing_deg)
    return np.asarray(
        zonal_wind_mps * np.sin(bearing) + meridional_wind_mps * np.cos(bearing),
        dtype=np.float64,
    )


def require_vertical_coverage(
    column: AtmosphericColumn,
    ground_altitude_m: float,
    source_altitude_m: float,
) -> None:
    if source_altitude_m <= ground_altitude_m:
        raise AtmosphereCoverageError("aircraft altitude must be above terrain")
    tolerance_m = 100.0
    if ground_altitude_m < column.altitude_m[0] - tolerance_m:
        raise AtmosphereCoverageError("NOAA column does not reach the terrain altitude")
    if source_altitude_m > column.altitude_m[-1] + tolerance_m:
        raise AtmosphereCoverageError("NOAA column does not reach the aircraft altitude")


def atmospheric_absorption_np_per_m(
    frequency_hz: NDArray[np.float64],
    temperature_k: NDArray[np.float64],
    pressure_pa: NDArray[np.float64],
    humidity_fraction: NDArray[np.float64],
) -> NDArray[np.float64]:
    """Return ISO-style atmospheric amplitude attenuation in nepers per metre.

    Inputs broadcast.  Relative humidity is a fraction from zero to one.  The zero-frequency
    coefficient is exactly zero.
    """

    frequency = np.maximum(np.asarray(frequency_hz, dtype=np.float64), 0.0)
    temperature = np.asarray(temperature_k, dtype=np.float64)
    pressure = np.asarray(pressure_pa, dtype=np.float64)
    humidity = np.clip(np.asarray(humidity_fraction, dtype=np.float64), 0.0, 1.0)
    relative_pressure = pressure / REFERENCE_PRESSURE_PA
    relative_temperature = temperature / REFERENCE_TEMPERATURE_K
    saturation_ratio = 10.0 ** (
        -6.8346 * (TRIPLE_POINT_TEMPERATURE_K / temperature) ** 1.261 + 4.6151
    )
    molar_water = humidity * saturation_ratio / relative_pressure
    relaxation_oxygen = relative_pressure * (
        24.0 + 4.04e4 * molar_water * (0.02 + molar_water) / (0.391 + molar_water)
    )
    relaxation_nitrogen = (
        relative_pressure
        * relative_temperature ** -0.5
        * (
            9.0
            + 280.0
            * molar_water
            * np.exp(-4.17 * (relative_temperature ** (-1.0 / 3.0) - 1.0))
        )
    )
    frequency_sq = frequency**2
    classical = 1.84e-11 * relative_pressure**-1 * relative_temperature**0.5
    oxygen = (
        0.01275
        * np.exp(-2239.1 / temperature)
        / (relaxation_oxygen + frequency_sq / relaxation_oxygen)
    )
    nitrogen = (
        0.1068
        * np.exp(-3352.0 / temperature)
        / (relaxation_nitrogen + frequency_sq / relaxation_nitrogen)
    )
    alpha_db_per_m = 8.686 * frequency_sq * (
        classical + relative_temperature**-2.5 * (oxygen + nitrogen)
    )
    alpha_np_per_m = alpha_db_per_m / 8.685889638
    return np.asarray(np.where(frequency == 0, 0.0, alpha_np_per_m), dtype=np.float64)
