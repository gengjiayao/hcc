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


if __name__ == "__main__":
    unittest.main()
