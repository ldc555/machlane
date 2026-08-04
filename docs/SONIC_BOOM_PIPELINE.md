# Physical sonic-boom calculation boundary

MachLane does not convert ambient pressure into boom overpressure and does not use the synthetic
planner score for engineering decisions. A physical run is allowed only after every boundary below
has a reviewed input and the propagation engine has passed validation.

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
 reviewed nonlinear ray + waveform propagation engine   ← not implemented
                 │
                 ▼
 receiver waveforms + peak overpressure + loudness + ray family
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

The next implementation must be based on published equations and canonical cases, reviewed by a
qualified acoustics/CFD engineer, and compared against PCBoom or another accepted reference. At minimum
it must cover three-dimensional atmospheric rays, nonlinear distortion, thermoviscous absorption,
molecular relaxation, geometrical spreading, wind, terrain intersection, ground reflection, primary and
secondary paths, waveform reconstruction, and the required ground metrics. Until then, the result is
`NOT_IMPLEMENTED`, never “safe.”
