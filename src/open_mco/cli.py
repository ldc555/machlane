"""MachLane command-line interface."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import typer
import yaml

from open_mco.aircraft import AircraftWorkbookError, load_aircraft_workbook
from open_mco.atmosphere import (
    AtmosphereProvider,
    ERA5Provider,
    HerbieGEFSProvider,
    HerbieHRRRProvider,
)
from open_mco.demo import run_demo
from open_mco.physics import assess_boom_readiness
from open_mco.route import OpenSkyTrackProvider, get_mission, route_from_waypoints
from open_mco.terrain import USGS3DEPProvider
from open_mco.validation import PCBoomAdapter, SU2NearFieldAdapter

app = typer.Typer(help="MachLane research planning and evidence tools.", no_args_is_help=True)


@app.command("validate-aircraft")
def validate_aircraft(path: Path) -> None:
    """Validate an aircraft workbook and print its checksum."""

    try:
        aircraft = load_aircraft_workbook(path)
    except AircraftWorkbookError as exc:
        typer.echo(f"INVALID: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"VALID: {aircraft.name.original_value}; sha256={aircraft.workbook_checksum}")


@app.command("boom-readiness")
def boom_readiness(
    path: Path = typer.Argument(..., help="Aircraft workbook to audit."),
    near_field: Path | None = typer.Option(None, help="Optional SI near-field signature CSV."),
    output: Path | None = typer.Option(None, help="Optional JSON report path."),
    strict: bool = typer.Option(False, help="Exit non-zero while any blocker remains."),
) -> None:
    """Explain every blocker before a physical sonic-boom prediction can run."""

    report = assess_boom_readiness(path, near_field_path=near_field)
    typer.echo(f"Physical prediction ready: {'YES' if report.ready_for_physical_prediction else 'NO'}")
    for check in report.checks:
        typer.echo(f"{check.status.value:15} {check.key}: {check.detail}")
        if check.action:
            typer.echo(f"{'':16}next: {check.action}")
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        typer.echo(f"Wrote machine-readable report to {output}")
    if strict and not report.ready_for_physical_prediction:
        raise typer.Exit(2)


@app.command()
def demo(
    results_root: Path = typer.Option(Path("results"), help="Evidence-package parent directory."),
) -> None:
    """Run the network-free, explicitly synthetic vertical slice."""

    target = run_demo(results_root=results_root)
    typer.echo("RESEARCH PROTOTYPE — NOT FAA APPROVED")
    typer.echo(f"Synthetic evidence package: {target}")


@app.command()
def plan(config: Path = typer.Option(Path("configs/baseline.yml"), exists=True)) -> None:
    """Validate configured inputs; use demo until the aircraft workbook is populated."""

    settings = yaml.safe_load(config.read_text(encoding="utf-8"))
    aircraft_path = Path(settings["aircraft"]["path"])
    try:
        load_aircraft_workbook(aircraft_path)
    except AircraftWorkbookError as exc:
        typer.echo(
            f"Configured aircraft is not ready: {exc}\n"
            "Populate required workbook cells from reviewed sources, or run `machlane demo` for the synthetic path.",
            err=True,
        )
        raise typer.Exit(1) from exc
    typer.echo(
        "Aircraft is valid. Real planning remains blocked until a validated propagation engine is selected."
    )
    raise typer.Exit(2)


@app.command("fetch-weather")
def fetch_weather(
    provider: str = typer.Option(..., help="hrrr, gefs, or era5"),
    latitude: float = typer.Option(...),
    longitude: float = typer.Option(...),
    valid_time: datetime = typer.Option(..., formats=["%Y-%m-%dT%H:%M:%S%z"]),
    forecast_hour: int = typer.Option(0, min=0),
    member: int = typer.Option(0, min=0, max=30),
    output: Path = typer.Option(Path("data/processed/weather_profile.json")),
) -> None:
    """Fetch one auditable pressure-level column through a configured real-data adapter."""

    normalized = provider.lower()
    if normalized not in {"hrrr", "gefs", "era5"}:
        raise typer.BadParameter("provider must be hrrr, gefs, or era5")
    if normalized == "hrrr":
        weather: AtmosphereProvider = HerbieHRRRProvider(
            network_enabled=True, forecast_hour=forecast_hour
        )
    elif normalized == "gefs":
        weather = HerbieGEFSProvider(
            network_enabled=True, forecast_hour=forecast_hour, member=member
        )
    else:
        weather = ERA5Provider(network_enabled=True)
    try:
        profile = weather.profile(latitude, longitude, valid_time)
    except (RuntimeError, ValueError, OSError) as exc:
        typer.echo(f"Weather fetch failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
    typer.echo(
        f"Wrote {normalized.upper()} profile with {len(profile.altitude_m)} levels to {output}"
    )


@app.command("fetch-terrain")
def fetch_terrain(
    start_latitude: float = typer.Option(...),
    start_longitude: float = typer.Option(...),
    end_latitude: float = typer.Option(...),
    end_longitude: float = typer.Option(...),
    sample_spacing_m: float = typer.Option(1_000.0, min=1),
    output: Path = typer.Option(Path("data/processed/terrain_profile.json")),
) -> None:
    """Fetch an auditable USGS 3DEP profile for one route leg."""

    route = route_from_waypoints(
        [(start_latitude, start_longitude), (end_latitude, end_longitude)],
        spacing_m=10_000_000,
        name="terrain fetch leg",
    )
    try:
        terrain = USGS3DEPProvider(network_enabled=True, sample_spacing_m=sample_spacing_m).profile(
            route.segments[0]
        )
    except (RuntimeError, ValueError, OSError) as exc:
        typer.echo(f"Terrain fetch failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(terrain.model_dump_json(indent=2), encoding="utf-8")
    typer.echo(f"Wrote USGS 3DEP profile with {len(terrain.distance_m)} samples to {output}")


@app.command("fetch-route")
def fetch_route(
    mission_id: str = typer.Option("dfw_jfk", help="Curated mission identifier."),
    observed_date: datetime = typer.Option(
        ...,
        "--date",
        formats=["%Y-%m-%d"],
        help="UTC departure date within OpenSky's recent-track window.",
    ),
    output: Path = typer.Option(Path("data/processed/opensky_route.json")),
) -> None:
    """Fetch one recent observed OpenSky trajectory using OAuth credentials from the environment."""

    try:
        mission = get_mission(mission_id)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--mission-id") from exc
    begin = observed_date.replace(tzinfo=UTC)
    end = begin + timedelta(days=1) - timedelta(seconds=1)
    provider = OpenSkyTrackProvider(network_enabled=True)
    try:
        route = provider.route_for_airports(
            mission.origin,
            mission.destination,
            begin=begin,
            end=end,
        )
    except (RuntimeError, ValueError, OSError) as exc:
        typer.echo(f"OpenSky route fetch failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(route.model_dump_json(indent=2), encoding="utf-8")
    source = route.source
    typer.echo(
        f"Wrote {len(route.waypoints)} OpenSky waypoints for "
        f"{source.callsign if source and source.callsign else mission.label} to {output}"
    )


@app.command("export-pcboom")
def export_pcboom(
    run_id: str, version: str = typer.Option(..., help="Your separately installed PCBoom version")
) -> None:
    """Stage a normalized run summary for manual PCBoom work outside this repository."""

    manifest_path = Path("results") / run_id / "manifest.json"
    if not manifest_path.exists():
        typer.echo(f"Run manifest not found: {manifest_path}", err=True)
        raise typer.Exit(1)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    adapter = PCBoomAdapter(version=version, configuration={"source_run": run_id})
    target = adapter.export_case(manifest, Path("results") / run_id / "pcboom_staging")
    typer.echo(f"Staged normalized case at {target}; PCBoom was not bundled or invoked.")


@app.command("stage-su2")
def stage_su2(
    config: Path = typer.Option(..., exists=True, help="Reviewed SU2 configuration file."),
    mesh: Path = typer.Option(..., exists=True, help="Reviewed SU2 mesh file."),
    version: str = typer.Option(..., help="Installed SU2 version."),
    mach: float = typer.Option(..., min=1.0),
    altitude_m: float = typer.Option(..., min=1.0),
    reference_distance_m: float = typer.Option(..., min=0.001),
    azimuth_deg: float = typer.Option(0.0, min=0.0, max=359.999),
    output: Path = typer.Option(Path("data/staging/su2")),
) -> None:
    """Stage and checksum a reproducible SU2 near-field CFD case without running it."""

    adapter = SU2NearFieldAdapter(version=version)
    manifest = adapter.stage_case(
        config_path=config,
        mesh_path=mesh,
        staging_directory=output,
        operating_point={
            "mach": mach,
            "altitude_m": altitude_m,
            "reference_distance_m": reference_distance_m,
            "azimuth_deg": azimuth_deg,
        },
    )
    typer.echo(f"Staged SU2 case at {manifest}")
    if adapter.installed_executable is None:
        typer.echo("SU2_CFD was not found on PATH; no solver was run.")
    else:
        typer.echo(f"Recorded SU2 executable: {adapter.installed_executable}; no solver was run.")


@app.command()
def report(run_id: str) -> None:
    """Print the HTML report path for a completed run."""

    target = Path("results") / run_id / "report.html"
    if not target.exists():
        typer.echo(f"Report not found: {target}", err=True)
        raise typer.Exit(1)
    typer.echo(target.resolve())


@app.command()
def ui() -> None:
    """Launch the Streamlit synthetic demonstration."""

    command = [sys.executable, "-m", "streamlit", "run", "src/open_mco/ui/app.py"]
    raise typer.Exit(subprocess.call(command))


if __name__ == "__main__":
    app()
