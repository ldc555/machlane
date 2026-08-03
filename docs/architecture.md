# Architecture

MachLane converts each external input into an immutable, SI-normalized Pydantic model before
planning. Providers preserve source metadata and checksums. The planner depends only on provider
and propagation protocols, making synthetic, real-data, fast-engine and PCBoom workflows
replaceable without changing route or evidence code.

```text
workbook + route + weather + terrain -> normalized models -> propagation protocol
                                                -> grid search -> evidence package + UI
```

The default path is entirely network-free. `MockMCOEngine` is deliberately synthetic.
`FastMCOEngine` raises `NotImplementedError` at every physics extension point. PCBoom is staged
offline and is never bundled, invoked or redistributed.

