"""Network-free Streamlit visualization for the synthetic MachLane demo."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import plotly.graph_objects as go
import pydeck as pdk
import streamlit as st

from open_mco.atmosphere import SyntheticAtmosphereProvider
from open_mco.compliance import compliance_matrix
from open_mco.demo import demo_route, synthetic_aircraft
from open_mco.optimization import GridSearchPlanner
from open_mco.physics import MockMCOEngine
from open_mco.route import interpolate_position
from open_mco.terrain import FlatTerrainProvider

st.set_page_config(page_title="MachLane", layout="wide")
st.error("RESEARCH PROTOTYPE — NOT FAA APPROVED")
st.title("MachLane synthetic corridor")
st.caption("Mock propagation is deterministic software-test behavior, not sonic-boom physics.")

aircraft = synthetic_aircraft()
route = demo_route()
weather = SyntheticAtmosphereProvider()
planner = GridSearchPlanner(
    atmosphere_provider=weather,
    terrain_provider=FlatTerrainProvider(),
    propagation_engine=MockMCOEngine(),
)
result = planner.plan(
    aircraft,
    route,
    mach_values=[1.02, 1.05, 1.08, 1.10, 1.12, 1.15],
    altitude_m=[12_192, 13_411, 14_630, 15_240],
    reliability_level=0.95,
    valid_time=datetime(2026, 8, 3, 12, tzinfo=UTC),
)

progress = st.slider("Aircraft progress", 0.0, 1.0, 0.25)
aircraft_lat, aircraft_lon = interpolate_position(route, progress)
route_rows = pd.DataFrame(
    [
        {
            "segment_id": segment.segment_id,
            "start": [segment.start_longitude, segment.start_latitude],
            "end": [segment.end_longitude, segment.end_latitude],
            "status": limit.status,
            "mach": limit.selected_mach,
            "altitude_m": limit.selected_altitude_m,
        }
        for segment, limit in zip(route.segments, result.segment_limits, strict=True)
    ]
)
layers = [
    pdk.Layer(
        "LineLayer",
        route_rows,
        get_source_position="start",
        get_target_position="end",
        get_color=[16, 185, 129, 220],
        get_width=4,
    ),
    pdk.Layer(
        "ScatterplotLayer",
        [{"position": [aircraft_lon, aircraft_lat]}],
        get_position="position",
        get_radius=6000,
        get_fill_color=[239, 68, 68, 255],
    ),
]
st.pydeck_chart(
    pdk.Deck(
        layers=layers,
        map_style=None,
        initial_view_state=pdk.ViewState(latitude=36.4, longitude=-95.0, zoom=5.2, pitch=30),
    )
)

left, right = st.columns(2)
with left:
    st.subheader("Segment recommendations")
    st.dataframe(route_rows[["segment_id", "status", "mach", "altitude_m"]], hide_index=True)
with right:
    profile = weather.profile(36.4, -95.0, datetime(2026, 8, 3, 12, tzinfo=UTC))
    figure = go.Figure(
        go.Scatter(x=profile.temperature_k, y=profile.altitude_m, mode="lines+markers")
    )
    figure.update_layout(
        title="Synthetic temperature profile", xaxis_title="K", yaxis_title="Altitude (m)"
    )
    st.plotly_chart(figure, use_container_width=True)

st.subheader("Provenance")
st.json(
    {
        "aircraft": aircraft.name.original_value,
        "weather": profile.source.model_dump(mode="json"),
        "terrain": "flat synthetic",
        "engine": result.engine_name,
        "label": result.label,
    }
)
st.subheader("FAA-oriented evidence checklist")
st.dataframe(
    pd.DataFrame([{"outcome": key, "status": value} for key, value in compliance_matrix().items()]),
    hide_index=True,
)
