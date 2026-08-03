"""Weather provider contracts with network access opt-in and provenance-first outputs."""

from __future__ import annotations

import math
import warnings
from datetime import UTC, datetime, timedelta
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import pandas as pd

from open_mco.aircraft.loader import sha256_file
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


class _HerbiePressureProvider:
    """Load a cached pressure-level subset through Herbie and extract point profiles."""

    name = "network"
    model = ""
    product = ""
    search = r":(?:HGT|TMP|UGRD|VGRD):[0-9]+ mb:"

    def __init__(
        self,
        *,
        network_enabled: bool = False,
        forecast_hour: int = 0,
        cache_dir: str | Path = "data/cache/herbie",
        priority: tuple[str, ...] = ("aws", "nomads", "google", "azure"),
        member: int | str | None = None,
    ) -> None:
        if forecast_hour < 0:
            raise ValueError("forecast hour cannot be negative")
        self.network_enabled = network_enabled
        self.forecast_hour = forecast_hour
        self.cache_dir = Path(cache_dir)
        self.priority = priority
        self.member = member
        self._dataset: Any | None = None
        self._herbie: Any | None = None
        self._cycle: datetime | None = None

    @staticmethod
    def _variable(dataset: Any, *names: str) -> Any:
        for name in names:
            if name in dataset:
                return dataset[name]
        raise ValueError(f"weather subset is missing required variable; tried {', '.join(names)}")

    @staticmethod
    def _pressure(dataset: Any) -> np.ndarray[Any, np.dtype[np.float64]]:
        for name in ("isobaricInhPa", "isobaricInPa", "pressure"):
            if name in dataset.coords:
                values = np.asarray(dataset.coords[name].values, dtype=float)
                return values if name == "isobaricInPa" else values * 100
        raise ValueError("weather subset has no recognized pressure-level coordinate")

    def _load(self, valid_time: datetime) -> tuple[Any, datetime]:
        if valid_time.tzinfo is None:
            raise ValueError("valid_time must be timezone-aware")
        cycle = valid_time.astimezone(UTC) - timedelta(hours=self.forecast_hour)
        if self._dataset is not None:
            if self._cycle != cycle:
                raise ValueError("one provider instance cannot mix model cycles")
            return self._dataset, cycle
        try:
            Herbie = import_module("herbie").Herbie
        except ImportError as exc:
            raise RuntimeError(
                "install MachLane's 'full' extra to use Herbie weather data"
            ) from exc
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "product": self.product,
            "fxx": self.forecast_hour,
            "priority": list(self.priority),
            "save_dir": self.cache_dir,
            "verbose": False,
        }
        if self.member is not None:
            kwargs["member"] = self.member
        herbie = Herbie(cycle.replace(tzinfo=None), **kwargs)
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="In a future version of xarray the default value for compat",
                category=FutureWarning,
                module="cfgrib.xarray_store",
            )
            dataset = herbie.xarray(self.search, remove_grib=False, errors="raise")
        if isinstance(dataset, list):
            try:
                xr = import_module("xarray")
            except ImportError as exc:
                raise RuntimeError("install MachLane's 'full' extra to merge model fields") from exc
            dataset = xr.merge(dataset, join="inner", compat="override")
            if not dataset.sizes.get("isobaricInhPa", 0):
                raise ValueError("weather variables have no common pressure levels")
        self._dataset = dataset
        self._herbie = herbie
        self._cycle = cycle
        return dataset, cycle

    def profile(
        self, latitude: float, longitude: float, valid_time: datetime
    ) -> AtmosphericProfile:
        if not self.network_enabled:
            raise RuntimeError(
                f"{self.name} network access is disabled; enable it explicitly and configure credentials/cache"
            )
        dataset, cycle = self._load(valid_time)
        point_frame = pd.DataFrame(
            {"latitude": [latitude], "longitude": [longitude], "stid": ["MACHLANE"]}
        )
        try:
            point = dataset.herbie.pick_points(point_frame).isel(point=0)
        except (AttributeError, KeyError, ValueError) as exc:
            raise ValueError("Herbie could not extract the requested point from this grid") from exc
        altitude = np.asarray(self._variable(point, "gh", "hgt").values, dtype=float).reshape(-1)
        temperature = np.asarray(self._variable(point, "t", "tmp").values, dtype=float).reshape(-1)
        zonal = np.asarray(self._variable(point, "u", "ugrd").values, dtype=float).reshape(-1)
        meridional = np.asarray(self._variable(point, "v", "vgrd").values, dtype=float).reshape(-1)
        pressure = self._pressure(point).reshape(-1)
        if len({len(altitude), len(temperature), len(zonal), len(meridional), len(pressure)}) != 1:
            raise ValueError("weather pressure-level arrays do not align")
        order = np.argsort(altitude)
        if self._herbie is None:
            raise RuntimeError("Herbie request metadata was not retained")
        local_path = Path(self._herbie.get_localFilePath(self.search))
        checksums = {local_path.name: sha256_file(local_path)} if local_path.exists() else {}
        remote = str(getattr(self._herbie, "grib", "")) or None
        resolved_member = str(getattr(self._herbie, "member", "") or "") or None
        return AtmosphericProfile(
            altitude_m=tuple(float(value) for value in altitude[order]),
            temperature_k=tuple(float(value) for value in temperature[order]),
            pressure_pa=tuple(float(value) for value in pressure[order]),
            zonal_wind_mps=tuple(float(value) for value in zonal[order]),
            meridional_wind_mps=tuple(float(value) for value in meridional[order]),
            latitude=latitude,
            longitude=longitude,
            valid_time=valid_time.astimezone(UTC),
            source=AtmosphericSourceMetadata(
                provider=f"{self.name}_via_herbie",
                model_cycle=cycle,
                forecast_hour=self.forecast_hour,
                ensemble_member=resolved_member,
                valid_time=valid_time.astimezone(UTC),
                variables=("geopotential_height", "temperature", "u_wind", "v_wind"),
                horizontal_interpolation="nearest model grid point via herbie.pick_points",
                vertical_interpolation="native pressure levels; no interpolation",
                retrieved_at=datetime.now(UTC),
                source_url=remote,
                checksums=checksums,
                label="REAL_MODEL_DATA_UNVALIDATED_FOR_OPERATIONAL_USE",
            ),
        )


class HerbieHRRRProvider(_HerbiePressureProvider):
    name = "hrrr"
    model = "hrrr"
    product = "prs"


class HerbieGEFSProvider(_HerbiePressureProvider):
    name = "gefs"
    model = "gefs"
    product = "atmos.5"


class ERA5Provider:
    """ERA5 configuration seam; CDS retrieval remains credential-gated."""

    name = "era5"

    def __init__(self, *, network_enabled: bool = False) -> None:
        self.network_enabled = network_enabled

    def profile(
        self, latitude: float, longitude: float, valid_time: datetime
    ) -> AtmosphericProfile:
        if not self.network_enabled:
            raise RuntimeError("ERA5 access is disabled; configure ~/.cdsapirc before enabling it")
        raise NotImplementedError(
            "ERA5 CDS request/export is not implemented; use HRRR/GEFS or a reviewed local ERA5 file"
        )
