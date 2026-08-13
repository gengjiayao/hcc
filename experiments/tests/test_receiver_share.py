import tempfile
from pathlib import Path
import unittest

from experiments.analyze_receiver_share import parse_bounded_trace, rate_metrics
from experiments.summarize_campaign import SummaryError
from run import HARD_GUARD_GRANT_MAX_LINES, validate_guard_grant_options


HEADER = (
    "time_ns,event,set_change,host_node,flow_id,data_source_ip,data_destination_ip,"
    "active_flows,line_rate_bps,grant_rate_bps,next_seq,serialized_bytes\n"
)


class ReceiverShareTests(unittest.TestCase):
    def test_grant_trace_options_are_bounded_and_guard_only(self):
        validate_guard_grant_options(1, "guard", None, HARD_GUARD_GRANT_MAX_LINES)
        validate_guard_grant_options(1, "guard-active-only", None, 1)
        with self.assertRaisesRegex(ValueError, "must be in"):
            validate_guard_grant_options(1, "guard", None, 0)
        with self.assertRaisesRegex(ValueError, "must be in"):
            validate_guard_grant_options(
                1, "guard", None, HARD_GUARD_GRANT_MAX_LINES + 1)
        with self.assertRaisesRegex(ValueError, "requires a GUARD mode"):
            validate_guard_grant_options(1, "hpcc", None, 16)
        with self.assertRaisesRegex(ValueError, "requires --guard_grant_trace"):
            validate_guard_grant_options(0, "guard", "/tmp/grants.csv", 16)

    def test_raw_trace_preserves_packet_bytes_and_received_rate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "grants.csv"
            path.write_text(
                HEADER
                + "100,sent,registration,15,0,1,2,2,100000000000,50000000000,1000,60\n"
                + "101,sent,registration,15,1,3,2,2,100000000000,50000000000,1000,60\n"
                + "110,received,none,0,0,1,2,0,100000000000,50000000000,9000,60\n"
                + "111,received,none,1,1,3,2,0,100000000000,50000000000,9000,60\n"
                + "# attempted 4 written 4 truncated 0\n",
                encoding="utf-8",
            )
            rows, counts = parse_bounded_trace(path)
            sent = [row for row in rows if row["event"] == "sent"]
            self.assertEqual(sum(row["serialized_bytes"] for row in sent), 120)
            self.assertEqual(counts, {"attempted": 4, "written": 4, "truncated": 0})
            audit = rate_metrics(rows, 15, [0, 1], 2)
            self.assertEqual(audit["max_active_c_over_n_error_bps_max"], 0)
            self.assertEqual(
                audit["sender_applied_grants_by_flow"]["0"]["rates_bps"],
                [50_000_000_000],
            )

    def test_full_guard_raw_trace_can_include_int_header_area(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "grants.csv"
            path.write_text(
                HEADER
                + "100,sent,registration,15,0,1,2,1,100000000000,100000000000,1000,94\n"
                + "110,received,none,0,0,1,2,0,100000000000,100000000000,9000,94\n"
                + "# attempted 2 written 2 truncated 0\n",
                encoding="utf-8",
            )
            rows, _counts = parse_bounded_trace(path)
            self.assertEqual([row["serialized_bytes"] for row in rows], [94, 94])

    def test_trace_rejects_truncation_and_send_receive_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "grants.csv"
            path.write_text(
                HEADER
                + "100,sent,registration,15,0,1,2,1,100000000000,100000000000,1000,60\n"
                + "# attempted 2 written 1 truncated 1\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SummaryError, "truncated"):
                parse_bounded_trace(path)


if __name__ == "__main__":
    unittest.main()
