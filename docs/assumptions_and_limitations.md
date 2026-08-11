# Assumptions and limitations

- The production UI requires a real OpenSky track and route-time NOAA atmosphere. Synthetic data is
  confined to automated tests and the explicit CLI demo.
- Automatic atmospheric regions are input groupings, not modeled or compliant corridors.
- The mock propagation boundary is arbitrary test behavior and is never used by the production UI.
- Ensemble pass fractions are empirical scenario rates, not validated 95% regulatory reliability.
- Nominal primary-ray surface overpressure is implemented as an unvalidated research estimate.
  Secondary-direct and secondary-indirect paths, caustics/diffraction, loudness, and bounded
  model-form uncertainty are not implemented.
- Historical HRRR samples use the preceding hourly analysis; historical GEFS samples use the
  preceding three-hour output. This is recorded rather than presented as exact temporal interpolation.
- The v1 HRRR and 3DEP coverage guards are deliberately conservative envelopes, not authoritative
  geographic coverage polygons.
- The production workspace's 3DEP result is a three-point-per-region real-data availability
  preview. It is explicitly not the high-resolution terrain/receiver grid required for propagation.
- The open research solver uses a horizontally stratified NOAA column, a fixed horizontal ray
  heading, route-aligned sparse terrain, and rigid-ground pressure doubling. These are declared
  research approximations, not a PCBoom-equivalent method.
- Multi-azimuth ground intersections reuse each region's route-aligned 3DEP elevation away from the
  route centerline. The plotted dashed reflected ray is illustrative specular geometry; it does not
  model ground impedance, scattering, diffraction, terrain shadowing, or reflected-wave propagation.
- The supplied NASA workbook is an unpopulated schema. Engineering values must be sourced and reviewed.
- MachLane is not FAA approved and must not be used as operational flight-control authority.
