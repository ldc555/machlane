"""Aircraft workbook import, review, and local activation workspace."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go  # type: ignore[import-untyped]
import streamlit as st
from plotly.subplots import make_subplots  # type: ignore[import-untyped]

from open_mco.aircraft import (
    AircraftDefinition,
    AircraftField,
    AircraftStore,
    AircraftWorkbookError,
    PhasePoint,
    PhaseTiming,
    export_aircraft_definition_workbook,
    load_aircraft_definition_workbook,
)

PROJECT_ROOT = Path(__file__).resolve().parents[4]
STORE = AircraftStore(PROJECT_ROOT / "data/local/aircraft")

st.set_page_config(page_title="MachLane · Aircraft Import", page_icon="✦", layout="wide")
st.markdown(
    """
<style>
.stApp { background:radial-gradient(circle at 50% -20%,#192b40 0,#0a111b 43%,#070c13 100%); color:#f3f7fb; }
[data-testid="stHeader"] { background:transparent; }
.block-container { max-width:1500px; padding:1rem 1.5rem 3rem; }
.hero { border:1px solid #3b536c; background:linear-gradient(145deg,#172a40,#0d1825); border-radius:.8rem; padding:1rem 1.1rem; margin:.5rem 0 1rem; }
.hero h2 { margin:0 0 .35rem; color:#ffffff; }
.hero p { margin:0; color:#c3d4e4; }
.gate { border:1px solid #9a702c; background:#302415; color:#ffe2a8; border-radius:.65rem; padding:.8rem; }
[data-testid="stMetric"] { background:#10243a; border:1px solid #5e7d9d; padding:.8rem; border-radius:.65rem; box-shadow:0 8px 24px #0005; }
[data-testid="stMetricLabel"] p { color:#cfe4f7 !important; font-size:.86rem !important; font-weight:700 !important; letter-spacing:.03em; }
[data-testid="stMetricValue"] { color:#ffffff !important; font-weight:800 !important; }
[data-testid="stCaptionContainer"] p, .stCaption p { color:#c8d9e8 !important; }
[data-testid="stFileUploaderDropzone"] { background:#10243a !important; border:2px dashed #68b7e8 !important; color:#fff !important; }
[data-testid="stFileUploaderDropzone"] * { color:#eef8ff !important; }
[data-testid="stFileUploaderDropzone"] button { color:#06121d !important; background:#83e6ff !important; border:1px solid #baf2ff !important; font-weight:850 !important; }
[data-baseweb="tab-list"] { gap:.35rem; border-bottom:1px solid #486681; }
button[data-baseweb="tab"] { color:#cfe3f4 !important; background:#102033 !important; border:1px solid #3d5c78 !important; border-radius:.5rem .5rem 0 0; padding:.65rem .9rem; }
button[data-baseweb="tab"][aria-selected="true"] { color:#ffffff !important; background:#176184 !important; border-color:#65c4ee !important; font-weight:800 !important; }
[data-testid="stDataFrame"] { border:1px solid #52718e; border-radius:.55rem; overflow:hidden; }
.stAlert { border:1px solid #587795 !important; }
h1,h2,h3 { color:#ffffff !important; }
p,label { color:#d7e5f1; }
</style>
""",
    unsafe_allow_html=True,
)

title, back = st.columns([5, 1], vertical_alignment="center")
with title:
    st.markdown("# Aircraft")
    st.caption(
        "Drop the LM1021, NASA STCA, or Boom/XB-1 Excel file. "
        "MachLane detects and populates it."
    )
with back:
    st.markdown("[← **MISSION WORKSPACE**](/)")

uploaded = st.file_uploader(
    "DROP AIRCRAFT EXCEL HERE",
    type=["xlsx", "xlsm"],
    help=(
        "Accepted: LM1021, NASA STCA, or the current Boom/XB-1 workbook contract. "
        "MachLane never fills unsupported engineering values."
    ),
)

definition: AircraftDefinition | None = None
if uploaded is not None:
    try:
        definition = load_aircraft_definition_workbook(uploaded.getvalue())
    except AircraftWorkbookError as exc:
        st.error(f"Aircraft workbook rejected: {exc}")
        st.stop()
    else:
        checksum = definition.workbook_checksum or "unavailable"
        st.success(
            f"{definition.value('Aircraft Name') or 'Aircraft'} populated · SHA-256 {checksum[:12]}… · review before activation."
        )

if definition is None:
    st.info(
        "No aircraft is preloaded. Drop an LM1021, NASA STCA, or Boom/XB-1 "
        "`.xlsx` or `.xlsm` file above."
    )
    st.stop()
active_definition = definition

st.markdown(
    f"""
<div class="hero"><h2>{active_definition.value('Aircraft Name') or 'Uploaded aircraft'}</h2><p>Workbook-backed aircraft definition · NOAA supplies the operational atmosphere.</p></div>
""",
    unsafe_allow_html=True,
)

status_a, status_b, status_c, status_d, status_e = st.columns(5)
status_a.metric("Required fields", f"{len(active_definition.missing_required_fields)} missing")
status_b.metric("Performance deck", f"{len(active_definition.performance_map)} points")
status_c.metric("Near-field", f"{len(active_definition.nearfield_samples)} samples")
status_d.metric("Flight phases", f"{len(active_definition.phase_profile)} points")
status_e.metric(
    "Atmosphere benchmarks", f"{len(active_definition.benchmark_atmospheres)} profiles"
)

editor_version = active_definition.workbook_checksum or f"revision-{active_definition.revision}"


def _optional_text(value: Any) -> str | None:
    if value is None or pd.isna(value) or str(value).strip() == "":
        return None
    return str(value).strip()


def _field_frame(section: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Parameter": item.parameter,
                "Value": item.value,
                "Unit": item.unit,
                "Required": item.required,
                "Evidence Class": item.evidence_class,
                "Source": item.source_name,
                "Source URL": item.source_url,
                "Page/Figure": item.page_figure,
                "Notes": item.notes,
            }
            for item in active_definition.fields
            if item.section == section
        ],
        columns=[
            "Parameter",
            "Value",
            "Unit",
            "Required",
            "Evidence Class",
            "Source",
            "Source URL",
            "Page/Figure",
            "Notes",
        ],
    )


def _edit_fields(section: str, label: str) -> pd.DataFrame:
    st.markdown(f"### {label}")
    return st.data_editor(
        _field_frame(section),
        hide_index=True,
        width="stretch",
        num_rows="fixed",
        disabled=["Parameter", "Unit", "Required"],
        key=f"fields-{section}-{editor_version}",
        column_config={
            "Required": st.column_config.CheckboxColumn("Required"),
            "Evidence Class": st.column_config.SelectboxColumn(
                options=["PUBLISHED", "CALCULATED", "UNVALIDATED_ASSUMPTION", "UNAVAILABLE"]
            ),
            "Source URL": st.column_config.LinkColumn("Source URL", display_text="Open"),
        },
    )


def _nearfield_figure(frame: pd.DataFrame, azimuth_deg: float) -> go.Figure:
    selected = frame.loc[frame["azimuth_deg"] == azimuth_deg].sort_values(
        "axial_position_ft"
    )
    figure = go.Figure(
        go.Scatter(
            x=selected["axial_position_ft"],
            y=selected["delta_pressure_psf"],
            mode="lines",
            line={"color": "#36c5f0", "width": 2.5},
            hovertemplate="%{x:,.1f} ft<br>%{y:+.4f} psf<extra></extra>",
        )
    )
    figure.add_hline(y=0, line_color="#8aa4bd", line_width=1)
    figure.update_layout(
        template="plotly_dark",
        height=430,
        margin={"l": 20, "r": 20, "t": 50, "b": 20},
        paper_bgcolor="#0b1725",
        plot_bgcolor="#0b1725",
        font={"color": "#e7f2ff"},
        title={
            "text": f"LM1021 near-field pressure signature · azimuth {azimuth_deg:g}°",
            "font": {"color": "#ffffff", "size": 20},
        },
        xaxis_title="Axial position (ft)",
        yaxis_title="Near-field Δp (psf)",
        hovermode="x unified",
    )
    return figure


def _benchmark_figure(profile: Any) -> go.Figure:
    altitude_ft = [value * 3.280839895 for value in profile.altitude_m]
    temperature_f = [(value - 273.15) * 9 / 5 + 32 for value in profile.temperature_k]
    pressure_inhg = [value * 0.000295299830714 for value in profile.pressure_pa]
    wind_kt = [
        (u**2 + v**2) ** 0.5 * 1.943844492
        for u, v in zip(profile.zonal_wind_mps, profile.meridional_wind_mps, strict=True)
    ]
    humidity_percent = [value * 100 for value in profile.humidity_fraction]
    figure = make_subplots(
        rows=1,
        cols=4,
        shared_yaxes=True,
        horizontal_spacing=0.055,
        subplot_titles=("Temperature", "Pressure", "Wind speed", "Humidity"),
    )
    series = (
        (temperature_f, "#ffbd59", "°F"),
        (pressure_inhg, "#ff5b79", "inHg"),
        (wind_kt, "#36c5f0", "kt"),
        (humidity_percent, "#7ee787", "%"),
    )
    for column, (values, color, unit) in enumerate(series, start=1):
        figure.add_trace(
            go.Scatter(
                x=values,
                y=altitude_ft,
                mode="lines+markers",
                line={"color": color, "width": 2.3},
                marker={"size": 5},
                showlegend=False,
                hovertemplate=f"%{{x:.2f}} {unit}<br>%{{y:,.0f}} ft<extra></extra>",
            ),
            row=1,
            col=column,
        )
    figure.update_yaxes(title_text="Altitude (ft)", row=1, col=1)
    figure.update_layout(
        template="plotly_dark",
        height=500,
        margin={"l": 20, "r": 20, "t": 60, "b": 20},
        paper_bgcolor="#0b1725",
        plot_bgcolor="#0b1725",
        font={"color": "#e7f2ff"},
        title={
            "text": f"{profile.display_name} · propagation validation benchmark",
            "font": {"color": "#ffffff", "size": 20},
        },
    )
    figure.update_annotations(font_color="#e7f2ff")
    return figure


general_tab, limits_tab, mission_tab, performance_tab, phase_tab, boom_tab = st.tabs(
    [
        "General",
        "Operating limits",
        "Mission setup",
        "Performance + fuel",
        "Phase profile",
        "Sonic boom inputs",
    ]
)
edited_frames: dict[str, pd.DataFrame] = {}
with general_tab:
    edited_frames["General"] = _edit_fields("General", "Identity and geometry")
with limits_tab:
    edited_frames["Operating Limits"] = _edit_fields(
        "Operating Limits", "Weights and demonstrated/operating limits"
    )
with mission_tab:
    edited_frames["Mission Config"] = _edit_fields(
        "Mission Config", "Planning configuration"
    )
    st.info(
        "NOAA provides the operational route- and time-matched atmosphere. NASA profiles "
        "1, 2, and the standard atmosphere remain isolated validation benchmarks."
    )
with performance_tab:
    st.markdown("### Calibrated aircraft deck")
    st.caption(
        "Each complete weight × altitude × Mach row must contain thrust available, drag required, fuel flow, throttle, and sustainability."
    )
    if active_definition.performance_map:
        st.dataframe(
            pd.DataFrame([item.model_dump() for item in active_definition.performance_map]),
            hide_index=True,
            width="stretch",
        )
    else:
        st.warning(
            "No calibrated performance/fuel rows loaded. MachLane cannot prove a requested Mach/altitude is sustainable or evolve aircraft weight."
        )
with phase_tab:
    phase_columns = [
        "Sequence",
        "Scene",
        "Altitude (ft)",
        "Mach",
        "Evidence Class",
        "Source",
        "Source URL",
        "Page/Figure",
        "Notes",
    ]
    phase_frame = pd.DataFrame(
        [
            {
                "Sequence": point.sequence,
                "Scene": point.phase,
                "Altitude (ft)": point.altitude_ft,
                "Mach": point.mach,
                "Evidence Class": point.evidence_class,
                "Source": point.source_name,
                "Source URL": point.source_url,
                "Page/Figure": point.page_figure,
                "Notes": point.notes,
            }
            for point in active_definition.phase_profile
        ],
        columns=phase_columns,
    )
    edited_phase = st.data_editor(
        phase_frame,
        hide_index=True,
        width="stretch",
        num_rows="dynamic",
        key=f"phase-{editor_version}",
        column_config={"Source URL": st.column_config.LinkColumn(display_text="Open")},
    )
    timing_columns = [
        "Phase",
        "Duration (min)",
        "Basis",
        "Source",
        "Source URL",
        "Page/Figure",
        "Notes",
    ]
    timing_frame = pd.DataFrame(
        [
            {
                "Phase": item.phase,
                "Duration (min)": item.duration_min,
                "Basis": item.basis,
                "Source": item.source_name,
                "Source URL": item.source_url,
                "Page/Figure": item.page_figure,
                "Notes": item.notes,
            }
            for item in active_definition.phase_timing
        ],
        columns=timing_columns,
    )
    edited_timing = st.data_editor(
        timing_frame,
        hide_index=True,
        width="stretch",
        num_rows="fixed",
        disabled=["Phase"],
        key=f"timing-{editor_version}",
    )
with boom_tab:
    edited_frames["Sonic Boom"] = _edit_fields(
        "Sonic Boom", "Acoustic model and validation metadata"
    )
    waveform_tab, atmosphere_tab, raw_tab = st.tabs(
        ["Near-field waveform", "Atmosphere benchmarks", "Raw imported samples"]
    )
    with waveform_tab:
        if active_definition.nearfield_samples:
            nearfield_frame = pd.DataFrame(
                [item.model_dump() for item in active_definition.nearfield_samples]
            )
            azimuths = sorted(float(value) for value in nearfield_frame["azimuth_deg"].unique())
            default_azimuth = azimuths.index(0.0) if 0.0 in azimuths else 0
            azimuth = st.selectbox(
                "Near-field azimuth",
                azimuths,
                index=default_azimuth,
                format_func=lambda value: f"{value:g}°",
            )
            st.plotly_chart(
                _nearfield_figure(nearfield_frame, float(azimuth)), width="stretch"
            )
            selected = nearfield_frame.loc[nearfield_frame["azimuth_deg"] == azimuth]
            wave_a, wave_b, wave_c, wave_d = st.columns(4)
            wave_a.metric("Samples", f"{len(selected):,}")
            wave_b.metric("Mach", f"{selected['mach'].iloc[0]:.2f}")
            wave_c.metric("Altitude", f"{selected['altitude_ft'].iloc[0]:,.0f} ft")
            wave_d.metric(
                "Reference distance", f"{selected['reference_distance_ft'].iloc[0]:,.1f} ft"
            )
            st.caption(
                "This is the aircraft pressure field at the NASA extraction cylinder—not a "
                "ground waveform and not surface boom overpressure."
            )
        else:
            st.warning("No near-field signature samples were imported.")
    with atmosphere_tab:
        if active_definition.benchmark_atmospheres:
            profiles = {
                profile.profile_id: profile
                for profile in active_definition.benchmark_atmospheres
            }
            selected_profile_id = st.selectbox(
                "NASA validation atmosphere",
                list(profiles),
                format_func=lambda value: profiles[value].display_name,
            )
            selected_profile = profiles[selected_profile_id]
            st.plotly_chart(_benchmark_figure(selected_profile), width="stretch")
            benchmark_state = (
                "REQUIRED LM1021 BENCHMARK"
                if selected_profile.required_for_validation
                else "OPTIONAL CROSS-CHECK"
            )
            st.info(
                f"{benchmark_state}. These profiles reproduce the NASA workshop cases. "
                "Real route calculations use NOAA instead."
            )
            st.markdown(f"[Open NASA source]({selected_profile.source_url})")
        else:
            st.warning(
                "No NASA benchmark atmospheres were imported. NOAA can model a real route, "
                "but the LM1021 propagation implementation cannot be benchmarked reproducibly."
            )
    with raw_tab:
        if active_definition.nearfield_samples:
            st.dataframe(nearfield_frame, hide_index=True, width="stretch")
    st.markdown(
        """
<div class="gate"><b>Surface boom stays locked until the required physics exists.</b><br/>A condition-specific near-field signature or equivalent-area/CFD input, a nonlinear propagation engine, primary and secondary rays, ground waveform metrics, and PCBoom/flight-measurement validation are required.</div>
""",
        unsafe_allow_html=True,
    )


def _build_definition() -> AircraftDefinition:
    fields: list[AircraftField] = []
    for section, frame in edited_frames.items():
        for row in frame.to_dict(orient="records"):
            fields.append(
                AircraftField(
                    section=section,
                    parameter=str(row["Parameter"]),
                    value=_optional_text(row["Value"]),
                    unit=str(row["Unit"]),
                    required=bool(row["Required"]),
                    evidence_class=str(row["Evidence Class"]),
                    source_name=_optional_text(row["Source"]),
                    source_url=_optional_text(row["Source URL"]),
                    page_figure=_optional_text(row["Page/Figure"]),
                    notes=_optional_text(row["Notes"]),
                )
            )
    phase_points = tuple(
        PhasePoint(
            sequence=int(row["Sequence"]),
            phase=str(row["Scene"]),
            altitude_ft=float(row["Altitude (ft)"]),
            mach=float(row["Mach"]),
            evidence_class=str(row["Evidence Class"]),
            source_name=str(row["Source"]),
            source_url=str(row["Source URL"]),
            page_figure=str(row["Page/Figure"]),
            notes=_optional_text(row["Notes"]),
        )
        for row in edited_phase.to_dict(orient="records")
        if _optional_text(row.get("Sequence")) is not None
    )
    phase_timings = tuple(
        PhaseTiming(
            phase=str(row["Phase"]),  # type: ignore[arg-type]
            duration_min=(
                None
                if pd.isna(row["Duration (min)"])
                else float(row["Duration (min)"])
            ),
            basis=str(row["Basis"]),  # type: ignore[arg-type]
            source_name=_optional_text(row["Source"]),
            source_url=_optional_text(row["Source URL"]),
            page_figure=_optional_text(row["Page/Figure"]),
            notes=_optional_text(row["Notes"]),
        )
        for row in edited_timing.to_dict(orient="records")
    )
    return AircraftDefinition(
        aircraft_id="aircraft_one",
        display_name=next(
            (
                field.value
                for field in fields
                if field.parameter == "Aircraft Name" and field.value
            ),
            "Uploaded Aircraft",
        ),
        revision=active_definition.revision + 1,
        updated_at=datetime.now(UTC),
        fields=tuple(fields),
        phase_profile=phase_points,
        phase_timing=phase_timings,
        performance_map=active_definition.performance_map,
        nearfield_samples=active_definition.nearfield_samples,
        benchmark_atmospheres=active_definition.benchmark_atmospheres,
        workbook_checksum=active_definition.workbook_checksum,
    )


st.divider()
save_col, export_col, readiness_col = st.columns([1, 1, 2], vertical_alignment="center")
with save_col:
    if st.button("SAVE AIRCRAFT & OPEN ROUTES", type="primary", width="stretch"):
        try:
            updated = _build_definition()
            STORE.save(updated)
            normalized_payload = export_aircraft_definition_workbook(updated)
            normalized_path = STORE.directory / f"{updated.aircraft_id}.xlsx"
            normalized_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = normalized_path.with_suffix(".xlsx.tmp")
            temporary_path.write_bytes(normalized_payload)
            temporary_path.replace(normalized_path)
        except (ValueError, OSError) as exc:
            st.error(f"Aircraft was not saved: {exc}")
        else:
            st.session_state["active_aircraft_checksum"] = (
                updated.workbook_checksum or f"revision-{updated.revision}"
            )
            if updated.phase_profile_ready:
                st.switch_page("app.py")
            else:
                st.success(f"{updated.display_name} saved locally, including normalized Excel.")
                st.warning(
                    "Route modeling remains locked because the phase profile or climb/descent/approach timing is incomplete."
                )
with export_col:
    st.download_button(
        "DOWNLOAD NORMALIZED EXCEL",
        export_aircraft_definition_workbook(active_definition),
        file_name="aircraft_one_normalized.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        width="stretch",
    )
with readiness_col:
    blockers = []
    if active_definition.missing_required_fields:
        blockers.append(
            f"{len(active_definition.missing_required_fields)} required aircraft fields"
        )
    if not active_definition.performance_data_ready:
        blockers.append("calibrated performance/fuel deck")
    if not active_definition.nearfield_ready:
        blockers.append("near-field pressure signature")
    st.caption(
        "Workbook aircraft inputs are present. Run analysis can calculate the built-in "
        "primary-ray research estimate; compliant-corridor approval remains unavailable."
        if not blockers
        else "Still required for full continuous boom analysis: " + ", ".join(blockers) + "."
    )
