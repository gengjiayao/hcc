import unittest

from experiments.summarize_guard_feature_ablation import cohort_passes


class GuardFeatureAblationSummaryTests(unittest.TestCase):
    def test_cohort_requires_every_preregistered_seed(self):
        indexed = {
            ("Ali", 11, "guard"): {"passed": True},
            ("Ali", 12, "guard"): {"passed": True},
            ("Ali", 11, "no_window"): {"passed": True},
            ("Ali", 12, "no_window"): {"passed": False},
        }
        self.assertTrue(cohort_passes(indexed, "Ali", "guard", (11, 12)))
        self.assertFalse(cohort_passes(indexed, "Ali", "no_window", (11, 12)))


if __name__ == "__main__":
    unittest.main()
