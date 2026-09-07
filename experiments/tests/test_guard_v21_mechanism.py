import unittest

from experiments.analyze_guard_v17_general_mechanisms import prefix_fallback_closes


class GuardV21MechanismTests(unittest.TestCase):
    def row(self, timeouts=1, remaining=28000):
        return {
            "timeouts": timeouts, "degraded": timeouts,
            "fallback_batches": timeouts, "fallback_closed_batches": timeouts,
            "target_bytes": 52000, "received_bytes": 52000 - remaining,
            "remaining_bytes": remaining,
        }

    def test_closed_fallback_retains_deadline_remaining_evidence(self):
        self.assertTrue(prefix_fallback_closes(self.row()))

    def test_ready_path_requires_zero_remaining(self):
        self.assertTrue(prefix_fallback_closes(self.row(0, 0)))
        self.assertFalse(prefix_fallback_closes(self.row(0, 1)))

    def test_fallback_requires_closed_batches_and_byte_accounting(self):
        row = self.row()
        row["fallback_closed_batches"] = 0
        self.assertFalse(prefix_fallback_closes(row))
        row = self.row()
        row["received_bytes"] -= 1
        self.assertFalse(prefix_fallback_closes(row))


if __name__ == "__main__":
    unittest.main()
