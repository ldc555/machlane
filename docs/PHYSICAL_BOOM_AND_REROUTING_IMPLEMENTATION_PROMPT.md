# MachLane physical boom and strategic rerouting implementation prompt

You are completing MachLane as a fail-closed operational Mach-cutoff assurance research system.
Work in `/path/to/machlane`. Preserve the existing real-data-only policy and never label an
approximation, atmospheric region, or synthetic fixture as a compliant corridor.

## Required outcome

One **Run analysis** action must load a real OpenSky observed baseline, match NOAA HRRR/GEFS to
position and UTC time, load USGS 3DEP where covered, apply the uploaded aircraft phase schedule,
and invoke a separately installed, reviewed sBOOM/PCBoom wrapper when configured. Without that
solver, stop explicitly and export the complete solver request. Never substitute a mock result.

The physical result must include the aircraft near-field pressure signature, the complete vertical
atmosphere, nonlinear waveform propagation, primary rays, secondary-direct rays,
secondary-indirect rays, absorption/relaxation, wind, geometric spreading, WGS-84 geometry,
terrain intersection, ground reflection, ground waveforms, peak positive/negative overpressure,
PLdB/ASEL where supported, uncertainty bounds, solver/version/configuration checksums, and
reference-validation cases.

## Acceptance and validation

Use 0.11 psf only as the current FAA NPRM research screening threshold. Preserve any different
workbook threshold as source data but do not use it for FAA-oriented screening. Classify a route
candidate as `UNKNOWN` unless primary, secondary-direct, and secondary-indirect families all
completed. Compare the uncertainty upper bound—not only the nominal value—to 0.11 psf. Allow an
operational recommendation only when the candidate is uncertainty-bounded within the threshold
and the solver result is marked `VALIDATED`. Always retain the distinction between research output,
an FAA-approved modeling methodology, and an actual authorization.

Use NASA LM1021 Atmosphere Profile 1, Profile 2, and Standard Atmosphere only as solver regression
benchmarks. Use time- and position-matched NOAA profiles for the operational route. The LM1021
near-field dataset covers only Mach 1.6 at 55,000 ft; fail closed at other supersonic operating
points until reviewed signatures are supplied.

## Strategic rerouting

When the baseline exceeds the threshold, evaluate lateral route offsets, altitude changes,
departure-time changes, and speed changes only inside the aircraft performance and near-field
coverage. For every candidate, resample NOAA at its new four-dimensional trajectory and reload
terrain across its new surface footprint. Preserve route geometry, timestamps, aircraft state,
airspace/operational constraints, rejected candidates, and rejection reasons. Rank compliant
candidates by minimum time, distance, and deviation cost; never call an acoustically acceptable
path operationally flyable unless all declared operational constraints were checked.

## Interface and exports

The UI must show the OpenSky baseline, physical surface samples, 0.11 psf exceedances, terrain,
and the validated suggested route as separate layers. Include an along-route overpressure/terrain
profile, selectable ground waveforms, primary/secondary ray visibility, candidate trade-space,
uncertainty margins, atmospheric columns, data coverage, and clear unavailable states.

Export a self-contained ZIP containing the normalized request, solver result, surface-sample CSV,
footprint GeoJSON, route candidates, manifest, source checksums, executable/configuration checksum,
timestamps, assumptions, limitations, and validation status. Also expose each major file as an
individual download.

## Engineering rules

Use the existing `SonicBoomPropagationEngine` and `machlane-physical-route-v1` contracts. Do not
bundle restricted NASA binaries. Invoke external tools without a shell, with explicit input/output
paths and a timeout. Validate every returned waveform and summary metric, bind results to the exact
request SHA-256, and reject stale or mismatched results. Add deterministic unit tests for incomplete
rays, limit classification, solver validation gating, checksum mismatch, exports, and workbook
operating-point coverage. Run Ruff, strict mypy, the complete pytest suite, and a browser test of the
local Streamlit workflow before claiming completion.
