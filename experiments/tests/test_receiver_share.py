import tempfile
from pathlib import Path
import unittest

from experiments.aggregate_receiver_share import ci, parse_runtime, validate_matrix
from experiments.analyze_receiver_share import (
    common_active_window_metrics, parse_bounded_trace, rate_metrics,
)
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
                + "120,sent,demand,15,0,1,2,2,100000000000,25000000000,10000,60\n"
                + "130,received,none,0,0,1,2,0,100000000000,25000000000,19000,60\n"
                + "# attempted 6 written 6 truncated 0\n",
                encoding="utf-8",
            )
            rows, counts = parse_bounded_trace(path)
            sent = [row for row in rows if row["event"] == "sent"]
            self.assertEqual(sum(row["serialized_bytes"] for row in sent), 180)
            self.assertEqual(counts, {"attempted": 6, "written": 6, "truncated": 0})
            self.assertEqual(sent[-1]["set_change"], "demand")
            audit = rate_metrics(rows, 15, [0, 1], 2)
            self.assertEqual(audit["max_active_c_over_n_error_bps_max"], 0)
            self.assertEqual(
                audit["sender_applied_grants_by_flow"]["0"]["rates_bps"],
                [50_000_000_000, 25_000_000_000],
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

    def test_formal_matrix_requires_five_independent_paired_seeds(self):
        rows = []
        for scenario, levels in (
            ("homogeneous", ("n2", "n4", "n8", "n15")),
            ("heterogeneous", ("k3",)),
        ):
            for level in levels:
                for seed in range(1, 6):
                    for controller in ("guard", "guard-active-only"):
                        rows.append({
                            "scenario": scenario, "level": level,
                            "controller": controller, "seed": seed,
                            "flow_sha256": f"{scenario}-{level}-{seed}",
                            "generated_flows": 2, "completed_flows": 2,
                            "switch_drops": 0, "recovery_events": 0,
                            "pfc_pause_events": 0, "grant_trace_truncated": 0,
                        })
        validate_matrix(rows)
        rows[-1]["flow_sha256"] = "unpaired"
        with self.assertRaisesRegex(SummaryError, "one flow hash"):
            validate_matrix(rows)

    def test_student_t_interval_and_resource_log_parser(self):
        mean, low, high, half = ci([1, 2, 3, 4, 5])
        self.assertEqual(mean, 3)
        self.assertAlmostEqual(mean - low, half)
        self.assertAlmostEqual(high - mean, half)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.log"
            path.write_text(
                "Elapsed (wall clock) time (h:mm:ss or m:ss): 1:02.50\n"
                "Maximum resident set size (kbytes): 54321\n",
                encoding="utf-8",
            )
            self.assertEqual(parse_runtime(path), (62.5, 54321))

    def test_common_active_rate_uses_receiver_progress_at_first_release(self):
        trace = [
            {"event": "sent", "host_node": 15, "flow_id": 0,
             "active_flows": 2, "time_ns": 100, "next_seq": 10,
             "set_change": "registration", "line_rate_bps": 100_000_000_000},
            {"event": "sent", "host_node": 15, "flow_id": 1,
             "active_flows": 2, "time_ns": 100, "next_seq": 20,
             "set_change": "registration", "line_rate_bps": 100_000_000_000},
            {"event": "sent", "host_node": 15, "flow_id": 0,
             "active_flows": 1, "time_ns": 200, "next_seq": 60,
             "set_change": "release", "line_rate_bps": 100_000_000_000},
        ]
        flows = [{"src": 0, "size": 100}, {"src": 1, "size": 100}]
        result = common_active_window_metrics(trace, flows, 15, [0, 1], 2)
        self.assertEqual(result["duration_ns"], 100)
        self.assertEqual(result["per_source"]["0"]["delivered_bytes"], 50)
        self.assertEqual(result["per_source"]["1"]["delivered_bytes"], 80)
        self.assertAlmostEqual(result["aggregate_payload_goodput_gbps"], 10.4)


if __name__ == "__main__":
    unittest.main()
