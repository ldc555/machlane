"""Auditable grid-search planner that preserves every candidate disposition."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from open_mco.atmosphere import AtmosphereProvider
from open_mco.models import (
    AircraftModel,
    CandidateEvaluation,
    PlannerResult,
    PropagationRequest,
    Route,
    SegmentLimit,
)
from open_mco.physics import BoomPropagationEngine
from open_mco.terrain import TerrainProvider


class GridSearchPlanner:
    """Choose the fastest admissible candidate and retain all rejection reasons."""

    def __init__(
        self,
        *,
        atmosphere_provider: AtmosphereProvider,
        terrain_provider: TerrainProvider,
        propagation_engine: BoomPropagationEngine,
    ) -> None:
        self.atmosphere_provider = atmosphere_provider
        self.terrain_provider = terrain_provider
        self.propagation_engine = propagation_engine

    def plan(
        self,
        aircraft: AircraftModel,
        route: Route,
        *,
        mach_values: list[float],
        altitude_m: list[float],
        reliability_level: float,
        valid_time: datetime,
    ) -> PlannerResult:
        if not 0 < reliability_level <= 1:
            raise ValueError("reliability level must be in (0, 1]")
        if not mach_values or not altitude_m:
            raise ValueError("candidate Mach values and altitudes cannot be empty")
        limits: list[SegmentLimit] = []
        maximum_mach = float(aircraft.operating_limits.maximum_cruise_mach.value_si)
        minimum_mach = float(aircraft.operating_limits.minimum_sustained_supersonic_mach.value_si)
        ceiling = float(aircraft.operating_limits.service_ceiling.value_si)
        floor_value = aircraft.operating_limits.minimum_cruise_altitude
        floor = 0.0 if floor_value is None else float(floor_value.value_si)
        for segment in route.segments:
            midpoint_lat = (segment.start_latitude + segment.end_latitude) / 2
            midpoint_lon = (segment.start_longitude + segment.end_longitude) / 2
            atmosphere = self.atmosphere_provider.profile(midpoint_lat, midpoint_lon, valid_time)
            terrain = self.terrain_provider.profile(segment)
            evaluations: list[CandidateEvaluation] = []
            for mach in sorted(mach_values, reverse=True):
                for altitude in sorted(altitude_m, reverse=True):
                    if not minimum_mach <= mach <= maximum_mach:
                        evaluations.append(
                            CandidateEvaluation(
                                segment_id=segment.segment_id,
                                mach=mach,
                                altitude_m=altitude,
                                accepted=False,
                                reason="outside aircraft Mach limits",
                            )
                        )
                        continue
                    if not floor <= altitude <= ceiling:
                        evaluations.append(
                            CandidateEvaluation(
                                segment_id=segment.segment_id,
                                mach=mach,
                                altitude_m=altitude,
                                accepted=False,
                                reason="outside aircraft altitude limits",
                            )
                        )
                        continue
                    result = self.propagation_engine.evaluate(
                        PropagationRequest(
                            aircraft=aircraft,
                            segment=segment,
                            atmosphere=atmosphere,
                            terrain=terrain,
                            mach=mach,
                            altitude_m=altitude,
                        )
                    )
                    accepted = result.allowable is True
                    evaluations.append(
                        CandidateEvaluation(
                            segment_id=segment.segment_id,
                            mach=mach,
                            altitude_m=altitude,
                            accepted=accepted,
                            reason="synthetic engine accepted"
                            if accepted
                            else f"engine classified {result.classification}",
                            propagation=result,
                        )
                    )
            accepted_evaluations = [evaluation for evaluation in evaluations if evaluation.accepted]
            selected = max(
                accepted_evaluations, key=lambda item: (item.mach, item.altitude_m), default=None
            )
            limits.append(
                SegmentLimit(
                    segment_id=segment.segment_id,
                    selected_mach=None if selected is None else selected.mach,
                    selected_altitude_m=None if selected is None else selected.altitude_m,
                    status="FAIL" if selected is None else "PASS",
                    candidate_evaluations=tuple(evaluations),
                )
            )
        return PlannerResult(
            run_id=f"run-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}",
            created_at=datetime.now(UTC),
            engine_name=self.propagation_engine.name,
            engine_version=self.propagation_engine.version,
            segment_limits=tuple(limits),
            reliability_level=reliability_level,
            label="SYNTHETIC_NOT_FOR_ENGINEERING_USE"
            if self.propagation_engine.name == "mock_mco"
            else "UNVALIDATED",
        )
