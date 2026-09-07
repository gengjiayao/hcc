#!/usr/bin/env python3
"""Source and safety-contract regressions for the default-off V14 vector path."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
HW_H = (ROOT / "src/point-to-point/model/rdma-hw.h").read_text()
HW_CC = (ROOT / "src/point-to-point/model/rdma-hw.cc").read_text()
DRIVER = (ROOT / "scratch/network-load-balance.cc").read_text()
RUN = (ROOT / "run.py").read_text()
CPP_TEST = (ROOT / "src/point-to-point/test/point-to-point-test.cc").read_text()


class GuardMixedPgVectorV14Test(unittest.TestCase):
    def test_feature_is_default_off_and_requires_fail_closed_chain(self):
        attribute = HW_CC.index('.AddAttribute("GuardMixedPgVectorFastpath"')
        self.assertIn("BooleanValue(false)", HW_CC[attribute:attribute + 500])
        self.assertIn('key.compare("GUARD_MIXED_PG_VECTOR_FASTPATH")', DRIVER)
        self.assertIn("!guard_transition_prefix_fail_closed", DRIVER)
        self.assertIn("--guard_mixed_pg_vector_fastpath", RUN)
        self.assertIn("not args.guard_transition_prefix_fail_closed", RUN)
        self.assertIn("GUARD_MIXED_PG_VECTOR_FASTPATH 1", RUN)

    def test_full_tuple_live_and_tombstone_collisions_fail_closed(self):
        for token in (
            "m_qpIdentity", "m_rxQpIdentity", "m_qpTombstoneIdentity",
            "m_rxQpTombstoneIdentity", "legacy-key collision on live lookup",
            "legacy-key collision on tombstone lookup",
        ):
            self.assertIn(token, HW_H + HW_CC)
        self.assertIn("senderCollisionA", CPP_TEST)
        self.assertIn("receiverCollisionA", CPP_TEST)
        self.assertIn("late DATA must not resurrect", CPP_TEST)

    def test_one_capacity_is_shared_across_priority_groups(self):
        validator = HW_CC[HW_CC.index("bool RdmaHw::ValidateGuardTransitionQueueCohort"):
                          HW_CC.index("void RdmaHw::StartGuardFastpathPrepare")]
        self.assertIn("nic != expected_nic", validator)
        self.assertIn("capacity_bps != expected_capacity_bps", validator)
        self.assertIn("!m_guardMixedPgVectorFastpath", validator)
        self.assertIn("two PG-local 80G targets", CPP_TEST)

    def test_canonical_vector_hash_is_pointer_free_and_permutation_stable(self):
        encoder = HW_CC[HW_CC.index("bool RdmaHw::EncodeGuardTargetVector"):
                        HW_CC.index("void RdmaHw::BeginGuardTransitionAudit")]
        for token in ("words.push_back(14)", "receiver_nic",
                      "receiver_capacity_bps", "membership_revision",
                      "record.identity.sip", "record.targetMbps"):
            self.assertIn(token, encoder)
        self.assertNotIn("reinterpret_cast", encoder)
        self.assertIn("canonical hash must ignore", CPP_TEST)

    def test_prepare_then_single_activation_covers_70_30_to_80_10_10(self):
        prepare = HW_CC[HW_CC.index("void RdmaHw::StartGuardFastpathPrepare"):
                        HW_CC.index("void RdmaHw::StartGuardFastpathActivate")]
        activate = HW_CC[HW_CC.index("void RdmaHw::StartGuardFastpathActivate"):
                         HW_CC.index("bool RdmaHw::ComputeGuardTransitionPrefixWireBudget")]
        self.assertIn("GetGuardPotentialUpperBoundBps(flow) > target_bps", prepare)
        self.assertIn("target.role == GUARD_VECTOR_WAITER", activate)
        self.assertIn("GetGuardPotentialUpperBoundBps(flow) < target_bps", activate)
        self.assertIn(
            "potential_sum + m_guardFrozenDrainingReservedBps >",
            HW_CC)

    def test_generation_ledger_handles_retry_stale_and_removal(self):
        self.assertIn("m_guardGenerationLedger", HW_H)
        self.assertIn("ACK does not match the frozen generation ledger", HW_CC)
        self.assertIn("generation < qp->m_guard_last_grant_generation", HW_CC)
        self.assertIn("ledger->second.tombstone = true", HW_CC)

    def test_late_join_cannot_mutate_frozen_revision_or_hash(self):
        freeze = HW_CC[HW_CC.index("void RdmaHw::FreezeGuardFastpathTargetVector"):
                       HW_CC.index("const GuardFrozenTargetRecord")]
        self.assertIn("m_guardFrozenMembershipRevision = membership_revision", freeze)
        self.assertIn("m_guardFrozenVectorHash", freeze)
        request = HW_CC[HW_CC.index("void RdmaHw::RequestGuardFastpathMembershipUpdate"):
                        HW_CC.index("void RdmaHw::ScheduleGuardFastpathHighCollection")]
        self.assertIn("m_guardFastpathQueuedMembershipChanges++", request)

    def test_release_decrease_is_required_and_terminal_state_is_zeroed(self):
        release = HW_CC[HW_CC.index("void RdmaHw::SendGuardFastpathOptionalRelease"):
                        HW_CC.index("void RdmaHw::FinishGuardFastpathGeneration")]
        self.assertIn("requires_barrier", release)
        self.assertIn("StartGuardFastpathPrepare();", release)
        self.assertIn("GUARD_VECTOR_RELEASE_DECREASE", HW_CC)
        reset = HW_CC[HW_CC.index("void RdmaHw::ResetGuardFastpathEpoch"):
                      HW_CC.index("void RdmaHw::ConsumeGuardFastpathMembershipThrough")]
        for token in ("m_guardFrozenTargetVector.clear()",
                      "m_guardFrozenTargetHolds.clear()",
                      "m_guardGenerationLedger.clear()"):
            self.assertIn(token, reset)

    def test_evidence_is_constant_space_and_reports_terminal_zero(self):
        self.assertIn("guard_mixed_pg_vector enabled 1", DRIVER)
        for token in ("terminal_records", "terminal_holds", "terminal_ledger"):
            self.assertIn(token, DRIVER)
        self.assertNotIn("push_back(record)", HW_CC)


if __name__ == "__main__":
    unittest.main()
