#!/usr/bin/env python3
"""Source and contract regressions for the default-off GUARD V14 audit."""

import importlib.util
import pathlib
import unittest


REPO = pathlib.Path(__file__).resolve().parents[1]
HW_CC = (REPO / "src/point-to-point/model/rdma-hw.cc").read_text()
HW_H = (REPO / "src/point-to-point/model/rdma-hw.h").read_text()
SIM = (REPO / "scratch/network-load-balance.cc").read_text()
RUN = (REPO / "run.py").read_text()


def function(name: str, next_name: str) -> str:
    start = HW_CC.index(name)
    return HW_CC[start:HW_CC.index(next_name, start)]


def load_run_module():
    spec = importlib.util.spec_from_file_location("guard_run_v14", REPO / "run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GuardTransitionAuditV14Test(unittest.TestCase):
    def test_default_off_preserves_legacy_output_contract(self):
        attribute = HW_CC[
            HW_CC.index('.AddAttribute("GuardTransitionPrefixFailClosed"'):
            HW_CC.index('.AddAttribute("GuardSrptQuantumPackets"')]
        self.assertIn("BooleanValue(false)", attribute)
        self.assertIn("--guard_transition_audit", RUN)
        self.assertIn("--guard_transition_prefix_fail_closed", RUN)
        self.assertIn("default=0", RUN[RUN.index("--guard_transition_audit"):])
        self.assertIn(
            "{guard_transition_prefix_wire_watchdog_config}GUARD_GRANT_RELIABILITY_RTTS",
            RUN)
        self.assertIn(
            'if args.guard_transition_audit else ""', RUN)
        self.assertNotIn("guard_transition_audit enabled 0", SIM)

    def test_record_is_per_transition_and_source_complete(self):
        for field in (
                "receiverNode", "receiverNic", "epoch", "transaction",
                "priorityGroupSetHash", "targetVectorHash",
                "receiverCapacityBps", "activeUpperBoundBps",
                "drainingUpperBoundBps", "activeDrainingUpperBoundBps",
                "drainingWireUpperBoundBytes", "prefixTargetBytes",
                "prefixObservedBytes", "deadlinePolicy", "deadlineOutcome",
                "terminalClosure"):
            self.assertIn(field, HW_H)
        begin = function("void RdmaHw::BeginGuardTransitionAudit",
                         "void RdmaHw::RetireGuardTransitionAuditPrefix")
        self.assertIn("m_guardFastpathTransaction", begin)
        self.assertIn("m_guardTransitionAuditEpoch", begin)
        self.assertIn("GuardAuditHashWords", begin)
        self.assertIn("watchdog_budget->occupancyBps", begin)
        self.assertIn("watchdog_budget->wireBytes", begin)

    def test_two_transitions_and_two_hardware_share_only_bounded_sink(self):
        self.assertIn("One sink is shared by every receiver NIC", HW_H)
        self.assertIn("GuardTransitionAuditRecord m_guardTransitionAuditRecord", HW_H)
        self.assertNotIn("std::vector<GuardTransitionAuditRecord>", HW_H)
        cpp_test = (REPO / "src/point-to-point/test/point-to-point-test.cc").read_text()
        self.assertIn("Emit (first, &sink, 1, 10)", cpp_test)
        self.assertIn("Emit (first, &sink, 2, 11)", cpp_test)
        self.assertIn("Emit (second, &sink, 1, 20)", cpp_test)

    def test_cap_and_overflow_paths_are_explicit(self):
        finalize = function("void RdmaHw::FinalizeGuardTransitionAudit",
                            "void RdmaHw::FlushGuardTransitionAudit")
        self.assertIn("sink->written < sink->max_records", finalize)
        self.assertIn("sink->attempted == std::numeric_limits<uint64_t>::max()", finalize)
        begin = function("void RdmaHw::BeginGuardTransitionAudit",
                         "void RdmaHw::RetireGuardTransitionAuditPrefix")
        self.assertIn("transition audit aggregate overflow", begin)
        transaction = function("void RdmaHw::StartGuardFastpathTransaction",
                               "void RdmaHw::StartGuardFastpathPrepare")
        self.assertIn("transition audit transaction exhausted", transaction)
        self.assertLess(
            transaction.index("transition audit transaction exhausted"),
            transaction.index("m_guardFastpathTransaction++;"))
        self.assertIn("guard_transition_audit_hard_max_records = 10000", SIM)
        self.assertIn("# records %lu attempted %lu written %lu truncated %lu", SIM)

    def test_normal_ready_and_terminal_closure(self):
        check = function("void RdmaHw::CheckGuardTransitionPrefixBarrier",
                         "void RdmaHw::HandleGuardTransitionPrefixDeadline")
        self.assertLess(check.index('ResolveGuardTransitionAudit("ready")'),
                        check.index("StartGuardFastpathActivate()"))
        finish = function("void RdmaHw::FinishGuardFastpathTransaction",
                          "void RdmaHw::AppendGuardFastpathTraceFields")
        self.assertIn("activation_ack_closed", finish)
        self.assertIn("fallback_activation_ack_closed", finish)

    def test_timeout_policy_aborts_before_any_fallback_activation(self):
        check = function("void RdmaHw::CheckGuardTransitionPrefixBarrier",
                         "void RdmaHw::HandleGuardTransitionPrefixDeadline")
        abort_index = check.index("GUARD V14 fail-closed transition prefix deadline expired")
        self.assertLess(abort_index, check.index("m_guardTransitionFallbackActive = true"))
        self.assertLess(abort_index, check.index("SendNextGuardTransitionFallbackBatch()"))
        self.assertIn("timeout_fail_closed", check)
        self.assertIn("fail_closed_abort", check)
        self.assertIn(
            'args.guard_transition_prefix_fail_closed and args.cc != "guard"',
            RUN)
        self.assertIn(
            "guard_transition_prefix_fail_closed && cc_mode != 11", SIM)

    def test_epoch_reset_and_simulation_stop_close_active_records(self):
        reset = function("void RdmaHw::ResetGuardFastpathEpoch",
                         "void RdmaHw::ConsumeGuardFastpathMembershipThrough")
        self.assertIn("epoch_reset_unresolved", reset)
        self.assertIn('deadlineOutcome == "pending"', reset)
        self.assertIn("FinalizeGuardTransitionAudit(\"epoch_reset\")", reset)
        self.assertIn("m_guardTransitionAuditEpoch++", reset)
        flush = function("void RdmaHw::FlushGuardTransitionAudit",
                         "void RdmaHw::StartGuardTransitionPrefixBarrier")
        self.assertIn("simulation_stop_unresolved", flush)
        self.assertIn('deadlineOutcome == "pending"', flush)
        self.assertIn("simulation_stop", flush)
        cpp_test = (REPO / "src/point-to-point/test/point-to-point-test.cc").read_text()
        self.assertIn(",ready,simulation_stop\\n", cpp_test)

    def test_cli_validation_rejects_unbounded_or_inapplicable_audits(self):
        run = load_run_module()
        run.validate_guard_transition_audit_options(1, "guard", None, 10000, 1)
        with self.assertRaises(ValueError):
            run.validate_guard_transition_audit_options(1, "hpcc", None, 1, 1)
        with self.assertRaises(ValueError):
            run.validate_guard_transition_audit_options(1, "guard", None, 10001, 1)
        with self.assertRaises(ValueError):
            run.validate_guard_transition_audit_options(1, "guard", None, 1, 0)
        with self.assertRaises(ValueError):
            run.validate_guard_transition_audit_options(0, "guard", "x.csv", 1, 0)

    def test_audit_output_path_cannot_alias_another_artifact(self):
        run = load_run_module()
        run.validate_guard_transition_audit_output_path(
            0, "guard_stats.txt", {"guard stats": "guard_stats.txt"})
        with self.assertRaises(ValueError):
            run.validate_guard_transition_audit_output_path(
                1, "./guard_stats.txt", {"guard stats": "guard_stats.txt"})
        self.assertIn(
            "GUARD_TRANSITION_AUDIT_OUTPUT_FILE must be a ", SIM)
        self.assertIn("NormalizeGuardArtifactPath(*reserved)", SIM)
        self.assertIn('"topology input": os.path.join(', RUN)
        self.assertIn(
            'reserved_outputs["custom traffic source"] = custom_flow_source',
            RUN)

    def test_scratch_reports_record_accounting_without_global_budget_mix(self):
        self.assertIn(
            "guard_transition_audit enabled 1 records %lu attempted %lu ", SIM)
        self.assertIn("written %lu truncated %lu max_records %lu", SIM)
        header = SIM[SIM.index("receiver_node,receiver_nic,epoch,transaction"):]
        self.assertIn("target_vector_hash", header)
        self.assertIn("active_plus_draining_upper_bound_bps", header)
        self.assertIn("deadline_policy,deadline_outcome,terminal_closure", header)


if __name__ == "__main__":
    unittest.main()
