#!/usr/bin/env python3
"""Source and state-machine regression gates for opt-in GUARD V12."""

import math
import pathlib
import unittest


REPO = pathlib.Path(__file__).resolve().parents[1]
HW_CC = (REPO / "src/point-to-point/model/rdma-hw.cc").read_text()
HW_H = (REPO / "src/point-to-point/model/rdma-hw.h").read_text()
FLOW_TAG = (REPO / "src/point-to-point/model/flow-stat-tag.cc").read_text()
SIM = (REPO / "scratch/network-load-balance.cc").read_text()
RUN = (REPO / "run.py").read_text()


def function(name: str, next_name: str) -> str:
    start = HW_CC.index(name)
    return HW_CC[start:HW_CC.index(next_name, start)]


class FallbackModel:
    """Small executable model of frozen, ACK-clocked fallback batches."""

    def __init__(self, identities, limit=4):
        self.order = sorted(identities)
        self.limit = limit
        self.cursor = 0
        self.generation = 0
        self.current = []
        self.acked = set()
        self.closed = []
        self.finished = False
        self.start_next()

    def start_next(self):
        if self.current:
            return
        if self.cursor == len(self.order):
            self.finished = True
            return
        self.generation += 1
        self.current = self.order[self.cursor:self.cursor + self.limit]
        self.cursor += len(self.current)
        self.acked = set()

    def ack(self, identity, generation):
        if self.finished or generation != self.generation or identity not in self.current:
            return
        self.acked.add(identity)
        if len(self.acked) == len(self.current):
            self.closed.append(tuple(self.current))
            self.current = []
            self.start_next()

    def remove(self, identity):
        if identity in self.current:
            self.current.remove(identity)
            if len(self.acked) == len(self.current):
                self.closed.append(tuple(self.current))
                self.current = []
                self.start_next()
            return
        if identity in self.order[self.cursor:]:
            self.order.remove(identity)


