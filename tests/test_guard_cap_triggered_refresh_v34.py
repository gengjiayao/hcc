import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HW_CC = (ROOT / "src/point-to-point/model/rdma-hw.cc").read_text(
    encoding="utf-8")
HW_H = (ROOT / "src/point-to-point/model/rdma-hw.h").read_text(
    encoding="utf-8")
RUNNER = (ROOT / "run.py").read_text(encoding="utf-8")
SCRATCH = (ROOT / "scratch/network-load-balance.cc").read_text(
    encoding="utf-8")


class GuardCapTriggeredRefreshV34Test(unittest.TestCase):
    def test_cap_reports_can_refresh_without_spillover_allocation(self):
        report_gate = HW_CC.index("void RdmaHw::MaybeSendGuardCapReport")
        refresh = HW_CC.index("void RdmaHw::RequestGuardCapacityRefresh")
        allocator = HW_CC.index("if (m_guardElephantCapSpillover &&", refresh)
        self.assertIn("m_guardCapTriggeredRefresh", HW_CC[report_gate:refresh])
        self.assertIn("m_guardCapTriggeredRefresh", HW_CC[refresh:allocator])
        self.assertNotIn("m_guardCapTriggeredRefresh", HW_CC[allocator:allocator + 250])

    def test_feature_is_default_off_and_serialized(self):
        self.assertIn("bool m_guardCapTriggeredRefresh;", HW_H)
        self.assertIn("GuardCapTriggeredRefresh", HW_CC)
        self.assertIn("--guard_cap_triggered_refresh", RUNNER)
        self.assertIn("cap-triggered refresh requires final K=1 GUARD", RUNNER)
        self.assertIn("GUARD_CAP_TRIGGERED_REFRESH", SCRATCH)
        self.assertIn("StartGuardProgressTransaction();", HW_CC)
        self.assertIn("m_guardCapacityDirty = true", HW_CC)

    def test_refresh_has_dedicated_counter(self):
        self.assertIn("m_guardCapTriggeredRefreshRequests", HW_H)
        self.assertIn("m_guardCapRefreshMaterialPercent", HW_H)
        self.assertIn("GuardCapRefreshMaterialPercent", HW_CC)
        self.assertIn("--guard_cap_refresh_material_percent", RUNNER)
        self.assertIn("material_percent %u", SCRATCH)

    def test_material_delta_uses_configured_relative_threshold(self):
        self.assertIn(
            "scaled_delta =\n            static_cast<__uint128_t>(old_cap_bps) *\n"
            "            m_guardCapRefreshMaterialPercent",
            HW_CC,
        )
        self.assertIn("100000000ULL, relative_delta", HW_CC)


if __name__ == "__main__":
    unittest.main()
