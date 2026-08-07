"""Real-data-only mission workspace for MachLane."""

from __future__ import annotations

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
from open_mco.route import (
    AUTOMATIC_WEATHER_POLICY_VERSION,
    AUTOMATIC_WEATHER_SAMPLE_SPACING_M,
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
    search_date: date,
    *,
    force_refresh: bool,
) -> Route:
    mission = get_mission(mission_id)
    if not force_refresh:
        cached = OPENSKY_CACHE.load(
            mission_id,
            search_date,
            origin_icao=mission.origin.icao,
            destination_icao=mission.destination.icao,
        )
        if cached is not None:
            return cached
    provider = OpenSkyTrackProvider(
        network_enabled=True,
        client_id=_credential("OPENSKY_CLIENT_ID"),
        client_secret=_credential("OPENSKY_CLIENT_SECRET"),
    )
    search_end = datetime.combine(search_date, time.max, tzinfo=UTC)
    route = provider.recent_route_for_airports(
        mission.origin,
        mission.destination,
        on_or_before=search_end,
        lookback_days=OPENSKY_LOOKBACK_DAYS,
    )
    OPENSKY_CACHE.save(mission_id, search_date, route)
    return route


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
<div class="notice"><b>Research prototype — not FAA approved.</b> Real route, atmosphere, and available terrain are loaded below. Surface overpressure and sonic-boom compliance are not calculated.</div>
""",
    unsafe_allow_html=True,
)

navigation_left, navigation_right = st.columns([4.6, 1.4], vertical_alignment="center")
selected_aircraft = AIRCRAFT_STORE.load("aircraft_one")
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

workspace_missions = {
    item.mission_id: item for item in list_missions() if item.mission_id in WORKSPACE_MISSION_IDS
}
control_mission, control_date, control_aircraft, control_action = st.columns(
    [1.35, 1.25, 1.6, 1], vertical_alignment="bottom"
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
with control_date:
    observed_date = st.date_input(
        "OpenSky search ending (UTC)",
        value=yesterday,
        min_value=yesterday - timedelta(days=29),
        max_value=yesterday,
    )
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
credentials_configured = bool(
    _credential("OPENSKY_CLIENT_ID") and _credential("OPENSKY_CLIENT_SECRET")
)
can_run = cached_route is not None or credentials_configured
with control_action:
    run_analysis = st.button("Run real analysis", disabled=not can_run, width="stretch")

if cached_route is not None:
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

route_state_key = f"real-route:{mission_id}:{observed_date.isoformat()}"
if run_analysis:
    try:
        with st.spinner("Loading the real OpenSky track…"):
            route = _load_or_fetch_route(mission_id, observed_date, force_refresh=False)
        st.session_state[route_state_key] = route.model_dump_json()
    except (RuntimeError, ValueError, OSError) as exc:
        st.error(f"OpenSky route unavailable: {exc}")
        st.stop()

route_json = st.session_state.get(route_state_key)
if not isinstance(route_json, str):
    st.info(
        "The OpenSky route is ready. Click **Run real analysis** when you want MachLane to load and match NOAA weather and available 3DEP terrain."
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
non_approach = [
    point for point in selected_aircraft.phase_profile if point.phase.lower() != "approach"
]
for index, point in enumerate(non_approach):
    if "cruise" in point.phase.lower():
        phase_progress[point.sequence] = 0.5
    else:
        denominator = max(1, len(non_approach) - 1)
        phase_progress[point.sequence] = min(0.38, index / denominator * 0.38)
for point in selected_aircraft.phase_profile:
    if point.phase.lower() == "approach":
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

climb_phase, cruise_phase, _, approach_phase = preliminary_flight_plan.phases
climb_end_progress = climb_phase.distance_miles / route_distance_miles
cruise_mid_progress = (
    climb_phase.distance_miles + cruise_phase.distance_miles / 2
) / route_distance_miles
approach_mid_progress = 1 - approach_phase.distance_miles / route_distance_miles / 2
ascent_points = selected_aircraft.phase_profile[:-2]
for index, point in enumerate(ascent_points):
    phase_progress[point.sequence] = climb_end_progress * index / max(1, len(ascent_points) - 1)
phase_progress[selected_aircraft.phase_profile[-2].sequence] = cruise_mid_progress
phase_progress[selected_aircraft.phase_profile[-1].sequence] = approach_mid_progress

climb_minutes = climb_phase.duration_min
planned_scene_times: dict[int, datetime] = {}
for index, point in enumerate(ascent_points):
    elapsed_min = climb_minutes * index / max(1, len(ascent_points) - 1)
    planned_scene_times[point.sequence] = observed_start + timedelta(minutes=elapsed_min)
cruise_scene = selected_aircraft.phase_profile[-2]
planned_scene_times[cruise_scene.sequence] = observed_start + timedelta(
    minutes=climb_minutes + preliminary_flight_plan.cruise_time_min / 2
)
approach_scene = selected_aircraft.phase_profile[-1]
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
    "NOT CALCULATED",
    "Near-field + validated propagation required",
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

st.markdown(
    f"""
