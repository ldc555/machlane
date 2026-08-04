"""Propagation interfaces; only a clearly synthetic integration engine is implemented."""

from __future__ import annotations

from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from open_mco.atmosphere import project_wind_onto_bearing
from open_mco.models import (
    PropagationRequest,
    PropagationResult,
    SonicBoomCase,
    SonicBoomPrediction,
)


class BoomPropagationEngine(Protocol):
    name: str
    version: str

    def evaluate(self, request: PropagationRequest) -> PropagationResult:
        """Classify one normalized candidate without making a regulatory determination."""
        ...


class SonicBoomPropagationEngine(Protocol):
    """Boundary for a physical engine; the synthetic planner engine does not satisfy it."""

    name: str
    version: str

    def predict(self, case: SonicBoomCase) -> SonicBoomPrediction:
        """Propagate a near-field signature and return ground waveforms and metrics."""
        ...


class MockMCOEngine:
    """Deterministic synthetic boundary used only to exercise software integration."""

    name = "mock_mco"
    version = "1.0-synthetic"

    def evaluate(self, request: PropagationRequest) -> PropagationResult:
        terrain_peak = max(request.terrain.elevation_m)
        u = float(
            np.interp(
                request.altitude_m, request.atmosphere.altitude_m, request.atmosphere.zonal_wind_mps
            )
        )
        v = float(
            np.interp(
                request.altitude_m,
                request.atmosphere.altitude_m,
                request.atmosphere.meridional_wind_mps,
            )
        )
        along_wind = project_wind_onto_bearing(u, v, request.segment.bearing_deg)
        # This arbitrary deterministic score is an integration fixture, not a physical equation.
        synthetic_limit = 1.08 + min(max(request.altitude_m - 10_000, 0), 8_000) / 200_000
        synthetic_limit += along_wind / 2_000 - terrain_peak / 200_000
        allowable = request.mach <= synthetic_limit
        return PropagationResult(
            engine_name=self.name,
            engine_version=self.version,
            classification="SAFE" if allowable else "UNSAFE",
            allowable=allowable,
            label="SYNTHETIC_NOT_FOR_ENGINEERING_USE",
            metrics={"synthetic_cutoff_score": synthetic_limit, "along_route_wind_mps": along_wind},
            assumptions=("arbitrary deterministic integration boundary",),
            limitations=(
                "does not predict surface overpressure",
                "does not model primary or secondary sonic-boom propagation",
                "must never support an FAA-compliance claim",
            ),
        )


class FastMCOEngine:
    """Open-engine scaffold awaiting cited, reviewed and validated physics."""

    name = "fast_mco"
    version = "0.0-not-implemented"

    def effective_sound_speed(self, request: PropagationRequest) -> NDArray[np.float64]:
        raise NotImplementedError(
            "effective sound-speed physics requires a cited implementation and review"
        )

    def integrate_rays(self, request: PropagationRequest) -> NDArray[np.float64]:
        raise NotImplementedError("3-D ray equations require a cited implementation and review")

    def terrain_intersection(self, request: PropagationRequest) -> float | None:
        raise NotImplementedError("terrain intersection requires a cited implementation and review")

    def cutoff_boundary(self, request: PropagationRequest) -> float:
        raise NotImplementedError(
            "cutoff-boundary search requires a cited implementation and review"
        )

    def evaluate(self, request: PropagationRequest) -> PropagationResult:
        raise NotImplementedError("FastMCOEngine physics is not implemented or validated")

    def predict(self, case: SonicBoomCase) -> SonicBoomPrediction:
        raise NotImplementedError(
            "physical near-field-to-ground propagation is not implemented or validated"
        )
