import json
from pathlib import Path
import tempfile
import unittest

from experiments.run_campaign import sha256_file
from experiments.select_guard_v23_elephant_spillover import SelectionError
from experiments.select_guard_v25_elephant_fabric_target import select


class GuardV25SelectionTests(unittest.TestCase):
    def write_json(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, sort_keys=True))

    def campaign(self, root, long_high=-6.0):
        spec = {
            "mechanism_profile": "V25", "name": "v25",
            "seeds": [195, 196, 197, 198, 199],
            "arms": {"guard_k1": {}, "guard_elephant_target": {}},
            "selection": {
                "require_gt_1MB_fct_us_mean_percent_ci95_high_at_most": -5.5,
                "require_gt_1MB_fct_us_p95_percent_ci95_high_at_most": 0.0,
                "require_overall_fct_us_mean_percent_ci95_high_at_most": 0.0,
                "require_le_8KB_fct_us_p95_percent_ci95_high_at_most": 0.5,
                "require_queue_mean_bytes_percent_ci95_high_at_most": 5.0,
                "require_queue_p99_bytes_percent_ci95_high_at_most": 5.0,
                "require_overall_flow_goodput_jain_difference_ci95_low_at_least": -0.005,
            },
        }
        self.write_json(root / "campaign.json", spec)
        self.write_json(root / "preflight.json", {
            "spec_path": str(root / "campaign.json"),
            "spec_sha256": sha256_file(root / "campaign.json")})
        self.write_json(root / "summary/mechanism-formal.json", {
            "phase": "formal", "passed": True, "performance_unsealed": True,
            "expected_runs": 10, "admitted_runs": 10,
            "preflight_sha256": sha256_file(root / "preflight.json"),
        })
        metrics = {}
        for name in (
                "gt_1MB_fct_us_mean", "gt_1MB_fct_us_p95",
                "overall_fct_us_mean", "le_8KB_fct_us_p95",
                "queue_bytes_mean", "queue_bytes_p99"):
            high = long_high if name == "gt_1MB_fct_us_mean" else -0.1
            metrics[name] = {"percent_vs_right": {
                "n": 5, "mean": high - 1.0,
                "ci95_low": high - 2.0, "ci95_high": high,
            }}
        metrics["overall_flow_goodput_jain"] = {"difference": {
            "n": 5, "mean": 0.0, "ci95_low": -0.001, "ci95_high": 0.001,
        }}
        self.write_json(root / "summary/general_report.json", {
            "performance": {"AliStorage50": {"paired": {
                "guard_elephant_target_minus_guard_k1": metrics}}}})

    def test_selects_only_after_all_frozen_gates_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.campaign(root)
            result = select(root)
            self.assertTrue(result["all_frozen_gates_passed"])
            self.assertEqual(result["selected_arm"], "guard_elephant_target")

    def test_retains_k1_when_long_flow_gate_misses(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.campaign(root, long_high=-5.4)
            result = select(root)
            self.assertFalse(result["all_frozen_gates_passed"])
            self.assertEqual(result["selected_arm"], "guard_k1")

    def test_refuses_sealed_performance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.campaign(root)
            path = root / "summary/mechanism-formal.json"
            value = json.loads(path.read_text())
            value["performance_unsealed"] = False
            path.write_text(json.dumps(value))
            with self.assertRaisesRegex(SelectionError, "still sealed"):
                select(root)


if __name__ == "__main__":
    unittest.main()
