import tempfile
import unittest

from experiments.summarize_campaign import (
    confidence_interval,
    paired_rows,
    percentile,
    validate_mode,
)


def metric_row(cc, seed, value, flow_hash="same"):
    return {
        "row_type": "run", "comparison": "", "stage": "components",
        "cc": cc, "topo": "topo", "cdf": "cdf", "netload": 40,
        "simul_time": 0.02, "bw": 100, "pfc": 1, "irn": 0,
        "guard_lambda": 1.0, "guard_beta": 0.125, "guard_gamma": 1.0,
        "guard_keep_last_hop_int": 0, "seed": seed, "metric": "fct_slowdown_mean",
        "category": "all", "value": value, "n": 100, "mean": "",
        "ci95_low": "", "ci95_high": "", "flow_sha256": flow_hash,
        "run_key": f"{cc}-{seed}",
    }


class SummaryTests(unittest.TestCase):
    def test_t_interval_is_computed_across_seed_values(self):
        mean, low, high = confidence_interval([1, 2, 3, 4, 5])
        self.assertEqual(mean, 3)
        self.assertAlmostEqual(low, 1.0370716, places=6)
        self.assertAlmostEqual(high, 4.9629284, places=6)

    def test_linear_percentile(self):
        self.assertEqual(percentile([1, 2, 3], 50), 2)
        self.assertAlmostEqual(percentile([1, 2, 3, 4], 95), 3.85)

    def test_full_guard_mode_requires_both_loops(self):
        params = {"cc": "guard"}
        config = {"CC_MODE": "11"}
        good = {"grants_sent": 1, "grants_received": 1, "hpcc_feedback_updates": 1}
        bad = dict(good, hpcc_feedback_updates=0)
        self.assertEqual(validate_mode(params, config, good), [])
        self.assertIn("nonzero grants and HPCC updates", validate_mode(params, config, bad)[0])

    def test_paired_difference_preserves_all_seeds(self):
        rows = []
        for seed in range(1, 6):
            rows.extend([metric_row("guard", seed, seed + 1), metric_row("hpcc", seed, seed)])
        result, errors = paired_rows(rows, [{
            "name": "guard_vs_hpcc", "stages": ["components"],
            "vary": "cc", "a": "guard", "b": "hpcc",
        }])
        self.assertEqual(errors, [])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["n"], 5)
        self.assertEqual(result[0]["mean"], 1)

    def test_paired_difference_rejects_mismatched_traffic(self):
        rows = [metric_row("guard", 1, 2, "a"), metric_row("hpcc", 1, 1, "b")]
        result, errors = paired_rows(rows, [{
            "name": "guard_vs_hpcc", "stages": ["components"],
            "vary": "cc", "a": "guard", "b": "hpcc",
        }])
        self.assertEqual(result, [])
        self.assertIn("flow hash mismatch", errors[0])


if __name__ == "__main__":
    unittest.main()
