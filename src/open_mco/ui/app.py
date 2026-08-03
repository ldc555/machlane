"""Map-first mission workspace for MachLane's network-free synthetic scenario."""

from __future__ import annotations

import math

import pandas as pd
import plotly.graph_objects as go
import pydeck as pdk
import streamlit as st

from open_mco.compliance import compliance_matrix
from open_mco.demo import build_demo_scenario, run_demo
from open_mco.route import (
    AIRPORT_SOURCE_RETRIEVED,
    AIRPORT_SOURCE_URL,
    get_mission,
    interpolate_position,
    list_missions,
    route_distance_m,
)
from open_mco.ui.view_model import active_segment_index, corridor_rows, segment_rows

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
.run-state { color:#9fb2c7; font:600 .72rem/1 ui-monospace,SFMono-Regular,monospace; letter-spacing:.08em; text-transform:uppercase; }
.notice { border:1px solid #855b27; background:#291f13; color:#ffd898; padding:.55rem .8rem; border-radius:.6rem; font-size:.78rem; margin-bottom:.85rem; }
.workflow { display:grid; grid-template-columns:repeat(6,1fr); gap:.4rem; margin:0 0 .85rem; }
.workflow span { padding:.5rem .6rem; border:1px solid var(--line); border-radius:.5rem; color:var(--muted); font-size:.72rem; background:#0d1723; }
.workflow b { color:var(--teal); margin-right:.3rem; }
.section-kicker { color:#6f879e; font:700 .68rem/1.2 ui-monospace,SFMono-Regular,monospace; letter-spacing:.12em; text-transform:uppercase; margin:.3rem 0 .65rem; }
.panel-note { padding:.65rem .8rem; border:1px solid var(--line); background:#0c1520; border-radius:.6rem; color:#9fb2c7; font-size:.76rem; line-height:1.45; }
.mission-strip { display:flex; gap:.9rem; flex-wrap:wrap; padding:.58rem .75rem; margin:.35rem 0 .65rem; border:1px solid var(--line); background:#0c1520; border-radius:.55rem; color:#8ba0b7; font-size:.72rem; }
.mission-strip b { color:#dbe8f4; font-weight:650; }
.status-card { border:1px solid var(--line); background:linear-gradient(145deg,#111d2b,#0c1520); border-radius:.7rem; padding:.8rem .9rem; margin:.35rem 0 .65rem; }
.status-card .label { color:#70879e; font-size:.68rem; letter-spacing:.1em; text-transform:uppercase; }
.status-card .value { color:#e7f8f5; font-size:1.15rem; font-weight:750; margin:.15rem 0; }
.status-card .meta { color:#8ba0b7; font-size:.74rem; }
.eligible { display:inline-block; padding:.24rem .46rem; color:#8ff5e8; background:#123c3a; border:1px solid #1c716a; border-radius:99px; font:700 .66rem/1 ui-monospace,SFMono-Regular,monospace; }
.pending { display:inline-block; padding:.24rem .46rem; color:#ffd38a; background:#3a2b16; border:1px solid #795a28; border-radius:99px; font:700 .66rem/1 ui-monospace,SFMono-Regular,monospace; }
[data-testid="stMetric"] { background:#0d1722; border:1px solid var(--line); padding:.65rem .75rem; border-radius:.6rem; }
[data-testid="stMetricLabel"] { color:#71869c; }
[data-testid="stMetricValue"] { font-size:1.35rem; }
[data-testid="stVerticalBlockBorderWrapper"] { border-color:var(--line); background:#0c1520; }
.stTabs [data-baseweb="tab-list"] { gap:.4rem; border-bottom:1px solid var(--line); }
.stTabs [data-baseweb="tab"] { color:#8da2b7; height:2.7rem; }
.stTabs [aria-selected="true"] { color:#eaf5ff; }
.stButton button { border:1px solid #2f6e69; background:#123a38; color:#d8fffa; }
@media (max-width:900px) { .workflow { grid-template-columns:repeat(3,1fr); } .block-container { padding:.8rem; } }
</style>
""",
    unsafe_allow_html=True,
)


@st.cache_resource(show_spinner=False)
def scenario(mission_id: str):
    """Avoid recomputing the planner when an interaction only moves the aircraft."""

    return build_demo_scenario(mission_id)

st.markdown(
    """
<div class="brand-row"><div class="brand"><span class="brand-mark">M</span>MachLane</div>
<div class="run-state">Synthetic workspace · deterministic</div></div>
<div class="notice"><b>Research prototype — not FAA approved.</b> No surface-overpressure or sonic-boom compliance result is calculated.</div>
<div class="workflow"><span><b>01</b>Aircraft</span><span><b>02</b>Atmosphere</span><span><b>03</b>Route</span><span><b>04</b>Plan</span><span><b>05</b>Validate</span><span><b>06</b>Evidence</span></div>
""",
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
demo = scenario(mission_id)
rows = segment_rows(demo.route, demo.result)
distance_km = route_distance_m(demo.route) / 1000
map_latitude, map_longitude = interpolate_position(demo.route, 0.5)
map_zoom = max(1.2, min(4.0, 4.9 - math.log2(max(distance_km, 500) / 500)))

st.caption(
    f"{mission.rationale} Real endpoints from OurAirports; path is conceptual, not an ATC clearance."
)

workspace, inspector = st.columns([3.15, 1], gap="medium")

with workspace:
    progress_percent = st.slider("Aircraft position", 0, 100, 24, 1, format="%d%%")
    progress = progress_percent / 100

active_index = active_segment_index(demo.route, progress)
active = rows[active_index]
aircraft_lat, aircraft_lon = interpolate_position(demo.route, progress)

with workspace:
    title_left, title_right = st.columns([3, 1])
    with title_left:
        st.subheader("Route corridor")
        st.caption("Modeled operational envelope · not legal airspace approval")
    with title_right:
        st.markdown(
            '<div style="text-align:right"><span class="eligible">SYNTHETIC ELIGIBLE</span></div>',
            unsafe_allow_html=True,
        )
    st.markdown(
        f'<div class="mission-strip"><span>Route <b>{mission.origin.iata} → {mission.destination.iata} · {distance_km:,.0f} km</b></span><span>Geometry <b>WGS-84 geodesic</b></span><span>Aircraft <b>Demo SST</b></span><span>Atmosphere <b>Synthetic</b></span><span>Terrain <b>Flat</b></span><span>Engine <b>Mock MCO</b></span><span>Valid <b>{demo.atmosphere.valid_time:%H:%M UTC}</b></span></div>',
        unsafe_allow_html=True,
    )

    map_layers = [
        pdk.Layer(
            "PolygonLayer",
            corridor_rows(demo.route, demo.result),
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
            [{"position": [lon, lat]} for lat, lon in demo.route.waypoints],
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
                    "position": [mission.origin.longitude, mission.origin.latitude],
                    "label": mission.origin.iata,
                },
                {
                    "position": [mission.destination.longitude, mission.destination.latitude],
                    "label": mission.destination.iata,
                },
            ],
            get_position="position",
            get_text="label",
            get_size=15,
            get_pixel_offset=[0, -18],
            get_color=[226, 236, 246, 230],
        ),
        pdk.Layer(
            "ScatterplotLayer",
            [{"position": [aircraft_lon, aircraft_lat]}],
            get_position="position",
            get_radius=5200,
            get_fill_color=[255, 255, 255, 245],
            get_line_color=[45, 212, 191, 255],
            line_width_min_pixels=4,
            stroked=True,
        ),
        pdk.Layer(
            "TextLayer",
            [{"position": [aircraft_lon, aircraft_lat], "label": ">"}],
            get_position="position",
            get_text="label",
            get_size=20,
            get_color=[7, 17, 27, 255],
            get_angle=active["bearing_deg"] - 90,
        ),
    ]
    st.pydeck_chart(
        pdk.Deck(
            layers=map_layers,
            map_style=pdk.map_styles.CARTO_DARK,
            initial_view_state=pdk.ViewState(
                latitude=map_latitude,
                longitude=map_longitude,
                zoom=map_zoom,
                pitch=8,
                bearing=0,
            ),
            tooltip={
                "html": "<b>{segment}</b><br/>Mach {mach}<br/>{altitude_ft} ft<br/>{decision}",
                "style": {"backgroundColor": "#0b1420", "color": "#e5eef8"},
            },
        ),
        height=490,
    )
    legend_left, legend_right = st.columns([2, 1])
    with legend_left:
        st.caption("Teal corridor: accepted by the synthetic integration boundary")
    with legend_right:
        st.caption(f"{progress:.0%} complete · {active['segment']}")

with inspector:
    st.markdown('<div class="section-kicker">Live inspector</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="status-card"><div class="label">Active segment</div><div class="value">{active["segment"]}</div><div class="meta">{active["start_km"]:.1f}–{active["end_km"]:.1f} km · {active["bearing_deg"]:.0f}°</div></div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="status-card"><div class="label">Route geometry</div><div class="value">{distance_km:,.0f} km</div><div class="meta">{distance_km / 1.852:,.0f} nmi · WGS-84 ellipsoid</div></div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="status-card"><div class="label">Recommendation</div><div class="value">Mach {active["mach"]:.2f}</div><div class="meta">{active["altitude_ft"]:,} ft · scenario target unvalidated</div></div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="status-card"><div class="label">Decision</div><div class="value"><span class="eligible">SYNTHETIC ELIGIBLE</span></div><div class="meta">Mock-engine integration result only</div></div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="status-card"><div class="label">Surface boom</div><div class="value"><span class="pending">NOT MODELED</span></div><div class="meta">No 0.11 psf determination</div></div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="status-card"><div class="label">Fuel / trip time</div><div class="value"><span class="pending">NOT MODELED</span></div><div class="meta">Requires validated performance data and segment winds</div></div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="status-card"><div class="label">PCBoom validation</div><div class="value"><span class="pending">PENDING</span></div><div class="meta">External adapter available · no run imported</div></div>',
        unsafe_allow_html=True,
    )
    st.caption(
        f"{active['accepted_candidates']} of {active['total_candidates']} grid candidates accepted by the mock boundary."
    )

plan_tab, engineering_tab, evidence_tab = st.tabs(["Segment plan", "Engineering", "Evidence"])

with plan_tab:
    table = pd.DataFrame(rows).drop(columns=["path", "color"])
    st.dataframe(
        table,
        hide_index=True,
        width="stretch",
        column_order=["segment", "start_km", "end_km", "altitude_ft", "mach", "decision", "boom"],
        column_config={
            "segment": "Segment",
            "start_km": st.column_config.NumberColumn("From km", format="%.1f"),
            "end_km": st.column_config.NumberColumn("To km", format="%.1f"),
            "altitude_ft": st.column_config.NumberColumn("Altitude", format="%d ft"),
            "mach": st.column_config.NumberColumn("Mach", format="%.2f"),
            "decision": "Planning result",
            "boom": "Surface boom",
        },
    )

with engineering_tab:
    chart_left, chart_right = st.columns(2)
    distance = [row["end_km"] for row in rows]
    with chart_left:
        route_figure = go.Figure()
        route_figure.add_trace(
            go.Scatter(
                x=distance,
                y=[row["mach"] for row in rows],
                name="Selected Mach",
                line={"color": "#2dd4bf", "width": 3},
            )
        )
        route_figure.update_layout(
            title="Synthetic segment plan",
            xaxis_title="Route distance (km)",
            yaxis_title="Mach",
            template="plotly_dark",
            height=340,
            margin={"l": 20, "r": 15, "t": 50, "b": 20},
            paper_bgcolor="#0a111b",
            plot_bgcolor="#0d1722",
        )
        st.plotly_chart(route_figure, width="stretch")
    with chart_right:
        atmosphere_figure = go.Figure(
            go.Scatter(
                x=demo.atmosphere.temperature_k,
                y=[altitude * 3.28084 for altitude in demo.atmosphere.altitude_m],
                mode="lines+markers",
                line={"color": "#60a5fa", "width": 3},
                marker={"size": 5},
            )
        )
        atmosphere_figure.update_layout(
            title="Synthetic atmosphere",
            xaxis_title="Temperature (K)",
            yaxis_title="Altitude (ft)",
            template="plotly_dark",
            height=340,
            margin={"l": 20, "r": 15, "t": 50, "b": 20},
            paper_bgcolor="#0a111b",
            plot_bgcolor="#0d1722",
        )
        st.plotly_chart(atmosphere_figure, width="stretch")
    st.info(
        "Effective sound speed, ray paths, boom footprint, and absolute overpressure remain unavailable until cited and validated physics is implemented."
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
