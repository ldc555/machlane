"""Terrain contracts and network-free test provider."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import numpy as np
from pyproj import Geod

from open_mco.aircraft.loader import sha256_file
from open_mco.models import RouteSegment, TerrainProfile, TerrainSourceMetadata


class TerrainProvider(Protocol):
    name: str

    def profile(self, segment: RouteSegment) -> TerrainProfile:
        """Return a sampled terrain profile with datum and source metadata."""
        ...


class FlatTerrainProvider:
    name = "flat"

    def __init__(self, elevation_m: float = 0.0) -> None:
        self.elevation_m = elevation_m

    def profile(self, segment: RouteSegment) -> TerrainProfile:
        return TerrainProfile(
            distance_m=(0.0, segment.distance_m),
            elevation_m=(self.elevation_m, self.elevation_m),
            latitude=(segment.start_latitude, segment.end_latitude),
            longitude=(segment.start_longitude, segment.end_longitude),
            source=TerrainSourceMetadata(
                provider=self.name,
                resolution_m=segment.distance_m,
                horizontal_datum="WGS84",
                vertical_datum="synthetic zero",
                interpolation="constant",
                retrieved_at=datetime.now(UTC),
                label="SYNTHETIC_NOT_FOR_ENGINEERING_USE",
            ),
        )


class USGS3DEPProvider:
    name = "usgs_3dep"

    def __init__(
        self,
        *,
        network_enabled: bool = False,
        resolution_m: float = 10.0,
        sample_spacing_m: float = 1_000.0,
    ) -> None:
        if resolution_m <= 0 or sample_spacing_m <= 0:
            raise ValueError("terrain resolution and sample spacing must be positive")
        self.network_enabled = network_enabled
        self.resolution_m = resolution_m
        self.sample_spacing_m = sample_spacing_m

    def profile(self, segment: RouteSegment) -> TerrainProfile:
        if not self.network_enabled:
            raise RuntimeError(
                "USGS 3DEP access is disabled; rerun an explicit fetch command to use py3dep"
            )
        try:
            import py3dep  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("install MachLane's 'full' extra for USGS 3DEP access") from exc
        geod = Geod(ellps="WGS84")
        sample_count = max(2, int(np.ceil(segment.distance_m / self.sample_spacing_m)) + 1)
        interior = (
            []
            if sample_count == 2
            else geod.npts(
                segment.start_longitude,
                segment.start_latitude,
                segment.end_longitude,
                segment.end_latitude,
                sample_count - 2,
            )
        )
        coordinates = [
            (segment.start_longitude, segment.start_latitude),
            *interior,
            (segment.end_longitude, segment.end_latitude),
        ]
        elevations = np.asarray(py3dep.elevation_bycoords(coordinates, crs=4326), dtype=float)
        if elevations.size != sample_count or not np.all(np.isfinite(elevations)):
            raise ValueError("3DEP returned missing or misaligned elevation samples")
        distances = np.linspace(0.0, segment.distance_m, sample_count)
        return TerrainProfile(
            distance_m=tuple(float(value) for value in distances),
            elevation_m=tuple(float(value) for value in elevations),
            latitude=tuple(float(latitude) for _, latitude in coordinates),
            longitude=tuple(float(longitude) for longitude, _ in coordinates),
            source=TerrainSourceMetadata(
                provider=self.name,
                resolution_m=self.resolution_m,
                horizontal_datum="WGS84",
                vertical_datum="3DEP service elevation datum; verify response metadata for case",
                interpolation=f"point query every {self.sample_spacing_m:g} m via elevation_bycoords",
                retrieved_at=datetime.now(UTC),
                source_url="https://www.usgs.gov/3d-elevation-program",
                label="REAL_USGS_DATA_UNVALIDATED_FOR_OPERATIONAL_USE",
            ),
        )


class RasterTerrainProvider:
    name = "local_raster"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(self.path)

    def profile(self, segment: RouteSegment) -> TerrainProfile:
        try:
            import rasterio  # type: ignore[import-not-found]
            from pyproj import Transformer
        except ImportError as exc:
            raise RuntimeError("install MachLane's 'full' extra for local raster sampling") from exc
        with rasterio.open(self.path) as dataset:
            transformer = Transformer.from_crs("EPSG:4326", dataset.crs, always_xy=True)
            coordinates = [
                transformer.transform(segment.start_longitude, segment.start_latitude),
                transformer.transform(segment.end_longitude, segment.end_latitude),
            ]
            elevations = tuple(float(sample[0]) for sample in dataset.sample(coordinates))
            resolution = max(abs(dataset.res[0]), abs(dataset.res[1]))
            datum = dataset.crs.to_string() if dataset.crs else "UNKNOWN"
        return TerrainProfile(
            distance_m=(0.0, segment.distance_m),
            elevation_m=elevations,
            latitude=(segment.start_latitude, segment.end_latitude),
            longitude=(segment.start_longitude, segment.end_longitude),
            source=TerrainSourceMetadata(
                provider=self.name,
                resolution_m=resolution,
                horizontal_datum=datum,
                vertical_datum="from raster metadata; verify before engineering use",
                interpolation="nearest sample",
                retrieved_at=datetime.now(UTC),
                source_url=str(self.path),
                checksum=sha256_file(self.path),
            ),
        )
