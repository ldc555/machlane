"""Private on-disk cache for normalized OpenSky observed routes."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from open_mco.models import Route

_SAFE_MISSION_ID = re.compile(r"^[a-z0-9_]+$")


class OpenSkyRouteCache:
    """Store normalized observations outside version control and validate on read."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def load(
        self,
        mission_id: str,
        search_date: date,
        *,
        origin_icao: str,
        destination_icao: str,
    ) -> Route | None:
        """Return a matching observed route, or ``None`` for missing/invalid cache data."""

        path = self._path(mission_id, search_date)
        try:
            route = Route.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, ValueError):
            return None
        source = route.source
        if (
            source is None
            or source.provider != "opensky"
            or source.data_kind != "observed_track"
            or source.origin_icao != origin_icao
            or source.destination_icao != destination_icao
        ):
            return None
        return route

    def save(self, mission_id: str, search_date: date, route: Route) -> Path:
        """Atomically persist an already-normalized OpenSky observed route."""

        source = route.source
        if source is None or source.provider != "opensky" or source.data_kind != "observed_track":
            raise ValueError("only normalized OpenSky observed routes can be cached")
        path = self._path(mission_id, search_date)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(route.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(path)
        return path

    def _path(self, mission_id: str, search_date: date) -> Path:
        if not _SAFE_MISSION_ID.fullmatch(mission_id):
            raise ValueError(
                "mission ID must contain only lowercase letters, numbers, and underscores"
            )
        return self.root / f"{mission_id}__{search_date.isoformat()}.json"
