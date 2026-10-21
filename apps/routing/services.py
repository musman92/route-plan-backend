from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import requests

from apps.routing.geometry import Track, overlap_ratio, track_from_steps, via_points
from apps.ttlcache import TtlCache

logger = logging.getLogger(__name__)

USER_AGENT = "weather-aware-truck-routing/1.0"
OSRM_URL = "https://router.project-osrm.org/route/v1/driving"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
CACHE_SECONDS = 15 * 60
MAX_VIA_REQUESTS = 8

_geocode_cache: TtlCache[Place] = TtlCache(CACHE_SECONDS)
_route_cache: TtlCache[list[RouteOption]] = TtlCache(CACHE_SECONDS)
_last_nominatim = 0.0


class RoutingError(Exception):
    pass


class LocationNotFound(RoutingError):
    pass


@dataclass(frozen=True)
class Place:
    label: str
    lat: float
    lon: float


@dataclass(frozen=True)
class RouteOption:
    track: Track


def clear_caches() -> None:
    global _last_nominatim
    _geocode_cache.clear()
    _route_cache.clear()
    _last_nominatim = 0.0


def geocode(query: str) -> Place:
    key = " ".join(query.lower().split())
    cached = _geocode_cache.get(key)
    if cached is not None:
        return cached
    place = _geocode(query)
    return _geocode_cache.set(key, place)


def _pace_nominatim() -> None:
    """Nominatim asks for at most one request per second."""
    global _last_nominatim
    if _last_nominatim:
        wait = 1.0 - (time.monotonic() - _last_nominatim)
        if wait > 0:
            time.sleep(wait)
    _last_nominatim = time.monotonic()


def _geocode(query: str) -> Place:
    _pace_nominatim()
    try:
        response = requests.get(
            NOMINATIM_URL,
            params={"q": query, "format": "jsonv2", "limit": 1},
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Geocoding failed for %s", query, exc_info=True)
        raise RoutingError("Geocoding is unavailable right now.") from exc
    if not payload:
        raise LocationNotFound(f'No location found for "{query}".')
    hit = payload[0]
    return Place(label=hit["display_name"], lat=float(hit["lat"]), lon=float(hit["lon"]))


def fetch_routes(origin: Place, destination: Place) -> list[RouteOption]:
    key = (
        round(origin.lat, 4),
        round(origin.lon, 4),
        round(destination.lat, 4),
        round(destination.lon, 4),
    )
    cached = _route_cache.get(key)
    if cached is not None:
        return cached
    routes = _fetch_routes(origin, destination)
    return _route_cache.set(key, routes)


def _fetch_routes(origin: Place, destination: Place) -> list[RouteOption]:
    direct = _driving_routes(
        [(origin.lon, origin.lat), (destination.lon, destination.lat)],
        alternatives=2,
    )
    routes = _distinct(direct)
    if len(routes) >= 3 or not routes:
        return routes[:3]

    primary_miles = routes[0].track.distance_miles
    fallback: tuple[float, RouteOption] | None = None
    attempts = 0
    for scale in (1.0, 1.8):
        for via_lon, via_lat in via_points(routes[0].track, scale):
            if attempts >= MAX_VIA_REQUESTS:
                break
            attempts += 1
            try:
                candidates = _driving_routes(
                    [(origin.lon, origin.lat), (via_lon, via_lat), (destination.lon, destination.lat)],
                    alternatives=0,
                )
            except RoutingError:
                logger.warning("Via-route request failed", exc_info=True)
                continue
            for candidate in candidates:
                if candidate.track.distance_miles > primary_miles * 1.45:
                    continue
                overlap = max(overlap_ratio(candidate.track, existing.track) for existing in routes)
                if overlap < 0.72:
                    routes.append(candidate)
                    if len(routes) >= 3:
                        return routes[:3]
                elif fallback is None or overlap < fallback[0]:
                    fallback = (overlap, candidate)
        if len(routes) >= 3 or attempts >= MAX_VIA_REQUESTS:
            break
    if len(routes) < 3 and fallback is not None and fallback[0] < 0.9:
        routes.append(fallback[1])
    if len(routes) < 3:
        logger.info("Routing returned %s distinct route(s)", len(routes))
    return routes[:3]


def _distinct(routes: list[RouteOption]) -> list[RouteOption]:
    kept: list[RouteOption] = []
    for route in routes:
        if any(overlap_ratio(route.track, existing.track) >= 0.9 for existing in kept):
            continue
        kept.append(route)
    return kept


def _driving_routes(points: list[tuple[float, float]], alternatives: int) -> list[RouteOption]:
    coordinates = ";".join(f"{lon:.6f},{lat:.6f}" for lon, lat in points)
    try:
        response = requests.get(
            f"{OSRM_URL}/{coordinates}",
            params={
                "alternatives": "false" if alternatives == 0 else str(alternatives),
                "steps": "true",
                "geometries": "geojson",
                "overview": "false",
            },
            headers={"User-Agent": USER_AGENT},
            timeout=25,
        )
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Routing request failed", exc_info=True)
        raise RoutingError("Routing is unavailable right now.") from exc

    code = payload.get("code")
    if code == "NoRoute":
        return []
    if code != "Ok":
        logger.warning("Routing response %s: %s", response.status_code, payload.get("message"))
        raise RoutingError("Routing is unavailable right now.")

    options: list[RouteOption] = []
    for route in payload.get("routes", []):
        try:
            track = track_from_steps(route.get("legs") or [])
        except ValueError:
            continue
        if track.distance_miles <= 0:
            continue
        options.append(RouteOption(track=track))
    return options
