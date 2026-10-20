from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

USER_AGENT = "weather-aware-truck-routing/1.0"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
BATCH_SIZE = 50
FORECAST_DAYS = 7


class WeatherError(Exception):
    pass


@dataclass
class HourlyForecast:
    times: list[int]
    wind_mph: list[float | None]
    rain_in_hr: list[float | None]
    snow_in_hr: list[float | None]

    def at(self, when: datetime, tolerance_seconds: int = 90 * 60) -> tuple[float, float, float] | None:
        if not self.times:
            return None
        target = int(when.timestamp())
        index = min(range(len(self.times)), key=lambda item: abs(self.times[item] - target))
        return self._values(index, abs(self.times[index] - target) <= tolerance_seconds)

    def at_stamp(self, stamp: int) -> tuple[float, float, float] | None:
        if stamp not in self._by_time:
            return self.at(datetime.fromtimestamp(stamp, tz=timezone.utc), tolerance_seconds=3600)
        return self._values(self._by_time[stamp], True)

    def __post_init__(self) -> None:
        self._by_time = {stamp: index for index, stamp in enumerate(self.times)}

    def _values(self, index: int, in_window: bool) -> tuple[float, float, float] | None:
        if not in_window:
            return None
        wind = self.wind_mph[index]
        rain = self.rain_in_hr[index]
        snow = self.snow_in_hr[index]
        if wind is None or rain is None or snow is None:
            return None
        return wind, rain, snow


def location_key(lat: float, lon: float) -> tuple[float, float]:
    return (round(lat, 4), round(lon, 4))


def fetch_forecasts(points: list[tuple[float, float]]) -> dict[tuple[float, float], HourlyForecast]:
    unique = list(dict.fromkeys(location_key(lat, lon) for lat, lon in points))
    forecasts: dict[tuple[float, float], HourlyForecast] = {}
    for start in range(0, len(unique), BATCH_SIZE):
        batch = unique[start : start + BATCH_SIZE]
        forecasts.update(_fetch_batch(batch))
    return forecasts


def heatmap_stamps(forecast: HourlyForecast, now: datetime, count: int = 49) -> list[int]:
    if not forecast.times:
        return []
    target = int(now.timestamp()) - 1800
    start = next((index for index, stamp in enumerate(forecast.times) if stamp >= target), None)
    if start is None:
        start = max(0, len(forecast.times) - count)
    return forecast.times[start : start + count]


def _fetch_batch(batch: list[tuple[float, float]]) -> dict[tuple[float, float], HourlyForecast]:
    try:
        response = requests.get(
            FORECAST_URL,
            params={
                "latitude": ",".join(str(lat) for lat, _ in batch),
                "longitude": ",".join(str(lon) for _, lon in batch),
                "hourly": "wind_speed_10m,rain,snowfall",
                "wind_speed_unit": "mph",
                "precipitation_unit": "inch",
                "forecast_days": FORECAST_DAYS,
                "timezone": "UTC",
                "timeformat": "unixtime",
            },
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Weather request failed", exc_info=True)
        raise WeatherError("Weather data is unavailable right now.") from exc

    if isinstance(payload, dict) and payload.get("error"):
        logger.warning("Weather error: %s", payload.get("reason"))
        raise WeatherError("Weather data is unavailable right now.")

    rows = payload if isinstance(payload, list) else [payload]
    if len(rows) != len(batch):
        raise WeatherError("Weather data is unavailable right now.")
    return {key: _parse_row(row) for key, row in zip(batch, rows)}


def _parse_row(row: dict) -> HourlyForecast:
    hourly = row["hourly"]
    units = row.get("hourly_units") or {}
    return HourlyForecast(
        times=[int(stamp) for stamp in hourly["time"]],
        wind_mph=_scaled(hourly["wind_speed_10m"], _wind_scale(units.get("wind_speed_10m"))),
        rain_in_hr=_scaled(hourly["rain"], _inch_scale(units.get("rain"))),
        snow_in_hr=_scaled(hourly["snowfall"], _inch_scale(units.get("snowfall"))),
    )


def _wind_scale(unit: str | None) -> float:
    if unit == "km/h":
        return 0.621371
    if unit == "m/s":
        return 2.23694
    return 1.0


def _inch_scale(unit: str | None) -> float:
    if unit == "mm":
        return 1 / 25.4
    if unit == "cm":
        return 1 / 2.54
    return 1.0


def _scaled(values: list, scale: float) -> list[float | None]:
    scaled: list[float | None] = []
    for value in values:
        scaled.append(None if value is None else float(value) * scale)
    return scaled
