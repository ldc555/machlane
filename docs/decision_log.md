# Decision log

## 2026-08-03 — Start with an evidence-preserving synthetic slice

Use a fictional demo aircraft because the supplied NASA workbook has no engineering values. Keep
the real workbook unchanged and fail validation on required blanks. Use an arbitrary, labeled mock
engine only for integration. Do not implement physics until a cited source and human review exist.

## 2026-08-03 — Keep external projects as dependencies

Herbie, xarray/cfgrib/ecCodes, Py3DEP/rasterio, GeoPandas/Shapely/PyProj and visualization tools are
package-manager dependencies. No upstream repository is copied or forked. PCBoom remains external.

