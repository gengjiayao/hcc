import csv
from pathlib import Path
import tempfile
import unittest

from experiments.summarize_component_workload import (
    confidence_intervals,
    mean_ci,
    parse_controller,
    percentile,
    scoped_fct,
)


class ComponentWorkloadSummaryTests(unittest.TestCase):
    def test_percentile_and_five_seed_t_interval(self):
        self.assertEqual(percentile([1.0, 2.0, 3.0], 50), 2.0)
        stats = mean_ci([1, 2, 3, 4, 5])
        self.assertEqual(stats["n"], 5)
        self.assertEqual(stats["mean"], 3.0)
        self.assertLess(stats["ci95_low"], 3.0)
        self.assertGreater(stats["ci95_high"], 3.0)

    def test_scoped_fct_reports_span_goodput_and_fairness(self):
        rows = [
            {"size": 1000, "start_ns": 0, "finish_ns": 1000,
             "duration_ns": 1000, "fct_us": 1.0, "slowdown": 2.0},
            {"size": 1000, "start_ns": 500, "finish_ns": 2500,
             "duration_ns": 2000, "fct_us": 2.0, "slowdown": 4.0},
        ]
        metrics = scoped_fct(rows, "incast")
        self.assertEqual(metrics["incast_completion_span_us"], 2.5)
        self.assertEqual(metrics["incast_fct_us_mean"], 1.5)
        self.assertAlmostEqual(metrics["incast_aggregate_goodput_gbps"], 6.4)
        self.assertLess(metrics["incast_flow_goodput_jain"], 1.0)

    def test_controller_binding_stops_at_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "controller.csv"
            fields = ("time_ns", "flow_id", "event_type", "binding", "fast_react")
            rows = []
            for flow_id in range(655):
                binding = "reactive" if flow_id < 15 else "grant"
                rows.append({"time_ns": 100, "flow_id": flow_id,
                             "event_type": "hpcc", "binding": binding,
                             "fast_react": 0})
                rows.append({"time_ns": 200, "flow_id": flow_id,
                             "event_type": "complete", "binding": binding,
                             "fast_react": 0})
            # A late event is audited but must not extend flow 0's binding span.
            rows.append({"time_ns": 300, "flow_id": 0,
                         "event_type": "hpcc", "binding": "grant",
                         "fast_react": 0})
            with path.open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
                stream.write(f"# attempted {len(rows)} written {len(rows)} truncated 0\n")
            footer, timelines, metrics = parse_controller(path, range(15))
            self.assertEqual(footer["truncated"], 0)
            self.assertEqual(len(timelines), 655)
            self.assertEqual(metrics["incast_binding_reactive_ratio_pooled"], 1.0)
            self.assertEqual(metrics["overall_binding_ever_reactive_flows"], 15.0)
            self.assertEqual(metrics["controller_post_complete_events"], 1.0)
            first = next(row for row in timelines if row["flow_id"] == 0)
            self.assertEqual(first["observed_binding_span_ns"], 100)

    def test_paired_ci_preserves_all_five_seeds(self):
        rows = []
        for seed in range(1, 6):
            rows.extend([
                {"seed": seed, "arm": "full", "metric": float(seed + 1)},
                {"seed": seed, "arm": "hpcc", "metric": float(seed)},
                {"seed": seed, "arm": "receiver", "metric": float(seed + 2)},
            ])
        report, _csv = confidence_intervals(rows)
        paired = report["paired"]["full_minus_hpcc"]["metric"]["difference"]
        self.assertEqual(paired["n"], 5)
        self.assertEqual(paired["mean"], 1.0)


if __name__ == "__main__":
    unittest.main()
