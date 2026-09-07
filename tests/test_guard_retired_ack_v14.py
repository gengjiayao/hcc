#!/usr/bin/env python3
"""Safety and source contracts for V14 retired required-grant ACKs."""

from dataclasses import dataclass, replace
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
HW_H = (ROOT / "src/point-to-point/model/rdma-hw.h").read_text()
HW_CC = (ROOT / "src/point-to-point/model/rdma-hw.cc").read_text()
DRIVER = (ROOT / "scratch/network-load-balance.cc").read_text()
CPP_TEST = (ROOT / "src/point-to-point/test/point-to-point-test.cc").read_text()


@dataclass(frozen=True)
class Record:
    identity: tuple
    generation: int
    phase: int
    action: str
    target: int
    flow_id: int
    transaction: int
    retired_ns: int


class RetiredAckOracle:
    """Pointer-free event oracle for the one-close/two-observation contract."""

    CAP = 4096

    def __init__(self):
        self.records = []
        self.pending = 1
        self.finish_calls = 0
        self.closures = 0
        self.received = 0
        self.stale = 0
        self.overflow = 0

    @staticmethod
    def compatible(action, phase):
        return ((action in {"prepare", "release_decrease"} and phase in {1, 3}) or
                (action in {"waiter", "increase", "release_increase"} and
                 phase in {2, 4}))

    def retire(self, record):
        if len(self.records) >= self.CAP:
            self.overflow += 1
            raise OverflowError
        self.records.append(record)
        self.closures += 1
        self.pending -= 1
        if self.pending == 0:
            self.finish_calls += 1

    def reset(self):
        # Epoch state resets; in-flight wire authentication must survive.
        pass

    def receive(self, identity, generation, phase, *, rx_qp_live):
        del rx_qp_live  # Retired authentication deliberately runs first.
        for index, record in enumerate(self.records):
            if record.identity == identity and record.generation == generation:
                if record.phase != phase or not self.compatible(record.action, phase):
                    self.stale += 1
                    return "stale"
                self.received += 1
                del self.records[index]
                return "retired"
        self.stale += 1
        return "stale"


