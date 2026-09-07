from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class GuardTransportWindowFloorSourceTests(unittest.TestCase):
    def test_default_off_and_cli_are_explicit(self):
        run = (ROOT / "run.py").read_text()
        scratch = (ROOT / "scratch/network-load-balance.cc").read_text()
        self.assertIn(
            "--guard_transport_window_floor_rtt_ns', type=int, default=0", run)
        self.assertIn("uint64_t guard_transport_window_floor_rtt_ns = 0;", scratch)
        self.assertIn("GUARD_TRANSPORT_WINDOW_FLOOR_RTT_NS", scratch)

    def test_transport_window_is_distinct_from_path_bdp(self):
        header = (ROOT /
                  "src/point-to-point/model/rdma-queue-pair.h").read_text()
        source = (ROOT /
                  "src/point-to-point/model/rdma-queue-pair.cc").read_text()
        hw = (ROOT / "src/point-to-point/model/rdma-hw.cc").read_text()
        self.assertIn("uint32_t m_win", header)
        self.assertIn("uint64_t m_guard_transport_win", header)
        self.assertIn("GetGuardTransportWin() const", header)
        self.assertIn("return m_guard_transport_win != 0 ? m_guard_transport_win : m_win", source)
        self.assertIn("fst.SetFirstGrantGateBytes(qp->GetGuardFirstGrantWin())", hw)
        self.assertIn("qp->m_guard_tail_bypass, qp->m_size, qp->m_win", hw)

    def test_floor_is_topology_rate_derived_and_checked(self):
        source = (ROOT / "src/point-to-point/model/rdma-hw.cc").read_text()
        self.assertIn("static_cast<__uint128_t>(floor_ns) * line_bps", source)
        self.assertIn("product + 8000000000ULL - 1", source)
        self.assertIn("m_guardTransportWindowFloorRtt.IsNegative()", source)
        self.assertIn("m_guardTransportWindowRaisedFlows++", source)

    def test_feature_requires_fixed_window_and_prefix_barrier(self):
        run = (ROOT / "run.py").read_text()
        self.assertIn("not args.guard_fixed_window", run)
        self.assertIn("not args.guard_transition_prefix_barrier", run)
        self.assertIn("exact transition-prefix barrier", run)

    def test_post_grant_scope_requires_a_positive_floor(self):
        run = (ROOT / "run.py").read_text()
        source = (ROOT / "src/point-to-point/model/rdma-queue-pair.cc").read_text()
        self.assertIn("--guard_transport_window_floor_after_first_grant", run)
        self.assertIn("requires a positive transport-window floor", run)
        self.assertIn("GetGuardFirstGrantWin() const", source)
        self.assertIn("snd_nxt >= first_grant_win", source)

    def test_whole_flow_gate_is_bounded_by_transport_window(self):
        run = (ROOT / "run.py").read_text()
        hw = (ROOT / "src/point-to-point/model/rdma-hw.cc").read_text()
        self.assertIn("--guard_transport_window_whole_flow_first_gate", run)
        self.assertIn("requires a positive post-grant transport-window floor", run)
        self.assertIn("size <= qp->GetGuardTransportWin()", hw)
        self.assertIn("m_guardTransportWindowWholeFlowFirstGateFlows++", hw)

    def test_ack_slack_cap_is_path_adaptive_and_checked(self):
        run = (ROOT / "run.py").read_text()
        hw = (ROOT / "src/point-to-point/model/rdma-hw.cc").read_text()
        self.assertIn("--guard_transport_window_ack_slack_packets", run)
        self.assertIn("the positive bounded whole-flow post-grant window", run)
        self.assertIn("static_cast<__uint128_t>(qp->m_win)", hw)
        self.assertIn("m_guardTransportWindowAckSlackPackets) * m_mtu", hw)
        self.assertIn("std::min<uint64_t>(", hw)
        self.assertIn("m_guardTransportWindowAckSlackLimitedFlows++", hw)


if __name__ == "__main__":
    unittest.main()
