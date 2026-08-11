# Physical sonic-boom calculation boundary

MachLane does not convert ambient pressure into boom overpressure and does not use the synthetic
planner score for engineering decisions. The built-in solver produces an `UNVALIDATED` primary-ray
research estimate only when the near-field, NOAA, and terrain inputs pass strict gates. Validation
and operational recommendations remain separately locked.

```text
aircraft geometry + operating point
                 │
                 ▼
       near-field CFD / measurement
       SU2 offline case boundary
                 │
                 ▼
  distance_m, overpressure_pa + provenance
                 │
HRRR / GEFS / ERA5 ──> normalized atmosphere ──> MetPy moist-air preparation
USGS 3DEP / raster ───> normalized terrain
route segment ────────> bearing and WGS-84 position
                 │
                 ▼
           SonicBoomCase
                 │
                 ▼
 open nonlinear primary-ray research solver              ← UNVALIDATED
                 │
                 ▼
 primary receiver waveforms + nominal peak overpressure
                 │
                 ▼
 PCBoom comparison matrix / published cases / flight measurements
```

## What was added

1. `NearFieldSignature` is an immutable SI model for distance/pressure samples, reference distance,
   azimuth, flight condition, file checksum, configuration checksum, solver version, and source label.
2. The interchange CSV has exactly two columns: `distance_m,overpressure_pa`. Samples must be finite,
   strictly ordered, non-zero, and contain at least three points. The file is not accepted as validated
   merely because it parses.
3. `SU2NearFieldAdapter` stages the solver version, configuration, mesh, operating point, and SHA-256
   checksums. It can inspect mesh structure through meshio and import the resulting pressure signature.
   It never launches a CFD job automatically.
4. MetPy optionally derives water-vapor mixing ratio and density from the normalized pressure,
   temperature, and relative-humidity arrays. These are atmospheric preparation values, not a sonic-boom
   result.
5. `SonicBoomCase` is the future engine input. It prevents mixing a signature generated at one Mach or
   altitude with a different propagation case and declares every requested physical effect.
6. `SonicBoomPrediction` requires ground waveforms, peak pressure metrics, ray-family identity, engine
   version, limitations, and validation status.
7. `boom-readiness` audits the workbook, performance map, mission settings, signature, data adapters,
   engine, and reference validation. It performs no network, CFD, or PCBoom call.
8. `OpenResearchRouteSolver` traces a wind-adjusted primary ray through each real NOAA column,
   marches the waveform with a conservative nonlinear finite-volume step and frequency-dependent
   absorption, intersects available 3DEP terrain, and returns checksum-bound nominal ground
   waveforms. It is explicitly `UNVALIDATED` and leaves the classification `UNKNOWN`.
9. The production UI presents four research diagnostics from the checksum-bound result: a
   ground-intersection pattern (multi-azimuth only when matched off-axis signatures exist), an along-route overpressure/terrain profile,
   incident-versus-rigid-ground waveforms, and a ray/terrain cross-section with illustrative
   specular-reflection geometry. None is labeled as a validated footprint or compliant corridor.
10. If the baseline nominal primary-ray value exceeds the research threshold, the open solver
    attempts ±2,000 ft and ±4,000 ft altitude sensitivities through the same NOAA columns and rejects
    source heights outside the available column. These
    cases hold the baseline near-field signature fixed, retain classification `UNKNOWN`, and cannot
    become the recommended candidate.

## Open-source roles and limits

| Component | Role in MachLane | What it does not prove |
|---|---|---|
| SU2 | Candidate near-field CFD and equivalent-area workflow | Mesh convergence, source-signature accuracy, or ground boom |
| meshio | Read SU2 and other mesh formats for structural inspection | Aerodynamic suitability or CFD convergence |
| MetPy | Unit-aware moist-air density and humidity preparation | Sound propagation, absorption, rays, or boom loudness |
| Herbie/xarray/cfgrib/ecCodes | Forecast retrieval and normalization | Forecast calibration or acoustical validity |
| PyProj | WGS-84 route and bearing geometry | Acoustic ray paths |
| Rasterio/USGS services | Terrain profile normalization | Ground impedance or reflection physics |
| NumPy/SciPy | Reviewed numerical primitives | A validated physical model by themselves |
| PCBoom adapter | Offline comparison exchange | A license, bundled solver, or automatic validation |

We did not add a generic environmental-noise attenuation library as the propagation engine. Linear
outdoor sound attenuation is not an adequate substitute for sonic-boom ray tracing and augmented
Burgers-type nonlinear waveform propagation.

## Current NASA STCA workbook state

The checked-in workbook and the supplied download have the same SHA-256 checksum. The workbook is a
stable data contract, but its required identity, geometry, operating-limit, performance, equivalent-area,
and near-field-signature fields are still blank. `Mission_Config` contains the reliability target and boom
limit. Running the readiness command prints every missing field without stopping after the first one.

## Next physical milestone

The primary research path is implemented, but the next milestone remains NASA SBPW2 numerical
comparison, independent review, secondary-direct and secondary-indirect eigenrays, caustic/diffraction
treatment, propagation-grade receiver terrain, model-form uncertainty, and reviewed loudness metrics.
Until those gates pass, the result is `UNVALIDATED`/`UNKNOWN`, never “safe.”
