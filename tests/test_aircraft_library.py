from __future__ import annotations

from open_mco.aircraft import (
    BLANK_AIRCRAFT_TEMPLATE_PATH,
    BUNDLED_LM1021_PATH,
    blank_aircraft_template_bytes,
    load_aircraft_definition_workbook,
    load_bundled_lm1021,
)


def test_bundled_lm1021_is_ready_for_research_route_analysis() -> None:
    aircraft = load_bundled_lm1021()

    assert BUNDLED_LM1021_PATH.is_file()
    assert aircraft.value("Aircraft Name") == "Lockheed Martin LM1021 N+2 QR4 1021-01"
    assert aircraft.missing_required_fields == ()
    assert len(aircraft.performance_map) == 4
    assert len(aircraft.phase_profile) == 10
    assert len(aircraft.nearfield_samples) == 3_725
    assert len(aircraft.benchmark_atmospheres) == 3


def test_blank_aircraft_template_matches_contract_without_invented_inputs() -> None:
    payload = blank_aircraft_template_bytes()
    aircraft = load_aircraft_definition_workbook(payload)

    assert BLANK_AIRCRAFT_TEMPLATE_PATH.is_file()
    assert aircraft.aircraft_id == "aircraft_one"
    assert aircraft.value("Aircraft Name") is None
    assert aircraft.missing_required_fields
    assert aircraft.performance_map == ()
    assert aircraft.phase_profile == ()
    assert aircraft.nearfield_samples == ()
