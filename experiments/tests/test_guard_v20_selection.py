import json
from pathlib import Path
import tempfile
import unittest

from experiments.run_campaign import sha256_file
from experiments.select_guard_v20_elephant_k import SelectionError, select


class GuardV20SelectionTests(unittest.TestCase):
    def write_json(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, sort_keys=True))

    def campaign(self, root, high=-2.0):
        spec = {
            "mechanism_profile": "V20", "name": "v20",
            "seeds": [170, 171, 172, 173, 174],
            "arms": {"guard_k1": {}, "guard_k2": {}},
            "selection": {
                "require_gt_1MB_fct_us_mean_percent_ci95_high_at_most": -1.0,
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
            metrics[name] = {"percent_vs_right": {
                "n": 5, "mean": -3.0, "ci95_low": -4.0,
                "ci95_high": high,
            }}
        metrics["overall_flow_goodput_jain"] = {"difference": {
            "n": 5, "mean": 0.0, "ci95_low": -0.001, "ci95_high": 0.001,
        }}
        self.write_json(root / "summary/general_report.json", {
            "performance": {"AliStorage50": {"paired": {
                "guard_k2_minus_guard_k1": metrics}}}})

    def test_selects_k2_only_when_all_frozen_gates_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.campaign(root)
            result = select(root)
            self.assertTrue(result["all_frozen_gates_passed"])
            self.assertEqual(result["selected_arm"], "guard_k2")

    def test_retains_k1_when_one_gate_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.campaign(root, high=0.1)
            result = select(root)
            self.assertFalse(result["all_frozen_gates_passed"])
            self.assertEqual(result["selected_arm"], "guard_k1")

    def test_refuses_performance_before_mechanism_unseal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.campaign(root)
            mechanism = root / "summary/mechanism-formal.json"
            value = json.loads(mechanism.read_text())
            value["performance_unsealed"] = False
            mechanism.write_text(json.dumps(value))
            with self.assertRaisesRegex(SelectionError, "still sealed"):
                select(root)


if __name__ == "__main__":
    unittest.main()