class GuardRetiredAckV14Test(unittest.TestCase):
    def setUp(self):
        self.identity = (0x0A000001, 0x0A000002, 4000, 5000, 4)
        self.record = Record(self.identity, 17, 3, "prepare", 25000, 91, 8, 42)

    def test_record_is_pointer_free_and_complete(self):
        start = HW_H.index("struct GuardRetiredAckRecord")
        record = HW_H[start:HW_H.index("};", start)]
        for field in ("GuardQpIdentity identity", "uint32_t generation",
                      "uint8_t phaseTag", "uint8_t action",
                      "uint32_t targetMbps", "int32_t flowId",
                      "uint64_t transactionId", "uint64_t retiredNs"):
            self.assertIn(field, record)
        self.assertNotIn("RdmaRxQueuePair", record)
        self.assertNotIn("Ptr<", record)

    def test_live_ledger_has_and_enforces_exact_phase(self):
        ledger_start = HW_H.index("struct GuardGenerationLedgerEntry")
        ledger = HW_H[ledger_start:HW_H.index("};", ledger_start)]
        self.assertIn("uint8_t phaseTag", ledger)
        receive = HW_CC[HW_CC.index("int RdmaHw::ReceiveGuardGrantAck"):
                        HW_CC.index("int RdmaHw::ReceiveGuardCapReport")]
        self.assertIn("ledger->second.phaseTag != phase_tag", receive)
        self.assertIn("IsGuardLedgerActionCompatible(ledger->second.action", receive)

    def test_remove_authenticates_and_records_before_one_pending_close(self):
        remove = HW_CC[HW_CC.index("bool RdmaHw::HandleRccRemove"):
                       HW_CC.index("Time RdmaHw::GetGuardInitialCollectionQuietWindow")]
        for token in ("ledger->second.identity != identity",
                      "ledger->second.generation !=",
                      "ledger->second.phaseTag != expected_phase",
                      "frozen->identity != identity",
                      "frozen->targetMbps != ledger->second.targetMbps"):
            self.assertIn(token, remove)
        retire = remove.index("RetireGuardRequiredAck(rx_qp, ledger->second);")
        decrement = remove.index("m_guardPendingGrantAcks--;", retire)
        trace = remove.index('"ack_retire_close"', decrement)
        tombstone = remove.index("ledger->second.tombstone = true;", trace)
        self.assertLess(retire, decrement)
        self.assertLess(decrement, trace)
        self.assertLess(trace, tombstone)

    def test_retired_lookup_precedes_live_or_deleted_rxqp_lookup(self):
        receive = HW_CC[HW_CC.index("int RdmaHw::ReceiveGuardGrantAck"):
                        HW_CC.index("int RdmaHw::ReceiveGuardCapReport")]
        retired = receive.index("FindGuardRetiredAckRecord")
        live = receive.index("GetRxQp(")
        self.assertLess(retired, live)
        for live_state in (True, False):
            oracle = RetiredAckOracle()
            oracle.retire(self.record)
            self.assertEqual(
                oracle.receive(self.identity, 17, 3, rx_qp_live=live_state),
                "retired")
            self.assertEqual((oracle.pending, oracle.finish_calls), (0, 1))

    def test_each_tuple_generation_and_phase_mismatch_is_stale_and_preserved(self):
        mutations = []
        for field in range(5):
            identity = list(self.identity)
            identity[field] += 1
            mutations.append((tuple(identity), 17, 3))
        mutations.extend(((self.identity, 18, 3), (self.identity, 17, 1)))
        for identity, generation, phase in mutations:
            oracle = RetiredAckOracle()
            oracle.retire(self.record)
            self.assertEqual(
                oracle.receive(identity, generation, phase, rx_qp_live=False),
                "stale")
            self.assertEqual(oracle.records, [self.record])
            self.assertEqual((oracle.pending, oracle.finish_calls), (0, 1))

    def test_phase_incompatible_action_and_legacy_collision_are_stale(self):
        oracle = RetiredAckOracle()
        incompatible = replace(self.record, action="waiter")
        oracle.retire(incompatible)
        self.assertEqual(
            oracle.receive(self.identity, 17, 3, rx_qp_live=False), "stale")
        self.assertEqual(oracle.records, [incompatible])
        legacy_collision = list(self.identity)
        legacy_collision[0] += 1
        self.assertEqual(
            oracle.receive(tuple(legacy_collision), 17, 3, rx_qp_live=False),
            "stale")

    def test_exact_hit_erases_and_duplicate_cannot_finish_again(self):
        oracle = RetiredAckOracle()
        oracle.retire(self.record)
        self.assertEqual(
            oracle.receive(self.identity, 17, 3, rx_qp_live=False), "retired")
        self.assertEqual(oracle.records, [])
        self.assertEqual(
            oracle.receive(self.identity, 17, 3, rx_qp_live=False), "stale")
        self.assertEqual((oracle.pending, oracle.finish_calls, oracle.received),
                         (0, 1, 1))
        receive = HW_CC[HW_CC.index("int RdmaHw::ReceiveGuardGrantAck"):
                        HW_CC.index("int RdmaHw::ReceiveGuardCapReport")]
        retired_branch = receive[receive.index("m_guardRetiredAcksReceived++;"):
                                 receive.index("Ptr<RdmaRxQueuePair> rx_qp")]
        self.assertIn("m_guardRetiredAckRecords.erase", retired_branch)
        self.assertNotIn("m_guardPendingGrantAcks--", retired_branch)
        self.assertNotIn("FinishGuardFastpathGeneration", retired_branch)
        self.assertNotIn("m_guardFullyAckedBatches", retired_branch)

    def test_reset_finish_and_delete_do_not_clear_retired_records(self):
        finish = HW_CC[HW_CC.index("void RdmaHw::FinishGuardFastpathTransaction"):
                       HW_CC.index("void RdmaHw::RequestGuardMembershipUpdate")]
        reset = HW_CC[HW_CC.index("void RdmaHw::ResetGuardFastpathEpoch"):
                      HW_CC.index("void RdmaHw::ConsumeGuardFastpathMembershipThrough")]
        delete = HW_CC[HW_CC.index("void RdmaHw::DeleteRxQp"):
                       HW_CC.index("int RdmaHw::ReceiveUdp")]
        for body in (finish, reset, delete):
            self.assertNotIn("m_guardRetiredAckRecords.clear", body)
        oracle = RetiredAckOracle()
        oracle.retire(self.record)
        oracle.reset()
        self.assertEqual(oracle.records, [self.record])

    def test_hard_cap_is_fail_closed_and_slots_are_reclaimed(self):
        helper = HW_CC[HW_CC.index("void RdmaHw::RetireGuardRequiredAck"):
                       HW_CC.index("void RdmaHw::StartGuardFastpathTransaction")]
        self.assertIn("kGuardRetiredAckRecordCap = 4096", helper)
        self.assertLess(helper.index("m_guardRetiredAckOverflow++;"),
                        helper.index('NS_ABORT_MSG("GUARD V14 retired ACK ledger exhausted")'))
        oracle = RetiredAckOracle()
        oracle.records = [self.record] * oracle.CAP
        with self.assertRaises(OverflowError):
            oracle.retire(self.record)
        self.assertEqual(oracle.overflow, 1)
        oracle.records.pop()
        oracle.retire(replace(self.record, generation=18))
        self.assertEqual(len(oracle.records), oracle.CAP)

    def test_null_trace_uses_retired_flow_without_dereference(self):
        trace = HW_CC[HW_CC.index("void RdmaHw::TraceGuardGrantAckReceive"):
                      HW_CC.index("void RdmaHw::TraceGuardGrantAckSend")]
        self.assertIn("retired == NULL ? -1 : retired->flowId", trace)
        self.assertIn('std::string(event) == "ack_retired" && retired != NULL', trace)
        self.assertIn("PeekPointer(qp)", trace)
        self.assertIn('"ack_retired", &retired', HW_CC)

    def test_stats_and_cpp_exact_identity_coverage_are_present(self):
        for field in ("m_guardRetiredAckClosures", "m_guardRetiredAcksReceived",
                      "m_guardRetiredAckPeak", "m_guardRetiredAckOverflow"):
            self.assertIn(field, HW_H + HW_CC + DRIVER)
        self.assertIn("guard_retired_ack closures %lu received %lu peak %lu", DRIVER)
        for token in ("GuardRetiredAckIdentityTest",
                      "DoesGuardRetiredAckMatch (record, identity, 17, 3)",
                      "legacyCollision", "incompatible.action"):
            self.assertIn(token, CPP_TEST)


if __name__ == "__main__":
    unittest.main()
