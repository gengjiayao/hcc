import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
HW_CC = (ROOT / "src/point-to-point/model/rdma-hw.cc").read_text()
HW_H = (ROOT / "src/point-to-point/model/rdma-hw.h").read_text()
RUN = (ROOT / "run.py").read_text()
SCRATCH = (ROOT / "scratch/network-load-balance.cc").read_text()
ANALYZER = (ROOT / "experiments/analyze_guard_v17_general_mechanisms.py").read_text()


class GuardElephantFabricTargetV25Test(unittest.TestCase):
    def test_default_off_and_scale_are_bounded(self):
        block = HW_CC[HW_CC.index('.AddAttribute("GuardElephantFabricTarget"'):
                      HW_CC.index('.AddAttribute("GuardCapHeadroom"')]
        self.assertIn("BooleanValue(false)", block)
        self.assertIn("DoubleValue(1.0)", block)
        self.assertIn("MakeDoubleChecker<double>(1.0, 2.0)", block)

    def test_exact_helper_preserves_strict_size_boundary(self):
        helper = HW_CC[HW_CC.index("bool RdmaHw::ComputeGuardElephantFabricTarget"):
                       HW_CC.index("uint32_t RdmaHw::ComputeGuardEffectiveElephantConcurrency")]
        self.assertIn("flow_size_bytes", helper)
        self.assertIn("threshold_bdps", helper)
        self.assertIn("path_bdp_bytes", helper)
        self.assertIn("base_target * scale", helper)
        self.assertIn(" >\n", helper)
        self.assertIn("ComputeGuardElephantFabricTarget", HW_H)

    def test_only_full_hpcc_updates_count(self):
        update = HW_CC[HW_CC.index("void RdmaHw::UpdateRateHp"):
                       HW_CC.index("void RdmaHw::UpdateRateTimely")]
        self.assertIn("m_guardElephantFabricTarget", update)
        self.assertIn("elephant_target_eligible && updated_any", update)
        self.assertIn("m_guardElephantFabricTargetUpdates++", update)

    def test_cli_requires_final_k1_vector_bundle(self):
        for source in (RUN, SCRATCH):
            self.assertIn("guard_elephant_fabric_target", source)
            self.assertIn("guard_receiver_concurrency", source)
            self.assertIn("guard_mixed_pg_vector_fastpath", source)
            self.assertIn("guard_serialized_progress_refresh", source)
            self.assertIn("guard_serialized_draining", source)
            self.assertIn("guard_capacity_admission_deferral", source)

    def test_stats_and_mechanism_gate_expose_scope(self):
        self.assertIn("guard_elephant_fabric_target enabled %u", SCRATCH)
        self.assertIn("threshold_bdps %.6f updates %lu", SCRATCH)
        self.assertIn('profile == "V25"', ANALYZER)
        self.assertIn("guard_elephant_fabric_target_updates", ANALYZER)


if __name__ == "__main__":
    unittest.main()
