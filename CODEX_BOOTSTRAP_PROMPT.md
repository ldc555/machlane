# Codex bootstrap prompt — Open MCO Compliance Engine

You are working inside a newly created private Git repository named `open-mco-compliance-engine`.

Build the initial repository for an open, modular Mach-cutoff planning and compliance-evidence research tool inspired by the workflow around NASA PCBoom, but streamlined for operational decision support.

## Mission

Create a reproducible vertical slice that answers this first engineering question:

> Can the software load one documented supersonic aircraft, combine it with a route and atmospheric profile, calculate segment-level allowable Mach limits through a replaceable propagation-engine interface, visualize the aircraft and corridor, and generate an auditable evidence package?

This is a research prototype. It is not FAA-approved software and must never claim regulatory compliance.

## Regulatory outcomes the architecture must support

The FAA NPRM “Enabling Supersonic Overland Flight,” Docket FAA-2026-6935, asks operators to demonstrate and operationally ensure that surface sonic-boom overpressure does not exceed 0.11 psf. The architecture must preserve evidence for:

1. the aircraft and configuration used;
2. the weather source, model cycle, valid time, variables, spatial/vertical interpolation, and downloaded-file checksums;
3. the propagation model name, version, assumptions, limitations, and configuration;
4. the means used to keep speed, altitude, and route within the calculated limits;
5. terrain data source, resolution, datum, and interpolation;
6. treatment of uncertainty and selected reliability level;
7. validation against a previously validated tool such as PCBoom or against flight-test measurements;
8. separate status for primary, secondary-direct, and secondary-indirect sonic boom;
9. an immutable run manifest and human-readable report.

The first version may address Mach-cutoff screening of the primary path only. It must label secondary direct/indirect boom and absolute 0.11 psf prediction as `NOT_IMPLEMENTED`, not silently treat them as satisfied.

## Technology choices

Use Python 3.11 and a `src/` package layout.

Use these dependencies through package managers; do not fork or vendor their repositories:

- numpy
- scipy
- pandas
- openpyxl
- pydantic
- pint
- xarray
- cfgrib
- eccodes
- herbie-data
- rasterio
- rioxarray
- pyproj
- shapely
- geopandas
- py3dep
- pyyaml
- typer
- streamlit
- pydeck
- plotly
- jinja2
- pytest
- pytest-cov
- hypothesis
- ruff
- mypy

Create both:

- `environment.yml` using conda-forge;
- `pyproject.toml` with project metadata, console scripts, linting, typing, and test configuration.

Do not download large NOAA, ERA5, or terrain datasets during repository creation.

## Required repository structure

Create:

```text
open-mco-compliance-engine/
├── .github/
│   ├── ISSUE_TEMPLATE/
│   └── workflows/ci.yml
├── aircraft_database/
│   └── NASA_STCA_55T/
│       ├── README.md
│       ├── references/.gitkeep
│       └── images/.gitkeep
├── configs/
│   ├── baseline.yml
│   └── data_sources.yml
├── data/
│   ├── examples/
│   ├── raw/.gitkeep
│   ├── processed/.gitkeep
│   └── cache/.gitkeep
├── docs/
│   ├── architecture.md
│   ├── data_dictionary.md
│   ├── faa_requirements_matrix.md
│   ├── assumptions_and_limitations.md
│   ├── validation_plan.md
│   └── decision_log.md
├── notebooks/
│   └── 01_vertical_slice_demo.ipynb
├── results/.gitkeep
├── src/open_mco/
│   ├── __init__.py
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
│   ├── cli.py
│   └── ui/app.py
├── tests/
├── .env.example
├── .gitignore
├── CONTRIBUTING.md
├── THIRD_PARTY_NOTICES.md
├── environment.yml
├── pyproject.toml
└── README.md
```

Do not create a software license yet. Add a note that licensing is pending agreement among project contributors.

## Core interfaces and models

Implement typed Pydantic models with units normalized internally to SI:

- `AircraftModel`
- `AircraftOperatingLimits`
- `AircraftPerformancePoint`
- `AtmosphericProfile`
- `AtmosphericSourceMetadata`
- `TerrainProfile`
- `TerrainSourceMetadata`
- `Route`
- `RouteSegment`
- `PropagationRequest`
- `PropagationResult`
- `SegmentLimit`
- `PlannerResult`
- `ComplianceStatus`
- `RunManifest`

