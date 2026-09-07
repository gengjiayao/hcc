#!/usr/bin/env python3
"""Safety-contract regressions for the explicit V15 timeout policy."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
HW_H = (ROOT / "src/point-to-point/model/rdma-hw.h").read_text()
HW_CC = (ROOT / "src/point-to-point/model/rdma-hw.cc").read_text()
DRIVER = (ROOT / "scratch/network-load-balance.cc").read_text()
RUN = (ROOT / "run.py").read_text()


def function(name: str, next_name: str) -> str:
    start = HW_CC.index(name)
    return HW_CC[start:HW_CC.index(next_name, start)]


class GuardAckClockFallbackV15Test(unittest.TestCase):
    def test_policy_is_default_off_and_explicit_on_both_drivers(self):
        attribute = HW_CC[
            HW_CC.index('.AddAttribute("GuardTransitionPrefixAckClockFallback"'):
            HW_CC.index('.AddAttribute("GuardMixedPgVectorFastpath"')]
        self.assertIn("BooleanValue(false)", attribute)
        self.assertIn("--guard_transition_prefix_ack_clock_fallback", RUN)
        self.assertIn("default=0", RUN[RUN.index(
            "--guard_transition_prefix_ack_clock_fallback"):])
        self.assertIn("GUARD_TRANSITION_PREFIX_ACK_CLOCK_FALLBACK", DRIVER)
        self.assertIn('"GuardTransitionPrefixAckClockFallback"', DRIVER)

    def test_policy_requires_watchdog_full_guard_and_no_fail_closed_mode(self):
        for token in (
                "not args.guard_transition_prefix_wire_watchdog",
                "args.guard_transition_prefix_fail_closed or args.cc != \"guard\"",
                "!guard_transition_prefix_wire_watchdog",
                "guard_transition_prefix_fail_closed || cc_mode != 11"):
            self.assertIn(token, RUN + DRIVER)
        self.assertIn("either the V14 fail-closed policy or the V15", RUN)
        self.assertIn("V14 fail-closed or V15 ACK-clocked", DRIVER)

    def test_fail_closed_remains_ordered_before_v15_fallback(self):
        check = function("void RdmaHw::CheckGuardTransitionPrefixBarrier",
                         "void RdmaHw::HandleGuardTransitionPrefixDeadline")
        abort_at = check.index(
            "GUARD V14 fail-closed transition prefix deadline expired")
        explicit_at = check.index("!m_guardTransitionPrefixAckClockFallback")
        fallback_at = check.index("m_guardTransitionFallbackActive = true")
        self.assertLess(abort_at, explicit_at)
        self.assertLess(explicit_at, fallback_at)
        self.assertIn('"timeout_fallback"', check)

    def test_timeout_path_reuses_required_ack_clocked_batches(self):
        check = function("void RdmaHw::CheckGuardTransitionPrefixBarrier",
                         "void RdmaHw::HandleGuardTransitionPrefixDeadline")
        send = function("void RdmaHw::SendNextGuardTransitionFallbackBatch",
                        "void RdmaHw::SendGuardFastpathRequiredGeneration")
        self.assertIn("SendNextGuardTransitionFallbackBatch()", check)
        self.assertIn("recipients.size() < m_guardSmallSetFastpathLimit", send)
        self.assertIn("SendGuardFastpathRequiredGenerationOrdered", send)
        self.assertIn("m_guardPendingGrantAcks != 0", send)
        self.assertIn("m_guardTransitionFallbackOrderViolations", send)

    def test_stats_disclose_policy_and_terminal_fallback_state(self):
        self.assertIn(
            "guard_transition_prefix_ack_clock_fallback enabled 1",
            DRIVER)
        for token in ("fallback_batches", "fallback_closed_batches",
                      "order_violations", "terminal_fallback",
                      "terminal_current_batch"):
            self.assertIn(token, DRIVER)


if __name__ == "__main__":
    unittest.main()
