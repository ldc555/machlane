"""Phase-aware travel-time estimates using explicit aircraft and atmosphere inputs."""

from __future__ import annotations

import math
from statistics import fmean

from pydantic import BaseModel, ConfigDict, Field

from .specification import AircraftDefinition

MILES_PER_NAUTICAL_MILE = 1.150779448
MPS_TO_KNOTS = 1.943844492
AIR_GAMMA = 1.4
AIR_GAS_CONSTANT = 287.05287


class FrozenPlanModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class SceneEnvironment(FrozenPlanModel):
    sequence: int
    temperature_f: float
    pressure_inhg: float
    wind_speed_kt: float
    along_track_wind_kt: float
    planned_time_utc: str
    noaa_valid_time: str
    atmospheric_region: str


class SceneState(FrozenPlanModel):
    sequence: int
    phase: str
    altitude_ft: float
    mach: float
    true_airspeed_kt: float
    ground_speed_kt: float
    temperature_f: float
    pressure_inhg: float
    wind_speed_kt: float
    along_track_wind_kt: float
    planned_time_utc: str
    noaa_valid_time: str
    atmospheric_region: str


class FlightPhaseEstimate(FrozenPlanModel):
    phase: str
    duration_min: float
    distance_miles: float
    start_altitude_ft: float
    end_altitude_ft: float
    start_mach: float
    end_mach: float
    timing_basis: str


class FlightPlanEstimate(FrozenPlanModel):
    route_distance_miles: float = Field(gt=0)
    airborne_time_min: float
    block_time_min: float
    cruise_time_min: float
    cruise_distance_miles: float
    scenes: tuple[SceneState, ...]
    phases: tuple[FlightPhaseEstimate, ...]
    limitations: tuple[str, ...]


def planned_state_at_progress(
    progress: float,
    aircraft: AircraftDefinition,
    flight_plan: FlightPlanEstimate,
) -> dict[str, float | str]:
    """Interpolate the declared phase envelope along route distance.

    This is a deterministic scheduling transform, not a flight-dynamics or performance solver.
    Keeping it in the aircraft domain lets the UI and external propagation request use the exact
    same Mach, altitude, phase, and elapsed-time state.
    """

    fraction = min(1.0, max(0.0, progress))
    if len(flight_plan.phases) != 4:
        raise ValueError("continuous state requires climb, cruise, descent, and approach")
    climb, cruise, descent, approach = flight_plan.phases
    total_distance = sum(phase.distance_miles for phase in flight_plan.phases)
    if total_distance <= 0:
        raise ValueError("flight-plan phase distance must be positive")
    climb_end = climb.distance_miles / total_distance
    cruise_end = climb_end + cruise.distance_miles / total_distance
    descent_end = cruise_end + descent.distance_miles / total_distance
    cruise_indices = [
        index for index, point in enumerate(aircraft.phase_profile) if "cruise" in point.phase.lower()
    ]
    approach_indices = [
        index for index, point in enumerate(aircraft.phase_profile) if "approach" in point.phase.lower()
    ]
    if not cruise_indices or not approach_indices:
        raise ValueError("aircraft profile requires explicit cruise and approach points")
    cruise_index = cruise_indices[-1]
    approach_index = approach_indices[-1]
    if approach_index <= cruise_index:
        raise ValueError("aircraft approach point must follow its cruise point")

    def blend(start: float, end: float, amount: float) -> float:
        return start + (end - start) * min(1.0, max(0.0, amount))

    if fraction <= climb_end:
        climb_points = aircraft.phase_profile[: cruise_index + 1]
        if len(climb_points) < 2:
            raise ValueError("aircraft profile requires at least two climb points")
        scaled = fraction / max(climb_end, 1e-9) * (len(climb_points) - 1)
        left_index = min(len(climb_points) - 2, math.floor(scaled))
        local = scaled - left_index
        left = climb_points[left_index]
        right = climb_points[left_index + 1]
        phase = left.phase if fraction == 0 else right.phase
        altitude_ft = blend(left.altitude_ft, right.altitude_ft, local)
        mach = blend(left.mach, right.mach, local)
        elapsed_min = climb.duration_min * fraction / max(climb_end, 1e-9)
    elif fraction <= cruise_end:
        local = (fraction - climb_end) / max(cruise_end - climb_end, 1e-9)
        phase = cruise.phase
        altitude_ft = cruise.start_altitude_ft
        mach = cruise.start_mach
        elapsed_min = climb.duration_min + cruise.duration_min * local
    elif fraction <= descent_end:
        local = (fraction - cruise_end) / max(descent_end - cruise_end, 1e-9)
        descent_points = aircraft.phase_profile[cruise_index : approach_index + 1]
        scaled = local * (len(descent_points) - 1)
        left_index = min(len(descent_points) - 2, math.floor(scaled))
        point_fraction = scaled - left_index
        left = descent_points[left_index]
        right = descent_points[left_index + 1]
        phase = descent.phase if local == 0 else right.phase
        altitude_ft = blend(left.altitude_ft, right.altitude_ft, point_fraction)
        mach = blend(left.mach, right.mach, point_fraction)
        elapsed_min = climb.duration_min + cruise.duration_min + descent.duration_min * local
    else:
        local = (fraction - descent_end) / max(1.0 - descent_end, 1e-9)
        phase = approach.phase
        altitude_ft = blend(approach.start_altitude_ft, approach.end_altitude_ft, local)
        mach = blend(approach.start_mach, approach.end_mach, local)
        elapsed_min = (
            climb.duration_min
            + cruise.duration_min
            + descent.duration_min
            + approach.duration_min * local
        )

    return {
        "phase": phase,
        "altitude_ft": altitude_ft,
        "mach": mach,
        "elapsed_min": elapsed_min,
    }


