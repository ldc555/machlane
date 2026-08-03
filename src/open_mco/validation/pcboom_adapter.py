"""Offline PCBoom case exchange; PCBoom itself is never bundled or invoked."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class PCBoomAdapter:
    name = "pcboom_offline"

    def __init__(self, *, version: str, configuration: dict[str, Any]) -> None:
        if not version.strip():
            raise ValueError("the separately installed PCBoom version must be recorded")
        self.version = version
        self.configuration = configuration

    def export_case(self, normalized_case: dict[str, Any], staging_directory: str | Path) -> Path:
        """Export normalized JSON for manual translation/use outside this repository."""

        target = Path(staging_directory)
        target.mkdir(parents=True, exist_ok=True)
        payload = {
            "adapter": self.name,
            "pcboom_version": self.version,
            "configuration": self.configuration,
            "normalized_case": normalized_case,
            "notice": "PCBoom is not included or invoked; use only under your NASA software agreement.",
        }
        case_path = target / "pcboom_case.json"
        case_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return case_path

    def import_results(self, result_path: str | Path) -> dict[str, Any]:
        """Import a user-supplied JSON summary without accepting restricted binaries/files."""

        path = Path(result_path)
        if not path.exists():
            raise FileNotFoundError(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "classification" not in data:
            raise ValueError("PCBoom result summary must be a JSON object with 'classification'")
        return data

    def compare(self, fast_result: dict[str, Any], pcboom_result: dict[str, Any]) -> dict[str, Any]:
        """Compare declared classifications and metrics without deciding regulatory validity."""

        return {
            "classification_match": fast_result.get("classification")
            == pcboom_result.get("classification"),
            "fast_result": fast_result,
            "pcboom_result": pcboom_result,
            "validation_status": "REVIEW_REQUIRED",
        }
