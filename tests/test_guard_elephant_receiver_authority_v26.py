import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
HW_CC = (ROOT / "src/point-to-point/model/rdma-hw.cc").read_text()
HW_H = (ROOT / "src/point-to-point/model/rdma-hw.h").read_text()
RUN = (ROOT / "run.py").read_text()
SCRATCH = (ROOT / "scratch/network-load-balance.cc").read_text()
ANALYZER = (ROOT / "experiments/analyze_guard_v17_general_mechanisms.py").read_text()


class GuardElephantReceiverAuthorityV26Test(unittest.TestCase):
    def test_default_off_and_final_bundle_gate(self):
        block = HW_CC[
            HW_CC.index('.AddAttribute("GuardElephantReceiverAuthority"'):
            HW_CC.index('.AddAttribute("GuardCapHeadroom"')]
        self.assertIn("BooleanValue(false)", block)
        for source in (RUN, SCRATCH):
            self.assertIn("guard_elephant_receiver_authority", source)
            self.assertIn("guard_receiver_concurrency", source)
            self.assertIn("guard_mixed_pg_vector_fastpath", source)
            self.assertIn("guard_serialized_progress_refresh", source)
            self.assertIn("guard_serialized_draining", source)
            self.assertIn("guard_capacity_admission_deferral", source)

    def test_eligibility_requires_real_selected_elephant(self):
        helper = HW_CC[
            HW_CC.index("bool RdmaHw::IsGuardElephantReceiverAuthorityEligible"):
            HW_CC.index("uint32_t RdmaHw::ComputeGuardEffectiveElephantConcurrency")]
        self.assertIn("has_receiver_grant", helper)
        self.assertIn("tail_bypass", helper)
        self.assertIn("grant_rate_bps > minimum_rate_bps", helper)
        self.assertIn("flow_size_bytes", helper)
        self.assertIn("path_bdp_bytes", helper)
        self.assertIn("m_guard_last_grant_generation > 0", helper)
        self.assertIn("IsGuardElephantReceiverAuthorityEligible", HW_H)

    def test_only_eligible_flow_bypasses_fabric_minimum(self):
        sync = HW_CC[HW_CC.index("void RdmaHw::SyncHwRate"):
                     HW_CC.index("void RdmaHw::MaybeSendGuardCapReport")]
        self.assertIn("UsesGuardElephantReceiverAuthority", sync)
        self.assertIn("final_rate = qp->hp.m_grantRate", sync)
        self.assertIn("m_guardElephantReceiverAuthorityBindings++", sync)
        self.assertIn("m_guardElephantReceiverAuthorityRateChanges++", sync)

    def test_validated_generation_precedes_rate_composition(self):
        receive = HW_CC[HW_CC.index("int RdmaHw::ReceiveRate"):
                        HW_CC.index("int RdmaHw::ReceiveGuardGrantAck")]
        self.assertLess(
            receive.index("qp->m_guard_last_grant_generation = generation"),
            receive.index("SyncHwRate(qp, qp->hp.m_curRate)"))

    def test_stats_and_v26_gate_require_active_candidate(self):
        self.assertIn("guard_elephant_receiver_authority enabled %u", SCRATCH)
        self.assertIn("bindings %lu rate_changes %lu", SCRATCH)
        self.assertIn('profile == "V26"', ANALYZER)
        self.assertIn("guard_elephant_receiver_authority_bindings", ANALYZER)
        self.assertIn("guard_elephant_receiver_authority_max_released_bps", ANALYZER)

    def test_base_gate_requires_feedback_but_not_fabric_binding(self):
        summary = (ROOT / "experiments/summarize_general_workloads.py").read_text()
        self.assertIn('if arm == "guard_elephant_authority"', summary)
        self.assertIn('checks.pop("full_actual_rate_change")', summary)
        self.assertIn('checks.pop("full_reactive_binding")', summary)
        self.assertIn('checks["authority_receiver_binding"]', summary)


if __name__ == "__main__":
    unittest.main()
