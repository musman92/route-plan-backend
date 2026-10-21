import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.planner.services import PlanError, plan_trip
from apps.routing.geometry import Track
from apps.routing.services import Place, RouteOption
from apps.weather.services import HourlyForecast, location_key


class PlanValidationTests(SimpleTestCase):
    def test_sample_interval_must_be_10_25_or_50(self):
        response = self.client.post(
            "/api/plan/",
            data=json.dumps(
                {
                    "origin": "Chicago, IL",
                    "destination": "Detroit, MI",
                    "departure": "2026-10-20T15:00:00Z",
                    "load_lbs": 20000,
                    "sample_miles": 15,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("sample_miles", response.json())

    def test_blank_origin_is_rejected(self):
        response = self.client.post(
            "/api/plan/",
            data=json.dumps(
                {
                    "origin": "   ",
                    "destination": "Detroit, MI",
                    "departure": "2026-10-20T15:00:00Z",
                    "load_lbs": 20000,
                    "sample_miles": 25,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("origin", response.json())


def _track(lat: float, miles: float = 100.0, seconds: float = 7200.0) -> Track:
    return Track(
        coordinates=[(-90.0, lat), (-89.0, lat)],
        cumulative_miles=[0.0, miles],
        cumulative_seconds=[0.0, seconds],
    )


def _forecasts(points, wind_for_lat, start: datetime):
    stamps = [int(start.timestamp()) + hour * 3600 for hour in range(80)]
    found = {}
    for lat, _lon in points:
        wind = wind_for_lat(lat)
        found[location_key(lat, _lon)] = HourlyForecast(
            times=stamps,
            wind_mph=[wind] * len(stamps),
            rain_in_hr=[0.0] * len(stamps),
            snow_in_hr=[0.0] * len(stamps),
        )
    return found


class PlanTripTests(SimpleTestCase):
    def setUp(self):
        self.departure = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        self.geocode = patch("apps.planner.services.geocode").start()
        self.fetch_routes = patch("apps.planner.services.fetch_routes").start()
        self.fetch_forecasts = patch("apps.planner.services.fetch_forecasts").start()
        self.addCleanup(patch.stopall)
        self.geocode.side_effect = lambda query: Place(
            query,
            41.0 if query == "Origin" else 40.0,
            -87.0 if query == "Origin" else -74.0,
        )

    def _routes(self):
        return [
            RouteOption(_track(40.0, seconds=7200.0)),
            RouteOption(_track(41.0, seconds=9000.0)),
        ]

    def test_eta_uses_the_forecast_for_when_the_truck_arrives(self):
        self.fetch_routes.return_value = [RouteOption(_track(40.0, miles=10.0, seconds=7200.0))]

        def forecasts(points):
            start = int(self.departure.timestamp())
            stamps = [start + hour * 3600 for hour in range(80)]
            winds = [10.0 if hour < 2 else 40.0 for hour in range(80)]
            return {
                location_key(lat, lon): HourlyForecast(stamps, winds, [0.0] * 80, [0.0] * 80)
                for lat, lon in points
            }

        self.fetch_forecasts.side_effect = forecasts
        plan = plan_trip("Origin", "Destination", self.departure, 10_000, 50)
        checkpoints = plan["routes"][0]["checkpoints"]
        self.assertEqual(checkpoints[0]["eta"], self.departure.strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.assertEqual(checkpoints[0]["wind_mph"], 10)
        self.assertEqual(checkpoints[-1]["mile"], 10)
        self.assertEqual(checkpoints[-1]["wind_mph"], 40)
        self.assertEqual(checkpoints[-1]["risk"], "high")
        self.assertEqual(
            checkpoints[-1]["eta"],
            (self.departure + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

    def test_load_can_turn_high_wind_into_a_blocked_road(self):
        self.fetch_routes.return_value = self._routes()
        self.fetch_forecasts.side_effect = lambda points: _forecasts(
            points,
            lambda lat: 36.0 if lat == 40 else 50.0,
            self.departure,
        )

        light = plan_trip("Origin", "Destination", self.departure, 20_000, 50)
        heavy = plan_trip("Origin", "Destination", self.departure, 45_000, 50)

        self.assertEqual(light["recommendation"]["route_id"], "route-1")
        self.assertEqual(light["recommendation"]["reason"], "It has the fewest severe miles.")
        self.assertGreater(light["routes"][1]["severe_miles"], 0)
        self.assertEqual(light["routes"][1]["no_travel_miles"], 0)

        self.assertEqual(heavy["recommendation"]["route_id"], "route-1")
        self.assertEqual(
            heavy["recommendation"]["reason"],
            "It has the fewest miles where travel is not advised.",
        )
        self.assertGreater(heavy["routes"][1]["no_travel_miles"], 0)
        self.assertEqual(heavy["routes"][0]["no_travel_miles"], 0)
        self.assertEqual(len(heavy["heatmap"]["times"]), 49)
        self.assertTrue(heavy["heatmap"]["points"])
        self.assertEqual(len(heavy["heatmap"]["points"][0]["risks"]), 49)

    def test_departure_outside_the_forecast_is_not_recommended(self):
        self.fetch_routes.return_value = [RouteOption(_track(40.0, miles=10.0))]
        later = self.departure + timedelta(days=10)

        def forecasts(points):
            stamps = [int(self.departure.timestamp()) + hour * 3600 for hour in range(6)]
            return {
                location_key(lat, lon): HourlyForecast(
                    stamps, [10.0] * 6, [0.0] * 6, [0.0] * 6
                )
                for lat, lon in points
            }

        self.fetch_forecasts.side_effect = forecasts
        plan = plan_trip("Origin", "Destination", later, 10_000, 50)
        self.assertIsNone(plan["recommendation"])
        self.assertFalse(plan["routes"][0]["recommended"])
        self.assertGreater(plan["routes"][0]["unknown_miles"], 0)
        self.assertEqual(plan["routes"][0]["low_miles"], 0)

    def test_same_place_is_rejected(self):
        self.geocode.side_effect = lambda _query: Place("Chicago", 41.88, -87.63)
        with self.assertRaises(PlanError) as caught:
            plan_trip("Origin", "Destination", self.departure, 10_000, 25)
        self.assertEqual(caught.exception.status, 400)
        self.fetch_routes.assert_not_called()
