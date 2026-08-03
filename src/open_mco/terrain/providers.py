"""Terrain contracts and network-free test provider."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode

import numpy as np
import requests

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
    """Sample a route segment through official USGS 3DEP web services."""

    name = "usgs_3dep"
    cross_section_url = (
        "https://api.water.usgs.gov/nldi/pygeoapi/processes/nldi-xsatendpts/execution?f=json"
    )
    point_url = "https://epqs.nationalmap.gov/v1/json"
    supported_resolutions_m = frozenset({1.0, 3.0, 5.0, 10.0, 30.0})

    def __init__(
        self,
        *,
        network_enabled: bool = False,
        resolution_m: float = 10.0,
        sample_spacing_m: float = 1_000.0,
    ) -> None:
        if resolution_m not in self.supported_resolutions_m:
            raise ValueError("3DEP resolution must be one of 1, 3, 5, 10, or 30 meters")
        if sample_spacing_m <= 0:
            raise ValueError("terrain sample spacing must be positive")
        self.network_enabled = network_enabled
        self.resolution_m = resolution_m
        self.sample_spacing_m = sample_spacing_m

    def profile(self, segment: RouteSegment) -> TerrainProfile:
        if not self.network_enabled:
            raise RuntimeError("USGS 3DEP access is disabled; rerun an explicit fetch command")
        if os.getenv("MACHLANE_NETWORK_DISABLED") == "1":
            raise RuntimeError("network access is disabled by MACHLANE_NETWORK_DISABLED")
        sample_count = max(2, int(np.ceil(segment.distance_m / self.sample_spacing_m)) + 1)
        if sample_count > 10_000:
            raise ValueError("terrain request exceeds 10,000 samples; increase sample spacing")
        cross_section_count = max(5, sample_count - 1)
        cross_section_body = json.dumps(
            {
                "inputs": [
                    {
                        "id": "lat",
                        "value": [segment.start_latitude, segment.end_latitude],
                        "type": "text/plain",
                    },
                    {
                        "id": "lon",
                        "value": [segment.start_longitude, segment.end_longitude],
                        "type": "text/plain",
                    },
                    {
                        "id": "numpts",
                        "value": str(cross_section_count),
                        "type": "text/plain",
                    },
                    {
                        "id": "3dep_res",
                        "value": f"{self.resolution_m:g}",
                        "type": "text/plain",
                    },
                ]
            }
        ).encode()
        response_bytes = self._request(
            self.cross_section_url,
            data=cross_section_body,
            headers={"Content-Type": "application/json", "User-Agent": "MachLane/0.1"},
        )
        payload = json.loads(response_bytes)
        features = payload.get("features", []) if isinstance(payload, dict) else []
        if not 2 <= len(features) <= 10_000:
            raise ValueError("3DEP cross-section response has an unexpected sample count")
        coordinates = [tuple(feature["geometry"]["coordinates"]) for feature in features]
        elevations = [float(feature["properties"]["elevation"]) for feature in features]

        endpoint_query = urlencode(
            {
                "x": segment.end_longitude,
                "y": segment.end_latitude,
                "wkid": 4326,
                "units": "Meters",
                "includeDate": "false",
            }
        )
        endpoint_bytes = self._request(f"{self.point_url}?{endpoint_query}")
        endpoint_payload = json.loads(endpoint_bytes)
        endpoint_elevation = float(endpoint_payload["value"])
        coordinates.append((segment.end_longitude, segment.end_latitude))
        elevations.append(endpoint_elevation)
        elevations_array = np.asarray(elevations, dtype=float)
        actual_count = len(features) + 1
        if elevations_array.size != actual_count or not np.all(np.isfinite(elevations_array)):
            raise ValueError("3DEP returned missing or misaligned elevation samples")
        distances = np.linspace(0.0, segment.distance_m, actual_count)
        checksum = hashlib.sha256(response_bytes + endpoint_bytes).hexdigest()
        return TerrainProfile(
            distance_m=tuple(float(value) for value in distances),
            elevation_m=tuple(float(value) for value in elevations_array),
            latitude=tuple(float(latitude) for _, latitude in coordinates),
            longitude=tuple(float(longitude) for longitude, _ in coordinates),
            source=TerrainSourceMetadata(
                provider=self.name,
                resolution_m=self.resolution_m,
                horizontal_datum="WGS84",
                vertical_datum="3DEP service elevation datum; verify for each engineering case",
                interpolation=f"USGS cross-section query every {self.sample_spacing_m:g} m",
                retrieved_at=datetime.now(UTC),
                source_url=self.cross_section_url,
                checksum=checksum,
                label="REAL_USGS_DATA_UNVALIDATED_FOR_OPERATIONAL_USE",
            ),
        )

    @staticmethod
    def _request(
        url: str,
        *,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> bytes:
        try:
            response = requests.request(
                "POST" if data is not None else "GET",
                url,
                data=data,
                headers=headers or {"User-Agent": "MachLane/0.1"},
                timeout=90,
            )
            response.raise_for_status()
            return response.content
        except requests.RequestException as exc:
            raise RuntimeError(f"USGS 3DEP request failed: {exc}") from exc


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
            raise RuntimeError("install MachLane's 'gis' extra for local raster sampling") from exc
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
