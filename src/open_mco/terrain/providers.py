"""Terrain contracts and network-free test provider."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import urlencode

import numpy as np
import requests
from pyproj import Geod

from open_mco.aircraft.loader import sha256_file
from open_mco.models import RouteSegment, TerrainProfile, TerrainSourceMetadata

_GEOD = Geod(ellps="WGS84")


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
        cache_dir: str | Path | None = None,
        sampling_mode: Literal["cross_section", "route_points"] = "cross_section",
        timeout_seconds: float = 30.0,
    ) -> None:
        if resolution_m not in self.supported_resolutions_m:
            raise ValueError("3DEP resolution must be one of 1, 3, 5, 10, or 30 meters")
        if sample_spacing_m <= 0:
            raise ValueError("terrain sample spacing must be positive")
        if timeout_seconds <= 0:
            raise ValueError("terrain request timeout must be positive")
        self.network_enabled = network_enabled
        self.resolution_m = resolution_m
        self.sample_spacing_m = sample_spacing_m
        self.cache_dir = None if cache_dir is None else Path(cache_dir)
        self.sampling_mode = sampling_mode
        self.timeout_seconds = timeout_seconds

    def _cache_path(self, segment: RouteSegment) -> Path | None:
        if self.cache_dir is None:
            return None
        payload = json.dumps(
            {
                "start": [segment.start_latitude, segment.start_longitude],
                "end": [segment.end_latitude, segment.end_longitude],
                "distance_m": segment.distance_m,
                "path": segment.path,
                "resolution_m": self.resolution_m,
                "sample_spacing_m": self.sample_spacing_m,
                "sampling_mode": self.sampling_mode,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return self.cache_dir / f"terrain_{hashlib.sha256(payload).hexdigest()[:24]}.json"

    def profile(self, segment: RouteSegment) -> TerrainProfile:
        if not self.network_enabled:
            raise RuntimeError("USGS 3DEP access is disabled; rerun an explicit fetch command")
        if os.getenv("MACHLANE_NETWORK_DISABLED") == "1":
            raise RuntimeError("network access is disabled by MACHLANE_NETWORK_DISABLED")
        cache_path = self._cache_path(segment)
        if cache_path is not None and cache_path.exists():
            try:
                cached = TerrainProfile.model_validate_json(cache_path.read_text(encoding="utf-8"))
                if cached.source.provider == self.name:
                    return cached
            except (OSError, ValueError):
                pass
        if self.sampling_mode == "route_points":
            return self._route_point_profile(segment, cache_path)
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
        if endpoint_elevation <= -1_000_000:
            raise ValueError("3DEP has no elevation at the requested endpoint")
        coordinates.append((segment.end_longitude, segment.end_latitude))
        elevations.append(endpoint_elevation)
        elevations_array = np.asarray(elevations, dtype=float)
        actual_count = len(features) + 1
        if elevations_array.size != actual_count or not np.all(np.isfinite(elevations_array)):
            raise ValueError("3DEP returned missing or misaligned elevation samples")
        distances = np.linspace(0.0, segment.distance_m, actual_count)
        checksum = hashlib.sha256(response_bytes + endpoint_bytes).hexdigest()
        profile = TerrainProfile(
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
        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            partial = cache_path.with_suffix(".json.part")
            partial.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
            partial.replace(cache_path)
        return profile

    def _route_point_profile(
        self,
        segment: RouteSegment,
        cache_path: Path | None,
    ) -> TerrainProfile:
        """Sample a small real 3DEP route cross-check through the responsive EPQS service.

        The production workspace uses this mode only to establish terrain availability while no
        propagation engine exists. It deliberately records its sparse sampling and must not be
        promoted to an acoustic terrain-interaction profile.
        """

        path = segment.path or (
            (segment.start_latitude, segment.start_longitude),
            (segment.end_latitude, segment.end_longitude),
        )
        def point_at_fraction(fraction: float) -> tuple[float, float]:
            legs = [
                max(0.0, _GEOD.inv(start[1], start[0], end[1], end[0])[2])
                for start, end in zip(path, path[1:], strict=False)
            ]
            target = sum(legs) * fraction
            elapsed = 0.0
            for start, distance, end in zip(path[:-1], legs, path[1:], strict=True):
                if elapsed + distance >= target:
                    bearing, _, _ = _GEOD.inv(start[1], start[0], end[1], end[0])
                    longitude, latitude, _ = _GEOD.fwd(
                        start[1], start[0], bearing, target - elapsed
                    )
                    return latitude, longitude
                elapsed += distance
            return path[-1]

        points = [point_at_fraction(fraction) for fraction in (0.0, 0.5, 1.0)]
        response_parts: list[bytes] = []
        elevations: list[float] = []
        resolutions: list[float] = []
        for latitude, longitude in points:
            query = urlencode(
                {
                    "x": longitude,
                    "y": latitude,
                    "wkid": 4326,
                    "units": "Meters",
                    "includeDate": "false",
                }
            )
            response_bytes = self._request(f"{self.point_url}?{query}")
            payload = json.loads(response_bytes)
            elevation = float(payload["value"])
            if elevation <= -1_000_000 or not np.isfinite(elevation):
                raise ValueError("3DEP has no elevation at a requested route point")
            elevations.append(elevation)
            resolutions.append(float(payload.get("resolution", self.resolution_m)))
            response_parts.append(response_bytes)
        distances = np.linspace(0.0, segment.distance_m, len(points))
        profile = TerrainProfile(
            distance_m=tuple(float(value) for value in distances),
            elevation_m=tuple(elevations),
            latitude=tuple(latitude for latitude, _ in points),
            longitude=tuple(longitude for _, longitude in points),
            source=TerrainSourceMetadata(
                provider=self.name,
                resolution_m=max(resolutions),
                horizontal_datum="WGS84",
                vertical_datum="3DEP service elevation datum; verify for each engineering case",
                interpolation="three EPQS route points; availability preview only",
                retrieved_at=datetime.now(UTC),
                source_url=self.point_url,
                checksum=hashlib.sha256(b"".join(response_parts)).hexdigest(),
                label="REAL_USGS_DATA_SPARSE_PREVIEW_NOT_FOR_PROPAGATION",
            ),
        )
        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            partial = cache_path.with_suffix(".json.part")
            partial.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
            partial.replace(cache_path)
        return profile

    def _request(
        self,
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
                timeout=self.timeout_seconds,
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
