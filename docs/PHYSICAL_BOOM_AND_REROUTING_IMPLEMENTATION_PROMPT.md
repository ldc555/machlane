# MachLane open physical boom and strategic rerouting implementation prompt

You are completing MachLane as a fail-closed operational Mach-cutoff assurance research system.
Work in `/path/to/machlane`. Preserve the existing real-data-only policy and never label an
approximation, atmospheric region, or synthetic fixture as a compliant corridor.

Implement a new, clean-room, open-source **research solver** from published equations and public
benchmark data. Do not copy, decompile, wrap, upload, or redistribute PCBoom or sBOOM source or
binaries. Keep the existing external `machlane-physical-route-v1` wrapper so independently obtained
PCBoom/sBOOM results can later be compared when their applicable software agreement permits it.

The immediate deliverable is reproducible research output, not FAA approval. Every open-solver result
must remain `UNVALIDATED` until the validation gates below are satisfied. Never describe a low predicted
value as “safe,” “compliant,” or an approved route.

## Required outcome

One **Run analysis** action must load a user-selected real OpenSky observed baseline, match NOAA
HRRR/GEFS to every position and UTC time, load USGS 3DEP where covered, apply the uploaded aircraft
phase schedule, and invoke either the new open research solver or a separately installed reviewed
wrapper. The selected OpenSky date must correspond to an actual observed flight; never search an empty
date and silently replace it. Never substitute a mock route, atmosphere, terrain, aircraft, or result.

The physical result must include the aircraft near-field pressure signature, the complete vertical
atmosphere, nonlinear waveform propagation, primary rays, secondary-direct rays,
secondary-indirect rays, absorption/relaxation, wind, geometric spreading, WGS-84 geometry,
terrain intersection, ground reflection, ground waveforms, peak positive/negative overpressure,
PLdB/ASEL where supported, uncertainty bounds, solver/version/configuration checksums, and
reference-validation cases.

## Required architecture

Implement the solver as a separate package under `src/open_mco/physics/open_solver/` with small,
testable modules. Do not put numerical physics in the Streamlit page.

- `models.py`: immutable SI-only solver inputs, ray states, waveforms, metrics, convergence records,
  and solver provenance.
- `atmosphere.py`: interpolate the complete vertical NOAA thermodynamic, humidity, and horizontal-wind
  profile without extrapolating beyond declared coverage; calculate sound speed and effective sound
  speed along the propagation direction.
- `rays.py`: WGS-84-aware three-dimensional Hamiltonian ray integration, eigenray search, turning-point
  detection, caustic/zone-of-silence reporting, and explicit primary, secondary-direct, and
  secondary-indirect ray-family identity.
- `burgers.py`: augmented Burgers-equation propagation of the complete near-field waveform, including
  nonlinearity, thermoviscous absorption, molecular relaxation, geometrical spreading, stratification,
  and wind effects. Use a documented conservative numerical method with shock capture and convergence
  diagnostics; do not replace the waveform with a peak-pressure attenuation formula.
- `terrain.py`: intersect rays with WGS-84 terrain profiles, identify missing 3DEP coverage explicitly,
  and apply a versioned ground-impedance/reflection model only when its required inputs exist.
- `metrics.py`: waveform-consistent positive/negative peak overpressure, impulse, rise time, duration,
  and documented loudness metrics. Unsupported metrics must be `None`, never estimated silently.
- `solver.py`: implement `SonicBoomPropagationEngine`, assemble per-ray results, and emit
  `PhysicalRouteAnalysis` through the existing checksum-bound schema.
- `uncertainty.py`: propagate declared atmosphere, near-field, terrain, numerical, and model-form
  uncertainty; never manufacture an uncertainty bound from a fixed percentage.
- `validation.py`: execute canonical cases and NASA SBPW2 LM1021 benchmark comparisons and produce a
  machine-readable validation report.

Add only justified dependencies. NumPy, SciPy, xarray, PyProj, MetPy, Rasterio and Shapely may provide
numerical, atmosphere, geodesy, and GIS primitives, but none may be presented as a validated sonic-boom
model. Pin any new dependency, record its license in `THIRD_PARTY_NOTICES.md`, and keep the core solver
usable without Streamlit.

## Numerical implementation sequence

1. Freeze units and coordinate conventions in SI and document pressure perturbation sign, waveform
   independent variable, propagation azimuth, meteorological wind convention, height datum, and time
   basis.
2. Implement analytic tests first: uniform atmosphere, no-wind symmetry, constant-gradient refraction,
   flat terrain intersection, zero-amplitude linear limit, and conservation/convergence checks.
3. Convert one condition-matched aircraft near-field waveform to the solver boundary without changing
   its Mach, altitude, weight, angle of attack, azimuth, or reference distance.
