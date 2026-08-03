"""Strict unit conversion helpers for aircraft workbook ingestion."""

from __future__ import annotations

from typing import Any

from pint import UnitRegistry

_UREG: UnitRegistry[Any] = UnitRegistry()
_ALIASES = {
    "-": "dimensionless",
    "count": "dimensionless",
    "Mach": "dimensionless",
    "probability": "dimensionless",
    "g": "dimensionless",
    "ft²": "foot ** 2",
    "lb": "pound",
    "lbf": "pound_force",
    "lb/hr": "pound / hour",
    "psf": "pound_force / foot ** 2",
    "deg": "degree",
    "kt": "knot",
}


def to_si(value: float | int | str | bool, unit: str) -> tuple[float | int | str | bool, str]:
    """Convert a numeric value to base SI without coercing textual identifiers."""

    normalized_unit = _ALIASES.get(unit, unit)
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        if normalized_unit != "dimensionless":
            raise ValueError(f"non-numeric value {value!r} cannot use unit {unit!r}")
        return value, "dimensionless"
    try:
        quantity = (value * _UREG(normalized_unit)).to_base_units()
    except Exception as exc:
        raise ValueError(f"unknown or incompatible unit {unit!r}") from exc
    return float(quantity.magnitude), f"{quantity.units:~}"
