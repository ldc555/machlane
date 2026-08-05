# MachLane

MachLane is an open-source research workspace for assembling the real route, atmosphere, terrain,
aircraft, propagation, and evidence inputs needed for future Mach-cutoff operational assurance.

> **Research prototype — not FAA approved.** MachLane does not currently calculate a ground
> waveform, surface overpressure, sonic-boom footprint, or compliant operating corridor.

## What the production workspace does now

The Streamlit workspace is real-data-only:

1. Load a real timestamped OpenSky observed track.
2. Retain every OpenSky observation timestamp and the complete route polyline.
3. Match archived NOAA atmosphere to the aircraft's changing position and UTC time.
4. Use HRRR only when the complete route is inside MachLane's conservative reviewed CONUS
   envelope; otherwise use the GEFS global grid.
5. Request a sparse USGS 3DEP elevation/availability preview automatically where U.S. land
   coverage may exist.
6. Form versioned, automatic atmospheric regions while retaining exact route geometry.
7. Export route, weather, terrain, region-boundary, timestamp, source, and checksum evidence.
8. Stop before sonic-boom propagation when physical inputs are unavailable.

There is no production Mock toggle, atmosphere-source selector, or segmentation-tolerance
selector. Synthetic providers and the mock propagation engine remain test fixtures only.

## What each source supplies

