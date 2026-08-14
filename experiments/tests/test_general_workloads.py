import csv
import json
from pathlib import Path
import tempfile
import unittest

from experiments.run_general_workloads import (
    CampaignError,
    choose_profile,
    inspect_flow_file,
    load_admission,
    planned_runs,
    read_spec,
    run_command,
)
from experiments.summarize_general_workloads import (
    AnalysisError,
    aggregate_formal,
    export_portable,
    fct_metrics,
    flow_scope,
    mechanism_checks,
    homa_completion_checks,
    parse_controller,
)


class GeneralWorkloadRunnerTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(__file__).resolve().parents[2]
        self.spec = read_spec(
            self.repo / "experiments/campaigns/general_workloads_formal.json"
        )

    def test_frozen_spec_has_pg3_size_priority_off_and_hard_cap(self):
        self.assertEqual(self.spec["limits"]["max_flows"], 10000)
        self.assertEqual(self.spec["defaults"]["priority_group"], 3)
        self.assertEqual(self.spec["arms"]["full"]["guard_size_priority"], 0)
        self.assertEqual(self.spec["arms"]["receiver"]["guard_size_priority"], 0)
        google = next(row for row in self.spec["workloads"] if row["name"] == "GoogleRPC")
        self.assertEqual(google["fallback"]["topo"], "leaf_spine_8_100G_OS1")
        self.assertEqual(google["fallback"]["simul_time"], 0.01)
        self.assertEqual(google["fallback"]["netload"], 20)

    def test_flow_inspection_enforces_pg3_endpoints_and_interval(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flow.txt"
            path.write_text("2\n0 1 3 1000 2.000001\n1 0 3 2000 2.019999\n")
            metadata = inspect_flow_file(path, hosts=2, duration=0.02, expected_pg=3)
            self.assertEqual(metadata["flow_count"], 2)
            self.assertEqual(metadata["total_flow_bytes"], 3000)
            path.write_text("1\n0 1 4 1000 2.000001\n")
            with self.assertRaisesRegex(CampaignError, "expected PG3"):
                inspect_flow_file(path, hosts=2, duration=0.02, expected_pg=3)

    def test_only_single_predeclared_fallback_can_rescue_flow_cap(self):
        def traces(*counts):
            return [{"flow_count": count} for count in counts]

        decision, _ = choose_profile(traces(1, 2, 3, 4, 5), None, 10)
        self.assertEqual(decision, "primary")
        decision, _ = choose_profile(
            traces(11, 2, 3, 4, 5), traces(6, 7, 8, 9, 10), 10
        )
        self.assertEqual(decision, "fallback")
        decision, _ = choose_profile(
            traces(11, 2, 3, 4, 5), traces(6, 7, 18, 9, 10), 10
        )
        self.assertEqual(decision, "excluded")

    def test_matched_commands_reuse_flow_and_bound_controller_trace(self):
        workload = {
            "name": "AliStorage2019", "cdf": "AliStorage2019",
            "selected_profile": "primary",
            "attempts": {"primary": {"profile": {
                "topo": "leaf_spine_16_100G_OS4", "hosts": 16,
                "oversubscription": 4, "simul_time": 0.02,
                "netload": 40, "bw": 100,
            }}},
        }
        trace = {"seed": 1, "path": "/tmp/frozen-flow.txt", "sha256": "same"}
        commands = {
            arm: run_command(self.repo, self.spec, workload, trace, arm, True)
            for arm in ("full", "hpcc", "receiver")
        }
        for command in commands.values():
            self.assertEqual(command[:2], ["env", "PYENV_VERSION=2.7.18"])
            self.assertEqual(command[command.index("--flow_file") + 1], trace["path"])
            self.assertIn("bulk", command)
            self.assertIn("10000", command)
        for arm in ("full", "receiver"):
            command = commands[arm]
            self.assertEqual(command[command.index("--guard_size_priority") + 1], "0")
        self.assertNotIn("--guard_size_priority", commands["hpcc"])
        self.assertIn("--guard_controller_trace", commands["full"])
        self.assertNotIn("--guard_controller_trace", commands["hpcc"])
        self.assertNotIn("--guard_controller_trace", commands["receiver"])

    def test_guard_homa_spec_freezes_protocol_native_schedulers(self):
        spec = read_spec(
            self.repo / "experiments/campaigns/guard_homa_general_formal.json"
        )
        workload = {
            "name": "AliStorage2019", "cdf": "AliStorage2019",
            "selected_profile": "primary",
            "attempts": {"primary": {"profile": {
                "topo": "leaf_spine_16_100G_OS4", "hosts": 16,
                "oversubscription": 4, "simul_time": 0.02,
                "netload": 40, "bw": 100,
            }}},
        }
        trace = {"seed": 1, "path": "/tmp/frozen-flow.txt", "sha256": "same"}
        commands = {
            arm: run_command(self.repo, spec, workload, trace, arm, True)
            for arm in ("guard", "hpcc", "homa")
        }
        guard = commands["guard"]
        self.assertEqual(guard[guard.index("--guard_lambda") + 1], "1.4")
        self.assertEqual(guard[guard.index("--guard_size_priority") + 1], "1")
        self.assertEqual(guard[guard.index("--guard_sender_srpt") + 1], "1")
        self.assertEqual(guard[guard.index("--guard_srpt_quantum_packets") + 1], "64")
        self.assertIn("--guard_controller_trace", guard)
        self.assertEqual(commands["homa"][commands["homa"].index("--cc") + 1], "homa")
        self.assertNotIn("--guard_controller_trace", commands["homa"])

    def test_optimized_holdout_freezes_new_controls_and_seeds(self):
        spec = read_spec(
            self.repo / "experiments/campaigns/guard_homa_optimized_holdout.json"
        )
        self.assertEqual(spec["seeds"], [6, 7, 8, 9, 10])
        workload = {
            "name": "FbHdp", "cdf": "FbHdp2015",
            "selected_profile": "primary",
            "attempts": {"primary": {"profile": {
                "topo": "leaf_spine_16_100G_OS4", "hosts": 16,
                "oversubscription": 4, "simul_time": 0.02,
                "netload": 40, "bw": 100,
            }}},
        }
        trace = {"seed": 6, "path": "/tmp/holdout-flow.txt", "sha256": "same"}
        command = run_command(self.repo, spec, workload, trace, "guard", True)
        expected = {
            "--guard_lambda": "1.8", "--guard_tail_bypass_bdps": "8.0",
            "--guard_min_share_fraction": "0.0",
            "--guard_remaining_exponent": "1.0",
            "--guard_grant_refresh_bdps": "1.0",
            "--guard_srpt_quantum_packets": "64",
        }
        for option, value in expected.items():
            self.assertEqual(command[command.index(option) + 1], value)

        preflight = {"workloads": [{
            **workload, "decision": "included",
            "selected_traces": [{"seed": seed} for seed in spec["seeds"]],
        }]}
        admission = planned_runs(spec, preflight, "admission")
        formal = planned_runs(spec, preflight, "formal", {"FbHdp": {"passed": True}})
        self.assertEqual({row[1]["seed"] for row in admission}, {6})
        self.assertEqual({row[1]["seed"] for row in formal}, {7, 8, 9, 10})

    def test_final_holdout_uses_fresh_seeds_without_share_reclamation(self):
        spec = read_spec(
            self.repo / "experiments/campaigns/guard_homa_final_holdout.json"
        )
        self.assertEqual(spec["seeds"], [16, 17, 18, 19, 20])
        self.assertEqual(spec["admission"]["seed"], 16)
        self.assertEqual(spec["arms"]["guard"]["guard_work_conserving"], 0)
        self.assertEqual(spec["defaults"]["guard_lambda"], 1.8)

    def test_final_load_figures_freeze_optimized_guard(self):
        for load in (30, 50):
            spec = read_spec(
                self.repo / f"experiments/campaigns/ali_load{load}_final_figures.json"
            )
            self.assertEqual(spec["seeds"], [21, 22, 23, 24, 25])
            self.assertEqual(spec["defaults"]["netload"], load)
            self.assertEqual(spec["defaults"]["guard_lambda"], 1.8)
            self.assertEqual(spec["arms"]["guard"]["guard_work_conserving"], 0)

    def test_elephant_holdout_freezes_selected_threshold_and_fresh_seeds(self):
        spec = read_spec(
            self.repo / "experiments/campaigns/guard_homa_elephant_holdout.json"
        )
        self.assertEqual(spec["seeds"], [66, 67, 68, 69, 70])
        self.assertEqual(spec["defaults"]["guard_receiver_concurrency"], 1)
        self.assertEqual(spec["defaults"]["guard_concurrency_min_bdps"], 12.0)
        workload = {
            "name": "AliStorage50", "cdf": "AliStorage2019",
            "selected_profile": "primary",
            "attempts": {"primary": {"profile": {
                "topo": "leaf_spine_8_100G_OS2", "hosts": 8,
                "oversubscription": 2, "simul_time": 0.01,
                "netload": 50, "bw": 100,
            }}},
        }
        trace = {"seed": 66, "path": "/tmp/holdout-flow.txt", "sha256": "same"}
        command = run_command(self.repo, spec, workload, trace, "guard", True)
        self.assertEqual(command[command.index("--guard_receiver_concurrency") + 1], "1")
        self.assertEqual(command[command.index("--guard_concurrency_min_bdps") + 1], "12.0")

    def test_elephant_generalization_freezes_three_workloads(self):
        spec = read_spec(
            self.repo / "experiments/campaigns/guard_homa_elephant_generalization.json"
        )
        self.assertEqual(spec["seeds"], [71, 72, 73, 74, 75])
        self.assertEqual(
            [workload["name"] for workload in spec["workloads"]],
            ["AliStorage2019", "WebSearch", "FbHdp"],
        )
        self.assertEqual(spec["defaults"]["guard_receiver_concurrency"], 1)
        self.assertEqual(spec["defaults"]["guard_concurrency_min_bdps"], 12.0)

    def test_formal_plan_requires_passing_workload_and_excludes_seed1(self):
        selected = []
        for name in ("AliStorage2019", "WebSearch"):
            selected.append({
                "name": name, "decision": "included", "selected_profile": "primary",
                "selected_traces": [{"seed": seed} for seed in range(1, 6)],
            })
        preflight = {"workloads": [
            *selected,
            {"name": "GoogleRPC", "decision": "excluded", "selected_traces": []},
        ]}
        plans = planned_runs(
            self.spec, preflight, "formal",
            {"AliStorage2019": {"passed": True}, "WebSearch": {"passed": False}},
        )
        self.assertEqual(len(plans), 12)
        self.assertEqual({plan[0]["name"] for plan in plans}, {"AliStorage2019"})
        self.assertEqual({plan[1]["seed"] for plan in plans}, {2, 3, 4, 5})

    def test_formal_phase_rejects_stale_admission(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "summary").mkdir()
            (root / "preflight.json").write_text("{}\n")
            (root / "summary/admission.json").write_text(json.dumps({
                "preflight_sha256": "stale", "workloads": {},
            }))
            with self.assertRaisesRegex(CampaignError, "changed after admission"):
                load_admission(root, {"workloads": []})


class GeneralWorkloadSummaryTests(unittest.TestCase):
    def setUp(self):
        self.buckets = [
            {"name": "le_8KB", "max_bytes": 8192},
            {"name": "8KB_to_1BDP", "min_exclusive_bytes": 8192,
             "max_bytes": 104000},
            {"name": "1BDP_to_1MB", "min_exclusive_bytes": 104000,
             "max_bytes": 1048576},
            {"name": "gt_1MB", "min_exclusive_bytes": 1048576},
        ]

    def test_size_buckets_and_fct_statistics_cover_boundaries(self):
        self.assertEqual(flow_scope(8192, self.buckets), "le_8KB")
        self.assertEqual(flow_scope(104000, self.buckets), "8KB_to_1BDP")
        self.assertEqual(flow_scope(1048576, self.buckets), "1BDP_to_1MB")
        self.assertEqual(flow_scope(1048577, self.buckets), "gt_1MB")
        rows = [
            {"size": 1000, "fct_us": 2.0, "slowdown": 1.0},
            {"size": 1000, "fct_us": 4.0, "slowdown": 3.0},
            {"size": 2 * 1024 * 1024, "fct_us": 20.0, "slowdown": 4.0},
        ]
        metrics = fct_metrics(rows, self.buckets)
        self.assertEqual(metrics["overall_fct_us_mean"], 26.0 / 3.0)
        self.assertEqual(metrics["le_8KB_slowdown_mean"], 2.0)
        self.assertEqual(metrics["gt_1MB_slowdown_p99"], 4.0)
        self.assertEqual(metrics["long_to_small_slowdown_mean_ratio"], 2.0)

    def test_mechanism_checks_distinguish_three_arms(self):
        base = {
            "grants_sent": 0, "grants_received": 0,
            "hpcc_feedback_updates": 0, "hpcc_valid_feedback": 0,
            "hpcc_rate_updates_applied": 0, "hpcc_full_computations": 0,
            "hpcc_fast_computations": 0, "hpcc_actual_rate_changes": 0,
            "reactive_binding_updates": 0,
        }
        full = dict(base, grants_sent=1, hpcc_valid_feedback=1,
                    hpcc_actual_rate_changes=1, reactive_binding_updates=1)
        hpcc = dict(base, hpcc_valid_feedback=1, hpcc_actual_rate_changes=1)
        receiver = dict(base, grants_sent=1)
        self.assertTrue(all(mechanism_checks("full", full).values()))
        self.assertTrue(all(mechanism_checks("hpcc", hpcc).values()))
        self.assertTrue(all(mechanism_checks("receiver", receiver).values()))
        receiver["hpcc_actual_rate_changes"] = 1
        self.assertFalse(mechanism_checks("receiver", receiver)["receiver_zero_hpcc"])
        homa = dict(
            base,
            homa_data_packets=100, homa_grants_sent=10, homa_grants_received=10,
            homa_messages_tracked=4, homa_messages_completed=4,
            homa_completion_notices_sent=4, homa_completion_notices_received=4,
            homa_max_pending_messages=2,
            homa_priority={1: {"data_packets": 20}, 7: {"data_packets": 80}},
        )
        self.assertTrue(all(mechanism_checks("homa", homa).values()))

    def test_homa_completion_replays_close_exactly(self):
        stats = {
            "homa_messages_tracked": 4,
            "homa_messages_completed": 4,
            "homa_completed_message_ids": 4,
            "homa_completion_notices_received": 4,
            "homa_completion_notices_sent": 6,
            "homa_duplicate_data_after_completion": 2,
            "homa_completion_notices_replayed": 2,
        }
        self.assertTrue(all(homa_completion_checks(stats, 4).values()))
        stats["homa_completion_notices_replayed"] = 1
        self.assertFalse(
            homa_completion_checks(stats, 4)["homa_completion_replays_close"]
        )

    def test_controller_trace_is_bounded_and_bucketed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "controller.csv"
            fields = ("time_ns", "flow_id", "event_type", "binding", "fast_react")
            with path.open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerows([
                    {"time_ns": 1, "flow_id": 0, "event_type": "hpcc",
                     "binding": "reactive", "fast_react": 0},
                    {"time_ns": 2, "flow_id": 0, "event_type": "complete",
                     "binding": "reactive", "fast_react": 0},
                    {"time_ns": 1, "flow_id": 1, "event_type": "grant",
                     "binding": "grant", "fast_react": 0},
                ])
                stream.write("# attempted 3 written 3 truncated 0\n")
            footer, metrics = parse_controller(
                path, {0: 1000, 1: 2 * 1024 * 1024}, self.buckets, 3
            )
            self.assertEqual(footer["attempted"], 3)
            self.assertEqual(metrics["controller_le_8KB_reactive_event_share"], 1.0)
            self.assertEqual(metrics["controller_gt_1MB_grant_event_share"], 1.0)
            with self.assertRaisesRegex(AnalysisError, "line bound"):
                parse_controller(path, {0: 1000, 1: 2 * 1024 * 1024}, self.buckets, 2)

    def test_paired_t95_uses_five_hash_matched_seeds(self):
        rows = []
        for seed in range(1, 6):
            for arm, value in (("full", seed + 1.0), ("hpcc", float(seed)),
                               ("receiver", seed + 2.0)):
                rows.append({
                    "workload": "AliStorage2019", "cdf": "AliStorage2019",
                    "seed": seed, "arm": arm, "git_sha": "sha",
                    "flow_sha256": f"flow-{seed}", "output_id": f"{arm}-{seed}",
                    "output_dir": "/tmp", "passed": True, "failures": [],
                    "overall_slowdown_mean": value,
                })
        report, _csv = aggregate_formal(
            rows, (("full", "hpcc"), ("full", "receiver"))
        )
        paired = report["AliStorage2019"]["paired"]["full_minus_hpcc"]
        self.assertEqual(paired["overall_slowdown_mean"]["difference"]["n"], 5)
        self.assertEqual(paired["overall_slowdown_mean"]["difference"]["mean"], 1.0)
        rows[-1]["flow_sha256"] = "wrong"
        with self.assertRaisesRegex(AnalysisError, "flow hash mismatch"):
            aggregate_formal(rows, (("receiver", "hpcc"),))

    def test_paired_t95_accepts_a_frozen_held_out_seed_cohort(self):
        rows = []
        for seed in range(6, 11):
            for arm, value in (("guard", seed - 1.0), ("hpcc", float(seed)),
                               ("homa", seed + 1.0)):
                rows.append({
                    "workload": "WebSearch", "cdf": "WebSearch",
                    "seed": seed, "arm": arm, "git_sha": "sha",
                    "flow_sha256": f"flow-{seed}", "output_id": f"{arm}-{seed}",
                    "output_dir": "/tmp", "passed": True, "failures": [],
                    "overall_slowdown_mean": value,
                })
        report, _csv = aggregate_formal(
            rows, (("guard", "homa"), ("guard", "hpcc")),
            ("guard", "hpcc", "homa"), tuple(range(6, 11)),
        )
        paired = report["WebSearch"]["paired"]["guard_minus_homa"]
        self.assertEqual(paired["overall_slowdown_mean"]["difference"]["n"], 5)
        self.assertEqual(paired["overall_slowdown_mean"]["difference"]["mean"], -2.0)

    def test_portable_export_rewrites_raw_output_locators_and_hashes_files(self):
        with tempfile.TemporaryDirectory() as directory:
            export = Path(directory) / "portable"
            rows = [{
                "workload": "WebSearch", "arm": "guard", "seed": 6,
                "git_sha": "abc", "output_id": "123",
                "output_dir": "/tmp/private/mix/output/123",
            }]
            export_portable(
                export,
                {"seeds": [6, 7, 8, 9, 10], "arms": {"guard": {}}},
                {"schema_version": 1}, {"all_selected_passed": True},
                rows, [{"analysis": "arm_mean", "value": 1.0}],
            )
            with (export / "run_registry.csv").open(encoding="utf-8") as stream:
                registry = list(csv.DictReader(stream))
            self.assertEqual(registry[0]["output_dir"], "mix/output/123")
            manifest = json.loads((export / "manifest.json").read_text())
            self.assertEqual(manifest["simulator_git_shas"], ["abc"])
            self.assertFalse(manifest["raw_outputs_in_git"])
            self.assertIn("metrics.csv", manifest["files"])


if __name__ == "__main__":
    unittest.main()
