"""Clean-room research sonic-boom propagation components.

The implementation in this package is intentionally labelled ``UNVALIDATED``.  It produces
reproducible primary-ray research estimates from public equations and inputs; it is not PCBoom,
sBOOM, an FAA-approved method, or a substitute for independent validation.
"""

from .solver import OpenResearchRouteSolver, ResearchSolverUnavailableError

__all__ = ["OpenResearchRouteSolver", "ResearchSolverUnavailableError"]