| Source | MachLane uses it for | It does not prove |
|---|---|---|
| [OpenSky REST API](https://openskynetwork.github.io/opensky-api/rest.html) | Recent observed track geometry, UTC timestamps, barometric altitude, track angle, flight identity, and provenance | Filed route, future supersonic approval, weather, Mach, or boom compliance |
| [NOAA HRRR](https://rapidrefresh.noaa.gov/hrrr/) via [Herbie](https://github.com/blaylockbk/Herbie) | Archived hourly pressure-level atmosphere for routes fully inside the reviewed v1 CONUS envelope | Global coverage or sonic-boom overpressure |
| [NOAA GEFS](https://www.emc.ncep.noaa.gov/emc/pages/numerical_forecast_systems/gefs.php) via Herbie | Archived global pressure-level atmosphere; control member is currently used in the workspace | Validated uncertainty or compliance |
| [ERA5](https://cds.climate.copernicus.eu/datasets/reanalysis-era5-pressure-levels) | Credential-gated historical replay, back-testing, and validation | Live prediction |
| [USGS 3DEP](https://www.usgs.gov/3d-elevation-program) | A cached three-point-per-region elevation/availability preview through the official point service | A propagation-grade terrain cross-section, global terrain, ocean bathymetry, or acoustic propagation |
| NASA STCA workbook | Reviewed aircraft identity, geometry, limits, performance points, and source references when populated | A near-field pressure signature unless one is supplied and reviewed |

OpenSky, NOAA, ERA5, and 3DEP are accessed through narrow adapters. Their repositories are not
copied into MachLane.

## Four-dimensional NOAA matching

Historical analysis does not use a single flight-midpoint atmosphere.

MachLane constructs route samples at no more than 15 statute miles apart. Every sample time is
interpolated through the real OpenSky observation timeline by traveled distance. Samples are then
batched by the model data actually needed:

- HRRR: preceding whole-hour archived analysis;
- GEFS: preceding three-hour archived output on its six-hour cycle.

Every normalized profile retains model, cycle, forecast lead, member, model-valid time,
interpolation, source URL, retrieval time, and available checksums. The exact OpenSky observation
times remain separate from the model-valid times, so the evidence shows the temporal match rather
than implying they are identical.

## Automatic atmospheric regions

The only production policy is `automatic-atmospheric-regions-v1`:

- maximum sampling interval: 15 statute miles;
- new region after a temperature change greater than 1 °F;
- new region after an ambient-pressure change greater than 0.02 inHg;
- new region after a wind-vector change greater than 3 knots;
- mandatory boundary at a weather-provider, cycle, or model-valid-time change;
- exact observed polyline retained; no endpoint-chord replacement.

Each region records the variable and change that caused its boundary. These thresholds group
atmospheric inputs. They are not FAA limits, sonic-boom margins, or evidence that an area is safe.

Once a physical propagation engine exists, this policy should be superseded by adaptive refinement
until additional sampling changes predicted surface overpressure by less than a reviewed numerical
tolerance. Additional refinement will be required near ray turning points, terrain interaction,
model boundaries, and uncertainty-sensitive locations.

## The three route concepts

MachLane keeps these separate:

1. **Historical route** — a real OpenSky observed trajectory.
2. **Planned route** — a future operator proposal with departure time and a reviewed aircraft phase
   schedule.
3. **Compliant operating corridor** — an uncertainty-bounded output from a validated physical
   propagation engine.

The current UI loads the first. The second fails closed while the aircraft workbook lacks a reviewed
climb, acceleration, cruise, and descent schedule. The third displays `NOT CALCULATED`.

Atmospheric regions are never called a compliant corridor. Blue/red route coloring represents
ambient pressure at the research reference altitude, not surface boom overpressure.

## Why MachLane cannot calculate the boom yet

Weather and terrain provide the atmosphere and ground boundary. A defensible sonic-boom result also
requires:

- a reviewed aircraft near-field pressure signature for the requested operating point;
- nonlinear propagation, absorption, molecular relaxation, and geometric spreading;
- primary, secondary-direct, and secondary-indirect ray handling;
- terrain intersection, ground reflection, and receiver modeling;
- ground waveform and peak-overpressure metrics;
- uncertainty analysis;
- comparison with PCBoom and ultimately flight measurements.

The workspace's sparse 3DEP preview keeps the UI responsive and verifies real U.S. elevation
availability. Before propagation, it must be replaced by a reviewed high-resolution terrain and
receiver grid across the acoustic footprint.

The repository contains typed boundaries, readiness checks, a near-field CSV format, SU2 staging,
and a PCBoom exchange adapter. It does not bundle PCBoom or silently run an unvalidated solver.

## Install

Python 3.11 or newer is required.

```bash
cd /Users/lucadecaneva/Desktop/machlane
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[full,gis,dev]"
```

On macOS, GRIB/GIS packages are usually most reliable through the supplied Conda environment:

```bash
conda env create -f environment.yml
conda activate machlane
python -m pip install -e ".[full,gis,dev]"
```

## Configure OpenSky once

Each user must use their own OpenSky API client. Never commit or publish a shared client secret.

For the current Terminal session:

```bash
export OPENSKY_CLIENT_ID="your-client-id"
export OPENSKY_CLIENT_SECRET="your-client-secret"
```

For persistent local Streamlit use, create `.streamlit/secrets.toml`:

```toml
OPENSKY_CLIENT_ID = "your-client-id"
OPENSKY_CLIENT_SECRET = "your-client-secret"
```

That file is git-ignored. NOAA and USGS do not require API keys. ERA5 requires a Copernicus account,
accepted dataset terms, and a local `~/.cdsapirc` file.

## Run

```bash
cd /Users/lucadecaneva/Desktop/machlane
source .venv/bin/activate
machlane ui
```

Open [http://localhost:8501](http://localhost:8501). If port 8501 is busy:

```bash
streamlit run src/open_mco/ui/app.py --server.address localhost --server.port 8502
```

The first route-time analysis may download several NOAA pressure-level subsets. Normalized OpenSky,
NOAA, and 3DEP caches are private, timestamped in evidence, and git-ignored.

## Other commands

```bash
machlane fetch-route --mission-id dfw_jfk --date 2026-08-03T00:00:00+0000
machlane fetch-weather --provider hrrr --latitude 39 --longitude -98 \
  --valid-time 2026-08-03T18:00:00+0000
machlane fetch-terrain --start-latitude 39 --start-longitude -98 \
  --end-latitude 39 --end-longitude -97.9
machlane validate-aircraft aircraft_database/NASA_STCA_55T/NASA_STCA_55T_Aircraft.xlsx
machlane boom-readiness aircraft_database/NASA_STCA_55T/NASA_STCA_55T_Aircraft.xlsx
```

Import checks such as `python -c "from open_mco.atmosphere import ..."` are developer diagnostics,
not a normal startup step.

## Development checks

```bash
ruff check src tests
mypy src
pytest
```

Network access is absent from automated tests. Set `MACHLANE_NETWORK_DISABLED=1` to force every live
adapter to reject network calls during offline development.

## Repository layout

```text
src/open_mco/       installable package
tests/              network-free automated tests
aircraft_database/  reviewed workbook interfaces
configs/            explicit source and research settings
docs/               architecture, physics, validation, and limitations
data/               private downloaded/normalized data; ignored
results/            generated evidence; ignored
```

See [docs/SONIC_BOOM_PIPELINE.md](docs/SONIC_BOOM_PIPELINE.md),
[docs/validation_plan.md](docs/validation_plan.md), and
[docs/assumptions_and_limitations.md](docs/assumptions_and_limitations.md) for the physics and
validation boundary.
