import json

from django.test import SimpleTestCase


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
