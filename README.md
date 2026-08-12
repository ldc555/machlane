# MachLane

MachLane is an open-source research workspace that combines a real OpenSky flight track, real NOAA
weather, available USGS terrain, and an uploaded aircraft workbook to build a route-aligned
high-speed flight analysis.

> **Research prototype — not FAA approved.** MachLane calculates an explicitly unvalidated
> primary-ray surface-waveform estimate when a condition-matched near-field signature is available.
> It does **not** yet calculate secondary rays, bounded uncertainty, validated loudness, or a
> compliant operating corridor.

## What the analysis uses

| Source | What MachLane imports | What it does not provide |
|---|---|---|
| [OpenSky REST API](https://openskynetwork.github.io/opensky-api/rest.html) | Observed route geometry, timestamps, altitude observations, flight identity, and provenance | A filed future route, weather, or sonic-boom compliance |
| [NOAA HRRR](https://rapidrefresh.noaa.gov/hrrr/) via [Herbie](https://github.com/blaylockbk/Herbie) | Archived pressure-level atmosphere for routes entirely inside the reviewed CONUS coverage | Global coverage or surface boom overpressure |
| [NOAA GEFS](https://www.emc.ncep.noaa.gov/emc/pages/numerical_forecast_systems/gefs.php) via Herbie | Archived pressure-level atmosphere for routes requiring global or oceanic coverage | A validated compliance or uncertainty result |
| [USGS 3DEP](https://www.usgs.gov/3d-elevation-program) | Available U.S. terrain previews from the official elevation service | Propagation-grade global terrain or acoustic propagation |
| [Aircraft workbook](#aircraft-files-download-drag-and-run) | Populated phase, Mach, altitude, geometry, performance, fuel, and acoustic inputs | Missing engineering values, a near-field signature, or certified limits |

MachLane keeps the OpenSky historical route, the proposed high-speed flight, and a future compliant
operating corridor as separate concepts. Atmospheric regions are not labeled as boom-safe areas.

## Quick start

### 1. Install the prerequisites

You need:

- Git;
- Python 3.11 or newer;
- a free [OpenSky account](https://opensky-network.org/).

### 2. Clone and install MachLane

Open Terminal and run:

```bash
git clone https://github.com/ldc555/machlane.git
cd machlane
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[full,gis]"
```

The first installation can take several minutes because it includes NOAA GRIB and GIS libraries.

If the GRIB/GIS installation fails, use the supplied Conda environment instead:

```bash
conda env create -f environment.yml
conda activate machlane
```

If you use Conda, replace `source .venv/bin/activate` in the later commands with
`conda activate machlane`.

### 3. Configure OpenSky once

Each user must use their own OpenSky API client. Never commit or publish a shared client secret.
Go to the [OpenSky Account page](https://opensky-network.org/my-opensky/account), open
**API Client Interface**, and generate the two required keys: `client_id` and `client_secret`.

OpenSky now uses OAuth2 API clients; website username/password authentication is not accepted by
the REST API.

1. Sign in on the [OpenSky Account page](https://opensky-network.org/my-opensky/account).
2. Find **API Client Interface**.
3. Generate or activate a new API client.
4. Copy its `client_id` and `client_secret`. The client ID normally ends in `-api-client`.

See the [official OpenSky authentication documentation](https://openskynetwork.github.io/opensky-api/rest.html#authentication).

### 4. Save the keys and start MachLane

On macOS using the default zsh Terminal, replace `/path/to/your/machlane` with the location of your
cloned repository, then paste this complete block:

```bash
cd /path/to/your/machlane
source .venv/bin/activate
mkdir -p .streamlit
read "OPENSKY_CLIENT_ID?OpenSky client ID: "
read -s "OPENSKY_CLIENT_SECRET?OpenSky client secret: "
echo
printf 'OPENSKY_CLIENT_ID = "%s"\nOPENSKY_CLIENT_SECRET = "%s"\n' "$OPENSKY_CLIENT_ID" "$OPENSKY_CLIENT_SECRET" > .streamlit/secrets.toml
chmod 600 .streamlit/secrets.toml
streamlit run src/open_mco/ui/app.py --server.address localhost --server.port 8501
```

Paste the ID and secret **without adding quotes** when Terminal asks for them. The secret remains
hidden while you type. The credentials are saved locally, so this setup is required only once.
The generated file is git-ignored; never commit or share it.

NOAA HRRR/GEFS and USGS 3DEP do not require keys. ERA5 is optional and is not required to start the
current workspace.

### 5. Open the website

Keep that Terminal window running and visit [http://localhost:8501](http://localhost:8501).

On macOS, this opens Safari directly:

```bash
open -a Safari http://localhost:8501
```

If Terminal reports that port 8501 is already in use, stop the previous server with `Control-C`,
or run:

```bash
streamlit run src/open_mco/ui/app.py --server.address localhost --server.port 8502
```

Then visit [http://localhost:8502](http://localhost:8502).

## Aircraft files: download, drag, and run

Use these aircraft workbooks:

- [NASA ST55 aircraft workbook](https://docs.google.com/spreadsheets/d/1p1tlfufxCVTymZm1oD5O2wBbNvwS_uMJ/edit?usp=share_link&ouid=103289146496595000341&rtpof=true&sd=true) — **use this now**.
- [Boom XB-1 aircraft workbook](https://docs.google.com/spreadsheets/d/17kS5a2LK2SAAHgEADfnvreDiwKyPZRlu/edit?usp=share_link&ouid=103289146496595000341&rtpof=true&sd=true) — **use only when its missing aircraft and phase data are completed**.

Follow these steps:

1. Open the NASA ST55 link above.
2. In Google Sheets, select **File → Download → Microsoft Excel (.xlsx)**.
3. Return to [MachLane](http://localhost:8501).
4. Click **Load aircraft**.
5. Drag the downloaded `.xlsx` file into **Drop aircraft Excel here**.
6. Wait for validation, then confirm that the NASA ST55 fields and 10 flight phases appear.
7. Click **Save aircraft & open routes**.
8. Select **DFW → JFK** or **LAX → JFK** under **Mission**.
9. Select an **Observed OpenSky flight**. The list contains only real matching departures, shown
   with their callsign and actual UTC departure time; MachLane never searches backward from an
   arbitrary date or substitutes a different flight.
10. Click **Run analysis**.
11. Let the analysis finish without closing the Terminal or browser. MachLane loads the real
    OpenSky track, matches NOAA weather across the flight, checks available 3DEP terrain, and forms
    automatic atmospheric regions. With the LM1021 workbook it also runs the open primary-ray
    research solver over propagation-eligible regions.
12. Use the aircraft-position slider to inspect phase, Mach, altitude, speed, pressure, temperature,
    wind, and atmospheric region. Use the lower tabs for provenance and exportable evidence.

The first analysis can take several minutes because NOAA files must be downloaded and decoded.
Later runs reuse local caches. A cached OpenSky route contains only the observed trajectory; weather
and terrain are loaded separately.

The Boom XB-1 workbook uses the same drag-and-drop process once it is complete. MachLane will save
an incomplete workbook, but it will not invent a phase profile or start route modeling from one.

## Physical sonic-boom calculation

Real weather and terrain are necessary but insufficient for a compliance result. MachLane's built-in
open research solver now calculates a nominal primary ground waveform from:

- a condition-matched aircraft near-field pressure signature;
- the complete route/time-matched NOAA vertical atmosphere available below the aircraft;
- a stratified, wind-adjusted geometrical-acoustics primary ray;
- conservative nonlinear waveform steepening based on an augmented-Burgers formulation;
- classical plus oxygen/nitrogen relaxation absorption;
- ray-tube/stratification scaling and a declared rigid-ground reflection;
- available route-aligned USGS 3DEP terrain.

The implementation follows the direction described in NASA's public
[augmented-Burgers paper](https://ntrs.nasa.gov/citations/20230005332) and uses the public
[NASA SBPW2 LM1021 inputs](https://lbpw.larc.nasa.gov/sbpw2/propagation/lm1021/). It is not a copy
of PCBoom or sBOOM and is not yet numerically equivalent to either tool.

A validated result still requires:

- a reviewed aircraft near-field pressure signature or equivalent-area/CFD input;
- calibrated thrust, drag, fuel-flow, and weight models;
- validated nonlinear propagation with primary and secondary rays;
- ground waveform and overpressure metrics;
- uncertainty analysis and comparison with PCBoom and flight measurements.

LM1021 supplies the NASA SBPW2 near-field signature and three atmospheric benchmark inputs. Those
files enable validation work; their presence does not validate the solver. NOAA remains the
operational atmosphere along a real route.

NASA sBOOM and PCBoom are separately distributed engineering tools, not ordinary Python packages.
MachLane therefore does not bundle them. Its clean-room solver remains `UNVALIDATED`. A reviewed
local comparison wrapper can still be registered through the MachLane JSON contract:

```bash
export MACHLANE_PROPAGATION_COMMAND="/absolute/path/to/your/sboom-or-pcboom-wrapper"
streamlit run src/open_mco/ui/app.py --server.address localhost --server.port 8501
```

The wrapper receives `--input request.json --output result.json`. The output must satisfy
`machlane-physical-route-v1`; MachLane verifies its request checksum, waveforms, uncertainty bound,
three ray families, solver version, validation status, and classification before showing a
validated suggestion. Without a registered wrapper, **Run analysis** uses the built-in primary-ray
research solver and exposes its nominal waveform, surface points, and evidence downloads. Its
research visualizations include a ground-intersection pattern (multi-azimuth when the workbook
supplies matched off-axis signatures), an along-route
overpressure/terrain profile, incident-versus-rigid-ground waveforms, and a ray/terrain cross-section
with illustrative specular-reflection geometry. The same tab also exports the request for independent
sBOOM/PCBoom comparison.

These plots are diagnostic views, not validated boom contours. Off-track intersections currently
reuse the atmospheric region's route-aligned terrain elevation, and the dashed reflected ray shows
the declared rigid-ground assumption rather than a frequency-dependent ground-interaction solution.

When the baseline nominal primary-ray maximum exceeds the research threshold, the open solver also
attempts four altitude sensitivities at −4,000, −2,000, +2,000, and +4,000 ft through the same
route-time NOAA columns. Candidates outside the available column are rejected. MachLane displays
the best improving case and says whether it remains above
the threshold. The baseline near-field signature is deliberately held fixed, so this is a research
sensitivity—not an aircraft-performance check, cleared altitude, route recommendation, or compliant
operating corridor.

MachLane also builds an adaptive height profile for that specific route. Each supersonic atmospheric
region keeps its baseline height unless its nominal primary-ray estimate exceeds the research
threshold; affected regions select the lowest-pressure available discrete height case. The scenario
selector updates the map, live altitude, region-level surface pressure, waveforms, and exports.

The current FAA NPRM value of 0.11 psf is treated as a research screening threshold, not a final
approval. A route is never recommended from nominal overpressure alone: all three requested ray
families must complete, the uncertainty upper bound must remain within the threshold, and the
solver must be marked `VALIDATED`.

## Restart later

```bash
cd /path/to/your/machlane
source .venv/bin/activate
streamlit run src/open_mco/ui/app.py --server.address localhost --server.port 8501
```

The saved OpenSky credentials do not need to be entered again.

## Development checks

```bash
ruff check src tests
mypy src
pytest
```

Automated tests are network-free. Downloaded routes, weather, terrain, normalized aircraft files,
credentials, and generated evidence remain local and are git-ignored.

For the detailed physics boundary, see [Sonic Boom Pipeline](docs/SONIC_BOOM_PIPELINE.md),
[Validation Plan](docs/validation_plan.md), and
[Assumptions and Limitations](docs/assumptions_and_limitations.md). The complete implementation
brief is in [Physical Boom and Rerouting Implementation Prompt](docs/PHYSICAL_BOOM_AND_REROUTING_IMPLEMENTATION_PROMPT.md).

---

Project initiated by [Luca De Caneva](https://github.com/ldc555).
