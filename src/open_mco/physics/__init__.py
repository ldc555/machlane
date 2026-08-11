"""Replaceable propagation interfaces and normalized pressure signatures."""

from .atmospheric import PreparedAtmosphericColumn, prepare_moist_thermodynamics
from .engines import (
    BoomPropagationEngine,
    FastMCOEngine,
    MockMCOEngine,
    SonicBoomPropagationEngine,
)
from .readiness import BoomReadinessReport, ReadinessStatus, assess_boom_readiness
from .route_analysis import (
    ExternalRouteSolver,
    PhysicalRouteAnalysis,
    RouteCandidateAnalysis,
    RouteSolverProvenance,
    SurfaceFootprintSample,
    build_physical_route_request,
    evidence_zip,
    footprint_geojson,
    load_physical_route_analysis,
    request_checksum,
    surface_sample_rows,
)
from .signatures import NearFieldSignatureError, load_near_field_signature

__all__ = [
    "BoomPropagationEngine",
    "BoomReadinessReport",
    "ExternalRouteSolver",
    "FastMCOEngine",
    "MockMCOEngine",
    "NearFieldSignatureError",
    "PreparedAtmosphericColumn",
    "PhysicalRouteAnalysis",
    "ReadinessStatus",
    "RouteCandidateAnalysis",
    "RouteSolverProvenance",
    "SonicBoomPropagationEngine",
    "SurfaceFootprintSample",
    "assess_boom_readiness",
    "build_physical_route_request",
    "evidence_zip",
    "footprint_geojson",
    "load_physical_route_analysis",
    "load_near_field_signature",
    "prepare_moist_thermodynamics",
    "request_checksum",
    "surface_sample_rows",
]
