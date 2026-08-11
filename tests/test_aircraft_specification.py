from __future__ import annotations

from open_mco.aircraft import (
    AircraftStore,
    PhasePoint,
    SceneEnvironment,
    estimate_flight_plan,
    nasa_stca_aircraft_1,
    speed_of_sound_knots,
)
from open_mco.ui.view_model import continuous_planned_state


def _environment_fixture():
    aircraft = nasa_stca_aircraft_1()
    return tuple(
        SceneEnvironment(
            sequence=point.sequence,
            temperature_f=-56.0,
            pressure_inhg=3.50,
            wind_speed_kt=30.0,
            along_track_wind_kt=10.0,
            planned_time_utc="2026-08-03T20:00:00+00:00",
            noaa_valid_time="2026-08-03T20:00:00+00:00",
            atmospheric_region=f"R{point.sequence:02d}",
        )
        for point in aircraft.phase_profile
    )


def test_nasa_aircraft_1_retains_blanks_and_sources() -> None:
    aircraft = nasa_stca_aircraft_1()

    assert aircraft.aircraft_id == "aircraft_one"
    assert aircraft.display_name == "Aircraft One"
    assert aircraft.numeric_value("Maximum Cruise Mach") == 1.4
    assert aircraft.numeric_value("Preferred Cruise Altitude") == 50_000
    assert aircraft.value("Maximum Operating Mach") is None
    assert aircraft.value("Service Ceiling") is None
    assert aircraft.phase_profile[-2].phase == "Supersonic cruise"
    assert aircraft.phase_profile[-2].altitude_ft == 50_000
    assert aircraft.phase_timing[3].basis == "NASA_N_PLUS_2_PROXY"
    assert "Maximum Operating Mach" in aircraft.missing_required_fields
    assert not aircraft.nearfield_ready


def test_aircraft_store_round_trip(tmp_path) -> None:
    store = AircraftStore(tmp_path / "aircraft")
    aircraft = nasa_stca_aircraft_1()

    path = store.save(aircraft)

    assert path.exists()
    assert store.load(aircraft.aircraft_id) == aircraft
    assert store.list() == (aircraft,)


def test_phase_plan_uses_temperature_and_wind() -> None:
    aircraft = nasa_stca_aircraft_1()
    baseline = estimate_flight_plan(aircraft, 1_209, _environment_fixture())
    headwind = tuple(
        item.model_copy(update={"along_track_wind_kt": -30.0}) for item in _environment_fixture()
    )
    slower = estimate_flight_plan(aircraft, 1_209, headwind)

    assert speed_of_sound_knots(-56.0) > 560
    assert baseline.cruise_distance_miles > 300
    assert baseline.block_time_min > baseline.airborne_time_min
    assert baseline.scenes[-2].mach == 1.4
    assert slower.block_time_min > baseline.block_time_min


def test_continuous_flight_state_spans_takeoff_cruise_and_landing() -> None:
    aircraft = nasa_stca_aircraft_1()
    plan = estimate_flight_plan(aircraft, 1_209, _environment_fixture())

    departure = continuous_planned_state(0, aircraft, plan)
    midpoint = continuous_planned_state(0.5, aircraft, plan)
    arrival = continuous_planned_state(1, aircraft, plan)

    assert departure["phase"] == "Takeoff"
    assert departure["altitude_ft"] == 0
    assert float(midpoint["mach"]) == 1.4
    assert midpoint["phase"] == "Supersonic cruise"
    assert arrival["phase"] == "Approach and landing"
    assert arrival["altitude_ft"] == 0
    assert arrival["mach"] == 0
    assert arrival["elapsed_min"] == plan.airborne_time_min


def test_explicit_cruise_is_not_assumed_to_be_penultimate_profile_row() -> None:
    aircraft = nasa_stca_aircraft_1()
    source = aircraft.phase_profile[0]
    phases = (
        PhasePoint(
            sequence=1,
            phase="Takeoff",
            altitude_ft=0,
            mach=0.3,
            source_name=source.source_name,
            source_url=source.source_url,
            page_figure=source.page_figure,
        ),
        PhasePoint(
            sequence=2,
            phase="Supersonic climb",
            altitude_ft=40_000,
            mach=1.2,
            source_name=source.source_name,
            source_url=source.source_url,
            page_figure=source.page_figure,
        ),
        PhasePoint(
            sequence=3,
            phase="Supersonic cruise",
            altitude_ft=55_000,
            mach=1.6,
            source_name=source.source_name,
            source_url=source.source_url,
            page_figure=source.page_figure,
        ),
        PhasePoint(
            sequence=4,
            phase="Descent",
            altitude_ft=20_000,
            mach=0.8,
            source_name=source.source_name,
            source_url=source.source_url,
            page_figure=source.page_figure,
        ),
        PhasePoint(
            sequence=5,
            phase="Approach",
            altitude_ft=5_000,
            mach=0.4,
            source_name=source.source_name,
            source_url=source.source_url,
            page_figure=source.page_figure,
        ),
    )
    aircraft = aircraft.model_copy(update={"phase_profile": phases})
    environments = tuple(
        SceneEnvironment(
            sequence=point.sequence,
            temperature_f=-56,
            pressure_inhg=3.5,
            wind_speed_kt=30,
            along_track_wind_kt=10,
            planned_time_utc="2026-08-03T20:00:00+00:00",
            noaa_valid_time="2026-08-03T20:00:00+00:00",
            atmospheric_region=f"R{point.sequence:02d}",
        )
        for point in phases
    )

    plan = estimate_flight_plan(aircraft, 1_209, environments)

    assert plan.phases[1].start_mach == 1.6
    assert plan.phases[1].start_altitude_ft == 55_000
    assert continuous_planned_state(0.5, aircraft, plan)["mach"] == 1.6


def test_phase_plan_requires_every_noaa_scene() -> None:
    aircraft = nasa_stca_aircraft_1()
    environments = _environment_fixture()[:-1]

    try:
        estimate_flight_plan(aircraft, 1_209, environments)
    except ValueError as exc:
        assert "every aircraft phase point" in str(exc)
    else:
        raise AssertionError("missing NOAA scene should fail closed")
