from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from apps.routing.geometry import Track, point_at, sample_track, track_from_steps, via_points
from apps.routing.services import Place, RouteOption, clear_caches, fetch_routes, geocode


class GeometryTests(SimpleTestCase):
    def test_step_distance_and_time_are_preserved(self):
        legs = [
            {
                "steps": [
                    {
                        "distance": 1609.344,
                        "duration": 60,
                        "geometry": {"coordinates": [[-87.0, 41.0], [-87.0, 41.02]]},
                    },
                    {
                        "distance": 1609.344,
                        "duration": 120,
                        "geometry": {"coordinates": [[-87.0, 41.02], [-87.0, 41.04]]},
                    },
                ]
            }
        ]
        track = track_from_steps(legs)
        self.assertAlmostEqual(track.distance_miles, 2, places=5)
        self.assertAlmostEqual(track.duration_seconds, 180, places=5)
        self.assertEqual(track.coordinates[0], (-87.0, 41.0))
        self.assertEqual(track.coordinates[-1], (-87.0, 41.04))

    def test_samples_include_the_start_and_the_end(self):
        track = Track(
            coordinates=[(-87.0, 41.0), (-86.0, 41.0)],
            cumulative_miles=[0.0, 69.0],
            cumulative_seconds=[0.0, 3600.0],
        )
        points = sample_track(track, 25)
        self.assertEqual(points[0].mile, 0)
        self.assertEqual(points[-1].mile, 69)
        self.assertAlmostEqual(points[1].mile, 25)
        midpoint = point_at(track, 34.5)
        self.assertAlmostEqual(midpoint.offset_seconds, 1800, places=5)
        self.assertAlmostEqual(midpoint.lon, -86.5, places=5)

    def test_via_points_need_a_route_long_enough_to_leave(self):
        short = Track(
            coordinates=[(-87.0, 41.0), (-86.9, 41.0)],
            cumulative_miles=[0.0, 10.0],
            cumulative_seconds=[0.0, 600.0],
        )
        long = Track(
            coordinates=[(-87.0, 41.0), (-86.0, 41.0)],
            cumulative_miles=[0.0, 80.0],
            cumulative_seconds=[0.0, 6000.0],
        )
        self.assertEqual(via_points(short), [])
        self.assertEqual(len(via_points(long)), 6)


class RouteSearchTests(SimpleTestCase):
    def tearDown(self):
        clear_caches()

    def test_via_search_fills_out_three_routes(self):
        clear_caches()
        calls = {"count": 0}

        def fake(_points, alternatives):
            calls["count"] += 1
            lat = 40.0 + calls["count"]
            return [
                RouteOption(
                    Track(
                        coordinates=[(-100.0, lat), (-99.0, lat)],
                        cumulative_miles=[0.0, 80.0],
                        cumulative_seconds=[0.0, 5000.0],
                    )
                )
            ]

        with patch("apps.routing.services._driving_routes", side_effect=fake):
            routes = fetch_routes(Place("A", 40.0, -100.0), Place("B", 41.0, -80.0))
        self.assertEqual(len(routes), 3)
        self.assertEqual(calls["count"], 3)

    def test_repeated_lookups_skip_the_outside_services(self):
        clear_caches()
        origin = Place("A", 40.0, -100.0)
        destination = Place("B", 41.0, -80.0)
        routes = [
            RouteOption(
                Track(
                    coordinates=[(-100.0, lat), (-99.0, lat)],
                    cumulative_miles=[0.0, 80.0],
                    cumulative_seconds=[0.0, 5000.0],
                )
            )
            for lat in (40.0, 42.0, 44.0)
        ]
        with patch("apps.routing.services._driving_routes", return_value=routes) as driving:
            fetch_routes(origin, destination)
            fetch_routes(origin, destination)
            self.assertEqual(driving.call_count, 1)

        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = [
            {"display_name": "Chicago, Illinois", "lat": "41.8", "lon": "-87.6"}
        ]
        with patch("apps.routing.services.requests.get", return_value=response) as get:
            geocode("Chicago, IL")
            geocode("  chicago,   il ")
            self.assertEqual(get.call_count, 1)
