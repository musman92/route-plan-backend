from django.test import SimpleTestCase

from apps.risk.rules import (
    Risk,
    assess,
    classify_rain,
    classify_snow,
    classify_wind,
    explain_recommendation,
    owned_miles,
    recommendation_key,
    score_checkpoints,
)


class WindRuleTests(SimpleTestCase):
    def test_wind_bands_for_a_light_load(self):
        cases = [
            (24.9, Risk.LOW),
            (25, Risk.MODERATE),
            (34.9, Risk.MODERATE),
            (35, Risk.HIGH),
            (44.9, Risk.HIGH),
            (45, Risk.SEVERE),
            (54.9, Risk.SEVERE),
            (55, Risk.NO_TRAVEL),
            (70, Risk.NO_TRAVEL),
        ]
        for mph, expected in cases:
            with self.subTest(mph=mph):
                self.assertEqual(classify_wind(mph, load_lbs=10_000), expected)

    def test_load_overrides(self):
        self.assertEqual(classify_wind(45, 30_000), Risk.SEVERE)
        self.assertEqual(classify_wind(54.9, 30_001), Risk.NO_TRAVEL)
        self.assertEqual(classify_wind(35, 40_000), Risk.HIGH)
        self.assertEqual(classify_wind(44.9, 40_001), Risk.SEVERE)
        self.assertEqual(classify_wind(25, 80_000), Risk.MODERATE)
        self.assertEqual(classify_wind(55, 1), Risk.NO_TRAVEL)


class PrecipitationRuleTests(SimpleTestCase):
    def test_rain_bands(self):
        cases = [
            (0.09, Risk.LOW),
            (0.10, Risk.MODERATE),
            (0.24, Risk.MODERATE),
            (0.25, Risk.HIGH),
            (0.49, Risk.HIGH),
            (0.50, Risk.SEVERE),
            (1.00, Risk.SEVERE),
            (1.01, Risk.NO_TRAVEL),
        ]
        for amount, expected in cases:
            with self.subTest(amount=amount):
                self.assertEqual(classify_rain(amount), expected)

    def test_snow_bands(self):
        cases = [
            (0.49, Risk.LOW),
            (0.5, Risk.MODERATE),
            (0.99, Risk.MODERATE),
            (1.0, Risk.HIGH),
            (1.99, Risk.HIGH),
            (2.0, Risk.SEVERE),
            (3.0, Risk.SEVERE),
            (3.01, Risk.NO_TRAVEL),
        ]
        for amount, expected in cases:
            with self.subTest(amount=amount):
                self.assertEqual(classify_snow(amount), expected)


class CombinedRiskTests(SimpleTestCase):
    def test_overall_risk_is_the_worst_condition(self):
        result = assess(wind_mph=10, rain_in_hr=0.6, snow_in_hr=0.2, load_lbs=10_000)
        self.assertEqual(result.wind, Risk.LOW)
        self.assertEqual(result.rain, Risk.SEVERE)
        self.assertEqual(result.snow, Risk.LOW)
        self.assertEqual(result.overall, Risk.SEVERE)

    def test_owned_miles_cover_the_route_once(self):
        self.assertEqual(owned_miles([0, 25, 50, 75]), [12.5, 25, 25, 12.5])
        self.assertAlmostEqual(sum(owned_miles([0, 10, 40])), 40)

    def test_checkpoint_miles_follow_the_nearest_sample(self):
        score = score_checkpoints(
            [
                (0, Risk.LOW),
                (25, Risk.SEVERE),
                (50, Risk.HIGH),
                (75, Risk.LOW),
            ]
        )
        self.assertAlmostEqual(score.severe_miles, 25)
        self.assertAlmostEqual(score.high_miles, 25)
        self.assertAlmostEqual(score.low_miles, 25)
        self.assertAlmostEqual(score.average_risk, (12.5 + 25 * 4 + 25 * 3 + 12.5) / 75)

    def test_missing_forecast_is_not_counted_as_low(self):
        score = score_checkpoints([(0, None), (50, None)])
        self.assertIsNone(score.average_risk)
        self.assertAlmostEqual(score.unknown_miles, 50)
        self.assertEqual(score.low_miles, 0)


