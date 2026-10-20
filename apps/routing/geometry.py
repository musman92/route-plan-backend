from __future__ import annotations

import math
from dataclasses import dataclass

EARTH_MILES = 3958.7613


def haversine_miles(start: tuple[float, float], end: tuple[float, float]) -> float:
    lon1, lat1 = start
    lon2, lat2 = end
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    half = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_MILES * math.asin(min(1.0, math.sqrt(half)))


def bearing_degrees(start: tuple[float, float], end: tuple[float, float]) -> float:
    lon1, lat1 = map(math.radians, start)
    lon2, lat2 = map(math.radians, end)
    dlambda = lon2 - lon1
    x = math.sin(dlambda) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlambda)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def destination_point(lon: float, lat: float, bearing: float, miles: float) -> tuple[float, float]:
    angular = miles / EARTH_MILES
    theta = math.radians(bearing)
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)
    lat2 = math.asin(
        math.sin(lat1) * math.cos(angular) + math.cos(lat1) * math.sin(angular) * math.cos(theta)
    )
    lon2 = lon1 + math.atan2(
        math.sin(theta) * math.sin(angular) * math.cos(lat1),
        math.cos(angular) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lon2), math.degrees(lat2)


@dataclass(frozen=True)
class Track:
    coordinates: list[tuple[float, float]]
    cumulative_miles: list[float]
    cumulative_seconds: list[float]

    @property
    def distance_miles(self) -> float:
        return self.cumulative_miles[-1] if self.cumulative_miles else 0.0

    @property
    def duration_seconds(self) -> float:
        return self.cumulative_seconds[-1] if self.cumulative_seconds else 0.0


@dataclass(frozen=True)
class SamplePoint:
    lon: float
    lat: float
    mile: float
    offset_seconds: float


def track_from_steps(legs: list[dict]) -> Track:
    coordinates: list[tuple[float, float]] = []
    cumulative_miles: list[float] = []
    cumulative_seconds: list[float] = []
    miles = 0.0
    seconds = 0.0

    for leg in legs:
        for step in leg.get("steps", []):
            points = step.get("geometry", {}).get("coordinates") or []
            if len(points) < 2:
                continue
            gaps = [haversine_miles(tuple(a), tuple(b)) for a, b in zip(points, points[1:])]
            gap_sum = sum(gaps)
            step_miles = float(step.get("distance") or 0) / 1609.344
            step_seconds = float(step.get("duration") or 0)
            if not coordinates:
                coordinates.append((float(points[0][0]), float(points[0][1])))
                cumulative_miles.append(0.0)
                cumulative_seconds.append(0.0)
            for index, gap in enumerate(gaps):
                fraction = (gap / gap_sum) if gap_sum else (1 / len(gaps))
                miles += step_miles * fraction
                seconds += step_seconds * fraction
                point = (float(points[index + 1][0]), float(points[index + 1][1]))
                if point == coordinates[-1]:
                    cumulative_miles[-1] = miles
                    cumulative_seconds[-1] = seconds
                    continue
                coordinates.append(point)
                cumulative_miles.append(miles)
                cumulative_seconds.append(seconds)

    if not coordinates:
        raise ValueError("Route geometry was empty.")
    return Track(coordinates, cumulative_miles, cumulative_seconds)


def point_at(track: Track, mile: float) -> SamplePoint:
    markers = track.cumulative_miles
    mile = min(max(mile, 0.0), markers[-1])
    hi = 0
    for index, marker in enumerate(markers):
        if marker >= mile:
            hi = index
            break
    else:
        hi = len(markers) - 1
    if hi == 0:
        lon, lat = track.coordinates[0]
        return SamplePoint(lon, lat, mile, track.cumulative_seconds[0])
    lo = hi - 1
    span = markers[hi] - markers[lo]
    fraction = 0.0 if span == 0 else (mile - markers[lo]) / span
    lon0, lat0 = track.coordinates[lo]
    lon1, lat1 = track.coordinates[hi]
    seconds = track.cumulative_seconds[lo] + (
        track.cumulative_seconds[hi] - track.cumulative_seconds[lo]
    ) * fraction
    return SamplePoint(
        lon0 + (lon1 - lon0) * fraction,
        lat0 + (lat1 - lat0) * fraction,
        mile,
        seconds,
    )


def sample_track(track: Track, interval_miles: float) -> list[SamplePoint]:
    total = track.distance_miles
    if total <= 0:
        lon, lat = track.coordinates[0]
        return [SamplePoint(lon, lat, 0.0, track.cumulative_seconds[0] if track.cumulative_seconds else 0.0)]
    markers = [0.0]
    cursor = interval_miles
    while cursor < total - 0.05:
        markers.append(cursor)
        cursor += interval_miles
    markers.append(total)
    return [point_at(track, mile) for mile in markers]


def overlap_ratio(left: Track, right: Track) -> float:
    samples = sample_track(left, 20)
    probes = sample_track(right, 5)
    if not samples:
        return 1.0
    close = 0
    for sample in samples:
        point = (sample.lon, sample.lat)
        if any(haversine_miles(point, (probe.lon, probe.lat)) <= 8 for probe in probes):
            close += 1
    return close / len(samples)


def via_points(track: Track) -> list[tuple[float, float]]:
    """Points offset from the route, used when the router returns fewer than three roads."""
    total = track.distance_miles
    if total < 40:
        return []
    offset = max(12.0, min(60.0, total * 0.06))
    points: list[tuple[float, float]] = []
    for fraction in (0.5, 0.33, 0.67):
        spot = point_at(track, total * fraction)
        before = point_at(track, max(0.0, spot.mile - 1))
        after = point_at(track, min(total, spot.mile + 1))
        bearing = bearing_degrees((before.lon, before.lat), (after.lon, after.lat))
        for turn in (90, -90):
            points.append(destination_point(spot.lon, spot.lat, bearing + turn, offset))
    return points
