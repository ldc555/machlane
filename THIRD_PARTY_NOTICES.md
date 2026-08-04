# Third-party notices

MachLane depends on separately distributed open-source packages listed in `pyproject.toml` and
`environment.yml`; each retains its own license. NASA PCBoom is not included and requires a
separate NASA software request and agreement. Public weather and terrain data retain their
source terms and attribution requirements.

Curated airport reference points are derived from OurAirports' public-domain data dump
(https://ourairports.com/data/), retrieved 2026-08-04. OurAirports provides the data without a
guarantee of accuracy or fitness for use.

Optional sonic-boom calculation-preparation tools remain separately distributed: SU2 is used only
through an offline case boundary under its own LGPL-2.1 terms; meshio is MIT-licensed; and MetPy is
BSD-3-Clause licensed. No source from those projects is copied into MachLane. Their presence does
not establish the validity of a CFD mesh, near-field signature, propagation calculation, or result.
