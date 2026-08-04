# MachLane

**Open-source Mach-cutoff flight planning and sonic-boom compliance research.**

An open, modular research platform for planning **Mach-cutoff operations (MCO)** using aircraft performance data, atmospheric forecasts, terrain, uncertainty analysis, replaceable sonic-boom propagation engines, and auditable evidence generation.

> **Research prototype — not FAA approved.**  
> This repository does not by itself demonstrate compliance with 14 CFR § 91.817, does not replace flight testing, and must not be used as an operational flight-control authority until its physics, software, data pipeline, and operational procedures have been independently validated and approved.

## 1. Project question

The first milestone is deliberately narrow:

> **Can the software successfully optimize one well-documented supersonic aircraft along a route while preserving all data, assumptions, limits, and validation evidence needed to evaluate the result?**

The first reference aircraft is the **NASA 55-tonne Supersonic Technology Concept Aeroplane (STCA)**.

The project is not attempting to recreate all of PCBoom immediately. It is building the streamlined orchestration layer that PCBoom, public weather data, terrain data, aircraft data, optimization, visualization, and compliance reporting do not provide as one integrated workflow.

## 2. Why this exists

The FAA’s 2026 NPRM, **Enabling Supersonic Overland Flight**, proposes a performance-based framework in which:

- surface sonic-boom overpressure must not exceed **0.11 psf**;
- the operator must demonstrate this for **primary**, **secondary direct**, and **secondary indirect** sonic booms;
- the operator must have an operational means to keep the flight within the approved conditions;
- a Mach-cutoff proposal may need to identify the weather source, propagation model, and method used to keep airspeed below the maximum allowable value;
- terrain, actual weather, atmospheric uncertainty, and validation against a previously validated tool such as PCBoom or suitable flight testing must be addressed.

This project converts those needs into reproducible software outputs rather than treating compliance as a single yes/no calculation.

Official rulemaking:

- [FAA/DOT — Enabling Supersonic Overland Flight, FR Doc. 2026-13440](https://www.transportation.gov/regulations/federal-register-documents/2026-13440)
- Docket: `FAA-2026-6935`
- NPRM comment deadline: **August 17, 2026**

## 3. Product outcome

Given:

```text
aircraft + route + date/time + weather source + terrain + reliability + propagation engine
```

the platform is intended to produce:

```text
recommended Mach and altitude by route segment
modeled acoustic corridor
scenario/ensemble success rate
rejected-candidate reasons
weather and terrain provenance
model version and assumptions
validation status
FAA-oriented evidence package
```

The long-term user experience is a map showing the aircraft, route, dynamic MCO corridor, segment-level Mach limits, uncertainty, and the evidence supporting each decision.

## 4. Scope

### Initial scope

- NASA STCA 55T aircraft workbook ingestion;
- deterministic and ensemble atmospheric profiles;
- U.S. terrain profiles;
- route segmentation;
- replaceable propagation-engine interface;
- transparent grid-search planning;
- corridor visualization;
- complete run provenance;
- PCBoom export/import adapter;
- compliance-requirements matrix.

### Not yet demonstrated

The initial vertical slice does **not** demonstrate:

- absolute surface overpressure at or below 0.11 psf;
- secondary-direct sonic-boom compliance;
- secondary-indirect sonic-boom compliance;
- flight-test validation;
- FAA acceptance of the method or operational means of compliance;
- real-time airborne control;
- legal airspace or ATC authorization.

Unsupported items must appear as `NOT_IMPLEMENTED` or `VALIDATION_REQUIRED`, never as a pass.

## 5. Architecture

```text
Aircraft workbook ───────────────┐
                                 │
NOAA HRRR / GEFS ──┐             │
ERA5 historical ───┼─> adapters ─┼─> normalized domain models
USGS 3DEP ─────────┘             │
Route/waypoints ─────────────────┘
                                         │
                                         ▼
                              propagation-engine interface
                         ┌────────────┬────────────┬────────────┐
                         │ Mock engine│ Fast MCO   │ PCBoom     │
                         │ tests only │ open core  │ validation │
                         └────────────┴────────────┴────────────┘
                                         │
                                         ▼
                              uncertainty + grid search
                                         │
                                         ▼
                            segment limits + MCO corridor
                                         │
                         ┌───────────────┴────────────────┐
                         ▼                                ▼
                  Streamlit/PyDeck UI            evidence package
```

Every external provider is converted into a stable internal object. The planner must not care whether weather came from HRRR, GEFS, ERA5, or a future provider; similarly, it must be possible to replace the propagation engine without changing the aircraft, route, reporting, or user-interface layers.

## 6. FAA outcome matrix

| FAA-oriented outcome | Repository evidence | Initial status |
|---|---|---|
| Identify weather source | Provider, model cycle, forecast hour, valid time, variables, interpolation, checksums | Supported by architecture |
| Identify propagation model | Engine name/version/configuration, assumptions, limitations | Supported by architecture |
| Keep airspeed within MCO limit | Segment-level maximum and selected Mach, rejected-candidate reasons | Synthetic vertical slice first |
| Account for uncertainty | Ensemble/scenario runner, conservative percentile, worst member | Partial; statistical validation required |
| Account for terrain | 3DEP/local-raster profile, datum, resolution, interpolation | Supported by architecture |
| Validate application | PCBoom comparison harness and validation matrix | Validation required |
| Primary boom ≤ 0.11 psf | Requires validated propagation and source model | Not implemented in first slice |
| Secondary direct ≤ 0.11 psf | Requires upper-atmosphere secondary-ray modeling | Not implemented |
| Secondary indirect ≤ 0.11 psf | Requires ground reflection plus upper-atmosphere path | Not implemented |
| Document assumptions/results/limits | Run manifest, report, Git SHA, configuration and data checksums | Supported by architecture |
| Operational means of compliance | Exportable segment limits and corridor; future dispatch/FMS integration | Partial |

## 7. Open-source components

The project uses packages as dependencies. **Do not fork or copy their source code into this repository unless a deliberate upstream contribution is being made.**

### Weather access and processing

- [Herbie](https://github.com/blaylockbk/Herbie) — retrieves HRRR, GEFS, GFS, RAP, and other forecast products from NOAA and partner archives.
- [xarray](https://github.com/pydata/xarray) — labeled multidimensional arrays.
- [cfgrib](https://github.com/ecmwf/cfgrib) — GRIB-to-xarray interface using ecCodes.
- [ecCodes](https://github.com/ecmwf/eccodes) — GRIB/BUFR decoding.
- [ERA5 pressure-level data](https://cds.climate.copernicus.eu/datasets/reanalysis-era5-pressure-levels) — historical development and back-testing.

### Terrain and geospatial

- [OurAirports open data](https://ourairports.com/data/) — public-domain airport reference points used by the curated mission catalog.
- [USGS 3DEP](https://www.usgs.gov/3d-elevation-program) — U.S. terrain source.
- [USGS Elevation web services](https://www.usgs.gov/the-national-map-data-delivery/gis-data-download) — direct 3DEP route and point sampling without a GIS runtime.
- [Rasterio](https://github.com/rasterio/rasterio) — raster/GeoTIFF processing.
- [PyProj](https://github.com/pyproj4/pyproj) — coordinate transformations and geodesics.
- [Shapely](https://github.com/shapely/shapely) — geometry operations.
- [GeoPandas](https://github.com/geopandas/geopandas) — tabular geospatial processing.

### Numerics and data

- [NumPy](https://github.com/numpy/numpy)
- [SciPy](https://github.com/scipy/scipy)
- [Pandas](https://github.com/pandas-dev/pandas)
- [Pydantic](https://github.com/pydantic/pydantic)
- [Pint](https://github.com/hgrecco/pint)

### Visualization

- [Streamlit](https://github.com/streamlit/streamlit)
- [PyDeck / deck.gl](https://github.com/visgl/deck.gl)
- [Plotly](https://github.com/plotly/plotly.py)

### High-fidelity external validation

- [NASA PCBoom 7.5 Software Catalog entry](https://software.nasa.gov/software/LAR-19926-1)
- [PCBoom 7 Technical Reference, 2nd Edition](https://ntrs.nasa.gov/citations/20250003228)

PCBoom is not bundled or redistributed. Access requires a separate NASA software request and its applicable agreement. The repository contains only an adapter for exporting cases and importing user-supplied results.

### Sonic-boom calculation preparation

- [SU2](https://github.com/su2code/SU2) — open-source CFD candidate for producing a reviewed aircraft near-field pressure signature. MachLane stages and checksums a case; it does not silently run or validate the solver.
- [meshio](https://github.com/nschloe/meshio) — optional mesh-format inspection, including SU2 meshes. It checks interchange structure, not aerodynamic mesh adequacy.
- [MetPy](https://unidata.github.io/MetPy/latest/) — optional unit-aware derivation of moist-air density and water-vapor mixing ratio from normalized forecast profiles.

These components prepare inputs; they do **not** replace a nonlinear atmospheric propagation solver.
No open-source far-field engine has been accepted into the calculation path yet. The physical interface
requires near-field pressure, three-dimensional rays, nonlinearity, absorption/relaxation, spreading,
wind, terrain/ground interaction, primary and secondary paths, ground waveforms, and acoustical metrics.
See [docs/SONIC_BOOM_PIPELINE.md](docs/SONIC_BOOM_PIPELINE.md).

## 8. Where the data comes from

MachLane keeps each source responsible for one job. A route source does not become weather, ambient
pressure does not become sonic-boom overpressure, and an observed subsonic flight does not become a
validated future supersonic trajectory.

| Source | Data retrieved | Used for | Not evidence of |
|---|---|---|---|
| [OpenSky REST API](https://openskynetwork.github.io/opensky-api/rest.html) | Recent observed flights and downsampled tracks: time, WGS-84 position, barometric altitude, true track, ground/air state | The only route geometry accepted by the interactive workspace | Filed route, ATC clearance, weather, future SST corridor, Mach schedule, or boom compliance |
| [OurAirports](https://ourairports.com/data/) | Reviewed airport reference coordinates | Airport-pair lookup keys and endpoint labels | Published airway, fallback route, or operational route |
| [NOAA HRRR](https://rapidrefresh.noaa.gov/hrrr/) via [Herbie](https://github.com/blaylockbk/Herbie) | Regional forecast pressure levels, temperature, wind, and available humidity | Nominal atmosphere over the CONUS portion of a route | Global coverage or validated boom prediction |
| [NOAA GEFS](https://www.emc.ncep.noaa.gov/emc/pages/numerical_forecast_systems/gefs.php) via Herbie | Global ensemble forecast members | Weather uncertainty and oceanic/global atmosphere | Calibrated regulatory reliability by itself |
| [ERA5](https://cds.climate.copernicus.eu/datasets/reanalysis-era5-pressure-levels) | Global historical pressure-level reanalysis | Repeatable back-tests and validation cases | Live forecast |
| [USGS 3DEP](https://www.usgs.gov/3d-elevation-program) | U.S. land elevation samples | Terrain interaction and receiver elevation where covered | Global terrain or ocean bathymetry |
| NASA STCA workbook | Reviewed aircraft geometry, limits, mass and future performance tables when populated | Aircraft source model and operating envelope | Valid near-field pressure signature unless that data is supplied and reviewed |

### Route geometry: observed OpenSky tracks only

OpenSky's official API root is `https://opensky-network.org/api`. MachLane uses only the endpoints
needed for route context:

1. `GET /flights/departure` finds a recent departure from the selected origin and matching arrival
   airport.
2. `GET /tracks/all` retrieves that aircraft's experimental track.
3. The adapter validates the response, preserves the timestamped observations, normalizes the path,
   and records a checksum and limitations.

OpenSky now requires OAuth2 client credentials for these authenticated calls. Set
`OPENSKY_CLIENT_ID` and `OPENSKY_CLIENT_SECRET` in the environment; the adapter obtains and refreshes
the short-lived bearer token. Tracks older than 30 days are unavailable through this REST endpoint.
Requests are credit-limited, so the UI searches newest-to-oldest over a bounded seven-day window,
fetches once per airport-pair/date selection, and writes a normalized private cache under
`data/cache/opensky_routes/`. That directory, credentials, and fetched tracks are git-ignored and
never committed.

Observed paths vary with weather, ATC, traffic, runway configuration and day-to-day operations. A
selected airport pair is only a search definition. If OpenSky has no matching direct observed track,
the workspace stops; it does not draw a shortest-path or silently substitute another route. The
current candidate set covers DFW–JFK, DFW–LAX and LAX–JFK overland tests, BOS–HNL oceanic U.S. travel,
DFW–SJU and JFK–SJU U.S.-territory travel, and JFK–LHR transatlantic travel. DEN–NRT is deliberately
excluded because no observed route was available for the requested test. LAX–NRT provides the
U.S.–Japan search instead, and still fails closed if OpenSky has no observation in the selected
lookback window.

After a track loads, pressure, temperature and wind split its geometry into variable-length
atmospheric regimes. The current mock segmentation starts a new regime after a 1 hPa flight-level
pressure change, a 0.7 K temperature change, or a 2.5 m/s wind-vector change. Those thresholds are
test fixtures, not validated operational margins. The segmentation receives atmosphere through a
provider interface, so the same observed geometry can later use HRRR over CONUS and GEFS for global
and oceanic coverage without changing the route or planner contracts. Each regime retains the full
OpenSky polyline and along-track distance; it is never replaced by a direct chord between weather
boundaries.

The map's blue-to-red centerline shows **ambient pressure at the planner altitude**. It does not show
a sonic-boom footprint. Atmospheric regimes use a thin colored line on the retained OpenSky
polyline; no filled corridor is drawn. Independent map toggles show or hide the yellow observed
track and atmospheric segments. Surface boom colors must wait for a propagation engine that
produces receiver waveforms and ground overpressure.

### Atmosphere

- **HRRR** is the preferred nominal forecast inside the conterminous United States.
- **GEFS** supplies global coverage and ensemble uncertainty for U.S. territories, oceanic routes,
  and international missions.
- **ERA5** supplies repeatable historical atmosphere for back-testing, not live prediction.

The normalized atmospheric profile contains altitude, pressure, temperature, zonal wind, meridional
wind, and humidity when available. Forecast cycle, valid time, member, request, source URL, and local
checksum travel with the data. Live adapters are opt-in; the default UI remains visibly mock until a
real forecast run is connected to the planner.

### Terrain

USGS 3DEP is used only where it has U.S. land coverage. Every terrain profile retains the service,
datum, resolution, interpolation method, retrieval time, and checksum when a local artifact exists.
International terrain and ocean-floor data require a separate reviewed global source.

### Aircraft speed and flight phase

OpenSky tracks describe real conventional flights but do not provide a validated performance model
for the future STCA aircraft. The mock UI therefore uses an explicitly synthetic phase schedule:
subsonic takeoff and climb, transonic acceleration after reaching the cruise corridor, supersonic
cruise, deceleration, subsonic descent, and landing. The transition location is a UI fixture—not a
claim that an SST always accelerates at that percentage of a route.

[OpenAP](https://openap.dev/openap.html) is a strong open-source option for conventional aircraft
phase identification, thrust, drag, fuel and trajectory studies. It can be added as a comparison
adapter, but it must not substitute for reviewed STCA-specific climb, acceleration, cruise, descent,
engine and mass data.

Coriolis is not an extra distance correction. WGS-84 geodesics determine geometric distance; Earth
rotation enters through the atmospheric model and winds. Fuel and trip time remain `NOT_MODELED`
until the aircraft performance model is integrated segment-by-segment with those winds.

### Adapter readiness

| Source | Current status | Boundary |
|---|---|---|
| OpenSky | Implemented: OAuth2, token refresh, seven-day airport-pair lookup, track normalization, private local cache, provenance and rate-limit errors | Recent experimental observations only; no weather fields |
| HRRR | Fetch and normalization implemented | Not yet wired into the validated planner |
| GEFS | Member-aware fetch and normalization implemented | Ensemble interpretation still requires validation |
| ERA5 | Credential-gated fetch implemented | Historical only |
| USGS 3DEP | Route-profile fetch implemented | U.S. land coverage only |
| Sonic-boom propagation | Interface and validation boundaries implemented | Physical engine not selected or validated |

Data ingestion is not a compliance result. Until the final row is complete, the UI must continue to
say `NOT_MODELED` for sonic-boom footprint and ground overpressure.

## 9. Aircraft database

Expected structure:

```text
aircraft_database/
└── NASA_STCA_55T/
    ├── NASA_STCA_55T_Aircraft.xlsx
    ├── README.md
    ├── references/
    └── images/
```

The workbook is the stable aircraft-data contract.

Parameter sheets use:

```text
Parameter | Value | Unit | Required | Source | Page/Figure | Notes
```

The performance map uses complete row-based operating points. Partial rows are invalid.

The loader converts values to SI internally but preserves the original value, unit, and source traceability.

## 10. Repository layout

```text
.
├── aircraft_database/
├── configs/
├── data/
│   ├── examples/
│   ├── raw/
│   ├── processed/
│   └── cache/
├── docs/
├── notebooks/
├── results/
├── src/open_mco/
│   ├── aircraft/
│   ├── atmosphere/
│   ├── terrain/
│   ├── route/
│   ├── physics/
│   ├── uncertainty/
│   ├── optimization/
│   ├── compliance/
│   ├── validation/
│   ├── reporting/
│   └── ui/
└── tests/
```

Large source data and generated runs are ignored by Git.

## 11. Setup

### Prerequisites

- Git;
- GitHub account;
- Miniforge, Mambaforge, or another conda-forge-compatible environment manager;
- Python 3.11 environment created from this repository;
- Codex optional for assisted development.

### Create the environment

```bash
conda env create -f environment.yml
conda activate open-mco
python -m cfgrib selfcheck
```

### Install the package

```bash
pip install -e ".[ui]"
```

Install the real weather and terrain adapters only when they are needed:

```bash
pip install -e ".[full]"
```

Install only the optional calculation-preparation tools:

```bash
pip install -e ".[physics]"
```

Local GeoTIFF/raster workflows are deliberately separate from live 3DEP access:

```bash
pip install -e ".[gis]"
```

### Validate the aircraft workbook

```bash
open-mco validate-aircraft aircraft_database/NASA_STCA_55T/NASA_STCA_55T_Aircraft.xlsx
```

Audit the full physical-calculation path without making any network or solver call:

```bash
open-mco boom-readiness aircraft_database/NASA_STCA_55T/NASA_STCA_55T_Aircraft.xlsx
```

Stage a reviewed SU2 configuration and mesh for an explicit expert-run CFD case:

```bash
open-mco stage-su2 \
  --config path/to/case.cfg --mesh path/to/mesh.su2 --version 8.5.0 \
  --mach 1.4 --altitude-m 15000 --reference-distance-m 100 \
  --output data/staging/su2
```

### Run the synthetic vertical slice

```bash
open-mco demo
```

### Open the user interface

The map-first workspace keeps only the observed route, phase-aware mock aircraft state, mock
atmospheric corridor, and calculation readiness in the primary view. Aviation distances are
displayed primarily in nautical miles. Moving the aircraft updates its phase and local atmosphere
without recomputing the cached route plan. The phase fixture keeps takeoff, climb, and landing
subsonic; acceleration to the mock supersonic cruise occurs only after climb, with deceleration and
descent before arrival.

To make OpenSky the route source, create an API client on the OpenSky account page and export the two
values in the same Terminal session before starting Streamlit:

```bash
cd /path/to/machlane
source .venv/bin/activate
export OPENSKY_CLIENT_ID="paste-your-client-id-here"
export OPENSKY_CLIENT_SECRET="paste-your-client-secret-here"
streamlit run src/open_mco/ui/app.py --server.address localhost --server.port 8501
```

Do not paste the secret into `.env.example`, source code, Git, screenshots, issues, or chat. The UI
automatically searches the prior seven days for the selected airport pair, keeps the result in the
current Streamlit session, and reuses its private local cache after restart. Use **Refresh observed
route** only when another request is needed. If no track exists, choose another pair or end date; the
planner remains paused.

```bash
open-mco ui
```

or:

```bash
streamlit run src/open_mco/ui/app.py
```

### Fetch reviewed real-data inputs

Weather and terrain retrieval are separate explicit steps. OpenSky supplies route observations only;
it does not supply weather. The UI segments a loaded observed route with clearly labeled mock
atmosphere today, and the injected atmosphere-provider boundary is ready for HRRR/GEFS wiring. None
of this changes the `NOT_MODELED` boom status.

```bash
open-mco fetch-weather \
  --provider hrrr \
  --latitude 39.8561 \
  --longitude -104.6737 \
  --valid-time 2026-08-03T12:00:00+0000 \
  --forecast-hour 0 \
  --output data/processed/hrrr_denver.json

open-mco fetch-weather \
  --provider gefs \
  --latitude 39.8561 \
  --longitude -104.6737 \
  --valid-time 2026-08-03T12:00:00+0000 \
  --forecast-hour 0 \
  --member 1 \
  --output data/processed/gefs_p01_denver.json

open-mco fetch-weather \
  --provider era5 \
  --latitude 39.8561 \
  --longitude -104.6737 \
  --valid-time 2024-03-28T00:00:00+0000 \
  --output data/processed/era5_denver.json

open-mco fetch-terrain \
  --start-latitude 39.8561 --start-longitude -104.6737 \
  --end-latitude 38.7487 --end-longitude -90.3700 \
  --sample-spacing-m 1000 \
  --output data/processed/denver_st_louis_terrain.json

open-mco fetch-route \
  --mission-id dfw_jfk \
  --date 2026-08-03 \
  --output data/processed/opensky_dfw_jfk.json
```

Each weather command downloads only the requested GRIB pressure-level fields, not a whole model
file. Large source files and generated profiles stay out of Git. HRRR, GEFS, ERA5, and 3DEP are
fetch paths; they are not yet wired into an end-to-end validated planner run. ERA5 requires an
accepted CDS dataset licence and the user's own `~/.cdsapirc`; credentials are never stored here.

### Run quality checks

```bash
ruff check .
mypy src
pytest --cov=open_mco --cov-report=term-missing
```

## 12. Configuration

`configs/baseline.yml` defines:

```yaml
aircraft:
  path: aircraft_database/NASA_STCA_55T/NASA_STCA_55T_Aircraft.xlsx

route:
  waypoint_file: data/examples/route.csv
  segment_spacing_km: 100

weather:
  provider: synthetic
  reliability: 0.95

terrain:
  provider: flat

propagation:
  engine: mock

planner:
  mach_values: [1.02, 1.05, 1.08, 1.10, 1.12, 1.15]
  altitude_ft: [40000, 42000, 44000, 46000, 48000, 50000]
```

The UI defaults to the DFW–JFK OpenSky airport-pair search and lets the user switch among the reviewed
candidate pairs. It will not run the planner or map without a cached or freshly fetched observed
track. The baseline configuration's CSV remains available to CLI-only synthetic tests and explicit
developer experiments; it is not a UI fallback.

## 13. CLI

```text
open-mco validate-aircraft PATH
open-mco boom-readiness PATH [--near-field SIGNATURE.csv]
open-mco stage-su2 --config CASE.cfg --mesh MESH.su2 ...
open-mco demo
open-mco plan --config configs/baseline.yml
open-mco fetch-weather --provider hrrr|gefs|era5 ...
open-mco fetch-terrain ...
open-mco fetch-route --mission-id lax_jfk --date YYYY-MM-DD --lookback-days 7
open-mco export-pcboom RUN_ID
open-mco report RUN_ID
open-mco ui
```

Network and credential-dependent commands must fail with explicit setup guidance rather than partial or silent output.

## 14. Run evidence package

Each planning run creates:

```text
results/<run_id>/
├── manifest.json
├── route.json
├── segment_limits.csv
├── candidate_evaluations.parquet
├── corridor.geojson
├── report.html
└── figures/
```

The run manifest records:

- Git commit SHA;
- package version;
- aircraft file checksum;
- configuration checksum;
- source-data checksums;
- normalized route geometry and OpenSky provenance when an observed track is used;
- weather source/cycle/valid time;
- terrain source/resolution/datum;
- propagation engine/version;
- reliability setting;
- assumptions and limitations;
- UTC execution timestamps.

A result without a complete manifest is not considered reproducible.

## 15. Propagation engines

### MockMCOEngine

Purpose: software integration, tests, and UI development.

It produces deterministic synthetic classifications and is labeled:

```text
SYNTHETIC_NOT_FOR_ENGINEERING_USE
```

### FastMCOEngine

Purpose: open, fast planning engine.

Development requires documented and reviewed implementation of:

- effective sound speed;
- atmospheric interpolation;
- three-dimensional ray equations;
- terrain intersection;
- cutoff-boundary search;
- caustic and near-cutoff behavior;
- model limitations.

No equation may be invented by an automated coding agent. Physics changes require a cited source, test case, and reviewer.

The separate `SonicBoomPropagationEngine` protocol is the fail-closed physical boundary. Unlike the
legacy synthetic planner fixture, it accepts a complete `SonicBoomCase` containing the aircraft,
operating point, near-field pressure signature, atmospheric column, terrain, boom limit, and an
explicit list of physical effects. It must return receiver waveforms and ground metrics, not a score.

### PCBoomAdapter

Purpose: high-fidelity comparison.

It:

- exports normalized cases;
- records PCBoom version/configuration;
- imports supplied outputs;
- compares classifications and metrics;
- does not redistribute or automatically license PCBoom.

## 16. Validation plan

Validation is staged.

### Stage 1 — Software verification

- units;
- interpolation;
- coordinate transforms;
- route segmentation;
- data checksums;
- deterministic repeatability;
- invalid-input handling.

### Stage 2 — Canonical physics cases

- uniform atmosphere;
- controlled gradients;
- no-wind and known wind-projection cases;
- flat and analytic terrain;
- clearly sub-cutoff and super-cutoff cases.

### Stage 3 — Published research cases

Use reproducible information from:

- [NASA FaINT Mach-cutoff study](https://ntrs.nasa.gov/citations/20160007348);
- [ASCENT Project 042 — Acoustical Model of Mach Cut-off](https://ascent.aero/project/acoustical-model-of-mach-cut-off/).

### Stage 4 — PCBoom comparison

Run a declared matrix across:

- Mach;
- altitude;
- bearing;
- temperature profiles;
- wind profiles;
- terrain conditions;
- clearly safe, clearly unsafe, and marginal cases.

Compare at least:

- cutoff/no-cutoff classification;
- limiting Mach;
- ground-intersection location;
- ray path;
- false-safe rate.

### Stage 5 — Flight-test comparison

Where sufficiently documented data are available, compare predictions against measured cases. Flight-test validation and FAA approval are outside the initial repository milestone.

## 17. Development roadmap

### Milestone 0 — Repository and evidence architecture

- project scaffold;
- CI;
- domain models;
- requirements matrix;
- synthetic demo.

### Milestone 1 — NASA STCA vertical slice

- load STCA workbook;
- validate units and sources;
- route segmentation;
- synthetic atmosphere;
- mock propagation;
- corridor visualization;
- evidence report.

### Milestone 2 — Historical real-atmosphere cases

- ERA5 adapter;
- real temperature/wind profiles;
- documented deterministic fast engine;
- repeatable case library.

### Milestone 3 — U.S. planning data

- HRRR nominal forecasts;
- GEFS ensemble scenarios;
- 3DEP terrain;
- probabilistic segment limits.

### Milestone 4 — PCBoom comparison

- export/import adapter;
- validation matrix;
- error analysis;
- model margins.

### Milestone 5 — Expanded boom modeling

- near-field source definitions;
- absolute surface overpressure;
- primary waveform;
- secondary-direct paths;
- secondary-indirect paths;
- turbulence and absorption as justified.

### Milestone 6 — Operational concept

- dispatch monitoring;
- forecast updates;
- validity windows;
- alerts;
- flight-crew limitation export;
- change-control and safety assessment.

## 18. Security and data handling

Never commit:

- `.env`;
- API keys or tokens;
- CDS credentials;
- proprietary aircraft data;
- NASA-distributed executables;
- NASA software agreement material;
- large GRIB/NetCDF/Zarr/GeoTIFF files;
- unreviewed flight-test data;
- generated compliance reports containing sensitive information.

All external files must be checksummed before use in an evidence package.

## 19. Contribution rules

Every physics or compliance-related pull request must include:

1. the cited technical or regulatory basis;
2. units and coordinate conventions;
3. tests;
4. assumptions and limitations;
5. validation status;
6. documentation updates;
7. no unsupported claim of FAA compliance.

Automated coding tools may scaffold, refactor, test, and document, but a qualified human must review physical equations, numerical methods, regulatory interpretations, and validation conclusions.

## 20. Licensing

Repository licensing is pending agreement among project contributors.

Third-party libraries retain their own licenses. Public data retain their source terms and attribution requirements. NASA PCBoom is not part of this repository and is governed by the separate NASA software-access process.

## 21. Immediate definition of done

The first vertical slice is complete when a new contributor can:

1. clone the repository;
2. create the environment;
3. validate the NASA STCA workbook;
4. run a network-free synthetic demonstration;
5. see the aircraft route and modeled corridor;
6. inspect recommended segment Mach/altitude values;
7. open the evidence report;
8. identify exactly which FAA-oriented outcomes are supported, partial, unimplemented, or awaiting validation;
9. reproduce the same run from its manifest.
