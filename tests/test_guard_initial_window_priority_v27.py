import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
CUSTOM_H = (ROOT / "src/network/utils/custom-header.h").read_text()
CUSTOM_CC = (ROOT / "src/network/utils/custom-header.cc").read_text()
HW_CC = (ROOT / "src/point-to-point/model/rdma-hw.cc").read_text()
SWITCH = (ROOT / "src/point-to-point/model/switch-node.cc").read_text()
RUN = (ROOT / "run.py").read_text()
SCRATCH = (ROOT / "scratch/network-load-balance.cc").read_text()


class GuardInitialWindowPriorityV27Test(unittest.TestCase):
    def test_default_off_and_guard_only_cli_gate(self):
        block = HW_CC[
            HW_CC.index('.AddAttribute("GuardInitialWindowPriority"'):
            HW_CC.index('.AddAttribute("GuardSenderSrpt"')]
        self.assertIn("BooleanValue(false)", block)
        self.assertIn("--guard_initial_window_priority", RUN)
        self.assertIn('args.cc != "guard"', RUN)
        self.assertIn("args.guard_size_priority != 1", RUN)

    def test_dscp_encoding_preserves_ecn_and_immutable_udp_pg(self):
        self.assertIn("GUARD_SCHEDULE_CLASS_VALID = 0x20", CUSTOM_H)
        self.assertIn("GUARD_SCHEDULE_CLASS_MASK = 0x1c", CUSTOM_H)
        self.assertIn("(ecnBits & 0x3)", CUSTOM_CC)
        packet = HW_CC[HW_CC.index("uint8_t ip_tos = 0;"):
                       HW_CC.index("ipHeader.SetTos(ip_tos);")]
        self.assertIn("seq < qp->GetGuardFirstGrantWin()", packet)
        self.assertIn("!qp->m_guard_initial_priority_closed", packet)
        self.assertIn("std::min<uint8_t>(scheduling_pg, 3)", packet)
        self.assertNotIn("qp->m_pg = scheduling_pg", packet)

    def test_switch_separates_service_queue_from_lossless_pg(self):
        enqueue = SWITCH[SWITCH.index("void SwitchNode::DoSwitchSend"):
                         SWITCH.index("void SwitchNode::SwitchNotifyDequeue")]
        self.assertIn("admissionQIndex = ch.udp.pg", enqueue)
        self.assertIn("SwitchSend(qIndex, p, ch)", enqueue)
        self.assertIn("CheckAndSendPfc(inDev, admissionQIndex)", enqueue)
        dequeue = SWITCH[SWITCH.index("void SwitchNode::SwitchNotifyDequeue"):
                         SWITCH.index("uint32_t SwitchNode::EcmpHash")]
        self.assertIn("RemoveFromIngressAdmission(inDev, admissionQIndex", dequeue)
        self.assertIn("RemoveFromEgressAdmission(ifIndex, admissionQIndex", dequeue)
        self.assertIn("CheckAndSendResume(inDev, admissionQIndex)", dequeue)

    def test_stats_expose_bounded_initial_window_activity(self):
        self.assertIn("guard_initial_window_priority enabled %u", SCRATCH)
        self.assertIn("flows %lu packets %lu bytes %lu", SCRATCH)
        self.assertIn("transitions %lu", SCRATCH)
        self.assertIn("unscheduled_pg 3", SCRATCH)


if __name__ == "__main__":
    unittest.main()
