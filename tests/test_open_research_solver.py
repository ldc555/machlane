from __future__ import annotations

from datetime import UTC, datetime

import numpy as np

from open_mco.physics import OpenResearchRouteSolver
from open_mco.physics.open_solver.atmosphere import (
    atmospheric_absorption_np_per_m,
    column_from_mapping,
)
from open_mco.physics.open_solver.rays import trace_primary_ray


def _atmosphere() -> dict[str, object]:
    return {
        "altitude_m": [100.0, 5_000.0, 10_000.0, 17_000.0],
        "temperature_k": [288.0, 255.0, 225.0, 218.0],
        "pressure_pa": [100_000.0, 54_000.0, 26_000.0, 8_500.0],
        "zonal_wind_mps": [0.0, 5.0, 12.0, 18.0],
        "meridional_wind_mps": [0.0, -2.0, -5.0, -8.0],
        "humidity_fraction": [0.5, 0.3, 0.1, 0.02],
        "latitude": 35.0,
        "longitude": -95.0,
        "valid_time": datetime(2026, 8, 3, 20, tzinfo=UTC).isoformat(),
        "source": {"provider": "NOAA_HRRR"},
    }


def _request() -> dict[str, object]:
    symmetric_atmosphere = _atmosphere()
    symmetric_atmosphere["zonal_wind_mps"] = [0.0, 0.0, 0.0, 0.0]
    symmetric_atmosphere["meridional_wind_mps"] = [0.0, 0.0, 0.0, 0.0]
    waveform_points = (
        (900.0, 0.0),
        (1_000.0, 1.2),
        (1_150.0, 0.4),
        (1_300.0, -0.8),
        (1_500.0, 0.0),
    )
    signature = [
        {
            "signature_id": f"LM1021_TEST_AZ{roll:+g}",
            "axial_position_ft": axial,
            "delta_pressure_psf": pressure,
            "reference_distance_ft": 730.3,
            "azimuth_deg": roll % 360,
            "mach": 1.6,
            "altitude_ft": 55_000.0,
            "weight_lb": 354_000.0,
            "angle_of_attack_deg": 2.1,
        }
        for roll in (-30.0, 0.0, 30.0)
        for axial, pressure in waveform_points
    ]
    return {
        "schema": "machlane-route-solver-request-v1",
        "created_at": "2026-08-03T18:00:00+00:00",
        "aircraft": {
            "aircraft_id": "lm1021-test",
            "workbook_checksum": "a" * 64,
            "nearfield_samples": signature,
        },
        "route": {"waypoints": [[32.9, -97.0], [40.6, -73.8]]},
        "regions": [
            {
                "segment": {
                    "segment_id": "S0001",
                    "start_latitude": 32.9,
                    "start_longitude": -97.0,
                    "end_latitude": 34.0,
                    "end_longitude": -95.0,
                    "distance_m": 220_000.0,
                    "bearing_deg": 55.0,
                    "path": [[32.9, -97.0], [34.0, -95.0]],
                },
                "planned_state": {"mach": 1.6, "altitude_ft": 55_000.0},
                "propagation_eligibility": "READY",
                "matching_signature_ids": [
                    "LM1021_TEST_AZ-30",
                    "LM1021_TEST_AZ+0",
                    "LM1021_TEST_AZ+30",
                ],
                "atmosphere": symmetric_atmosphere,
                "terrain": {
                    "status": "LOADED",
                    "reason": "test terrain",
                    "profile": {
                        "distance_m": [0.0, 220_000.0],
                        "elevation_m": [180.0, 220.0],
                        "latitude": [32.9, 34.0],
                        "longitude": [-97.0, -95.0],
                        "source": {"provider": "USGS_3DEP"},
                    },
                },
            }
        ],
        "acceptance": {
            "boom_limit_psf": 0.11,
            "boom_limit_pa": 0.11 * 47.88025898033584,
            "requested_ray_families": [
                "PRIMARY",
                "SECONDARY_DIRECT",
                "SECONDARY_INDIRECT",
            ],
        },
    }


def test_absorption_is_zero_at_dc_and_positive_at_audio_frequency() -> None:
    attenuation = atmospheric_absorption_np_per_m(
        np.asarray([0.0, 100.0]),
        np.asarray([293.15, 293.15]),
        np.asarray([101_325.0, 101_325.0]),
        np.asarray([0.5, 0.5]),
    )
    assert attenuation[0] == 0
    assert attenuation[1] > 0


def test_primary_ray_reaches_ground_and_records_three_dimensional_heading() -> None:
    ray = trace_primary_ray(
        column_from_mapping(_atmosphere()),
        source_altitude_m=55_000 * 0.3048,
        ground_altitude_m=200.0,
        mach=1.6,
        route_bearing_deg=55.0,
    )
    assert ray.path_length_m > 16_000
    assert ray.horizontal_distance_m > 0
    assert ray.travel_time_s > 0
    assert 0 <= ray.ground_incidence_deg < 90
    assert ray.bearing_deg != 55.0


def test_open_solver_calculates_waveform_but_fails_closed_on_secondary_rays() -> None:
    result = OpenResearchRouteSolver().run(_request())
    assert result.solver.validation_status == "UNVALIDATED"
    assert result.baseline.classification == "UNKNOWN"
    assert result.recommended is None
    assert result.baseline.completed_ray_families == ("PRIMARY",)
    assert len(result.baseline.surface_samples) == 3
    assert {sample.launch_roll_deg for sample in result.baseline.surface_samples} == {
        -30.0,
        0.0,
        30.0,
    }
    assert min(sample.cross_track_m for sample in result.baseline.surface_samples) < 0
    assert max(sample.cross_track_m for sample in result.baseline.surface_samples) > 0
    sample = result.baseline.surface_samples[0]
    assert sample.peak_positive_overpressure_pa > 0
    assert sample.peak_positive_overpressure_pa == max(sample.overpressure_pa)
    assert sample.uncertainty_upper_pa is None
    assert sample.perceived_level_db is None
    assert sample.reflection_factor == 2.0
    assert len(sample.ray_path_horizontal_m) == len(sample.ray_path_altitude_m)
    assert sample.ray_path_horizontal_m[0] == 0
    assert sample.ray_path_horizontal_m[-1] > 0
