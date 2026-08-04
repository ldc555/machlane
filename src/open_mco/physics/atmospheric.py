"""Optional unit-aware atmospheric preparation; this is not boom propagation."""

from __future__ import annotations

from importlib import import_module

import numpy as np

from open_mco.models import AtmosphericProfile, FrozenModel


class PreparedAtmosphericColumn(FrozenModel):
    altitude_m: tuple[float, ...]
    density_kg_m3: tuple[float, ...]
    water_vapor_mixing_ratio: tuple[float, ...]


def prepare_moist_thermodynamics(profile: AtmosphericProfile) -> PreparedAtmosphericColumn:
    """Derive density and water-vapor mixing ratio with MetPy's cited equations."""

    if profile.humidity_fraction is None:
        raise ValueError("relative humidity is required for propagation-atmosphere preparation")
    try:
        mpcalc = import_module("metpy.calc")
        units = import_module("metpy.units").units
    except ImportError as exc:
        raise RuntimeError('install the optional physics tools with `pip install -e ".[physics]"`') from exc

    pressure = np.asarray(profile.pressure_pa) * units.pascal
    temperature = np.asarray(profile.temperature_k) * units.kelvin
    humidity = np.asarray(profile.humidity_fraction) * units.dimensionless
    mixing_ratio = mpcalc.mixing_ratio_from_relative_humidity(
        pressure=pressure,
        temperature=temperature,
        relative_humidity=humidity,
        phase="auto",
    )
    density = mpcalc.density(
        pressure=pressure,
        temperature=temperature,
        mixing_ratio=mixing_ratio,
    )
    return PreparedAtmosphericColumn(
        altitude_m=profile.altitude_m,
        density_kg_m3=tuple(float(value) for value in density.to("kg/m^3").magnitude),
        water_vapor_mixing_ratio=tuple(float(value) for value in mixing_ratio.magnitude),
    )
