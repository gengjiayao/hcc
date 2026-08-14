import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from experiments.generate_cap_transition import build, write_trace


class CapTransitionGeneratorTest(unittest.TestCase):
    def test_geometry_and_phase_order(self):
        rows = build(3)
        self.assertEqual(11, len(rows))
        self.assertEqual((0, 8), rows[0][:2])
        self.assertEqual({(9, 8), (10, 8), (11, 8)}, {row[:2] for row in rows[1:4]})
        self.assertEqual({(src, src + 8) for src in range(1, 8)},
                         {row[:2] for row in rows[4:]})
        self.assertGreater(min(row[4] for row in rows[4:]), max(row[4] for row in rows[:4]))

    def test_output_is_deterministic_and_hashed(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.txt"
            second = Path(directory) / "second.txt"
            write_trace(7, first)
            write_trace(7, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            manifest = json.loads(first.with_suffix(".txt.manifest.json").read_text())
            self.assertEqual(hashlib.sha256(first.read_bytes()).hexdigest(), manifest["sha256"])

    def test_seeds_change_jitter(self):
        self.assertNotEqual(build(1), build(2))


if __name__ == "__main__":
    unittest.main()