All externally sourced values must preserve:

- original value;
- original unit;
- normalized SI value;
- source name;
- source document or URL;
- page/figure/table when applicable;
- retrieval timestamp;
- checksum when a local source file exists.

## Aircraft workbook

The expected workbook location is:

`aircraft_database/NASA_STCA_55T/NASA_STCA_55T_Aircraft.xlsx`

If it is absent, keep the folder and produce a clear CLI error explaining where to copy it.

Implement a resilient Excel loader for these sheets:

- `General`
- `Operating_Limits`
- `Performance_Map`
- `Aerodynamics_Optional`
- `Sonic_Boom_Optional`
- `Mission_Config`

Parameter sheets use:

`Parameter | Value | Unit | Required | Source | Page/Figure | Notes`

The performance map uses row-based grid data.

Validation rules:

- required values cannot be blank;
- unknown units fail with a useful error;
- performance rows must be complete or omitted;
- duplicate grid coordinates fail;
- source and page/figure warnings are emitted for populated engineering values without traceability;
- optional sheets may be empty;
- mission/regulatory constants are not treated as aircraft properties.

Add unit tests using a generated temporary workbook fixture.

## Replaceable propagation-engine architecture

Create a protocol or abstract base class:

```python
class BoomPropagationEngine(Protocol):
    name: str
    version: str

    def evaluate(self, request: PropagationRequest) -> PropagationResult: ...
```

Create three adapters:

1. `MockMCOEngine`
   - deterministic;
   - used only for software integration tests and the UI demo;
   - clearly labels results `SYNTHETIC_NOT_FOR_ENGINEERING_USE`;
   - never outputs a claim that 0.11 psf is satisfied.

2. `FastMCOEngine`
   - scaffold only;
   - contains documented extension points for effective sound speed, ray equations, terrain intersection, and cutoff boundary search;
   - any unimplemented physics raises `NotImplementedError`;
   - do not invent or silently approximate equations.

3. `PCBoomAdapter`
   - offline adapter scaffold;
   - exports normalized aircraft/trajectory/atmosphere cases to a staging directory;
   - imports result files when supplied;
   - does not bundle, call, or redistribute PCBoom;
   - documents that PCBoom requires a separate NASA software request.

## Weather adapters

Create an `AtmosphereProvider` interface and adapters:

- `HerbieHRRRProvider`
- `HerbieGEFSProvider`
- `ERA5Provider`
- `SyntheticAtmosphereProvider`

For the real adapters, implement configuration validation, metadata/provenance handling, and method signatures. Network calls may be behind explicit CLI commands and should not run in tests.

Normalize all providers to `AtmosphericProfile` containing at minimum:

- altitude or pressure level;
- temperature;
- pressure;
- zonal wind;
- meridional wind;
- optional humidity;
- latitude;
- longitude;
- valid time.

Add logic to project wind onto route bearing.

## Terrain adapter

Create a `TerrainProvider` interface and:

- `USGS3DEPProvider` using `py3dep`;
- `RasterTerrainProvider` for local GeoTIFF;
- `FlatTerrainProvider` for tests.

Preserve datum, resolution, source, retrieval time, and checksum.

## Route and corridor

Implement:

- route creation from an ordered list of latitude/longitude waypoints;
- geodesic segmentation at configurable spacing;
- bearing and distance for each segment;
- aircraft position interpolation along the route;
- corridor GeoJSON generation from segment results.

Do not infer legal airspace approval. The corridor represents only the modeled acoustic/operational envelope.

## Planner

Implement a transparent grid-search planner before any advanced optimizer.

Inputs:

- aircraft;
- route;
- atmosphere provider;
- terrain provider;
- propagation engine;
- candidate Mach values;
- candidate altitudes;
- reliability level.

For each route segment:

1. validate aircraft feasibility;
2. evaluate candidates through the selected propagation engine;
3. reject candidates outside aircraft limits;
4. reject candidates the engine marks unsafe or unknown;
5. select the fastest valid candidate;
6. preserve all rejected-candidate reasons.

For the mock engine, produce deterministic synthetic results so the complete software path works.

## Uncertainty

Create an ensemble runner that accepts multiple atmospheric profiles and calculates:

- candidate success rate;
- conservative allowable Mach percentile;
- worst member;
- nominal member;
- number of members;
- reliability threshold.

