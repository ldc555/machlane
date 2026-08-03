# Data dictionary

All normalized dimensional values are SI. Every externally sourced scalar records its original
value/unit, normalized value/unit, source, document or URL, page/figure when available, retrieval
timestamp, and local-file checksum.

| Model | Purpose |
|---|---|
| `AircraftModel` | configuration, limits, performance points, workbook checksum |
| `AtmosphericProfile` | vertical temperature, pressure, winds and optional humidity |
| `TerrainProfile` | distance/elevation samples plus datum and resolution |
| `Route` / `RouteSegment` | ordered WGS84 geometry, distance and bearing |
| `PropagationRequest` / `PropagationResult` | engine-neutral candidate exchange |
| `SegmentLimit` / `PlannerResult` | selected candidate and every rejected reason |
| `RunManifest` | versions, checksums, inputs, assumptions, limitations and statuses |

