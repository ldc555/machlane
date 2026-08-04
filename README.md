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

## 8. Data sources

### Routes and distance

The UI ships with a small catalog of conceptual future high-speed missions: DFW–JFK, DFW–LAX,
LAX–JFK, LAX–HNL, DFW–SJU, JFK–LHR, and SFO–HND. Endpoints are real airport reference points
retrieved from the public-domain [OurAirports data dump](https://ourairports.com/data/) on
2026-08-03. The points are committed as a reviewed subset so opening the UI never depends on a
network request or a changing upstream file.

Each concept uses the shortest path on the WGS-84 ellipsoid, calculated with `pyproj.Geod`, and is
split into roughly 200 km analysis segments. It is intentionally **not** described as a filed route,
published airway, daily oceanic track, ATC clearance, or approved supersonic corridor. Current U.S.
procedures can later be imported from the FAA's 28-day [CIFP product](https://www.faa.gov/air_traffic/flight_info/aeronav/digital_products/cifp/download/),
but present-day subsonic procedures should not be passed off as future hypersonic routing.

Coriolis is not an extra correction to geometric distance. Earth shape belongs in the WGS-84
geodesic; Earth rotation and atmospheric dynamics belong in the forecast model and its wind field.
Fuel and trip time therefore remain **NOT MODELED** until reviewed aircraft performance data can be
integrated segment-by-segment with along-track HRRR or GEFS winds. Adding a percentage to distance
would look precise while double-counting or inventing physics.

### Operational U.S. forecast

**HRRR** is the initial nominal forecast source for the conterminous United States. The
`fetch-weather` command retrieves a pressure-level subset through Herbie only when explicitly
invoked, then writes a normalized profile with its model cycle, valid time, source URL, and local
GRIB checksum. The default demo never contacts the network.

Required upper-air variables include, at minimum:

- temperature;
- pressure or geopotential height;
- zonal wind;
- meridional wind;
- humidity when required by the propagation model.

### Forecast uncertainty

**GEFS** provides multiple forecast members. The same explicit fetch path accepts a declared
member and records it in the source request. The first planning implementation treats member
outcomes as an empirical scenario distribution.

GEFS is global, so it is the forecast baseline for Hawaii, Puerto Rico, and international or
oceanic missions. HRRR remains a higher-resolution regional supplement where the route is inside
its CONUS domain; the current adapter does not claim HRRR coverage for U.S. territories.

A high fraction of passing ensemble members must not automatically be described as validated 95% regulatory reliability. Ensemble calibration, dependence, model error, representativeness, and conservative margin selection require separate justification.

### Historical development

**ERA5** is used for repeatable historical cases and back-testing. The explicit CDS retrieval path
requires the contributor's own accepted dataset terms and `~/.cdsapirc` credentials; downloaded
GRIB files are cached and checksummed like NOAA inputs. ERA5 has global coverage, but it is
reanalysis rather than a live forecast.

### Terrain

Use **USGS 3DEP** on covered U.S. land, including available U.S.-territory products. `fetch-terrain`
samples one declared route leg through official
USGS cross-section and elevation-point services and serializes the normalized terrain profile.
It is not a global ocean-floor or international terrain source.

- product and resolution;
- horizontal datum;
- vertical datum;
- retrieval date;
- source tile or service;
- interpolation method;
- checksum when stored locally.

### Adapter readiness

| Source | Retrieval | Verification boundary |
|---|---|---|
| NOAA HRRR | Implemented through Herbie | Live historical pressure-profile fetch completed; not planner-validated |
| NOAA GEFS | Implemented through Herbie, including member identity | Live `p01` pressure-profile fetch completed; common native levels only |
| USGS 3DEP | Implemented through official USGS HTTPS services | Live route-profile fetch completed; no GIS installation required |
| ERA5 | Implemented through the CDS API | Credential-gated; test double verified because no CDS credentials are stored in the repository |
| NOAA RRFS/REFS | Not implemented | Track the [announced October 2026 production transition](https://www.weather.gov/notification/) |

These statuses describe data ingestion only. No source in this table turns the mock propagation
result into an operational or compliance determination.

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

Local GeoTIFF/raster workflows are deliberately separate from live 3DEP access:

```bash
pip install -e ".[gis]"
```

### Validate the aircraft workbook

```bash
open-mco validate-aircraft aircraft_database/NASA_STCA_55T/NASA_STCA_55T_Aircraft.xlsx
```

### Run the synthetic vertical slice

```bash
open-mco demo
```

### Open the user interface

The map-first workspace keeps the synthetic aircraft, route corridor, active-segment recommendation,
atmospheric profile, model explanation, validation state, provenance, and evidence generation in one
screen. Aviation distances are displayed primarily in nautical miles. Pacific missions are unwrapped
around the antimeridian for one continuous map path. Moving the aircraft updates the inspector without
recomputing the cached route plan.

```bash
open-mco ui
```

or:

```bash
streamlit run src/open_mco/ui/app.py
```

### Fetch reviewed real-data inputs

Real-data retrieval is a separate, explicit step. It never silently replaces the synthetic demo.

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

The UI defaults to the DFW–JFK concept and lets the user switch among the reviewed mission catalog.
The baseline configuration's CSV remains available for explicit custom waypoint experiments. Both
paths remain synthetic and network-free so contributors can run the full repository immediately.

## 13. CLI

```text
open-mco validate-aircraft PATH
open-mco demo
open-mco plan --config configs/baseline.yml
open-mco fetch-weather --provider hrrr|gefs|era5 ...
open-mco fetch-terrain ...
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