Do not equate a finite weather ensemble automatically with validated 95% regulatory reliability. Label it as an empirical scenario success rate and document the statistical limitation.

## Compliance evidence

Implement a requirements matrix with these statuses:

- `SUPPORTED`
- `PARTIAL`
- `NOT_IMPLEMENTED`
- `NOT_APPLICABLE`
- `VALIDATION_REQUIRED`

For each run, write:

```text
results/<run_id>/
├── manifest.json
├── segment_limits.csv
├── candidate_evaluations.parquet
├── corridor.geojson
├── report.html
└── figures/
```

The manifest must include:

- Git commit SHA;
- package version;
- configuration checksum;
- aircraft workbook checksum;
- weather source and cycle;
- terrain source and resolution;
- propagation engine and version;
- reliability setting;
- assumptions;
- limitations;
- UTC timestamps.

The report must distinguish:

- method-of-compliance evidence;
- operational means-of-compliance evidence;
- unsupported claims;
- validation status;
- primary boom status;
- secondary-direct status;
- secondary-indirect status.

## User interface

Create a Streamlit application that works with the synthetic demo and later real providers.

Display:

- selected aircraft;
- selected weather source;
- route map;
- aircraft marker with progress slider;
- modeled corridor;
- segment colors for pass/warning/fail/unknown;
- recommended Mach and altitude per segment;
- reliability/scenario success rate;
- provenance panel;
- compliance-evidence checklist;
- prominent banner: `RESEARCH PROTOTYPE — NOT FAA APPROVED`.

Use PyDeck for the map and Plotly for profiles. No proprietary basemap token may be required for the default demo.

## CLI

Implement Typer commands:

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

Commands that require missing credentials must fail with precise setup instructions.

## Documentation

Write concise but complete documentation.

`docs/faa_requirements_matrix.md` must map each relevant NPRM request to:

- evidence produced by this repository;
- current implementation status;
- validation needed;
- known gap.

Explicitly state that the first vertical slice does not yet demonstrate:

- absolute surface overpressure at or below 0.11 psf;
- secondary-direct boom compliance;
- secondary-indirect boom compliance;
- FAA approval;
- flight-test validation.

`docs/validation_plan.md` must define staged validation:

1. software/unit verification;
2. analytical or canonical physics cases;
3. comparison against published FaINT cases where reproducible;
4. PCBoom comparison matrix;
5. flight-test comparison when data become available.

## Example data

Create small tracked examples only:

- a synthetic aircraft workbook fixture generated by tests;
- a three-waypoint U.S. route;
- a synthetic pressure-level atmosphere;
- a flat and simple hill terrain profile;
- expected mock-engine outputs.

Do not commit real large weather files, proprietary aircraft data, NASA software, or generated results.

## CI and quality

Create GitHub Actions CI for:

- Python 3.11;
- install;
- Ruff;
- MyPy;
- Pytest with coverage;
- no network access in tests.

Aim for at least 85% coverage on implemented non-UI modules.

Use strict typing where practical. Keep functions small and documented.

## Git hygiene

Add `.gitignore` rules for:

- `.env`;
- credentials;
- CDS API configuration;
- raw weather/terrain data;
- GRIB, NetCDF, Zarr, GeoTIFF;
- PCBoom executables and outputs;
- results;
- caches;
- generated reports;
- Excel temporary files.

Never commit API keys, NASA software, large datasets, or proprietary aircraft data.

## README

Use the supplied project README requirements and include:

- mission;
- non-certification disclaimer;
- architecture;
- regulatory outcome matrix;
- setup;
- commands;
- source links;
- data provenance;
- roadmap;
- contribution rules.

## Execution sequence

Work in this order:

1. inspect the current repository;
2. create the structure and configuration;
3. implement data models;
4. implement aircraft loader and tests;
5. implement route and terrain test providers;
6. implement propagation-engine interfaces and mock engine;
7. implement planner and uncertainty runner;
8. implement evidence package;
9. implement CLI;
10. implement Streamlit UI;
11. write documentation;
12. run Ruff, MyPy, and Pytest;
13. fix failures;
14. summarize created files, commands run, tests, and remaining physics gaps.

Do not ask for confirmation unless a destructive action is required. Do not claim that the physics is validated. Prefer explicit `NotImplementedError` and documented gaps over invented engineering logic.
