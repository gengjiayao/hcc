import copy
import json
from pathlib import Path
import tempfile
import unittest

from experiments.run_campaign import CampaignError
from experiments.run_guard_feature_ablation import run_command
from experiments.run_guard_parameter_search import read_spec
from experiments.summarize_guard_parameter_search import select_candidate


REPO = Path(__file__).resolve().parents[2]
SPEC_PATH = REPO / "experiments" / "campaigns" / "guard_parameter_search_stage1.json"


class GuardParameterSearchTest(unittest.TestCase):
    def setUp(self):
        self.spec = read_spec(SPEC_PATH)

    def rows(self, primary=101.0, eligible=True):
        rows = []
        baseline = self.spec["selection"]["baseline_arm"]
        for seed in self.spec["seeds"]:
            for arm in self.spec["arms"]:
                metrics = {
                    "gt_1MB_fct_us_mean": 100.0 if arm == baseline else primary,
                    "overall_fct_us_mean": 10.0 if arm == baseline else 10.0,
                    "le_8KB_slowdown_p95": 1.0,
                    "queue_bytes_mean": 100.0,
                }
                if arm == "eta010_rho075_q64" and eligible:
                    metrics.update({
                        "gt_1MB_fct_us_mean": 95.0,
                        "overall_fct_us_mean": 10.05,
                        "le_8KB_slowdown_p95": 1.005,
                        "queue_bytes_mean": 105.0,
                    })
                if arm == "eta020_rho050_q64":
                    metrics.update({
                        "gt_1MB_fct_us_mean": 94.0,
                        "overall_fct_us_mean": 10.2,
                    })
                rows.append({"seed": seed, "arm": arm, "metrics": metrics})
        return rows

    def test_frozen_grid_is_complete(self):
        self.assertEqual(len(self.spec["arms"]), 9)
        observed = {
            (arm["guard_min_share_fraction"], arm["guard_remaining_exponent"])
            for arm in self.spec["arms"].values()
        }
        self.assertEqual(len(observed), 9)

    def test_reader_rejects_incomplete_grid(self):
        broken = copy.deepcopy(self.spec)
        broken["arms"].pop("eta020_rho050_q64")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "spec.json"
            path.write_text(json.dumps(broken), encoding="utf-8")
            with self.assertRaises(CampaignError):
                read_spec(path)

    def test_selection_applies_primary_and_constraint_gates(self):
        result = select_candidate(self.spec, self.rows())
        self.assertEqual(result["selected_arm"], "eta010_rho075_q64")
        rejected = next(
            row for row in result["candidates"]
            if row["arm"] == "eta020_rho050_q64")
        self.assertFalse(rejected["eligible"])

    def test_selection_falls_back_when_no_candidate_qualifies(self):
        result = select_candidate(self.spec, self.rows(eligible=False))
        self.assertEqual(result["selected_arm"], self.spec["selection"]["baseline_arm"])
        self.assertEqual(result["status"], "no_candidate_beats_frozen_gates")

    def test_run_command_includes_candidate_parameters(self):
        workload = {"name": "AliStorage50", "cdf": "AliStorage2019"}
        trace = {"seed": 26, "path": "/tmp/frozen-flow.txt"}
        command = run_command(
            REPO, self.spec, workload, trace, "eta010_rho075_q64")
        joined = " ".join(command)
        self.assertIn("--guard_min_share_fraction 0.1", joined)
        self.assertIn("--guard_remaining_exponent 0.75", joined)
        self.assertIn("--guard_srpt_quantum_packets 64", joined)


if __name__ == "__main__":
    unittest.main()
