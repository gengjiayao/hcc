import tempfile
import json
from pathlib import Path
import unittest

from experiments.aggregate_recovery import aggregate
from experiments.analyze_recovery import SummaryError, target_queue
from experiments.select_recovery_tier import evaluate


class RecoveryAnalysisTests(unittest.TestCase):
    def test_target_queue_requires_unique_receiver_egress(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "queue.txt"
            path.write_text(
                "port node_id if_index neighbor_id samples positive_samples average_bytes "
                "positive_average_bytes p95_bytes p99_bytes max_bytes tx_bytes\n"
                "port 16 1 18 100 2 1 50 0 0 100 1000\n"
                "port 17 8 15 100 5 4 80 0 0 100 2000\n",
                encoding="utf-8")
            metrics = target_queue(path, 15)
            self.assertEqual(metrics["node_id"], 17)
            self.assertEqual(metrics["if_index"], 8)
            self.assertEqual(metrics["tx_bytes"], 2000)
            with self.assertRaisesRegex(SummaryError, "found 0"):
                target_queue(path, 14)

    def test_selector_withholds_performance_until_both_mechanisms_fire(self):
        ladder = json.loads((
            Path(__file__).resolve().parents[1] / "recovery_ladder.json"
        ).read_text(encoding="utf-8"))

        def summary(arm):
            mechanism = {
                "pfc_matched_intervals": 2 if arm == "pfc" else 0,
                "pfc_cumulative_pause_ns": 100 if arm == "pfc" else 0,
                "pfc_unmatched_events": 0,
                "pfc_pause_count": 2 if arm == "pfc" else 0,
                "pfc_resume_count": 2 if arm == "pfc" else 0,
                "irn_nacks_generated": 2 if arm == "irn" else 0,
                "irn_nacks_received": 2 if arm == "irn" else 0,
                "irn_retransmit_packets": 2 if arm == "irn" else 0,
                "irn_retransmit_bytes": 2000 if arm == "irn" else 0,
            }
            return {
                "status": "validated_complete", "arm": arm,
                "provenance": {"flow_sha256": "same"},
                "configuration": {"error_rate_per_link": 0.0},
                "validation": {"generated_flow_count": 32, "completed_flow_count": 32},
                "mechanism": mechanism, "performance": {"hidden": arm},
            }

        summaries = {arm: summary(arm) for arm in ("pfc", "irn")}
        selected = evaluate(ladder, "tier1", summaries)
        self.assertEqual(selected["decision"], "selected")
        self.assertIn("post_selection_performance", selected)

        failed = {arm: dict(value) for arm, value in summaries.items()}
        failed["irn"] = dict(failed["irn"])
        failed["irn"]["mechanism"] = dict(failed["irn"]["mechanism"])
        failed["irn"]["mechanism"]["irn_retransmit_packets"] = 0
        rejected = evaluate(ladder, "tier1", failed)
        self.assertEqual(rejected["decision"], "advance_to_next_tier")
        self.assertNotIn("post_selection_performance", rejected)

    def test_aggregate_keeps_seed_specific_matched_flow_hashes(self):
        def performance(value):
            return {
                "duration_mean_us": value, "duration_p95_us": value,
                "duration_p99_us": value, "slowdown_mean": value,
                "slowdown_p95": value, "slowdown_p99": value,
                "queue_average_bytes": value, "queue_p95_bytes": 0,
                "queue_p99_bytes": value, "queue_max_bytes": value,
                "target_receiver_queue": {
                    "average_bytes": value, "positive_average_bytes": value,
                    "p95_bytes": 0, "p99_bytes": value, "max_bytes": value,
                },
            }

        def selection(seed):
            return {
                "decision": "selected", "tier": {"name": "tier2"},
                "flow_sha256": f"seed-{seed}",
                "post_selection_performance": {
                    "pfc": performance(2.0), "irn": performance(1.0),
                },
                "mechanism_by_arm": {
                    "pfc": {"pfc_matched_intervals": 2},
                    "irn": {"pfc_matched_intervals": 0},
                },
            }

        result = aggregate({1: selection(1), 2: selection(2)})
        self.assertEqual(result["flow_sha256_by_seed"], {"1": "seed-1", "2": "seed-2"})
        reduction = result["paired_relative_irn_reduction_vs_pfc"]["slowdown_mean"]
        self.assertEqual(reduction["mean"], 0.5)
        self.assertEqual(
            result["paired_relative_irn_reduction_vs_pfc"]["queue_p95_bytes"]["status"],
            "undefined_nonpositive_baseline")


if __name__ == "__main__":
    unittest.main()
