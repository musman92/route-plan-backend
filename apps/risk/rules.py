from __future__ import annotations

import math
from dataclasses import dataclass
from enum import IntEnum


class Risk(IntEnum):
    LOW = 1
    MODERATE = 2
    HIGH = 3
    SEVERE = 4
    NO_TRAVEL = 5


RISK_NAME = {
    Risk.LOW: "low",
    Risk.MODERATE: "moderate",
    Risk.HIGH: "high",
    Risk.SEVERE: "severe",
    Risk.NO_TRAVEL: "no_travel",
}


def classify_wind(mph: float, load_lbs: float) -> Risk:
    """Wind bands from the brief, then the load overrides."""
    if mph >= 55:
        return Risk.NO_TRAVEL
    if mph >= 45:
        if load_lbs > 30_000:
            return Risk.NO_TRAVEL
        return Risk.SEVERE
    if mph >= 35:
        if load_lbs > 40_000:
            return Risk.SEVERE
        return Risk.HIGH
    if mph >= 25:
        return Risk.MODERATE
    return Risk.LOW


def classify_rain(inches_per_hour: float) -> Risk:
    if inches_per_hour > 1:
        return Risk.NO_TRAVEL
    if inches_per_hour >= 0.5:
        return Risk.SEVERE
    if inches_per_hour >= 0.25:
        return Risk.HIGH
    if inches_per_hour >= 0.1:
        return Risk.MODERATE
    return Risk.LOW


def classify_snow(inches_per_hour: float) -> Risk:
    if inches_per_hour > 3:
        return Risk.NO_TRAVEL
    if inches_per_hour >= 2:
        return Risk.SEVERE
    if inches_per_hour >= 1:
        return Risk.HIGH
    if inches_per_hour >= 0.5:
        return Risk.MODERATE
    return Risk.LOW


@dataclass(frozen=True)
class ConditionRisk:
    wind: Risk
    rain: Risk
    snow: Risk

    @property
    def overall(self) -> Risk:
        return max(self.wind, self.rain, self.snow)


def assess(wind_mph: float, rain_in_hr: float, snow_in_hr: float, load_lbs: float) -> ConditionRisk:
    return ConditionRisk(
        wind=classify_wind(wind_mph, load_lbs),
        rain=classify_rain(rain_in_hr),
        snow=classify_snow(snow_in_hr),
    )


def owned_miles(markers: list[float]) -> list[float]:
    """Miles owned by each checkpoint: the road closer to it than to its neighbors."""
    if not markers:
        return []
    if len(markers) == 1:
        return [0.0]
    owned: list[float] = []
    last = len(markers) - 1
    for index, mile in enumerate(markers):
        start = mile if index == 0 else (markers[index - 1] + mile) / 2
        end = mile if index == last else (mile + markers[index + 1]) / 2
        owned.append(max(0.0, end - start))
    return owned


@dataclass(frozen=True)
class RouteScore:
    no_travel_miles: float = 0.0
    severe_miles: float = 0.0
    high_miles: float = 0.0
    moderate_miles: float = 0.0
    low_miles: float = 0.0
    unknown_miles: float = 0.0

    @property
    def average_risk(self) -> float | None:
        known = (
            self.no_travel_miles
            + self.severe_miles
            + self.high_miles
            + self.moderate_miles
            + self.low_miles
        )
        if known <= 0:
            return None
        weighted = (
            self.no_travel_miles * Risk.NO_TRAVEL
            + self.severe_miles * Risk.SEVERE
            + self.high_miles * Risk.HIGH
            + self.moderate_miles * Risk.MODERATE
            + self.low_miles * Risk.LOW
        )
        return weighted / known


def summarize(segments: list[tuple[float, Risk | None]]) -> RouteScore:
    totals = {level: 0.0 for level in Risk}
    unknown = 0.0
    for miles, level in segments:
        if miles <= 0:
            continue
        if level is None:
            unknown += miles
        else:
            totals[level] += miles
    return RouteScore(
        no_travel_miles=totals[Risk.NO_TRAVEL],
        severe_miles=totals[Risk.SEVERE],
        high_miles=totals[Risk.HIGH],
        moderate_miles=totals[Risk.MODERATE],
        low_miles=totals[Risk.LOW],
        unknown_miles=unknown,
    )


def score_checkpoints(markers: list[tuple[float, Risk | None]]) -> RouteScore:
    miles = owned_miles([mile for mile, _ in markers])
    return summarize([(length, risk) for length, (_, risk) in zip(miles, markers)])


def recommendation_key(score: RouteScore, duration_seconds: float) -> tuple[float, ...]:
    """No-travel miles rank first because that level is worse than severe.

    The brief lists severe miles, high miles, average risk, then travel time.
    No-travel miles are checked before severe miles so a blocked road cannot win
    just by having fewer severe miles. A route with no scored weather sorts
    after every scored route, so a missing forecast is not treated as zero risk.
    """
    average = score.average_risk
    unscored = 0.0 if average is not None else 1.0
    return (
        unscored,
        score.no_travel_miles,
        score.severe_miles,
        score.high_miles,
        average if average is not None else math.inf,
        duration_seconds,
    )


_REASON_BY_COMPONENT = {
    1: "It has the fewest miles where travel is not advised.",
    2: "It has the fewest severe miles.",
    3: "It has the fewest high-risk miles.",
    4: "It has the lowest average risk.",
    5: "It has the shortest travel time.",
}


def explain_recommendation(
    entries: list[tuple[RouteScore, float]],
) -> tuple[int | None, str | None]:
    """Pick the winning route and the first ranking rule that decided it."""
    if not entries:
        return None, None
    keys = [recommendation_key(score, duration) for score, duration in entries]
    winner = min(range(len(keys)), key=keys.__getitem__)
    if entries[winner][0].average_risk is None:
        return None, None
    scored = [index for index, (score, _) in enumerate(entries) if score.average_risk is not None]
    if len(scored) == 1:
        return winner, "It is the only route with forecast coverage."
    winner_key = keys[winner]
    for component, reason in _REASON_BY_COMPONENT.items():
        if any(winner_key[component] != keys[other][component] for other in scored):
            return winner, reason
    return winner, "It matches the other routes on weather and travel time."
