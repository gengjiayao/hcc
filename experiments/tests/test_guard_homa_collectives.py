import json
from pathlib import Path
import tempfile
import unittest

from experiments.run_guard_homa_collectives import _flow_count, read_spec, run_command
from experiments.summarize_guard_homa_collectives import mean_ci, validate_mechanism


ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "experiments" / "campaigns" / "guard_homa_collectives.json"


class CollectiveRunnerTest(unittest.TestCase):
    def test_frozen_spec_and_arm_commands(self):
        spec = read_spec(SPEC)
        trace = {"seed": 3, "flow_file": "/tmp/frozen-flow.txt", "buffer_mb": 16}
        guard = run_command(ROOT, spec, trace, "guard")
        hpcc = run_command(ROOT, spec, trace, "hpcc")
        homa = run_command(ROOT, spec, trace, "homa")
        self.assertIn("guard", guard)
        self.assertIn("--guard_lambda", guard)
        self.assertIn("1.4", guard)
        self.assertEqual(guard[guard.index("--buffer") + 1], "16")
        self.assertNotIn("--guard_lambda", hpcc)
        self.assertIn("homa", homa)
        self.assertIn("--homa_resend_timeout_us", homa)

    def test_flow_header_is_strict(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "flow.txt"
            path.write_text("2\n0 1 4 1000 2.0\n1 0 4 1000 2.0\n")
            self.assertEqual(_flow_count(path), 2)
            path.write_text("3\n0 1 4 1000 2.0\n")
            with self.assertRaises(Exception):
                _flow_count(path)


class CollectiveSummaryTest(unittest.TestCase):
    def values(self):
        return {
            "grants_sent": 0, "grants_received": 0,
            "hpcc_actual_rate_changes": 0, "homa_data_packets": 0,
            "homa_grants_sent": 0, "homa_messages_completed": 0,
            "homa_messages_tracked": 0, "homa_completed_message_ids": 0,
            "homa_completion_notices_sent": 0,
            "homa_completion_notices_received": 0,
            "homa_duplicate_data_after_completion": 0,
            "homa_completion_notices_replayed": 0,
        }

    def test_mechanism_arms_are_distinct(self):
        guard = self.values()
        guard.update(grants_sent=1, hpcc_actual_rate_changes=1)
        self.assertEqual(validate_mechanism("guard", guard, 10, 0), [])

        hpcc = self.values()
        hpcc["hpcc_actual_rate_changes"] = 1
        self.assertEqual(validate_mechanism("hpcc", hpcc, 10, 0), [])

        homa = self.values()
        homa.update(
            homa_data_packets=10, homa_grants_sent=2,
            homa_messages_tracked=10, homa_messages_completed=10,
            homa_completed_message_ids=10, homa_completion_notices_sent=12,
            homa_completion_notices_received=10,
            homa_duplicate_data_after_completion=2,
            homa_completion_notices_replayed=2,
        )
        self.assertEqual(validate_mechanism("homa", homa, 10, 2), [])

    def test_homa_completion_replay_must_close_exactly(self):
        homa = self.values()
        homa.update(
            homa_data_packets=10, homa_grants_sent=2,
            homa_messages_tracked=10, homa_messages_completed=10,
            homa_completed_message_ids=10, homa_completion_notices_sent=12,
            homa_completion_notices_received=10,
            homa_duplicate_data_after_completion=2,
            homa_completion_notices_replayed=1,
        )
        self.assertIn(
            "homa_completion_replay_count_mismatch",
            validate_mechanism("homa", homa, 10, 2),
        )

    def test_guard_requires_actual_reactive_change(self):
        guard = self.values()
        guard["grants_sent"] = 1
        self.assertIn(
            "guard_actual_hpcc_changes_zero",
            validate_mechanism("guard", guard, 10, 0),
        )

    def test_five_seed_interval(self):
        interval = mean_ci([1, 2, 3, 4, 5])
        self.assertEqual(interval["n"], 5)
        self.assertEqual(interval["mean"], 3)
        self.assertLess(interval["ci95_low"], 3)
        self.assertGreater(interval["ci95_high"], 3)


if __name__ == "__main__":
    unittest.main()
