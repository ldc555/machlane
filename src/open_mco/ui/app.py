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
from open_mco.ui.view_model import (
    METERS_TO_FEET,
    active_segment_index,
    atmosphere_metrics,
    corridor_rows,
    display_longitude,
    segment_rows,
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
.run-state { color:#9fb2c7; font:600 .72rem/1 ui-monospace,SFMono-Regular,monospace; letter-spacing:.08em; text-transform:uppercase; }
.notice { border:1px solid #855b27; background:#291f13; color:#ffd898; padding:.55rem .8rem; border-radius:.6rem; font-size:.78rem; margin-bottom:.85rem; }
.section-kicker { color:#6f879e; font:700 .68rem/1.2 ui-monospace,SFMono-Regular,monospace; letter-spacing:.12em; text-transform:uppercase; margin:.3rem 0 .65rem; }
.panel-note { padding:.65rem .8rem; border:1px solid var(--line); background:#0c1520; border-radius:.6rem; color:#9fb2c7; font-size:.76rem; line-height:1.45; }
.mission-strip { display:flex; gap:.9rem; flex-wrap:wrap; padding:.58rem .75rem; margin:.35rem 0 .65rem; border:1px solid var(--line); background:#0c1520; border-radius:.55rem; color:#8ba0b7; font-size:.72rem; }
.mission-strip b { color:#dbe8f4; font-weight:650; }
.status-card { border:1px solid var(--line); background:linear-gradient(145deg,#111d2b,#0c1520); border-radius:.7rem; padding:.8rem .9rem; margin:.35rem 0 .65rem; }
.status-card .label { color:#70879e; font-size:.68rem; letter-spacing:.1em; text-transform:uppercase; }
.status-card .value { color:#e7f8f5; font-size:1.15rem; font-weight:750; margin:.15rem 0; }
.status-card .meta { color:#8ba0b7; font-size:.74rem; }
.calc-grid { display:grid; grid-template-columns:repeat(4,1fr); gap:.55rem; margin:.4rem 0 .8rem; }
.calc-step { border:1px solid var(--line); background:#0d1722; border-radius:.65rem; padding:.75rem; min-height:7rem; }
.calc-step .number { color:var(--teal); font:700 .68rem/1 ui-monospace,SFMono-Regular,monospace; letter-spacing:.1em; }
.calc-step b { display:block; color:#e5eef8; margin:.45rem 0 .25rem; font-size:.85rem; }
.calc-step span { color:#8ba0b7; font-size:.73rem; line-height:1.4; }
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
@media (max-width:900px) { .calc-grid { grid-template-columns:repeat(2,1fr); } .block-container { padding:.8rem; } }
</style>
""",
    unsafe_allow_html=True,
)


SCENARIO_CACHE_SCHEMA = "weather-regimes-v1"


@st.cache_resource(show_spinner=False)
def scenario(mission_id: str, cache_schema: str):
    """Cache by mission and result schema so old objects cannot survive model changes."""

    del cache_schema
    return build_demo_scenario(mission_id)

st.markdown(
    """
<div class="brand-row"><div class="brand"><span class="brand-mark">M</span>MachLane</div>
<div class="run-state">Synthetic workspace · deterministic</div></div>
<div class="notice"><b>Research prototype — not FAA approved.</b> No surface-overpressure or sonic-boom compliance result is calculated.</div>
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
demo = scenario(mission_id, SCENARIO_CACHE_SCHEMA)
rows = segment_rows(demo.route, demo.result)
regime_by_id = {regime.segment_id: regime for regime in demo.weather_regimes}
segment_palette = (
    [45, 212, 191, 225],
    [96, 165, 250, 225],
    [167, 139, 250, 225],
    [251, 191, 36, 225],
    [244, 114, 182, 225],
    [52, 211, 153, 225],
)
for index, row in enumerate(rows):
    regime = regime_by_id[str(row["segment"])]
    row.update(
        {
            "boundary_reason": regime.boundary_reason,
            "weather_samples": regime.sample_count,
            "regime_temperature_c": regime.temperature_k - 273.15,
            "regime_pressure_hpa": regime.pressure_hpa,
            "regime_wind_kt": regime.wind_speed_mps * 1.943844492,
            "ray_status": "NOT MODELED",
            "color": segment_palette[index % len(segment_palette)],
        }
    )
corridors = corridor_rows(demo.route, demo.result)
for index, corridor in enumerate(corridors):
    corridor["color"] = [*segment_palette[index % len(segment_palette)][:3], 48]
distance_km = route_distance_m(demo.route) / 1000
distance_nmi = distance_km / 1.852
map_latitude, map_longitude = interpolate_position(demo.route, 0.5)
map_zoom = max(1.2, min(4.0, 4.9 - math.log2(max(distance_km, 500) / 500)))

st.caption(
    f"{mission.rationale} Real endpoints from OurAirports; path is conceptual, not an ATC clearance."
)

summary_columns = st.columns(4)
summary_columns[0].metric(
    "Route distance", f"{distance_nmi:,.0f} nmi", f"{distance_km:,.0f} km", delta_color="off"
)
summary_columns[1].metric(
    "Weather segments",
    f"{len(demo.route.segments)}",
    "Variable length · atmosphere-defined",
    delta_color="off",
)
summary_columns[2].metric(
    "Weather input", "Synthetic", "Live NOAA not connected", delta_color="off"
)
summary_columns[3].metric(
    "Boom output", "Not modeled", "No ground overpressure", delta_color="off"
)

@st.fragment
def live_route_tracker() -> None:
    """Rerun only the position-dependent map and inspector during slider interaction."""

    workspace, inspector = st.columns([3.15, 1], gap="medium")
    with workspace:
        progress_percent = st.slider(
            "Aircraft position",
            0,
            100,
            24,
            1,
            format="%d%%",
            key="aircraft_progress",
        )
    progress = progress_percent / 100
    active_index = active_segment_index(demo.route, progress)
    active = rows[active_index]
    aircraft_lat, aircraft_lon = interpolate_position(demo.route, progress)
    aircraft_display_lon = display_longitude(aircraft_lon, map_longitude)
    active_limit = demo.result.segment_limits[active_index]
    active_altitude_m = active_limit.selected_altitude_m or 0.0
    active_atmosphere = demo.segment_atmospheres[active_index]
    weather_at_altitude = atmosphere_metrics(
        active_atmosphere, active_altitude_m, float(active["bearing_deg"])
    )

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
            f'<div class="mission-strip"><span>Route <b>{mission.origin.iata} → {mission.destination.iata} · {distance_nmi:,.0f} nmi</b></span><span>Segments <b>Weather regimes · variable length</b></span><span>Aircraft <b>Demo SST</b></span><span>Atmosphere <b>Synthetic</b></span><span>Terrain <b>Flat</b></span><span>Engine <b>Mock MCO</b></span><span>Valid <b>{demo.atmosphere.valid_time:%H:%M UTC}</b></span></div>',
            unsafe_allow_html=True,
        )
        map_layers = [
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
            pdk.Layer(
                "ScatterplotLayer",
                [{"position": [aircraft_display_lon, aircraft_lat]}],
                get_position="position",
                get_radius=7000,
                radius_min_pixels=16,
                radius_max_pixels=24,
                get_fill_color=[255, 255, 255, 245],
                get_line_color=[45, 212, 191, 255],
                line_width_min_pixels=4,
                stroked=True,
            ),
            pdk.Layer(
                "TextLayer",
                [{"position": [aircraft_display_lon, aircraft_lat], "label": "✈"}],
                get_position="position",
                get_text="label",
                get_size=36,
                get_color=[7, 17, 27, 255],
                get_angle=active["bearing_deg"] - 45,
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
            st.caption("Colored sections: internally uniform synthetic weather regimes")
        with legend_right:
            st.caption(f"{progress:.0%} complete · {active['segment']}")

    with inspector:
        st.markdown('<div class="section-kicker">Live inspector</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="status-card"><div class="label">Active segment</div><div class="value">{active["segment"]}</div><div class="meta">{active["start_nmi"]:.1f}–{active["end_nmi"]:.1f} nmi · track {active["bearing_deg"]:.0f}°</div></div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="status-card"><div class="label">Recommendation</div><div class="value">Mach {active["mach"]:.2f}</div><div class="meta">{active["altitude_ft"]:,} ft · scenario target unvalidated</div></div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="status-card"><div class="label">Ambient profile</div><div class="value">{weather_at_altitude["pressure_hpa"]:.0f} hPa</div><div class="meta">{weather_at_altitude["temperature_c"]:.1f} °C · wind {weather_at_altitude["wind_speed_kt"]:.0f} kt · synthetic</div></div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="status-card"><div class="label">Why this segment starts</div><div class="value" style="font-size:.92rem">{active["boundary_reason"]}</div><div class="meta">{active["weather_samples"]} atmosphere samples grouped</div></div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="status-card"><div class="label">Surface boom</div><div class="value"><span class="pending">NOT MODELED</span></div><div class="meta">No 0.11 psf determination</div></div>',
            unsafe_allow_html=True,
        )
        st.caption(
            f"{active['accepted_candidates']} of {active['total_candidates']} grid candidates accepted by the mock boundary."
        )


live_route_tracker()

progress = float(st.session_state.get("aircraft_progress", 24)) / 100
active_index = active_segment_index(demo.route, progress)
active = rows[active_index]
active_limit = demo.result.segment_limits[active_index]
active_altitude_m = active_limit.selected_altitude_m or 0.0
active_atmosphere = demo.segment_atmospheres[active_index]
weather_at_altitude = atmosphere_metrics(
    active_atmosphere, active_altitude_m, float(active["bearing_deg"])
)

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
    weather_columns = st.columns(4)
    weather_columns[0].metric("Ambient pressure", f'{weather_at_altitude["pressure_hpa"]:.0f} hPa')
    weather_columns[1].metric("Temperature", f'{weather_at_altitude["temperature_c"]:.1f} °C')
    weather_columns[2].metric("Wind speed", f'{weather_at_altitude["wind_speed_kt"]:.0f} kt')
    weather_columns[3].metric("Along-track wind", f'{weather_at_altitude["along_wind_kt"]:+.0f} kt')

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
    with st.expander("View profile values"):
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
