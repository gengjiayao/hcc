#!/usr/bin/env python3

import contextlib
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest


REPOSITORY = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))

from experiments import generate_workload  # noqa: E402


class GenerateWorkloadTest(unittest.TestCase):
    def test_default_workloads_are_bounded_and_valid(self):
        expected_counts = {
            "incast": 15,
            "hybrid": 527,
            "directed-hybrid": 15,
            "ring-allreduce": 480,
            "all-to-all": 240,
        }
        with tempfile.TemporaryDirectory() as temporary:
            for workload, expected_count in expected_counts.items():
                output = pathlib.Path(temporary) / (workload + ".txt")
                with contextlib.redirect_stdout(io.StringIO()):
                    result = generate_workload.main([
                        "--workload", workload,
                        "--output", str(output),
                    ])
                self.assertEqual(result, 0)
                manifest = json.loads(pathlib.Path(
                    str(output) + ".manifest.json").read_text(encoding="utf-8"))
                self.assertEqual(manifest["validation"]["status"], "passed")
                self.assertEqual(manifest["validation"]["flow_count"], expected_count)
                self.assertLessEqual(expected_count, generate_workload.HARD_MAX_FLOWS)
                self.assertGreater(manifest["validation"]["total_bytes"], 0)
                self.assertGreaterEqual(manifest["parameters"]["duration_ms"], 10)

    def test_hybrid_is_deterministic_for_a_seed(self):
        with tempfile.TemporaryDirectory() as temporary:
            first = pathlib.Path(temporary) / "first.txt"
            second = pathlib.Path(temporary) / "second.txt"
            common = ["--workload", "hybrid", "--seed", "19"]
            with contextlib.redirect_stdout(io.StringIO()):
                generate_workload.main(common + ["--output", str(first)])
                generate_workload.main(common + ["--output", str(second)])
            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_directed_hybrid_separates_fabric_and_receiver_scopes(self):
        common = [
            "--workload", "directed-hybrid", "--output", "unused",
            "--hosts", "16", "--duration-ms", "10",
            "--flow-bytes", str(2 * 1024 * 1024),
            "--incast-jitter-us", "0.5", "--priority-group", "4",
        ]
        first = generate_workload.generate(generate_workload.parse_args(
            common + ["--seed", "1"]))
        repeat = generate_workload.generate(generate_workload.parse_args(
            common + ["--seed", "1"]))
        second = generate_workload.generate(generate_workload.parse_args(
            common + ["--seed", "2"]))
        self.assertEqual(first, repeat)
        self.assertNotEqual(first, second)
        self.assertEqual(len(first), 15)
        endpoints = {(flow.src, flow.dst) for flow in first}
        self.assertTrue({(src, src + 8) for src in range(8)} <= endpoints)
        self.assertTrue({(src, 8) for src in range(9, 16)} <= endpoints)
        self.assertTrue(all(flow.pg == 4 for flow in first))
        self.assertTrue(all(flow.size_bytes == 2 * 1024 * 1024 for flow in first))
        self.assertLessEqual(
            max(flow.start_s for flow in first) - min(flow.start_s for flow in first),
            0.5e-6)

    def test_incast_seeded_jitter_changes_trace_but_remains_synchronized(self):
        first = generate_workload.parse_args([
            "--workload", "incast", "--output", "unused", "--seed", "1",
            "--incast-jitter-us", "1",
        ])
        second = generate_workload.parse_args([
            "--workload", "incast", "--output", "unused", "--seed", "2",
            "--incast-jitter-us", "1",
        ])
        first_flows = generate_workload.generate(first)
        second_flows = generate_workload.generate(second)
        self.assertNotEqual(first_flows, second_flows)
        self.assertLessEqual(
            max(flow.start_s for flow in first_flows) -
            min(flow.start_s for flow in first_flows), 1e-6)
        self.assertEqual(
            sorted((flow.src, flow.dst, flow.size_bytes) for flow in first_flows),
            sorted((flow.src, flow.dst, flow.size_bytes) for flow in second_flows))

    def test_incast_fanin_and_rounds_repeat_fixed_sender_set(self):
        args = generate_workload.parse_args([
            "--workload", "incast", "--output", "unused", "--hosts", "64",
            "--duration-ms", "10",
            "--incast-destination", "63", "--incast-fanin", "15",
            "--incast-rounds", "2", "--incast-round-gap-us", "20",
            "--incast-jitter-us", "0.5", "--seed", "7",
        ])
        flows = generate_workload.generate(args)
        self.assertEqual(len(flows), 30)
        first_round = [flow for flow in flows if flow.start_s < 2.00351]
        second_round = [flow for flow in flows if flow.start_s > 2.00351]
        self.assertEqual(sorted(flow.src for flow in first_round), list(range(15)))
        self.assertEqual(sorted(flow.src for flow in second_round), list(range(15)))
        self.assertTrue(all(flow.dst == 63 for flow in flows))

    def test_all_to_all_seeded_jitter_changes_order_with_fixed_pairs(self):
        common = [
            "--workload", "all-to-all", "--output", "unused",
            "--hosts", "16", "--duration-ms", "20",
            "--all-to-all-jitter-us", "0.5", "--priority-group", "4",
        ]
        first = generate_workload.generate(generate_workload.parse_args(
            common + ["--seed", "1"]))
        repeat = generate_workload.generate(generate_workload.parse_args(
            common + ["--seed", "1"]))
        second = generate_workload.generate(generate_workload.parse_args(
            common + ["--seed", "2"]))
        self.assertEqual(first, repeat)
        self.assertNotEqual(first, second)
        expected_pairs = {(src, dst) for src in range(16) for dst in range(16)
                          if src != dst}
        for flows in (first, second):
            self.assertEqual({(flow.src, flow.dst) for flow in flows}, expected_pairs)
            self.assertEqual({flow.pg for flow in flows}, {4})
            self.assertLessEqual(
                max(flow.start_s for flow in flows) -
                min(flow.start_s for flow in flows), 0.5e-6)

    def test_ring_seeded_jitter_preserves_steps_and_changes_trace(self):
        common = [
            "--workload", "ring-allreduce", "--output", "unused",
            "--hosts", "16", "--duration-ms", "20",
            "--ring-jitter-us", "0.5", "--priority-group", "4",
        ]
        first = generate_workload.generate(generate_workload.parse_args(
            common + ["--seed", "1"]))
        repeat = generate_workload.generate(generate_workload.parse_args(
            common + ["--seed", "1"]))
        second = generate_workload.generate(generate_workload.parse_args(
            common + ["--seed", "2"]))
        self.assertEqual(first, repeat)
        self.assertNotEqual(first, second)
        self.assertEqual(len(first), 480)
        self.assertEqual(
            sorted((flow.src, flow.dst, flow.size_bytes) for flow in first),
            sorted((flow.src, flow.dst, flow.size_bytes) for flow in second))
        step_interval = 0.02 / 31
        for step in range(30):
            nominal = 2.0 + 0.02 * (step + 1) / 31
            step_flows = [
                flow for flow in first
                if nominal <= flow.start_s < nominal + 0.5e-6
            ]
            self.assertEqual(len(step_flows), 16)
            self.assertTrue(all(
                flow.start_s < nominal + min(0.5e-6, step_interval)
                for flow in step_flows))

    def test_oflm_churn_has_fixed_rate_bdp_mix_behind_active_elephants(self):
        args = generate_workload.parse_args([
            "--workload", "oflm-churn", "--output", "unused",
        ])
        flows = generate_workload.generate(args)
        bdp = generate_workload.DEFAULT_OFLM_BDP_BYTES
        elephants = [flow for flow in flows if flow.size_bytes == 16 * 1024 * 1024]
        churn = [flow for flow in flows if flow.size_bytes != 16 * 1024 * 1024]

        self.assertEqual(len(elephants), 8)
        self.assertEqual(len({flow.start_s for flow in elephants}), 1)
        self.assertEqual(len(churn), 64)
        self.assertGreaterEqual(min(flow.start_s for flow in churn), 2.005)
        self.assertEqual(
            {flow.size_bytes for flow in churn},
            {bdp - 1, bdp, bdp + 1, 2 * bdp})
        intervals_us = [
            round((right.start_s - left.start_s) * 1_000_000, 6)
            for left, right in zip(churn, churn[1:])
        ]
        self.assertEqual(set(intervals_us), {25.0})
        self.assertTrue(all(flow.src < 8 <= flow.dst for flow in flows))
        self.assertEqual({flow.dst for flow in flows}, {15})
        self.assertEqual(sum(flow.size_bytes > bdp for flow in flows), 40)
        self.assertEqual(sum(flow.size_bytes <= bdp for flow in flows), 32)

    def test_oflm_churn_rejects_arrivals_outside_duration(self):
        args = generate_workload.parse_args([
            "--workload", "oflm-churn", "--output", "unused",
            "--oflm-churn-rounds", "100", "--oflm-churn-interval-us", "100",
        ])
        with self.assertRaisesRegex(ValueError, "exceed the duration"):
            generate_workload.generate(args)

    def test_oflm_churn_seeded_jitter_changes_only_start_times(self):
        common = [
            "--workload", "oflm-churn", "--output", "unused",
            "--oflm-churn-jitter-us", "5",
        ]
        first = generate_workload.generate(generate_workload.parse_args(
            common + ["--seed", "1"]))
        second = generate_workload.generate(generate_workload.parse_args(
            common + ["--seed", "2"]))
        self.assertNotEqual(first, second)
        self.assertEqual(
            sorted((flow.src, flow.dst, flow.pg, flow.size_bytes) for flow in first),
            sorted((flow.src, flow.dst, flow.pg, flow.size_bytes) for flow in second))
        first_churn = [flow for flow in first if flow.size_bytes != 16 * 1024 * 1024]
        self.assertTrue(all(
            0 <= (flow.start_s - (2.006 + index * 25e-6)) < 5e-6
            for index, flow in enumerate(first_churn)))

    def test_oflm_churn_jitter_must_be_below_interval(self):
        args = generate_workload.parse_args([
            "--workload", "oflm-churn", "--output", "unused",
            "--oflm-churn-jitter-us", "25",
        ])
        with self.assertRaisesRegex(ValueError, "below the arrival interval"):
            generate_workload.generate(args)

    def test_receiver_share_freezes_target_set_and_source_limited_background(self):
        common = [
            "--workload", "receiver-share", "--output", "unused",
            "--receiver-share-flows", "2",
            "--receiver-share-background-flows", "3",
            "--receiver-share-jitter-us", "1",
            "--priority-group", "4",
        ]
        first = generate_workload.generate(generate_workload.parse_args(
            common + ["--seed", "1"]))
        repeat = generate_workload.generate(generate_workload.parse_args(
            common + ["--seed", "1"]))
        second = generate_workload.generate(generate_workload.parse_args(
            common + ["--seed", "2"]))
        self.assertEqual(first, repeat)
        self.assertNotEqual(first, second)
        self.assertEqual(len(first), 5)
        target = [flow for flow in first if flow.dst == 15]
        background = [flow for flow in first if flow.dst != 15]
        self.assertEqual({flow.src for flow in target}, {0, 1})
        self.assertEqual({flow.src for flow in background}, {0})
        self.assertEqual({flow.dst for flow in background}, {2, 3, 4})
        self.assertEqual({flow.pg for flow in first}, {4})
        self.assertLessEqual(
            max(flow.start_s for flow in first) - min(flow.start_s for flow in first),
            1e-6)

    def test_receiver_share_rejects_background_outside_two_target_case(self):
        args = generate_workload.parse_args([
            "--workload", "receiver-share", "--output", "unused",
            "--receiver-share-flows", "4",
            "--receiver-share-background-flows", "3",
        ])
        with self.assertRaisesRegex(ValueError, "exactly two target flows"):
            generate_workload.generate(args)

    def test_generation_rejects_short_or_excessive_workloads(self):
        short = generate_workload.parse_args([
            "--workload", "incast", "--output", "unused", "--duration-ms", "9",
        ])
        with self.assertRaisesRegex(ValueError, "at least 10"):
            generate_workload.generate(short)

        excessive = generate_workload.parse_args([
            "--workload", "all-to-all", "--output", "unused", "--hosts", "200",
        ])
        with self.assertRaisesRegex(ValueError, "exceeding --max-flows"):
            generate_workload.generate(excessive)

    def test_readback_validator_rejects_endpoint_and_time_errors(self):
        with tempfile.TemporaryDirectory() as temporary:
            invalid_endpoint = pathlib.Path(temporary) / "endpoint.txt"
            invalid_endpoint.write_text(
                "1\n16 0 3 1024 2.001000000\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid endpoints"):
                generate_workload.validate_flow_file(
                    str(invalid_endpoint), 16, 2.0, 2.02, 25000)

            invalid_time = pathlib.Path(temporary) / "time.txt"
            invalid_time.write_text(
                "1\n0 1 3 1024 2.021000000\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "time window"):
                generate_workload.validate_flow_file(
                    str(invalid_time), 16, 2.0, 2.02, 25000)

    def test_run_driver_rejects_custom_input_over_flow_cap(self):
        with tempfile.TemporaryDirectory() as temporary:
            flow_file = pathlib.Path(temporary) / "two-flows.txt"
            flow_file.write_text(
                "2\n0 1 3 1024 2.001000000\n1 0 3 1024 2.002000000\n",
                encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable, "run.py",
                    "--topo", "leaf_spine_16_100G_OS4",
                    "--netload", "40",
                    "--simul_time", "0.01",
                    "--flow_file", str(flow_file),
                    "--max_flows", "1",
                ],
                cwd=str(REPOSITORY), text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("exceeding --max_flows 1", completed.stdout)
            self.assertNotIn("Running simulation", completed.stdout)

    def test_run_driver_rejects_invalid_link_error_rate(self):
        completed = subprocess.run(
            [
                sys.executable, "run.py", "--topo", "leaf_spine_16_100G_OS4",
                "--simul_time", "0.01", "--error_rate_per_link", "1.0",
            ],
            cwd=str(REPOSITORY), text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("--error_rate_per_link must be in [0, 1)", completed.stdout)
        self.assertNotIn("Running simulation", completed.stdout)


if __name__ == "__main__":
    unittest.main()