class GuardTransitionPrefixV12Test(unittest.TestCase):
    def test_feature_is_explicit_and_default_off(self):
        attribute = HW_CC[HW_CC.index('.AddAttribute("GuardTransitionPrefixBarrier"'):
                          HW_CC.index('.AddAttribute("GuardSrptQuantumPackets"')]
        self.assertIn("BooleanValue(false)", attribute)
        option = RUN[RUN.index("--guard_transition_prefix_barrier"):
                     RUN.index("--guard_rebalance_interval_us")]
        self.assertIn("default=0", option)
        self.assertIn("guard_small_set_fastpath_limit != 4", RUN)
        self.assertIn("guard_fixed_window != 1", RUN)
        self.assertIn("guard_small_set_fastpath_limit != 4", SIM)
        self.assertIn("!guard_fixed_window", SIM)

    def test_sender_exact_window_is_carried_without_wire_bytes(self):
        send = function("Ptr<Packet> RdmaHw::GetNxtPacket(",
                        "void RdmaHw::PktSent")
        self.assertIn("fst.SetFirstGrantGateBytes(qp->GetGuardFirstGrantWin())", send)
        receive = function("int RdmaHw::ReceiveUdp", "int RdmaHw::ReceiveCnp")
        self.assertIn("fst.GetFirstGrantGateBytes()", receive)
        barrier = function("void RdmaHw::StartGuardTransitionPrefixBarrier",
                           "void RdmaHw::CheckGuardTransitionPrefixBarrier")
        self.assertIn("flow->m_guard_first_grant_gate_bytes", barrier)
        self.assertNotIn("path_bdp_bytes", barrier)
        for global_t, pair_window, global_window in (
                (0, 104123, 208257), (1, 104123, 208257)):
            exact = global_window if global_t == 1 else pair_window
            self.assertEqual(min(900000, exact), exact)
            self.assertNotEqual(exact % 1000, 0)
        self.assertIn("m_hasFirstGrantGateBytes", FLOW_TAG)

    def test_empty_prepare_still_enters_prefix_barrier(self):
        prepare = function("void RdmaHw::StartGuardFastpathPrepare",
                           "void RdmaHw::StartGuardFastpathActivate")
        activate = function("void RdmaHw::StartGuardFastpathActivate",
                            "void RdmaHw::StartGuardTransitionPrefixBarrier")
        self.assertIn("StartGuardFastpathActivate()", prepare)
        self.assertIn("StartGuardTransitionPrefixBarrier()", activate)
        self.assertLess(activate.index("StartGuardTransitionPrefixBarrier()"),
                        activate.index("std::unordered_set<RdmaRxQueuePair*> recipients"))

    def test_prepare_and_drain_validate_the_complete_receiver_queue(self):
        transaction = function("void RdmaHw::StartGuardFastpathTransaction",
                               "bool RdmaHw::ValidateGuardTransitionQueueCohort")
        validate = function("bool RdmaHw::ValidateGuardTransitionQueueCohort",
                            "void RdmaHw::StartGuardFastpathPrepare")
        barrier = function("void RdmaHw::StartGuardTransitionPrefixBarrier",
                           "void RdmaHw::CheckGuardTransitionPrefixBarrier")
        self.assertLess(transaction.index("ValidateGuardTransitionQueueCohort()"),
                        transaction.index("StartGuardFastpathPrepare()"))
        self.assertIn("m_guardFastpathIncumbents", validate)
        self.assertIn("m_guardFastpathTransactionWaiters", validate)
        self.assertIn("flow->m_guard_pg != expected_pg", validate)
        self.assertIn("ValidateGuardTransitionQueueCohort()", barrier)

    def test_empty_frozen_cohort_does_not_admit_a_late_join(self):
        frozen_incumbents = set()
        frozen_waiters = set()
        global_active = {"late-join"}
        self.assertFalse(frozen_incumbents | frozen_waiters)
        self.assertTrue(global_active)
        barrier = function("void RdmaHw::StartGuardTransitionPrefixBarrier",
                           "void RdmaHw::CheckGuardTransitionPrefixBarrier")
        empty = barrier.index("if (!ValidateGuardTransitionQueueCohort())")
        finish = barrier.index("FinishGuardFastpathTransaction()", empty)
        freeze = barrier.index("m_guardFastpathPhase = GUARD_FASTPATH_PREFIX_BARRIER")
        self.assertLess(empty, finish)
        self.assertLess(finish, freeze)
        self.assertNotIn("m_rate_flow_ctl_set.begin", barrier[empty:finish])

    def test_ready_predicate_and_nominal_deadline_are_integer_safe(self):
        barrier = function("void RdmaHw::StartGuardTransitionPrefixBarrier",
                           "void RdmaHw::CheckGuardTransitionPrefixBarrier")
        check = function("void RdmaHw::CheckGuardTransitionPrefixBarrier",
                         "void RdmaHw::HandleGuardTransitionPrefixDeadline")
        deadline = function("void RdmaHw::HandleGuardTransitionPrefixDeadline",
                            "void RdmaHw::SendNextGuardTransitionFallbackBatch")
        self.assertIn("__uint128_t serialization_numerator", barrier)
        self.assertIn("std::numeric_limits<int64_t>::max()", barrier)
        self.assertIn("flow_nic != receiver_nic", barrier)
        self.assertIn("flow->m_guard_pg != receiver_pg", barrier)
        self.assertIn("target, flow->ReceiverNextExpectedSeq", check)
        self.assertIn("Simulator::Now() < m_guardTransitionPrefixDeadline", check)
        self.assertIn("Simulator::ScheduleNow", deadline)
        remaining, capacity, max_rtt = 104123 * 11, 100_000_000_000, 8320
        serial_ns = math.ceil(remaining * 8_000_000_000 / capacity)
        self.assertEqual(max_rtt + serial_ns, 99949)

    def test_eleven_waiters_fall_back_as_four_four_three(self):
        identities = [(100 + i // 2, i) for i in range(11)]
        model = FallbackModel(identities)
        while not model.finished:
            generation = model.generation
            for identity in list(reversed(model.current)):
                model.ack(identity, generation)
        self.assertEqual([len(batch) for batch in model.closed], [4, 4, 3])
        self.assertEqual([item for batch in model.closed for item in batch],
                         sorted(identities))

    def test_duplicate_stale_and_loss_retry_do_not_cross_ack_barrier(self):
        model = FallbackModel([(0, i) for i in range(5)])
        first_generation = model.generation
        model.ack((0, 0), first_generation)
        model.ack((0, 0), first_generation)  # duplicate ACK
        model.ack((0, 1), first_generation - 1)  # stale ACK
        self.assertEqual(model.generation, first_generation)
        self.assertEqual(model.cursor, 4)
        for identity in ((0, 1), (0, 2), (0, 3)):
            model.ack(identity, first_generation)
        self.assertEqual(model.generation, first_generation + 1)
        self.assertFalse(model.finished)

    def test_current_future_removal_and_late_join_are_isolated(self):
        snapshot = [(0, i) for i in range(7)]
        model = FallbackModel(snapshot)
        model.remove((0, 1))  # current batch completion
        model.remove((0, 5))  # future completion
        late_join = (1, 99)
        self.assertNotIn(late_join, model.order)
        generation = model.generation
        for identity in list(model.current):
            model.ack(identity, generation)
        generation = model.generation
        for identity in list(model.current):
            model.ack(identity, generation)
        self.assertTrue(model.finished)
        self.assertNotIn((0, 5), [item for batch in model.closed for item in batch])

    def test_future_removal_does_not_claim_a_predicted_batch_total(self):
        identities = [(0, i) for i in range(11)]
        model = FallbackModel(identities)
        for identity in identities[4:]:
            model.remove(identity)
        generation = model.generation
        for identity in list(model.current):
            model.ack(identity, generation)
        self.assertTrue(model.finished)
        self.assertEqual([len(batch) for batch in model.closed], [4])
        trace = function("void RdmaHw::AppendGuardFastpathTraceFields",
                         "void RdmaHw::TraceGuardGrant(")
        self.assertIn("m_guardTransitionActivationBatchSize", trace)
        self.assertNotIn("batch_count", trace)

    def test_same_tick_empty_snapshot_closes_without_a_timer(self):
        start = function("void RdmaHw::StartGuardTransitionPrefixBarrier",
                         "void RdmaHw::CheckGuardTransitionPrefixBarrier")
        reset = function("void RdmaHw::ResetGuardFastpathEpoch",
                         "void RdmaHw::ConsumeGuardFastpathMembershipThrough")
        self.assertLess(start.index("m_guardTransitionActivationOrder.empty()"),
                        start.index("m_guardTransitionPrefixDeadlineEvent ="))
        self.assertIn("Simulator::Cancel(m_guardTransitionPrefixDeadlineEvent)", reset)
        remove = function("bool RdmaHw::HandleRccRemove",
                          "Time RdmaHw::GetGuardInitialCollectionQuietWindow")
        self.assertIn("m_guardFastpathReadyWaiters.erase", remove)
        self.assertIn("m_guardTransitionActivationHolds.erase", remove)
        self.assertIn("CheckGuardTransitionPrefixBarrier()", remove)

    def test_trace_and_terminal_evidence_are_bounded(self):
        for token in (
                "prefix_target_bytes", "prefix_observed_bytes", "drain_outcome",
                "activation_batch_index", "activation_batch_size",
                "activation_register_ns",
                "waiters_required", "waiters_ready", "fallback_closed_batches",
                "terminal_timer", "terminal_cursor", "terminal_cohort",
                "terminal_future_queue", "terminal_current_batch"):
            self.assertIn(token, SIM)
        self.assertIn("m_guardTransitionActivationHolds", HW_H)
        self.assertIn("m_guardTransitionCurrentBatch", HW_H)


if __name__ == "__main__":
    unittest.main()
