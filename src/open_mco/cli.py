"""MachLane command-line interface."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import typer
import yaml

from open_mco.aircraft import AircraftWorkbookError, load_aircraft_workbook
from open_mco.atmosphere import AtmosphereProvider, HerbieGEFSProvider, HerbieHRRRProvider
from open_mco.demo import run_demo
from open_mco.route import route_from_waypoints
from open_mco.terrain import USGS3DEPProvider
from open_mco.validation import PCBoomAdapter

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
    if normalized == "era5":
        typer.echo(
            "No data downloaded. ERA5 CDS retrieval is not implemented; configure ~/.cdsapirc "
            "and use a reviewed local export until that adapter is completed.",
            err=True,
        )
        raise typer.Exit(2)
    if normalized == "hrrr":
        weather: AtmosphereProvider = HerbieHRRRProvider(
            network_enabled=True, forecast_hour=forecast_hour
        )
    else:
        weather = HerbieGEFSProvider(
            network_enabled=True, forecast_hour=forecast_hour, member=member
        )
    profile = weather.profile(latitude, longitude, valid_time)
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
    terrain = USGS3DEPProvider(network_enabled=True, sample_spacing_m=sample_spacing_m).profile(
        route.segments[0]
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(terrain.model_dump_json(indent=2), encoding="utf-8")
    typer.echo(f"Wrote USGS 3DEP profile with {len(terrain.distance_m)} samples to {output}")


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
