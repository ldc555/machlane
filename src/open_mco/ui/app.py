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

from open_mco.mission_analysis import RealMissionAnalysis, build_real_mission_analysis
from open_mco.models import Route
from open_mco.route import (
    AUTOMATIC_WEATHER_POLICY_VERSION,
    AUTOMATIC_WEATHER_SAMPLE_SPACING_M,
    AUTOMATIC_WEATHER_SETTINGS,
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
    display_longitude,
    observed_flight_state,
    pressure_color,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
OPENSKY_CACHE = OpenSkyRouteCache(PROJECT_ROOT / "data/cache/opensky_routes")
OPENSKY_LOOKBACK_DAYS = 7
ANALYSIS_CACHE_SCHEMA = "real-spacetime-noaa-3dep-v2-sparse-preview"

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
        "planned_route": {"status": "NOT SUPPLIED"},
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
:root { --ink:#e8f0f8; --muted:#9aafc4; --panel:#101a27; --line:#2a3a4e; --teal:#2dd4bf; --amber:#fbbf24; }
.stApp { background:radial-gradient(circle at 50% -20%,#192b40 0,#0a111b 43%,#070c13 100%); color:var(--ink); }
[data-testid="stHeader"] { background:transparent; }
[data-testid="stToolbar"],[data-testid="stStatusWidget"] { display:none; }
.block-container { max-width:1600px; padding:1rem 1.5rem 3rem; }
.brand-row { display:flex; justify-content:space-between; align-items:center; margin:.1rem 0 .75rem; }
.brand { display:flex; align-items:center; gap:.7rem; font-weight:800; letter-spacing:.01em; }
.brand-mark { width:2rem; height:2rem; display:grid; place-items:center; border:1px solid #3c556f; border-radius:.55rem; color:var(--teal); background:#101c2a; }
.run-state { color:#a9bed2; font:700 .7rem/1 ui-monospace,SFMono-Regular,monospace; letter-spacing:.1em; }
.notice { border:1px solid #815d2e; background:#2a2014; color:#ffdda4; padding:.55rem .8rem; border-radius:.6rem; font-size:.78rem; margin-bottom:.8rem; }
.source-banner { border:1px solid #25655f; background:linear-gradient(90deg,#123936,#0e1d29); color:#d5fff8; padding:.65rem .8rem; border-radius:.6rem; font-size:.78rem; margin:.5rem 0 .75rem; }
.section-kicker { color:#7f98b0; font:700 .68rem/1.2 ui-monospace,SFMono-Regular,monospace; letter-spacing:.12em; text-transform:uppercase; margin:.3rem 0 .65rem; }
.status-card { border:1px solid var(--line); background:linear-gradient(145deg,#142235,#0d1722); border-radius:.7rem; padding:.8rem .9rem; margin:.35rem 0 .65rem; }
.status-card .label { color:#a9bdd0; font-size:.66rem; font-weight:750; letter-spacing:.1em; text-transform:uppercase; }
.status-card .value { color:#f4f8fc; font-size:1.05rem; font-weight:800; margin:.18rem 0; }
.status-card .meta { color:#a8bbce; font-size:.73rem; line-height:1.42; }
.layer-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:.6rem; margin:.6rem 0 .8rem; }
.layer { border:1px solid var(--line); background:#0c1520; border-radius:.65rem; padding:.75rem; }
.layer b { display:block; color:#e9f1f8; font-size:.8rem; margin-bottom:.25rem; }
.layer span { color:#94a9bd; font-size:.72rem; line-height:1.4; }
.pending { display:inline-block; padding:.24rem .46rem; color:#ffd38a; background:#3a2b16; border:1px solid #795a28; border-radius:99px; font:700 .65rem/1 ui-monospace,SFMono-Regular,monospace; }
[data-testid="stMetric"] { background:linear-gradient(145deg,#152437,#0e1825); border:1px solid #405672; padding:.72rem .82rem; border-radius:.65rem; }
[data-testid="stMetricLabel"],[data-testid="stMetricLabel"] * { color:#d8e5f2 !important; font-weight:700 !important; }
[data-testid="stMetricValue"],[data-testid="stMetricValue"] * { color:#fff !important; font-size:1.28rem; font-weight:800 !important; }
[data-testid="stMetricDelta"],[data-testid="stMetricDelta"] * { color:#c2d2e3 !important; opacity:1 !important; }
.stTabs [data-baseweb="tab-list"] { gap:.35rem; border-bottom:1px solid var(--line); }
.stTabs [data-baseweb="tab"] { color:#91a6ba; }
.stTabs [aria-selected="true"] { color:#eef7ff; }
.stButton button { border:1px solid #2f6e69; background:#123a38; color:#d8fffa; }
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

missions = list_missions()
control_mission, control_timeline, control_aircraft, control_action = st.columns(
    [1.45, 1.25, 1.35, 1], vertical_alignment="bottom"
)
with control_mission:
    mission_id = st.selectbox(
        "Mission",
        [mission.mission_id for mission in missions],
        format_func=lambda value: get_mission(value).label,
    )
mission = get_mission(mission_id)
yesterday = datetime.now(UTC).date() - timedelta(days=1)
with control_timeline:
    timeline_mode = st.selectbox(
        "Timeline",
        ["Historical OpenSky replay", "Proposed departure"],
    )
with control_aircraft:
    st.selectbox(
        "Aircraft",
        ["NASA STCA 55T · specification incomplete"],
        help="The current workbook is not complete enough for an aircraft-specific propagation run.",
    )

if timeline_mode == "Historical OpenSky replay":
    observed_date = st.date_input(
        "OpenSky search ending (UTC)",
        value=yesterday,
        min_value=yesterday - timedelta(days=29),
        max_value=yesterday,
    )
    proposed_departure: datetime | None = None
else:
    proposed_day, proposed_clock = st.columns(2)
    with proposed_day:
        planned_date = st.date_input("Proposed departure date (UTC)", value=yesterday + timedelta(days=1))
    with proposed_clock:
        planned_time = st.time_input("Proposed departure time (UTC)", value=time(15, 0))
    proposed_departure = datetime.combine(planned_date, planned_time, tzinfo=UTC)
    observed_date = yesterday
    st.error(
        "Proposed-flight analysis is paused: the selected aircraft has no reviewed climb, acceleration, cruise, and descent schedule. MachLane will not invent one."
    )

cached_route = OPENSKY_CACHE.load(
    mission_id,
    observed_date,
    origin_icao=mission.origin.icao,
    destination_icao=mission.destination.icao,
)
credentials_configured = bool(
    _credential("OPENSKY_CLIENT_ID") and _credential("OPENSKY_CLIENT_SECRET")
)
can_run = timeline_mode == "Historical OpenSky replay" and (
    cached_route is not None or credentials_configured
)
with control_action:
    run_analysis = st.button("Run real analysis", disabled=not can_run, width="stretch")

if timeline_mode == "Proposed departure":
    st.info(
        f"A real OpenSky baseline is required, but {proposed_departure:%Y-%m-%d %H:%M UTC} cannot be sampled until aircraft-specific route timing is reviewed."
    )
    st.stop()

if cached_route is not None:
    source = cached_route.source
    retrieved = "unknown" if source is None else source.retrieved_at.strftime("%Y-%m-%d %H:%M UTC")
    st.caption(f"Real OpenSky route is available in the private cache · retrieved {retrieved}. Refresh requires OpenSky credentials.")
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
elif route_state_key not in st.session_state and cached_route is not None:
    st.session_state[route_state_key] = cached_route.model_dump_json()

route_json = st.session_state.get(route_state_key)
if not isinstance(route_json, str):
    st.info("Select the mission and date, then run the real-data analysis.")
    st.stop()

observed_route = Route.model_validate_json(route_json)
source = observed_route.source
if source is None or source.provider != "opensky" or source.data_kind != "observed_track":
    st.error("Route provenance failed: the loaded geometry is not a normalized OpenSky observation.")
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

st.markdown(
    f'<div class="source-banner"><b>Real inputs loaded.</b> OpenSky {callsign} · NOAA {analysis.noaa_model} matched throughout {observed_start:%H:%M}–{observed_end:%H:%M} UTC · automatic atmospheric regions · available USGS 3DEP terrain previews.</div>',
    unsafe_allow_html=True,
)

summary = st.columns(5)
summary[0].metric("Route", "OpenSky observed", f"{callsign} · {point_count:,} points", delta_color="off")
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

st.markdown(
    """
<div class="layer-grid">
  <div class="layer"><b>1 · Historical route</b><span>Real OpenSky observed trajectory, timestamps, altitude observations, checksum, and limitations.</span></div>
  <div class="layer"><b>2 · Planned route</b><span>Not supplied. A future plan must retain its own departure time and reviewed aircraft phase schedule.</span></div>
  <div class="layer"><b>3 · Compliant operating corridor</b><span><span class="pending">NOT CALCULATED</span><br/>Atmospheric regions are not a compliant corridor.</span></div>
</div>
""",
    unsafe_allow_html=True,
)

atmosphere_layers = [
    pdk.Layer(
        "PathLayer",
        rows,
        id="automatic-atmospheric-regions",
        get_path="path",
        get_color="color",
        get_width=5,
        width_units="pixels",
        width_min_pixels=4,
        pickable=True,
    ),
    pdk.Layer(
        "ScatterplotLayer",
        [
            {
                "position": row["path"][0],
                "region": row["region"],
                "boundary_reason": row["boundary_reason"],
                "pressure_inhg": f'{row["pressure_inhg"]:.3f}',
            }
            for row in rows[1:]
        ],
        id="automatic-region-boundaries",
        get_position="position",
        get_radius=4,
        radius_units="pixels",
        get_fill_color=[244, 248, 252, 255],
        get_line_color=[7, 12, 19, 255],
        line_width_min_pixels=1,
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
            "position": [display_longitude(mission.origin.longitude, map_longitude), mission.origin.latitude],
            "label": mission.origin.iata,
        },
        {
            "position": [display_longitude(mission.destination.longitude, map_longitude), mission.destination.latitude],
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
            "Inspect observed flight position",
            0,
            100,
            value=24,
            step=1,
            format="%d%%",
            key="aircraft_progress_pct",
        )
        progress = percent / 100
        active_index = active_segment_index(analysis.atmospheric_route, progress)
        active_row = rows[active_index]
        profile = analysis.segment_atmospheres[active_index]
        aircraft = aircraft_view(observed_route, progress, map_longitude)
        state = observed_flight_state(observed_route, progress)
        altitude_m = state["altitude_m"]
        sampled_altitude_m = (
            AUTOMATIC_WEATHER_SETTINGS.altitude_m
            if altitude_m is None
            else float(altitude_m)
        )
        weather = atmosphere_metrics(profile, sampled_altitude_m, aircraft["bearing_deg"])
        altitude_label = (
            "Unavailable from OpenSky"
            if state["altitude_ft"] is None
            else f'{float(state["altitude_ft"]):,.0f} ft'
        )
        timestamp = state["timestamp"]

        st.markdown('<div class="section-kicker">Route-aligned ambient atmosphere</div>', unsafe_allow_html=True)
        live = st.columns(4)
        live[0].metric("Ambient pressure", f'{weather["pressure_inhg"]:.3f} inHg')
        live[1].metric("Temperature", f'{weather["temperature_f"]:.1f} °F')
        live[2].metric("Wind speed", f'{weather["wind_speed_kt"]:.0f} kt')
        live[3].metric("Along-track wind", f'{weather["along_wind_kt"]:+.0f} kt')

        st.subheader("Observed route and automatic atmospheric regions")
        st.caption(
            "Yellow = historical OpenSky track. Thin blue-to-red sections = ambient pressure at the 50,000 ft research reference altitude. They are not boom-safe or boom-unsafe areas."
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
                layers=[*atmosphere_layers, observed_layer, label_layer, aircraft_layer],
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
        st.markdown('<div class="section-kicker">Observed flight inspector</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="status-card"><div class="label">OpenSky phase</div><div class="value">{state["phase"]}</div><div class="meta">{altitude_label} · Mach unavailable from this API</div></div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="status-card"><div class="label">UTC position time</div><div class="value">{timestamp:%H:%M:%S UTC}</div><div class="meta">{timestamp:%A · %B %d, %Y}</div></div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="status-card"><div class="label">NOAA atmosphere</div><div class="value">{analysis.noaa_model} · {profile.valid_time:%H:%M UTC}</div><div class="meta">Region {active_row["region"]} · real model data</div></div>',
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
        st.caption(
            f'{percent}% · {aircraft["latitude"]:.2f}°, {aircraft["longitude"]:.2f}° · track {aircraft["bearing_deg"]:.0f}°'
        )


render_workspace()

regions_tab, atmosphere_tab, terrain_tab, provenance_tab, how_tab, evidence_tab = st.tabs(
    ["Atmospheric regions", "Atmosphere", "Terrain", "Data provenance", "How it works", "Evidence"]
)

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
    profile_index = next(index for index, row in enumerate(rows) if row["region"] == selected_region)
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
                "Resolution": None if result.profile is None else f"{result.profile.source.resolution_m:g} m",
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
                    "Input": "Historical route",
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
7. Stops before boom propagation because no reviewed near-field signature and validated nonlinear engine are connected.
"""
    )
    st.info(
        "Adaptive refinement based on predicted ground overpressure will replace simple atmospheric thresholds only after a physical propagation engine exists."
    )

with evidence_tab:
    evidence = _evidence_payload(mission_id, analysis, rows)
    st.markdown("#### Evidence-ready real inputs")
    st.dataframe(
        pd.DataFrame(
            [
                {"Outcome": "Historical route", "Status": "OPEN SKY OBSERVED · LOADED"},
                {"Outcome": "Time-varying atmosphere", "Status": f"NOAA {analysis.noaa_model} · LOADED"},
                {"Outcome": "Automatic atmospheric regions", "Status": f"{len(rows)} · POLICY V1"},
                {"Outcome": "Terrain", "Status": f"{terrain_loaded}/{len(rows)} REGIONS LOADED"},
                {"Outcome": "Planned route", "Status": "NOT SUPPLIED"},
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
