"""Opt-in OpenSky observed-flight route adapter."""

from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import requests

from open_mco.models import Route, RouteSourceMetadata

from .geometry import route_from_waypoints
from .missions import Airport


class OpenSkyTrackProvider:
    """Import one observed OpenSky trajectory for an airport pair.

    OpenSky's track endpoint is experimental and downsampled. Network access is therefore disabled
    by default, credentials are read from the environment unless explicitly supplied, and every
    resulting route carries a prominent provenance and limitations record.
    """

    name = "opensky"
    api_base_url = "https://opensky-network.org/api"
    token_url = (
        "https://auth.opensky-network.org/auth/realms/opensky-network/"
        "protocol/openid-connect/token"
    )

    def __init__(
        self,
        *,
        network_enabled: bool = False,
        client_id: str | None = None,
        client_secret: str | None = None,
        timeout_seconds: float = 30,
        session: requests.Session | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("OpenSky timeout must be positive")
        self.network_enabled = network_enabled
        self.client_id = client_id or os.getenv("OPENSKY_CLIENT_ID")
        self.client_secret = client_secret or os.getenv("OPENSKY_CLIENT_SECRET")
        self.timeout_seconds = timeout_seconds
        self._session = session or requests.Session()
        self._access_token: str | None = None
        self._token_expires_at: datetime | None = None

    @property
    def credentials_configured(self) -> bool:
        """Return whether both OAuth2 client-credential values are available."""

        return bool(self.client_id and self.client_secret)

    def route_for_airports(
        self,
        origin: Airport,
        destination: Airport,
        *,
        begin: datetime,
        end: datetime,
        spacing_m: float = 185_200,
    ) -> Route:
        """Fetch the latest matching departure and normalize its observed trajectory."""

        self._validate_request(begin, end, spacing_m)
        departures = self._api_get(
            "/flights/departure",
            params={
                "airport": origin.icao,
                "begin": int(begin.timestamp()),
                "end": int(end.timestamp()),
            },
        )
        if departures is None:
            raise ValueError(
                f"OpenSky returned no departures from {origin.icao} in the requested interval"
            )
        flight = self._latest_matching_flight(departures, destination.icao)
        icao24 = str(flight["icao24"]).lower()
        first_seen = int(flight["firstSeen"])
        last_seen = int(flight["lastSeen"])
        track_time = first_seen + max(1, (last_seen - first_seen) // 2)
        track = self._api_get(
            "/tracks/all",
            params={"icao24": icao24, "time": track_time},
        )
        if not isinstance(track, dict):
            raise ValueError(f"OpenSky returned no track for aircraft {icao24}")
        waypoints = self._track_waypoints(track)
        checksum_payload = json.dumps(
            {"flight": flight, "track": track}, sort_keys=True, separators=(",", ":")
        ).encode()
        checksum = hashlib.sha256(checksum_payload).hexdigest()
        callsign = str(
            track.get("callsign")
            or track.get("calllsign")
            or flight.get("callsign")
            or ""
        ).strip()
        observed_start = datetime.fromtimestamp(
            int(track.get("startTime", first_seen)), tz=UTC
        )
        observed_end = datetime.fromtimestamp(int(track.get("endTime", last_seen)), tz=UTC)
        flight_id = f"{icao24}:{first_seen}"
        source_url = f"{self.api_base_url}/tracks/all?icao24={icao24}&time={track_time}"
        return route_from_waypoints(
            waypoints,
            spacing_m=spacing_m,
            name=f"OpenSky observed {origin.iata} → {destination.iata} · {callsign or icao24}",
            source=RouteSourceMetadata(
                provider=self.name,
                data_kind="observed_track",
                retrieved_at=datetime.now(UTC),
                source_url=source_url,
                label="OBSERVED_OPENSKY_TRACK_EXPERIMENTAL_NOT_A_FILED_ROUTE",
                flight_id=flight_id,
                callsign=callsign or None,
                origin_icao=origin.icao,
                destination_icao=destination.icao,
                observed_start=observed_start,
                observed_end=observed_end,
                point_count=len(waypoints),
                checksum=checksum,
                limitations=(
                    "OpenSky tracks are experimental, downsampled, and may have reception gaps.",
                    "Observed subsonic operations are context, not approved future supersonic routing.",
                    "This is an observed trajectory, not the filed flight plan or ATC clearance.",
                ),
            ),
        )

    def _validate_request(self, begin: datetime, end: datetime, spacing_m: float) -> None:
        if not self.network_enabled:
            raise RuntimeError("OpenSky network access is disabled; use an explicit fetch action")
        if os.getenv("MACHLANE_NETWORK_DISABLED") == "1":
            raise RuntimeError("network access is disabled by MACHLANE_NETWORK_DISABLED")
        if not self.credentials_configured:
            raise RuntimeError(
                "OpenSky OAuth credentials are missing; create an API client and set "
                "OPENSKY_CLIENT_ID and OPENSKY_CLIENT_SECRET"
            )
        if begin.tzinfo is None or end.tzinfo is None:
            raise ValueError("OpenSky begin and end must be timezone-aware")
        if end <= begin:
            raise ValueError("OpenSky end must be after begin")
        if end - begin > timedelta(days=2):
            raise ValueError("OpenSky airport-flight intervals cannot exceed two days")
        if spacing_m <= 0:
            raise ValueError("route spacing must be positive")

    def _access_headers(self, *, force_refresh: bool = False) -> dict[str, str]:
        now = datetime.now(UTC)
        if (
            not force_refresh
            and self._access_token
            and self._token_expires_at
            and now < self._token_expires_at
        ):
            return {"Authorization": f"Bearer {self._access_token}"}
        if not self.client_id or not self.client_secret:
            raise RuntimeError("OpenSky OAuth credentials are missing")
        try:
            response = self._session.post(
                self.token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise RuntimeError(f"OpenSky authentication failed: {exc}") from exc
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            raise RuntimeError("OpenSky authentication response did not include an access token")
        expires_in = payload.get("expires_in", 1800)
        try:
            lifetime = max(60, int(expires_in))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("OpenSky authentication returned an invalid token lifetime") from exc
        self._access_token = token
        self._token_expires_at = now + timedelta(seconds=lifetime - 30)
        return {"Authorization": f"Bearer {token}"}

    def _api_get(self, path: str, *, params: dict[str, str | int]) -> Any:
        url = f"{self.api_base_url}{path}"
        response: requests.Response | None = None
        try:
            for attempt in range(2):
                response = self._session.get(
                    url,
                    params=params,
                    headers={
                        **self._access_headers(force_refresh=attempt == 1),
                        "Accept": "application/json",
                        "User-Agent": "MachLane/0.1",
                    },
                    timeout=self.timeout_seconds,
                )
                if response.status_code != 401 or attempt == 1:
                    break
            if response is None:
                raise RuntimeError("OpenSky request did not produce a response")
            if response.status_code == 404:
                return None
            if response.status_code == 429:
                retry_after = response.headers.get("X-Rate-Limit-Retry-After-Seconds", "unknown")
                raise RuntimeError(f"OpenSky rate limit exhausted; retry after {retry_after} seconds")
            response.raise_for_status()
            return response.json()
        except RuntimeError:
            raise
        except (requests.RequestException, ValueError) as exc:
            raise RuntimeError(f"OpenSky request failed: {exc}") from exc

    @staticmethod
    def _latest_matching_flight(payload: Any, destination_icao: str) -> dict[str, Any]:
        if not isinstance(payload, list):
            raise ValueError("OpenSky departures response was not a list")
        candidates = []
        for value in payload:
            if not isinstance(value, dict):
                continue
            if value.get("estArrivalAirport") != destination_icao:
                continue
            if not value.get("icao24") or value.get("firstSeen") is None or value.get("lastSeen") is None:
                continue
            candidates.append(value)
        if not candidates:
            raise ValueError(f"OpenSky found no departure arriving at {destination_icao}")
        return max(candidates, key=lambda value: int(value["firstSeen"]))

    @staticmethod
    def _track_waypoints(payload: dict[str, Any]) -> list[tuple[float, float]]:
        path = payload.get("path")
        if not isinstance(path, list):
            raise ValueError("OpenSky track response has no waypoint path")
        waypoints: list[tuple[float, float]] = []
        for raw_point in path:
            if not isinstance(raw_point, list) or len(raw_point) < 6 or raw_point[5] is True:
                continue
            try:
                latitude = float(raw_point[1])
                longitude = float(raw_point[2])
            except (TypeError, ValueError):
                continue
            if not math.isfinite(latitude) or not math.isfinite(longitude):
                continue
            if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
                continue
            point = (latitude, longitude)
            if not waypoints or point != waypoints[-1]:
                waypoints.append(point)
        if len(waypoints) < 2:
            raise ValueError("OpenSky track contains fewer than two usable airborne waypoints")
        if len(waypoints) > 2_000:
            raise ValueError("OpenSky track exceeds the 2,000-waypoint safety limit")
        return waypoints
