import unittest

from experiments.summarize_guard_feature_ablation import AnalysisError, mean_ci


class GuardFeatureAblationSummaryTests(unittest.TestCase):
    def test_five_identical_values_have_zero_width_interval(self):
        result = mean_ci([7.0] * 5)
        self.assertEqual(result["mean"], 7.0)
        self.assertEqual(result["ci95_low"], 7.0)
        self.assertEqual(result["ci95_high"], 7.0)

    def test_interval_rejects_non_five_seed_input(self):
        with self.assertRaisesRegex(AnalysisError, "requires 5 values"):
            mean_ci([1.0, 2.0, 3.0])


if __name__ == "__main__":
    unittest.main()
