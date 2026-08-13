import csv
import copy
import json
from pathlib import Path
import tempfile
import unittest

from experiments.analyze_oflm_churn import (
    LIFECYCLE_FIELDS,
    SummaryError,
    grouped_fct_metrics,
    lifecycle_metrics,
    parse_lifecycle,
)
from experiments.select_oflm_churn_tier import evaluate


class OflmChurnAnalysisTests(unittest.TestCase):
    def write_trace(self, directory, rows):
        path = Path(directory) / "lifecycle.csv"
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=LIFECYCLE_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def valid_rows(self):
        return [
            {
                "flow_id": 1, "size_bytes": 16 * 1024 * 1024,
                "receiver_node": 15, "first_rx_ns": 90, "register_ns": 100,
                "release_ns": 200, "complete_ns": 200,
                "release_reason": "completion", "remaining_bytes_at_release": 0,
                "active_before_register": 0, "active_after_register": 1,
                "active_before_release": 2, "active_after_release": 1,
            },
            {
                "flow_id": 2, "size_bytes": 104001,
                "receiver_node": 15, "first_rx_ns": 110, "register_ns": 120,
                "release_ns": 180, "complete_ns": 220,
                "release_reason": "proactive", "remaining_bytes_at_release": 1000,
                "active_before_register": 1, "active_after_register": 2,
                "active_before_release": 2, "active_after_release": 1,
            },
        ]

    def test_lifecycle_metrics_separate_churn_area_and_release_lead(self):
        with tempfile.TemporaryDirectory() as directory:
            rows = parse_lifecycle(self.write_trace(directory, self.valid_rows()))
        metrics = lifecycle_metrics(rows, 16 * 1024 * 1024)
        self.assertEqual(metrics["trace_rows"], 2)
        self.assertEqual(metrics["churn_trace_rows"], 1)
        self.assertEqual(metrics["active_set_area_ns"], 160)
        self.assertEqual(metrics["churn_active_set_area_ns"], 60)
        self.assertEqual(metrics["churn_release_lead_median_ns"], 40)
        self.assertEqual(metrics["trace_max_active_flows"], 2)
        self.assertEqual(metrics["proactive_release_fraction"], 0.5)

    def test_lifecycle_parser_rejects_duplicate_or_invalid_release(self):
        duplicate = self.valid_rows()
        duplicate[1]["flow_id"] = duplicate[0]["flow_id"]
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_trace(directory, duplicate)
            with self.assertRaisesRegex(SummaryError, "duplicate flow IDs"):
                parse_lifecycle(path)

        invalid = self.valid_rows()
        invalid[1]["remaining_bytes_at_release"] = 0
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_trace(directory, invalid)
            with self.assertRaisesRegex(SummaryError, "no remaining bytes"):
                parse_lifecycle(path)

    def test_grouped_fct_metrics_keep_admission_classes_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "1_out_fct.txt"
            path.write_text(
                "0 15 1 2 16777216 2000000000 400 100\n"
                "1 15 3 4 104000 2000000100 200 100\n"
                "2 15 5 6 104001 2000000200 300 100\n",
                encoding="utf-8")
            metrics = grouped_fct_metrics(Path(directory), 104000, 16777216)
        self.assertEqual(metrics["all"]["flows"], 3)
        self.assertEqual(metrics["elephant"]["flows"], 1)
        self.assertEqual(metrics["churn"]["flows"], 2)
        self.assertEqual(metrics["le_bdp_churn"]["mean_slowdown"], 2.0)
        self.assertEqual(metrics["gt_bdp_churn"]["mean_slowdown"], 3.0)

    def test_frozen_selector_withholds_performance_until_mechanisms_pass(self):
        ladder = json.loads((
            Path(__file__).resolve().parents[1] / "oflm_churn_ladder.json"
        ).read_text(encoding="utf-8"))
        summaries = {}
        for combo in ("00", "01", "10", "11"):
            selective, proactive = map(int, combo)
            summaries[combo] = {
                "status": "validated_complete", "workload": "oflm-churn",
                "configuration": {
                    "selective_registration": selective,
                    "proactive_release": proactive,
                },
                "provenance": {"flow_sha256": "same-flow"},
                "validation": {
                    "completed_flow_count": 72, "generated_flow_count": 72,
                    "switch_drops": 0, "recovery_events": 0,
                    "artifact_bytes": 1000,
                },
                "mechanism": {
                    "registrations": 40 if selective else 72,
                    "proactive_release_fraction": 1.0 if proactive else 0.0,
                    "churn_release_lead_median_ns": 10000 if proactive else 0,
                    "churn_active_set_area_ns": (
                        60 if selective and proactive else
                        70 if selective else
                        80 if proactive else 100),
                    "grants_sent": 600 if selective else 1000,
                },
                "performance": {"not_used_for_selection": combo},
            }
        selected = evaluate(ladder, "tier1", summaries)
        self.assertEqual(selected["decision"], "selected")
        self.assertIn("post_selection_performance", selected)

        failed = copy.deepcopy(summaries)
        failed["11"]["mechanism"]["churn_active_set_area_ns"] = 70
        rejected = evaluate(ladder, "tier1", failed)
        self.assertEqual(rejected["decision"], "advance_to_next_tier")
        self.assertNotIn("post_selection_performance", rejected)


if __name__ == "__main__":
    unittest.main()
