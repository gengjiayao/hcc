import copy
import unittest

from experiments.aggregate_oflm_sensitivity import (
    BASELINE,
    CELL_PARAMETERS,
    SensitivityError,
    aggregate,
)


class OflmSensitivityTests(unittest.TestCase):
    def make_summary(self, cell, seed, flow_hash):
        beta, gamma = CELL_PARAMETERS[cell]
        effect = 100.0 * beta + 10.0 * gamma + seed
        mechanism = {
            "trace_rows": 40,
            "registrations": 40,
            "selected_registrations": 40,
            "proactive_releases": 40,
            "completion_releases": 0,
            "proactive_release_fraction": 1.0,
            "release_lead_mean_ns": 1000 + effect,
            "release_lead_median_ns": 900 + effect,
            "churn_release_lead_mean_ns": 800 + effect,
            "churn_release_lead_median_ns": 700 + effect,
            "remaining_bytes_mean": 600 + effect,
            "remaining_bytes_median": 500 + effect,
            "churn_remaining_bytes_mean": 400 + effect,
            "churn_remaining_bytes_median": 300 + effect,
            "active_set_area_ns": 2000 + effect,
            "churn_active_set_area_ns": 1000 + effect,
            "grants_sent": 500 + effect,
            "grants_per_registration": 10 + effect / 40,
            "max_active_flows": 10,
        }
        group = {
            "mean_fct_us": 10 + effect,
            "p95_fct_us": 11 + effect,
            "p99_fct_us": 12 + effect,
            "mean_slowdown": 2 + effect,
            "p95_slowdown": 3 + effect,
            "p99_slowdown": 4 + effect,
        }
        return {
            "status": "validated_complete",
            "workload": "oflm-churn",
            "configuration": {
                "beta": beta,
                "gamma": gamma,
                "seed": seed,
                "selective_registration": 1,
                "proactive_release": 1,
                "priority_group": 4,
                "churn_rounds": 8,
                "churn_interval_us": 25.0,
                "churn_jitter_us": 5.0,
                "lifecycle_trace_max_lines": 100,
                "controller_trace_enabled": 0,
            },
            "validation": {
                "generated_flow_count": 72,
                "completed_flow_count": 72,
                "switch_drops": 0,
                "recovery_events": 0,
                "pfc_pause_events": 0,
                "pfc_resume_events": 0,
                "artifact_bytes": 1000,
            },
            "mechanism": mechanism,
            "controller_diagnostics": {
                "status": "disabled_by_configuration",
                "truncated_rows": 0,
                "hpcc_valid_feedback": 100,
                "hpcc_rate_updates_applied": 90,
                "hpcc_actual_rate_changes": 80,
                "reactive_binding_updates": 70,
                "grant_binding_updates": 60,
                "tie_binding_updates": 0,
            },
            "performance": {
                "queue_mean_bytes": 1 + effect,
                "queue_p95_bytes": 2 + effect,
                "queue_p99_bytes": 3 + effect,
                "queue_max_bytes": 4 + effect,
                "target_receiver_queue": {
                    "average_bytes": 1 + effect,
                    "p95_bytes": 2 + effect,
                    "p99_bytes": 3 + effect,
                    "max_bytes": 4 + effect,
                },
                "fct_groups": {
                    "le_bdp_churn": copy.deepcopy(group),
                    "gt_bdp_churn": copy.deepcopy(group),
                    "elephant": copy.deepcopy(group),
                },
            },
            "provenance": {
                "flow_sha256": flow_hash,
                "output_directory": f"/tmp/{cell}-seed{seed}",
            },
        }

    def matrix(self, seeds):
        return {
            cell: {
                seed: self.make_summary(cell, seed, f"flow-hash-seed-{seed}")
                for seed in seeds
            }
            for cell in CELL_PARAMETERS
        }

    def test_seed1_gate_enforces_grid_and_mechanism_gradient(self):
        result = aggregate(self.matrix((1,)))
        self.assertEqual(result["status"], "seed1_mechanism_gate_passed")
        self.assertTrue(result["mechanism_gradient"]["passed"])

        flat = self.matrix((1,))
        for cell in CELL_PARAMETERS:
            for name in (
                "churn_release_lead_mean_ns",
                "churn_remaining_bytes_mean",
                "churn_active_set_area_ns",
            ):
                flat[cell][1]["mechanism"][name] = 1
        with self.assertRaisesRegex(SensitivityError, "did not change"):
            aggregate(flat)

    def test_complete_grid_reports_paired_cell_minus_baseline(self):
        result = aggregate(self.matrix((1, 2, 3, 4, 5)))
        self.assertEqual(result["status"], "validated_complete_sensitivity")
        self.assertEqual(result["seeds"], [1, 2, 3, 4, 5])
        paired = result["paired_vs_baseline"]["b0p5-g1"][
            "churn_release_lead_mean_ns"]
        self.assertEqual(
            paired["absolute_difference_cell_minus_baseline"]["mean"], 37.5)
        self.assertEqual(
            paired["relative_percent_cell_minus_baseline"]["n"], 5)
        baseline = result["paired_vs_baseline"][BASELINE][
            "churn_release_lead_mean_ns"]
        self.assertEqual(
            baseline["absolute_difference_cell_minus_baseline"]["mean"], 0)

    def test_rejects_cross_cell_hash_mismatch_and_incomplete_mechanism(self):
        matrix = self.matrix((1,))
        matrix["b0-g1"][1]["provenance"]["flow_sha256"] = "different"
        with self.assertRaisesRegex(SensitivityError, "one flow hash"):
            aggregate(matrix)

        matrix = self.matrix((1,))
        matrix["b0-g1"][1]["mechanism"]["proactive_releases"] = 39
        with self.assertRaisesRegex(SensitivityError, "proactive_releases"):
            aggregate(matrix)


if __name__ == "__main__":
    unittest.main()
