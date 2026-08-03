"""Terrain contracts and network-free test provider."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

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

    def __init__(self, *, network_enabled: bool = False, resolution_m: float = 10.0) -> None:
        self.network_enabled = network_enabled
        self.resolution_m = resolution_m

    def profile(self, segment: RouteSegment) -> TerrainProfile:
        if not self.network_enabled:
            raise RuntimeError(
                "USGS 3DEP access is disabled; rerun an explicit fetch command to use py3dep"
            )
        raise NotImplementedError(
            "3DEP sampling is an adapter extension point; no network call was made"
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
