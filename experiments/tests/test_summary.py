import tempfile
import unittest
from pathlib import Path

from experiments.summarize_campaign import (
    confidence_interval,
    parse_guard_stats,
    parse_pfc,
    parse_port_queue_summaries,
    paired_rows,
    percentile,
    queue_summary_metrics,
    validate_mode,
)


def metric_row(cc, seed, value, flow_hash="same"):
    return {
        "row_type": "run", "comparison": "", "stage": "components",
        "cc": cc, "topo": "topo", "cdf": "cdf", "netload": 40,
        "simul_time": 0.02, "bw": 100, "pfc": 1, "irn": 0,
        "guard_lambda": 1.0, "guard_beta": 0.125, "guard_gamma": 1.0,
        "guard_selective_registration": 1, "guard_proactive_release": 1,
        "guard_keep_last_hop_int": 0, "seed": seed,
        "metric": "fct_slowdown_mean",
        "category": "all", "value": value, "n": 100, "mean": "",
        "ci95_low": "", "ci95_high": "", "flow_sha256": flow_hash,
        "run_key": f"{cc}-{seed}",
    }


class SummaryTests(unittest.TestCase):
    def test_extended_guard_stats_include_recovery_drops_and_pfc_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stats.txt"
            path.write_text(
                "node_id grants_sent grants_received hpcc_feedback_updates max_active_flows "
                "recovery_nacks_generated recovery_nacks_received irn_nacks_generated "
                "irn_nacks_received irn_retransmit_packets irn_retransmit_bytes timeout_recoveries\n"
                "total 10 10 20 3 4 5 6 7 8 9000 9\n"
                "switch_drops ingress 2 egress 3 total 5\n"
                "pfc_priority qindex pause_count resume_count matched_intervals "
                "cumulative_pause_ns max_pause_ns unmatched_pauses unmatched_resumes\n"
                "pfc_priority 3 4 4 4 1000 400 0 0\n",
                encoding="utf-8",
            )
            stats = parse_guard_stats(path)
            self.assertEqual(stats["irn_retransmit_bytes"], 9000)
            self.assertEqual(stats["switch_drops_total"], 5)
            self.assertEqual(stats["pfc_priority"][3]["cumulative_pause_ns"], 1000)
            self.assertEqual(stats["pfc_max_pause_ns"], 400)

    def test_extended_pfc_trace_skips_header_and_keeps_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pfc.txt"
            path.write_text(
                "# time_ns node_id node_type if_index q_index event advertised_pause_us\n"
                "10 1 0 2 3 1 5\n20 1 0 2 3 0 0\n",
                encoding="utf-8",
            )
            stats = parse_pfc(path)
            self.assertEqual(stats["pfc_pause_events"], 1)
            self.assertEqual(stats["pfc_resume_events"], 1)
            self.assertEqual(stats["pfc_event_priority"][3]["pause_count"], 1)

    def test_oflm_stats_format_preserves_lifecycle_counters(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stats.txt"
            path.write_text(
                "total 10 10 20 30 25 22 8 4 1 1 2 2 3 3000 4\n",
                encoding="utf-8",
            )
            stats = parse_guard_stats(path)
            self.assertEqual(stats["registrations"], 30)
            self.assertEqual(stats["selected_registrations"], 25)
            self.assertEqual(stats["proactive_releases"], 22)
            self.assertEqual(stats["completion_releases"], 8)
            self.assertEqual(stats["max_active_flows"], 4)

    def test_guard_stats_preserve_serialized_grant_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stats.txt"
            values = list(range(1, 31))
            path.write_text(
                "total " + " ".join(map(str, values)) + "\n",
                encoding="utf-8",
            )
            stats = parse_guard_stats(path)
            self.assertEqual(stats["grants_sent"], 1)
            self.assertEqual(stats["int_records_stripped"], 29)
            self.assertEqual(stats["grant_bytes_sent"], 30)

    def test_homa_stats_preserve_lifecycle_and_priority_counters(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stats.txt"
            path.write_text(
                "total 0 0 0 0\n"
                "homa_total 100 90000 3 40 40 2 2 7 7 8 7 4\n"
                "homa_priority priority data_packets data_bytes\n"
                "homa_priority 1 20 18000\n"
                "homa_priority 7 80 72000\n",
                encoding="utf-8",
            )
            stats = parse_guard_stats(path)
            self.assertEqual(stats["homa_data_packets"], 100)
            self.assertEqual(stats["homa_messages_completed"], 7)
            self.assertEqual(stats["homa_priority"][7]["data_bytes"], 72000)

    def test_adaptive_guard_stats_preserve_rebalance_counters(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stats.txt"
            values = list(range(1, 33))
            path.write_text(
                "total " + " ".join(map(str, values)) + "\n",
                encoding="utf-8",
            )
            stats = parse_guard_stats(path)
            self.assertEqual(stats["grant_bytes_sent"], 30)
            self.assertEqual(stats["guard_rebalance_events"], 31)
            self.assertEqual(stats["guard_adaptive_grant_updates"], 32)

    def test_guard_sender_scheduler_stats_are_bounded_counters(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stats.txt"
            path.write_text(
                "total 0 0 0 0\n"
                "guard_sender_scheduler enabled 1 quantum_packets 64 "
                "selections 1000 non_rr 25 forced_rr 3\n",
                encoding="utf-8",
            )
            stats = parse_guard_stats(path)
            self.assertEqual(stats["guard_srpt_quantum_packets"], 64)
            self.assertEqual(stats["guard_sender_srpt_selections"], 1000)
            self.assertEqual(stats["guard_sender_srpt_non_rr"], 25)
            self.assertEqual(stats["guard_sender_srpt_forced_rr"], 3)

    def test_queue_summary_is_preaggregated_and_includes_zero_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "queue.txt"
            path.write_text(
                "samples 100\naverage_bytes 12.5\np95_bytes 100\n"
                "p99_bytes 200\nmax_bytes 300\n",
                encoding="utf-8",
            )
            metrics = dict((metric, value) for metric, _category, value, _n
                           in queue_summary_metrics(path))
            self.assertEqual(metrics["queue_sample_count"], 100)
            self.assertEqual(metrics["queue_bytes_mean"], 12.5)
            self.assertEqual(metrics["queue_bytes_max"], 300)

    def test_queue_summary_ignores_bounded_port_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "queue.txt"
            path.write_text(
                "samples 100\naverage_bytes 12.5\np95_bytes 100\n"
                "p99_bytes 200\nmax_bytes 300\n"
                "port node_id if_index neighbor_id samples positive_samples average_bytes "
                "positive_average_bytes p95_bytes p99_bytes max_bytes tx_bytes\n"
                "port 16 1 0 10 2 20 100 0 30 40 5000\n",
                encoding="utf-8",
            )
            metrics = dict((metric, value) for metric, _category, value, _n
                           in queue_summary_metrics(path))
            self.assertEqual(metrics["queue_bytes_mean"], 12.5)
            self.assertEqual(metrics["queue_bytes_max"], 300)
            ports = parse_port_queue_summaries(path)
            self.assertEqual(len(ports), 1)
            self.assertEqual(ports[0]["neighbor_id"], 0)
            self.assertEqual(ports[0]["p99_bytes"], 30)

    def test_t_interval_is_computed_across_seed_values(self):
        mean, low, high = confidence_interval([1, 2, 3, 4, 5])
        self.assertEqual(mean, 3)
        self.assertAlmostEqual(low, 1.0370716, places=6)
        self.assertAlmostEqual(high, 4.9629284, places=6)

    def test_linear_percentile(self):
        self.assertEqual(percentile([1, 2, 3], 50), 2)
        self.assertAlmostEqual(percentile([1, 2, 3, 4], 95), 3.85)

    def test_full_guard_mode_requires_both_loops(self):
        params = {"cc": "guard"}
        config = {"CC_MODE": "11"}
        good = {
            "grants_sent": 1, "grants_received": 1, "hpcc_feedback_updates": 1,
            "hpcc_valid_feedback": 1, "hpcc_rate_updates_applied": 1,
            "hpcc_actual_rate_changes": 1,
        }
        bad = dict(good, hpcc_feedback_updates=0)
        self.assertEqual(validate_mode(params, config, good), [])
        self.assertIn("valid-hop HPCC rate updates", validate_mode(params, config, bad)[0])

    def test_full_guard_rejects_counter_calls_without_int_hops(self):
        params = {"cc": "guard"}
        config = {"CC_MODE": "11"}
        stats = {
            "grants_sent": 1, "grants_received": 1, "hpcc_feedback_updates": 10,
            "hpcc_valid_feedback": 0, "hpcc_rate_updates_applied": 0,
            "hpcc_actual_rate_changes": 0,
        }
        self.assertIn("valid-hop HPCC rate updates", validate_mode(params, config, stats)[0])

    def test_component_switches_are_checked_against_config(self):
        params = {
            "cc": "guard", "guard_selective_registration": 0,
            "guard_proactive_release": 1,
        }
        config = {
            "CC_MODE": "11", "GUARD_SELECTIVE_REGISTRATION": "1",
            "GUARD_PROACTIVE_RELEASE": "1",
        }
        stats = {
            "grants_sent": 1, "grants_received": 1, "hpcc_feedback_updates": 1,
            "hpcc_valid_feedback": 1, "hpcc_rate_updates_applied": 1,
            "hpcc_actual_rate_changes": 1,
        }
        errors = validate_mode(params, config, stats)
        self.assertIn("GUARD_SELECTIVE_REGISTRATION is 1, expected 0", errors)

    def test_paired_difference_preserves_all_seeds(self):
        rows = []
        for seed in range(1, 6):
            rows.extend([metric_row("guard", seed, seed + 1), metric_row("hpcc", seed, seed)])
        result, errors = paired_rows(rows, [{
            "name": "guard_vs_hpcc", "stages": ["components"],
            "vary": "cc", "a": "guard", "b": "hpcc",
        }])
        self.assertEqual(errors, [])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["n"], 5)
        self.assertEqual(result[0]["mean"], 1)

    def test_paired_difference_rejects_mismatched_traffic(self):
        rows = [metric_row("guard", 1, 2, "a"), metric_row("hpcc", 1, 1, "b")]
        result, errors = paired_rows(rows, [{
            "name": "guard_vs_hpcc", "stages": ["components"],
            "vary": "cc", "a": "guard", "b": "hpcc",
        }])
        self.assertEqual(result, [])
        self.assertIn("flow hash mismatch", errors[0])

    def test_paired_difference_skips_absent_campaign_arm(self):
        result, errors = paired_rows([metric_row("guard", 1, 2)], [{
            "name": "partial", "stages": ["components"],
            "vary": "cc", "a": "guard", "b": "hpcc",
        }])
        self.assertEqual(result, [])
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
