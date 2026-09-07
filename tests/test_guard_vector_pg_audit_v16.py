#!/usr/bin/env python3
"""Source contracts for per-freeze priority-group evidence."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
HW_H = (ROOT / "src/point-to-point/model/rdma-hw.h").read_text()
HW_CC = (ROOT / "src/point-to-point/model/rdma-hw.cc").read_text()
DRIVER = (ROOT / "scratch/network-load-balance.cc").read_text()


class GuardVectorPgAuditV16Test(unittest.TestCase):
    def test_runtime_identity_pg_sets_drive_the_audit(self):
        freeze = HW_CC[
            HW_CC.index("void RdmaHw::FreezeGuardFastpathTargetVector"):
            HW_CC.index("const GuardFrozenTargetRecord")
        ]
        self.assertIn("priority_groups.emplace(target.identity.pg)", freeze)
        self.assertIn("priority_group_mask |= 1ULL << priority_group", freeze)
        self.assertIn("m_guardVectorMixedPriorityGroupMask |=", freeze)
        self.assertIn("m_guardVectorAllPriorityGroupMask |=", freeze)
        self.assertNotIn("m_guardSizePriority", freeze)

    def test_mask_is_bounded_and_initialized(self):
        self.assertIn("priority_group >= 64", HW_CC)
        for token in (
            "m_guardVectorMaxPriorityGroups = 0",
            "m_guardVectorMixedPriorityGroupMask = 0",
            "m_guardVectorAllPriorityGroupMask = 0",
        ):
            self.assertIn(token, HW_CC)
        for token in (
            "m_guardVectorMaxPriorityGroups",
            "m_guardVectorMixedPriorityGroupMask",
            "m_guardVectorAllPriorityGroupMask",
        ):
            self.assertIn(token, HW_H)

    def test_stats_preserve_max_and_union_provenance(self):
        for token in (
            "max_priority_groups %lu",
            "mixed_priority_group_mask %lu",
            "all_priority_group_mask %lu",
            "max_vector_priority_groups = std::max",
            "mixed_vector_priority_group_mask |=",
            "all_vector_priority_group_mask |=",
        ):
            self.assertIn(token, DRIVER)


if __name__ == "__main__":
    unittest.main()
