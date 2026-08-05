"""Weather provider contracts with network access opt-in and provenance-first outputs."""

from __future__ import annotations

import hashlib
import json
import math
import os
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


def profiles_at_points(
    provider: AtmosphereProvider,
    points: list[tuple[float, float]],
    valid_time: datetime,
) -> tuple[AtmosphericProfile, ...]:
    """Use a provider's vectorized path when available, with a portable fallback."""

    batch = getattr(provider, "profiles", None)
    if callable(batch):
        return tuple(batch(points, valid_time))
    return tuple(provider.profile(latitude, longitude, valid_time) for latitude, longitude in points)


def project_wind_onto_bearing(zonal_mps: float, meridional_mps: float, bearing_deg: float) -> float:
    """Project eastward/northward wind onto a clockwise-from-north route bearing."""

    angle = math.radians(bearing_deg)
    return zonal_mps * math.sin(angle) + meridional_mps * math.cos(angle)


class SyntheticAtmosphereProvider:
    """Small spatially varying atmosphere for integration tests and UI development only."""

    name = "synthetic"

    def profile(
        self, latitude: float, longitude: float, valid_time: datetime
    ) -> AtmosphericProfile:
        longitude_phase = math.radians(longitude + 97)
        latitude_phase = math.radians((latitude - 37) * 3)
        temperature_offset = 2.4 * math.sin(2 * longitude_phase) + 1.2 * math.sin(
            latitude_phase
        )
        pressure_scale = 1 + 0.012 * math.cos(longitude_phase) + 0.006 * math.sin(
            latitude_phase
        )
        zonal_offset = 10 * math.sin(2 * longitude_phase) + 0.35 * (latitude - 37)
        meridional_offset = 7 * math.sin(latitude_phase) - 4 * math.sin(longitude_phase)
        base_temperature = (288.15, 268.65, 249.15, 229.65, 216.65, 216.65, 216.65)
        base_pressure = (101325, 70109, 47181, 30742, 19399, 12045, 7505)
        base_zonal_wind = (2, 5, 8, 12, 18, 22, 25)
        base_meridional_wind = (1, 2, 3, 5, 8, 10, 12)
        now = datetime.now(UTC)
        source = AtmosphericSourceMetadata(
            provider=self.name,
            valid_time=valid_time,
            variables=("altitude", "temperature", "pressure", "u_wind", "v_wind"),
            horizontal_interpolation="smooth synthetic spatial field",
            vertical_interpolation="linear",
            retrieved_at=now,
            label="SYNTHETIC_NOT_FOR_ENGINEERING_USE",
        )
        return AtmosphericProfile(
            altitude_m=(0, 3000, 6000, 9000, 12000, 15000, 18000),
            temperature_k=tuple(value + temperature_offset for value in base_temperature),
            pressure_pa=tuple(value * pressure_scale for value in base_pressure),
            zonal_wind_mps=tuple(value + zonal_offset for value in base_zonal_wind),
            meridional_wind_mps=tuple(value + meridional_offset for value in base_meridional_wind),
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
    pressure_levels = (100, 150, 200, 250, 300, 400, 500, 700, 850, 1000)
    search = (
        r":(?:HGT|TMP|UGRD|VGRD|RH):"
        r"(?:100|150|200|250|300|400|500|700|850|1000) mb:"
    )

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
        self._checksums: dict[str, str] | None = None
        self._grid_tree: Any | None = None
        self._grid_shape: tuple[int, ...] | None = None
        self._grid_dims: tuple[str, ...] | None = None

    @staticmethod
    def _variable(dataset: Any, *names: str) -> Any:
        for name in names:
            if name in dataset:
                return dataset[name]
        raise ValueError(f"weather subset is missing required variable; tried {', '.join(names)}")

    @staticmethod
    def _pressure(dataset: Any) -> np.ndarray[Any, np.dtype[np.float64]]:
        for name in ("isobaricInhPa", "isobaricInPa", "pressure", "pressure_level"):
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
            dataset = xr.merge(
                dataset,
                join="exact",
                compat="override",
                combine_attrs="override",
            )
            if not dataset.sizes.get("isobaricInhPa", 0):
                raise ValueError("weather variables have no common pressure levels")
        # cfgrib arrays are lazy. Route sampling touches many different grid cells, so retaining
        # the compact selected pressure-level subset in memory avoids re-decoding GRIB messages
        # once per point.
        load = getattr(dataset, "load", None)
        if callable(load):
            load()
        self._dataset = dataset
        self._herbie = herbie
        self._cycle = cycle
        return dataset, cycle

    def _validate_request(self, valid_time: datetime) -> None:
        if not self.network_enabled:
            raise RuntimeError(
                f"{self.name} network access is disabled; enable it explicitly and configure credentials/cache"
            )
        if os.getenv("MACHLANE_NETWORK_DISABLED") == "1":
            raise RuntimeError("network access is disabled by MACHLANE_NETWORK_DISABLED")

    def _select_points(self, dataset: Any, points: list[tuple[float, float]]) -> Any:
        """Select nearest model cells in one vectorized operation.

        Regular GEFS coordinates use xarray's indexed selection. HRRR has a curvilinear
        two-dimensional grid, so a cached spherical KD-tree avoids rebuilding Herbie's
        point matcher for every planner stage.
        """

        try:
            xr = import_module("xarray")
        except ImportError as exc:
            raise RuntimeError("install MachLane's 'full' extra to select model points") from exc
        coordinates = getattr(dataset, "coords", {})
        latitude_name = next(
            (name for name in ("latitude", "lat") if name in coordinates), None
        )
        longitude_name = next(
            (name for name in ("longitude", "lon") if name in coordinates), None
        )
        if latitude_name is None or longitude_name is None:
            point_frame = pd.DataFrame(
                {
                    "latitude": [latitude_value for latitude_value, _ in points],
                    "longitude": [longitude_value for _, longitude_value in points],
                    "stid": [
                        "MACHLANE" if len(points) == 1 else f"MACHLANE-{index:04d}"
                        for index in range(len(points))
                    ],
                }
            )
            return dataset.herbie.pick_points(point_frame)
        latitude = np.asarray(coordinates[latitude_name].values, dtype=float)
        longitude = np.asarray(coordinates[longitude_name].values, dtype=float)
        target_latitude = [latitude_value for latitude_value, _ in points]
        target_longitude = [longitude_value for _, longitude_value in points]
        if latitude.ndim == longitude.ndim == 1:
            return dataset.sel(
                {
                    latitude_name: xr.DataArray(target_latitude, dims="point"),
                    longitude_name: xr.DataArray(target_longitude, dims="point"),
                },
                method="nearest",
            )
        if latitude.shape != longitude.shape or latitude.ndim != 2:
            raise ValueError("weather grid latitude/longitude coordinates do not align")
        if self._grid_tree is None:
            try:
                cKDTree = import_module("scipy.spatial").cKDTree
            except ImportError as exc:
                raise RuntimeError("install MachLane's 'full' extra to select HRRR points") from exc

            def unit_sphere(
                latitudes: np.ndarray[Any, np.dtype[np.float64]],
                longitudes: np.ndarray[Any, np.dtype[np.float64]],
            ) -> np.ndarray[Any, np.dtype[np.float64]]:
                latitude_rad = np.radians(latitudes)
                longitude_rad = np.radians(longitudes)
                return np.column_stack(
                    (
                        np.cos(latitude_rad) * np.cos(longitude_rad),
                        np.cos(latitude_rad) * np.sin(longitude_rad),
                        np.sin(latitude_rad),
                    )
                )

            self._grid_tree = cKDTree(unit_sphere(latitude.ravel(), longitude.ravel()))
            self._grid_shape = latitude.shape
            self._grid_dims = tuple(coordinates[latitude_name].dims)
        if self._grid_shape is None or self._grid_dims is None:
            raise RuntimeError("weather grid index metadata was not retained")
        target_latitude_array = np.asarray(target_latitude)
        target_longitude_array = np.asarray(target_longitude)
        target_latitude_rad = np.radians(target_latitude_array)
        target_longitude_rad = np.radians(target_longitude_array)
        target_xyz = np.column_stack(
            (
                np.cos(target_latitude_rad) * np.cos(target_longitude_rad),
                np.cos(target_latitude_rad) * np.sin(target_longitude_rad),
                np.sin(target_latitude_rad),
            )
        )
        _, flat_indices = self._grid_tree.query(target_xyz)
        row_indices, column_indices = np.unravel_index(flat_indices, self._grid_shape)
        return dataset.isel(
            {
                self._grid_dims[0]: xr.DataArray(row_indices, dims="point"),
                self._grid_dims[1]: xr.DataArray(column_indices, dims="point"),
            }
        )

    def _profile_from_point(
        self,
        point: Any,
        *,
        latitude: float,
        longitude: float,
        valid_time: datetime,
        cycle: datetime,
    ) -> AtmosphericProfile:
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError("latitude/longitude are outside valid ranges")
        altitude = np.asarray(self._variable(point, "gh", "hgt").values, dtype=float).reshape(-1)
        temperature = np.asarray(self._variable(point, "t", "tmp").values, dtype=float).reshape(-1)
        zonal = np.asarray(self._variable(point, "u", "ugrd").values, dtype=float).reshape(-1)
        meridional = np.asarray(self._variable(point, "v", "vgrd").values, dtype=float).reshape(-1)
        humidity = np.asarray(self._variable(point, "r", "rh").values, dtype=float).reshape(-1)
        if np.nanmax(humidity) > 1:
            humidity[:] = humidity / 100
        pressure = self._pressure(point).reshape(-1)
        if (
            len(
                {
                    len(altitude),
                    len(temperature),
                    len(zonal),
                    len(meridional),
                    len(humidity),
                    len(pressure),
                }
            )
            != 1
        ):
            raise ValueError("weather pressure-level arrays do not align")
        order = np.argsort(altitude)
        if self._herbie is None:
            raise RuntimeError("Herbie request metadata was not retained")
        if self._checksums is None:
            local_path = Path(self._herbie.get_localFilePath(self.search))
            self._checksums = (
                {local_path.name: sha256_file(local_path)} if local_path.exists() else {}
            )
        remote = str(getattr(self._herbie, "grib", "")) or None
        resolved_member = str(getattr(self._herbie, "member", "") or "") or None
        return AtmosphericProfile(
            altitude_m=tuple(float(value) for value in altitude[order]),
            temperature_k=tuple(float(value) for value in temperature[order]),
            pressure_pa=tuple(float(value) for value in pressure[order]),
            zonal_wind_mps=tuple(float(value) for value in zonal[order]),
            meridional_wind_mps=tuple(float(value) for value in meridional[order]),
            humidity_fraction=tuple(float(value) for value in humidity[order]),
            latitude=latitude,
            longitude=longitude,
            valid_time=valid_time.astimezone(UTC),
            source=AtmosphericSourceMetadata(
                provider=f"{self.name}_via_herbie",
                model_cycle=cycle,
                forecast_hour=self.forecast_hour,
                ensemble_member=resolved_member,
                valid_time=valid_time.astimezone(UTC),
                variables=(
                    "geopotential_height",
                    "temperature",
                    "u_wind",
                    "v_wind",
                    "relative_humidity",
                ),
                horizontal_interpolation="nearest model grid point via herbie.pick_points",
                vertical_interpolation="native pressure levels; no interpolation",
                retrieved_at=datetime.now(UTC),
                source_url=remote,
                checksums=self._checksums,
                label="REAL_MODEL_DATA_UNVALIDATED_FOR_OPERATIONAL_USE",
            ),
        )

    def profiles(
        self,
        points: list[tuple[float, float]],
        valid_time: datetime,
    ) -> tuple[AtmosphericProfile, ...]:
        """Extract many route columns in one nearest-grid operation."""

        self._validate_request(valid_time)
        if not points:
            return ()
        for latitude, longitude in points:
            if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
                raise ValueError("latitude/longitude are outside valid ranges")
        dataset, cycle = self._load(valid_time)
        try:
            selected = self._select_points(dataset, points)
        except (AttributeError, KeyError, ValueError) as exc:
            raise ValueError("MachLane could not extract the requested points from this grid") from exc
        return tuple(
            self._profile_from_point(
                selected.isel(point=index),
                latitude=latitude,
                longitude=longitude,
                valid_time=valid_time,
                cycle=cycle,
            )
            for index, (latitude, longitude) in enumerate(points)
        )

    def profile(
        self, latitude: float, longitude: float, valid_time: datetime
    ) -> AtmosphericProfile:
        return self.profiles([(latitude, longitude)], valid_time)[0]


class HerbieHRRRProvider(_HerbiePressureProvider):
    name = "hrrr"
    model = "hrrr"
    product = "prs"


class HerbieGEFSProvider(_HerbiePressureProvider):
    name = "gefs"
    model = "gefs"
    product = "atmos.5"


class ERA5Provider:
    """Retrieve one historical ERA5 pressure-level column through the CDS API."""

    name = "era5"
    dataset = "reanalysis-era5-pressure-levels"
    pressure_levels_hpa = (
        1,
        2,
        3,
        5,
        7,
        10,
        20,
        30,
        50,
        70,
        100,
        125,
        150,
        175,
        200,
        225,
        250,
        300,
        350,
        400,
        450,
        500,
        550,
        600,
        650,
        700,
        750,
        775,
        800,
        825,
        850,
        875,
        900,
        925,
        950,
        975,
        1000,
    )

    def __init__(
        self,
        *,
        network_enabled: bool = False,
        cache_dir: str | Path = "data/cache/era5",
    ) -> None:
        self.network_enabled = network_enabled
        self.cache_dir = Path(cache_dir)

    def profile(
        self, latitude: float, longitude: float, valid_time: datetime
    ) -> AtmosphericProfile:
        if not self.network_enabled:
            raise RuntimeError("ERA5 access is disabled; configure ~/.cdsapirc before enabling it")
        if os.getenv("MACHLANE_NETWORK_DISABLED") == "1":
            raise RuntimeError("network access is disabled by MACHLANE_NETWORK_DISABLED")
        if valid_time.tzinfo is None:
            raise ValueError("valid_time must be timezone-aware")
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError("latitude/longitude are outside valid ranges")
        valid_utc = valid_time.astimezone(UTC)
        if valid_utc.minute or valid_utc.second or valid_utc.microsecond:
            raise ValueError("ERA5 valid_time must be aligned to a whole UTC hour")
        try:
            cdsapi = import_module("cdsapi")
            xr = import_module("xarray")
        except ImportError as exc:
            raise RuntimeError("install MachLane's 'full' extra to use ERA5") from exc

        north = min(90.0, latitude + 0.125)
        south = max(-90.0, latitude - 0.125)
        west = max(-180.0, longitude - 0.125)
        east = min(180.0, longitude + 0.125)
        request: dict[str, Any] = {
            "product_type": ["reanalysis"],
            "variable": [
                "geopotential",
                "temperature",
                "u_component_of_wind",
                "v_component_of_wind",
                "relative_humidity",
            ],
            "pressure_level": [str(level) for level in self.pressure_levels_hpa],
            "year": [f"{valid_utc.year:04d}"],
            "month": [f"{valid_utc.month:02d}"],
            "day": [f"{valid_utc.day:02d}"],
            "time": [f"{valid_utc.hour:02d}:00"],
            "data_format": "grib",
            "area": [north, west, south, east],
            "grid": [0.25, 0.25],
        }
        request_key = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        target = self.cache_dir / f"era5_{request_key[:16]}.grib"
        if not target.exists():
            partial = target.with_suffix(".grib.part")
            try:
                client = cdsapi.Client()
                client.retrieve(self.dataset, request, str(partial))
                partial.replace(target)
            except Exception as exc:
                partial.unlink(missing_ok=True)
                raise RuntimeError(
                    "ERA5 retrieval failed; verify ~/.cdsapirc, dataset terms, date, and network access"
                ) from exc
        try:
            dataset = xr.open_dataset(
                target,
                engine="cfgrib",
                backend_kwargs={"indexpath": ""},
            )
            try:
                point = dataset.sel(
                    latitude=latitude, longitude=longitude, method="nearest"
                ).squeeze()
                geopotential = np.asarray(
                    _HerbiePressureProvider._variable(point, "z", "geopotential").values,
                    dtype=float,
                ).reshape(-1)
                temperature = np.asarray(
                    _HerbiePressureProvider._variable(point, "t", "temperature").values,
                    dtype=float,
                ).reshape(-1)
                zonal = np.asarray(
                    _HerbiePressureProvider._variable(point, "u", "u_component_of_wind").values,
                    dtype=float,
                ).reshape(-1)
                meridional = np.asarray(
                    _HerbiePressureProvider._variable(point, "v", "v_component_of_wind").values,
                    dtype=float,
                ).reshape(-1)
                humidity = np.asarray(
                    _HerbiePressureProvider._variable(point, "r", "relative_humidity").values,
                    dtype=float,
                ).reshape(-1)
                if np.nanmax(humidity) > 1:
                    humidity[:] = humidity / 100
                pressure = _HerbiePressureProvider._pressure(point).reshape(-1)
            finally:
                close = getattr(dataset, "close", None)
                if callable(close):
                    close()
        except (AttributeError, KeyError, ValueError) as exc:
            raise ValueError(
                "ERA5 file does not contain one aligned pressure-level column"
            ) from exc
        altitude = geopotential / 9.80665
        if (
            len(
                {
                    len(altitude),
                    len(temperature),
                    len(zonal),
                    len(meridional),
                    len(humidity),
                    len(pressure),
                }
            )
            != 1
        ):
            raise ValueError("ERA5 pressure-level arrays do not align")
        order = np.argsort(altitude)
        return AtmosphericProfile(
            altitude_m=tuple(float(value) for value in altitude[order]),
            temperature_k=tuple(float(value) for value in temperature[order]),
            pressure_pa=tuple(float(value) for value in pressure[order]),
            zonal_wind_mps=tuple(float(value) for value in zonal[order]),
            meridional_wind_mps=tuple(float(value) for value in meridional[order]),
            humidity_fraction=tuple(float(value) for value in humidity[order]),
            latitude=latitude,
            longitude=longitude,
            valid_time=valid_utc,
            source=AtmosphericSourceMetadata(
                provider="era5_via_cdsapi",
                valid_time=valid_utc,
                variables=(
                    "geopotential",
                    "temperature",
                    "u_wind",
                    "v_wind",
                    "relative_humidity",
                ),
                horizontal_interpolation="nearest ERA5 0.25-degree grid point",
                vertical_interpolation="native pressure levels; no interpolation",
                retrieved_at=datetime.now(UTC),
                source_url="https://cds.climate.copernicus.eu/datasets/reanalysis-era5-pressure-levels",
                checksums={target.name: sha256_file(target)},
                label="REAL_REANALYSIS_DATA_UNVALIDATED_FOR_OPERATIONAL_USE",
            ),
        )
