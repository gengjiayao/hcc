import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
HW_CC = (ROOT / "src/point-to-point/model/rdma-hw.cc").read_text()
HW_H = (ROOT / "src/point-to-point/model/rdma-hw.h").read_text()
QP_H = (ROOT / "src/point-to-point/model/rdma-queue-pair.h").read_text()
RUN = (ROOT / "run.py").read_text()
SCRATCH = (ROOT / "scratch/network-load-balance.cc").read_text()
ANALYZER = (ROOT / "experiments/analyze_guard_v17_general_mechanisms.py").read_text()


class GuardElephantSpilloverV23Test(unittest.TestCase):
    def test_default_off_and_full_bundle_fail_closed(self):
        block = HW_CC[HW_CC.index('.AddAttribute("GuardElephantCapSpillover"'):
                      HW_CC.index('.AddAttribute("GuardCapHeadroom"')]
        self.assertIn("BooleanValue(false)", block)
        for source in (RUN, SCRATCH):
            self.assertIn("guard_elephant_cap_spillover", source)
            self.assertIn("guard_receiver_concurrency", source)
            self.assertIn("guard_adaptive_elephant_concurrency", source)
            self.assertIn("guard_elephant_aging_rtts", source)
            self.assertIn("guard_serialized_progress_refresh", source)
            self.assertIn("guard_serialized_draining", source)
            self.assertIn("guard_capacity_admission_deferral", source)

    def test_reports_use_dedicated_consecutive_state(self):
        receive = HW_CC[HW_CC.index("int RdmaHw::ReceiveGuardCapReport"):
                        HW_CC.index("int RdmaHw::ReceiveAck")]
        self.assertIn("m_guardElephantSpilloverEnterReports", receive)
        self.assertIn("m_guardElephantSpilloverExitReports", receive)
        self.assertIn("reported_rate_bps) * 100U", receive)
        self.assertIn("m_guard_grant_rate_bps) * 95U", receive)
        self.assertIn("RequestGuardCapacityRefresh()", receive)
        self.assertIn("m_guard_spillover_reported_cap_bps", QP_H)

    def test_allocator_transfers_without_increasing_capacity(self):
        allocator = HW_CC[HW_CC.index("bool RdmaHw::ComputeGuardFrozenRequestedTargets"):
                          HW_CC.index("bool RdmaHw::IsGuardLedgerActionCompatible")]
        self.assertIn("effective_concurrency == 1", allocator)
        self.assertIn("elephants.size() < 2", allocator)
        self.assertIn("headroom_numerator", allocator)
        self.assertIn("donor_target_bps - donor_new_bps", allocator)
        self.assertIn("requestedBps += spillover_bps", allocator)
        self.assertNotIn("std::swap(elephants[0], elephants[1])", allocator)

    def test_capacity_change_is_serialized(self):
        request = HW_CC[HW_CC.index("void RdmaHw::RequestGuardCapacityRefresh"):
                        HW_CC.index("void RdmaHw::StartGuardProgressTransaction")]
        self.assertIn("m_guardCapacityDirty = true", request)
        self.assertIn("StartGuardProgressTransaction()", request)
        self.assertIn("m_guardFrozenVectorActive", request)
        self.assertIn("void RequestGuardCapacityRefresh()", HW_H)

    def test_bounded_stats_expose_trigger_and_transfer(self):
        self.assertIn("guard_elephant_spillover enabled %u", SCRATCH)
        self.assertIn("enter_reports %u exit_reports %u", SCRATCH)
        self.assertIn("under_grant_percent 5", SCRATCH)
        self.assertIn("headroom_percent 10", SCRATCH)
        self.assertIn("refresh_requests %lu", SCRATCH)
        self.assertIn("vectors %lu max_bps %lu", SCRATCH)

    def test_mechanism_analyzer_admits_the_frozen_profile(self):
        self.assertIn('"V23", "V24"', ANALYZER)
        self.assertIn('profile in ("V23", "V24")', ANALYZER)


if __name__ == "__main__":
    unittest.main()
