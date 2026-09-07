#!/usr/bin/env python3
"""Source and arithmetic regression gates for the opt-in V11 protocol."""

import pathlib
import unittest


REPO = pathlib.Path(__file__).resolve().parents[1]
HW_CC = (REPO / "src/point-to-point/model/rdma-hw.cc").read_text()
HW_H = (REPO / "src/point-to-point/model/rdma-hw.h").read_text()
QBB_H = (REPO / "src/point-to-point/model/qbb-header.h").read_text()
SIM = (REPO / "scratch/network-load-balance.cc").read_text()
RUN = (REPO / "run.py").read_text()


def function(name: str, next_name: str) -> str:
    start = HW_CC.index(name)
    end = HW_CC.index(next_name, start)
    return HW_CC[start:end]


class RevisionModel:
    """Minimal model of the revision/snapshot rule used by the C++ core."""

    def __init__(self) -> None:
        self.revision = 0
        self.consumed = 0
        self.ready = 0

    def change(self) -> None:
        self.revision += 1

    def flush(self) -> None:
        self.ready = self.revision

    def consume_ready(self) -> None:
        self.consumed = self.ready

    @property
    def pending(self) -> int:
        return self.revision - self.consumed


class GuardSmallSetFastpathTest(unittest.TestCase):
    def test_feature_is_default_off_and_v11_configuration_is_isolated(self):
        attribute = HW_CC[HW_CC.index('.AddAttribute("GuardSmallSetFastpathLimit"'):
                          HW_CC.index('.AddAttribute("GuardSrptQuantumPackets"')]
        self.assertIn("UintegerValue(0)", attribute)
        option = RUN[RUN.index("--guard_small_set_fastpath_limit"):
                     RUN.index("--guard_rebalance_interval_us")]
        self.assertIn("default=0", option)
        self.assertIn("not in (0, 4)", RUN)
        for token in ("guard_membership_coalesce_ns != 12480",
                      "guard_initial_collection_quiet_ns != 16640",
                      "guard_membership_coalesce_max_windows != 5",
                      "guard_initial_collection_full_deadline != 0"):
            self.assertIn(token, SIM)

    def test_required_transactions_use_nonzero_monotonic_generations(self):
        required = function("void RdmaHw::SendGuardFastpathRequiredGeneration",
                            "void RdmaHw::SendGuardFastpathOptionalRelease")
        self.assertIn("NextGuardGrantGeneration()", required)
        self.assertIn("m_guardPendingGrantAcks = recipients.size()", required)
        self.assertIn("ResetGuardReliabilityRefresh()", required)
        reset = function("void RdmaHw::ResetGuardFastpathEpoch",
                         "void RdmaHw::ConsumeGuardFastpathMembershipThrough")
        self.assertNotIn("m_guardGrantGeneration = 0", reset)

    def test_prepare_and_activate_are_ack_serialized(self):
        prepare = function("void RdmaHw::StartGuardFastpathPrepare",
                           "void RdmaHw::StartGuardFastpathActivate")
        activate = function("void RdmaHw::StartGuardFastpathActivate",
                            "void RdmaHw::SendGuardFastpathRequiredGeneration")
        finish = function("void RdmaHw::FinishGuardFastpathGeneration",
                          "void RdmaHw::FinishGuardFastpathTransaction")
        self.assertIn("m_guardFastpathIncumbents", prepare)
        self.assertIn("m_guard_grant_upper_bound_bps > target_bps", prepare)
        self.assertNotIn("m_guardFastpathTransactionWaiters", prepare)
        self.assertIn("m_guardFastpathTransactionWaiters", activate)
        self.assertIn("StartGuardFastpathActivate()", finish)
        self.assertIn("m_guardPendingGrantAcks != 0", finish)

    def test_busy_flush_late_waiter_remains_dirty(self):
        model = RevisionModel()
        for _ in range(5):
            model.change()
        model.flush()
        model.change()  # registration after the busy flush snapshot
        model.consume_ready()
        self.assertEqual(model.pending, 1)

        flush = function("void RdmaHw::FlushGuardFastpathHighCollection",
                         "void RdmaHw::StartGuardFastpathTransaction")
        start = function("void RdmaHw::StartGuardFastpathTransaction",
                         "void RdmaHw::StartGuardFastpathPrepare")
        finish = function("void RdmaHw::FinishGuardFastpathTransaction",
                          "void RdmaHw::RequestGuardMembershipUpdate")
        self.assertIn("m_guardFastpathReadyMembershipRevision =", flush)
        self.assertIn("snapshot_revision = m_guardFastpathReadyMembershipRevision", start)
        self.assertIn("ConsumeGuardFastpathMembershipThrough(snapshot_revision)", start)
        self.assertNotIn("m_guardPendingMembershipChanges = 0", start)
        self.assertIn("waiter lost its dirty membership revision", finish)

    def test_initial_flush_and_transition_commit_are_distinct(self):
        schedule = function("void RdmaHw::ScheduleGuardFastpathHighCollection",
                            "void RdmaHw::FlushGuardFastpathHighCollection")
        flush = function("void RdmaHw::FlushGuardFastpathHighCollection",
                         "void RdmaHw::StartGuardFastpathTransaction")
        finish = function("void RdmaHw::FinishGuardFastpathTransaction",
                          "void RdmaHw::RequestGuardMembershipUpdate")
        self.assertIn("!m_guardFastpathHighInitialCollectionFlushed", schedule)
        self.assertNotIn("!m_guardFastpathHighInitialCommitted", schedule)
        self.assertIn("m_guardFastpathHighInitialCollectionFlushed = true", flush)
        self.assertIn("m_guardFastpathHighInitialCommitted = true", finish)

    def test_transition_barrier_times_are_latched(self):
        prepare = function("void RdmaHw::StartGuardFastpathPrepare",
                           "void RdmaHw::StartGuardFastpathActivate")
        flush = function("void RdmaHw::FlushGuardFastpathHighCollection",
                         "void RdmaHw::StartGuardFastpathTransaction")
        finish = function("void RdmaHw::FinishGuardFastpathTransaction",
                          "void RdmaHw::RequestGuardMembershipUpdate")
        reset = function("void RdmaHw::ResetGuardFastpathEpoch",
                         "void RdmaHw::ConsumeGuardFastpathMembershipThrough")
        self.assertIn("m_guardFastpathEpochCollectionFlushNs", flush)
        self.assertIn("m_guardFastpathPrefixCloseNs == 0", prepare)
        self.assertIn("m_guardFastpathCollectionFlushNs == 0", prepare)
        self.assertIn("m_guardFastpathTransitionPrepareStartNs == 0", prepare)
        self.assertIn("m_guardFastpathLastTransactionCloseNs", prepare)
        self.assertIn("m_guardFastpathLastTransactionCloseNs =", finish)
        self.assertIn("m_guardFastpathEpochPrefixCloseNs = 0", reset)
        self.assertNotIn("m_guardFastpathPrefixCloseNs = 0", reset)

    def test_high_transition_cohort_is_the_frozen_transaction_snapshot(self):
        start = function("void RdmaHw::StartGuardFastpathTransaction",
                         "void RdmaHw::StartGuardFastpathPrepare")
        target = start.index("m_guardFastpathTransactionTargetN =")
        registered = start.index("m_guardFastpathHighTransitionRegisteredN =")
        self.assertGreater(registered, target)
        self.assertIn("m_guardFastpathTransactionWaiters.size()", start[registered:])

    def test_phase_tags_keep_the_compact_header_size_and_count_retries(self):
        self.assertIn("bits 1--3 are the optional V11 phase tag", QBB_H)
        self.assertIn("GetGuardFastpathWirePhaseTag", HW_CC)
        refresh = function("void RdmaHw::RefreshGuardGrantsForReliability",
                           "void RdmaHw::RedistributeGuardRates")
        self.assertIn("SendRateControlPacket", refresh)
        send = function("void RdmaHw::SendRateControlPacket",
                        "void RdmaHw::SendGuardGrantAck")
        self.assertIn("CountGuardFastpathGrantFrame(phase_tag)", send)
        receive = function("int RdmaHw::ReceiveRate", "int RdmaHw::ReceiveGuardGrantAck")
        self.assertIn("CountGuardFastpathAckFrame(phase_tag)", receive)

    def test_frame_bounds_and_terminal_schema_are_frozen(self):
        self.assertEqual({n: n * n + 2 * n - 1 for n in (2, 4)},
                         {2: 7, 4: 23})
        self.assertEqual({n: 3 * n + 19 for n in (8, 15)},
                         {8: 43, 15: 64})
        for token in ("transaction_id", "grant_phase", "membership_target_n",
                      "subject_role", "wire_reconciled", "terminal_phase",
                      "terminal_pending_membership", "early_unlocks"):
            self.assertIn(token, SIM)


if __name__ == "__main__":
    unittest.main()
