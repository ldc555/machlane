"""Conservative primary-waveform propagation for the open research solver.

The nonlinear step follows the conservative finite-volume direction described by Rallabhandi,
Nemec, and Aftosmis, *Recent Enhancements to Modeling Sonic Boom Propagation using Augmented
Burgers' Equation* (NASA, 2023):
https://ntrs.nasa.gov/citations/20230005332

This first implementation couples conservative nonlinear steepening, frequency-dependent classical
and molecular absorption, ray-tube/stratification scaling, and a declared rigid-ground reflection.
It is deliberately ``UNVALIDATED`` and does not claim numerical equivalence with sBOOM.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil

import numpy as np
from numpy.typing import NDArray

from .atmosphere import atmospheric_absorption_np_per_m
from .rays import PrimaryRay

NONLINEARITY_COEFFICIENT = 1.2
RIGID_GROUND_PRESSURE_FACTOR = 2.0


@dataclass(frozen=True)
class PropagatedWaveform:
    time_s: NDArray[np.float64]
    overpressure_pa: NDArray[np.float64]
    step_count: int
    maximum_cfl: float
    final_geometric_scale: float
    reflection_factor: float


def _spectral_loss(
    pressure: NDArray[np.float64],
    time_step_s: float,
    ray: PrimaryRay,
    fraction: float,
) -> NDArray[np.float64]:
    frequency = np.fft.rfftfreq(pressure.size, d=time_step_s)
    midpoint_temperature = 0.5 * (ray.temperature_k[:-1] + ray.temperature_k[1:])
    midpoint_pressure = 0.5 * (ray.pressure_pa[:-1] + ray.pressure_pa[1:])
    midpoint_humidity = 0.5 * (ray.humidity_fraction[:-1] + ray.humidity_fraction[1:])
    attenuation = atmospheric_absorption_np_per_m(
        np.asarray(frequency[None, :], dtype=np.float64),
        np.asarray(midpoint_temperature[:, None], dtype=np.float64),
        np.asarray(midpoint_pressure[:, None], dtype=np.float64),
        np.asarray(midpoint_humidity[:, None], dtype=np.float64),
    )
    integrated = np.sum(attenuation * ray.path_increment_m[:, None], axis=0) * fraction
    return np.fft.irfft(np.fft.rfft(pressure) * np.exp(-integrated), n=pressure.size)


def _uniform_waveform(
    axial_position_m: NDArray[np.float64],
    overpressure_pa: NDArray[np.float64],
    flight_speed_mps: float,
    sample_count: int,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    if sample_count < 128 or sample_count & (sample_count - 1):
        raise ValueError("waveform sample count must be a power of two and at least 128")
    order = np.argsort(axial_position_m)
    axial = axial_position_m[order]
    pressure = overpressure_pa[order]
    unique_axial, unique_indices = np.unique(axial, return_index=True)
    pressure = pressure[unique_indices]
    if unique_axial.size < 3 or np.allclose(pressure, 0):
        raise ValueError("near-field waveform requires three distinct, non-zero samples")
    signal_time = (unique_axial - unique_axial[0]) / flight_speed_mps
    duration = float(signal_time[-1])
    padding = max(0.08, duration * 0.2)
    time = np.linspace(-padding, duration + padding, sample_count, dtype=np.float64)
    uniform_pressure = np.interp(time, signal_time, pressure, left=0.0, right=0.0)
    uniform_pressure[[0, -1]] = 0.0
    return time, np.asarray(uniform_pressure, dtype=np.float64)


def propagate_primary_waveform(
    *,
    axial_position_m: NDArray[np.float64],
    nearfield_overpressure_pa: NDArray[np.float64],
    reference_distance_m: float,
    mach: float,
    ray: PrimaryRay,
    sample_count: int = 512,
    maximum_steps: int = 1_500,
) -> PropagatedWaveform:
    """Propagate a near-field waveform to rigid terrain along one primary ray."""

    if reference_distance_m <= 0 or maximum_steps < 16:
        raise ValueError("reference distance and maximum step count must be positive")
    source_sound_speed = float(ray.sound_speed_mps[-1])
    flight_speed = mach * source_sound_speed
    time, pressure = _uniform_waveform(
        axial_position_m,
        nearfield_overpressure_pa,
        flight_speed,
        sample_count,
    )
    time_step = float(time[1] - time[0])
    pressure = _spectral_loss(pressure, time_step, ray, 0.5)

    propagation_distance = max(0.0, ray.path_length_m - reference_distance_m)
    source_density = float(ray.density_kg_m3[-1])
    ground_density = float(ray.density_kg_m3[0])
    ground_sound_speed = float(ray.sound_speed_mps[0])
    final_scale = (
        np.sqrt(reference_distance_m / max(ray.path_length_m, reference_distance_m))
        * np.sqrt(
            (ground_density * ground_sound_speed)
            / (source_density * source_sound_speed)
        )
    )
    source_nonlinearity = NONLINEARITY_COEFFICIENT / (
        source_density * source_sound_speed**3
    )
    required_steps = max(
        64,
        ceil(
            propagation_distance
            * source_nonlinearity
            * max(float(np.max(np.abs(pressure))), 1e-12)
            / (0.35 * time_step)
        ),
    )
    step_count = min(required_steps, maximum_steps)
    distance_step = propagation_distance / step_count if step_count else 0.0
    maximum_cfl = 0.0
    prior_scale = 1.0
    for index in range(step_count):
        fraction = (index + 0.5) / step_count
        atmosphere_index = int(round((1.0 - fraction) * (ray.sound_speed_mps.size - 1)))
        local_density = float(ray.density_kg_m3[atmosphere_index])
        local_sound_speed = float(ray.sound_speed_mps[atmosphere_index])
        nonlinearity = NONLINEARITY_COEFFICIENT / (local_density * local_sound_speed**3)
        target_scale = 1.0 + (final_scale - 1.0) * ((index + 1) / step_count)
        pressure *= target_scale / prior_scale
        prior_scale = target_scale

        flux = -0.5 * nonlinearity * pressure**2
        left = pressure[:-1]
        right = pressure[1:]
        wave_speed = nonlinearity * np.maximum(np.abs(left), np.abs(right))
        interface_flux = 0.5 * (flux[:-1] + flux[1:]) - 0.5 * wave_speed * (right - left)
        cfl = float(np.max(wave_speed) * distance_step / time_step)
        maximum_cfl = max(maximum_cfl, cfl)
        updated = pressure.copy()
        updated[1:-1] -= distance_step / time_step * (
            interface_flux[1:] - interface_flux[:-1]
        )
        updated[[0, -1]] = 0.0
        pressure = updated

    pressure = _spectral_loss(pressure, time_step, ray, 0.5)
    pressure *= RIGID_GROUND_PRESSURE_FACTOR
    pressure[[0, -1]] = 0.0
    if not np.all(np.isfinite(pressure)):
        raise ValueError("nonlinear propagation produced non-finite pressure values")
    return PropagatedWaveform(
        time_s=time,
        overpressure_pa=pressure,
        step_count=step_count,
        maximum_cfl=maximum_cfl,
        final_geometric_scale=float(final_scale),
        reflection_factor=RIGID_GROUND_PRESSURE_FACTOR,
    )
