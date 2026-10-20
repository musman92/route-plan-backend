from django.test import SimpleTestCase

from apps.risk.rules import (
    Risk,
    assess,
    classify_rain,
    classify_snow,
    classify_wind,
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
