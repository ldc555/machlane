"""Offline staging boundary for open-source SU2 near-field CFD cases."""

from __future__ import annotations

import json
import shutil
from importlib import import_module
from pathlib import Path
from typing import Any

from open_mco.aircraft.loader import sha256_file
from open_mco.models import NearFieldSignature
from open_mco.physics.signatures import load_near_field_signature


class SU2NearFieldAdapter:
    """Record and inspect SU2 inputs without silently running a high-cost CFD job."""

    name = "su2_near_field_offline"

    def __init__(self, *, version: str, executable: str = "SU2_CFD") -> None:
        if not version.strip():
            raise ValueError("the SU2 version must be recorded")
        self.version = version
        self.executable = executable

    @property
    def installed_executable(self) -> str | None:
        return shutil.which(self.executable)

    def inspect_mesh(self, mesh_path: str | Path) -> dict[str, int]:
        """Read a mesh through optional meshio and report its basic dimensions."""

        try:
            meshio = import_module("meshio")
        except ImportError as exc:
            raise RuntimeError(
                'install the optional physics tools with `pip install -e ".[physics]"`'
            ) from exc
        mesh = meshio.read(Path(mesh_path))
        cell_count = sum(len(block.data) for block in mesh.cells)
        return {"points": len(mesh.points), "cells": cell_count}

    def stage_case(
        self,
        *,
        config_path: str | Path,
        mesh_path: str | Path,
        staging_directory: str | Path,
        operating_point: dict[str, float],
    ) -> Path:
        """Write a reproducible manifest; execution remains an explicit expert action."""

        config = Path(config_path)
        mesh = Path(mesh_path)
        for label, path in (("SU2 configuration", config), ("SU2 mesh", mesh)):
            if not path.exists():
                raise FileNotFoundError(f"{label} not found: {path}")
        required = {"mach", "altitude_m", "reference_distance_m", "azimuth_deg"}
        missing = sorted(required - operating_point.keys())
        if missing:
            raise ValueError(f"missing SU2 operating-point fields: {', '.join(missing)}")
        target = Path(staging_directory)
        target.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "adapter": self.name,
            "su2_version": self.version,
            "executable": self.installed_executable,
            "config": {"path": str(config.resolve()), "sha256": sha256_file(config)},
            "mesh": {"path": str(mesh.resolve()), "sha256": sha256_file(mesh)},
            "operating_point": operating_point,
            "expected_signature": {
                "format": "CSV",
                "columns": ["distance_m", "overpressure_pa"],
                "minimum_samples": 3,
            },
            "execution_status": "NOT_RUN",
            "notice": (
                "This manifest does not prove mesh adequacy, CFD convergence, signature accuracy, "
                "or atmospheric propagation validity."
            ),
        }
        manifest = target / "su2_near_field_case.json"
        manifest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return manifest

    def import_signature(
        self,
        path: str | Path,
        *,
        reference_distance_m: float,
        azimuth_deg: float,
        flight_mach: float,
        flight_altitude_m: float,
        configuration_checksum: str,
    ) -> NearFieldSignature:
        return load_near_field_signature(
            path,
            reference_distance_m=reference_distance_m,
            azimuth_deg=azimuth_deg,
            flight_mach=flight_mach,
            flight_altitude_m=flight_altitude_m,
            provider=self.name,
            solver_version=self.version,
            configuration_checksum=configuration_checksum,
        )
