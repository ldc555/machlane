"""Map-first mission workspace for observed routes and route-aligned atmosphere."""

from __future__ import annotations

import math
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar

import pandas as pd
import plotly.graph_objects as go
import pydeck as pdk
import streamlit as st

from open_mco.atmosphere import (
    SyntheticAtmosphereProvider,
    build_noaa_provider,
    plan_noaa_atmosphere,
)
from open_mco.compliance import compliance_matrix, write_evidence_package
from open_mco.demo import build_demo_scenario
from open_mco.models import Route
from open_mco.route import (
    AIRPORT_SOURCE_RETRIEVED,
    AIRPORT_SOURCE_URL,
    OpenSkyRouteCache,
    OpenSkyTrackProvider,
    WeatherSegmentationSettings,
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
    mock_flight_state,
    mock_live_metrics,
    pressure_color,
    segment_rows,
)

OPENSKY_LOOKBACK_DAYS = 7
PROJECT_ROOT = Path(__file__).resolve().parents[3]
OPENSKY_CACHE = OpenSkyRouteCache(PROJECT_ROOT / "data/cache/opensky_routes")

# Streamlit renamed ``experimental_fragment`` to ``fragment`` in 1.37. Prefer the stable name, fall
# back to the experimental one, and degrade to a no-op decorator on older pins (the page simply
# reruns in full, exactly as before, so nothing breaks — dragging is just less responsive).
_FragmentFunc = TypeVar("_FragmentFunc", bound=Callable[..., Any])


def _identity_fragment(func: _FragmentFunc) -> _FragmentFunc:
    return func


fragment: Any = (
    getattr(st, "fragment", None)
    or getattr(st, "experimental_fragment", None)
    or _identity_fragment
)

