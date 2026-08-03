# FAA-oriented requirements matrix

This is research traceability, not a legal conclusion or FAA approval.

| Requested outcome | Repository evidence | Status | Validation / known gap |
|---|---|---|---|
| Aircraft/configuration | workbook schema, SI conversion, checksum, source/page fields | SUPPORTED | NASA STCA values remain unpopulated |
| Weather source and interpolation | provider metadata, cycle/valid time, variables, checksums | SUPPORTED | real adapter downloads not implemented |
| Propagation model | name, version, assumptions, limitations | SUPPORTED | only synthetic mock executes |
| Maximum allowable speed / operational control | segment selection and rejected candidates | PARTIAL | no dispatch/FMS control authority |
| Terrain | provider, resolution, datum, interpolation, checksum | SUPPORTED | 3DEP network sampling is scaffolded |
| Uncertainty | empirical scenario rate and conservative member summary | VALIDATION_REQUIRED | not calibrated regulatory reliability |
| PCBoom / flight-test comparison | offline exchange and comparison seam | VALIDATION_REQUIRED | requires separately licensed PCBoom and reviewed cases |
| Primary boom ≤ 0.11 psf | explicit report status | NOT_IMPLEMENTED | validated source/propagation model required |
| Secondary-direct boom | explicit report status | NOT_IMPLEMENTED | upper-atmosphere path model required |
| Secondary-indirect boom | explicit report status | NOT_IMPLEMENTED | reflection plus upper-atmosphere model required |
| FAA approval | prominent disclaimer | VALIDATION_REQUIRED | outside repository authority |

