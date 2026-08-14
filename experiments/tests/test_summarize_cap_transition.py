import tempfile
import unittest
from pathlib import Path

from experiments.summarize_campaign import SummaryError
from experiments.summarize_cap_transition import parse_controller, parse_flow


class CapTransitionSummaryTest(unittest.TestCase):
    def test_controller_footer_is_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "controller.csv"
            path.write_text(
                "time_ns,flow_id,event_type,binding,rate_changed\n"
                "10,0,grant,grant,1\n"
                "20,0,hpcc,reactive,1\n"
                "# attempted 2 written 2 truncated 0\n",
                encoding="utf-8",
            )
            rows, footer = parse_controller(path)
            self.assertEqual(2, len(rows))
            self.assertEqual({"attempted": 2, "written": 2, "truncated": 0}, footer)

    def test_controller_rejects_truncation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "controller.csv"
            path.write_text(
                "time_ns,flow_id,event_type,binding,rate_changed\n"
                "10,0,grant,grant,1\n"
                "# attempted 2 written 1 truncated 1\n",
                encoding="utf-8",
            )
            with self.assertRaises(SummaryError):
                parse_controller(path)

    def test_flow_geometry_requires_two_phases(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flows.txt"
            receiver = [(0, 8), (9, 8), (10, 8), (11, 8)]
            fabric = [(src, src + 8) for src in range(1, 8)]
            rows = receiver + fabric
            text = "11\n" + "".join(
                f"{src} {dst} 4 4194304 {2.0 if index < 4 else 2.0001:.7f}\n"
                for index, (src, dst) in enumerate(rows)
            )
            path.write_text(text, encoding="utf-8")
            self.assertEqual(11, len(parse_flow(path)))


if __name__ == "__main__":
    unittest.main()
