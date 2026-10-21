from datetime import datetime, timezone
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from apps.weather.services import HourlyForecast, _inch_scale, _wind_scale, clear_cache, fetch_forecasts


class ForecastLookupTests(SimpleTestCase):
    def test_nearest_hour_and_out_of_range(self):
        start = 1_700_000_000
        forecast = HourlyForecast(
            times=[start, start + 3600, start + 7200],
            wind_mph=[10, 20, None],
            rain_in_hr=[0, 0.2, 1],
            snow_in_hr=[0, 0, 0],
        )
        on_hour = datetime.fromtimestamp(start + 3700, tz=timezone.utc)
        self.assertEqual(forecast.at(on_hour), (20, 0.2, 0))
        missing = datetime.fromtimestamp(start + 7200, tz=timezone.utc)
        self.assertIsNone(forecast.at(missing))
        later = datetime.fromtimestamp(start + 12 * 3600, tz=timezone.utc)
        self.assertIsNone(forecast.at(later))

    def test_unit_scales(self):
        self.assertEqual(_wind_scale("mp/h"), 1)
        self.assertAlmostEqual(_wind_scale("km/h"), 0.621371)
        self.assertEqual(_inch_scale("inch"), 1)
        self.assertAlmostEqual(_inch_scale("cm"), 1 / 2.54)
        self.assertAlmostEqual(_inch_scale("mm"), 1 / 25.4)

    def test_repeated_locations_are_cached(self):
        clear_cache()
        self.addCleanup(clear_cache)
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "hourly": {
                "time": [1_700_000_000],
                "wind_speed_10m": [10],
                "rain": [0],
                "snowfall": [0],
            },
            "hourly_units": {"wind_speed_10m": "mph", "rain": "inch", "snowfall": "inch"},
        }
        with patch("apps.weather.services.requests.get", return_value=response) as get:
            first = fetch_forecasts([(41.12341, -87.1)])
            second = fetch_forecasts([(41.12344, -87.1)])
        self.assertEqual(get.call_count, 1)
        self.assertEqual(first.keys(), second.keys())
