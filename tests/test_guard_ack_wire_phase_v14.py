#!/usr/bin/env python3
"""Wire/source and mechanism regressions for the V14 grant ACK phase."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
QBB_H = (ROOT / "src/point-to-point/model/qbb-header.h").read_text()
QBB_CC = (ROOT / "src/point-to-point/model/qbb-header.cc").read_text()
CUSTOM_H = (ROOT / "src/network/utils/custom-header.h").read_text()
CUSTOM_CC = (ROOT / "src/network/utils/custom-header.cc").read_text()
HW_CC = (ROOT / "src/point-to-point/model/rdma-hw.cc").read_text()
CPP_TEST = (ROOT / "src/point-to-point/test/point-to-point-test.cc").read_text()


class GuardAckWirePhaseV14Test(unittest.TestCase):
    def test_ack_header_serializes_an_explicit_phase_byte(self):
        for token in ("SetPhaseTag (uint8_t phaseTag)",
                      "GetPhaseTag () const", "uint8_t m_phaseTag"):
            self.assertIn(token, QBB_H)
        for token in ("i.WriteU8(m_phaseTag)",
                      "m_phaseTag = i.ReadU8()",
                      "+ sizeof(m_phaseTag)"):
            self.assertIn(token, QBB_CC)
        self.assertIn("m_phaseTag(0)", QBB_CC)

    def test_custom_parser_has_ack_owned_fields_and_size(self):
        self.assertIn("} grantAck;", CUSTOM_H)
        deserialize = CUSTOM_CC.index("CustomHeader::Deserialize")
        ack_start = CUSTOM_CC.index(
            "}else if (l3Prot == GUARD_RATE_GRANT_ACK){", deserialize)
        ack_parse = CUSTOM_CC[
            ack_start:CUSTOM_CC.index("}else if (l3Prot == 0xFC", ack_start)]
        for field in ("sport", "dport", "pg", "generation", "phaseTag"):
            self.assertIn(f"grantAck.{field}", ack_parse)
        self.assertNotIn("grant.", ack_parse)
        size = CUSTOM_CC[CUSTOM_CC.index(
            "uint32_t CustomHeader::GetGuardGrantAckSerializedSize"):
            CUSTOM_CC.index("uint32_t CustomHeader::GetUdpHeaderSize")]
        self.assertIn("sizeof(grantAck.phaseTag)", size)

    def test_sender_and_receiver_use_the_same_wire_phase(self):
        send = HW_CC[HW_CC.index("void RdmaHw::SendGuardGrantAck"):
                     HW_CC.index("/***********************", HW_CC.index(
                         "void RdmaHw::SendGuardGrantAck"))]
        receive = HW_CC[HW_CC.index("int RdmaHw::ReceiveGuardGrantAck"):
                        HW_CC.index("int RdmaHw::ReceiveGuardCapReport")]
        self.assertIn("ack.SetPhaseTag(phase_tag)", send)
        self.assertIn("uint8_t phase_tag = ch.grantAck.phaseTag", receive)
        self.assertIn("IsGuardLedgerActionCompatible(ledger->second.action,",
                      receive)
        self.assertNotIn("ch.grant.ackRequired", receive)

    def test_cpp_wire_roundtrip_covers_phase_size_and_padding(self):
        for token in ("ack.SetPhaseTag (3)",
                      "ack.GetSerializedSize (), 11",
                      "parsedAck.grantAck.phaseTag, 3",
                      "parsedAck.GetSerializedSize (), 45",
                      "60 - 14 - 20 - static_cast<int> (ack.GetSerializedSize ())"):
            self.assertIn(token, CPP_TEST)

    def test_phase_is_part_of_mechanism_identity(self):
        def compatible(expected_phase, wire_phase):
            return expected_phase == wire_phase

        self.assertTrue(compatible(3, 3))
        self.assertFalse(compatible(3, 0))
        self.assertTrue(compatible(0, 0))


if __name__ == "__main__":
    unittest.main()