<div class="layer-grid">
  <div class="layer"><b>1 · Planned flight</b><span>{selected_aircraft.value('Aircraft Name') or selected_aircraft.display_name} phase profile applied to the real OpenSky baseline geometry · {flight_plan.block_time_min / 60:.2f} hr estimated block time.</span></div>
  <div class="layer"><b>2 · Compliant operating corridor</b><span><span class="pending">NOT CALCULATED</span><br/>Atmospheric regions are not a compliant corridor.</span></div>
</div>
""",
    unsafe_allow_html=True,
)

alert_left, alert_right = st.columns([3, 1], vertical_alignment="center")
with alert_left:
    st.warning(
        "Alert mode: a route variation cannot be recommended yet. Real NOAA and terrain are inputs, but the aircraft near-field signature and validated nonlinear propagation result are missing."
    )
with alert_right:
    st.toggle(
        "Suggested variation",
        value=False,
        disabled=True,
        help="Unlocked only after a physical, uncertainty-aware sonic-boom calculation identifies a constraint violation.",
    )

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
            '<div class="zone-legend">Yellow = exact real OpenSky baseline. Blue → red = lower → higher flight-level ambient pressure. Translucent interiors preserve map context; bright edges delineate automatic atmospheric regions. These are not boom-safe or boom-unsafe areas.</div>',
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
                    observed_layer,
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
                    "html": "<b>{region}</b><br/>Ambient pressure {pressure_inhg} inHg<br/>{boundary_reason}",
                    "style": {"backgroundColor": "#0b1420", "color": "#e5eef8"},
                },
            ),
            height=500,
        )
        st.caption(
            f"Ambient-pressure scale: {pressure_min * 100 * PASCALS_TO_INHG:.3f}–{pressure_max * 100 * PASCALS_TO_INHG:.3f} inHg · exact retained route polyline · no surface-boom footprint"
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
        st.markdown(
            '<div class="status-card"><div class="label">Surface boom</div><div class="value"><span class="pending">NOT CALCULATED</span></div><div class="meta">No ground waveform or overpressure metric</div></div>',
            unsafe_allow_html=True,
        )


render_workspace()

flight_tab, regions_tab, atmosphere_tab, terrain_tab, provenance_tab, how_tab, evidence_tab = (
    st.tabs(
        [
            "Flight plan",
            "Atmospheric regions",
            "Atmosphere",
            "Terrain",
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
                    "Source": "Not supplied",
                    "Time": "—",
                    "State": "MISSING",
                },
                {
                    "Input": "Physical propagation",
                    "Source": "Not connected",
                    "Time": "—",
                    "State": "NOT CALCULATED",
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
9. Stops before boom propagation because no reviewed near-field signature and validated nonlinear engine are connected.
"""
    )
    st.info(
        "Adaptive refinement based on predicted ground overpressure will replace simple atmospheric thresholds only after a physical propagation engine exists."
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
                {"Outcome": "Compliant operating corridor", "Status": "NOT CALCULATED"},
                {"Outcome": "Ground waveform / overpressure", "Status": "NOT CALCULATED"},
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
