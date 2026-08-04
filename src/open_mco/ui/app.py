"""Map-first mission workspace for MachLane's network-free synthetic scenario."""

from __future__ import annotations

import math
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar

import pandas as pd
import plotly.graph_objects as go
import pydeck as pdk
import streamlit as st

from open_mco.compliance import compliance_matrix
from open_mco.demo import build_demo_scenario, run_demo
from open_mco.models import Route
from open_mco.route import (
    AIRPORT_SOURCE_RETRIEVED,
    AIRPORT_SOURCE_URL,
    OpenSkyTrackProvider,
    get_mission,
    interpolate_position,
    list_missions,
    route_distance_m,
)
from open_mco.ui.view_model import (
    METERS_TO_FEET,
    active_segment_index,
    aircraft_view,
    atmosphere_metrics,
    corridor_rows,
    display_longitude,
    mock_flight_state,
    mock_live_metrics,
    pressure_color,
    segment_rows,
)

PLANE_ICON_MAPPING = {
    "plane": {
        "x": 0,
        "y": 0,
        "width": 64,
        "height": 64,
        "anchorX": 32,
        "anchorY": 32,
        "mask": False,
    }
}
PLANE_ICON_ATLAS = str(Path(__file__).with_name("static") / "plane.png")

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
.calc-grid { display:grid; grid-template-columns:repeat(4,1fr); gap:.55rem; margin:.4rem 0 .8rem; }
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


SCENARIO_CACHE_SCHEMA = "weather-regimes-v3-pressure-corridor"


@st.cache_resource(show_spinner=False)
def scenario(mission_id: str, cache_schema: str, route_json: str | None = None):
    """Cache by mission and result schema so old objects cannot survive model changes."""

    del cache_schema
    route_override = None if route_json is None else Route.model_validate_json(route_json)
    return build_demo_scenario(mission_id, route_override=route_override)

st.markdown(
    """
<div class="brand-row"><div class="brand"><span class="brand-mark">M</span>MachLane</div>
<div class="run-state">Mission feed simulator</div></div>
<div class="notice"><b>Research prototype — not FAA approved.</b> No surface-overpressure or sonic-boom compliance result is calculated.</div>
""",
    unsafe_allow_html=True,
)

mode_banner, mode_control = st.columns([4.5, 1], vertical_alignment="center")
with mode_control:
    mock_mode = st.toggle(
        "MOCK mode",
        value=True,
        key="mock_feed_enabled",
        help="Simulates changing HRRR, GEFS, terrain, and aircraft channels locally. It never fetches or presents real observations.",
    )
with mode_banner:
    if mock_mode:
        st.markdown(
            '<div class="mode-banner mock"><b>MOCK</b><span>Simulated HRRR · GEFS · 3DEP · aircraft telemetry. Randomized, local, and never operational data.</span></div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="mode-banner baseline"><b>BASELINE</b><span>Deterministic synthetic backend. Live NOAA and terrain feeds remain disconnected.</span></div>',
            unsafe_allow_html=True,
        )

missions = list_missions()
mission_id = st.selectbox(
    "Future high-speed mission",
    options=[mission.mission_id for mission in missions],
    format_func=lambda value: get_mission(value).label,
    help="Real airport reference points connected by the shortest WGS-84 geodesic. These are concept missions, not filed or cleared routes.",
)
mission = get_mission(mission_id)

