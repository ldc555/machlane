"""Real-data-only mission workspace for MachLane."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, TypeVar

import pandas as pd
import plotly.graph_objects as go
import pydeck as pdk
import streamlit as st
from plotly.subplots import make_subplots

from open_mco.aircraft import (
    AircraftDefinition,
    AircraftStore,
    FlightPlanEstimate,
    SceneEnvironment,
    estimate_flight_plan,
    speed_of_sound_knots,
)
from open_mco.mission_analysis import (
    RealMissionAnalysis,
    build_real_mission_analysis,
    planned_scene_atmospheres,
)
from open_mco.models import Route
from open_mco.physics import (
    ExternalRouteSolver,
    OpenResearchRouteSolver,
    PhysicalRouteAnalysis,
    ResearchSolverUnavailableError,
    build_physical_route_request,
    evidence_zip,
    footprint_geojson,
    load_physical_route_analysis,
    request_checksum,
    surface_sample_rows,
)
from open_mco.route import (
    AUTOMATIC_WEATHER_POLICY_VERSION,
    AUTOMATIC_WEATHER_SAMPLE_SPACING_M,
    OpenSkyObservedFlight,
    OpenSkyRouteCache,
    OpenSkyTrackProvider,
    get_mission,
    interpolate_position,
    list_missions,
    route_distance_m,
)
from open_mco.ui.view_model import (
    METERS_TO_FEET,
    METERS_TO_MILES,
    PASCALS_TO_INHG,
    active_segment_index,
    aircraft_view,
    atmosphere_metrics,
    continuous_planned_state,
    display_longitude,
    pressure_color,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
OPENSKY_CACHE = OpenSkyRouteCache(PROJECT_ROOT / "data/cache/opensky_routes")
OPENSKY_LOOKBACK_DAYS = 7
ANALYSIS_CACHE_SCHEMA = "real-spacetime-noaa-3dep-v2-sparse-preview"
AIRCRAFT_STORE = AircraftStore(PROJECT_ROOT / "data/local/aircraft")
WORKSPACE_MISSION_IDS = ("dfw_jfk", "lax_jfk")
FAA_NPRM_RESEARCH_LIMIT_PSF = 0.11
PASCALS_PER_PSF = 47.88025898033584

_FragmentFunc = TypeVar("_FragmentFunc", bound=Callable[..., Any])


def _identity_fragment(func: _FragmentFunc) -> _FragmentFunc:
    return func


fragment: Any = (
    getattr(st, "fragment", None)
    or getattr(st, "experimental_fragment", None)
    or _identity_fragment
)


def _credential(name: str) -> str | None:
    value = os.getenv(name)
    if value:
        return value
    try:
        secret = st.secrets.get(name)
    except (FileNotFoundError, KeyError):
        return None
    return str(secret) if secret else None


def _roll_label(value: float | None) -> str:
    """Format near-field launch roll while remaining compatible with older result files."""

    return "roll n/a" if value is None else f"roll {value:+.0f}°"


@st.cache_resource(show_spinner=False)
def _analyze_real_route(
    mission_id: str,
    route_json: str,
    cache_schema: str,
) -> RealMissionAnalysis:
    """Build one fail-closed real mission and cache its decoded model grids."""

    del cache_schema
    route = Route.model_validate_json(route_json)
    mission = get_mission(mission_id)
    return build_real_mission_analysis(
        route,
        mission.domain,
        weather_cache_dir=PROJECT_ROOT / "data/cache/herbie",
        terrain_cache_dir=PROJECT_ROOT / "data/cache/3dep",
        network_enabled=True,
    )


@st.cache_resource(show_spinner=False)
def _load_planned_scene_atmospheres(
    mission_id: str,
    route_json: str,
    points_json: str,
    times_json: str,
    cache_schema: str,
) -> tuple[Any, Any]:
    """Cache the second-pass NOAA match for the faster planned trajectory."""

    del cache_schema
    route = Route.model_validate_json(route_json)
    mission = get_mission(mission_id)
    raw_points = json.loads(points_json)
    raw_times = json.loads(times_json)
    points = tuple((float(point[0]), float(point[1])) for point in raw_points)
    sample_times = tuple(datetime.fromisoformat(value) for value in raw_times)
    return planned_scene_atmospheres(
        route,
        mission.domain,
        points,
        sample_times,
        weather_cache_dir=PROJECT_ROOT / "data/cache/herbie",
        network_enabled=True,
    )


def _load_or_fetch_route(
    mission_id: str,
    flight_json: str,
    *,
    force_refresh: bool,
) -> Route:
    mission = get_mission(mission_id)
    flight = OpenSkyObservedFlight.model_validate_json(flight_json)
    observed_date = flight.first_seen.date()
    if not force_refresh:
        cached = OPENSKY_CACHE.load(
            mission_id,
            observed_date,
            origin_icao=mission.origin.icao,
            destination_icao=mission.destination.icao,
        )
        if (
            cached is not None
            and cached.source is not None
            and cached.source.flight_id == flight.flight_id
        ):
            return cached
    provider = OpenSkyTrackProvider(
        network_enabled=True,
        client_id=_credential("OPENSKY_CLIENT_ID"),
        client_secret=_credential("OPENSKY_CLIENT_SECRET"),
    )
    route = provider.route_for_observed_flight(
        mission.origin,
        mission.destination,
        flight,
    )
    OPENSKY_CACHE.save(mission_id, observed_date, route)
    return route


@st.cache_data(show_spinner=False, ttl=900)
def _available_observed_flights(
    mission_id: str,
    ending_date_iso: str,
    lookback_days: int,
) -> tuple[str, ...]:
    """Return only actual OpenSky flights for the mission's recent UTC dates."""

    mission = get_mission(mission_id)
    ending_date = date.fromisoformat(ending_date_iso)
    end = datetime.combine(ending_date + timedelta(days=1), time.min, tzinfo=UTC)
    begin = end - timedelta(days=lookback_days)
    provider = OpenSkyTrackProvider(
        network_enabled=True,
        client_id=_credential("OPENSKY_CLIENT_ID"),
        client_secret=_credential("OPENSKY_CLIENT_SECRET"),
    )
    flights = provider.observed_flights_for_airports(
        mission.origin,
        mission.destination,
        begin=begin,
        end=end - timedelta(seconds=1),
    )
    latest_by_day: dict[date, OpenSkyObservedFlight] = {}
    for flight in flights:
        flight_date = flight.first_seen.date()
        current = latest_by_day.get(flight_date)
        if current is None or flight.first_seen > current.first_seen:
            latest_by_day[flight_date] = flight
    return tuple(
        flight.model_dump_json()
        for flight in sorted(
            latest_by_day.values(), key=lambda item: item.first_seen, reverse=True
        )
    )


def _cached_observed_flights(
    mission_id: str,
    ending_date: date,
    lookback_days: int,
) -> tuple[OpenSkyObservedFlight, ...]:
    """Recover selectable real-flight identities from validated private route caches."""

    mission = get_mission(mission_id)
    flights: dict[str, OpenSkyObservedFlight] = {}
    for offset in range(lookback_days):
        observed_date = ending_date - timedelta(days=offset)
        route = OPENSKY_CACHE.load(
            mission_id,
            observed_date,
            origin_icao=mission.origin.icao,
            destination_icao=mission.destination.icao,
        )
        source = None if route is None else route.source
        if (
            source is None
            or source.flight_id is None
            or source.observed_start is None
            or source.observed_end is None
            or ":" not in source.flight_id
        ):
            continue
        icao24, first_seen_raw = source.flight_id.split(":", 1)
        try:
            first_seen = datetime.fromtimestamp(int(first_seen_raw), tz=UTC)
            flight = OpenSkyObservedFlight(
                icao24=icao24,
                callsign=source.callsign,
                first_seen=first_seen,
                last_seen=source.observed_end,
                origin_icao=mission.origin.icao,
                destination_icao=mission.destination.icao,
            )
        except (ValueError, OSError):
            continue
        flights[flight.flight_id] = flight
    return tuple(sorted(flights.values(), key=lambda item: item.first_seen, reverse=True))


def _imperial_boundary_reason(reason: str) -> str:
    match = re.search(r"([0-9.]+) (K|hPa|m/s)$", reason)
    if match is None:
        if "Weather-model valid time changed" in reason:
            start, end = reason.rsplit(" from ", 1)[-1].split(" to ", 1)
            return f"NOAA valid time changed · {start[11:16]} → {end[11:16]} UTC"
        if "Weather-model cycle changed" in reason:
            return "NOAA model cycle changed"
        return reason
    value = float(match.group(1))
    unit = match.group(2)
    replacement = {
        "K": f"{value * 1.8:.1f} °F",
        "hPa": f"{value * 100 * PASCALS_TO_INHG:.3f} inHg",
        "m/s": f"{value * 1.943844492:.1f} kt",
    }[unit]
    return f"{reason[: match.start(1)]}{replacement}"


def _region_rows(analysis: RealMissionAnalysis) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    elapsed_m = 0.0
    _, longitude_reference = interpolate_position(analysis.atmospheric_route, 0.5)
    terrain_by_id = {item.segment_id: item for item in analysis.terrain_regions}
    for segment, regime in zip(
        analysis.atmospheric_route.segments,
        analysis.weather_regimes,
        strict=True,
    ):
        start_m = elapsed_m
        elapsed_m += segment.distance_m
        terrain = terrain_by_id[segment.segment_id]
        rows.append(
            {
                "region": segment.segment_id,
                "start_mi": start_m * METERS_TO_MILES,
                "end_mi": elapsed_m * METERS_TO_MILES,
                "length_mi": segment.distance_m * METERS_TO_MILES,
                "path": [
                    [display_longitude(longitude, longitude_reference), latitude]
                    for latitude, longitude in segment.path
                ],
                "bearing_deg": segment.bearing_deg,
                "boundary_reason": _imperial_boundary_reason(regime.boundary_reason),
                "samples": regime.sample_count,
                "pressure_hpa": regime.pressure_hpa,
                "pressure_inhg": regime.pressure_hpa * 100 * PASCALS_TO_INHG,
                "temperature_f": (regime.temperature_k - 273.15) * 9 / 5 + 32,
                "wind_kt": regime.wind_speed_mps * 1.943844492,
                "sample_start_utc": regime.sample_start_time.isoformat(),
                "sample_end_utc": regime.sample_end_time.isoformat(),
                "model_valid_start_utc": regime.model_valid_start.isoformat(),
                "model_valid_end_utc": regime.model_valid_end.isoformat(),
                "model_cycles_utc": ", ".join(value.isoformat() for value in regime.model_cycles),
                "provider": ", ".join(regime.providers),
                "terrain": terrain.status,
                "terrain_reason": terrain.reason,
                "surface_boom": "NOT CALCULATED",
            }
        )
    return rows


def _evidence_payload(
    mission_id: str,
    analysis: RealMissionAnalysis,
    rows: list[dict[str, Any]],
    aircraft: AircraftDefinition,
    flight_plan: FlightPlanEstimate,
    planned_requests: tuple[Any, ...],
) -> dict[str, Any]:
    terrain = []
    for result in analysis.terrain_regions:
        terrain.append(
            {
                "segment_id": result.segment_id,
                "status": result.status,
                "reason": result.reason,
                "source": None
                if result.profile is None
                else result.profile.source.model_dump(mode="json"),
            }
        )
    return {
        "schema": "machlane-real-input-evidence-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "mission_id": mission_id,
        "route": analysis.observed_route.model_dump(mode="json"),
        "atmosphere": {
            "model": analysis.noaa_model,
            "coverage": analysis.noaa_coverage,
            "requests": [
                {
                    "model": plan.model,
                    "cycle": plan.model_cycle.isoformat(),
                    "forecast_hour": plan.forecast_hour,
                    "valid_time": plan.valid_time.isoformat(),
                    "member": plan.member,
                    "temporal_match": plan.temporal_match,
                }
                for plan in analysis.noaa_requests
            ],
            "profiles": [
                profile.source.model_dump(mode="json") for profile in analysis.segment_atmospheres
            ],
        },
        "terrain": terrain,
        "segmentation": {
            "policy": analysis.policy_version,
            "maximum_sampling_interval_miles": 15,
            "temperature_threshold_f": 1,
            "ambient_pressure_threshold_inhg": 0.02,
            "wind_vector_threshold_kt": 3,
            "regions": [
                {key: value for key, value in row.items() if key not in {"path", "color"}}
                for row in rows
            ],
        },
        "planned_route": {
            "status": "RESEARCH ESTIMATE",
            "geometry_basis": "Exact OpenSky observed baseline polyline",
            "aircraft": aircraft.model_dump(mode="json"),
            "phase_plan": flight_plan.model_dump(mode="json"),
            "noaa_requests": [
                {
                    "model": plan.model,
                    "cycle": plan.model_cycle.isoformat(),
                    "forecast_hour": plan.forecast_hour,
                    "valid_time": plan.valid_time.isoformat(),
                    "member": plan.member,
                    "temporal_match": plan.temporal_match,
                }
                for plan in planned_requests
            ],
        },
        "compliant_operating_corridor": {
            "status": "NOT CALCULATED",
            "reason": "Reviewed near-field signature and validated propagation engine required",
        },
        "sonic_boom": {
            "status": "NOT CALCULATED",
            "ground_overpressure": None,
            "primary_rays": "NOT CALCULATED",
            "secondary_direct_rays": "NOT CALCULATED",
            "secondary_indirect_rays": "NOT CALCULATED",
        },
    }