def speed_of_sound_knots(temperature_f: float) -> float:
    temperature_k = (temperature_f - 32.0) * 5.0 / 9.0 + 273.15
    if temperature_k <= 0:
        raise ValueError("atmospheric temperature must be above absolute zero")
    return math.sqrt(AIR_GAMMA * AIR_GAS_CONSTANT * temperature_k) * MPS_TO_KNOTS


def estimate_flight_plan(
    aircraft: AircraftDefinition,
    route_distance_miles: float,
    environments: tuple[SceneEnvironment, ...],
) -> FlightPlanEstimate:
    """Estimate phase timing without presenting the result as certified performance.

    Aircraft phase Mach/altitude points and durations are explicit inputs. NOAA temperature
    supplies local speed of sound; route-aligned wind supplies ground-speed adjustment.
    """

    environment_by_sequence = {item.sequence: item for item in environments}
    if set(environment_by_sequence) != {point.sequence for point in aircraft.phase_profile}:
        raise ValueError("every aircraft phase point requires a matched NOAA environment")
    scenes: list[SceneState] = []
    for point in aircraft.phase_profile:
        environment = environment_by_sequence[point.sequence]
        true_airspeed = point.mach * speed_of_sound_knots(environment.temperature_f)
        scenes.append(
            SceneState(
                sequence=point.sequence,
                phase=point.phase,
                altitude_ft=point.altitude_ft,
                mach=point.mach,
                true_airspeed_kt=true_airspeed,
                ground_speed_kt=max(1.0, true_airspeed + environment.along_track_wind_kt),
                temperature_f=environment.temperature_f,
                pressure_inhg=environment.pressure_inhg,
                wind_speed_kt=environment.wind_speed_kt,
                along_track_wind_kt=environment.along_track_wind_kt,
                planned_time_utc=environment.planned_time_utc,
                noaa_valid_time=environment.noaa_valid_time,
                atmospheric_region=environment.atmospheric_region,
            )
        )
    timing = {item.phase: item for item in aircraft.phase_timing}
    if any(
        item.duration_min is None
        for item in (timing["climb_acceleration"], timing["descent"], timing["approach"])
    ):
        raise ValueError("climb, descent, and approach durations must be populated")

    cruise_indices = [
        index for index, scene in enumerate(scenes) if "cruise" in scene.phase.lower()
    ]
    approach_indices = [
        index for index, scene in enumerate(scenes) if "approach" in scene.phase.lower()
    ]
    if not cruise_indices or not approach_indices:
        raise ValueError("aircraft profile requires explicit cruise and approach points")
    cruise_index = cruise_indices[-1]
    approach_index = approach_indices[-1]
    if approach_index <= cruise_index:
        raise ValueError("aircraft approach point must follow its cruise point")
    climb_scenes = scenes[: cruise_index + 1]
    cruise_scene = scenes[cruise_index]
    approach_scene = scenes[approach_index]
    descent_scenes = scenes[cruise_index:approach_index]
    climb_minutes = float(timing["climb_acceleration"].duration_min or 0)
    descent_minutes = float(timing["descent"].duration_min or 0)
    approach_minutes = float(timing["approach"].duration_min or 0)
    climb_nmi = fmean(item.ground_speed_kt for item in climb_scenes) * climb_minutes / 60
    descent_nmi = fmean(item.ground_speed_kt for item in descent_scenes) * descent_minutes / 60
    approach_nmi = approach_scene.ground_speed_kt * approach_minutes / 60
    route_nmi = route_distance_miles / MILES_PER_NAUTICAL_MILE
    cruise_nmi = route_nmi - climb_nmi - descent_nmi - approach_nmi
    if cruise_nmi <= 0:
        raise ValueError(
            "the selected phase durations consume the complete route; shorten the phase model"
        )
    cruise_minutes = cruise_nmi / cruise_scene.ground_speed_kt * 60
    airborne_minutes = climb_minutes + cruise_minutes + descent_minutes + approach_minutes
    taxi_out = float(timing["taxi_out"].duration_min or 0)
    taxi_in = float(timing["taxi_in"].duration_min or 0)
    phases = (
        FlightPhaseEstimate(
            phase="Climb and acceleration",
            duration_min=climb_minutes,
            distance_miles=climb_nmi * MILES_PER_NAUTICAL_MILE,
            start_altitude_ft=scenes[0].altitude_ft,
            end_altitude_ft=cruise_scene.altitude_ft,
            start_mach=scenes[0].mach,
            end_mach=cruise_scene.mach,
            timing_basis=timing["climb_acceleration"].basis,
        ),
        FlightPhaseEstimate(
            phase="Supersonic cruise",
            duration_min=cruise_minutes,
            distance_miles=cruise_nmi * MILES_PER_NAUTICAL_MILE,
            start_altitude_ft=cruise_scene.altitude_ft,
            end_altitude_ft=cruise_scene.altitude_ft,
            start_mach=cruise_scene.mach,
            end_mach=cruise_scene.mach,
            timing_basis="CALCULATED_FROM_NOAA",
        ),
        FlightPhaseEstimate(
            phase="Descent",
            duration_min=descent_minutes,
            distance_miles=descent_nmi * MILES_PER_NAUTICAL_MILE,
            start_altitude_ft=cruise_scene.altitude_ft,
            end_altitude_ft=approach_scene.altitude_ft,
            start_mach=cruise_scene.mach,
            end_mach=approach_scene.mach,
            timing_basis=timing["descent"].basis,
        ),
        FlightPhaseEstimate(
            phase="Approach and landing",
            duration_min=approach_minutes,
            distance_miles=approach_nmi * MILES_PER_NAUTICAL_MILE,
            start_altitude_ft=approach_scene.altitude_ft,
            end_altitude_ft=0,
            start_mach=approach_scene.mach,
            end_mach=0,
            timing_basis=timing["approach"].basis,
        ),
    )
    return FlightPlanEstimate(
        route_distance_miles=route_distance_miles,
        airborne_time_min=airborne_minutes,
        block_time_min=airborne_minutes + taxi_out + taxi_in,
        cruise_time_min=cruise_minutes,
        cruise_distance_miles=cruise_nmi * MILES_PER_NAUTICAL_MILE,
        scenes=tuple(scenes),
        phases=phases,
        limitations=(
            "Research trajectory estimate; not certified aircraft performance.",
            "The descent duration is an editable NASA N+2 proxy, not STCA-specific validation data.",
            f"Mach {cruise_scene.mach:.2f} cruise is a research scenario, not operational or regulatory approval.",
            "The flight-time estimate does not establish a sonic-boom footprint, compliant corridor, or route variation.",
        ),
    )
