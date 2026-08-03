"""MachLane command-line interface."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import typer
import yaml

from open_mco.aircraft import AircraftWorkbookError, load_aircraft_workbook
from open_mco.demo import run_demo
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
def fetch_weather(provider: str = typer.Option(..., help="hrrr, gefs, or era5")) -> None:
    """Explain the explicit setup needed for real weather access."""

    normalized = provider.lower()
    if normalized not in {"hrrr", "gefs", "era5"}:
        raise typer.BadParameter("provider must be hrrr, gefs, or era5")
    if normalized == "era5":
        message = (
            "Configure ~/.cdsapirc for Copernicus CDS, then enable the ERA5 adapter explicitly."
        )
    else:
        message = f"Install MachLane's full extra and enable network access for the Herbie {normalized.upper()} adapter."
    typer.echo(f"No data downloaded. {message}")
    raise typer.Exit(2)


@app.command("fetch-terrain")
def fetch_terrain() -> None:
    """Explain the explicit setup needed for USGS terrain access."""

    typer.echo(
        "No data downloaded. Install MachLane's full extra and explicitly enable the py3dep USGS adapter."
    )
    raise typer.Exit(2)


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
