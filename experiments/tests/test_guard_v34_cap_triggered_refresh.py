import json
from pathlib import Path
import tempfile
import unittest

from experiments.run_campaign import sha256_file
from experiments.run_general_workloads import CampaignError, read_spec, run_command
from experiments.select_guard_v23_elephant_spillover import SelectionError
from experiments.select_guard_v34_cap_triggered_refresh import PERCENT_GATES, select
from experiments.summarize_general_workloads import mechanism_checks


ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "experiments/campaigns/guard_v34_cap_triggered_refresh_development.json"


class GuardV34CapTriggeredRefreshTests(unittest.TestCase):
    def write_json(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")

    def test_spec_freezes_only_refresh_trigger_and_fresh_seeds(self):
        spec = read_spec(SPEC)
        self.assertEqual(spec["mechanism_profile"], "V34")
        self.assertEqual(spec["seeds"], [235, 236, 237, 238, 239])
        self.assertEqual(set(spec["arms"]), {
            "guard_ack_slack_window", "guard_cap_refresh", "hpcc", "homa"})
        control = dict(spec["arms"]["guard_ack_slack_window"])
        candidate = dict(spec["arms"]["guard_cap_refresh"])
        self.assertEqual(control["guard_cap_triggered_refresh"], 0)
        self.assertEqual(candidate["guard_cap_triggered_refresh"], 1)
        control.pop("guard_cap_triggered_refresh")
        candidate.pop("guard_cap_triggered_refresh")
        self.assertEqual(candidate, control)
        self.assertEqual(spec["defaults"]["guard_elephant_spillover_enter_reports"], 1)
        self.assertEqual(spec["defaults"]["guard_elephant_spillover_exit_reports"], 2)

    def test_candidate_cli_enables_only_cap_refresh(self):
        spec = read_spec(SPEC)
        workload = {
            "name": "AliStorage40", "cdf": "AliStorage2019",
            "selected_profile": "primary",
            "attempts": {"primary": {"profile": {
                "topo": "leaf_spine_16_100G_OS4", "hosts": 16,
                "oversubscription": 4, "simul_time": 0.02,
                "netload": 40, "bw": 100,
            }}},
        }
        trace = {"seed": 235, "path": "/tmp/v34-flow.txt", "sha256": "same"}
        control = run_command(
            ROOT, spec, workload, trace, "guard_ack_slack_window", True)
        candidate = run_command(
            ROOT, spec, workload, trace, "guard_cap_refresh", True)
        self.assertEqual(control[control.index("--guard_cap_triggered_refresh") + 1], "0")
        self.assertEqual(candidate[candidate.index("--guard_cap_triggered_refresh") + 1], "1")
        for option, expected in (
                ("--guard_transport_window_floor_rtt_ns", "8320"),
                ("--guard_transport_window_floor_after_first_grant", "1"),
                ("--guard_transport_window_whole_flow_first_gate", "1"),
                ("--guard_transport_window_ack_slack_packets", "16")):
            self.assertEqual(candidate[candidate.index(option) + 1], expected)
            self.assertEqual(control[control.index(option) + 1], expected)

    def test_rejects_any_second_candidate_change(self):
        value = json.loads(SPEC.read_text(encoding="utf-8"))
        value["arms"]["guard_cap_refresh"]["guard_remaining_exponent"] = 2.0
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v34.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(CampaignError, "other than cap-triggered refresh"):
                read_spec(path)

    def campaign(self, root, homa_long_high=-0.5):
        selection = {
            field: (-0.01 if field.endswith("jain_difference_ci95_low_at_least")
                    else 5.0)
            for _comparison, _metric, field in PERCENT_GATES
        }
        selection.update({
            "require_candidate_minus_control_gt_1MB_mean_percent_ci95_high_at_most": 2.0,
            "require_candidate_minus_control_overall_mean_percent_ci95_high_at_most": 0.5,
            "require_candidate_minus_control_overall_p95_percent_ci95_high_at_most": 0.0,
            "require_candidate_minus_control_overall_p99_percent_ci95_high_at_most": 0.0,
            "require_candidate_minus_control_le_8KB_p95_percent_ci95_high_at_most": 0.5,
            "require_candidate_minus_control_queue_mean_percent_ci95_high_at_most": 5.0,
            "require_candidate_minus_control_queue_p99_percent_ci95_high_at_most": 5.0,
            "require_candidate_minus_control_jain_difference_ci95_low_at_least": -0.005,
            "require_candidate_minus_homa_jain_difference_ci95_low_at_least": -0.01,
            "require_candidate_minus_homa_gt_1MB_mean_percent_ci95_high_at_most": 0.0,
            "require_candidate_minus_homa_overall_mean_percent_ci95_high_at_most": 0.0,
            "require_candidate_minus_homa_overall_p95_percent_ci95_high_at_most": 0.0,
            "require_candidate_minus_homa_overall_p99_percent_ci95_high_at_most": 0.0,
            "require_candidate_minus_homa_le_8KB_p95_percent_ci95_high_at_most": 0.0,
            "require_candidate_minus_homa_queue_mean_percent_ci95_high_at_most": 0.0,
            "require_candidate_minus_homa_queue_p99_percent_ci95_high_at_most": 0.0,
            "require_candidate_minus_hpcc_overall_mean_percent_ci95_high_at_most": 0.0,
            "require_candidate_minus_hpcc_overall_p95_percent_ci95_high_at_most": 0.0,
            "require_candidate_minus_hpcc_overall_p99_percent_ci95_high_at_most": 0.0,
            "require_candidate_minus_hpcc_gt_1MB_mean_percent_ci95_high_at_most": 0.0,
        })
        spec = {
            "mechanism_profile": "V34", "name": "v34",
            "seeds": [235, 236, 237, 238, 239],
            "arms": {"guard_ack_slack_window": {}, "guard_cap_refresh": {},
                     "hpcc": {}, "homa": {}},
            "selection": selection,
        }
        self.write_json(root / "campaign.json", spec)
        self.write_json(root / "preflight.json", {
            "spec_path": str(root / "campaign.json"),
            "spec_sha256": sha256_file(root / "campaign.json"),
        })
        self.write_json(root / "summary/mechanism-formal.json", {
            "phase": "formal", "passed": True, "performance_unsealed": True,
            "expected_runs": 20, "admitted_runs": 20,
            "preflight_sha256": sha256_file(root / "preflight.json"),
        })
        paired = {}
        for comparison, metric, _field in PERCENT_GATES:
            high = homa_long_high if (
                comparison == "guard_cap_refresh_minus_homa" and
                metric == "gt_1MB_fct_us_mean") else -0.5
            paired.setdefault(comparison, {})[metric] = {"percent_vs_right": {
                "n": 5, "mean": high - 0.5, "ci95_low": high - 1.0,
                "ci95_high": high,
            }}
        for comparison in (
                "guard_cap_refresh_minus_guard_ack_slack_window",
                "guard_cap_refresh_minus_homa"):
            paired.setdefault(comparison, {})["overall_flow_goodput_jain"] = {
                "difference": {"n": 5, "mean": -0.001,
                               "ci95_low": -0.002, "ci95_high": 0.0}}
        self.write_json(root / "summary/general_report.json", {
            "performance": {"AliStorage40": {"paired": paired}}})

    def test_selection_requires_homa_long_flow_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); self.campaign(root)
            self.assertEqual(select(root)["selected_arm"], "guard_cap_refresh")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); self.campaign(root, 0.01)
            self.assertEqual(select(root)["selected_arm"], "guard_ack_slack_window")

    def test_selection_refuses_sealed_performance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); self.campaign(root)
            path = root / "summary/mechanism-formal.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["performance_unsealed"] = False
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(SelectionError, "still sealed"):
                select(root)

    def test_alias_uses_full_guard_mechanism_checks(self):
        stats = {
            "grants_sent": 1, "grants_received": 1,
            "hpcc_valid_feedback": 1, "hpcc_actual_rate_changes": 1,
            "reactive_binding_updates": 1, "guard_sender_srpt_enabled": 1,
            "guard_sender_srpt_selections": 1, "guard_sender_srpt_non_rr": 1,
            "guard_tail_bypass_flows": 1, "guard_remaining_refresh_events": 1,
            "guard_adaptive_target_enabled": 0,
        }
        self.assertTrue(all(mechanism_checks("guard_cap_refresh", stats).values()))


if __name__ == "__main__":
    unittest.main()
