import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from experiments.summarize_campaign import SummaryError
from experiments.summarize_workload import main, summarize, validate_completions


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class WorkloadSummaryTests(unittest.TestCase):
    def test_completion_start_allows_two_nanosecond_driver_truncation(self):
        flows = [{"src": 1, "dst": 0, "size": 1000, "start_ns": 2000000001}]
        completions = [{"src": 1, "dst": 0, "size": 1000, "start_ns": 1999999999}]
        validate_completions(flows, completions, 2)

    def test_completion_start_rejects_larger_time_shift(self):
        flows = [{"src": 1, "dst": 0, "size": 1000, "start_ns": 2000000003}]
        completions = [{"src": 1, "dst": 0, "size": 1000, "start_ns": 1999999999}]
        with self.assertRaisesRegex(SummaryError, "more than 2ns"):
            validate_completions(flows, completions, 2)

    def make_run(self, directory, workload="incast"):
        root = Path(directory)
        output = root / "123"
        output.mkdir()
        snapshot = output / "123_input_flow.txt"
        snapshot.write_text(
            "3\n"
            "1 0 3 1000 2.000000000\n"
            "2 0 3 2000 2.000000001\n"
            "0 2 3 3000 2.000000002\n",
            encoding="utf-8",
        )
        manifest = root / "workload.manifest.json"
        manifest.write_text(json.dumps({
            "schema_version": 1,
            "workload": workload,
            "parameters": {"hosts": 3, "max_flows": 25000},
            "validation": {
                "status": "passed", "flow_count": 3, "total_bytes": 6000,
                "first_start_s": 2.0, "last_start_s": 2.000000002,
                "sha256": sha256(snapshot),
            },
        }), encoding="utf-8")
        (output / "config.txt").write_text(
            "FLOW_FILE mix/output/123/123_input_flow.txt\n"
            "PREFLIGHT_MAX_FLOWS 25000\n",
            encoding="utf-8",
        )
        (output / "123_out_fct.txt").write_text(
            "1 0 10 20 1000 2000000000 10000 5000\n"
            "2 0 11 21 2000 2000000001 20000 10000\n"
            "0 2 12 22 3000 2000000002 30000 15000\n",
            encoding="utf-8",
        )
        (output / "123_out_queue_stats.txt").write_text(
            "samples 100\naverage_bytes 12.5\np95_bytes 100\n"
            "p99_bytes 200\nmax_bytes 300\n",
            encoding="utf-8",
        )
        (output / "123_out_guard_stats.txt").write_text(
            "total 3 3 4 3 3 2 1 2 0 0 0 0 0 0 0\n"
            "switch_drops ingress 1 egress 2 total 3\n"
            "pfc_priority 3 1 1 1 5000 5000 0 0\n",
            encoding="utf-8",
        )
        (output / "123_out_pfc.txt").write_text(
            "10 1 0 2 3 1 5\n5010 1 0 2 3 0 0\n",
            encoding="utf-8",
        )
        return output, manifest

    @staticmethod
    def metric_map(summary):
        return {
            (row["metric"], row["scope"]): row["value"]
            for row in summary["metrics"]
        }

    def test_complete_run_emits_fct_goodput_queue_and_protocol_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            output, manifest = self.make_run(directory)
            result = summarize(output, manifest)
            metrics = self.metric_map(result)
            self.assertEqual(result["status"], "validated_complete")
            self.assertEqual(result["validation"]["completed_flow_count"], 3)
            self.assertAlmostEqual(metrics[("fct_us_mean", "all")], 20.0)
            self.assertIn(("fct_us_p95", "all"), metrics)
            self.assertIn(("fct_us_p99", "all"), metrics)
            self.assertNotIn(("fct_us_p999", "all"), metrics)
            self.assertIn(("aggregate_goodput_gbps", "all"), metrics)
            self.assertEqual(metrics[("homa_messages_completed", "all")], 0.0)
            self.assertIn(("receiver_incast_goodput_gbps", "receiver:0"), metrics)
            self.assertIn(("receiver_incast_flow_goodput_jain", "receiver:0"), metrics)
            self.assertIn(("receiver_incast_flow_goodput_min_gbps", "receiver:0"), metrics)
            self.assertIn(("receiver_incast_flow_goodput_max_gbps", "receiver:0"), metrics)
            self.assertIn(("receiver_aggregate_goodput_jain", "all"), metrics)
            self.assertEqual(metrics[("queue_bytes_max", "all")], 300)
            self.assertEqual(metrics[("grants_sent", "all")], 3)
            self.assertEqual(metrics[("switch_drops_total", "all")], 3)
            self.assertEqual(metrics[("pfc_pause_events", "all")], 1)
            self.assertEqual(metrics[("recovery_nacks_generated", "all")], 0)

    def test_ring_allreduce_is_only_an_open_loop_trace(self):
        with tempfile.TemporaryDirectory() as directory:
            output, manifest = self.make_run(directory, "ring-allreduce")
            result = summarize(output, manifest)
            self.assertEqual(result["completion_semantics"], "open_loop_trace")
            self.assertIn("no collective dependency", result["interpretation"])
            names = [row["metric"].lower() for row in result["metrics"]]
            self.assertIn("trace_completion_time_us", names)
            self.assertFalse(any("cct" in name or "jct" in name for name in names))

    def test_all_to_all_reports_communication_phase_cct(self):
        with tempfile.TemporaryDirectory() as directory:
            output, manifest = self.make_run(directory, "all-to-all")
            result = summarize(output, manifest)
            self.assertEqual(result["completion_semantics"], "communication_phase")
            names = [row["metric"] for row in result["metrics"]]
            self.assertIn("communication_phase_cct_us", names)

    def test_completion_count_is_strict(self):
        with tempfile.TemporaryDirectory() as directory:
            output, manifest = self.make_run(directory)
            fct = output / "123_out_fct.txt"
            fct.write_text(fct.read_text(encoding="utf-8").splitlines()[0] + "\n", encoding="utf-8")
            with self.assertRaisesRegex(SummaryError, "completed flow count"):
                summarize(output, manifest)

    def test_endpoint_size_and_start_must_match_snapshot(self):
        replacements = (("1 0 10", "1 2 10"), ("1000 2000000000", "1001 2000000000"),
                        ("2000000000", "2000000009"))
        for old, new in replacements:
            with self.subTest(replacement=new), tempfile.TemporaryDirectory() as directory:
                output, manifest = self.make_run(directory)
                fct = output / "123_out_fct.txt"
                fct.write_text(fct.read_text(encoding="utf-8").replace(old, new, 1), encoding="utf-8")
                with self.assertRaisesRegex(SummaryError, "completion .* differ"):
                    summarize(output, manifest)

    def test_manifest_hash_must_match_private_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            output, manifest = self.make_run(directory)
            data = json.loads(manifest.read_text(encoding="utf-8"))
            data["validation"]["sha256"] = "0" * 64
            manifest.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(SummaryError, "does not match snapshot"):
                summarize(output, manifest)

    def test_cli_atomically_writes_json_and_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            output, manifest = self.make_run(directory)
            self.assertEqual(main([str(output), "--manifest", str(manifest)]), 0)
            payload = json.loads((output / "workload_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "validated_complete")
            with (output / "workload_summary.csv").open(encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertGreater(len(rows), 20)
            self.assertEqual(rows[0]["workload"], "incast")

    def test_directed_hybrid_manifest_is_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            output, manifest = self.make_run(directory, workload="directed-hybrid")
            result = summarize(output, manifest)
            self.assertEqual(result["status"], "validated_complete")
            self.assertEqual(result["workload"], "directed-hybrid")

    def test_access_saturation_manifest_is_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            output, manifest = self.make_run(directory, workload="access-saturation")
            result = summarize(output, manifest)
            self.assertEqual(result["status"], "validated_complete")
            self.assertEqual(result["workload"], "access-saturation")


if __name__ == "__main__":
    unittest.main()
