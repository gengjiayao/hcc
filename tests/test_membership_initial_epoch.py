#!/usr/bin/env python3
"""Source-level regression gates for the bounded initial collection epoch."""

import pathlib
import unittest


REPO = pathlib.Path(__file__).resolve().parents[1]
HW_CC = (REPO / "src/point-to-point/model/rdma-hw.cc").read_text()
HW_H = (REPO / "src/point-to-point/model/rdma-hw.h").read_text()
SIM = (REPO / "scratch/network-load-balance.cc").read_text()
RUN = (REPO / "run.py").read_text()


class InitialCollectionEpochTest(unittest.TestCase):
    def test_epoch_state_is_explicit_and_resets_when_empty(self):
        self.assertIn("m_guardHasEmittedVectorThisEpoch", HW_H)
        self.assertIn("m_guardInitialCollectionPending", HW_H)
        remove_start = HW_CC.index("bool RdmaHw::HandleRccRemove")
        remove_end = HW_CC.index(
            "void RdmaHw::RequestGuardMembershipUpdate", remove_start)
        empty = HW_CC[remove_start:remove_end]
        self.assertIn("m_guardHasEmittedVectorThisEpoch = false", empty)
        self.assertIn("m_guardInitialCollectionPending = false", empty)
        self.assertIn("m_guardInitialCollectionTarget = Time(0)", empty)
        self.assertNotIn("m_guardGrantGeneration = 0", empty)

    def test_initial_timer_supports_fixed_and_bounded_sliding_modes(self):
        request = HW_CC[HW_CC.index("void RdmaHw::RequestGuardMembershipUpdate"):
                        HW_CC.index("bool RdmaHw::IsGuardMembershipDirty")]
        initial = request[request.index("if (!m_guardHasEmittedVectorThisEpoch)"):
                          request.index("bool release")]
        self.assertIn("m_guardInitialCollectionFullDeadline", initial)
        self.assertIn("GetGuardInitialCollectionQuietWindow", initial)
        self.assertIn("Time initial_window", initial)
        self.assertIn("m_guardMembershipCoalesceMaxWindows", initial)
        self.assertIn("m_guardInitialCollectionDeadline", initial)
        self.assertIn("m_guardInitialCollectionTarget", initial)
        self.assertIn("std::numeric_limits<int64_t>::max()", initial)
        self.assertIn("absolute deadline overflow", initial)
        self.assertIn("Simulator::Schedule", initial)
        self.assertIn("return;", initial)
        self.assertIn("if (!m_guardInitialCollectionFullDeadline)", initial)
        self.assertIn("remaining <= initial_window", initial)
        self.assertIn("? m_guardInitialCollectionDeadline", initial)
        self.assertIn(": now + initial_window", initial)
        self.assertIn("Simulator::Cancel", initial)
        self.assertIn("m_guardInitialCollectionReschedules++", initial)

    def test_fixed_mode_is_default_and_flush_assertion_is_mode_specific(self):
        attribute = HW_CC[HW_CC.index(
            '.AddAttribute("GuardInitialCollectionFullDeadline"'):
            HW_CC.index('.AddAttribute("GuardSrptQuantumPackets"')]
        self.assertIn("BooleanValue(true)", attribute)
        flush = HW_CC[HW_CC.index("void RdmaHw::FlushGuardMembershipUpdate"):
                      HW_CC.index("void RdmaHw::ResetGuardReliabilityRefresh")]
        self.assertIn("m_guardInitialCollectionFullDeadline", flush)
        self.assertIn("? m_guardInitialCollectionDeadline", flush)
        self.assertIn(": m_guardInitialCollectionTarget", flush)
        self.assertIn("did not flush at its scheduled target", flush)

    def test_sliding_mode_is_scoped_to_initial_collection(self):
        request = HW_CC[HW_CC.index("void RdmaHw::RequestGuardMembershipUpdate"):
                        HW_CC.index("bool RdmaHw::IsGuardMembershipDirty")]
        post_initial = request[request.index("bool release"):]
        self.assertNotIn("m_guardInitialCollectionFullDeadline", post_initial)
        self.assertNotIn("m_guardInitialCollectionQuietWindow", post_initial)
        self.assertNotIn("GetGuardInitialCollectionQuietWindow", post_initial)
        self.assertIn("m_guardReleaseThresholdDeferrals++", post_initial)
        self.assertIn("now + m_guardMembershipCoalesceWindow", post_initial)
        self.assertIn("std::min", post_initial)

    def test_cli_and_config_are_default_on_and_range_checked(self):
        self.assertIn("--guard_initial_collection_full_deadline", RUN)
        self.assertIn("--guard_initial_collection_quiet_ns", RUN)
        quiet_option = RUN[RUN.index("--guard_initial_collection_quiet_ns"):
                           RUN.index("--guard_initial_collection_full_deadline")]
        self.assertIn("default=0", quiet_option)
        self.assertIn("[0, 65536]", quiet_option)
        option = RUN[RUN.index("--guard_initial_collection_full_deadline"):
                     RUN.index("--guard_grant_reliability_rtts")]
        self.assertIn("choices=(0, 1)", option)
        self.assertIn("default=1", option)
        self.assertIn("GUARD_INITIAL_COLLECTION_FULL_DEADLINE ", RUN)
        self.assertIn("GUARD_INITIAL_COLLECTION_QUIET_NS ", RUN)
        self.assertIn("guard_initial_collection_quiet_ns=", RUN)
        self.assertIn("guard_initial_collection_full_deadline=", RUN)
        self.assertIn("GUARD_INITIAL_COLLECTION_QUIET_NS must be in [0, 65536]", SIM)
        self.assertIn("GUARD_INITIAL_COLLECTION_FULL_DEADLINE must be 0 or 1", SIM)
        self.assertIn('"GuardInitialCollectionQuietWindow"', SIM)
        self.assertIn('"GuardInitialCollectionFullDeadline"', SIM)

    def test_stats_expose_mode_reschedules_and_disjoint_flush_reasons(self):
        for token in (
                "initial_collection_full_deadline",
                "initial_collection_quiet_ns",
                "initial_collection_starts",
                "initial_collection_flushes",
                "initial_collection_deferred_changes",
                "initial_collection_reschedules",
                "initial_collection_cancellations",
                "initial_collection_quiet_flushes",
                "initial_collection_hard_flushes",
                "initial_collection_wait_ns",
                "initial_collection_max_wait_ns"):
            self.assertIn(token, SIM)
        flush = HW_CC[HW_CC.index("void RdmaHw::FlushGuardMembershipUpdate"):
                      HW_CC.index("void RdmaHw::ResetGuardReliabilityRefresh")]
        self.assertIn("expected == m_guardInitialCollectionDeadline", flush)
        self.assertIn("m_guardInitialCollectionHardFlushes++", flush)
        self.assertIn("m_guardInitialCollectionQuietFlushes++", flush)
        self.assertIn("flush-reason counters diverged", flush)


if __name__ == "__main__":
    unittest.main()
