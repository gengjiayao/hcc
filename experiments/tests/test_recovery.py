import tempfile
from pathlib import Path
import unittest

from experiments.analyze_recovery import SummaryError, target_queue


class RecoveryAnalysisTests(unittest.TestCase):
    def test_target_queue_requires_unique_receiver_egress(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "queue.txt"
            path.write_text(
                "port node_id if_index neighbor_id samples positive_samples average_bytes "
                "positive_average_bytes p95_bytes p99_bytes max_bytes tx_bytes\n"
                "port 16 1 18 100 2 1 50 0 0 100 1000\n"
                "port 17 8 15 100 5 4 80 0 0 100 2000\n",
                encoding="utf-8")
            metrics = target_queue(path, 15)
            self.assertEqual(metrics["node_id"], 17)
            self.assertEqual(metrics["if_index"], 8)
            self.assertEqual(metrics["tx_bytes"], 2000)
            with self.assertRaisesRegex(SummaryError, "found 0"):
                target_queue(path, 14)


if __name__ == "__main__":
    unittest.main()
