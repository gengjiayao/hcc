#!/usr/bin/env python3
"""Source contracts for default-off fixed-K elephant aging."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
HW_H = (ROOT / "src/point-to-point/model/rdma-hw.h").read_text()
HW_CC = (ROOT / "src/point-to-point/model/rdma-hw.cc").read_text()
QP_H = (ROOT / "src/point-to-point/model/rdma-queue-pair.h").read_text()
DRIVER = (ROOT / "scratch/network-load-balance.cc").read_text()
RUN = (ROOT / "run.py").read_text()
CPP_TEST = (ROOT / "src/point-to-point/test/point-to-point-test.cc").read_text()


class GuardElephantAgingV22Test(unittest.TestCase):
    def test_policy_is_default_off_and_bounded(self):
        start = HW_CC.index('.AddAttribute("GuardElephantAgingRtts"')
        attribute = HW_CC[start:start + 700]
        self.assertIn("DoubleValue(0.0)", attribute)
        self.assertIn("MakeDoubleChecker<double>(0.0, 64.0)", attribute)
        self.assertIn("double m_guardElephantAgingRtts", HW_H)

    def test_wait_state_is_per_receiver_flow(self):
        self.assertIn("m_guard_elephant_deferred_since_ns", QP_H)
        self.assertIn("m_guard_elephant_deferred_since_ns = -1", HW_CC)
        selection = HW_CC[
            HW_CC.index("if (m_guardElephantAgingRtts > 0.0"):
            HW_CC.index("for (size_t index : elephants) receives_residual",)
        ]
        self.assertIn("m_base_rtt_sec", selection)
        self.assertIn("std::ceil(threshold)", selection)
        self.assertIn("std::swap(elephants[0], elephants[aged_rank])", selection)
        self.assertIn("m_guardElephantAgingRotations++", selection)

    def test_launcher_and_simulator_reject_policy_confounding(self):
        for text in (RUN, DRIVER):
            self.assertIn("guard_elephant_aging_rtts", text)
            self.assertIn("guard_adaptive_elephant_concurrency", text)
            self.assertIn("guard_receiver_concurrency", text)
        self.assertIn("elephant aging and adaptive concurrency are exclusive", RUN)
        self.assertIn("guard_receiver_concurrency != 1", DRIVER)
        self.assertIn("complete V18 canonical-vector bundle", DRIVER)

    def test_stats_close_activity_and_terminal_state(self):
        for token in ("guard_elephant_aging enabled", "rotations %lu",
                      "max_wait_ns %lu", "active_deferred %lu"):
            self.assertIn(token, DRIVER)

    def test_cpp_event_test_covers_threshold_rotation_and_cleanup(self):
        for phrase in (
                "GUARD V22 bounded elephant aging",
                "aging must not preempt before its threshold",
                "oldest deferred elephant must receive one residual quantum",
                "the observed wait must use the exact per-flow RTT threshold",
                "non-elephants must not retain a stale aging timestamp"):
            self.assertIn(phrase, CPP_TEST)


if __name__ == "__main__":
    unittest.main()
