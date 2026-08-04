"""Curated future high-speed missions between real airport reference points."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from open_mco.models import Route, RouteSourceMetadata

from .geometry import route_from_waypoints

AIRPORT_SOURCE_URL = "https://ourairports.com/data/"
AIRPORT_SOURCE_RETRIEVED = "2026-08-04"


@dataclass(frozen=True)
class Airport:
    """A versioned airport reference point sourced from OurAirports."""

    icao: str
    iata: str
    name: str
    latitude: float
    longitude: float
    region: str


@dataclass(frozen=True)
class MissionDefinition:
    """An airport pair eligible for an observed OpenSky route lookup."""

    mission_id: str
    origin: Airport
    destination: Airport
    market: str
    domain: Literal["conus", "us_oceanic", "global_oceanic"]
    rationale: str

    @property
    def label(self) -> str:
        return f"{self.origin.iata} → {self.destination.iata} · {self.market}"

    @property
    def forecast_plan(self) -> str:
        if self.domain == "conus":
            return "HRRR regional + GEFS global"
        return "GEFS global"

    @property
    def hrrr_coverage(self) -> str:
        if self.domain == "conus":
            return "FULL ROUTE"
        if self.origin.region.startswith("US-") or self.destination.region.startswith("US-"):
            return "CONUS ENDPOINT ONLY"
        return "OUTSIDE DOMAIN"

    @property
    def terrain_plan(self) -> str:
        return "3DEP on U.S. land only"

    def build_route(self, *, spacing_m: float = 200_000) -> Route:
        """Build the shortest WGS-84 ellipsoidal path between the airport references."""

        return route_from_waypoints(
            [
                (self.origin.latitude, self.origin.longitude),
                (self.destination.latitude, self.destination.longitude),
            ],
            spacing_m=spacing_m,
            name=f"{self.label} conceptual geodesic mission",
            source=RouteSourceMetadata(
                provider="OurAirports + pyproj.Geod",
                data_kind="conceptual_geodesic",
                retrieved_at=datetime.fromisoformat(AIRPORT_SOURCE_RETRIEVED).replace(tzinfo=UTC),
                source_url=AIRPORT_SOURCE_URL,
                label="CONCEPTUAL_GEODESIC_NOT_A_FILED_OR_OBSERVED_ROUTE",
                origin_icao=self.origin.icao,
                destination_icao=self.destination.icao,
                point_count=2,
                limitations=(
                    "Shortest WGS-84 geodesic between airport reference points.",
                    "Not a filed route, ATC clearance, or observed aircraft trajectory.",
                ),
            ),
        )


_AIRPORTS = {
    airport.iata: airport
    for airport in (
        Airport(
            "KDFW", "DFW", "Dallas Fort Worth International Airport", 32.896801, -97.038002, "US-TX"
        ),
        Airport(
            "KBOS", "BOS", "Boston Logan International Airport", 42.361970, -71.007900, "US-MA"
        ),
        Airport(
            "KJFK", "JFK", "John F. Kennedy International Airport", 40.639447, -73.779317, "US-NY"
        ),
        Airport(
            "KLAX", "LAX", "Los Angeles International Airport", 33.942501, -118.407997, "US-CA"
        ),
        Airport(
            "PHNL", "HNL", "Daniel K. Inouye International Airport", 21.318387, -157.925670, "US-HI"
        ),
        Airport(
            "TJSJ", "SJU", "Luis Munoz Marin International Airport", 18.439400, -66.001801, "PR-U-A"
        ),
        Airport("EGLL", "LHR", "London Heathrow Airport", 51.470748, -0.459909, "GB-ENG"),
        Airport("RJAA", "NRT", "Narita International Airport", 35.768580, 140.388714, "JP-12"),
    )
}


def _mission(
    mission_id: str,
    origin: str,
    destination: str,
    market: str,
    domain: Literal["conus", "us_oceanic", "global_oceanic"],
    rationale: str,
) -> MissionDefinition:
    return MissionDefinition(
        mission_id=mission_id,
        origin=_AIRPORTS[origin],
        destination=_AIRPORTS[destination],
        market=market,
        domain=domain,
        rationale=rationale,
    )


_MISSIONS = (
    _mission(
        "dfw_jfk",
        "DFW",
        "JFK",
        "Dallas to East Coast",
        "conus",
        "Future overland high-speed research case between major U.S. hubs.",
    ),
    _mission(
        "dfw_lax",
        "DFW",
        "LAX",
        "Dallas to West Coast",
        "conus",
        "Future overland high-speed research case from North Texas to the Pacific coast.",
    ),
    _mission(
        "lax_jfk",
        "LAX",
        "JFK",
        "Coast to coast",
        "conus",
        "Long transcontinental research case with full CONUS forecast coverage.",
    ),
    _mission(
        "bos_hnl",
        "BOS",
        "HNL",
        "Boston to Hawaii",
        "us_oceanic",
        "Long U.S. oceanic concept connecting New England and Hawaii.",
    ),
    _mission(
        "dfw_sju",
        "DFW",
        "SJU",
        "U.S. territory",
        "us_oceanic",
        "Mixed overland-oceanic U.S. territory test case connecting Dallas and Puerto Rico.",
    ),
    _mission(
        "jfk_sju",
        "JFK",
        "SJU",
        "East Coast to U.S. territory",
        "us_oceanic",
        "Frequent long-haul test case between New York and Puerto Rico.",
    ),
    _mission(
        "jfk_lhr",
        "JFK",
        "LHR",
        "North Atlantic",
        "global_oceanic",
        "Classic transatlantic long-haul test case; an observed track is not a future NAT clearance.",
    ),
    _mission(
        "lax_nrt",
        "LAX",
        "NRT",
        "U.S. to Japan",
        "global_oceanic",
        "Transpacific test case that runs only when OpenSky returns an observed LAX–Narita track.",
    ),
)


def list_missions() -> tuple[MissionDefinition, ...]:
    """Return the stable catalog order used by the CLI and UI."""

    return _MISSIONS


def get_mission(mission_id: str) -> MissionDefinition:
    """Resolve one mission identifier or fail with a useful message."""

    for mission in _MISSIONS:
        if mission.mission_id == mission_id:
            return mission
    choices = ", ".join(mission.mission_id for mission in _MISSIONS)
    raise ValueError(f"unknown mission {mission_id!r}; choose one of: {choices}")
