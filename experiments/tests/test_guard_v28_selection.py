import json
from pathlib import Path
import tempfile
import unittest

from experiments.run_campaign import sha256_file
from experiments.select_guard_v23_elephant_spillover import SelectionError
from experiments.select_guard_v28_transport_window_floor import PERCENT_GATES, select


class GuardV28SelectionTests(unittest.TestCase):
    def write_json(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, sort_keys=True))

    def campaign(self, root, long_homa_high=-0.5):
        selection = {
            field: (0.0 if "homa" in field or "hpcc" in field else 5.0)
            for _comparison, _metric, field in PERCENT_GATES
        }
        selection.update({
            "require_candidate_minus_control_gt_1MB_mean_percent_ci95_high_at_most":
                -1.0,
            "require_candidate_minus_control_overall_mean_percent_ci95_high_at_most":
                0.0,
            "require_candidate_minus_control_le_8KB_p95_percent_ci95_high_at_most":
                0.5,
            "require_candidate_minus_control_jain_difference_ci95_low_at_least":
                -0.005,
        })
        spec = {
            "mechanism_profile": "V28", "name": "v28",
            "seeds": [210, 211, 212, 213, 214],
            "arms": {
                "guard_k1": {}, "guard_window_floor": {},
                "hpcc": {}, "homa": {},
            },
            "selection": selection,
        }
        self.write_json(root / "campaign.json", spec)
        self.write_json(root / "preflight.json", {
            "spec_path": str(root / "campaign.json"),
            "spec_sha256": sha256_file(root / "campaign.json")})
        self.write_json(root / "summary/mechanism-formal.json", {
            "phase": "formal", "passed": True, "performance_unsealed": True,
            "expected_runs": 20, "admitted_runs": 20,
            "preflight_sha256": sha256_file(root / "preflight.json"),
        })
        comparisons = {}
        for comparison, metric, _field in PERCENT_GATES:
            high = -1.5
            if comparison == "guard_window_floor_minus_homa" and metric == \
                    "gt_1MB_fct_us_mean":
                high = long_homa_high
            comparisons.setdefault(comparison, {})[metric] = {
                "percent_vs_right": {
                    "n": 5, "mean": high - 0.5,
                    "ci95_low": high - 1.0, "ci95_high": high,
                }}
        comparisons["guard_window_floor_minus_guard_k1"][
            "overall_flow_goodput_jain"] = {"difference": {
                "n": 5, "mean": 0.0, "ci95_low": -0.001,
                "ci95_high": 0.001}}
        self.write_json(root / "summary/general_report.json", {
            "performance": {"AliStorage40": {"paired": comparisons}}})

    def test_selects_only_when_every_frozen_gate_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.campaign(root)
            result = select(root)
            self.assertTrue(result["all_frozen_gates_passed"])
            self.assertEqual(result["selected_arm"], "guard_window_floor")

    def test_retains_k1_if_homa_long_flow_gate_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.campaign(root, long_homa_high=0.01)
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
