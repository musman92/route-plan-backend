from __future__ import annotations

import math
import time
from datetime import datetime, timedelta, timezone

from django.utils import timezone as dj_timezone

from apps.risk.rules import RISK_NAME, assess, recommendation_key, score_checkpoints
from apps.routing.geometry import haversine_miles, sample_track
from apps.routing.services import (
    LocationNotFound,
    Place,
    RouteOption,
    RoutingError,
    fetch_routes,
    geocode,
)
from apps.weather.services import (
    HourlyForecast,
    WeatherError,
    fetch_forecasts,
    heatmap_stamps,
    location_key,
)

CORRIDOR_MILES = 20
HEATMAP_HOURS = 49
DISPLAY_POINT_LIMIT = 2000


class PlanError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def plan_trip(
    origin: str,
    destination: str,
    departure: datetime,
    load_lbs: float,
    sample_miles: int,
) -> dict:
    if dj_timezone.is_naive(departure):
        departure = dj_timezone.make_aware(departure, timezone.utc)

    try:
        origin_place = geocode(origin)
        time.sleep(1)
        destination_place = geocode(destination)
        if haversine_miles(
            (origin_place.lon, origin_place.lat),
            (destination_place.lon, destination_place.lat),
        ) < 1:
            raise PlanError("Origin and destination are the same place.")
        routes = fetch_routes(origin_place, destination_place)
    except PlanError:
        raise
    except LocationNotFound as exc:
        raise PlanError(str(exc), 404) from exc
    except RoutingError as exc:
        raise PlanError(str(exc), 502) from exc

    if not routes:
        raise PlanError("No driving route was found between those places.", 404)

    route_samples = [sample_track(route.track, sample_miles) for route in routes]
    corridor_samples = [sample_track(route.track, CORRIDOR_MILES) for route in routes]
    points = [
        (point.lat, point.lon)
        for group in (*route_samples, *corridor_samples)
        for point in group
    ]
    try:
        forecasts = fetch_forecasts(points)
    except WeatherError as exc:
        raise PlanError(str(exc), 502) from exc

    scored = [
        _score_route(route, samples, forecasts, departure, load_lbs)
        for route, samples in zip(routes, route_samples)
    ]
    winner = _winner(scored)
    return {
        "origin": _place_payload(origin_place),
        "destination": _place_payload(destination_place),
        "departure": _iso(departure),
        "load_lbs": load_lbs,
        "sample_miles": sample_miles,
        "routes": [
            _route_payload(index, item, recommended=index == winner)
            for index, item in enumerate(scored)
        ],
        "heatmap": _heatmap(corridor_samples, forecasts, load_lbs),
    }


def _score_route(route, samples, forecasts, departure, load_lbs):
    checkpoints = []
    for point in samples:
        eta = departure + timedelta(seconds=point.offset_seconds)
        forecast = forecasts.get(location_key(point.lat, point.lon))
        weather = forecast.at(eta) if forecast else None
        checkpoint = {
            "lat": round(point.lat, 5),
            "lon": round(point.lon, 5),
            "mile": round(point.mile, 2),
            "eta": _iso(eta),
            "wind_mph": None,
            "rain_in_hr": None,
            "snow_in_hr": None,
            "wind_risk": None,
            "rain_risk": None,
            "snow_risk": None,
            "risk": None,
            "_level": None,
        }
        if weather is not None:
            wind, rain, snow = weather
            condition = assess(wind, rain, snow, load_lbs)
            checkpoint.update(
                {
                    "wind_mph": round(wind, 2),
                    "rain_in_hr": round(rain, 3),
                    "snow_in_hr": round(snow, 3),
                    "wind_risk": RISK_NAME[condition.wind],
                    "rain_risk": RISK_NAME[condition.rain],
                    "snow_risk": RISK_NAME[condition.snow],
                    "risk": RISK_NAME[condition.overall],
                    "_level": condition.overall,
                }
            )
        checkpoints.append(checkpoint)
    score = score_checkpoints([(item["mile"], item["_level"]) for item in checkpoints])
    return route, checkpoints, score


def _winner(scored) -> int | None:
    order = sorted(
        range(len(scored)),
        key=lambda index: recommendation_key(scored[index][2], scored[index][0].track.duration_seconds),
    )
    if scored[order[0]][2].average_risk is None:
        return None
    return order[0]


def _route_payload(index: int, item, recommended: bool) -> dict:
    route, checkpoints, score = item
    for checkpoint in checkpoints:
        checkpoint.pop("_level", None)
    return {
        "id": f"route-{index + 1}",
        "name": f"Route {index + 1}",
        "recommended": recommended,
        "distance_miles": round(route.track.distance_miles, 1),
        "duration_minutes": round(route.track.duration_seconds / 60, 1),
        "no_travel_miles": round(score.no_travel_miles, 1),
        "severe_miles": round(score.severe_miles, 1),
        "high_miles": round(score.high_miles, 1),
        "moderate_miles": round(score.moderate_miles, 1),
        "low_miles": round(score.low_miles, 1),
        "unknown_miles": round(score.unknown_miles, 1),
        "average_risk": None if score.average_risk is None else round(score.average_risk, 2),
        "geometry": {
            "type": "LineString",
            "coordinates": _display_coordinates(route),
        },
        "checkpoints": checkpoints,
    }


def _display_coordinates(route: RouteOption) -> list[list[float]]:
    coordinates = route.track.coordinates
    if len(coordinates) > DISPLAY_POINT_LIMIT:
        step = math.ceil(len(coordinates) / DISPLAY_POINT_LIMIT)
        coordinates = coordinates[::step]
        if coordinates[-1] != route.track.coordinates[-1]:
            coordinates = [*coordinates, route.track.coordinates[-1]]
    return [[round(lon, 5), round(lat, 5)] for lon, lat in coordinates]


def _heatmap(corridor_samples, forecasts: dict[tuple[float, float], HourlyForecast], load_lbs: float) -> dict:
    if not forecasts:
        return {"times": [], "points": []}
    reference = next(iter(forecasts.values()))
    stamps = heatmap_stamps(reference, datetime.now(timezone.utc), HEATMAP_HOURS)
    seen: set[tuple[float, float]] = set()
    points = []
    for samples in corridor_samples:
        for point in samples:
            key = location_key(point.lat, point.lon)
            if key in seen:
                continue
            seen.add(key)
            forecast = forecasts.get(key)
            risks: list[int | None] = []
            for stamp in stamps:
                weather = forecast.at_stamp(stamp) if forecast else None
                if weather is None:
                    risks.append(None)
                else:
                    risks.append(int(assess(*weather, load_lbs).overall))
            points.append(
                {
                    "lat": round(point.lat, 5),
                    "lon": round(point.lon, 5),
                    "risks": risks,
                }
            )
    return {
        "times": [
            datetime.fromtimestamp(stamp, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            for stamp in stamps
        ],
        "points": points,
    }


def _place_payload(place: Place) -> dict:
    return {
        "label": place.label,
        "lat": round(place.lat, 5),
        "lon": round(place.lon, 5),
    }


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
