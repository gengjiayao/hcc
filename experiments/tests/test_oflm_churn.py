import csv
from pathlib import Path
import tempfile
import unittest

from experiments.analyze_oflm_churn import (
    LIFECYCLE_FIELDS,
    SummaryError,
    lifecycle_metrics,
    parse_lifecycle,
)


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


if __name__ == "__main__":
    unittest.main()
