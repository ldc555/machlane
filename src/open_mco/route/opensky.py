"""Opt-in OpenSky observed-flight route adapter."""

from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import requests
from pydantic import BaseModel, ConfigDict

from open_mco.models import Route, RouteObservation, RouteSourceMetadata

from .geometry import route_from_waypoints
from .missions import Airport


class OpenSkyRouteNotFoundError(ValueError):
    """No usable observed route matched the requested airport pair and time window."""


class OpenSkyObservedFlight(BaseModel):
    """One real OpenSky flight record suitable for explicit user selection."""

    model_config = ConfigDict(frozen=True)

    icao24: str
    callsign: str | None = None
    first_seen: datetime
    last_seen: datetime
    origin_icao: str
    destination_icao: str

    @property
    def flight_id(self) -> str:
        return f"{self.icao24}:{int(self.first_seen.timestamp())}"

    @classmethod
    def from_api(
        cls,
        payload: dict[str, Any],
        *,
        origin_icao: str,
        destination_icao: str,
    ) -> OpenSkyObservedFlight:
        try:
            icao24 = str(payload["icao24"]).lower()
            first_seen = datetime.fromtimestamp(int(payload["firstSeen"]), tz=UTC)
            last_seen = datetime.fromtimestamp(int(payload["lastSeen"]), tz=UTC)
        except (KeyError, TypeError, ValueError, OSError) as exc:
            raise ValueError("OpenSky flight record is missing its identity or timestamps") from exc
        if last_seen <= first_seen:
            raise ValueError("OpenSky flight record has a non-positive observation interval")
        callsign = str(payload.get("callsign") or "").strip() or None
        return cls(
            icao24=icao24,
            callsign=callsign,
            first_seen=first_seen,
            last_seen=last_seen,
            origin_icao=origin_icao,
            destination_icao=destination_icao,
        )