with st.container(border=True):
    source_control, source_date, source_action = st.columns(
        [2.1, 1.2, 1.25], vertical_alignment="bottom"
    )
    with source_control:
        st.markdown("**Observed route · OpenSky**")
        st.caption(
            "Loads the latest matching departure once credentials are configured. "
            "Falls back safely to the concept corridor."
        )
    yesterday = datetime.now(UTC).date() - timedelta(days=1)
    with source_date:
        observed_date = st.date_input(
            "Departure date (UTC)",
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
    automatic_fetch = (
        credentials_configured
        and opensky_key not in st.session_state
        and opensky_attempt_key not in st.session_state
    )
    if fetch_opensky or automatic_fetch:
        st.session_state[opensky_attempt_key] = True
        begin = datetime.combine(observed_date, datetime.min.time(), tzinfo=UTC)
        end = begin + timedelta(days=1) - timedelta(seconds=1)
        try:
            with st.spinner("Finding a matching OpenSky flight and observed track…"):
                fetched_route = OpenSkyTrackProvider(network_enabled=True).route_for_airports(
                    mission.origin,
                    mission.destination,
                    begin=begin,
                    end=end,
                )
            st.session_state[opensky_key] = fetched_route.model_dump_json()
        except (RuntimeError, ValueError, OSError) as exc:
            st.error(f"OpenSky import failed: {exc}")

    observed_route_json = st.session_state.get(opensky_key)
    if not credentials_configured:
        st.warning(
            "OpenSky credentials are missing. Set OPENSKY_CLIENT_ID and "
            "OPENSKY_CLIENT_SECRET in Terminal, then restart Streamlit."
        )
    elif observed_route_json is None:
        st.caption(
            "No observed track was available. The conceptual corridor remains active."
        )
    st.caption(
        "Experimental, downsampled observed path — not a filed route. "
        "[OpenSky terms](https://opensky-network.org/about/terms-of-use)"
    )

demo = scenario(mission_id, SCENARIO_CACHE_SCHEMA, observed_route_json)
observed_route = (
    None if observed_route_json is None else Route.model_validate_json(observed_route_json)
)
display_route = observed_route or demo.route
rows = segment_rows(demo.route, demo.result)
regime_by_id = {regime.segment_id: regime for regime in demo.weather_regimes}
for row in rows:
    regime = regime_by_id[str(row["segment"])]
    row.update(
        {
            "boundary_reason": regime.boundary_reason,
            "weather_samples": regime.sample_count,
            "regime_temperature_c": regime.temperature_k - 273.15,
            "regime_pressure_hpa": regime.pressure_hpa,
            "pressure_label": f"{regime.pressure_hpa:.1f}",
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
corridors = corridor_rows(demo.route, demo.result)
for corridor, row in zip(corridors, rows, strict=True):
    corridor["color"] = [*row["color"][:3], 48]
distance_km = route_distance_m(display_route) / 1000
distance_nmi = distance_km / 1.852
map_latitude, map_longitude = interpolate_position(display_route, 0.5)
map_zoom = max(1.2, min(4.0, 4.9 - math.log2(max(distance_km, 500) / 500)))

if observed_route is None:
    st.caption(
        f"{mission.rationale} Real endpoints from OurAirports; path is conceptual, not an ATC clearance."
    )
else:
    observed_source = observed_route.source
    callsign = observed_source.callsign if observed_source and observed_source.callsign else "unknown"
    observed_day = (
        observed_source.observed_start.strftime("%Y-%m-%d")
        if observed_source and observed_source.observed_start
        else str(observed_date)
    )
    st.caption(
        f"Observed OpenSky trajectory · {callsign} · {observed_day} · "
        "experimental/downsampled, not a filed route or future supersonic approval."
    )

summary_columns = st.columns(3)
summary_columns[0].metric(
    "Route",
    "OpenSky observed" if observed_route is not None else "Concept fallback",
    (
        f"{distance_nmi:,.0f} nmi · {len(display_route.waypoints)} observed points"
        if observed_route is not None
        else f"{distance_nmi:,.0f} nmi · waiting for OpenSky"
    ),
    delta_color="off",
)
summary_columns[1].metric(
    "Atmosphere",
    "MOCK feed" if mock_mode else "Synthetic",
    f"{len(demo.route.segments)} regimes · 1 hPa pressure tolerance",
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
progress = st.session_state.get("aircraft_progress_pct", 24) / 100
active_index = active_segment_index(demo.route, progress)
active = rows[active_index]
active_limit = demo.result.segment_limits[active_index]
active_altitude_m = active_limit.selected_altitude_m or 0.0
active_atmosphere = demo.segment_atmospheres[active_index]
weather_at_altitude = atmosphere_metrics(
    active_atmosphere, active_altitude_m, float(active["bearing_deg"])
)

# Route, corridors, waypoints and labels never move while the slider does, so build them once and
# let the fragment append only the two moving aircraft layers each time it reruns.
static_map_layers = [
    pdk.Layer(
        "PolygonLayer",
        corridors,
        get_polygon="polygon",
        get_fill_color="color",
        get_line_color=[45, 212, 191, 120],
        line_width_min_pixels=1,
        pickable=True,
    ),
    pdk.Layer(
        "PathLayer",
        rows,
        get_path="path",
        get_color="color",
        width_min_pixels=3,
        pickable=True,
    ),
    pdk.Layer(
        "ScatterplotLayer",
        [
            {"position": [display_longitude(lon, map_longitude), lat]}
            for lat, lon in demo.route.waypoints
        ],
        get_position="position",
        get_radius=2700,
        get_fill_color=[226, 236, 246, 220],
        get_line_color=[6, 12, 20, 255],
        line_width_min_pixels=2,
        stroked=True,
    ),
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
if observed_route is not None:
    static_map_layers.insert(
        2,
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
    )


@fragment
def render_workspace() -> None:
    """Slider, map and live inspector, isolated so dragging the aircraft skips the heavy tabs.

    Only this fragment reruns while the slider moves, so the aircraft repositions without rebuilding
    the Plotly profiles, dataframes or evidence views. Route geometry is untouched: the marker is
    placed by the WGS-84 ``aircraft_view`` transformation, never by screen-space interpolation.
    """

    workspace, inspector = st.columns([3.15, 1], gap="medium")
    with workspace:
        percent = st.slider(
            "Aircraft position", 0, 100, 24, 1, format="%d%%", key="aircraft_progress_pct"
        )
        drag_progress = percent / 100
        drag_index = active_segment_index(demo.route, drag_progress)
        drag_row = rows[drag_index]
        aircraft = aircraft_view(display_route, drag_progress, map_longitude)
        flight_state = mock_flight_state(
            drag_progress,
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
            float(drag_row["bearing_deg"]),
        )
        if mock_mode:
            drag_weather = mock_live_metrics(drag_weather, mission_id, drag_progress)

        st.markdown(
            '<div class="section-kicker">Aircraft atmosphere · follows phase and position</div>',
            unsafe_allow_html=True,
        )
        live_weather_columns = st.columns(4)
        live_weather_columns[0].metric(
            "Ambient pressure", f'{drag_weather["pressure_hpa"]:.0f} hPa'
        )
        live_weather_columns[1].metric(
            "Temperature", f'{drag_weather["temperature_c"]:.1f} °C'
        )
        live_weather_columns[2].metric(
            "Wind speed", f'{drag_weather["wind_speed_kt"]:.0f} kt'
        )
        live_weather_columns[3].metric(
            "Along-track wind", f'{drag_weather["along_wind_kt"]:+.0f} kt'
        )

        title_left, title_right = st.columns([3, 1])
        with title_left:
            st.subheader("Atmospheric corridor")
            st.caption("Blue = lower ambient pressure · red = higher ambient pressure")
        with title_right:
            st.markdown(
                '<div style="text-align:right"><span class="eligible">AMBIENT PRESSURE</span></div>',
                unsafe_allow_html=True,
            )
        st.markdown(
            f'<div class="mission-strip"><span>Route <b>{mission.origin.iata} → {mission.destination.iata} · {distance_nmi:,.0f} nmi</b></span><span>Geometry <b>{"OpenSky observed" if observed_route is not None else "Concept fallback"}</b></span><span>Pressure <b>{pressure_min_hpa:.1f}–{pressure_max_hpa:.1f} hPa at cruise</b></span><span>Boom <b>not modeled</b></span></div>',
            unsafe_allow_html=True,
        )

        aircraft_layers = [
            pdk.Layer(
                "ScatterplotLayer",
                [{"position": [aircraft["display_longitude"], aircraft["latitude"]]}],
                id="aircraft-halo",
                get_position="position",
                get_radius=18,
                radius_units="pixels",
                radius_min_pixels=18,
                radius_max_pixels=18,
                get_fill_color=[10, 17, 27, 48],
                get_line_color=[255, 213, 0, 255],
                line_width_min_pixels=3,
                stroked=True,
            ),
            pdk.Layer(
                "IconLayer",
                [
                    {
                        "position": [
                            aircraft["display_longitude"],
                            aircraft["latitude"],
                        ],
                        "icon": "plane",
                    }
                ],
                id="aircraft-plane",
                get_position="position",
                icon_atlas=PLANE_ICON_ATLAS,
                icon_mapping=PLANE_ICON_MAPPING,
                get_icon="icon",
                get_size=48,
                get_color=[255, 255, 255, 255],
                size_scale=1,
                size_min_pixels=48,
                size_max_pixels=48,
                get_angle=aircraft["bearing_deg"],
            ),
        ]
        st.pydeck_chart(
            pdk.Deck(
                layers=[*static_map_layers, *aircraft_layers],
                map_style=pdk.map_styles.CARTO_DARK,
                initial_view_state=pdk.ViewState(
                    latitude=map_latitude,
                    longitude=map_longitude,
                    zoom=map_zoom,
                    pitch=8,
                    bearing=0,
                ),
                tooltip={
                    "html": "<b>{segment}</b><br/>Ambient pressure {pressure_label} hPa<br/>{boundary_reason}",
                    "style": {"backgroundColor": "#0b1420", "color": "#e5eef8"},
                },
            ),
            height=490,
        )
        legend_left, legend_right = st.columns([2, 1])
        with legend_left:
            st.caption(
                f"Blue {pressure_scale_min_hpa:.1f} hPa → red {pressure_scale_max_hpa:.1f} hPa · ambient pressure scale, not boom overpressure"
                if observed_route is not None
                else f"Blue {pressure_scale_min_hpa:.1f} hPa → red {pressure_scale_max_hpa:.1f} hPa · concept route until OpenSky loads"
            )
        with legend_right:
            st.caption(f"{drag_progress:.0%} complete · {drag_row['segment']}")

    with inspector:
        st.markdown('<div class="section-kicker">Live inspector</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="status-card"><div class="label">Mock flight phase</div><div class="value">{flight_phase}</div><div class="meta">Mach {flight_mach:.2f} · {flight_altitude_ft:,.0f} ft · phase-aware fixture</div></div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="status-card"><div class="label">Ambient profile</div><div class="value">{drag_weather["pressure_hpa"]:.0f} hPa</div><div class="meta">{drag_weather["temperature_c"]:.1f} °C · wind {drag_weather["wind_speed_kt"]:.0f} kt · {"MOCK feed" if mock_mode else "synthetic"}</div></div>',
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

plan_tab, atmosphere_tab, model_tab, evidence_tab = st.tabs(
    ["Weather segments", "Atmosphere", "How it works", "Evidence"]
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
            "start_nmi",
            "end_nmi",
            "regime_pressure_hpa",
            "regime_temperature_c",
            "regime_wind_kt",
            "altitude_ft",
            "mach",
            "ray_status",
        ],
        column_config={
            "segment": "Segment",
            "boundary_reason": "Boundary trigger",
            "start_nmi": st.column_config.NumberColumn("From nmi", format="%.1f"),
            "end_nmi": st.column_config.NumberColumn("To nmi", format="%.1f"),
            "regime_pressure_hpa": st.column_config.NumberColumn(
                "Pressure", format="%.1f hPa"
            ),
            "regime_temperature_c": st.column_config.NumberColumn(
                "Temperature", format="%.1f °C"
            ),
            "regime_wind_kt": st.column_config.NumberColumn("Wind", format="%.1f kt"),
            "altitude_ft": st.column_config.NumberColumn("Altitude", format="%d ft"),
            "mach": st.column_config.NumberColumn("Mach", format="%.2f"),
            "ray_status": "Ray propagation",
        },
    )
    st.caption(
        "Synthetic demo thresholds at 50,000 ft: Δtemperature > 0.7 K, Δpressure > 1.0 hPa, or Δwind vector > 2.5 m/s. The internal 100 nmi sampling interval controls detection resolution; segment lengths are variable outputs, not fixed spacing."
    )

with atmosphere_tab:
    st.caption(
        "The high-contrast flight-level values above the map follow the aircraft live. These charts "
        "show the full vertical profile for the selected mission snapshot."
    )
    chart_left, chart_right = st.columns(2)
    altitude_ft = [altitude * METERS_TO_FEET for altitude in active_atmosphere.altitude_m]
    pressure_hpa = [pressure / 100 for pressure in active_atmosphere.pressure_pa]
    temperature_c = [temperature - 273.15 for temperature in active_atmosphere.temperature_k]
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
                x=pressure_hpa,
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
                x=[weather_at_altitude["pressure_hpa"]],
                y=[active_altitude_m * METERS_TO_FEET],
                name="Selected altitude",
                mode="markers",
                marker={"color": "#fbbf24", "size": 11},
            )
        )
        pressure_figure.update_layout(
            title=f'Pressure profile · {active["segment"]} · synthetic',
            xaxis_title="Pressure (hPa)",
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
            title=f'Wind profile · {active["segment"]} · synthetic',
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
    st.dataframe(
        pd.DataFrame(
            {
                "Altitude (ft)": [round(value) for value in altitude_ft],
                "Pressure (hPa)": [round(value, 1) for value in pressure_hpa],
                "Temperature (°C)": [round(value, 1) for value in temperature_c],
                "Wind (kt)": [round(value, 1) for value in wind_speed_kt],
            }
        ),
        hide_index=True,
        width="stretch",
    )

with model_tab:
    st.markdown("#### What MachLane calculates today")
    st.markdown(
        """
<div class="calc-grid">
  <div class="calc-step"><span class="number">01</span><b>Sample atmosphere</b><span>Pressure, temperature, and wind are sampled along the WGS-84 flight path.</span></div>
  <div class="calc-step"><span class="number">02</span><b>Form weather regimes</b><span>Adjacent samples remain together until a characteristic crosses its threshold.</span></div>
  <div class="calc-step"><span class="number">03</span><b>Plan each segment</b><span>Mach and altitude candidates are evaluated separately inside every weather regime.</span></div>
  <div class="calc-step"><span class="number">04</span><b>Propagate rays</b><span>Not implemented: effective sound speed, ray paths, footprint, and ground overpressure.</span></div>
</div>
""",
        unsafe_allow_html=True,
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
                    {"Variable": "Candidate Mach", "Value": f'{active["mach"]:.2f}'},
                    {"Variable": "Altitude", "Value": f'{active["altitude_ft"]:,} ft'},
                    {"Variable": "Along-track wind", "Value": f'{active["along_wind_kt"]:+.1f} kt'},
                    {"Variable": "Synthetic limit", "Value": f'{active["synthetic_score"]:.3f}'},
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
    st.markdown("#### Data readiness")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Source": "NOAA HRRR",
                    "State": mission.hrrr_coverage,
                    "Use": "Regional nominal forecast",
                },
                {
                    "Source": "NOAA GEFS",
                    "State": "GLOBAL · FETCH READY",
                    "Use": "Forecast ensemble",
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
                        "geometry": "WGS-84 ellipsoidal geodesic",
                        "distance_m": route_distance_m(demo.route),
                        "airport_source": AIRPORT_SOURCE_URL,
                        "airport_source_retrieved": AIRPORT_SOURCE_RETRIEVED,
                        "operational_status": "CONCEPTUAL_NOT_FILED_OR_CLEARED",
                    },
                    "atmosphere": demo.atmosphere.source.model_dump(mode="json"),
                    "terrain": demo.terrain.source.model_dump(mode="json"),
                    "run_label": demo.result.label,
                }
            )
        if st.button("Create evidence package", width="stretch"):
            output = run_demo(mission_id=mission_id)
            st.success(f"Evidence package created: {output}")
