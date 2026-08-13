from collections import defaultdict
import unittest

from experiments.summarize_alltoall_components import (
    PERCENT_METRICS,
    paired_rows,
    validate_flow_design,
    validate_mechanism,
)
from experiments.summarize_campaign import SummaryError


class AllToAllComponentSummaryTests(unittest.TestCase):
    def test_flow_design_requires_every_pg4_ordered_pair_once(self):
        flows = [
            {"src": src, "dst": dst, "pg": 4, "size": 524288, "start_ns": 1000 + src}
            for src in range(3) for dst in range(3) if src != dst
        ]
        validate_flow_design(flows, 3, 6, 4, 524288, 0.5)
        flows[0]["pg"] = 3
        with self.assertRaisesRegex(SummaryError, "priority group 4"):
            validate_flow_design(flows, 3, 6, 4, 524288, 0.5)

    def test_hpcc_admission_requires_actual_rate_changes(self):
        stats = defaultdict(int, {
            "hpcc_feedback_updates": 10,
            "hpcc_valid_feedback": 9,
            "hpcc_rate_updates_applied": 8,
            "hpcc_actual_rate_changes": 0,
        })
        with self.assertRaisesRegex(SummaryError, "actual HPCC changes"):
            validate_mechanism("hpcc", stats, 6, [])

    def test_paired_percent_is_formed_within_seed(self):
        rows = []
        values = {
            ("guard", 1): 2.0, ("hpcc", 1): 1.0, ("receiver_only", 1): 1.5,
            ("guard", 2): 3.0, ("hpcc", 2): 2.0, ("receiver_only", 2): 2.5,
        }
        for (arm, seed), cct in values.items():
            row = {"arm": arm, "seed": seed}
            row.update({metric: 1.0 for metric in PERCENT_METRICS})
            row["communication_phase_cct_us"] = cct
            rows.append(row)
        result = paired_rows(rows, percent=True)
        selected = next(
            row for row in result
            if row["comparison"] == "guard_vs_hpcc"
            and row["metric"] == "communication_phase_cct_us"
        )
        # Mean of seed-local percentages: mean(100%, 50%) = 75%, not the
        # 66.7% obtained by taking a percentage of cross-seed means.
        self.assertAlmostEqual(selected["mean"], 75.0)


if __name__ == "__main__":
    unittest.main()