st.set_page_config(
    page_title="MachLane · Real mission workspace",
    page_icon="✦",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
<style>
:root { --ink:#f5fbff; --muted:#b7cbe0; --panel:#081728; --line:#31506e; --cyan:#30d5ff; --teal:#2df0cf; --amber:#ffd447; }
.stApp {
  background-color:#040b14;
  background-image:linear-gradient(rgba(46,119,166,.045) 1px,transparent 1px),linear-gradient(90deg,rgba(46,119,166,.045) 1px,transparent 1px),radial-gradient(circle at 48% -18%,#16466e 0,#092036 31%,#050e19 62%,#030810 100%);
  background-size:28px 28px,28px 28px,auto;
  color:var(--ink);
}
[data-testid="stHeader"] { background:transparent; }
[data-testid="stToolbar"],[data-testid="stStatusWidget"] { display:none; }
.block-container { max-width:1600px; padding:1rem 1.5rem 3rem; }
.brand-row { display:flex; justify-content:space-between; align-items:center; margin:.1rem 0 .75rem; }
.brand { display:flex; align-items:center; gap:.7rem; color:#fff; font-weight:850; letter-spacing:.01em; }
.brand-mark { width:2rem; height:2rem; display:grid; place-items:center; border:1px solid #2ecaf1; border-radius:.4rem; color:#071522; background:var(--cyan); box-shadow:0 0 18px rgba(48,213,255,.22); }
.run-state { color:#8de9ff; font:750 .7rem/1 ui-monospace,SFMono-Regular,monospace; letter-spacing:.1em; }
.notice { border:1px solid #815d2e; background:#2a2014; color:#ffdda4; padding:.55rem .8rem; border-radius:.6rem; font-size:.78rem; margin-bottom:.8rem; }
.empty-state { max-width:760px; margin:8vh auto 1.2rem; padding:2.2rem; text-align:center; border:1px solid #3c6d8e; border-radius:.85rem; background:linear-gradient(145deg,#102a43,#081523); box-shadow:0 22px 60px rgba(0,0,0,.35); }
.empty-state .eyebrow { color:#7be5ff; font:800 .68rem/1 ui-monospace,SFMono-Regular,monospace; letter-spacing:.14em; }
.empty-state h2 { color:#fff; margin:.55rem 0; }
.empty-state p { color:#c7dbea; margin:0 auto; max-width:620px; line-height:1.55; }
.source-banner { border:1px solid #25655f; background:linear-gradient(90deg,#123936,#0e1d29); color:#d5fff8; padding:.65rem .8rem; border-radius:.6rem; font-size:.78rem; margin:.5rem 0 .75rem; }
.section-kicker { color:#72ddff; font:750 .68rem/1.2 ui-monospace,SFMono-Regular,monospace; letter-spacing:.12em; text-transform:uppercase; margin:.3rem 0 .65rem; }
.status-card { border:1px solid #365877; background:linear-gradient(145deg,#0d2238,#071522); border-radius:.55rem; padding:.8rem .9rem; margin:.35rem 0 .65rem; box-shadow:inset 0 1px rgba(255,255,255,.025); }
.status-card .label { color:#9fc8e4; font-size:.66rem; font-weight:750; letter-spacing:.1em; text-transform:uppercase; }
.status-card .value { color:#f4f8fc; font-size:1.05rem; font-weight:800; margin:.18rem 0; }
.status-card .meta { color:#c2d5e5; font-size:.73rem; line-height:1.42; }
.instrument-panel { border:1px solid #38bde3; background:linear-gradient(155deg,rgba(13,47,75,.97),rgba(4,15,27,.99)); border-radius:.55rem; padding:.85rem; margin:.1rem 0 .75rem; box-shadow:0 0 0 1px rgba(48,213,255,.08),0 12px 30px rgba(0,0,0,.22); }
.instrument-head { display:flex; align-items:center; justify-content:space-between; gap:.5rem; padding-bottom:.65rem; border-bottom:1px solid #294c68; }
.instrument-title { color:#8ee9ff; font:800 .66rem/1 ui-monospace,SFMono-Regular,monospace; letter-spacing:.12em; }
.phase-chip { max-width:66%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:#06121d; background:var(--amber); border-radius:.25rem; padding:.28rem .42rem; font:850 .64rem/1 ui-monospace,SFMono-Regular,monospace; text-transform:uppercase; }
.instrument-grid { display:grid; grid-template-columns:1fr 1fr; gap:1px; margin-top:.7rem; background:#294c68; border:1px solid #294c68; }
.instrument { min-width:0; background:#071725; padding:.62rem .65rem; }
.instrument .label { display:block; color:#96bdd7; font:750 .58rem/1.2 ui-monospace,SFMono-Regular,monospace; letter-spacing:.08em; }
.instrument .reading { color:#fff; font:850 1.28rem/1.15 ui-monospace,SFMono-Regular,monospace; letter-spacing:-.04em; }
.instrument.primary .reading { color:var(--cyan); font-size:1.5rem; text-shadow:0 0 12px rgba(48,213,255,.18); }
.instrument .unit { color:#9fc7df; font:750 .62rem/1 ui-monospace,SFMono-Regular,monospace; margin-left:.2rem; }
.instrument-meta { color:#bcd0e1; font:650 .66rem/1.45 ui-monospace,SFMono-Regular,monospace; margin-top:.65rem; }
.phase-readout { display:flex; justify-content:space-between; align-items:center; border:1px solid #315a79; background:#081a2b; color:#dff7ff; padding:.48rem .65rem; border-radius:.4rem; margin:-.15rem 0 .45rem; font:700 .7rem/1.2 ui-monospace,SFMono-Regular,monospace; }
.phase-readout b { color:var(--amber); }
.zone-legend { border-left:3px solid var(--cyan); background:#071725; color:#c4d9e8; padding:.5rem .65rem; margin:.35rem 0 .55rem; font-size:.72rem; }
.layer-grid { display:grid; grid-template-columns:repeat(2,1fr); gap:.6rem; margin:.6rem 0 .8rem; }
.layer { border:1px solid var(--line); background:#0c1520; border-radius:.65rem; padding:.75rem; }
.layer b { display:block; color:#e9f1f8; font-size:.8rem; margin-bottom:.25rem; }
.layer span { color:#94a9bd; font-size:.72rem; line-height:1.4; }
.pending { display:inline-block; padding:.24rem .46rem; color:#ffd38a; background:#3a2b16; border:1px solid #795a28; border-radius:99px; font:700 .65rem/1 ui-monospace,SFMono-Regular,monospace; }
[data-testid="stMetric"] { background:linear-gradient(145deg,#102b45,#071625); border:1px solid #3b6a8d; padding:.72rem .82rem; border-radius:.5rem; }
[data-testid="stMetricLabel"],[data-testid="stMetricLabel"] * { color:#d8e5f2 !important; font-weight:700 !important; }
[data-testid="stMetricValue"],[data-testid="stMetricValue"] * { color:#fff !important; font:850 1.28rem/1.2 ui-monospace,SFMono-Regular,monospace !important; }
[data-testid="stMetricDelta"],[data-testid="stMetricDelta"] * { color:#c2d2e3 !important; opacity:1 !important; }
.stTabs [data-baseweb="tab-list"] { gap:.35rem; border-bottom:1px solid var(--line); }
.stTabs [data-baseweb="tab"] { color:#b5cce0; }
.stTabs [aria-selected="true"] { color:#fff; background:#0c2840; }
.stButton button { border:1px solid #2f6e69; background:#123a38; color:#d8fffa; }
[data-testid="stDownloadButton"] button { background:#dff7ff !important; border:1px solid #67d9f5 !important; }
[data-testid="stDownloadButton"] button,[data-testid="stDownloadButton"] button * { color:#04111d !important; font-weight:800 !important; opacity:1 !important; }
[data-testid="stDownloadButton"] button:hover { background:#ffffff !important; border-color:#b9f2ff !important; }
[data-testid="stPageLink"] a { min-height:3.2rem; justify-content:center; border:1px solid #39c9ef !important; border-radius:.5rem; background:linear-gradient(135deg,#0d5f83,#0a334e) !important; color:#fff !important; font-size:.92rem !important; font-weight:850 !important; box-shadow:0 0 20px rgba(48,213,255,.16); }
[data-testid="stPageLink"] a:hover { border-color:#8ceaff !important; background:linear-gradient(135deg,#11739b,#0c4262) !important; }
[data-testid="stWidgetLabel"],[data-testid="stWidgetLabel"] * { color:#e6f4ff !important; font-weight:700 !important; }
[data-baseweb="radio"] label,[data-baseweb="checkbox"] label { color:#e8f6ff !important; }
[data-baseweb="slider"] [role="slider"] { background:var(--amber) !important; border-color:#fff !important; box-shadow:0 0 0 3px rgba(255,212,71,.14); }
[data-baseweb="slider"] > div > div { background-color:#38c8ee !important; }
.stCaption,[data-testid="stCaptionContainer"],[data-testid="stCaptionContainer"] * { color:#b5cadb !important; }
h1,h2,h3,h4,p,li { color:var(--ink); }
@media (max-width:900px) { .layer-grid { grid-template-columns:1fr; } .block-container { padding:.8rem; } }
</style>
""",
    unsafe_allow_html=True,
)

st.markdown(
    """
<div class="brand-row"><div class="brand"><span class="brand-mark">M</span>MachLane</div><div class="run-state">REAL MISSION INPUTS</div></div>
<div class="notice"><b>Research prototype — not FAA approved.</b> The built-in solver reports unvalidated primary-ray estimates; validated external results remain separately identified. Ambient weather alone never produces a surface-boom or compliance determination.</div>
""",
    unsafe_allow_html=True,
)

navigation_left, navigation_right = st.columns([4.6, 1.4], vertical_alignment="center")
stored_aircraft = AIRCRAFT_STORE.load("aircraft_one")
stored_aircraft_checksum = (
    None
    if stored_aircraft is None
    else stored_aircraft.workbook_checksum or f"revision-{stored_aircraft.revision}"
)
selected_aircraft = (
    stored_aircraft
    if st.session_state.get("active_aircraft_checksum") == stored_aircraft_checksum
    else None
)
with navigation_left:
    st.caption("Real OpenSky route geometry · source-backed aircraft · automatic NOAA matching")
if selected_aircraft is not None:
    with navigation_right:
        st.page_link(
            "pages/1_Aircraft_Specificity.py",
            label="CHANGE AIRCRAFT →",
            width="stretch",
        )

if selected_aircraft is None:
    st.markdown(
        """
<div class="empty-state">
  <div class="eyebrow">FIRST STEP</div>
  <h2>Load one aircraft workbook</h2>
  <p>MachLane will validate it, populate the aircraft workspace, and save a normalized local copy before any route analysis can run.</p>
</div>
""",
        unsafe_allow_html=True,
    )
    _, load_aircraft, _ = st.columns([1.35, 1, 1.35])
    with load_aircraft:
        st.page_link(
            "pages/1_Aircraft_Specificity.py",
            label="LOAD AIRCRAFT",
            width="stretch",
        )
    st.stop()

assert selected_aircraft is not None

workspace_missions = {
    item.mission_id: item for item in list_missions() if item.mission_id in WORKSPACE_MISSION_IDS
}
control_mission, control_flight, control_aircraft, control_action = st.columns(
    [1.2, 1.8, 1.45, 1], vertical_alignment="bottom"
)
with control_mission:
    mission_id = st.selectbox(
        "Mission",
        list(workspace_missions),
        format_func=lambda value: workspace_missions[value].label,
        help="Only airport pairs backed by a matching real OpenSky observation can run.",
    )
mission = get_mission(mission_id)
yesterday = datetime.now(UTC).date() - timedelta(days=1)
credentials_configured = bool(
    _credential("OPENSKY_CLIENT_ID") and _credential("OPENSKY_CLIENT_SECRET")
)
cached_flights = _cached_observed_flights(
    mission_id,
    yesterday,
    OPENSKY_LOOKBACK_DAYS,
)
discovery_error: str | None = None
network_flights: tuple[OpenSkyObservedFlight, ...] = ()
if credentials_configured:
    try:
        with st.spinner("Finding actual recent OpenSky flights for this airport pair…"):
            network_flights = tuple(
                OpenSkyObservedFlight.model_validate_json(payload)
                for payload in _available_observed_flights(
                    mission_id,
                    yesterday.isoformat(),
                    OPENSKY_LOOKBACK_DAYS,
                )
            )
    except (RuntimeError, ValueError, OSError) as exc:
        discovery_error = str(exc)

available_by_id = {
    flight.flight_id: flight for flight in (*network_flights, *cached_flights)
}
available_flights = tuple(
    sorted(available_by_id.values(), key=lambda item: item.first_seen, reverse=True)
)
if not available_flights:
    reason = (
        discovery_error
        or "OpenSky credentials are not configured and no real observed flight is cached"
    )
    st.error(f"No selectable OpenSky flight is available: {reason}.")
    st.caption(
        "MachLane stopped before NOAA or terrain processing. It did not substitute another date or invent a route."
    )
    st.stop()

flight_by_id = {flight.flight_id: flight for flight in available_flights}
with control_flight:
    selected_flight_id = st.selectbox(
        "Observed OpenSky flight",
        list(flight_by_id),
        format_func=lambda value: (
            f"{flight_by_id[value].callsign or flight_by_id[value].icao24.upper()} · "
            f"{flight_by_id[value].first_seen:%b %d, %Y · %H:%M UTC}"
        ),
        help="Only real flights returned by OpenSky or a checksum-validated private cache appear here.",
    )
selected_flight = flight_by_id[selected_flight_id]
selected_flight_json = selected_flight.model_dump_json()
observed_date = selected_flight.first_seen.date()
with control_aircraft:
    st.text_input(
        "Aircraft",
        value=(
            "No aircraft loaded"
            if selected_aircraft is None
            else selected_aircraft.value("Aircraft Name") or selected_aircraft.display_name
        ),
        disabled=True,
        help="Drop and activate a NASA STCA or Boom/XB-1 Excel file on the Aircraft page.",
    )

if not selected_aircraft.phase_profile_ready:
    with control_action:
        st.page_link(
            "pages/1_Aircraft_Specificity.py",
            label="COMPLETE AIRCRAFT",
            width="stretch",
        )
    st.error(
        "The aircraft is saved, but route modeling is locked until its phase profile and climb, descent, and approach timing are complete."
    )
    st.stop()

cached_route = OPENSKY_CACHE.load(
    mission_id,
    observed_date,
    origin_icao=mission.origin.icao,
    destination_icao=mission.destination.icao,
)
cached_route_matches_flight = bool(
    cached_route is not None
    and cached_route.source is not None
    and cached_route.source.flight_id == selected_flight.flight_id
)
can_run = cached_route_matches_flight or credentials_configured
with control_action:
    run_analysis = st.button("Run analysis", disabled=not can_run, width="stretch")

if cached_route_matches_flight and cached_route is not None:
    source = cached_route.source
    retrieved = "unknown" if source is None else source.retrieved_at.strftime("%Y-%m-%d %H:%M UTC")
    st.caption(
        f"Real OpenSky route is available in the private cache · retrieved {retrieved}. Refresh requires OpenSky credentials."
    )
elif not credentials_configured:
    st.error(
        "OpenSky route unavailable: no matching real route is cached and OPENSKY_CLIENT_ID / OPENSKY_CLIENT_SECRET are not configured."
    )
    st.stop()

flight_state_id = hashlib.sha256(selected_flight.flight_id.encode()).hexdigest()[:12]
route_state_key = f"real-route:{mission_id}:{observed_date.isoformat()}:{flight_state_id}"
if run_analysis:
    try:
        with st.spinner("Loading the real OpenSky track…"):
            route = _load_or_fetch_route(
                mission_id,
                selected_flight_json,
                force_refresh=False,
            )
        st.session_state[route_state_key] = route.model_dump_json()
    except (RuntimeError, ValueError, OSError) as exc:
        st.error(f"OpenSky route unavailable: {exc}")
        st.stop()

route_json = st.session_state.get(route_state_key)
if not isinstance(route_json, str):
    st.info(
        "Select an observed OpenSky flight and click **Run analysis**. NOAA and terrain will be matched only to that flight's real position and UTC timestamps."
    )
    st.stop()

observed_route = Route.model_validate_json(route_json)
source = observed_route.source
if source is None or source.provider != "opensky" or source.data_kind != "observed_track":
    st.error(
        "Route provenance failed: the loaded geometry is not a normalized OpenSky observation."
    )
    st.stop()

try:
    with st.spinner(
        "Matching archived NOAA weather across every route position and UTC time, then loading available 3DEP terrain…"
    ):
        analysis = _analyze_real_route(mission_id, route_json, ANALYSIS_CACHE_SCHEMA)
except (RuntimeError, ValueError, OSError) as exc:
    st.error(f"NOAA atmosphere unavailable: {exc}")
    st.info(
        "Analysis stopped. MachLane did not substitute synthetic weather, a standard atmosphere, or a different route."
    )
    st.stop()

rows = _region_rows(analysis)
pressure_values = [float(row["pressure_hpa"]) for row in rows]
pressure_min = min(pressure_values) - 0.5
pressure_max = max(pressure_values) + 0.5
for row in rows:
    row["color"] = pressure_color(float(row["pressure_hpa"]), pressure_min, pressure_max)
    row["zone_fill_color"] = [*row["color"][:3], 42]
    row["zone_edge_color"] = [*row["color"][:3], 238]

route_distance_miles = route_distance_m(observed_route) * METERS_TO_MILES
map_latitude, map_longitude = interpolate_position(observed_route, 0.5)
map_zoom = max(1.2, min(4.2, 4.9 - math.log2(max(route_distance_miles, 300) / 300)))
callsign = source.callsign or "unknown callsign"
observed_start = source.observed_start
observed_end = source.observed_end
if observed_start is None or observed_end is None:
    st.error("OpenSky route unavailable: the observation UTC window is missing.")
    st.stop()
observed_window = f"{observed_start:%Y-%m-%d %H:%M}–{observed_end:%H:%M} UTC"
point_count = source.point_count or len(observed_route.observations)
terrain_loaded = sum(result.status == "LOADED" for result in analysis.terrain_regions)
terrain_unavailable = sum(result.status == "UNAVAILABLE" for result in analysis.terrain_regions)

phase_progress: dict[int, float] = {}
cruise_profile_indices = [
    index
    for index, point in enumerate(selected_aircraft.phase_profile)
    if "cruise" in point.phase.lower()
]
approach_profile_indices = [
    index
    for index, point in enumerate(selected_aircraft.phase_profile)
    if "approach" in point.phase.lower()
]
if not cruise_profile_indices or not approach_profile_indices:
    st.error("Aircraft profile requires explicit cruise and approach points.")
    st.stop()
cruise_profile_index = cruise_profile_indices[-1]
approach_profile_index = approach_profile_indices[-1]
if approach_profile_index <= cruise_profile_index:
    st.error("Aircraft approach point must follow its cruise point.")
    st.stop()
for index, point in enumerate(selected_aircraft.phase_profile):
    if index < cruise_profile_index:
        phase_progress[point.sequence] = index / max(1, cruise_profile_index) * 0.38
    elif index == cruise_profile_index:
        phase_progress[point.sequence] = 0.5
    elif index < approach_profile_index:
        phase_progress[point.sequence] = 0.62 + (
            (index - cruise_profile_index)
            / max(1, approach_profile_index - cruise_profile_index)
            * 0.30
        )
    else:
        phase_progress[point.sequence] = 0.97

scene_environments: list[SceneEnvironment] = []
for point in selected_aircraft.phase_profile:
    progress = phase_progress[point.sequence]
    region_index = active_segment_index(analysis.atmospheric_route, progress)
    region = rows[region_index]
    profile = analysis.segment_atmospheres[region_index]
    scene_aircraft = aircraft_view(observed_route, progress, map_longitude)
    metrics = atmosphere_metrics(
        profile, point.altitude_ft / METERS_TO_FEET, scene_aircraft["bearing_deg"]
    )
    scene_environments.append(
        SceneEnvironment(
            sequence=point.sequence,
            temperature_f=float(metrics["temperature_f"]),
            pressure_inhg=float(metrics["pressure_inhg"]),
            wind_speed_kt=float(metrics["wind_speed_kt"]),
            along_track_wind_kt=float(metrics["along_wind_kt"]),
            planned_time_utc=profile.valid_time.isoformat(),
            noaa_valid_time=profile.valid_time.isoformat(),
            atmospheric_region=str(region["region"]),
        )
    )
try:
    preliminary_flight_plan = estimate_flight_plan(
        selected_aircraft,
        route_distance_miles,
        tuple(scene_environments),
    )
except ValueError as exc:
    st.error(f"Aircraft phase plan unavailable: {exc}")
    st.info("Open Aircraft Specificity, complete the missing phase data, and save the aircraft.")
    st.stop()

climb_phase, cruise_phase, descent_phase, approach_phase = preliminary_flight_plan.phases
climb_end_progress = climb_phase.distance_miles / route_distance_miles
cruise_mid_progress = (
    climb_phase.distance_miles + cruise_phase.distance_miles / 2
) / route_distance_miles
cruise_end_progress = (
    climb_phase.distance_miles + cruise_phase.distance_miles
) / route_distance_miles
descent_end_progress = (
    climb_phase.distance_miles + cruise_phase.distance_miles + descent_phase.distance_miles
) / route_distance_miles
approach_mid_progress = 1 - approach_phase.distance_miles / route_distance_miles / 2
ascent_points = selected_aircraft.phase_profile[:cruise_profile_index]
for index, point in enumerate(ascent_points):
    phase_progress[point.sequence] = climb_end_progress * index / max(1, len(ascent_points) - 1)
cruise_scene = selected_aircraft.phase_profile[cruise_profile_index]
phase_progress[cruise_scene.sequence] = cruise_mid_progress
descent_points = selected_aircraft.phase_profile[
    cruise_profile_index + 1 : approach_profile_index
]
for index, point in enumerate(descent_points, start=1):
    phase_progress[point.sequence] = cruise_end_progress + (
        descent_end_progress - cruise_end_progress
    ) * index / (len(descent_points) + 1)
approach_scene = selected_aircraft.phase_profile[approach_profile_index]
phase_progress[approach_scene.sequence] = approach_mid_progress

climb_minutes = climb_phase.duration_min
planned_scene_times: dict[int, datetime] = {}
for index, point in enumerate(ascent_points):
    elapsed_min = climb_minutes * index / max(1, len(ascent_points) - 1)
    planned_scene_times[point.sequence] = observed_start + timedelta(minutes=elapsed_min)
planned_scene_times[cruise_scene.sequence] = observed_start + timedelta(
    minutes=climb_minutes + preliminary_flight_plan.cruise_time_min / 2
)
for index, point in enumerate(descent_points, start=1):
    planned_scene_times[point.sequence] = observed_start + timedelta(
        minutes=(
            climb_minutes
            + preliminary_flight_plan.cruise_time_min
            + descent_phase.duration_min * index / (len(descent_points) + 1)
        )
    )
planned_scene_times[approach_scene.sequence] = observed_start + timedelta(
    minutes=preliminary_flight_plan.airborne_time_min - approach_phase.duration_min / 2
)
planned_scene_points = tuple(
    interpolate_position(observed_route, phase_progress[point.sequence])
    for point in selected_aircraft.phase_profile
)
planned_times = tuple(
    planned_scene_times[point.sequence] for point in selected_aircraft.phase_profile
)
try:
    with st.spinner("Matching NOAA again at the uploaded aircraft scene times…"):
        planned_profiles, planned_noaa_requests = _load_planned_scene_atmospheres(
            mission_id,
            route_json,
            json.dumps(planned_scene_points),
            json.dumps([value.isoformat() for value in planned_times]),
            ANALYSIS_CACHE_SCHEMA,
        )
except (RuntimeError, ValueError, OSError) as exc:
    st.error(f"Planned-scene NOAA atmosphere unavailable: {exc}")
    st.info("Flight-time calculation stopped; historical-route weather was not substituted.")
    st.stop()

planned_environments: list[SceneEnvironment] = []
for point, profile, progress, planned_time in zip(
    selected_aircraft.phase_profile,
    planned_profiles,
    (phase_progress[point.sequence] for point in selected_aircraft.phase_profile),
    planned_times,
    strict=True,
):
    scene_aircraft = aircraft_view(observed_route, progress, map_longitude)
    metrics = atmosphere_metrics(
        profile, point.altitude_ft / METERS_TO_FEET, scene_aircraft["bearing_deg"]
    )
    region_index = active_segment_index(analysis.atmospheric_route, progress)
    planned_environments.append(
        SceneEnvironment(
            sequence=point.sequence,
            temperature_f=float(metrics["temperature_f"]),
            pressure_inhg=float(metrics["pressure_inhg"]),
            wind_speed_kt=float(metrics["wind_speed_kt"]),
            along_track_wind_kt=float(metrics["along_wind_kt"]),
            planned_time_utc=planned_time.isoformat(),
            noaa_valid_time=profile.valid_time.isoformat(),
            atmospheric_region=str(rows[region_index]["region"]),
        )
    )
flight_plan = estimate_flight_plan(
    selected_aircraft,
    route_distance_miles,
    tuple(planned_environments),
)

physical_request = build_physical_route_request(
    aircraft=selected_aircraft,
    analysis=analysis,
    flight_plan=flight_plan,
    boom_limit_psf=FAA_NPRM_RESEARCH_LIMIT_PSF,
)
physical_request_checksum = request_checksum(physical_request)
physical_state_key = (
    f"physical-result:{mission_id}:{observed_date.isoformat()}:"
    f"{selected_aircraft.workbook_checksum or selected_aircraft.aircraft_id}"
)
physical_result: PhysicalRouteAnalysis | None = None
saved_physical_result = st.session_state.get(physical_state_key)
if isinstance(saved_physical_result, str):
    try:
        candidate_result = load_physical_route_analysis(saved_physical_result)
    except (ValueError, OSError):
        st.session_state.pop(physical_state_key, None)
    else:
        if candidate_result.request_checksum == physical_request_checksum:
            physical_result = candidate_result
        else:
            st.session_state.pop(physical_state_key, None)

propagation_command = os.getenv("MACHLANE_PROPAGATION_COMMAND")
if run_analysis:
    try:
        if propagation_command:
            with st.spinner(
                "Running the registered physical propagation wrapper across route, NOAA, and terrain…"
            ):
                physical_result = ExternalRouteSolver(propagation_command).run(physical_request)
        else:
            with st.spinner(
                "Calculating an unvalidated primary-ray research estimate from LM1021, NOAA, and 3DEP…"
            ):
                physical_result = OpenResearchRouteSolver().run(physical_request)
    except (ResearchSolverUnavailableError, RuntimeError, ValueError, OSError) as exc:
        st.error(f"Physical propagation stopped: {exc}")
    else:
        st.session_state[physical_state_key] = physical_result.model_dump_json()

st.markdown(
    f'<div class="source-banner"><b>Real inputs loaded.</b> OpenSky {callsign} · NOAA {analysis.noaa_model} matched throughout {observed_start:%H:%M}–{observed_end:%H:%M} UTC · automatic atmospheric regions · available USGS 3DEP terrain previews.</div>',
    unsafe_allow_html=True,
)

summary = st.columns(5)
summary[0].metric(
    "Route", "OpenSky observed", f"{callsign} · {point_count:,} points", delta_color="off"
)
summary[1].metric(
    "Atmosphere",
    f"NOAA {analysis.noaa_model} archive",
    f"Matched {observed_start:%H:%M}–{observed_end:%H:%M} UTC",
    delta_color="off",
)
summary[2].metric(
    "Terrain",
    "USGS 3DEP",
    f"{terrain_loaded} sparse previews · {terrain_unavailable} unavailable",
    delta_color="off",
)
summary[3].metric(
    "Atmospheric regions",
    f"{len(rows)} automatic regions",
    "Policy v1 · high resolution",
    delta_color="off",
)
summary[4].metric(
    "Surface boom",
    (
        "NOT CALCULATED"
        if physical_result is None
        else (
            "RESEARCH ESTIMATE"
            if physical_result.solver.validation_status != "VALIDATED"
            else physical_result.baseline.classification.replace("_", " ")
        )
    ),
    (
        "Near-field + validated propagation required"
        if physical_result is None
        else (
            f"{physical_result.baseline.maximum_nominal_overpressure_pa / PASCALS_PER_PSF:.3f} psf nominal · "
            + (
                "primary ray only · not uncertainty bounded"
                if physical_result.solver.validation_status != "VALIDATED"
                else f"limit {physical_result.boom_limit_pa / PASCALS_PER_PSF:.2f} psf"
            )
        )
    ),
    delta_color="off",
)

plan_summary = st.columns(4)
plan_summary[0].metric(
    "Aircraft",
    selected_aircraft.value("Aircraft Name") or selected_aircraft.display_name,
    f"{selected_aircraft.numeric_value('Preferred Cruise Mach') or 0:.1f} Mach research cruise",
    delta_color="off",
)
plan_summary[1].metric(
    "Estimated airborne time",
    f"{flight_plan.airborne_time_min / 60:.2f} hr",
    "NOAA temperature + route-aligned wind",
    delta_color="off",
)
plan_summary[2].metric(
    "Estimated block time",
    f"{flight_plan.block_time_min / 60:.2f} hr",
    "Includes source-backed taxi times",
    delta_color="off",
)
plan_summary[3].metric(
    "Supersonic cruise",
    f"{flight_plan.cruise_distance_miles:,.0f} mi",
    f"{flight_plan.cruise_time_min:.0f} min · research scenario",
    delta_color="off",
)

research_suggestion = None
baseline_primary_exceeds = False
if physical_result is not None:
    baseline_primary_exceeds = (
        physical_result.baseline.maximum_nominal_overpressure_pa
        > physical_result.boom_limit_pa
    )
    sensitivity_candidates = [
        candidate
        for candidate in physical_result.candidates
        if candidate.candidate_id != physical_result.baseline_candidate_id
        and candidate.altitude_offset_ft != 0
    ]
    if sensitivity_candidates:
        best_sensitivity = min(
            sensitivity_candidates,
            key=lambda candidate: candidate.maximum_nominal_overpressure_pa,
        )
        if (
            best_sensitivity.maximum_nominal_overpressure_pa
            < physical_result.baseline.maximum_nominal_overpressure_pa
        ):
            research_suggestion = best_sensitivity

ground_result_text = (
    '<span class="pending">NOT CALCULATED</span><br/>No ground-intersection result is available.'
    if physical_result is None
    else (
        f'<span class="pending">INTERSECTS TERRAIN</span><br/>{len(physical_result.baseline.surface_samples)} primary-ray receiver samples · '
        f'{physical_result.baseline.maximum_nominal_overpressure_pa / PASCALS_PER_PSF:.3f} psf nominal maximum.'
    )
)
mitigation_text = '<span class="pending">NOT TRIGGERED</span><br/>Run the physical analysis first.'
if physical_result is not None:
    if not baseline_primary_exceeds:
        mitigation_text = (
            '<span class="pending">NO CHANGE GENERATED</span><br/>'
            'Nominal primary-ray estimate does not exceed the research threshold. This is not a no-boom or compliance finding.'
        )
    elif research_suggestion is not None:
        sensitivity_threshold_text = (
            "below the nominal research threshold"
            if research_suggestion.maximum_nominal_overpressure_pa
            <= physical_result.boom_limit_pa
            else "still above the nominal research threshold"
        )
        mitigation_text = (
            f'<span class="pending">HEIGHT SENSITIVITY</span><br/>{research_suggestion.altitude_offset_ft:+,.0f} ft lowers the nominal primary-ray maximum to '
            f'{research_suggestion.maximum_nominal_overpressure_pa / PASCALS_PER_PSF:.3f} psf ({sensitivity_threshold_text}) with the source signature held fixed.'
        )
    else:
        mitigation_text = (
            '<span class="pending">NO SUPPORTED OPTION</span><br/>'
            'The baseline nominal primary-ray estimate exceeds the research threshold, but no calculated height sensitivity improved it.'
        )

physical_corridor_text = (
    '<span class="pending">NOT CALCULATED</span><br/>Atmospheric regions are not a compliant corridor.'
    if physical_result is None
    else (
        '<span class="pending">NOT DETERMINED</span><br/>'
        + (
            "Primary-ray ground waveform calculated, but secondary rays, bounded uncertainty, and validation are incomplete."
            if physical_result.solver.validation_status != "VALIDATED"
            else (
                f"{physical_result.baseline.classification.replace('_', ' ')} baseline · "
                + (
                    "no validated variation available"
                    if physical_result.recommended is None
                    else f"recommended {physical_result.recommended.label}"
                )
            )
        )
    )
)
st.markdown(
    f"""
<div class="layer-grid">
  <div class="layer"><b>1 · Planned flight</b><span>{selected_aircraft.value('Aircraft Name') or selected_aircraft.display_name} phase profile applied to the real OpenSky baseline geometry · {flight_plan.block_time_min / 60:.2f} hr estimated block time.</span></div>
  <div class="layer"><b>2 · Primary-ray ground result</b><span>{ground_result_text}</span></div>
  <div class="layer"><b>3 · Proposed flight-path adjustment</b><span>{mitigation_text}</span></div>
  <div class="layer"><b>4 · Compliant operating corridor</b><span>{physical_corridor_text}</span></div>
</div>
""",
    unsafe_allow_html=True,
)

alert_left, alert_right = st.columns([3, 1], vertical_alignment="center")
with alert_left:
    if physical_result is None:
        st.warning(
            "Alert mode is locked until a registered physical solver returns uncertainty-bounded primary, secondary-direct, and secondary-indirect surface results."
        )
    elif physical_result.solver.validation_status != "VALIDATED":
        if not baseline_primary_exceeds:
            st.info(
                "The modeled primary ray intersects terrain, but its nominal peak does not exceed "
                "the research threshold. No mitigation scenario was triggered. This does not mean "
                "that no sonic boom reaches the ground: secondary rays and uncertainty remain incomplete."
            )
        elif research_suggestion is not None:
            sensitivity_threshold_text = (
                "below the nominal research threshold"
                if research_suggestion.maximum_nominal_overpressure_pa
                <= physical_result.boom_limit_pa
                else "still above the nominal research threshold"
            )
            st.warning(
                "The modeled primary ray intersects terrain and its nominal peak exceeds the research "
                f"threshold. A {research_suggestion.altitude_offset_ft:+,.0f} ft height sensitivity "
                f"recalculates to {research_suggestion.maximum_nominal_overpressure_pa / PASCALS_PER_PSF:.3f} psf nominal—{sensitivity_threshold_text}—using the same route-time NOAA columns. "
                "The near-field signature is held fixed, so this is a research mitigation scenario—not a cleared altitude or compliant corridor."
            )
        else:
            st.warning(
                "The modeled primary ray intersects terrain and its nominal peak exceeds the research "
                "threshold. The available NOAA column and aircraft signature did not produce an improving "
                "height sensitivity. Route/time/speed alternatives require corresponding NOAA samples and condition-matched near-field signatures."
            )
    elif physical_result.baseline.classification == "EXCEEDS_LIMIT":
        st.error(
            "Baseline exceeds the research threshold. "
            + (
                "No uncertainty-bounded route candidate passed every requested ray family."
                if physical_result.recommended is None
                else f"Validated strategic alternative available: {physical_result.recommended.label}."
            )
        )
    elif physical_result.baseline.classification == "WITHIN_LIMIT":
        st.success("Baseline is within the declared uncertainty-bounded research threshold.")
    else:
        st.warning("Physical result is incomplete; no operating recommendation is permitted.")
with alert_right:
    if research_suggestion is not None:
        st.metric(
            "Proposed height change",
            f"{research_suggestion.altitude_offset_ft:+,.0f} ft",
            f"{research_suggestion.maximum_nominal_overpressure_pa / PASCALS_PER_PSF:.3f} psf nominal",
            delta_color="off",
        )
    else:
        adjustment_state = (
            "Not calculated"
            if physical_result is None
            else "No improving option" if baseline_primary_exceeds else "Not triggered"
        )
        st.metric(
            "Flight-path adjustment",
            adjustment_state,
            "Automatic threshold trigger",
            delta_color="off",
        )

show_suggested_variation = physical_result is not None and physical_result.recommended is not None

atmosphere_layers = [
    pdk.Layer(
        "PathLayer",
        rows,
        id="automatic-atmospheric-region-fill",
        get_path="path",
        get_color="zone_fill_color",
        get_width=22,
        width_units="pixels",
        width_min_pixels=18,
        width_max_pixels=26,
        pickable=False,
    ),
    pdk.Layer(
        "PathLayer",
        rows,
        id="automatic-atmospheric-region-edge",
        get_path="path",
        get_color="zone_edge_color",
        get_width=4,
        width_units="pixels",
        width_min_pixels=3,
        width_max_pixels=5,
        pickable=True,
    ),
    pdk.Layer(
        "ScatterplotLayer",
        [
            {
                "position": row["path"][0],
                "region": row["region"],
                "boundary_reason": row["boundary_reason"],
                "pressure_inhg": f"{row['pressure_inhg']:.3f}",
                "edge_color": row["zone_edge_color"],
            }
            for row in rows[1:]
        ],
        id="automatic-region-boundaries",
        get_position="position",
        get_radius=7,
        radius_units="pixels",
        get_fill_color=[5, 16, 29, 100],
        get_line_color="edge_color",
        line_width_min_pixels=3,
        stroked=True,
        pickable=True,
    ),
]
observed_layer = pdk.Layer(
    "PathLayer",
    [
        {
            "path": [
                [display_longitude(longitude, map_longitude), latitude]
                for latitude, longitude in observed_route.waypoints
            ]
        }
    ],
    id="opensky-observed-route",
    get_path="path",
    get_color=[255, 213, 0, 235],
    width_min_pixels=3,
)
label_layer = pdk.Layer(
    "TextLayer",
    [
        {
            "position": [
                display_longitude(mission.origin.longitude, map_longitude),
                mission.origin.latitude,
            ],
            "label": mission.origin.iata,
        },
        {
            "position": [
                display_longitude(mission.destination.longitude, map_longitude),
                mission.destination.latitude,
            ],
            "label": mission.destination.iata,
        },
    ],
    get_position="position",
    get_text="label",
    get_size=15,
    get_pixel_offset=[0, -18],
    get_color=[230, 239, 248, 235],
)

physical_footprint_layer = None
suggested_route_layer = None
baseline_surface_by_segment: dict[str, list[dict[str, Any]]] = {}
if physical_result is not None:
    physical_points = []
    limit_pa = physical_result.boom_limit_pa
    result_is_validated = physical_result.solver.validation_status == "VALIDATED"
    for sample in physical_result.baseline.surface_samples:
        peak_psf = sample.peak_positive_overpressure_pa / PASCALS_PER_PSF
        upper_pa = (
            sample.peak_positive_overpressure_pa
            if sample.uncertainty_upper_pa is None
            else sample.uncertainty_upper_pa
        )
        upper_psf = upper_pa / PASCALS_PER_PSF
        compliant = result_is_validated and upper_pa <= limit_pa
        row = {
            "position": [
                display_longitude(sample.longitude, map_longitude),
                sample.latitude,
            ],
            "segment_id": sample.segment_id,
            "ray_family": sample.ray_family,
            "peak_psf": peak_psf,
            "upper_psf": upper_psf,
            "terrain_ft": sample.terrain_elevation_m * METERS_TO_FEET,
            "color": (
                [35, 220, 180, 120]
                if compliant
                else (
                    [255, 71, 102, 145]
                    if result_is_validated
                    else [156, 92, 255, 115]
                )
            ),
            "edge": (
                [106, 255, 220, 245]
                if compliant
                else (
                    [255, 173, 187, 255]
                    if result_is_validated
                    else [205, 176, 255, 245]
                )
            ),
        }
        physical_points.append(row)
        baseline_surface_by_segment.setdefault(sample.segment_id, []).append(row)
    physical_footprint_layer = pdk.Layer(
        "ScatterplotLayer",
        physical_points,
        id="physical-surface-overpressure",
        get_position="position",
        get_radius=4_500,
        radius_units="meters",
        radius_min_pixels=5,
        radius_max_pixels=24,
        get_fill_color="color",
        get_line_color="edge",
        line_width_min_pixels=2,
        stroked=True,
        pickable=True,
    )
    if physical_result.recommended is not None:
        suggested_route_layer = pdk.Layer(
            "PathLayer",
            [
                {
                    "path": [
                        [display_longitude(longitude, map_longitude), latitude]
                        for latitude, longitude in physical_result.recommended.route_coordinates
                    ]
                }
            ],
            id="validated-suggested-route",
            get_path="path",
            get_color=[45, 240, 207, 245],
            get_width=5,
            width_units="pixels",
            width_min_pixels=4,
        )


@fragment
def render_workspace() -> None:
    workspace, inspector = st.columns([3.15, 1], gap="medium")
    with workspace:
        percent = st.slider(
            "Flight progress",
            min_value=0.0,
            max_value=100.0,
            value=0.0,
            step=0.1,
            format="%.1f%%",
            key="planned_flight_progress_pct_v2",
            help="Move continuously through the planned flight. Altitude, Mach, position, atmosphere, and phase update at every 0.1% step.",
        )
        progress = percent / 100
        planned_state = continuous_planned_state(progress, selected_aircraft, flight_plan)
        active_index = active_segment_index(analysis.atmospheric_route, progress)
        active_row = rows[active_index]
        profile = analysis.segment_atmospheres[active_index]
        aircraft = aircraft_view(observed_route, progress, map_longitude)
        altitude_ft = float(planned_state["altitude_ft"])
        mach = float(planned_state["mach"])
        weather = atmosphere_metrics(profile, altitude_ft / METERS_TO_FEET, aircraft["bearing_deg"])
        timestamp = observed_start + timedelta(minutes=float(planned_state["elapsed_min"]))
        noaa_timestamp = profile.valid_time
        phase_label = str(planned_state["phase"])
        true_airspeed_kt = mach * speed_of_sound_knots(float(weather["temperature_f"]))
        ground_speed_kt = (
            0.0 if mach <= 0.01 else max(0.0, true_airspeed_kt + float(weather["along_wind_kt"]))
        )
        mach_reading = f"{mach:.2f}"
        tas_reading = f"{true_airspeed_kt:,.0f}"
        ground_reading = f"{ground_speed_kt:,.0f}"
        altitude_reading = f"{altitude_ft:,.0f}"
        st.markdown(
            f'<div class="phase-readout"><span>{percent:05.1f}% · REGION {active_row["region"]}</span><b>{phase_label} · Mach {mach:.2f} · {altitude_ft:,.0f} ft</b></div>',
            unsafe_allow_html=True,
        )

        st.markdown(
            '<div class="section-kicker">Route-aligned ambient atmosphere</div>',
            unsafe_allow_html=True,
        )
        live = st.columns(4)
        live[0].metric("Ambient pressure", f"{weather['pressure_inhg']:.3f} inHg")
        live[1].metric("Temperature", f"{weather['temperature_f']:.1f} °F")
        live[2].metric("Wind speed", f"{weather['wind_speed_kt']:.0f} kt")
        live[3].metric("Along-track wind", f"{weather['along_wind_kt']:+.0f} kt")

        map_title, map_control = st.columns([4, 1], vertical_alignment="bottom")
        with map_title:
            st.subheader(
                f"{mission.origin.iata}–{mission.destination.iata} route and automatic atmospheric regions"
            )
        with map_control:
            show_pressure_zones = st.toggle(
                "Pressure zones",
                value=True,
                help="Show or hide NOAA-derived atmospheric regions. This does not hide the OpenSky route.",
            )
        st.markdown(
            '<div class="zone-legend">Yellow = exact OpenSky baseline. Blue → red route bands = ambient pressure only. Purple surface points are unvalidated primary-ray research estimates; they are not safe/unsafe classifications. The cyan path appears only for a validated, uncertainty-bounded variation.</div>',
            unsafe_allow_html=True,
        )
        aircraft_layer = pdk.Layer(
            "ScatterplotLayer",
            [{"position": [aircraft["display_longitude"], aircraft["latitude"]]}],
            id="observed-aircraft-position",
            get_position="position",
            get_radius=8,
            radius_units="pixels",
            radius_min_pixels=8,
            radius_max_pixels=8,
            get_fill_color=[255, 213, 0, 255],
            get_line_color=[7, 12, 19, 255],
            line_width_min_pixels=2,
            stroked=True,
        )
        st.pydeck_chart(
            pdk.Deck(
                layers=[
                    *(atmosphere_layers if show_pressure_zones else []),
                    *(
                        [physical_footprint_layer]
                        if physical_footprint_layer is not None
                        else []
                    ),
                    observed_layer,
                    *(
                        [suggested_route_layer]
                        if show_suggested_variation and suggested_route_layer is not None
                        else []
                    ),
                    label_layer,
                    aircraft_layer,
                ],
                map_style=pdk.map_styles.CARTO_DARK,
                initial_view_state=pdk.ViewState(
                    latitude=map_latitude,
                    longitude=map_longitude,
                    zoom=map_zoom,
                    pitch=8,
                ),
                tooltip={
                    "html": "<b>{region}{segment_id}</b><br/>{ray_family}<br/>Ambient {pressure_inhg} inHg<br/>Surface peak {peak_psf} psf<br/>Upper bound {upper_psf} psf<br/>{boundary_reason}",
                    "style": {"backgroundColor": "#0b1420", "color": "#e5eef8"},
                },
            ),
            height=500,
        )
        st.caption(
            f"Ambient-pressure scale: {pressure_min * 100 * PASCALS_TO_INHG:.3f}–{pressure_max * 100 * PASCALS_TO_INHG:.3f} inHg · exact retained route polyline · "
            + (
                "surface boom not calculated"
                if physical_result is None
                else (
                    f"primary-ray research footprint · {physical_result.solver.name} "
                    f"{physical_result.solver.version} · {physical_result.solver.validation_status}"
                )
            )
        )

    with inspector:
        st.markdown(
            '<div class="section-kicker">Live flight instruments</div>', unsafe_allow_html=True
        )
        st.markdown(
            f"""
            <div class="instrument-panel">
              <div class="instrument-head"><span class="instrument-title">AIRCRAFT STATE</span><span class="phase-chip">{phase_label}</span></div>
              <div class="instrument-grid">
                <div class="instrument primary"><span class="label">MACH</span><span class="reading">{mach_reading}</span></div>
                <div class="instrument primary"><span class="label">ALTITUDE</span><span class="reading">{altitude_reading}</span><span class="unit">FT</span></div>
                <div class="instrument"><span class="label">TRUE AIRSPEED</span><span class="reading">{tas_reading}</span><span class="unit">KT</span></div>
                <div class="instrument"><span class="label">GROUNDSPEED</span><span class="reading">{ground_reading}</span><span class="unit">KT</span></div>
                <div class="instrument"><span class="label">PRESSURE</span><span class="reading">{weather["pressure_inhg"]:.3f}</span><span class="unit">INHG</span></div>
                <div class="instrument"><span class="label">TEMPERATURE</span><span class="reading">{weather["temperature_f"]:.1f}</span><span class="unit">°F</span></div>
                <div class="instrument"><span class="label">WIND</span><span class="reading">{weather["wind_speed_kt"]:.0f}</span><span class="unit">KT</span></div>
                <div class="instrument"><span class="label">TRACK</span><span class="reading">{aircraft["bearing_deg"]:.0f}</span><span class="unit">°</span></div>
              </div>
              <div class="instrument-meta">{timestamp:%H:%M UTC} · NOAA VALID {noaa_timestamp:%H:%M UTC}<br>{percent:.1f}% COMPLETE · {aircraft["latitude"]:.2f}°, {aircraft["longitude"]:.2f}°</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="status-card"><div class="label">NOAA atmosphere</div><div class="value">{analysis.noaa_model} · {noaa_timestamp:%H:%M UTC}</div><div class="meta">Region {active_row["region"]} · real model data</div></div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="status-card"><div class="label">USGS terrain</div><div class="value">{active_row["terrain"].replace("_", " ")}</div><div class="meta">{active_row["terrain_reason"]}</div></div>',
            unsafe_allow_html=True,
        )
        active_physical_samples = baseline_surface_by_segment.get(active_row["region"], [])
        if physical_result is None:
            boom_card = '<div class="status-card"><div class="label">Surface boom</div><div class="value"><span class="pending">NOT CALCULATED</span></div><div class="meta">No registered physical result</div></div>'
        elif not active_physical_samples:
            boom_card = '<div class="status-card"><div class="label">Surface boom</div><div class="value"><span class="pending">UNKNOWN</span></div><div class="meta">No complete physical ray sample for this region</div></div>'
        else:
            active_nominal_psf = max(point["peak_psf"] for point in active_physical_samples)
            boom_card = (
                '<div class="status-card"><div class="label">Surface boom · nominal</div>'
                f'<div class="value">{active_nominal_psf:.3f} <span class="unit">PSF</span></div>'
                f'<div class="meta">PRIMARY ONLY · {physical_result.solver.validation_status} · NOT A COMPLIANCE RESULT</div></div>'
            )
        st.markdown(
            boom_card,
            unsafe_allow_html=True,
        )


render_workspace()

flight_tab, regions_tab, atmosphere_tab, terrain_tab, boom_tab, provenance_tab, how_tab, evidence_tab = (
    st.tabs(
        [
            "Flight plan",
            "Atmospheric regions",
            "Atmosphere",
            "Terrain",
            "Sonic boom",
            "Data provenance",
            "How it works",
            "Evidence",
        ]
    )
)

with flight_tab:
    st.markdown(f"#### Phase-aware {mission.origin.iata}–{mission.destination.iata} estimate")
    st.caption(
        "MachLane applies the uploaded aircraft scene profile to the exact OpenSky route geometry. Local NOAA temperature determines speed of sound; route-aligned wind adjusts groundspeed."
    )
    phase_frame = pd.DataFrame(
        [
            {
                "Phase": phase.phase,
                "Time (min)": phase.duration_min,
                "Distance (mi)": phase.distance_miles,
                "Start altitude (ft)": phase.start_altitude_ft,
                "End altitude (ft)": phase.end_altitude_ft,
                "Start Mach": phase.start_mach,
                "End Mach": phase.end_mach,
                "Timing basis": phase.timing_basis,
            }
            for phase in flight_plan.phases
        ]
    )
    st.dataframe(
        phase_frame,
        hide_index=True,
        width="stretch",
        column_config={
            "Time (min)": st.column_config.NumberColumn(format="%.1f min"),
            "Distance (mi)": st.column_config.NumberColumn(format="%.1f mi"),
            "Start altitude (ft)": st.column_config.NumberColumn(format="%,.0f ft"),
            "End altitude (ft)": st.column_config.NumberColumn(format="%,.0f ft"),
            "Start Mach": st.column_config.NumberColumn(format="%.2f"),
            "End Mach": st.column_config.NumberColumn(format="%.2f"),
        },
    )
    st.markdown("#### Scene-by-scene NOAA match")
    scene_frame = pd.DataFrame(
        [
            {
                "Scene": scene.phase,
                "Altitude (ft)": scene.altitude_ft,
                "Mach": scene.mach,
                "TAS (kt)": scene.true_airspeed_kt,
                "Groundspeed (kt)": scene.ground_speed_kt,
                "Temperature (°F)": scene.temperature_f,
                "Ambient pressure (inHg)": scene.pressure_inhg,
                "Along-track wind (kt)": scene.along_track_wind_kt,
                "Planned UTC": scene.planned_time_utc,
                "NOAA valid UTC": scene.noaa_valid_time,
                "Atmospheric region": scene.atmospheric_region,
            }
            for scene in flight_plan.scenes
        ]
    )
    st.dataframe(scene_frame, hide_index=True, width="stretch")
    st.download_button(
        "Export phase plan · CSV",
        scene_frame.to_csv(index=False),
        file_name=f"machlane_{mission_id}_{observed_start:%Y%m%d}_aircraft_one_phase_plan.csv",
        mime="text/csv",
    )
    st.warning(" ".join(flight_plan.limitations))

with regions_tab:
    frame = pd.DataFrame(rows)
    st.dataframe(
        frame,
        hide_index=True,
        width="stretch",
        column_order=[
            "region",
            "boundary_reason",
            "start_mi",
            "end_mi",
            "length_mi",
            "pressure_inhg",
            "temperature_f",
            "wind_kt",
            "terrain",
            "surface_boom",
        ],
        column_config={
            "region": "Region",
            "boundary_reason": "Boundary trigger",
            "start_mi": st.column_config.NumberColumn("From mi", format="%.1f"),
            "end_mi": st.column_config.NumberColumn("To mi", format="%.1f"),
            "length_mi": st.column_config.NumberColumn("Length", format="%.1f mi"),
            "pressure_inhg": st.column_config.NumberColumn("Ambient pressure", format="%.3f inHg"),
            "temperature_f": st.column_config.NumberColumn("Temperature", format="%.1f °F"),
            "wind_kt": st.column_config.NumberColumn("Wind", format="%.1f kt"),
            "terrain": "3DEP",
            "surface_boom": "Surface boom",
        },
    )
    export_columns = [key for key in frame.columns if key not in {"path", "color"}]
    st.download_button(
        "Export atmospheric regions · CSV",
        frame[export_columns].to_csv(index=False),
        file_name=f"machlane_{mission_id}_{observed_start:%Y%m%d}_automatic_regions.csv",
        mime="text/csv",
    )
    st.caption(
        "Automatic atmospheric regions · policy v1. Boundaries are caused by a recorded temperature, ambient-pressure, wind-vector, model-cycle, or model-valid-time change. The thresholds group atmospheric inputs; they are not compliance margins."
    )

with atmosphere_tab:
    st.caption(
        "Each region has its own real NOAA pressure-level column and valid time. Select a region to inspect; this does not change the source or segmentation policy."
    )
    selected_region = st.selectbox("Region", [row["region"] for row in rows], key="profile_region")
    profile_index = next(
        index for index, row in enumerate(rows) if row["region"] == selected_region
    )
    profile = analysis.segment_atmospheres[profile_index]
    altitude_ft = [value * METERS_TO_FEET for value in profile.altitude_m]
    pressure_inhg = [value * PASCALS_TO_INHG for value in profile.pressure_pa]
    temperature_f = [(value - 273.15) * 9 / 5 + 32 for value in profile.temperature_k]
    wind_kt = [
        math.hypot(u, v) * 1.943844492
        for u, v in zip(profile.zonal_wind_mps, profile.meridional_wind_mps, strict=True)
    ]
    chart_left, chart_right = st.columns(2)
    with chart_left:
        figure = go.Figure(
            go.Scatter(
                x=pressure_inhg,
                y=altitude_ft,
                mode="lines+markers",
                line={"color": "#60a5fa", "width": 3},
            )
        )
        figure.update_layout(
            title=f"Ambient pressure · {selected_region} · {profile.valid_time:%H:%M UTC}",
            xaxis_title="Pressure (inHg)",
            yaxis_title="Altitude (ft)",
            template="plotly_dark",
            height=380,
            paper_bgcolor="#0a111b",
            plot_bgcolor="#0d1722",
        )
        st.plotly_chart(figure, width="stretch")
    with chart_right:
        figure = go.Figure(
            go.Scatter(
                x=wind_kt,
                y=altitude_ft,
                mode="lines+markers",
                line={"color": "#2dd4bf", "width": 3},
            )
        )
        figure.update_layout(
            title=f"Wind speed · {selected_region} · {profile.valid_time:%H:%M UTC}",
            xaxis_title="Wind speed (kt)",
            yaxis_title="Altitude (ft)",
            template="plotly_dark",
            height=380,
            paper_bgcolor="#0a111b",
            plot_bgcolor="#0d1722",
        )
        st.plotly_chart(figure, width="stretch")
    profile_frame = pd.DataFrame(
        {
            "Altitude (ft)": [round(value) for value in altitude_ft],
            "Ambient pressure (inHg)": [round(value, 3) for value in pressure_inhg],
            "Temperature (°F)": [round(value, 1) for value in temperature_f],
            "Wind (kt)": [round(value, 1) for value in wind_kt],
        }
    )
    st.dataframe(profile_frame, hide_index=True, width="stretch")
    st.warning(
        "Ambient pressure is an atmospheric input. It is not sonic-boom overpressure and cannot determine compliance by itself."
    )

with terrain_tab:
    terrain_frame = pd.DataFrame(
        [
            {
                "Region": result.segment_id,
                "Status": result.status.replace("_", " "),
                "Reason": result.reason,
                "Resolution": None
                if result.profile is None
                else f"{result.profile.source.resolution_m:g} m",
                "Retrieved": None
                if result.profile is None
                else result.profile.source.retrieved_at.isoformat(),
                "Checksum": None if result.profile is None else result.profile.source.checksum,
            }
            for result in analysis.terrain_regions
        ]
    )
    st.dataframe(terrain_frame, hide_index=True, width="stretch")
    st.caption(
        "3DEP is requested automatically only inside MachLane's conservative U.S.-territory envelope. Oceans and unsupported international terrain stay explicitly unavailable."
    )

with boom_tab:
    st.markdown("#### Sonic boom and terrain")
    st.caption(
        "MachLane propagates the uploaded aircraft signature through route-matched NOAA atmosphere to available 3DEP terrain. Results below are primary-ray research estimates, not compliance findings."
    )
    coverage = physical_request["coverage_summary"]
    coverage_cards = st.columns(4)
    coverage_cards[0].metric("Route regions", f"{coverage['region_count']}")
    coverage_cards[1].metric("Near-field matched", f"{coverage['ready']}")
    coverage_cards[2].metric("Subsonic", f"{coverage['subsonic']}")
    coverage_cards[3].metric("Missing signature", f"{coverage['missing_nearfield']}")
    if physical_result is None:
        st.error(
            "Ground intersection has not been calculated. Click Run analysis to use the loaded aircraft, NOAA atmosphere, and terrain."
        )
    else:
        baseline = physical_result.baseline
        baseline_psf = baseline.maximum_nominal_overpressure_pa / PASCALS_PER_PSF
        threshold_psf = physical_result.boom_limit_pa / PASCALS_PER_PSF
        if baseline_primary_exceeds:
            st.error(
                f"GROUND BOOM WARNING · The primary-ray model intersects terrain at {baseline_psf:.3f} psf nominal, above the {threshold_psf:.2f} psf research threshold."
            )
            if research_suggestion is not None:
                suggested_psf = (
                    research_suggestion.maximum_nominal_overpressure_pa
                    / PASCALS_PER_PSF
                )
                st.warning(
                    f"PROPOSED FLIGHT-PATH ADJUSTMENT · Change cruise height by {research_suggestion.altitude_offset_ft:+,.0f} ft. "
                    f"The recalculated primary-ray estimate is {suggested_psf:.3f} psf. "
                    + (
                        "It falls below the research threshold."
                        if suggested_psf <= threshold_psf
                        else "It improves the result but remains above the research threshold."
                    )
                    + " Aircraft performance and the source signature at this new height still require validation."
                )
            else:
                st.warning(
                    "NO IMPROVING FLIGHT-PATH ADJUSTMENT FOUND · The available height options did not lower the primary-ray estimate."
                )
        else:
            st.success(
                f"PRIMARY-RAY SCREEN · The modeled ray intersects terrain at {baseline_psf:.3f} psf nominal, below the {threshold_psf:.2f} psf research threshold. No adjustment was triggered."
            )
        result_has_uncertainty = all(
            sample.uncertainty_upper_pa is not None for sample in baseline.surface_samples
        )
        result_cards = st.columns(5)
        result_cards[0].metric(
            "Baseline",
            (
                baseline.classification.replace("_", " ")
                if physical_result.solver.validation_status == "VALIDATED"
                else "RESEARCH ONLY"
            ),
        )
        result_cards[1].metric(
            "Nominal maximum",
            f"{baseline.maximum_nominal_overpressure_pa / PASCALS_PER_PSF:.3f} psf",
        )
        result_cards[2].metric(
            "Uncertainty upper",
            (
                f"{baseline.maximum_uncertainty_overpressure_pa / PASCALS_PER_PSF:.3f} psf"
                if result_has_uncertainty
                else "NOT BOUNDED"
            ),
        )
        result_cards[3].metric(
            "Research threshold", f"{physical_result.boom_limit_pa / PASCALS_PER_PSF:.2f} psf"
        )
        result_cards[4].metric("Solver", physical_result.solver.validation_status)

        candidate_frame = pd.DataFrame(
            [
                {
                    "Candidate": candidate.label,
                    "ID": candidate.candidate_id,
                    "Classification": candidate.classification,
                    "Distance (mi)": candidate.distance_m * METERS_TO_MILES,
                    "Time change (min)": candidate.time_delta_min,
                    "Maximum offset (nmi)": candidate.maximum_lateral_offset_m / 1852,
                    "Altitude change (ft)": candidate.altitude_offset_ft,
                    "Nominal max (psf)": candidate.maximum_nominal_overpressure_pa / PASCALS_PER_PSF,
                    "Upper max (psf)": (
                        candidate.maximum_uncertainty_overpressure_pa / PASCALS_PER_PSF
                        if all(
                            sample.uncertainty_upper_pa is not None
                            for sample in candidate.surface_samples
                        )
                        else None
                    ),
                    "Ray families": ", ".join(candidate.completed_ray_families),
                }
                for candidate in physical_result.candidates
            ]
        )
        candidate_id = st.selectbox(
            "Route candidate",
            [candidate.candidate_id for candidate in physical_result.candidates],
            format_func=lambda value: next(
                candidate.label for candidate in physical_result.candidates if candidate.candidate_id == value
            ),
            key="physical_candidate",
        )
        selected_candidate = next(
            candidate for candidate in physical_result.candidates if candidate.candidate_id == candidate_id
        )
        physical_profile = sorted(
            selected_candidate.surface_samples,
            key=lambda sample: (sample.along_track_m, sample.cross_track_m, sample.ray_family),
        )

        intersection_figure = go.Figure()
        roll_values = sorted(
            {
                sample.launch_roll_deg
                for sample in physical_profile
                if sample.launch_roll_deg is not None
            }
        )
        has_lateral_pattern = len(roll_values) > 1 and any(
            abs(sample.cross_track_m) > 1.0 for sample in physical_profile
        )
        for roll in roll_values:
            roll_samples = sorted(
                [sample for sample in physical_profile if sample.launch_roll_deg == roll],
                key=lambda sample: sample.along_track_m,
            )
            intersection_figure.add_trace(
                go.Scatter(
                    x=[sample.along_track_m * METERS_TO_MILES for sample in roll_samples],
                    y=[sample.cross_track_m / 1852 for sample in roll_samples],
                    mode="lines",
                    line={"color": "rgba(170,190,215,.24)", "width": 1},
                    hoverinfo="skip",
                    showlegend=False,
                )
            )
        intersection_figure.add_trace(
            go.Scatter(
                x=[sample.along_track_m * METERS_TO_MILES for sample in physical_profile],
                y=[sample.cross_track_m / 1852 for sample in physical_profile],
                text=[
                    f"{sample.segment_id}<br>{_roll_label(sample.launch_roll_deg)}<br>"
                    f"peak {sample.peak_positive_overpressure_pa / PASCALS_PER_PSF:.3f} psf"
                    for sample in physical_profile
                ],
                hovertemplate="%{text}<extra></extra>",
                mode="markers",
                name="Primary ground intersection",
                marker={
                    "size": 11,
                    "color": [
                        sample.peak_positive_overpressure_pa / PASCALS_PER_PSF
                        for sample in physical_profile
                    ],
                    "colorscale": "Turbo",
                    "showscale": True,
                    "colorbar": {"title": "Nominal<br>peak psf"},
                    "line": {"color": "#e9f2ff", "width": 0.8},
                },
            )
        )
        intersection_figure.add_hline(
            y=0,
            line_color="#ffd447",
            line_width=2,
            annotation_text="OpenSky route centerline",
        )
        intersection_figure.update_layout(
            title=(
                "Multi-azimuth primary-ray ground-intersection pattern"
                if has_lateral_pattern
                else "Undertrack primary-ray ground-intersection chain"
            ),
            xaxis_title="Along-route position (mi)",
            yaxis_title="Cross-track ground intersection (nmi)",
            template="plotly_dark",
            height=470,
            paper_bgcolor="#081524",
            plot_bgcolor="#081524",
            font={"color": "#e9f2ff"},
            title_font={"color": "#ffffff", "size": 19},
        )
        st.plotly_chart(intersection_figure, width="stretch")
        st.caption(
            "Each point is a geometrical-acoustics primary-ray intersection from a published, condition-matched LM1021 azimuth signature. "
            + (
                "Off-track terrain currently reuses the region's route-aligned 3DEP elevation. "
                if has_lateral_pattern
                else "This workbook supplies only the undertrack signature at the matched condition, so MachLane does not invent a lateral boom carpet. "
            )
            + "This is an intersection diagnostic, not a validated footprint contour."
        )

        profile_figure = make_subplots(specs=[[{"secondary_y": True}]])
        for ray_family, color in (
            ("PRIMARY", "#30d5ff"),
            ("SECONDARY_DIRECT", "#ffd447"),
            ("SECONDARY_INDIRECT", "#ff5b79"),
        ):
            family_samples = [sample for sample in physical_profile if sample.ray_family == ray_family]
            if not family_samples:
                continue
            family_by_distance: dict[float, Any] = {}
            for sample in family_samples:
                current = family_by_distance.get(sample.along_track_m)
                if (
                    current is None
                    or sample.peak_positive_overpressure_pa
                    > current.peak_positive_overpressure_pa
                ):
                    family_by_distance[sample.along_track_m] = sample
            family_envelope = [family_by_distance[key] for key in sorted(family_by_distance)]
            profile_figure.add_trace(
                go.Scatter(
                    x=[sample.along_track_m * METERS_TO_MILES for sample in family_envelope],
                    y=[
                        (
                            sample.uncertainty_upper_pa
                            if sample.uncertainty_upper_pa is not None
                            else sample.peak_positive_overpressure_pa
                        ) / PASCALS_PER_PSF
                        for sample in family_envelope
                    ],
                    mode="lines+markers",
                    name=ray_family.replace("_", " ").title(),
                    line={"color": color, "width": 2.5},
                ),
                secondary_y=False,
            )
        terrain_samples = sorted(
            {sample.along_track_m: sample.terrain_elevation_m for sample in physical_profile}.items()
        )
        profile_figure.add_trace(
            go.Scatter(
                x=[distance * METERS_TO_MILES for distance, _ in terrain_samples],
                y=[elevation * METERS_TO_FEET for _, elevation in terrain_samples],
                mode="lines",
                name="Terrain",
                fill="tozeroy",
                line={"color": "#8fa6b8", "width": 1.5},
                opacity=0.45,
            ),
            secondary_y=True,
        )
        profile_figure.add_hline(
            y=physical_result.boom_limit_pa / PASCALS_PER_PSF,
            line_color="#ff4466",
            line_dash="dash",
            annotation_text="0.11 psf research threshold",
            secondary_y=False,
        )
        profile_figure.update_layout(
            title="Terrain-intersected surface overpressure along route",
            template="plotly_dark",
            height=470,
            paper_bgcolor="#081524",
            plot_bgcolor="#081524",
            hovermode="x unified",
            font={"color": "#e9f2ff"},
            title_font={"color": "#ffffff", "size": 19},
        )
        profile_figure.update_xaxes(title_text="Along-track distance (mi)")
        profile_figure.update_yaxes(
            title_text=(
                "Uncertainty upper overpressure (psf)"
                if result_has_uncertainty
                else "Nominal research overpressure (psf)"
            ),
            secondary_y=False,
        )
        profile_figure.update_yaxes(title_text="Terrain elevation (ft)", secondary_y=True)
        st.plotly_chart(profile_figure, width="stretch")

        waveform_options = {
            f"{sample.segment_id} · {_roll_label(sample.launch_roll_deg)} · {sample.cross_track_m / 1852:+.1f} nmi": sample
            for sample in physical_profile
        }
        waveform_label = st.selectbox("Ground waveform", list(waveform_options), key="ground_waveform")
        waveform = waveform_options[waveform_label]
        waveform_figure = go.Figure()
        reflection_factor = waveform.reflection_factor or 1.0
        waveform_figure.add_trace(
            go.Scatter(
                x=[value * 1_000 for value in waveform.time_s],
                y=[
                    value / reflection_factor / PASCALS_PER_PSF
                    for value in waveform.overpressure_pa
                ],
                mode="lines",
                name="Incident at terrain",
                line={"color": "#8fb9d9", "width": 1.8, "dash": "dot"},
            )
        )
        waveform_figure.add_trace(
            go.Scatter(
                x=[value * 1_000 for value in waveform.time_s],
                y=[value / PASCALS_PER_PSF for value in waveform.overpressure_pa],
                mode="lines",
                name=f"Ground pressure · rigid factor {reflection_factor:.1f}",
                line={"color": "#30d5ff", "width": 2.8},
            )
        )
        waveform_figure.add_hline(y=0, line_color="#8fa6b8")
        waveform_figure.update_layout(
            title=(
                "Incident and rigid-ground waveform · "
                f"{waveform.segment_id} · {_roll_label(waveform.launch_roll_deg)}"
            ),
            xaxis_title="Time (ms)",
            yaxis_title="Surface overpressure (psf)",
            template="plotly_dark",
            height=390,
            paper_bgcolor="#081524",
            plot_bgcolor="#081524",
            font={"color": "#e9f2ff"},
            title_font={"color": "#ffffff", "size": 18},
        )
        ray_figure = go.Figure()
        if waveform.ray_path_horizontal_m and waveform.ray_path_altitude_m:
            ray_x_nmi = [value / 1852 for value in waveform.ray_path_horizontal_m]
            ray_y_ft = [value * METERS_TO_FEET for value in waveform.ray_path_altitude_m]
            ray_figure.add_trace(
                go.Scatter(
                    x=ray_x_nmi,
                    y=ray_y_ft,
                    mode="lines",
                    name="Incident primary ray",
                    line={"color": "#30d5ff", "width": 3},
                )
            )
            ground_ft = waveform.terrain_elevation_m * METERS_TO_FEET
            receiver_nmi = ray_x_nmi[-1]
            reflected_horizontal_nmi = max(receiver_nmi * 0.22, 1.0)
            incidence_deg = waveform.ground_incidence_deg or 0.0
            reflected_altitude_ft = (
                reflected_horizontal_nmi
                * 1852
                / max(math.tan(math.radians(incidence_deg)), 1e-6)
                * METERS_TO_FEET
            )
            reflected_altitude_ft = min(
                reflected_altitude_ft,
                max(ray_y_ft[0] - ground_ft, 1.0),
            )
            ray_figure.add_trace(
                go.Scatter(
                    x=[receiver_nmi, receiver_nmi + reflected_horizontal_nmi],
                    y=[ground_ft, ground_ft + reflected_altitude_ft],
                    mode="lines",
                    name="Specular reflection geometry",
                    line={"color": "#ffd447", "width": 2.2, "dash": "dash"},
                )
            )
            ray_figure.add_trace(
                go.Scatter(
                    x=[0, receiver_nmi, receiver_nmi + reflected_horizontal_nmi],
                    y=[ground_ft, ground_ft, ground_ft],
                    mode="lines",
                    name="Local 3DEP terrain plane",
                    fill="tozeroy",
                    line={"color": "#8fa6b8", "width": 1.5},
                    opacity=0.5,
                )
            )
            ray_figure.add_trace(
                go.Scatter(
                    x=[0, receiver_nmi],
                    y=[ray_y_ft[0], ground_ft],
                    mode="markers",
                    name="Aircraft / receiver",
                    marker={"size": [11, 12], "color": ["#ffd447", "#ff5b79"]},
                )
            )
        ray_figure.update_layout(
            title="Ray–terrain intersection and reflection geometry",
            xaxis_title="Horizontal distance from aircraft (nmi)",
            yaxis_title="Altitude MSL (ft)",
            template="plotly_dark",
            height=430,
            paper_bgcolor="#081524",
            plot_bgcolor="#081524",
            font={"color": "#e9f2ff"},
            title_font={"color": "#ffffff", "size": 18},
        )
        visual_left, visual_right = st.columns(2)
        with visual_left:
            st.plotly_chart(ray_figure, width="stretch")
        with visual_right:
            st.plotly_chart(waveform_figure, width="stretch")
        st.caption(
            "The dashed reflected ray is geometric context for the declared rigid-ground pressure-doubling assumption. MachLane does not yet model frequency-dependent ground impedance, scattering, diffraction, or terrain shadowing."
        )

        if len(physical_result.candidates) > 1:
            if physical_result.solver.validation_status != "VALIDATED":
                sensitivity_frame = candidate_frame.sort_values("Altitude change (ft)")
                trade_figure = go.Figure(
                    go.Scatter(
                        x=sensitivity_frame["Altitude change (ft)"],
                        y=sensitivity_frame["Nominal max (psf)"],
                        text=sensitivity_frame["Candidate"],
                        mode="lines+markers+text",
                        textposition="top center",
                        line={"color": "#62839e", "width": 2},
                        marker={
                            "size": 13,
                            "color": [
                                (
                                    "#2df0cf"
                                    if research_suggestion is not None
                                    and candidate_id == research_suggestion.candidate_id
                                    else "#ffd447" if candidate_id == "baseline" else "#ff7b8f"
                                )
                                for candidate_id in sensitivity_frame["ID"]
                            ],
                            "line": {"color": "#ffffff", "width": 1},
                        },
                    )
                )
                trade_title = "Calculated height options · nominal primary-ray ground pressure"
                trade_x_title = "Cruise-height change (ft)"
                trade_y_title = "Nominal surface overpressure (psf)"
            else:
                trade_figure = go.Figure(
                    go.Scatter(
                        x=candidate_frame["Time change (min)"],
                        y=candidate_frame["Upper max (psf)"],
                        text=candidate_frame["Candidate"],
                        mode="markers+text",
                        textposition="top center",
                        marker={
                            "size": [
                                12 + value * 1.5
                                for value in candidate_frame["Maximum offset (nmi)"]
                            ],
                            "color": [
                                "#2df0cf" if value == "WITHIN_LIMIT" else "#ff5b79"
                                for value in candidate_frame["Classification"]
                            ],
                        },
                    )
                )
                trade_title = "Strategic alternatives · time versus uncertainty-bounded boom"
                trade_x_title = "Flight-time change (min)"
                trade_y_title = "Maximum surface overpressure (psf)"
            trade_figure.add_hline(
                y=physical_result.boom_limit_pa / PASCALS_PER_PSF,
                line_color="#ff4466",
                line_dash="dash",
                annotation_text="Research threshold",
            )
            trade_figure.update_layout(
                title=trade_title,
                xaxis_title=trade_x_title,
                yaxis_title=trade_y_title,
                template="plotly_dark",
                height=390,
                paper_bgcolor="#081524",
                plot_bgcolor="#081524",
                font={"color": "#e9f2ff"},
                title_font={"color": "#ffffff", "size": 18},
            )
            st.plotly_chart(trade_figure, width="stretch")

        st.dataframe(candidate_frame, hide_index=True, width="stretch")
        flattened_samples = pd.DataFrame(surface_sample_rows(physical_result))
        download_columns = st.columns(4)
        download_columns[0].download_button(
            "Boom + terrain · CSV",
            flattened_samples.to_csv(index=False),
            file_name=f"{physical_result.run_id}_surface_samples.csv",
            mime="text/csv",
            width="stretch",
        )
        download_columns[1].download_button(
            "Ground intersections · GeoJSON",
            json.dumps(footprint_geojson(physical_result), indent=2),
            file_name=f"{physical_result.run_id}_footprint.geojson",
            mime="application/geo+json",
            width="stretch",
        )
        download_columns[2].download_button(
            "Complete analysis · JSON",
            physical_result.model_dump_json(indent=2),
            file_name=f"{physical_result.run_id}_physical_result.json",
            mime="application/json",
            width="stretch",
        )
        download_columns[3].download_button(
            "Evidence package · ZIP",
            evidence_zip(physical_result, physical_request),
            file_name=f"{physical_result.run_id}_evidence.zip",
            mime="application/zip",
            width="stretch",
        )

with provenance_tab:
    st.markdown("#### Real input lineage")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Input": "Route baseline",
                    "Source": "OpenSky Network REST API",
                    "Time": observed_window,
                    "State": "LOADED",
                },
                {
                    "Input": "Atmosphere",
                    "Source": f"NOAA {analysis.noaa_model} via Herbie",
                    "Time": f"{len(analysis.noaa_requests)} archived valid times",
                    "State": "LOADED",
                },
                {
                    "Input": "Terrain",
                    "Source": "USGS 3DEP",
                    "Time": "Not time-varying",
                    "State": f"{terrain_loaded}/{len(rows)} REGIONS LOADED",
                },
                {
                    "Input": "Aircraft phase profile",
                    "Source": selected_aircraft.display_name,
                    "Time": f"{len(flight_plan.scenes)} source-backed scenes",
                    "State": "LOADED · RESEARCH ESTIMATE",
                },
                {
                    "Input": "Aircraft near-field signature",
                    "Source": (
                        selected_aircraft.value("Nearfield Signature File") or "Not supplied"
                    ),
                    "Time": "—",
                    "State": (
                        f"LOADED · {len(selected_aircraft.nearfield_samples):,} SAMPLES"
                        if selected_aircraft.nearfield_ready
                        else "MISSING"
                    ),
                },
                {
                    "Input": "Physical propagation",
                    "Source": (
                        "Not connected"
                        if physical_result is None
                        else f"{physical_result.solver.name} {physical_result.solver.version}"
                    ),
                    "Time": "—",
                    "State": (
                        "NOT CALCULATED"
                        if physical_result is None
                        else f"{physical_result.solver.validation_status} · {physical_result.baseline.classification}"
                    ),
                },
            ]
        ),
        hide_index=True,
        width="stretch",
    )
    with st.expander("NOAA request details"):
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Model": plan.model,
                        "Cycle UTC": plan.model_cycle.isoformat(),
                        "Lead": f"+{plan.forecast_hour} h",
                        "Valid UTC": plan.valid_time.isoformat(),
                        "Member": plan.member,
                        "Time matching": plan.temporal_match,
                        "Coverage": plan.coverage,
                    }
                    for plan in analysis.noaa_requests
                ]
            ),
            hide_index=True,
            width="stretch",
        )
        st.caption(
            "Automatic policy v1 · maximum 15 mi sampling · 1 °F · 0.02 inHg · 3 kt. Exact thresholds are evidence metadata, not user controls."
        )
    with st.expander("Route provenance"):
        st.json(source.model_dump(mode="json"))

with how_tab:
    st.markdown("#### What this run does")
    st.markdown(
        """
1. Loads a real timestamped OpenSky track and stops if none is available.
2. Retains every observation timestamp and the complete route polyline.
3. Adds route samples only where needed to keep spacing at or below 15 miles.
4. Matches archived HRRR or GEFS columns to each sample's changing UTC position and time.
5. Starts a new automatic atmospheric region when a variable or NOAA model boundary crosses policy v1.
6. Requests USGS 3DEP terrain where U.S. coverage may exist and records every unavailable region.
7. Applies the editable NASA STCA Mach/altitude scene profile and matches NOAA to each scene.
8. Calculates a research travel-time estimate from local speed of sound and route-aligned wind.
9. Builds a checksum-bound physical request containing aircraft near-field data, NOAA columns, 3DEP terrain, flight state, all three ray families, and strategic route/time/altitude candidates.
10. Runs a registered sBOOM/PCBoom wrapper or imports its strict result; otherwise stops without inventing a footprint.
11. Recommends a route only when every requested ray family is complete, the uncertainty upper bound is at or below 0.11 psf, and the solver is marked VALIDATED.
"""
    )
    st.info(
        "NASA Profile 1, Profile 2, and Standard Atmosphere are validation benchmarks. Operational route calculations use position- and time-matched NOAA weather."
    )

with evidence_tab:
    evidence = _evidence_payload(
        mission_id,
        analysis,
        rows,
        selected_aircraft,
        flight_plan,
        tuple(planned_noaa_requests),
    )
    evidence["physical_solver_request"] = {
        "checksum": physical_request_checksum,
        "coverage_summary": physical_request["coverage_summary"],
        "acceptance": physical_request["acceptance"],
    }
    if physical_result is not None:
        evidence["sonic_boom"] = physical_result.model_dump(mode="json")
        evidence["compliant_operating_corridor"] = {
            "status": physical_result.baseline.classification,
            "recommended_candidate_id": physical_result.recommended_candidate_id,
            "solver_validation": physical_result.solver.validation_status,
        }
    st.markdown("#### Evidence-ready real inputs")
    st.dataframe(
        pd.DataFrame(
            [
                {"Outcome": "Route baseline", "Status": "OPEN SKY OBSERVED · LOADED"},
                {
                    "Outcome": "Time-varying atmosphere",
                    "Status": f"NOAA {analysis.noaa_model} · LOADED",
                },
                {"Outcome": "Automatic atmospheric regions", "Status": f"{len(rows)} · POLICY V1"},
                {"Outcome": "Terrain", "Status": f"{terrain_loaded}/{len(rows)} REGIONS LOADED"},
                {
                    "Outcome": "Planned route",
                    "Status": "OPEN SKY BASELINE · AIRCRAFT 1 PHASE ESTIMATE",
                },
                {
                    "Outcome": "Physical operating corridor",
                    "Status": (
                        "NOT CALCULATED"
                        if physical_result is None
                        else physical_result.baseline.classification
                    ),
                },
                {
                    "Outcome": "Ground waveform / overpressure",
                    "Status": (
                        "NOT CALCULATED"
                        if physical_result is None
                        else f"{len(surface_sample_rows(physical_result)):,} SAMPLES · {physical_result.solver.validation_status}"
                    ),
                },
            ]
        ),
        hide_index=True,
        width="stretch",
    )
    st.download_button(
        "Export real-input evidence · JSON",
        json.dumps(evidence, indent=2, default=str),
        file_name=f"machlane_{mission_id}_{observed_start:%Y%m%d}_real_input_evidence.json",
        mime="application/json",
        width="stretch",
    )
    st.caption(
        f"Policy identifier · {AUTOMATIC_WEATHER_POLICY_VERSION} · maximum sample interval {AUTOMATIC_WEATHER_SAMPLE_SPACING_M * METERS_TO_MILES:.0f} mi"
    )