class OpenSkyTrackProvider:
    """Import one observed OpenSky trajectory for an airport pair.

    OpenSky's track endpoint is experimental and downsampled. Network access is therefore disabled
    by default, credentials are read from the environment unless explicitly supplied, and every
    resulting route carries a prominent provenance and limitations record.
    """

    name = "opensky"
    api_base_url = "https://opensky-network.org/api"
    token_url = (
        "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
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
        flights = self.observed_flights_for_airports(
            origin,
            destination,
            begin=begin,
            end=end,
        )
        if not flights:
            raise OpenSkyRouteNotFoundError(
                f"OpenSky found no {origin.icao} → {destination.icao} flight in the requested interval"
            )
        return self.route_for_observed_flight(
            origin,
            destination,
            flights[0],
            spacing_m=spacing_m,
        )

    def observed_flights_for_airports(
        self,
        origin: Airport,
        destination: Airport,
        *,
        begin: datetime,
        end: datetime,
    ) -> tuple[OpenSkyObservedFlight, ...]:
        """List real matching departures, newest first, over a bounded interval.

        OpenSky airport-flight requests are partitioned into at most two UTC days. Splitting the
        lookup keeps API usage predictable and lets the UI show only dates with an actual flight.
        """

        self._validate_flight_interval(begin, end)
        flights: dict[str, OpenSkyObservedFlight] = {}
        cursor = begin.astimezone(UTC)
        end_utc = end.astimezone(UTC)
        while cursor < end_utc:
            chunk_end = min(cursor + timedelta(days=2) - timedelta(seconds=1), end_utc)
            departures = self._api_get(
                "/flights/departure",
                params={
                    "airport": origin.icao,
                    "begin": int(cursor.timestamp()),
                    "end": int(chunk_end.timestamp()),
                },
            )
            if departures is not None:
                if not isinstance(departures, list):
                    raise ValueError("OpenSky departures response was not a list")
                for value in departures:
                    if not isinstance(value, dict):
                        continue
                    if value.get("estArrivalAirport") != destination.icao:
                        continue
                    try:
                        flight = OpenSkyObservedFlight.from_api(
                            value,
                            origin_icao=origin.icao,
                            destination_icao=destination.icao,
                        )
                    except ValueError:
                        continue
                    if begin <= flight.first_seen <= end:
                        flights[flight.flight_id] = flight
            cursor = chunk_end + timedelta(seconds=1)
        return tuple(sorted(flights.values(), key=lambda item: item.first_seen, reverse=True))

    def route_for_observed_flight(
        self,
        origin: Airport,
        destination: Airport,
        flight: OpenSkyObservedFlight,
        *,
        spacing_m: float = 185_200,
    ) -> Route:
        """Fetch the experimental track belonging to one explicitly selected flight."""

        self._validate_request(flight.first_seen, flight.last_seen, spacing_m)
        if flight.origin_icao != origin.icao or flight.destination_icao != destination.icao:
            raise ValueError("selected OpenSky flight does not match the requested airport pair")
        first_seen = int(flight.first_seen.timestamp())
        last_seen = int(flight.last_seen.timestamp())
        track_time = first_seen + max(1, (last_seen - first_seen) // 2)
        track = self._api_get(
            "/tracks/all",
            params={"icao24": flight.icao24, "time": track_time},
        )
        if not isinstance(track, dict):
            raise OpenSkyRouteNotFoundError(
                f"OpenSky returned no track for selected flight {flight.flight_id}"
            )
        observations = self._track_observations(track)
        waypoints: list[tuple[float, float]] = []
        for point in observations:
            coordinates = (point.latitude, point.longitude)
            if not waypoints or coordinates != waypoints[-1]:
                waypoints.append(coordinates)
        if len(waypoints) < 2:
            raise ValueError("OpenSky track contains fewer than two distinct positions")
        checksum_payload = json.dumps(
            {"flight": flight.model_dump(mode="json"), "track": track},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        checksum = hashlib.sha256(checksum_payload).hexdigest()
        callsign = str(
            track.get("callsign") or track.get("calllsign") or flight.callsign or ""
        ).strip()
        observed_start = observations[0].timestamp
        observed_end = observations[-1].timestamp
        source_url = (
            f"{self.api_base_url}/tracks/all?icao24={flight.icao24}&time={track_time}"
        )
        return route_from_waypoints(
            waypoints,
            spacing_m=spacing_m,
            name=(
                f"OpenSky observed {origin.iata} → {destination.iata} · "
                f"{callsign or flight.icao24}"
            ),
            observations=tuple(observations),
            source=RouteSourceMetadata(
                provider=self.name,
                data_kind="observed_track",
                retrieved_at=datetime.now(UTC),
                source_url=source_url,
                label="OBSERVED_OPENSKY_TRACK_EXPERIMENTAL_NOT_A_FILED_ROUTE",
                flight_id=flight.flight_id,
                callsign=callsign or None,
                origin_icao=origin.icao,
                destination_icao=destination.icao,
                observed_start=observed_start,
                observed_end=observed_end,
                point_count=len(observations),
                checksum=checksum,
                limitations=(
                    "OpenSky tracks are experimental, downsampled, and may have reception gaps.",
                    "Observed subsonic operations are context, not approved future supersonic routing.",
                    "This is an observed trajectory, not the filed flight plan or ATC clearance.",
                ),
            ),
        )

    def recent_route_for_airports(
        self,
        origin: Airport,
        destination: Airport,
        *,
        on_or_before: datetime,
        lookback_days: int = 7,
        spacing_m: float = 185_200,
    ) -> Route:
        """Find the most recent usable observed route in bounded one-day requests."""

        if on_or_before.tzinfo is None:
            raise ValueError("OpenSky lookback time must be timezone-aware")
        if not 1 <= lookback_days <= 30:
            raise ValueError("OpenSky lookback must be between 1 and 30 days")
        selected_day = on_or_before.astimezone(UTC).date()
        last_error: OpenSkyRouteNotFoundError | None = None
        for offset in range(lookback_days):
            day = selected_day - timedelta(days=offset)
            begin = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
            end = begin + timedelta(days=1) - timedelta(seconds=1)
            try:
                return self.route_for_airports(
                    origin,
                    destination,
                    begin=begin,
                    end=end,
                    spacing_m=spacing_m,
                )
            except OpenSkyRouteNotFoundError as exc:
                last_error = exc
        message = (
            f"OpenSky found no usable {origin.icao} → {destination.icao} observed route "
            f"in the {lookback_days} days ending {selected_day.isoformat()}"
        )
        raise OpenSkyRouteNotFoundError(message) from last_error

    def _validate_request(self, begin: datetime, end: datetime, spacing_m: float) -> None:
        self._validate_flight_interval(begin, end, maximum_days=2)
        if spacing_m <= 0:
            raise ValueError("route spacing must be positive")

    def _validate_flight_interval(
        self,
        begin: datetime,
        end: datetime,
        *,
        maximum_days: int = 30,
    ) -> None:
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
        if end - begin > timedelta(days=maximum_days):
            raise ValueError(
                f"OpenSky flight discovery cannot exceed {maximum_days} days"
            )

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
                raise RuntimeError(
                    f"OpenSky rate limit exhausted; retry after {retry_after} seconds"
                )
            response.raise_for_status()
            return response.json()
        except RuntimeError:
            raise
        except (requests.RequestException, ValueError) as exc:
            raise RuntimeError(f"OpenSky request failed: {exc}") from exc

    @staticmethod
    def _track_observations(payload: dict[str, Any]) -> list[RouteObservation]:
        path = payload.get("path")
        if not isinstance(path, list):
            raise ValueError("OpenSky track response has no waypoint path")
        observations: list[RouteObservation] = []
        for raw_point in path:
            if not isinstance(raw_point, list) or len(raw_point) < 6:
                continue
            try:
                timestamp = int(raw_point[0])
                latitude = float(raw_point[1])
                longitude = float(raw_point[2])
            except (TypeError, ValueError):
                continue
            if not math.isfinite(latitude) or not math.isfinite(longitude):
                continue
            if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
                continue
            altitude = OpenSkyTrackProvider._optional_finite_float(raw_point[3])
            true_track = OpenSkyTrackProvider._optional_finite_float(raw_point[4])
            if true_track is not None:
                true_track %= 360
            observation = RouteObservation(
                timestamp=datetime.fromtimestamp(timestamp, tz=UTC),
                latitude=latitude,
                longitude=longitude,
                barometric_altitude_m=altitude,
                true_track_deg=true_track,
                on_ground=bool(raw_point[5]),
            )
            observations.append(observation)
        if len(observations) < 2:
            raise ValueError("OpenSky track contains fewer than two usable trajectory points")
        if len(observations) > 2_000:
            raise ValueError("OpenSky track exceeds the 2,000-waypoint safety limit")
        observations.sort(key=lambda observation: observation.timestamp)
        return observations

    @staticmethod
    def _optional_finite_float(value: Any) -> float | None:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) else None
