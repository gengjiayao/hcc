#!/usr/bin/env python3

import tempfile
import unittest
from pathlib import Path

from experiments.summarize_campaign import SummaryError
from experiments.summarize_directed_hybrid import mean_ci, parse_flow_geometry


class DirectedHybridSummaryTests(unittest.TestCase):
    def make_flow(self, root, jitter=0.4e-6):
        path = Path(root) / "flow.txt"
        rows = []
        endpoints = [(src, src + 8) for src in range(8)]
        endpoints.extend((src, 8) for src in range(9, 16))
        for index, (src, dst) in enumerate(endpoints):
            rows.append(
                f"{src} {dst} 4 {2 * 1024 * 1024} {2.0035 + index * jitter / 14:.9f}"
            )
        path.write_text("15\n" + "\n".join(rows) + "\n")
        return path

    def test_geometry_accepts_frozen_dual_bottleneck(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(len(parse_flow_geometry(self.make_flow(root))), 15)

    def test_geometry_rejects_excess_jitter(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(SummaryError, "jitter"):
                parse_flow_geometry(self.make_flow(root, jitter=1e-6))

    def test_mean_ci_uses_five_run_samples(self):
        result = mean_ci([1, 2, 3, 4, 5])
        self.assertEqual(result["n"], 5)
        self.assertEqual(result["mean"], 3)
        self.assertLess(result["ci95_low"], 3)
        self.assertGreater(result["ci95_high"], 3)
        with self.assertRaisesRegex(SummaryError, "five"):
            mean_ci([1, 2, 3])


if __name__ == "__main__":
    unittest.main()
