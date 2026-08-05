# Architecture

MachLane converts each external input into an immutable, SI-normalized Pydantic model before
planning. Providers preserve source metadata and checksums. The production workspace is
real-data-only; synthetic providers remain isolated test fixtures.

```text
OpenSky observation timestamps + route positions
                    -> time-aligned HRRR/GEFS columns
                    -> automatic atmospheric regions
                    -> available 3DEP terrain
                    -> normalized real-input evidence
                    -> propagation protocol (not yet connected)
```

The running UI stops if OpenSky or NOAA is unavailable and never substitutes synthetic inputs.
`MockMCOEngine` remains deliberately synthetic and test-only. `FastMCOEngine` raises
`NotImplementedError` at every physics extension point. PCBoom is staged offline and is never
bundled, invoked, or redistributed.