st.set_page_config(
    page_title="MachLane · Mission workspace",
    page_icon="✦",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
<style>
:root { --ink:#e5eef8; --muted:#8ba0b7; --panel:#101a27; --line:#233247; --teal:#2dd4bf; }
.stApp { background: radial-gradient(circle at 50% -20%, #18283b 0, #0a111b 42%, #070c13 100%); color:var(--ink); }
[data-testid="stHeader"] { background:transparent; }
[data-testid="stToolbar"], [data-testid="stStatusWidget"] { display:none; }
.block-container { max-width:1600px; padding:1rem 1.5rem 3rem; }
h1,h2,h3 { letter-spacing:-.02em; }
.brand-row { display:flex; align-items:center; justify-content:space-between; margin:.15rem 0 .75rem; }
.brand { display:flex; align-items:center; gap:.75rem; font-size:1.05rem; font-weight:750; letter-spacing:.02em; }
.brand-mark { width:2rem; height:2rem; display:grid; place-items:center; border:1px solid #3a536d; border-radius:.55rem; color:var(--teal); background:#101c2a; }
.run-state { color:#c6d5e5; font:700 .72rem/1 ui-monospace,SFMono-Regular,monospace; letter-spacing:.08em; text-transform:uppercase; }
.notice { border:1px solid #855b27; background:#291f13; color:#ffd898; padding:.55rem .8rem; border-radius:.6rem; font-size:.78rem; margin-bottom:.85rem; }
.mode-banner { min-height:3.35rem; display:flex; align-items:center; gap:.65rem; border-radius:.65rem; padding:.65rem .85rem; margin:.1rem 0 .75rem; font-size:.78rem; }
.mode-banner b { letter-spacing:.1em; font:800 .72rem/1 ui-monospace,SFMono-Regular,monospace; }
.mode-banner.mock { color:#fff4b8; border:1px solid #d9a900; background:linear-gradient(90deg,#3b2e08,#211c0d); }
.mode-banner.baseline { color:#d8e8f7; border:1px solid #405873; background:#101b29; }
.section-kicker { color:#6f879e; font:700 .68rem/1.2 ui-monospace,SFMono-Regular,monospace; letter-spacing:.12em; text-transform:uppercase; margin:.3rem 0 .65rem; }
.panel-note { padding:.65rem .8rem; border:1px solid var(--line); background:#0c1520; border-radius:.6rem; color:#9fb2c7; font-size:.76rem; line-height:1.45; }
.mission-strip { display:flex; gap:.9rem; flex-wrap:wrap; padding:.58rem .75rem; margin:.35rem 0 .65rem; border:1px solid var(--line); background:#0c1520; border-radius:.55rem; color:#8ba0b7; font-size:.72rem; }
.mission-strip b { color:#dbe8f4; font-weight:650; }
.status-card { border:1px solid var(--line); background:linear-gradient(145deg,#111d2b,#0c1520); border-radius:.7rem; padding:.8rem .9rem; margin:.35rem 0 .65rem; }
.status-card .label { color:#b8c9da; font-size:.68rem; font-weight:700; letter-spacing:.1em; text-transform:uppercase; }
.status-card .value { color:#e7f8f5; font-size:1.15rem; font-weight:750; margin:.15rem 0; }
.status-card .meta { color:#b3c4d5; font-size:.74rem; }
.calc-grid { display:grid; grid-template-columns:repeat(5,1fr); gap:.55rem; margin:.4rem 0 .8rem; }
.calc-step { border:1px solid var(--line); background:#0d1722; border-radius:.65rem; padding:.75rem; min-height:7rem; }
.calc-step .number { color:var(--teal); font:700 .68rem/1 ui-monospace,SFMono-Regular,monospace; letter-spacing:.1em; }
.calc-step b { display:block; color:#e5eef8; margin:.45rem 0 .25rem; font-size:.85rem; }
.calc-step span { color:#8ba0b7; font-size:.73rem; line-height:1.4; }
.eligible { display:inline-block; padding:.24rem .46rem; color:#8ff5e8; background:#123c3a; border:1px solid #1c716a; border-radius:99px; font:700 .66rem/1 ui-monospace,SFMono-Regular,monospace; }
.pending { display:inline-block; padding:.24rem .46rem; color:#ffd38a; background:#3a2b16; border:1px solid #795a28; border-radius:99px; font:700 .66rem/1 ui-monospace,SFMono-Regular,monospace; }
[data-testid="stMetric"] { background:linear-gradient(145deg,#152437,#0e1825); border:1px solid #405672; padding:.72rem .82rem; border-radius:.65rem; box-shadow:0 8px 24px rgba(0,0,0,.16); }
[data-testid="stMetricLabel"], [data-testid="stMetricLabel"] * { color:#d8e5f2 !important; font-weight:700 !important; }
[data-testid="stMetricValue"], [data-testid="stMetricValue"] * { color:#ffffff !important; font-size:1.42rem; font-weight:800 !important; text-shadow:0 1px 12px rgba(255,255,255,.08); }
[data-testid="stMetricDelta"], [data-testid="stMetricDelta"] * { color:#c2d2e3 !important; opacity:1 !important; }
[data-testid="stToggle"] label, [data-testid="stToggle"] p { color:#f4f8fc !important; font-weight:750 !important; }
[data-testid="stVerticalBlockBorderWrapper"] { border-color:var(--line); background:#0c1520; }
.stTabs [data-baseweb="tab-list"] { gap:.4rem; border-bottom:1px solid var(--line); }
.stTabs [data-baseweb="tab"] { color:#8da2b7; height:2.7rem; }
.stTabs [aria-selected="true"] { color:#eaf5ff; }
.stButton button { border:1px solid #2f6e69; background:#123a38; color:#d8fffa; }
@media (max-width:900px) { .calc-grid { grid-template-columns:repeat(2,1fr); } .block-container { padding:.8rem; } }
</style>
""",
    unsafe_allow_html=True,
)


SCENARIO_CACHE_SCHEMA = "weather-regimes-v4-noaa-imperial"

WEATHER_PRESETS = {
    "Tight": (
        15 * 1609.344,
        WeatherSegmentationSettings(
            temperature_change_k=0.56,
            pressure_change_hpa=0.68,
            wind_vector_change_mps=1.54,
        ),
        "15 mi samples · 1 °F · 0.02 inHg · 3 kt",
    ),
    "Balanced": (
        25 * 1609.344,
        WeatherSegmentationSettings(
            temperature_change_k=1.1,
            pressure_change_hpa=1.02,
            wind_vector_change_mps=2.57,
        ),
        "25 mi samples · 2 °F · 0.03 inHg · 5 kt",
    ),
    "Broad": (
        40 * 1609.344,
        WeatherSegmentationSettings(
            temperature_change_k=2.2,
            pressure_change_hpa=2.03,
            wind_vector_change_mps=5.14,
        ),
        "40 mi samples · 4 °F · 0.06 inHg · 10 kt",
    ),
}

PHASE_ANCHORS = (
    ("Takeoff", 0),
    ("Climb", 7),
    ("Accelerate", 14),
    ("Cruise", 50),
    ("Descent", 91),
    ("Landing", 100),
)


def _set_aircraft_position(percent: int) -> None:
    st.session_state["aircraft_progress_pct"] = percent


def _imperial_boundary_reason(reason: str) -> str:
    match = re.search(r"([0-9.]+) (K|hPa|m/s)$", reason)
    if match is None:
        return reason
    value = float(match.group(1))
    unit = match.group(2)
    if unit == "K":
        replacement = f"{value * 1.8:.1f} °F"
    elif unit == "hPa":
        replacement = f"{value * 100 * PASCALS_TO_INHG:.3f} inHg"
    else:
        replacement = f"{value * 1.943844492:.1f} kt"
    return f"{reason[: match.start(1)]}{replacement}"


@st.cache_resource(show_spinner=False)
def scenario(
    mission_id: str,
    cache_schema: str,
    route_json: str,
    atmosphere_mode: str,
    weather_preset: str,
):
    """Run one normalized observed route with an explicit atmosphere source."""

    del cache_schema
    route_override = Route.model_validate_json(route_json)
    plan = plan_noaa_atmosphere(route_override, get_mission(mission_id).domain)
    spacing_m, settings, _ = WEATHER_PRESETS[weather_preset]
    provider = (
        build_noaa_provider(
            plan,
            cache_dir=PROJECT_ROOT / "data/cache/herbie",
            network_enabled=True,
        )
        if atmosphere_mode == "NOAA archived"
        else SyntheticAtmosphereProvider()
    )
    return build_demo_scenario(
        mission_id,
        route_override=route_override,
        atmosphere_provider=provider,
        valid_time=plan.valid_time,
        weather_sample_spacing_m=spacing_m,
        weather_settings=settings,
    )


st.markdown(
    """
<div class="brand-row"><div class="brand"><span class="brand-mark">M</span>MachLane</div>
<div class="run-state">Mission feed simulator</div></div>
<div class="notice"><b>Research prototype — not FAA approved.</b> No surface-overpressure or sonic-boom compliance result is calculated.</div>
""",
    unsafe_allow_html=True,
)

missions = list_missions()
mission_id = st.selectbox(
    "Future high-speed mission",
    options=[mission.mission_id for mission in missions],
    format_func=lambda value: get_mission(value).label,
    help="Airport pairs searched in OpenSky. The planner does not run unless an observed track is loaded.",
)
mission = get_mission(mission_id)

with st.container(border=True):
    source_control, source_date, source_action = st.columns(
        [2.1, 1.2, 1.25], vertical_alignment="bottom"
    )
    with source_control:
        st.markdown("**Observed route · OpenSky**")
        st.caption(
            f"Loads the most recent direct observed track in a {OPENSKY_LOOKBACK_DAYS}-day window. "
            "No invented route fallback."
        )
    yesterday = datetime.now(UTC).date() - timedelta(days=1)
    with source_date:
        observed_date = st.date_input(
            "Search ending (UTC)",
            value=yesterday,
            min_value=yesterday - timedelta(days=29),
            max_value=yesterday,
            key="opensky_observed_date",
        )
    credentials_configured = bool(
        os.getenv("OPENSKY_CLIENT_ID") and os.getenv("OPENSKY_CLIENT_SECRET")
    )
    with source_action:
        fetch_opensky = st.button(
            "Refresh observed route",
            disabled=not credentials_configured,
            width="stretch",
        )

    opensky_key = f"opensky-route:{mission_id}:{observed_date.isoformat()}"
    opensky_attempt_key = f"opensky-attempt:{mission_id}:{observed_date.isoformat()}"
    cached_route = OPENSKY_CACHE.load(
        mission_id,
        observed_date,
        origin_icao=mission.origin.icao,
        destination_icao=mission.destination.icao,
    )
    if opensky_key not in st.session_state and cached_route is not None:
        st.session_state[opensky_key] = cached_route.model_dump_json()
    automatic_fetch = (
        credentials_configured
        and opensky_key not in st.session_state
        and opensky_attempt_key not in st.session_state
    )
    if fetch_opensky or automatic_fetch:
        st.session_state[opensky_attempt_key] = True
        search_end = datetime.combine(observed_date, datetime.max.time(), tzinfo=UTC)
        try:
            with st.spinner(
                f"Searching the latest {OPENSKY_LOOKBACK_DAYS} days for an OpenSky track…"
            ):
                fetched_route = OpenSkyTrackProvider(
                    network_enabled=True
                ).recent_route_for_airports(
                    mission.origin,
                    mission.destination,
                    on_or_before=search_end,
                    lookback_days=OPENSKY_LOOKBACK_DAYS,
                )
            st.session_state[opensky_key] = fetched_route.model_dump_json()
            OPENSKY_CACHE.save(mission_id, observed_date, fetched_route)
        except (RuntimeError, ValueError, OSError) as exc:
            st.error(f"OpenSky import failed: {exc}")

    observed_route_json = st.session_state.get(opensky_key)
    if not credentials_configured:
        st.warning(
            "OpenSky credentials are missing. Set OPENSKY_CLIENT_ID and "
            "OPENSKY_CLIENT_SECRET in Terminal, then restart Streamlit."
        )
    elif observed_route_json is None:
        st.error(
            "No observed OpenSky route is loaded. Choose another airport pair/date or refresh; "
            "MachLane will not invent a replacement route."
        )
    st.caption(
        "Experimental, downsampled observed path — not a filed route. "
        "[OpenSky terms](https://opensky-network.org/about/terms-of-use)"
    )

if not isinstance(observed_route_json, str):
    st.info(
        "OpenSky-only workspace paused. Configure credentials and load an observed route to run "
        "mock atmospheric segmentation."
    )
    st.stop()

observed_route = Route.model_validate_json(observed_route_json)
if (
    observed_route.source is None
    or observed_route.source.provider != "opensky"
    or observed_route.source.data_kind != "observed_track"
):
    st.error("Route provenance is not a normalized OpenSky observation; planning is stopped.")
    st.stop()
noaa_plan = plan_noaa_atmosphere(observed_route, mission.domain)

with st.container(border=True):
    atmosphere_choice, tolerance_choice, timing = st.columns(
        [1.35, 1.1, 2.25], vertical_alignment="bottom"
    )
    with atmosphere_choice:
        atmosphere_mode = st.radio(
            "Atmosphere source",
            ["Mock", "NOAA archived"],
            horizontal=True,
            help="NOAA downloads an archived HRRR or GEFS pressure-level snapshot valid during the observed flight.",
        )
    with tolerance_choice:
        weather_preset = st.selectbox(
            "Macro-area tolerance",
            options=list(WEATHER_PRESETS),
            index=1,
            help="Tighter tolerances make more atmosphere regimes. These are research grouping controls, not boom-certification thresholds.",
        )
    with timing:
        st.markdown(f"**{noaa_plan.model} · route-aligned atmosphere**")
        st.caption(
            f"Observed flight midpoint → {noaa_plan.valid_time:%Y-%m-%d %H:%M UTC} · "
            f"cycle {noaa_plan.model_cycle:%H:%M UTC} · lead +{noaa_plan.forecast_hour} h · "
            f"{WEATHER_PRESETS[weather_preset][2]}"
        )

mock_mode = atmosphere_mode == "Mock"
if mock_mode:
    st.markdown(
        '<div class="mode-banner mock"><b>MOCK</b><span>Synthetic atmosphere and phase-aware aircraft fixture. No NOAA network request is made.</span></div>',
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        f'<div class="mode-banner baseline"><b>NOAA</b><span>Archived {noaa_plan.model} model atmosphere valid during this OpenSky flight. Real model input; still not a sonic-boom result.</span></div>',
        unsafe_allow_html=True,
    )

try:
    spinner_message = (
        f"Fetching and sampling archived {noaa_plan.model} data for the observed route…"
        if not mock_mode
        else "Building mock atmospheric macro areas…"
    )
    with st.spinner(spinner_message):
        demo = scenario(
            mission_id,
            SCENARIO_CACHE_SCHEMA,
            observed_route_json,
            atmosphere_mode,
            weather_preset,
        )
except (RuntimeError, ValueError, OSError) as exc:
    st.error(f"Atmosphere load failed: {exc}")
    st.info(
        "Select Mock to continue without a NOAA network request. MachLane does not silently substitute synthetic data."
    )
    st.stop()

display_route = observed_route
rows = segment_rows(demo.route, demo.result)
regime_by_id = {regime.segment_id: regime for regime in demo.weather_regimes}
for row in rows:
    regime = regime_by_id[str(row["segment"])]
    row.update(
        {
            "boundary_reason": _imperial_boundary_reason(regime.boundary_reason),
            "weather_samples": regime.sample_count,
            "regime_temperature_f": (regime.temperature_k - 273.15) * 9 / 5 + 32,
            "regime_temperature_c": regime.temperature_k - 273.15,
            "regime_pressure_inhg": regime.pressure_hpa * 100 * PASCALS_TO_INHG,
            "regime_pressure_hpa": regime.pressure_hpa,
            "pressure_label": f"{regime.pressure_hpa * 100 * PASCALS_TO_INHG:.3f}",
            "regime_wind_kt": regime.wind_speed_mps * 1.943844492,
            "ray_status": "NOT MODELED",
        }
    )
pressure_values = [float(row["regime_pressure_hpa"]) for row in rows]
pressure_min_hpa = min(pressure_values)
pressure_max_hpa = max(pressure_values)
pressure_scale_min_hpa = pressure_min_hpa - 0.5
pressure_scale_max_hpa = pressure_max_hpa + 0.5
for row in rows:
    row["color"] = pressure_color(
        float(row["regime_pressure_hpa"]),
        pressure_scale_min_hpa,
        pressure_scale_max_hpa,
    )
distance_miles = route_distance_m(display_route) * METERS_TO_MILES
map_latitude, map_longitude = interpolate_position(display_route, 0.5)
map_zoom = max(1.2, min(4.0, 4.9 - math.log2(max(distance_miles, 300) / 300)))

observed_source = observed_route.source
callsign = observed_source.callsign if observed_source and observed_source.callsign else "unknown"
observed_day = (
    observed_source.observed_start.strftime("%Y-%m-%d")
    if observed_source and observed_source.observed_start
    else str(observed_date)
)
observed_window = "Time window unavailable"
if observed_source and observed_source.observed_start and observed_source.observed_end:
    observed_window = (
        f"{observed_source.observed_start.strftime('%Y-%m-%d %H:%M')}–"
        f"{observed_source.observed_end.strftime('%H:%M')} UTC"
    )
source_point_count = (
    observed_source.point_count
    if observed_source and observed_source.point_count is not None
    else len(observed_route.waypoints)
)
st.caption(
    f"Observed OpenSky trajectory · {callsign} · {observed_day} · "
    "experimental/downsampled, not a filed route or future supersonic approval."
)

summary_columns = st.columns(3)
summary_columns[0].metric(
    "Route",
    "OpenSky observed",
    f"{distance_miles:,.0f} mi · {len(display_route.waypoints)} observed points",
    delta_color="off",
)
summary_columns[1].metric(
    "Atmosphere",
    "MOCK fixture" if mock_mode else noaa_plan.model,
    f"{len(demo.route.segments)} macro areas · {noaa_plan.valid_time:%H:%M UTC}",
    delta_color="off",
)
summary_columns[2].metric(
    "Sonic boom",
    "Not calculated",
    "No footprint or ground overpressure",
    delta_color="off",
)

# The position slider lives inside the fragment below. Its value is persisted in session state so
# the position-dependent detail tabs further down still resolve the correct segment on a full
# rerun, while dragging the slider only reruns the fragment.
st.session_state.setdefault("aircraft_progress_pct", 24)
progress = st.session_state["aircraft_progress_pct"] / 100
active_index = active_segment_index(demo.route, progress)
active = rows[active_index]
active_limit = demo.result.segment_limits[active_index]
active_altitude_m = active_limit.selected_altitude_m or 0.0
active_atmosphere = demo.segment_atmospheres[active_index]
active_aircraft = aircraft_view(display_route, progress, map_longitude)
weather_at_altitude = atmosphere_metrics(
    active_atmosphere, active_altitude_m, active_aircraft["bearing_deg"]
)

# Route geometry and labels never move while the slider does, so build them once. Every weather
# path below comes from the grouped segment's retained OpenSky polyline, never its endpoint chord.
atmosphere_map_layers = [
    pdk.Layer(
        "PathLayer",
        rows,
        id="weather-regime-centerlines",
        get_path="path",
        get_color="color",
        get_width=5,
        width_units="pixels",
        width_min_pixels=4,
        width_max_pixels=6,
        pickable=True,
    ),
    pdk.Layer(
        "ScatterplotLayer",
        [
            {
                "position": row["path"][0],
                "segment": row["segment"],
                "boundary_reason": row["boundary_reason"],
                "pressure_label": row["pressure_label"],
            }
            for row in rows[1:]
        ],
        id="weather-regime-boundaries",
        get_position="position",
        get_radius=4,
        radius_units="pixels",
        radius_min_pixels=4,
        radius_max_pixels=4,
        get_fill_color=[240, 248, 255, 255],
        get_line_color=[7, 12, 19, 255],
        line_width_min_pixels=1,
        stroked=True,
        pickable=True,
    ),
]
observed_track_layers = [
    pdk.Layer(
        "PathLayer",
        [
            {
                "path": [
                    [display_longitude(longitude, map_longitude), latitude]
                    for latitude, longitude in observed_route.waypoints
                ]
            }
        ],
        id="opensky-observed-track",
        get_path="path",
        get_color=[255, 213, 0, 235],
        width_min_pixels=4,
    ),
]
label_map_layers = [
    pdk.Layer(
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
        get_color=[226, 236, 246, 230],
    ),
]


@fragment
def render_workspace() -> None:
    """Slider, map and live inspector, isolated so dragging the aircraft skips the heavy tabs.

    Only this fragment reruns while the slider moves, so the aircraft repositions without rebuilding
    the Plotly profiles, dataframes or evidence views. Route geometry is untouched: the marker is
    placed by the WGS-84 ``aircraft_view`` transformation, never by screen-space interpolation.
    """

    workspace, inspector = st.columns([3.15, 1], gap="medium")
    with workspace:
        show_phase_panel = st.toggle(
            "Flight phases",
            value=True,
            help="Jump the aircraft to a representative point in the synthetic phase schedule.",
        )
        if show_phase_panel:
            phase_columns = st.columns(len(PHASE_ANCHORS))
            for phase_column, (label, anchor) in zip(phase_columns, PHASE_ANCHORS, strict=True):
                with phase_column:
                    st.button(
                        label,
                        key=f"phase-anchor-{anchor}",
                        on_click=_set_aircraft_position,
                        args=(anchor,),
                        width="stretch",
                    )
        percent = st.slider(
            "Aircraft position",
            0,
            100,
            step=1,
            format="%d%%",
            key="aircraft_progress_pct",
        )
        drag_progress = percent / 100
        drag_index = active_segment_index(demo.route, drag_progress)
        drag_row = rows[drag_index]
        aircraft = aircraft_view(display_route, drag_progress, map_longitude)
        flight_state = mock_flight_state(
            drag_progress,
            route_distance_miles=distance_miles,
            cruise_mach=float(drag_row["mach"]),
            cruise_altitude_ft=float(drag_row["altitude_ft"]),
        )
        flight_phase = str(flight_state["phase"])
        flight_mach = float(flight_state["mach"])
        flight_altitude_ft = float(flight_state["altitude_ft"])
        flight_is_supersonic = bool(flight_state["supersonic"])
        drag_weather = atmosphere_metrics(
            demo.segment_atmospheres[drag_index],
            flight_altitude_ft / METERS_TO_FEET,
            aircraft["bearing_deg"],
        )
        if mock_mode:
            drag_weather = mock_live_metrics(drag_weather, mission_id, drag_progress)
        speed_of_sound_kt = (
            math.sqrt(1.4 * 287.05287 * float(drag_weather["temperature_k"])) * 1.943844492
        )
        estimated_tas_kt = flight_mach * speed_of_sound_kt
        flight_time = None
        if observed_source and observed_source.observed_start and observed_source.observed_end:
            flight_time = (
                observed_source.observed_start
                + (observed_source.observed_end - observed_source.observed_start) * drag_progress
            )

        st.markdown(
            '<div class="section-kicker">Aircraft atmosphere · follows phase and position</div>',
            unsafe_allow_html=True,
        )
        live_weather_columns = st.columns(4)
        live_weather_columns[0].metric(
            "Ambient pressure", f"{drag_weather['pressure_inhg']:.3f} inHg"
        )
        live_weather_columns[1].metric("Temperature", f"{drag_weather['temperature_f']:.1f} °F")
        live_weather_columns[2].metric("Wind speed", f"{drag_weather['wind_speed_kt']:.0f} kt")
        live_weather_columns[3].metric(
            "Along-track wind", f"{drag_weather['along_wind_kt']:+.0f} kt"
        )

        title_left, title_right = st.columns([3, 1])
        with title_left:
            st.subheader("Atmospheric corridor")
            st.caption(
                "Blue = lower ambient pressure · red = higher ambient pressure · centered on OpenSky"
            )
        with title_right:
            st.markdown(
                '<div style="text-align:right"><span class="eligible">AMBIENT PRESSURE</span></div>',
                unsafe_allow_html=True,
            )
        st.markdown(
            f'<div class="mission-strip"><span>Route <b>{mission.origin.iata} → {mission.destination.iata} · {distance_miles:,.0f} mi</b></span><span>Geometry <b>OpenSky observed</b></span><span>Atmosphere <b>{"MOCK" if mock_mode else noaa_plan.model} · {noaa_plan.valid_time:%H:%M UTC}</b></span><span>Boom <b>not modeled</b></span></div>',
            unsafe_allow_html=True,
        )
        route_toggle, segment_toggle, toggle_note = st.columns([1, 1.15, 2.5])
        with route_toggle:
            show_observed_track = st.toggle("OpenSky track", value=True, key="show_observed_track")
        with segment_toggle:
            show_atmosphere_segments = st.toggle(
                "Atmosphere segments", value=True, key="show_atmosphere_segments"
            )
        with toggle_note:
            st.caption(
                "Colored regimes share the exact OpenSky centerline; no filled corridor is drawn."
            )

        aircraft_layers = [
            pdk.Layer(
                "ScatterplotLayer",
                [{"position": [aircraft["display_longitude"], aircraft["latitude"]]}],
                id="aircraft-position-dot",
                get_position="position",
                get_radius=9,
                radius_units="pixels",
                radius_min_pixels=9,
                radius_max_pixels=9,
                get_fill_color=[255, 213, 0, 255],
                get_line_color=[7, 12, 19, 255],
                line_width_min_pixels=2,
                stroked=True,
            ),
        ]
        visible_map_layers: list[pdk.Layer] = []
        if show_atmosphere_segments:
            visible_map_layers.extend(atmosphere_map_layers)
        if show_observed_track:
            visible_map_layers.extend(observed_track_layers)
        visible_map_layers.extend(label_map_layers)
        st.pydeck_chart(
            pdk.Deck(
                layers=[*visible_map_layers, *aircraft_layers],
                map_style=pdk.map_styles.CARTO_DARK,
                initial_view_state=pdk.ViewState(
                    latitude=map_latitude,
                    longitude=map_longitude,
                    zoom=map_zoom,
                    pitch=8,
                    bearing=0,
                ),
                tooltip={
                    "html": "<b>{segment}</b><br/>Ambient pressure {pressure_label} inHg<br/>{boundary_reason}",
                    "style": {"backgroundColor": "#0b1420", "color": "#e5eef8"},
                },
            ),
            height=490,
        )
        legend_left, legend_right = st.columns([2, 1])
        with legend_left:
            st.caption(
                f"Blue {pressure_scale_min_hpa * 100 * PASCALS_TO_INHG:.3f} inHg → red {pressure_scale_max_hpa * 100 * PASCALS_TO_INHG:.3f} inHg · exact OpenSky polyline, not boom overpressure"
            )
        with legend_right:
            st.caption(f"{drag_progress:.0%} complete · {drag_row['segment']}")

    with inspector:
        st.markdown('<div class="section-kicker">Live inspector</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="status-card"><div class="label">Flight phase fixture</div><div class="value">{flight_phase}</div><div class="meta">Mach {flight_mach:.2f} · estimated TAS {estimated_tas_kt:,.0f} kt · {flight_altitude_ft:,.0f} ft</div></div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="status-card"><div class="label">Ambient profile</div><div class="value">{drag_weather["pressure_inhg"]:.3f} inHg</div><div class="meta">{drag_weather["temperature_f"]:.1f} °F · wind {drag_weather["wind_speed_kt"]:.0f} kt · {"MOCK" if mock_mode else noaa_plan.model}</div></div>',
            unsafe_allow_html=True,
        )
        if flight_time is not None:
            st.markdown(
                f'<div class="status-card"><div class="label">Observed-track time</div><div class="value">{flight_time:%H:%M UTC}</div><div class="meta">{flight_time:%A · %B %d, %Y}</div></div>',
                unsafe_allow_html=True,
            )
        st.markdown(
            f'<div class="status-card"><div class="label">Mock MCO corridor</div><div class="value">{"ACTIVE" if flight_is_supersonic else "INACTIVE"}</div><div class="meta">{"Synthetic limit Mach " + format(drag_row["mach"], ".2f") if flight_is_supersonic else "No Mach-cutoff decision below Mach 1"}</div></div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="status-card"><div class="label">Surface boom</div><div class="value"><span class="pending">NOT MODELED</span></div><div class="meta">No 0.11 psf determination</div></div>',
            unsafe_allow_html=True,
        )
        st.caption(
            f"{drag_progress:.0%} · {aircraft['latitude']:.2f}°, {aircraft['longitude']:.2f}° · track {aircraft['bearing_deg']:.0f}°"
        )


render_workspace()

plan_tab, atmosphere_tab, api_tab, model_tab, evidence_tab = st.tabs(
    ["Segments", "Atmosphere", "Data & APIs", "How it works", "Evidence"]
)

with plan_tab:
    table = pd.DataFrame(rows)
    st.dataframe(
        table,
        hide_index=True,
        width="stretch",
        column_order=[
            "segment",
            "boundary_reason",
            "start_mi",
            "end_mi",
            "regime_pressure_inhg",
            "regime_temperature_f",
            "regime_wind_kt",
            "altitude_ft",
            "mach",
            "ray_status",
        ],
        column_config={
            "segment": "Segment",
            "boundary_reason": "Boundary trigger",
            "start_mi": st.column_config.NumberColumn("From mi", format="%.1f"),
            "end_mi": st.column_config.NumberColumn("To mi", format="%.1f"),
            "regime_pressure_inhg": st.column_config.NumberColumn("Pressure", format="%.3f inHg"),
            "regime_temperature_f": st.column_config.NumberColumn("Temperature", format="%.1f °F"),
            "regime_wind_kt": st.column_config.NumberColumn("Wind", format="%.1f kt"),
            "altitude_ft": st.column_config.NumberColumn("Altitude", format="%d ft"),
            "mach": st.column_config.NumberColumn("Mach", format="%.2f"),
            "ray_status": "Ray propagation",
        },
    )
    segment_export_columns = [
        "segment",
        "boundary_reason",
        "start_mi",
        "end_mi",
        "weather_samples",
        "regime_pressure_inhg",
        "regime_temperature_f",
        "regime_wind_kt",
        "altitude_ft",
        "mach",
        "ray_status",
    ]
    st.download_button(
        "Export segments · CSV",
        table[segment_export_columns].to_csv(index=False),
        file_name=f"machlane_{mission_id}_{observed_day}_segments_imperial.csv",
        mime="text/csv",
    )
    st.caption(
        f"{weather_preset} macro-area preset at 50,000 ft · {WEATHER_PRESETS[weather_preset][2]}. Boundaries occur when temperature, ambient pressure, or the wind vector leaves tolerance; lengths are variable and retain the exact OpenSky centerline."
    )

with atmosphere_tab:
    st.caption(
        "The high-contrast flight-level values above the map follow the aircraft live. These charts "
        "show the full vertical profile for the selected mission snapshot."
    )
    chart_left, chart_right = st.columns(2)
    altitude_ft = [altitude * METERS_TO_FEET for altitude in active_atmosphere.altitude_m]
    pressure_inhg = [pressure * PASCALS_TO_INHG for pressure in active_atmosphere.pressure_pa]
    temperature_f = [
        (temperature - 273.15) * 9 / 5 + 32 for temperature in active_atmosphere.temperature_k
    ]
    wind_speed_kt = [
        math.hypot(u, v) * 1.943844492
        for u, v in zip(
            active_atmosphere.zonal_wind_mps,
            active_atmosphere.meridional_wind_mps,
            strict=True,
        )
    ]
    with chart_left:
        pressure_figure = go.Figure()
        pressure_figure.add_trace(
            go.Scatter(
                x=pressure_inhg,
                y=altitude_ft,
                name="Ambient pressure",
                mode="lines+markers",
                line={"color": "#60a5fa", "width": 3},
                fill="tozerox",
                fillcolor="rgba(96,165,250,.10)",
            )
        )
        pressure_figure.add_trace(
            go.Scatter(
                x=[weather_at_altitude["pressure_inhg"]],
                y=[active_altitude_m * METERS_TO_FEET],
                name="Selected altitude",
                mode="markers",
                marker={"color": "#fbbf24", "size": 11},
            )
        )
        pressure_figure.update_layout(
            title=f"Pressure profile · {active['segment']} · {'MOCK' if mock_mode else noaa_plan.model}",
            xaxis_title="Pressure (inHg)",
            yaxis_title="Altitude (ft)",
            template="plotly_dark",
            height=380,
            margin={"l": 20, "r": 15, "t": 50, "b": 20},
            paper_bgcolor="#0a111b",
            plot_bgcolor="#0d1722",
        )
        st.plotly_chart(pressure_figure, width="stretch")
    with chart_right:
        wind_figure = go.Figure(
            go.Scatter(
                x=wind_speed_kt,
                y=altitude_ft,
                mode="lines+markers",
                line={"color": "#2dd4bf", "width": 3},
                marker={"size": 5},
                fill="tozerox",
                fillcolor="rgba(45,212,191,.10)",
            )
        )
        wind_figure.update_layout(
            title=f"Wind profile · {active['segment']} · synthetic",
            xaxis_title="Wind speed (kt)",
            yaxis_title="Altitude (ft)",
            template="plotly_dark",
            height=380,
            margin={"l": 20, "r": 15, "t": 50, "b": 20},
            paper_bgcolor="#0a111b",
            plot_bgcolor="#0d1722",
        )
        st.plotly_chart(wind_figure, width="stretch")
    st.warning(
        "Ambient pressure describes the surrounding atmosphere. Sonic-boom overpressure is a separate pressure disturbance and is not calculated here."
    )
    st.markdown("#### Profile values")
    profile_table = pd.DataFrame(
        {
            "Altitude (ft)": [round(value) for value in altitude_ft],
            "Pressure (inHg)": [round(value, 3) for value in pressure_inhg],
            "Temperature (°F)": [round(value, 1) for value in temperature_f],
            "Wind (kt)": [round(value, 1) for value in wind_speed_kt],
        }
    )
    st.dataframe(
        profile_table,
        hide_index=True,
        width="stretch",
    )
    st.download_button(
        "Export active profile · CSV",
        profile_table.to_csv(index=False),
        file_name=f"machlane_{mission_id}_{active['segment']}_profile_imperial.csv",
        mime="text/csv",
    )

with api_tab:
    st.markdown("#### Inputs, cache, and exports")
    st.caption(
        "OpenSky supplies the observed aircraft track. NOAA supplies the route-time atmosphere. "
        "Neither source supplies a sonic-boom result. Downloads below contain the normalized data currently loaded in this run."
    )
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Input": "Route geometry",
                    "API": "OpenSky Network REST API",
                    "Time": observed_window,
                    "Use": "Observed centerline and timestamps",
                },
                {
                    "Input": "Atmosphere",
                    "API": "Local mock" if mock_mode else f"NOAA {noaa_plan.model} via Herbie",
                    "Time": noaa_plan.valid_time.strftime("%Y-%m-%d %H:%M UTC"),
                    "Use": "Pressure, temperature, humidity, and wind profiles",
                },
                {
                    "Input": "Terrain",
                    "API": "Flat fixture",
                    "Time": "Not time-varying",
                    "Use": "Placeholder only; 3DEP not loaded in this run",
                },
                {
                    "Input": "Boom propagation",
                    "API": "Not connected",
                    "Time": "—",
                    "Use": "No footprint or ground overpressure",
                },
            ]
        ),
        hide_index=True,
        width="stretch",
    )
    route_export = pd.DataFrame(
        {
            "timestamp_utc": [
                observation.timestamp.isoformat() for observation in observed_route.observations
            ],
            "latitude_deg": [observation.latitude for observation in observed_route.observations],
            "longitude_deg": [observation.longitude for observation in observed_route.observations],
            "altitude_ft": [
                None
                if observation.barometric_altitude_m is None
                else round(observation.barometric_altitude_m * METERS_TO_FEET)
                for observation in observed_route.observations
            ],
            "track_deg": [
                observation.true_track_deg for observation in observed_route.observations
            ],
            "on_ground": [observation.on_ground for observation in observed_route.observations],
        }
    )
    export_left, export_right = st.columns(2)
    with export_left:
        st.download_button(
            "Export OpenSky observations · CSV",
            route_export.to_csv(index=False),
            file_name=f"machlane_{mission_id}_{observed_day}_opensky.csv",
            mime="text/csv",
            width="stretch",
        )
    with export_right:
        st.download_button(
            "Export normalized route · JSON",
            observed_route.model_dump_json(indent=2),
            file_name=f"machlane_{mission_id}_{observed_day}_route.json",
            mime="application/json",
            width="stretch",
        )
    st.caption(
        f"NOAA cache · data/cache/herbie · {noaa_plan.label}. First real-model load may download a sizeable pressure-level subset; reruns reuse the cache."
    )

with model_tab:
    st.markdown("#### What MachLane calculates today")
    st.markdown(
        """
<div class="calc-grid">
  <div class="calc-step"><span class="number">01</span><b>Load observed track</b><span>OpenSky departure and track responses are normalized with timestamps, coordinates, checksum, and limitations. No route fallback.</span></div>
  <div class="calc-step"><span class="number">02</span><b>Sample atmosphere</b><span>Pressure, temperature, and wind are sampled along the retained OpenSky polyline.</span></div>
  <div class="calc-step"><span class="number">03</span><b>Form weather regimes</b><span>Adjacent samples remain together until a characteristic crosses its threshold; every bend stays attached to the observed track.</span></div>
  <div class="calc-step"><span class="number">04</span><b>Plan each segment</b><span>Mach and altitude candidates are evaluated separately inside every weather regime.</span></div>
  <div class="calc-step"><span class="number">05</span><b>Propagate rays</b><span>Not implemented: effective sound speed, ray paths, footprint, and ground overpressure.</span></div>
</div>
""",
        unsafe_allow_html=True,
    )
    st.caption(
        f"Route input · OpenSky observed track · {callsign} · {observed_window} · "
        f"{source_point_count:,} source points. OpenSky supplies route geometry, not weather."
    )
    equation_left, equation_right = st.columns([1.35, 1])
    with equation_left:
        st.markdown("##### Exact mock equation currently running")
        st.code(
            "synthetic_limit = 1.08\n"
            "  + clamp(altitude_m - 10,000, 0, 8,000) / 200,000\n"
            "  + along_track_wind_mps / 2,000\n"
            "  - terrain_peak_m / 200,000\n\n"
            "candidate accepted when Mach <= synthetic_limit",
            language="text",
        )
        st.caption(
            "This equation is intentionally arbitrary. It tests data flow and UI behavior; it is not sonic-boom physics."
        )
    with equation_right:
        st.markdown("##### Active-segment variables")
        st.caption("Reflect the segment at the last full reload, not mid-drag.")
        st.dataframe(
            pd.DataFrame(
                [
                    {"Variable": "Route source", "Value": f"OpenSky · {callsign}"},
                    {
                        "Variable": "Observed track",
                        "Value": f"{source_point_count:,} points · {observed_window}",
                    },
                    {"Variable": "Candidate Mach", "Value": f"{active['mach']:.2f}"},
                    {"Variable": "Altitude", "Value": f"{active['altitude_ft']:,} ft"},
                    {"Variable": "Along-track wind", "Value": f"{active['along_wind_kt']:+.1f} kt"},
                    {"Variable": "Synthetic limit", "Value": f"{active['synthetic_score']:.3f}"},
                    {"Variable": "Surface overpressure", "Value": "NOT MODELED"},
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    st.info(
        "A real boom calculation still needs a reviewed aircraft near-field pressure signature, atmospheric propagation, nonlinear distortion and absorption, terrain interaction, ground metrics, and validation against a trusted reference such as PCBoom."
    )

with evidence_tab:
    st.markdown("#### Observed route evidence")
    route_checksum = (
        observed_source.checksum if observed_source and observed_source.checksum else "Unavailable"
    )
    st.dataframe(
        pd.DataFrame(
            [
                {"Field": "Provider", "Value": "OpenSky Network REST API"},
                {
                    "Field": "Airport pair",
                    "Value": f"{mission.origin.icao} → {mission.destination.icao}",
                },
                {"Field": "Callsign", "Value": callsign},
                {
                    "Field": "Flight ID",
                    "Value": observed_source.flight_id or "Unavailable",
                },
                {"Field": "Observed UTC", "Value": observed_window},
                {"Field": "Source observations", "Value": f"{source_point_count:,}"},
                {
                    "Field": "Normalized route points",
                    "Value": f"{len(observed_route.waypoints):,}",
                },
                {"Field": "Payload checksum", "Value": route_checksum},
                {
                    "Field": "Geometry policy",
                    "Value": "Full observed polyline retained; regimes drawn on centerline",
                },
            ]
        ),
        hide_index=True,
        width="stretch",
    )
    if observed_source.source_url:
        st.link_button("Open OpenSky source request", observed_source.source_url)
    with st.expander("OpenSky limitations"):
        for limitation in observed_source.limitations:
            st.markdown(f"- {limitation}")

    st.markdown("#### Data readiness")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Source": "OpenSky",
                    "State": "OBSERVED · LOADED",
                    "Use": f"Route geometry · {callsign} · {source_point_count:,} points",
                },
                {
                    "Source": "NOAA HRRR",
                    "State": (
                        "ARCHIVED MODEL · LOADED"
                        if not mock_mode and noaa_plan.model == "HRRR"
                        else mission.hrrr_coverage
                    ),
                    "Use": "Route-time regional atmosphere",
                },
                {
                    "Source": "NOAA GEFS",
                    "State": (
                        "ARCHIVED CONTROL · LOADED"
                        if not mock_mode and noaa_plan.model == "GEFS"
                        else "GLOBAL · FETCH READY"
                    ),
                    "Use": "Route-time global atmosphere",
                },
                {
                    "Source": "USGS 3DEP",
                    "State": "U.S. LAND ONLY",
                    "Use": "Terrain where covered",
                },
                {
                    "Source": "ERA5",
                    "State": "GLOBAL · CREDENTIAL GATED",
                    "Use": "Historical back-testing",
                },
            ]
        ),
        hide_index=True,
        width="stretch",
    )
    st.caption(
        "Fetch-ready means the adapter can retrieve and normalize data. It does not mean the "
        "source has been integrated into a validated planning or compliance workflow."
    )
    evidence_left, evidence_right = st.columns([1.6, 1])
    statuses = pd.DataFrame(
        [
            {"Outcome": key.replace("_", " ").title(), "Status": str(value)}
            for key, value in compliance_matrix().items()
        ]
    )
    with evidence_left:
        st.dataframe(statuses, hide_index=True, width="stretch")
    with evidence_right:
        st.markdown("#### Traceability")
        st.caption(f"Route · OpenSky · {callsign} · {observed_window}")
        st.caption(f"Engine · {demo.result.engine_name} {demo.result.engine_version}")
        st.caption(f"Atmosphere · {demo.atmosphere.source.provider}")
        st.caption(f"Terrain · {demo.terrain.source.provider}")
        st.caption(f"Scenario target · {demo.result.reliability_level:.0%} · unvalidated")
        with st.expander("Raw provenance"):
            st.json(
                {
                    "aircraft": demo.aircraft.name.original_value,
                    "route": {
                        "mission_id": mission.mission_id,
                        "geometry": "OpenSky observed track resampled on WGS-84",
                        "distance_miles": route_distance_m(demo.route) * METERS_TO_MILES,
                        "airport_source": AIRPORT_SOURCE_URL,
                        "airport_source_retrieved": AIRPORT_SOURCE_RETRIEVED,
                        "operational_status": "OBSERVED_NOT_FILED_OR_CLEARED",
                        "source": observed_source.model_dump(mode="json"),
                    },
                    "atmosphere": demo.atmosphere.source.model_dump(mode="json"),
                    "terrain": demo.terrain.source.model_dump(mode="json"),
                    "run_label": demo.result.label,
                }
            )
        if st.button("Create evidence package", width="stretch"):
            output = write_evidence_package(
                aircraft=demo.aircraft,
                route=demo.route,
                result=demo.result,
                atmosphere_source=demo.atmosphere.source,
                terrain_source=demo.terrain.source,
                configuration_path=PROJECT_ROOT / "configs/baseline.yml",
                results_root=PROJECT_ROOT / "results",
            )
            st.success(f"Evidence package created: {output}")
