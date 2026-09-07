#!/usr/bin/env python3
"""Source/state regressions for the default-off GUARD V13 watchdog."""

import pathlib
import unittest


REPO = pathlib.Path(__file__).resolve().parents[1]
HW_CC = (REPO / "src/point-to-point/model/rdma-hw.cc").read_text()
SIM = (REPO / "scratch/network-load-balance.cc").read_text()
RUN = (REPO / "run.py").read_text()


def function(name: str, next_name: str) -> str:
    start = HW_CC.index(name)
    return HW_CC[start:HW_CC.index(next_name, start)]


class GuardTransitionPrefixV13Test(unittest.TestCase):
    @staticmethod
    def aggregate_record_provenance(records_per_hardware):
        records = sum(records_per_hardware)
        contributing_hardware = sum(value > 0 for value in records_per_hardware)
        source_non_reconstructable = any(
            value > 1 for value in records_per_hardware)
        non_reconstructable = (
            source_non_reconstructable or records > 1 or
            contributing_hardware > 1)
        return {
            "records": records,
            "non_reconstructable": non_reconstructable,
            "inconsistent": non_reconstructable,
        }

    def test_default_off_and_requires_v12(self):
        attribute = HW_CC[
            HW_CC.index('.AddAttribute("GuardTransitionPrefixWireWatchdog"'):
            HW_CC.index('.AddAttribute("GuardSrptQuantumPackets"')]
        self.assertIn("BooleanValue(false)", attribute)
        option = RUN[
            RUN.index("--guard_transition_prefix_wire_watchdog"):
            RUN.index("--guard_grant_reliability_rtts")]
        self.assertIn("default=0", option)
        self.assertIn("not args.guard_transition_prefix_barrier", RUN)
        self.assertIn("!guard_transition_prefix_barrier", SIM)

    def test_disabled_v13_preserves_v12_config_and_stats_shape(self):
        self.assertIn(
            '{guard_transition_prefix_wire_watchdog_config}GUARD_GRANT_RELIABILITY_RTTS',
            RUN)
        self.assertIn(
            'if args.guard_transition_prefix_wire_watchdog else ""', RUN)
        stats = SIM[SIM.index('if (guard_transition_prefix_wire_watchdog) {'):]
        self.assertIn('"guard_transition_prefix_watchdog enabled %u "', stats)
        prefix_stats = SIM[
            SIM.index('if (guard_transition_prefix_barrier) {',
                      SIM.index('fprintf(guard_stats_output')):
            SIM.index('if (guard_transition_prefix_wire_watchdog) {',
                      SIM.index('fprintf(guard_stats_output'))]
        self.assertNotIn("watchdog", prefix_stats)

    def test_watchdog_changes_only_deadline_budget(self):
        check = function("void RdmaHw::CheckGuardTransitionPrefixBarrier",
                         "void RdmaHw::HandleGuardTransitionPrefixDeadline")
        deadline = function("void RdmaHw::HandleGuardTransitionPrefixDeadline",
                            "void RdmaHw::SendNextGuardTransitionFallbackBatch")
        fallback = function("void RdmaHw::SendNextGuardTransitionFallbackBatch",
                            "void RdmaHw::SendGuardFastpathRequiredGeneration")
        self.assertNotIn("WireWatchdog", check)
        self.assertNotIn("WireWatchdog", deadline)
        self.assertNotIn("WireWatchdog", fallback)
        self.assertIn("target, flow->ReceiverNextExpectedSeq", check)
        self.assertIn("Simulator::ScheduleNow", deadline)

    def test_budget_ignores_observed_progress_and_uses_wire_bytes(self):
        start = function("void RdmaHw::StartGuardTransitionPrefixBarrier",
                         "void RdmaHw::CheckGuardTransitionPrefixBarrier")
        branch = start[
            start.index("if (m_guardTransitionPrefixWireWatchdogEnabled)"):
            start.index("} else {", start.index(
                "if (m_guardTransitionPrefixWireWatchdogEnabled)"))]
        self.assertNotIn("ReceiverNextExpectedSeq", branch)
        self.assertNotIn("remaining_bytes", branch)
        self.assertIn("flow->m_guard_first_grant_gate_bytes", branch)
        self.assertIn("flow->m_guard_last_acked_upper_bound_bps", branch)
        self.assertIn("CustomHeader::GetStaticWholeHeaderSize()", branch)
        helper = function("bool RdmaHw::ComputeGuardTransitionPrefixWireBudget",
                          "void RdmaHw::StartGuardTransitionPrefixBarrier")
        self.assertIn("target / mtu", helper)
        self.assertIn("target % mtu", helper)
        self.assertIn("payload_sent_upper", helper)
        self.assertIn("wire_bytes", helper)
        self.assertNotIn("interframe", helper.lower())

    def test_acknowledged_occupancy_is_sampled_after_prepare_closure(self):
        ack = function("int RdmaHw::ReceiveGuardGrantAck",
                       "int RdmaHw::ReceiveGuardCapReport")
        self.assertLess(
            ack.index("m_guard_grant_upper_bound_bps ="),
            ack.index("FinishGuardFastpathGeneration()"))
        activate = function("void RdmaHw::StartGuardFastpathActivate",
                            "bool RdmaHw::ComputeGuardTransitionPrefixWireBudget")
        self.assertIn("StartGuardTransitionPrefixBarrier()", activate)
        start = function("void RdmaHw::StartGuardTransitionPrefixBarrier",
                         "void RdmaHw::CheckGuardTransitionPrefixBarrier")
        self.assertIn("m_guardPendingGrantAcks != 0", start)

    def test_checked_math_and_absolute_deadline(self):
        helper = function("bool RdmaHw::ComputeGuardTransitionPrefixWireBudget",
                          "void RdmaHw::StartGuardTransitionPrefixBarrier")
        for token in (
                "__uint128_t", "wide_max", "u64_max",
                "occupancy_bps >= receiver_capacity_bps",
                "serialization_numerator % residual_bps",
                "std::numeric_limits<int64_t>::max()"):
            self.assertIn(token, helper)
        start = function("void RdmaHw::StartGuardTransitionPrefixBarrier",
                         "void RdmaHw::CheckGuardTransitionPrefixBarrier")
        self.assertIn("now_ns > std::numeric_limits<int64_t>::max()", start)

    def test_immediate_ready_remove_same_tick_and_reset_are_unchanged(self):
        start = function("void RdmaHw::StartGuardTransitionPrefixBarrier",
                         "void RdmaHw::CheckGuardTransitionPrefixBarrier")
        self.assertLess(start.index("CheckGuardTransitionPrefixBarrier()"),
                        start.index("m_guardTransitionPrefixDeadlineEvent ="))
        remove = function("bool RdmaHw::HandleRccRemove",
                          "Time RdmaHw::GetGuardInitialCollectionQuietWindow")
        self.assertIn("CheckGuardTransitionPrefixBarrier()", remove)
        deadline = function("void RdmaHw::HandleGuardTransitionPrefixDeadline",
                            "void RdmaHw::SendNextGuardTransitionFallbackBatch")
        self.assertIn("Simulator::ScheduleNow", deadline)
        reset = function("void RdmaHw::ResetGuardFastpathEpoch",
                         "void RdmaHw::ConsumeGuardFastpathMembershipThrough")
        self.assertIn("m_guardTransitionPrefixWatchdogBudgetActive = false", reset)
        finish = function("void RdmaHw::FinishGuardFastpathTransaction",
                          "void RdmaHw::AppendGuardFastpathTraceFields")
        self.assertIn("m_guardTransitionPrefixWatchdogBudgetActive = false", finish)

    def test_stats_cover_budget_and_terminal_state(self):
        for label in (
                "records", "non_reconstructable", "inconsistent",
                "rounded_payload_budget_bytes", "packet_count", "header_per_packet",
                "wire_bytes", "incumbent_count", "occupancy_bps",
                "capacity_bps", "residual_bps", "serialization_ns",
                "max_rtt_ns", "delay_ns", "start_ns", "deadline_ns",
                "terminal_budget"):
            self.assertIn(label, SIM)

    def test_two_transitions_are_explicitly_non_reconstructable(self):
        aggregate = self.aggregate_record_provenance([2])
        self.assertEqual(aggregate["records"], 2)
        self.assertTrue(aggregate["non_reconstructable"])
        self.assertTrue(aggregate["inconsistent"])
        start = function("void RdmaHw::StartGuardTransitionPrefixBarrier",
                         "void RdmaHw::CheckGuardTransitionPrefixBarrier")
        self.assertIn("m_guardTransitionPrefixWatchdogBudgetRecords++", start)
        self.assertIn("m_guardTransitionPrefixWatchdogBudgetRecords > 1", start)

    def test_multiple_receivers_cannot_mix_sum_and_max_as_one_budget(self):
        aggregate = self.aggregate_record_provenance([1, 1])
        self.assertEqual(aggregate["records"], 2)
        self.assertTrue(aggregate["non_reconstructable"])
        self.assertTrue(aggregate["inconsistent"])
        self.assertIn(
            "IsGuardTransitionPrefixWatchdogAggregateInconsistent", SIM)

    def test_one_receiver_record_remains_reconstructable(self):
        aggregate = self.aggregate_record_provenance([0, 1, 0])
        self.assertEqual(aggregate, {
            "records": 1,
            "non_reconstructable": False,
            "inconsistent": False,
        })


if __name__ == "__main__":
    unittest.main()