class RankingTests(SimpleTestCase):
    def test_preference_order(self):
        no_travel = score_checkpoints([(0, Risk.NO_TRAVEL), (10, Risk.LOW)])
        severe = score_checkpoints([(0, Risk.SEVERE), (100, Risk.LOW)])
        high = score_checkpoints([(0, Risk.HIGH), (10, Risk.LOW)])
        milder = score_checkpoints([(0, Risk.MODERATE), (10, Risk.LOW)])
        same_as_milder = score_checkpoints([(0, Risk.MODERATE), (10, Risk.LOW)])
        unscored = score_checkpoints([(0, None), (10, None)])

        keys = {
            "no_travel": recommendation_key(no_travel, 100),
            "severe": recommendation_key(severe, 100),
            "high": recommendation_key(high, 100),
            "milder": recommendation_key(milder, 90),
            "slower_mild": recommendation_key(same_as_milder, 120),
            "unscored": recommendation_key(unscored, 10),
        }
        ordered = sorted(keys, key=keys.get)
        self.assertEqual(
            ordered,
            ["milder", "slower_mild", "high", "severe", "no_travel", "unscored"],
        )


class RecommendationReasonTests(SimpleTestCase):
    def test_reason_names_the_first_rule_that_differs(self):
        severe = score_checkpoints([(0, Risk.SEVERE), (100, Risk.LOW)])
        high = score_checkpoints([(0, Risk.HIGH), (10, Risk.LOW)])
        mild = score_checkpoints([(0, Risk.MODERATE), (10, Risk.LOW)])
        slower = score_checkpoints([(0, Risk.MODERATE), (10, Risk.LOW)])

        winner, reason = explain_recommendation(
            [(severe, 100), (high, 100), (mild, 90), (slower, 120)]
        )
        self.assertEqual(winner, 2)
        self.assertEqual(reason, "It has the fewest severe miles.")

    def test_travel_time_breaks_a_weather_tie(self):
        calm = score_checkpoints([(0, Risk.LOW), (10, Risk.LOW)])
        also_calm = score_checkpoints([(0, Risk.LOW), (10, Risk.LOW)])
        winner, reason = explain_recommendation([(calm, 100), (also_calm, 50)])
        self.assertEqual(winner, 1)
        self.assertEqual(reason, "It has the shortest travel time.")

    def test_no_travel_miles_outrank_severe_miles(self):
        blocked = score_checkpoints([(0, Risk.NO_TRAVEL), (10, Risk.LOW)])
        severe = score_checkpoints([(0, Risk.SEVERE), (10, Risk.LOW)])
        winner, reason = explain_recommendation([(blocked, 60), (severe, 120)])
        self.assertEqual(winner, 1)
        self.assertEqual(reason, "It has the fewest miles where travel is not advised.")

    def test_missing_forecast_is_not_recommended(self):
        missing = score_checkpoints([(0, None), (10, None)])
        calm = score_checkpoints([(0, Risk.LOW), (10, Risk.LOW)])
        winner, reason = explain_recommendation([(missing, 10), (calm, 100)])
        self.assertEqual(winner, 1)
        self.assertEqual(reason, "It is the only route with forecast coverage.")
        self.assertEqual(explain_recommendation([(missing, 10), (missing, 20)]), (None, None))

    def test_exact_tie_keeps_the_first_route(self):
        calm = score_checkpoints([(0, Risk.LOW), (10, Risk.LOW)])
        also_calm = score_checkpoints([(0, Risk.LOW), (10, Risk.LOW)])
        winner, reason = explain_recommendation([(calm, 100), (also_calm, 100)])
        self.assertEqual(winner, 0)
        self.assertEqual(reason, "It matches the other routes on weather and travel time.")
