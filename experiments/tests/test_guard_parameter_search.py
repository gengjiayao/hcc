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
QUANTUM_SPEC_PATH = REPO / "experiments" / "campaigns" / "guard_parameter_search_stage1b.json"
TAIL_SPEC_PATH = REPO / "experiments" / "campaigns" / "guard_parameter_search_stage1c.json"
LAMBDA_SPEC_PATH = REPO / "experiments" / "campaigns" / "guard_parameter_search_stage1d.json"
CONCURRENCY_SPEC_PATH = REPO / "experiments" / "campaigns" / "guard_parameter_search_stage2a.json"
ELEPHANT_SPEC_PATH = REPO / "experiments" / "campaigns" / "guard_parameter_search_stage2b.json"
THRESHOLD_SPEC_PATH = REPO / "experiments" / "campaigns" / "guard_parameter_search_stage2c.json"
TAIL_THRESHOLD_SPEC_PATH = REPO / "experiments" / "campaigns" / "guard_parameter_search_stage3a.json"
ADAPTIVE_TARGET_SPEC_PATH = REPO / "experiments" / "campaigns" / "guard_parameter_search_stage3b.json"
ADAPTIVE_SCOPE_SPEC_PATH = REPO / "experiments" / "campaigns" / "guard_parameter_search_stage3c.json"
ADAPTIVE_REFINEMENT_SPEC_PATH = REPO / "experiments" / "campaigns" / "guard_parameter_search_stage3d.json"


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

    def test_quantum_grid_is_complete(self):
        spec = read_spec(QUANTUM_SPEC_PATH)
        self.assertEqual(spec["search_kind"], "srpt_quantum")
        self.assertEqual(
            {arm["guard_srpt_quantum_packets"] for arm in spec["arms"].values()},
            {16, 32, 64, 128},
        )

    def test_tail_gate_grid_is_complete_and_forwarded(self):
        spec = read_spec(TAIL_SPEC_PATH)
        self.assertEqual(spec["search_kind"], "tail_gate")
        observed = {
            (arm["guard_tail_congestion_gate"], arm["guard_tail_safe_ratio"],
             arm["guard_tail_safe_samples"])
            for arm in spec["arms"].values()
        }
        self.assertEqual(observed, {
            (0, 0.9, 2), (1, 0.8, 2), (1, 0.9, 2), (1, 1.0, 2),
        })
        command = run_command(
            REPO, spec, {"name": "AliStorage50", "cdf": "AliStorage2019"},
            {"seed": 41, "path": "/tmp/frozen-flow.txt"},
            "tail_gate_r090_s2")
        joined = " ".join(command)
        self.assertIn("--guard_tail_congestion_gate 1", joined)
        self.assertIn("--guard_tail_safe_ratio 0.9", joined)
        self.assertIn("--guard_tail_safe_samples 2", joined)

    def test_high_load_lambda_grid_is_complete(self):
        spec = read_spec(LAMBDA_SPEC_PATH)
        self.assertEqual(spec["search_kind"], "lambda_high_load")
        self.assertEqual(
            {arm["guard_lambda"] for arm in spec["arms"].values()},
            {1.8, 2.0, 2.2, 2.4},
        )
        command = run_command(
            REPO, spec, {"name": "AliStorage50", "cdf": "AliStorage2019"},
            {"seed": 46, "path": "/tmp/frozen-flow.txt"}, "lambda220")
        self.assertIn("--guard_lambda 2.2", " ".join(command))

    def test_receiver_concurrency_grid_uses_fresh_seeds(self):
        spec = read_spec(CONCURRENCY_SPEC_PATH)
        self.assertEqual(spec["search_kind"], "receiver_concurrency")
        self.assertEqual(spec["seeds"], [51, 52, 53, 54, 55])
        self.assertEqual(
            {arm["guard_receiver_concurrency"] for arm in spec["arms"].values()},
            {0, 1},
        )
        command = run_command(
            REPO, spec, {"name": "AliStorage50", "cdf": "AliStorage2019"},
            {"seed": 51, "path": "/tmp/frozen-flow.txt"}, "concurrency_one")
        self.assertIn("--guard_receiver_concurrency 1", " ".join(command))

    def test_elephant_concurrency_freezes_threshold_and_new_seeds(self):
        spec = read_spec(ELEPHANT_SPEC_PATH)
        self.assertEqual(spec["search_kind"], "elephant_concurrency")
        self.assertEqual(spec["seeds"], [56, 57, 58, 59, 60])
        self.assertEqual(spec["defaults"]["guard_concurrency_min_bdps"], 8.0)
        command = run_command(
            REPO, spec, {"name": "AliStorage50", "cdf": "AliStorage2019"},
            {"seed": 56, "path": "/tmp/frozen-flow.txt"}, "elephant_one")
        joined = " ".join(command)
        self.assertIn("--guard_receiver_concurrency 1", joined)
        self.assertIn("--guard_concurrency_min_bdps 8.0", joined)

    def test_elephant_threshold_grid_is_complete(self):
        spec = read_spec(THRESHOLD_SPEC_PATH)
        self.assertEqual(spec["search_kind"], "elephant_threshold")
        self.assertEqual(spec["seeds"], [61, 62, 63, 64, 65])
        candidates = [
            arm for arm in spec["arms"].values()
            if arm["guard_receiver_concurrency"] == 1
        ]
        self.assertEqual(
            {arm["guard_concurrency_min_bdps"] for arm in candidates},
            {4.0, 6.0, 8.0, 12.0},
        )

    def test_tail_threshold_grid_retains_selected_elephant_policy(self):
        spec = read_spec(TAIL_THRESHOLD_SPEC_PATH)
        self.assertEqual(spec["search_kind"], "tail_bypass_threshold")
        self.assertEqual(spec["seeds"], [76, 77, 78, 79, 80])
        self.assertEqual(spec["defaults"]["guard_receiver_concurrency"], 1)
        self.assertEqual(spec["defaults"]["guard_concurrency_min_bdps"], 12.0)
        self.assertEqual(
            {arm["guard_tail_bypass_bdps"] for arm in spec["arms"].values()},
            {1.0, 2.0, 4.0, 8.0},
        )
        command = run_command(
            REPO, spec, {"name": "AliStorage50", "cdf": "AliStorage2019"},
            {"seed": 76, "path": "/tmp/frozen-flow.txt"}, "tail2")
        joined = " ".join(command)
        self.assertIn("--guard_tail_bypass_bdps 2.0", joined)
        self.assertIn("--guard_receiver_concurrency 1", joined)
        self.assertIn("--guard_concurrency_min_bdps 12.0", joined)

    def test_adaptive_target_grid_uses_fresh_seeds_and_fixed_floor(self):
        spec = read_spec(ADAPTIVE_TARGET_SPEC_PATH)
        self.assertEqual(spec["search_kind"], "adaptive_fabric_target")
        self.assertEqual(spec["seeds"], [81, 82, 83, 84, 85])
        self.assertEqual(spec["defaults"]["guard_target_floor"], 0.95)
        enabled = [
            arm for arm in spec["arms"].values()
            if arm["guard_adaptive_fabric_target"] == 1
        ]
        self.assertEqual(
            {arm["guard_queue_budget_bdps"] for arm in enabled},
            {0.25, 0.5, 1.0},
        )
        command = run_command(
            REPO, spec, {"name": "AliStorage50", "cdf": "AliStorage2019"},
            {"seed": 81, "path": "/tmp/frozen-flow.txt"}, "budget050")
        joined = " ".join(command)
        self.assertIn("--guard_adaptive_fabric_target 1", joined)
        self.assertIn("--guard_target_floor 0.95", joined)
        self.assertIn("--guard_queue_budget_bdps 0.5", joined)

    def test_adaptive_scope_grid_excludes_selected_elephants(self):
        spec = read_spec(ADAPTIVE_SCOPE_SPEC_PATH)
        self.assertEqual(spec["search_kind"], "adaptive_target_scope")
        self.assertEqual(spec["seeds"], [86, 87, 88, 89, 90])
        self.assertEqual(spec["defaults"]["guard_queue_budget_bdps"], 0.5)
        enabled = [
            arm for arm in spec["arms"].values()
            if arm["guard_adaptive_fabric_target"] == 1
        ]
        self.assertEqual(
            {arm["guard_adaptive_target_max_bdps"] for arm in enabled},
            {4.0, 8.0, 12.0},
        )
        command = run_command(
            REPO, spec, {"name": "AliStorage50", "cdf": "AliStorage2019"},
            {"seed": 86, "path": "/tmp/frozen-flow.txt"}, "scope8")
        self.assertIn("--guard_adaptive_target_max_bdps 8.0", " ".join(command))

    def test_adaptive_scope_refinement_uses_only_active_scope(self):
        spec = read_spec(ADAPTIVE_REFINEMENT_SPEC_PATH)
        self.assertEqual(spec["search_kind"], "adaptive_scope_refinement")
        self.assertEqual(spec["seeds"], [91, 92, 93, 94, 95])
        self.assertEqual(set(spec["arms"]), {"adaptive_off", "scope12"})
        self.assertEqual(
            spec["arms"]["scope12"]["guard_adaptive_target_max_bdps"], 12.0)

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

    def test_selection_rejects_candidate_with_failed_seed(self):
        rows = self.rows()
        rejected_arm = "eta010_rho075_q64"
        for row in rows:
            row["passed"] = not (
                row["arm"] == rejected_arm and row["seed"] == self.spec["seeds"][-1])
            row["failures"] = [] if row["passed"] else ["controller path inactive"]
        result = select_candidate(self.spec, rows)
        rejected = next(row for row in result["candidates"] if row["arm"] == rejected_arm)
        self.assertFalse(rejected["eligible"])
        self.assertEqual(rejected["rejected_seeds"], [self.spec["seeds"][-1]])

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
