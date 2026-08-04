"""Strict near-field pressure-signature interchange for CFD and propagation tools."""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path

from open_mco.aircraft.loader import sha256_file
from open_mco.models import NearFieldSignature, NearFieldSourceMetadata

REQUIRED_COLUMNS = ("distance_m", "overpressure_pa")


class NearFieldSignatureError(ValueError):
    """Raised when a near-field signature cannot cross the normalized boundary."""


def read_near_field_samples(path: str | Path) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Read the deliberately small, SI-only CSV interchange format."""

    source_path = Path(path)
    if not source_path.exists():
        raise NearFieldSignatureError(f"near-field signature not found: {source_path}")
    with source_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or tuple(reader.fieldnames) != REQUIRED_COLUMNS:
            raise NearFieldSignatureError(
                "near-field CSV columns must be exactly: distance_m, overpressure_pa"
            )
        distance: list[float] = []
        pressure: list[float] = []
        for row_number, row in enumerate(reader, start=2):
            try:
                distance.append(float(row["distance_m"]))
                pressure.append(float(row["overpressure_pa"]))
            except (TypeError, ValueError) as exc:
                raise NearFieldSignatureError(
                    f"near-field CSV row {row_number} contains a non-numeric value"
                ) from exc
    if len(distance) < 3:
        raise NearFieldSignatureError("near-field CSV requires at least three samples")
    if not any(abs(value) > 0 for value in pressure):
        raise NearFieldSignatureError("near-field CSV is still an all-zero template")
    return tuple(distance), tuple(pressure)


def load_near_field_signature(
    path: str | Path,
    *,
    reference_distance_m: float,
    azimuth_deg: float,
    flight_mach: float,
    flight_altitude_m: float,
    provider: str,
    solver_version: str | None = None,
    configuration_checksum: str | None = None,
) -> NearFieldSignature:
    """Normalize an external CFD/measurement signature and retain its provenance."""

    source_path = Path(path)
    distance, pressure = read_near_field_samples(source_path)
    return NearFieldSignature(
        distance_m=distance,
        overpressure_pa=pressure,
        reference_distance_m=reference_distance_m,
        azimuth_deg=azimuth_deg,
        flight_mach=flight_mach,
        flight_altitude_m=flight_altitude_m,
        source=NearFieldSourceMetadata(
            provider=provider,
            solver_version=solver_version,
            source_document=str(source_path.resolve()),
            retrieved_at=datetime.now(UTC),
            checksum=sha256_file(source_path),
            configuration_checksum=configuration_checksum,
        ),
    )
