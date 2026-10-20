from django.test import SimpleTestCase

from apps.routing.geometry import Track, point_at, sample_track, track_from_steps


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
