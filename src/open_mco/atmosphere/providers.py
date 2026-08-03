"""Weather provider contracts with network access opt-in and provenance-first outputs."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Protocol

from open_mco.models import AtmosphericProfile, AtmosphericSourceMetadata


class AtmosphereProvider(Protocol):
    name: str

    def profile(
        self, latitude: float, longitude: float, valid_time: datetime
    ) -> AtmosphericProfile:
        """Return an SI atmospheric column and its provenance."""
        ...


def project_wind_onto_bearing(zonal_mps: float, meridional_mps: float, bearing_deg: float) -> float:
    """Project eastward/northward wind onto a clockwise-from-north route bearing."""

    angle = math.radians(bearing_deg)
    return zonal_mps * math.sin(angle) + meridional_mps * math.cos(angle)


class SyntheticAtmosphereProvider:
    """Small deterministic atmosphere for integration tests and UI development only."""

    name = "synthetic"

    def profile(
        self, latitude: float, longitude: float, valid_time: datetime
    ) -> AtmosphericProfile:
        now = datetime.now(UTC)
        source = AtmosphericSourceMetadata(
            provider=self.name,
            valid_time=valid_time,
            variables=("altitude", "temperature", "pressure", "u_wind", "v_wind"),
            horizontal_interpolation="constant synthetic column",
            vertical_interpolation="linear",
            retrieved_at=now,
            label="SYNTHETIC_NOT_FOR_ENGINEERING_USE",
        )
        return AtmosphericProfile(
            altitude_m=(0, 3000, 6000, 9000, 12000, 15000, 18000),
            temperature_k=(288.15, 268.65, 249.15, 229.65, 216.65, 216.65, 216.65),
            pressure_pa=(101325, 70109, 47181, 30742, 19399, 12045, 7505),
            zonal_wind_mps=(2, 5, 8, 12, 18, 22, 25),
            meridional_wind_mps=(1, 2, 3, 5, 8, 10, 12),
            latitude=latitude,
            longitude=longitude,
            valid_time=valid_time,
            source=source,
        )


class _NetworkWeatherProvider:
    name = "network"

    def __init__(self, *, network_enabled: bool = False, model: str | None = None) -> None:
        self.network_enabled = network_enabled
        self.model = model

    def profile(
        self, latitude: float, longitude: float, valid_time: datetime
    ) -> AtmosphericProfile:
        if not self.network_enabled:
            raise RuntimeError(
                f"{self.name} network access is disabled; enable it explicitly and configure credentials/cache"
            )
        raise NotImplementedError(
            f"{self.name} download and interpolation are adapter extension points; no network call was made"
        )


class HerbieHRRRProvider(_NetworkWeatherProvider):
    name = "hrrr"


class HerbieGEFSProvider(_NetworkWeatherProvider):
    name = "gefs"


class ERA5Provider(_NetworkWeatherProvider):
    name = "era5"
