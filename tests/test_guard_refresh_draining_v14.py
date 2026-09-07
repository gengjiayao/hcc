#!/usr/bin/env python3
"""Source contracts for the default-off V14 refresh/draining bundle."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
HW_H = (ROOT / "src/point-to-point/model/rdma-hw.h").read_text()
HW_CC = (ROOT / "src/point-to-point/model/rdma-hw.cc").read_text()
DRIVER = (ROOT / "scratch/network-load-balance.cc").read_text()
RUN = (ROOT / "run.py").read_text()


class TerminalDrainEventModel:
    """Small event oracle for serialized terminal-reservation ownership."""

    def __init__(self, *, active=(), draining=(), pending=(), epoch=7,
                 progress_transaction_active=False):
        self.active = set(active)
        self.drains = {flow: "DRAINING" for flow in draining}
        self.drains.update({flow: "DRAIN_PENDING" for flow in pending})
        self.contiguous = set()
        self.epoch = epoch
        self.reset_count = 0
        self.receive_completions = 0
        self.draining_completion_releases = 0
        self.boundary_commits = 0
        self.progress_revision = 0
        self.capacity_dirty = False
        self.progress_transaction_active = progress_transaction_active
        self.progress_events_coalesced = 0

    def completion(self, flow):
        self.contiguous.add(flow)
        self.receive_completions += 1
        state = self.drains[flow]
        if state == "DRAIN_PENDING":
            # Completion retires ACTIVE now, while the live vector retains the
            # reservation record until its serialized boundary.
            self.active.discard(flow)
            return
        self._release(flow)

    def boundary(self):
        for flow, state in list(self.drains.items()):
            if state != "DRAIN_PENDING":
                continue
            if flow not in self.active:
                if flow not in self.contiguous:
                    raise AssertionError("inactive pending D is not complete")
                del self.drains[flow]
                self.draining_completion_releases += 1
                self.boundary_commits += 1
                self._settle_if_active_empty()

    def _release(self, flow):
        del self.drains[flow]
        self.draining_completion_releases += 1
        if not self.active:
            self._settle_if_active_empty()
            return
        self.progress_revision += 1
        self.capacity_dirty = True
        if self.progress_transaction_active:
            self.progress_events_coalesced += 1

    def _settle_if_active_empty(self):
        if self.active:
            return
        if not self.drains:
            self.reset_count += 1
            self.epoch = 0


class GuardRefreshDrainingV14Test(unittest.TestCase):
    def test_bundle_is_default_off(self):
        for attribute in ("GuardSerializedProgressRefresh",
                          "GuardSerializedDraining"):
            start = HW_CC.index('.AddAttribute("' + attribute + '"')
            self.assertIn("BooleanValue(false)", HW_CC[start:start + 600])
        start = HW_CC.index('.AddAttribute("GuardCapacityAdmissionDeferral"')
        self.assertIn("BooleanValue(false)", HW_CC[start:start + 600])

    def test_v18_capacity_shortage_defers_without_consuming_membership(self):
        start = HW_CC[HW_CC.index("void RdmaHw::StartGuardFastpathTransaction"):
                      HW_CC.index("bool RdmaHw::ValidateGuardTransitionQueueCohort")]
        self.assertLess(start.index("GuardFastpathTransactionFloorFits"),
                        start.index("m_guardFastpathTransactionWaiters.clear()"))
        self.assertLess(start.index("GuardFastpathTransactionFloorFits"),
                        start.index("ConsumeGuardFastpathMembershipThrough"))
        self.assertIn("m_guardCapacityAdmissionBlocked = true", start)
        self.assertIn("m_guardCapacityAdmissionDeferrals++", start)
        release = HW_CC[HW_CC.index("bool RdmaHw::ReleaseGuardDrainingOnCompletion"):
                        HW_CC.index("bool RdmaHw::HandleRccRemove")]
        self.assertLess(release.index("m_guardDrainingRecords.erase(found)"),
                        release.index("StartGuardFastpathTransaction();"))
        self.assertIn("m_guardCapacityAdmissionBlocked", release)
        for token in ("--guard_capacity_admission_deferral",
                      "guard_capacity_admission_deferral_config"):
            self.assertIn(token, RUN)
        self.assertIn("GUARD_CAPACITY_ADMISSION_DEFERRAL", DRIVER)

    def test_half_enabled_and_unsafe_combinations_fail_closed(self):
        self.assertIn(
            "guard_serialized_progress_refresh != guard_serialized_draining",
            DRIVER)
        self.assertIn(
            "args.guard_serialized_progress_refresh != args.guard_serialized_draining",
            RUN)
        for token in ("!guard_mixed_pg_vector_fastpath",
                      "!guard_remaining_aware",
                      "guard_grant_refresh_bdps <= 0",
                      "!guard_proactive_release", "cc_mode != 11"):
            self.assertIn(token, DRIVER)
        for option in ("--guard_serialized_progress_refresh",
                       "--guard_serialized_draining"):
            self.assertIn(option, RUN)
        self.assertIn(
            "not args.guard_serialized_progress_refresh", RUN)
        self.assertIn("!guard_serialized_progress_refresh", DRIVER)
        self.assertIn(
            "guard_proactive_release &&\n            !guard_serialized_progress_refresh",
            DRIVER)
        self.assertIn(
            "guard_grant_refresh_bdps > 0 &&\n             !guard_serialized_progress_refresh",
            DRIVER)
        self.assertIn(
            "args.guard_proactive_release == 1 and\n                not args.guard_serialized_progress_refresh",
            RUN)
        self.assertIn(
            "args.guard_grant_refresh_bdps > 0 and\n             not args.guard_serialized_progress_refresh",
            RUN)

    def test_acked_issued_and_potential_bounds_are_explicit(self):
        for token in ("m_guard_last_acked_generation",
                      "m_guard_last_acked_upper_bound_bps",
                      "m_guard_last_issued_generation",
                      "m_guard_last_issued_target_bps"):
            self.assertIn(token, HW_H + HW_CC)
        potential = HW_CC[HW_CC.index("uint64_t RdmaHw::GetGuardPotentialUpperBoundBps"):
                          HW_CC.index("bool RdmaHw::IsGuardLedgerActionCompatible")]
        self.assertIn("m_guard_last_acked_upper_bound_bps", potential)
        self.assertIn("m_guard_last_issued_target_bps", potential)

    def test_ack_and_retry_use_the_frozen_ledger(self):
        ack = HW_CC[HW_CC.index("int RdmaHw::ReceiveGuardGrantAck"):
                    HW_CC.index("int RdmaHw::ReceiveGuardCapReport")]
        for token in ("ledger->second.identity != identity",
                      "ledger->second.generation != generation",
                      "IsGuardLedgerActionCompatible",
                      "frozen->targetMbps != ledger->second.targetMbps"):
            self.assertIn(token, ack)
        self.assertNotIn(
            "ledger->second.targetMbps) * 1000000ULL !=\n                    rx_qp->m_guard_grant_rate_bps",
            ack)
        retry = HW_CC[HW_CC.index("void RdmaHw::RefreshGuardGrantsForReliability"):
                      HW_CC.index("void RdmaHw::RedistributeGuardRates")]
        self.assertIn("rate_mbps = ledger->second.targetMbps", retry)
        self.assertLess(retry.index("AssertGuardFrozenPotentialFits()"),
                        retry.index("SendRateControlPacket(flow"))

    def test_vector_freezes_allocation_and_progress_revisions(self):
        for token in ("frozenProgressSeq", "allocationRevision",
                      "progressRevision", "m_guardFrozenAllocationRevision",
                      "m_guardFrozenProgressRevision"):
            self.assertIn(token, HW_H + HW_CC)
        encoder = HW_CC[HW_CC.index("bool RdmaHw::EncodeGuardTargetVector"):
                        HW_CC.index("void RdmaHw::BeginGuardTransitionAudit")]
        self.assertIn("words.push_back(allocation_revision)", encoder)
        self.assertIn("words.push_back(progress_revision)", encoder)
        self.assertIn("words.push_back(reason_mask)", encoder)

    def test_allocation_revision_is_run_monotonic_across_epoch_reset(self):
        reset = HW_CC[HW_CC.index("void RdmaHw::ResetGuardFastpathEpoch"):
                      HW_CC.index("void RdmaHw::ConsumeGuardFastpathMembershipThrough")]
        self.assertEqual(HW_CC.count("m_guardAllocationRevision = 0;"), 1)
        self.assertNotIn("m_guardAllocationRevision = 0;", reset)
        self.assertIn("run-lifetime trace identity", reset)
        self.assertIn("m_guardFrozenAllocationRevision = 0;", reset)

    def test_remaining_aware_targets_use_only_frozen_remaining(self):
        allocation = HW_CC[
            HW_CC.index("bool RdmaHw::ComputeGuardFrozenRequestedTargets"):
            HW_CC.index("bool RdmaHw::IsGuardLedgerActionCompatible")]
        self.assertIn("input.remainingBytes", allocation)
        self.assertNotIn("ReceiverNextExpectedSeq", allocation)
        self.assertIn("effective_min_mbps", allocation)
        freeze = HW_CC[HW_CC.index("void RdmaHw::FreezeGuardFastpathTargetVector"):
                       HW_CC.index("const GuardFrozenTargetRecord")]
        self.assertIn("ComputeGuardFrozenRequestedTargets", freeze)

    def test_progress_threshold_routes_to_the_vector_coordinator(self):
        start = HW_CC.index("if (v_remain > 0 && m_guardRemainingAware")
        receive = HW_CC[start:HW_CC.index("// homa-simple", start)]
        self.assertIn("RequestGuardProgressRefresh", receive)
        serialized = HW_CC[HW_CC.index("void RdmaHw::RequestGuardProgressRefresh"):
                           HW_CC.index("void RdmaHw::ScheduleGuardFastpathHighCollection")]
        self.assertIn("m_guardProgressDirtyFlows.emplace(flow)", serialized)
        self.assertIn("m_guardProgressEventsCoalesced++", serialized)
        self.assertIn("StartGuardProgressTransaction();", serialized)
        self.assertNotIn("m_guard_last_schedule_seq = progress_seq", serialized)

    def test_progress_commits_only_after_barrier_close(self):
        close = HW_CC[HW_CC.index("void RdmaHw::CloseGuardProgressTransaction"):
                      HW_CC.index("void RdmaHw::ScheduleGuardFastpathHighCollection")]
        self.assertIn("m_guardPendingGrantAcks != 0", close)
        self.assertIn("target->frozenProgressSeq", close)
        self.assertIn("m_guard_last_schedule_seq", close)
        request = HW_CC[HW_CC.index("void RdmaHw::RequestGuardProgressRefresh"):
                        HW_CC.index("void RdmaHw::CloseGuardProgressTransaction")]
        self.assertNotIn("m_guard_last_schedule_seq =", request)

    def test_refresh_counterexamples_have_cpp_coverage(self):
        cpp = (ROOT / "src/point-to-point/test/point-to-point-test.cc").read_text()
        for phrase in (
                "50/50 progress refresh must fit",
                "1:3 frozen remaining must produce the 75G side",
                "mutable requested rate must not change a frozen target",
                "late progress must identify a later frozen vector"):
            self.assertIn(phrase, cpp)
        prepare = HW_CC[HW_CC.index("void RdmaHw::StartGuardFastpathPrepare"):
                        HW_CC.index("void RdmaHw::StartGuardFastpathActivate")]
        self.assertIn("GetGuardPotentialUpperBoundBps(flow) > target_bps", prepare)

    def test_draining_record_is_full_identity_and_capacity_bounded(self):
        for token in ("GuardDrainingRecord", "GuardQpIdentity identity",
                      "Ptr<RdmaRxQueuePair> hold", "receiverNic",
                      "priorityGroup", "lastAckedUpperBoundBps",
                      "lastIssuedTargetBps", "reservedBps", "generation",
                      "remainingBytesAtRelease", "releaseNs",
                      "GUARD_DRAIN_PENDING", "GUARD_DRAINING"):
            self.assertIn(token, HW_H)

    def test_transaction_defers_proactive_drain(self):
        drain = HW_CC[HW_CC.index("bool RdmaHw::RequestGuardProactiveDrain"):
                      HW_CC.index("bool RdmaHw::HandleRccRemove")]
        for token in ("m_guardFastpathPhase != GUARD_FASTPATH_IDLE",
                      "m_guardPendingGrantAcks != 0",
                      "m_guardFrozenVectorActive",
                      "m_guardProgressTransactionActive",
                      "GUARD_DRAIN_PENDING", "GUARD_DRAINING"):
            self.assertIn(token, drain)
        self.assertIn("requires an ACTIVE flow", drain)
        receive = HW_CC[HW_CC.index("if (v_remain == 0)"):
                        HW_CC.index("if (v_remain > 0 && m_guardRemainingAware")]
        self.assertIn("RequestGuardProactiveDrain", receive)
        commit = HW_CC[HW_CC.index("uint64_t RdmaHw::CommitGuardPendingDrains"):
                       HW_CC.index("void RdmaHw::ScheduleGuardFastpathHighCollection")]
        self.assertIn("found->second.state = GUARD_DRAINING", commit)
        self.assertIn(
            "record.lastIssuedTargetBps = flow->m_guard_last_issued_target_bps",
            commit)
        self.assertIn("AssertGuardDrainReplacementFits", commit)
        self.assertIn("m_guardApplyingPendingDrains = true", commit)
        self.assertIn(
            "m_guardFastpathMembershipRevision += proactive_removals", commit)
        finish = HW_CC[HW_CC.index("void RdmaHw::FinishGuardFastpathTransaction"):
                       HW_CC.index("void RdmaHw::RequestGuardMembershipUpdate")]
        high_fan = finish[finish.index("if (m_guardFastpathHighFanIn)"):]
        self.assertIn("StartGuardProgressTransaction();", high_fan)
        self.assertIn("m_guardCapacityDirty", high_fan)

    def test_allocator_subtracts_draining_reservations(self):
        freeze = HW_CC[HW_CC.index("void RdmaHw::FreezeGuardFastpathTargetVector"):
                       HW_CC.index("const GuardFrozenTargetRecord")]
        for token in ("GetGuardDrainingReservedBps(receiver_nic)",
                      "allocatable_bps = capacity_bps - draining_reserved_bps",
                      "waiter_floor > allocatable_bps",
                      "ComputeGuardFrozenRequestedTargets(capacity_bps, allocatable_bps"):
            self.assertIn(token, freeze)
        self.assertIn("potential_sum + m_guardFrozenDrainingReservedBps", HW_CC)
        required = HW_CC[
            HW_CC.index("void RdmaHw::SendGuardFastpathRequiredGeneration("):
            HW_CC.index("void RdmaHw::SendGuardFastpathRequiredGenerationOrdered")]
        self.assertLess(required.index("AssertGuardFrozenPotentialFits()"),
                        required.index("SendRateControlPacket(flow"))
        encoder = HW_CC[HW_CC.index("bool RdmaHw::EncodeGuardTargetVector"):
                        HW_CC.index("void RdmaHw::BeginGuardTransitionAudit")]
        self.assertIn("words.push_back(draining_reserved_bps)", encoder)
        self.assertIn("words.push_back(allocatable_capacity_bps)", encoder)

    def test_frozen_allocator_applies_bounded_elephant_service(self):
        allocator = HW_CC[
            HW_CC.index("bool RdmaHw::ComputeGuardFrozenRequestedTargets"):
            HW_CC.index("bool RdmaHw::IsGuardLedgerActionCompatible")]
        for token in (
                "flow->m_guard_flow_size",
                "m_guardConcurrencyMinBdps",
                "elephants.size() > effective_concurrency",
                "receives_residual[elephants[rank]] = true",
                "m_guardConcurrencyLimitedAllocations++",
                "m_guardConcurrencyMaxDeferredFlows"):
            self.assertIn(token, allocator)
        self.assertLess(
            allocator.index("(*inputs)[index].requestedBps = floor_share"),
            allocator.index("weights[index] / weight_sum"))

    def test_bounded_elephant_mode_requires_complete_frozen_bundle(self):
        for source in (RUN, DRIVER):
            self.assertIn("frozen_concurrency", source)
            self.assertIn("guard_receiver_concurrency", source)
            self.assertIn("guard_mixed_pg_vector_fastpath", source)
            self.assertIn("guard_serialized_progress_refresh", source)
            self.assertIn("guard_serialized_draining", source)
            self.assertIn("guard_capacity_admission_deferral", source)

    def test_watchdog_occupancy_includes_draining(self):
        budget = HW_CC[
            HW_CC.index("bool RdmaHw::ComputeGuardTransitionPrefixWireBudget"):
            HW_CC.index("bool RdmaHw::IsGuardTransitionPrefixWatchdogAggregateInconsistent")]
        self.assertIn("record.reservedUpperBoundBps", budget)
        self.assertIn("occupancy_bps += record.reservedUpperBoundBps", budget)
        self.assertIn("budget->drainingReservedBps", budget)
        audit = HW_CC[HW_CC.index("void RdmaHw::BeginGuardTransitionAudit"):
                      HW_CC.index("void RdmaHw::RetireGuardTransitionAuditPrefix")]
        self.assertIn("active_upper_bps += draining.reservedBps", audit)
        self.assertIn("watchdog_budget->drainingReservedBps", audit)
        self.assertIn("watchdog occupancy must include draining D",
                      (ROOT / "src/point-to-point/test/point-to-point-test.cc").read_text())

    def test_only_contiguous_completion_releases_draining(self):
        release = HW_CC[HW_CC.index("bool RdmaHw::ReleaseGuardDrainingOnCompletion"):
                        HW_CC.index("bool RdmaHw::HandleRccRemove")]
        self.assertIn("CanReleaseGuardDraining(rx_qp->m_guard_flow_size", release)
        self.assertIn("found->second.state == GUARD_DRAIN_PENDING", release)
        self.assertIn("found->second.state != GUARD_DRAINING", release)
        self.assertIn("m_guardDrainingRecords.erase(found)", release)
        self.assertIn("m_guardCapacityDirty = true", release)
        receive = HW_CC[HW_CC.index("if (v_remain == 0)"):
                        HW_CC.index("else if (m_guardProactiveRelease")]
        self.assertIn("ReleaseGuardDrainingOnCompletion", receive)
        self.assertIn("tracked_drain->second.state == GUARD_DRAIN_PENDING",
                      receive)
        self.assertIn("pending_drain\n                    ? HandleRccRemove", receive)
        commit = HW_CC[HW_CC.index("uint64_t RdmaHw::CommitGuardPendingDrains"):
                       HW_CC.index("void RdmaHw::ScheduleGuardFastpathHighCollection")]
        self.assertIn("CanReleaseGuardDraining(hold->m_guard_flow_size", commit)
        self.assertIn("pending drain lost ACTIVE before contiguous completion",
                      commit)
        self.assertIn("m_guardDrainingCompletionReleases++", commit)
        inactive = commit[
            commit.index("if (!active)"):
            commit.index("uint64_t effective_min_mbps")]
        self.assertNotIn("m_guardCompletionReleases++", inactive)
        boundary_completion = commit[
            commit.index("if (removed)"):
            commit.index("m_guardApplyingPendingDrains = false")]
        self.assertIn("m_guardCompletionReleases++", boundary_completion)

    def test_active_empty_draining_nonempty_does_not_reset(self):
        release = HW_CC[HW_CC.index("bool RdmaHw::ReleaseGuardDrainingOnCompletion"):
                        HW_CC.index("bool RdmaHw::HandleRccRemove")]
        terminal = release[release.index("if (m_rate_flow_ctl_set.empty())"):]
        terminal = terminal[:terminal.index("m_guardProgressRevision")]
        self.assertIn("SettleGuardActiveEmptyState();", terminal)
        self.assertNotIn("CanResetGuardCoordinator()", terminal)
        self.assertNotIn("ResetGuardFastpathEpoch()", terminal)
        self.assertLess(release.index("m_guardDrainingRecords.erase(found)"),
                        release.index("SettleGuardActiveEmptyState();"))
        remove = HW_CC[HW_CC.index("bool RdmaHw::HandleRccRemove"):
                       HW_CC.index("Time RdmaHw::GetGuardInitialCollectionQuietWindow")]
        self.assertIn("m_guardFastpathPhase != GUARD_FASTPATH_IDLE", remove)
        self.assertNotIn(
            "!m_guardDrainingRecords.empty() &&\n        m_guardFastpathPhase",
            remove)
        self.assertIn("ACTIVE-empty transaction has no closable generation", remove)
        self.assertIn("SettleGuardActiveEmptyState();", remove)
        self.assertLess(remove.index("FinishGuardFastpathGeneration();"),
                        remove.index("SettleGuardActiveEmptyState();"))
        settle = HW_CC[HW_CC.index("void RdmaHw::SettleGuardActiveEmptyState"):
                       HW_CC.index("bool RdmaHw::CanResetGuardCoordinator")]
        self.assertIn("m_guardFastpathConsumedMembershipRevision", settle)
        self.assertIn("m_guardConsumedProgressRevision", settle)
        self.assertIn("CanResetGuardCoordinator()", settle)
        reset = HW_CC[HW_CC.index("bool RdmaHw::CanResetGuardCoordinator"):
                      HW_CC.index("void RdmaHw::ScheduleGuardFastpathHighCollection")]
        for token in ("m_guardDrainingRecords.empty()",
                      "m_guardGenerationLedger.empty()",
                      "m_guardProgressDirtyFlows.empty()",
                      "m_guardConsumedProgressRevision == m_guardProgressRevision",
                      "m_guardPendingGrantAcks == 0"):
            self.assertIn(token, reset)

    def test_two_terminal_drains_complete_at_the_same_tick(self):
        model = TerminalDrainEventModel(draining=("d0", "d1"), epoch=23)
        model.completion("d0")
        self.assertEqual(len(model.drains), 1)
        self.assertEqual(model.epoch, 23)
        self.assertEqual(model.reset_count, 0)
        model.completion("d1")
        self.assertEqual(len(model.drains), 0)
        self.assertEqual(model.epoch, 0)
        self.assertEqual(model.reset_count, 1)
        self.assertEqual(model.draining_completion_releases, 2)

    def test_pending_completion_is_released_only_at_boundary(self):
        model = TerminalDrainEventModel(active=("pending",),
                                        pending=("pending",), epoch=29)
        model.completion("pending")
        self.assertNotIn("pending", model.active)
        self.assertIn("pending", model.drains)
        self.assertEqual(model.draining_completion_releases, 0)
        model.boundary()
        self.assertEqual(model.drains, {})
        self.assertEqual(model.boundary_commits, 1)
        self.assertEqual(model.draining_completion_releases, 1)
        self.assertEqual(model.receive_completions, 1)
        self.assertEqual(model.reset_count, 1)

    def test_active_capacity_release_serializes_one_revector(self):
        release = HW_CC[HW_CC.index("bool RdmaHw::ReleaseGuardDrainingOnCompletion"):
                        HW_CC.index("bool RdmaHw::HandleRccRemove")]
        self.assertLess(release.index("m_guardDrainingRecords.erase(found)"),
                        release.index("m_guardProgressRevision++"))
        self.assertLess(release.index("m_guardProgressRevision++"),
                        release.index("m_guardCapacityDirty = true"))
        busy = release[release.index("bool busy ="):]
        self.assertLess(busy.index("if (busy)"),
                        busy.index("StartGuardProgressTransaction();"))
        model = TerminalDrainEventModel(
            active=("survivor",), draining=("d0", "d1"),
            progress_transaction_active=True)
        model.completion("d0")
        model.completion("d1")
        self.assertEqual(model.progress_revision, 2)
        self.assertTrue(model.capacity_dirty)
        self.assertEqual(model.progress_events_coalesced, 2)
        self.assertEqual(model.reset_count, 0)

    def test_draining_counterexamples_have_cpp_coverage(self):
        cpp = (ROOT / "src/point-to-point/test/point-to-point-test.cc").read_text()
        for phrase in (
                "remove-at-50 plus new-at-100 must not issue 150G",
                "pending decrease removal must reserve the possibly applied issue",
                "sole 100G drain must gate a new 100G waiter",
                "D=99.95G must fail below the 100M effective minimum",
                "an out-of-order final packet must not release draining",
                "ACTIVE empty with draining nonempty must not reset",
                "watchdog must serialize at C-active-D"):
            self.assertIn(phrase, cpp)

    def test_counters_and_trace_are_bounded(self):
        for token in ("m_guardSerializedProgressRequests",
                      "m_guardSerializedProgressTransactions",
                      "m_guardSerializedProgressCommits",
                      "m_guardDrainingRequests",
                      "m_guardDrainingBoundaryCommits",
                      "m_guardDrainingCompletionReleases",
                      "m_guardDrainingMaxRecords",
                      "m_guardDrainingMaxReservedBps"):
            self.assertIn(token, HW_H + HW_CC)
        self.assertIn("guard_refresh_draining enabled 1", DRIVER)
        self.assertIn("terminal_draining_records", DRIVER)
        self.assertIn("terminal_progress_dirty", DRIVER)
        self.assertIn("allocation_revision,progress_revision", DRIVER)
        self.assertIn("sink->written >= sink->max_lines", HW_CC)

    def test_grant_trace_separates_frozen_and_live_capacity_provenance(self):
        for field in (
                "snapshot_valid", "frozen_capacity_bps",
                "frozen_active_records", "frozen_draining_records",
                "frozen_draining_reserved_bps", "frozen_allocatable_bps",
                "frozen_encoded_target_bps", "live_active_records",
                "live_draining_records", "live_draining_reserved_bps",
                "capacity_recompute_pending"):
            self.assertIn(field, DRIVER)
        for state in (
                "m_guardFrozenActiveRecords", "m_guardFrozenDrainingRecords",
                "m_guardFrozenEncodedTargetBps"):
            self.assertIn(state, HW_H)
            self.assertGreaterEqual(HW_CC.count(f"{state} = 0;"), 4)
        trace = HW_CC[HW_CC.index("void RdmaHw::AppendGuardFastpathTraceFields"):
                      HW_CC.index("void RdmaHw::TraceGuardGrant(")]
        self.assertIn("receiver_authoritative && m_guardFrozenVectorActive", trace)
        self.assertIn("ValidateGuardGrantTraceSnapshot", trace)
        self.assertIn("m_guardCapacityDirty ||", trace)
        self.assertIn("m_guardProgressTransactionCapacityDirty", trace)
        self.assertIn("snapshot_valid ? m_guardFrozenDrainingReservedBps : 0", trace)
        validator = HW_CC[HW_CC.index("bool RdmaHw::ValidateGuardGrantTraceSnapshot"):
                          HW_CC.index("uint64_t RdmaHw::ComputeGuardDrainingReservationBps")]
        self.assertIn("frozen_encoded_target_bps <= frozen_allocatable_bps", validator)
        self.assertIn("live_draining_bps <= frozen_draining_bps", validator)
        self.assertIn("capacity_recompute_pending", validator)

    def test_frozen_cohort_allows_only_proven_deferred_waiters(self):
        freeze = HW_CC[HW_CC.index("void RdmaHw::FreezeGuardFastpathTargetVector"):
                       HW_CC.index("const GuardFrozenTargetRecord")]
        for token in (
                "cohort_set", "ClassifyGuardFrozenActiveProvenance",
                "m_guard_registration_membership_revision",
                "m_guardFastpathMembershipRevision",
                "m_guardPendingMembershipChanges",
                "m_guardFastpathWaiters.find(flow)",
                "m_guardFastpathIncumbents.find(flow)",
                "m_guardFastpathTransactionWaiters.find(flow)"):
            self.assertIn(token, freeze)
        self.assertNotIn("cohort.size() != m_rate_flow_ctl_set.size()", freeze)
        self.assertIn("m_guardFrozenActiveRecords = cohort.size()", freeze)
        trace = HW_CC[HW_CC.index("void RdmaHw::AppendGuardFastpathTraceFields"):
                      HW_CC.index("void RdmaHw::TraceGuardGrant(")]
        self.assertIn("live_active_records = m_rate_flow_ctl_set.size()", trace)

        classifier = HW_CC[
            HW_CC.index("RdmaHw::ClassifyGuardFrozenActiveProvenance"):
            HW_CC.index("Ptr<RdmaQueuePair> RdmaHw::GetQp", HW_CC.index(
                "RdmaHw::ClassifyGuardFrozenActiveProvenance"))]
        for token in (
                "input.inFastpathWaiters", "!input.inIncumbents",
                "!input.inTransactionWaiters",
                "input.registrationMembershipRevision >",
                "input.pendingMembershipChanges > 0",
                "input.lastAckedUpperBoundBps == 0",
                "input.lastIssuedTargetBps == 0",
                "input.grantUpperBoundBps == 0",
                "input.grantGeneration == 0",
                "input.hasFirstGrantGateBytes",
                "input.firstGrantGateBytes > 0",
                "input.receiverNic == input.frozenReceiverNic",
                "input.receiverCapacityBps == input.frozenReceiverCapacityBps"):
            self.assertIn(token, classifier)

    def test_deferred_waiter_counterexamples_have_cpp_coverage(self):
        cpp = (ROOT / "src/point-to-point/test/point-to-point-test.cc").read_text()
        for phrase in (
                "cohort two plus deferred one must classify",
                "non-waiter outside the cohort must fail closed",
                "nonzero deferred potential must fail closed",
                "deferred waiter without dirty membership must fail closed",
                "deferred waiter on another receiver NIC must fail closed",
                "deferred waiter without first-grant metadata must fail closed"):
            self.assertIn(phrase, cpp)
        small_set = (ROOT / "tests/test_guard_small_set_fastpath.py").read_text()
        self.assertIn("test_busy_flush_late_waiter_remains_dirty", small_set)


if __name__ == "__main__":
    unittest.main()