4. Integrate each requested ray family through the complete atmosphere and retain the full ray path,
   travel time, spreading/Jacobian diagnostics, turning points, and termination reason.
5. March the waveform along each valid ray with adaptive distance/time resolution. Record grid spacing,
   tolerances, rejected steps, conservation diagnostics, and convergence history.
6. Intersect with terrain and apply ground reflection only where the terrain and impedance assumptions
   are declared. Missing terrain or a failed ray produces `UNKNOWN`, never zero overpressure.
7. Reconstruct ground waveforms and metrics at every receiver; preserve nominal values and uncertainty
   bounds separately.
8. Run mesh/time-step/ray-launch refinement until the declared metrics converge to a documented research
   tolerance. A non-converged receiver remains `UNKNOWN`.

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

Validation must be staged and visible:

1. `UNVALIDATED`: equations implemented and analytic tests pass.
2. `COMPARISON_ONLY`: numerical convergence is demonstrated and LM1021/SBPW2 waveform and metric
   comparisons pass documented tolerances against published reference outputs.
3. `VALIDATED`: never assigned automatically by this repository. It requires an approved validation
   plan, independent expert review, controlled comparison with PCBoom/sBOOM and relevant flight
   measurements, and explicit signed evidence identifying the approved method and scope.

The software must never promote its own validation status based only on tests it generated itself.

## Strategic rerouting

When the baseline uncertainty upper bound exceeds the research threshold—or a receiver is
`UNKNOWN`—evaluate changes in this order: departure time, altitude, Mach, then bounded lateral route
offset. Every candidate must be a new four-dimensional trajectory: recompute aircraft state and fuel,
resample NOAA at the new position/time, reload terrain across the full predicted footprint, rematch the
near-field operating point, and rerun every requested ray family. Do not treat atmospheric-region color
or ambient pressure as a routing objective.

Reject candidates outside the aircraft performance deck, near-field envelope, available weather,
terrain, declared airspace constraints, climb/descent feasibility, reserve-fuel policy, or solver
convergence domain. Preserve route geometry, timestamps, aircraft state, rejected candidates, and exact
rejection reasons. Rank research candidates by a documented multi-objective cost containing uncertainty
margin, time, fuel, distance, lateral deviation, altitude changes, and operational penalties. Never call
an acoustically acceptable path operationally flyable unless all declared operational constraints were
checked. With an `UNVALIDATED` or `COMPARISON_ONLY` solver, label the output **research candidate**, not
“suggested compliant route.”

## Interface and exports

The UI must show the OpenSky baseline, physical surface samples, 0.11 psf exceedances, terrain,
and the research or validated suggested route as separate layers. Include an along-route overpressure/terrain
profile, selectable ground waveforms, primary/secondary ray visibility, candidate trade-space,
uncertainty margins, atmospheric columns, data coverage, and clear unavailable states.

The main screen must make these distinctions impossible to miss:

- ambient NOAA pressure is not boom overpressure;
- atmospheric regions are numerical sampling regions, not compliant corridors;
- an aircraft near-field curve is not a ground waveform;
- `UNVALIDATED`, `COMPARISON_ONLY`, and `VALIDATED` are different states;
- the 0.11 psf value is a research screening threshold from an NPRM, not a final universal approval
  criterion;
- an acoustically preferred candidate is not an ATC-cleared or operationally approved route.

Export a self-contained ZIP containing the normalized request, solver result, surface-sample CSV,
footprint GeoJSON, route candidates, manifest, source checksums, executable/configuration checksum,
timestamps, assumptions, limitations, and validation status. Also expose each major file as an
individual download.

## Engineering rules

Use the existing `SonicBoomPropagationEngine` and `machlane-physical-route-v1` contracts. Do not
bundle restricted NASA binaries. Invoke external tools without a shell, with explicit input/output
paths and a timeout. Validate every returned waveform and summary metric, bind results to the exact
request SHA-256, and reject stale or mismatched results.

Add deterministic tests for equations, unit conversions, interpolation, ray families, turning points,
terrain intersections, waveform/summary consistency, non-convergence, missing inputs, incomplete rays,
limit classification, solver validation gating, checksum mismatch, evidence exports, workbook
operating-point coverage, and reroute candidate rejection. Add regression fixtures from public NASA
SBPW2 LM1021 benchmark files with source URL and SHA-256, but do not commit NASA executable software.

Run Ruff, strict mypy, the complete pytest suite with coverage, numerical convergence tests, and a
browser test of the local Streamlit workflow before claiming completion. Finish with a validation matrix
showing each required physical effect as `IMPLEMENTED`, `TESTED`, `COMPARED`, or `NOT READY`, with links
to equations, sources, tests, and evidence. Any unfinished or weakly supported item must keep the route
classification `UNKNOWN`.
