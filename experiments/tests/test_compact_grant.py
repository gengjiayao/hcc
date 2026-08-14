import json
import tempfile
import unittest
from pathlib import Path

from experiments.analyze_compact_grant import run_view, validate


FIELDS = [
    "grants_sent",
    "grants_received",
    "recovery_nacks_generated",
    "recovery_nacks_received",
    "irn_nacks_generated",
    "irn_nacks_received",
    "irn_retransmit_packets",
    "irn_retransmit_bytes",
    "timeout_recoveries",
    "grant_bytes_sent",
]


class CompactGrantAuditTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.flow = "2 0\n0 15 4 8388608 2.002000000\n1 15 4 8388608 2.002000100\n"
        import hashlib
        self.flow_hash = hashlib.sha256(self.flow.encode()).hexdigest()
        self.spec = {
            "legacy_commit": "old",
            "compact_run_commit": "new",
            "flow_sha256": self.flow_hash,
            "expected": {
                "legacy_serialized_bytes_per_grant": 94,
                "compact_serialized_bytes_per_grant": 60,
                "grant_count": 4,
                "completed_flows": 2,
                "switch_drops": 0,
                "recovery_events": 0,
                "pfc_events": 0,
                "grant_trace_truncated": 0,
            },
        }

    def tearDown(self):
        self.temp.cleanup()

    def make_run(self, name, packet_bytes, mutate_rate=False):
        directory = self.root / name
        directory.mkdir()
        (directory / (name + "_input_flow.txt")).write_text(self.flow)
        (directory / (name + "_out_fct.txt")).write_text(
            "0 15 10000 100 8388608 1 100 10\n"
            "1 15 10000 101 8388608 2 200 10\n"
        )
        (directory / (name + "_out_pfc.txt")).write_text("# header\n")
        values = {
            "grants_sent": 4,
            "grants_received": 4,
            "grant_bytes_sent": 4 * packet_bytes,
        }
        stats_values = [str(values.get(field, 0)) for field in FIELDS]
        (directory / (name + "_out_guard_stats.txt")).write_text(
            "node_id " + " ".join(FIELDS) + "\n"
            "total " + " ".join(stats_values) + "\n"
            "switch_drops ingress 0 egress 0 total 0\n"
        )
        rate = 49000000000 if mutate_rate else 50000000000
        rows = [
            (100, "sent", "registration", 15, 0, 1, 2, 1,
             100000000000, 100000000000, 1000, packet_bytes),
            (101, "sent", "registration", 15, 1, 3, 2, 2,
             100000000000, 50000000000, 1000, packet_bytes),
            (102, "received", "none", 0, 0, 1, 2, 0,
             100000000000, 100000000000, 9000, packet_bytes),
            (103, "received", "none", 1, 1, 3, 2, 0,
             100000000000, rate, 9000, packet_bytes),
        ]
        grant_path = directory / (name + "_grants.csv")
        grant_path.write_text(
            "time_ns,event,set_change,host_node,flow_id,data_source_ip,"
            "data_destination_ip,active_flows,line_rate_bps,grant_rate_bps,"
            "next_seq,serialized_bytes\n"
            + "".join(",".join(map(str, row)) + "\n" for row in rows)
            + "# attempted 4 written 4 truncated 0\n"
        )
        return run_view(directory, grant_path)

    def test_accepts_byte_only_encoding_change(self):
        result = validate(
            self.spec,
            self.make_run("legacy", 94),
            self.make_run("compact", 60),
        )
        self.assertEqual(result["status"], "passed")
        self.assertAlmostEqual(result["serialized_byte_reduction_percent"],
                               36.170212765957444)
        self.assertTrue(result["semantic_grant_events_equal"])
        self.assertTrue(result["fct_rows_equal"])

    def test_rejects_semantic_rate_change(self):
        with self.assertRaisesRegex(ValueError, "grant event sequence"):
            validate(
                self.spec,
                self.make_run("legacy", 94),
                self.make_run("compact", 60, mutate_rate=True),
            )

    def test_rejects_wrong_serialized_size(self):
        with self.assertRaisesRegex(ValueError, "grant size mismatch"):
            validate(
                self.spec,
                self.make_run("legacy", 94),
                self.make_run("compact", 64),
            )


if __name__ == "__main__":
    unittest.main()
