import json
from pathlib import Path
import tempfile
import unittest

from experiments.summarize_guard_feature_ablation import cohort_passes, export_portable


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

    def test_portable_export_rewrites_raw_output_locator(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            runs = [{
                "workload": "Ali", "seed": 11, "arm": "guard", "git_sha": "abc",
                "flow_sha256": "flow", "flow_count": 1, "completed_flow_count": 1,
                "completion_fraction": 1.0, "output_id": "123",
                "output_dir": "/tmp/private/123", "output_bytes": 10,
                "elapsed_seconds": 1, "passed": True, "failures": [],
            }]
            run_fields = tuple(field for field in runs[0] if field != "failures") + ("failures",)
            ci_fields = (
                "analysis", "workload", "comparison", "metric", "n", "mean",
                "ci95_low", "ci95_high", "ci95_half_width",
            )
            export_portable(
                target, {"seeds": [11]}, {}, {}, runs,
                [{"workload": "Ali", "seed": 11, "arm": "guard",
                  "metric": "m", "value": 1}], [], run_fields, ci_fields,
            )
            registry = (target / "run_registry.csv").read_text(encoding="utf-8")
            self.assertIn("mix/output/123", registry)
            manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["simulator_git_shas"], ["abc"])


if __name__ == "__main__":
    unittest.main()
