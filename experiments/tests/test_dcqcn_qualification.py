from pathlib import Path
import tempfile
import unittest

from experiments.summarize_campaign import SummaryError
from experiments.summarize_dcqcn_qualification import (
    CSV_FIELDS,
    DCQCN_FIELDS,
    parse_cnp_trace,
    parse_dcqcn_stats,
    parse_threshold_map,
    qualification_status,
)


class DcqcnQualificationTests(unittest.TestCase):
    def test_cumulative_stats_require_node_sum_to_equal_total(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stats.txt"
            header = "dcqcn node_id " + " ".join(DCQCN_FIELDS)
            path.write_text(
                header + "\n"
                + "dcqcn 0 1 2 3 4 5 6 7 8 9\n"
                + "dcqcn 1 10 20 30 40 50 60 70 80 90\n"
                + "dcqcn_total 11 22 33 44 55 66 77 88 99\n",
                encoding="utf-8",
            )
            total = parse_dcqcn_stats(path, expected_nodes=2)
            self.assertEqual(total["cnp_generated_ecn"], 11)
            self.assertEqual(total["actual_rate_decreases"], 77)
            path.write_text(path.read_text().replace("11 22", "12 22"), encoding="utf-8")
            with self.assertRaises(SummaryError):
                parse_dcqcn_stats(path, expected_nodes=2)

    def test_cnp_buckets_preserve_separate_ecn_and_ooo_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cnp.txt"
            path.write_text("100 0 5 2 6\n200 1 3 0 3\n", encoding="utf-8")
            total = parse_cnp_trace(path)
            self.assertEqual(total, {
                "cnp_generated_ecn": 8,
                "cnp_generated_ooo": 2,
                "cnp_trace_unique": 9,
            })
            path.write_text("100 0 5 2 4\n", encoding="utf-8")
            with self.assertRaises(SummaryError):
                parse_cnp_trace(path)

    def test_threshold_parser_selects_active_rate(self):
        values = parse_threshold_map(
            "3 20000000000 0 100000000000 0.20 400000000000 0.20",
            "PMAX_MAP",
        )
        self.assertEqual(values[20_000_000_000], 0.0)
        self.assertEqual(values[100_000_000_000], 0.2)

    def test_first_admitted_level_and_invalid_status(self):
        rows = [
            {"level": "L0", "admitted": False},
            {"level": "L1", "admitted": True},
            {"level": "L2", "admitted": True},
        ]
        self.assertEqual(qualification_status(rows), ("L1", "qualified_pilot"))
        for row in rows:
            row["admitted"] = False
        self.assertEqual(qualification_status(rows), (None, "invalid_no_safe_level"))

    def test_qualification_csv_cannot_emit_performance_metrics(self):
        forbidden_fragments = ("fct", "slowdown", "throughput", "goodput", "span", "jain")
        for field in CSV_FIELDS:
            self.assertFalse(any(fragment in field.lower() for fragment in forbidden_fragments))


if __name__ == "__main__":
    unittest.main()
