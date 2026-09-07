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
    validate_config,
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

    def test_absent_opt_in_fail_closed_flag_means_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = (Path(directory) / "flow.txt").resolve()
            snapshot.write_text("0\n")
            manifest = {
                "seed": 160,
                "output_dir": str(Path(directory).resolve()),
            }
            spec = {
                "defaults": {"guard_transition_prefix_fail_closed": 0},
                "arms": {"guard": {"cc": "guard"}},
            }
            config = {
                "CC_MODE": "11",
                "ENABLE_PFC": "1",
                "ENABLE_IRN": "0",
                "RANDOM_SEED": "160",
                "PREFLIGHT_MAX_FLOWS": "10000",
                "MONITOR_PROFILE": "bulk",
                "GUARD_CONTROLLER_TRACE": "0",
                "FLOW_FILE": str(snapshot),
            }
            self.assertEqual(
                validate_config(config, manifest, "guard", False, snapshot, spec),
                [],
            )
            config["GUARD_TRANSITION_PREFIX_FAIL_CLOSED"] = "1"
            self.assertIn(
                "GUARD_TRANSITION_PREFIX_FAIL_CLOSED=1, expected 0",
                validate_config(config, manifest, "guard", False, snapshot, spec),
            )

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

    def test_v17_development_profile_passes_complete_safety_bundle(self):
        spec = read_spec(
            self.repo / "experiments/campaigns/guard_v17_ali50_development.json"
        )
        self.assertTrue(spec["development_only"])
        self.assertEqual(spec["seeds"], [155, 156, 157, 158, 159])
        workload = {
            "name": "AliStorage50", "cdf": "AliStorage2019",
            "selected_profile": "primary",
            "attempts": {"primary": {"profile": {
                "topo": "leaf_spine_8_100G_OS2", "hosts": 8,
                "oversubscription": 2, "simul_time": 0.01,
                "netload": 50, "bw": 100,
            }}},
        }
        trace = {"seed": 155, "path": "/tmp/v17-flow.txt", "sha256": "same"}
        command = run_command(self.repo, spec, workload, trace, "guard", True)
        exact = {
            "--guard_membership_coalesce_ns": "12480",
            "--guard_initial_collection_quiet_ns": "16640",
            "--guard_small_set_fastpath_limit": "4",
            "--guard_transition_prefix_barrier": "1",
            "--guard_transition_prefix_wire_watchdog": "1",
            "--guard_transition_prefix_fail_closed": "0",
            "--guard_transition_prefix_ack_clock_fallback": "1",
            "--guard_mixed_pg_vector_fastpath": "1",
            "--guard_serialized_progress_refresh": "1",
            "--guard_serialized_draining": "1",
            "--guard_grant_reliability_rtts": "2.0",
        }
        for option, value in exact.items():
            self.assertEqual(command[command.index(option) + 1], value)
        self.assertNotIn("--guard_controller_trace", command)

    def test_v17_profile_rejects_partial_safety_bundle(self):
        path = self.repo / "experiments/campaigns/guard_v17_ali50_development.json"
        value = json.loads(path.read_text())
        del value["defaults"]["guard_serialized_draining"]
        with tempfile.TemporaryDirectory() as directory:
            partial = Path(directory) / "partial.json"
            partial.write_text(json.dumps(value))
            with self.assertRaisesRegex(CampaignError, "complete safety bundle"):
                read_spec(partial)

    def test_v18_freezes_capacity_admission_and_fresh_seeds(self):
        spec = read_spec(
            self.repo / "experiments/campaigns/guard_v18_ali50_development.json"
        )
        self.assertEqual(spec["mechanism_profile"], "V18")
        self.assertEqual(spec["seeds"], [160, 161, 162, 163, 164])
        workload = {
            "name": "AliStorage50", "cdf": "AliStorage2019",
            "selected_profile": "primary",
            "attempts": {"primary": {"profile": {
                "topo": "leaf_spine_8_100G_OS2", "hosts": 8,
                "oversubscription": 2, "simul_time": 0.01,
                "netload": 50, "bw": 100,
            }}},
        }
        trace = {"seed": 160, "path": "/tmp/v18-flow.txt", "sha256": "same"}
        command = run_command(self.repo, spec, workload, trace, "guard", True)
        option = "--guard_capacity_admission_deferral"
        self.assertEqual(command[command.index(option) + 1], "1")

    def test_v19_freezes_canonical_bounded_elephant_service(self):
        spec = read_spec(
            self.repo / "experiments/campaigns/guard_v19_ali50_development.json"
        )
        self.assertEqual(spec["mechanism_profile"], "V19")
        self.assertEqual(spec["seeds"], [165, 166, 167, 168, 169])
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
        trace = {"seed": 165, "path": "/tmp/v19-flow.txt", "sha256": "same"}
        command = run_command(self.repo, spec, workload, trace, "guard", True)
        for option, value in (
                ("--guard_receiver_concurrency", "1"),
                ("--guard_concurrency_min_bdps", "12.0"),
                ("--guard_capacity_admission_deferral", "1")):
            self.assertEqual(command[command.index(option) + 1], value)

    def test_v20_freezes_matched_k1_k2_pair(self):
        spec = read_spec(
            self.repo / "experiments/campaigns/guard_v20_elephant_k_development.json"
        )
        self.assertEqual(spec["mechanism_profile"], "V20")
        self.assertEqual(spec["seeds"], [170, 171, 172, 173, 174])
        self.assertEqual(set(spec["arms"]), {"guard_k1", "guard_k2"})
        workload = {
            "name": "AliStorage50", "cdf": "AliStorage2019",
            "selected_profile": "primary",
            "attempts": {"primary": {"profile": {
                "topo": "leaf_spine_8_100G_OS2", "hosts": 8,
                "oversubscription": 2, "simul_time": 0.01,
                "netload": 50, "bw": 100,
            }}},
        }
        trace = {"seed": 170, "path": "/tmp/v20-flow.txt", "sha256": "same"}
        for arm, expected in (("guard_k1", "1"), ("guard_k2", "2")):
            command = run_command(self.repo, spec, workload, trace, arm, True)
            option = "--guard_receiver_concurrency"
            self.assertEqual(command[command.index(option) + 1], expected)
            self.assertEqual(command[command.index("--guard_concurrency_min_bdps") + 1],
                             "12.0")

    def test_v20_rejects_a_second_changed_control(self):
        path = self.repo / "experiments/campaigns/guard_v20_elephant_k_development.json"
        value = json.loads(path.read_text())
        value["arms"]["guard_k2"]["guard_lambda"] = 2.0
        with tempfile.TemporaryDirectory() as directory:
            modified = Path(directory) / "v20.json"
            modified.write_text(json.dumps(value))
            with self.assertRaisesRegex(CampaignError, "changes controls other than K"):
                read_spec(modified)

    def test_v21_freezes_sublinear_adaptive_pair(self):
        spec = read_spec(
            self.repo /
            "experiments/campaigns/guard_v21_adaptive_elephant_development.json"
        )
        self.assertEqual(spec["mechanism_profile"], "V21")
        self.assertEqual(spec["seeds"], [175, 176, 177, 178, 179])
        self.assertEqual(set(spec["arms"]), {"guard_k1", "guard_adaptive"})
        workload = {
            "name": "AliStorage50", "cdf": "AliStorage2019",
            "selected_profile": "primary",
            "attempts": {"primary": {"profile": {
                "topo": "leaf_spine_8_100G_OS2", "hosts": 8,
                "oversubscription": 2, "simul_time": 0.01,
                "netload": 50, "bw": 100,
            }}},
        }
        trace = {"seed": 175, "path": "/tmp/v21-flow.txt", "sha256": "same"}
        expected = {
            "guard_k1": ("1", "0"),
            "guard_adaptive": ("2", "1"),
        }
        for arm, values in expected.items():
            command = run_command(self.repo, spec, workload, trace, arm, True)
            self.assertEqual(
                command[command.index("--guard_receiver_concurrency") + 1], values[0])
            self.assertEqual(
                command[command.index("--guard_adaptive_elephant_concurrency") + 1],
                values[1])

    def test_v21_rejects_an_extra_adaptive_arm_change(self):
        path = (self.repo /
                "experiments/campaigns/guard_v21_adaptive_elephant_development.json")
        value = json.loads(path.read_text())
        value["arms"]["guard_adaptive"]["guard_remaining_exponent"] = 2.0
        with tempfile.TemporaryDirectory() as directory:
            modified = Path(directory) / "v21.json"
            modified.write_text(json.dumps(value))
            with self.assertRaisesRegex(CampaignError, "other than adaptive K"):
                read_spec(modified)

    def test_v22_freezes_fixed_k_aging_pair(self):
        spec = read_spec(
            self.repo /
            "experiments/campaigns/guard_v22_elephant_aging_development.json"
        )
        self.assertEqual(spec["mechanism_profile"], "V22")
        self.assertEqual(spec["seeds"], [180, 181, 182, 183, 184])
        self.assertEqual(set(spec["arms"]), {"guard_k1", "guard_aging"})
        workload = {
            "name": "AliStorage50", "cdf": "AliStorage2019",
            "selected_profile": "primary",
            "attempts": {"primary": {"profile": {
                "topo": "leaf_spine_8_100G_OS2", "hosts": 8,
                "oversubscription": 2, "simul_time": 0.01,
                "netload": 50, "bw": 100,
            }}},
        }
        trace = {"seed": 180, "path": "/tmp/v22-flow.txt", "sha256": "same"}
        for arm, wait in (("guard_k1", "0.0"), ("guard_aging", "2.0")):
            command = run_command(self.repo, spec, workload, trace, arm, True)
            self.assertEqual(
                command[command.index("--guard_receiver_concurrency") + 1], "1")
            self.assertEqual(
                command[command.index("--guard_elephant_aging_rtts") + 1], wait)

    def test_v22_rejects_an_extra_aging_arm_change(self):
        path = (self.repo /
                "experiments/campaigns/guard_v22_elephant_aging_development.json")
        value = json.loads(path.read_text())
        value["arms"]["guard_aging"]["guard_remaining_exponent"] = 2.0
        with tempfile.TemporaryDirectory() as directory:
            modified = Path(directory) / "v22.json"
            modified.write_text(json.dumps(value))
            with self.assertRaisesRegex(CampaignError, "other than aging"):
                read_spec(modified)

    def test_v24_freezes_fast_enter_slow_exit_pair(self):
        spec = read_spec(
            self.repo /
            "experiments/campaigns/guard_v24_fast_spillover_development.json"
        )
        self.assertEqual(spec["mechanism_profile"], "V24")
        self.assertEqual(spec["seeds"], [190, 191, 192, 193, 194])
        self.assertEqual(set(spec["arms"]), {"guard_k1", "guard_spillover"})
        self.assertEqual(spec["defaults"]["guard_elephant_spillover_enter_reports"], 1)
        self.assertEqual(spec["defaults"]["guard_elephant_spillover_exit_reports"], 2)
        workload = {
            "name": "AliStorage50", "cdf": "AliStorage2019",
            "selected_profile": "primary",
            "attempts": {"primary": {"profile": {
                "topo": "leaf_spine_8_100G_OS2", "hosts": 8,
                "oversubscription": 2, "simul_time": 0.01,
                "netload": 50, "bw": 100,
            }}},
        }
        trace = {"seed": 190, "path": "/tmp/v24-flow.txt", "sha256": "same"}
        for arm, enabled in (("guard_k1", "0"), ("guard_spillover", "1")):
            command = run_command(self.repo, spec, workload, trace, arm, True)
            self.assertEqual(
                command[command.index("--guard_elephant_cap_spillover") + 1],
                enabled)
            self.assertEqual(
                command[command.index("--guard_elephant_spillover_enter_reports") + 1],
                "1")
            self.assertEqual(
                command[command.index("--guard_elephant_spillover_exit_reports") + 1],
                "2")

    def test_v24_rejects_changed_hysteresis(self):
        path = (self.repo /
                "experiments/campaigns/guard_v24_fast_spillover_development.json")
        value = json.loads(path.read_text())
        value["defaults"]["guard_elephant_spillover_exit_reports"] = 3
        with tempfile.TemporaryDirectory() as directory:
            modified = Path(directory) / "v24.json"
            modified.write_text(json.dumps(value))
            with self.assertRaisesRegex(CampaignError, "enter/exit reports"):
                read_spec(modified)

    def test_v25_freezes_class_scoped_elephant_target(self):
        spec = read_spec(
            self.repo /
            "experiments/campaigns/guard_v25_elephant_fabric_target_development.json"
        )
        self.assertEqual(spec["mechanism_profile"], "V25")
        self.assertEqual(spec["seeds"], [195, 196, 197, 198, 199])
        self.assertEqual(set(spec["arms"]), {"guard_k1", "guard_elephant_target"})
        self.assertEqual(spec["defaults"]["guard_elephant_fabric_target_scale"], 11 / 9)
        workload = {
            "name": "AliStorage50", "cdf": "AliStorage2019",
            "selected_profile": "primary",
            "attempts": {"primary": {"profile": {
                "topo": "leaf_spine_8_100G_OS2", "hosts": 8,
                "oversubscription": 2, "simul_time": 0.01,
                "netload": 50, "bw": 100,
            }}},
        }
        trace = {"seed": 195, "path": "/tmp/v25-flow.txt", "sha256": "same"}
        for arm, enabled in (("guard_k1", "0"), ("guard_elephant_target", "1")):
            command = run_command(self.repo, spec, workload, trace, arm, True)
            self.assertEqual(
                command[command.index("--guard_elephant_fabric_target") + 1],
                enabled)
            self.assertEqual(
                float(command[command.index(
                    "--guard_elephant_fabric_target_scale") + 1]),
                11 / 9)

    def test_v25_rejects_changed_scale(self):
        path = (self.repo /
                "experiments/campaigns/guard_v25_elephant_fabric_target_development.json")
        value = json.loads(path.read_text())
        value["defaults"]["guard_elephant_fabric_target_scale"] = 1.2
        with tempfile.TemporaryDirectory() as directory:
            modified = Path(directory) / "v25.json"
            modified.write_text(json.dumps(value))
            with self.assertRaisesRegex(CampaignError, "scale to 11/9"):
                read_spec(modified)

    def test_v26_freezes_selected_elephant_receiver_authority(self):
        spec = read_spec(
            self.repo /
            "experiments/campaigns/guard_v26_elephant_receiver_authority_development.json"
        )
        self.assertEqual(spec["mechanism_profile"], "V26")
        self.assertEqual(spec["seeds"], [200, 201, 202, 203, 204])
        self.assertEqual(
            set(spec["arms"]), {"guard_k1", "guard_elephant_authority"})
        workload = {
            "name": "AliStorage50", "cdf": "AliStorage2019",
            "selected_profile": "primary",
            "attempts": {"primary": {"profile": {
                "topo": "leaf_spine_8_100G_OS2", "hosts": 8,
                "oversubscription": 2, "simul_time": 0.01,
                "netload": 50, "bw": 100,
            }}},
        }
        trace = {"seed": 200, "path": "/tmp/v26-flow.txt", "sha256": "same"}
        for arm, enabled in (("guard_k1", "0"),
                             ("guard_elephant_authority", "1")):
            command = run_command(self.repo, spec, workload, trace, arm, True)
            self.assertEqual(
                command[command.index(
                    "--guard_elephant_receiver_authority") + 1], enabled)
            self.assertEqual(
                command[command.index("--guard_receiver_concurrency") + 1], "1")

    def test_v26_rejects_an_extra_authority_arm_change(self):
        path = (self.repo /
                "experiments/campaigns/guard_v26_elephant_receiver_authority_development.json")
        value = json.loads(path.read_text())
        value["arms"]["guard_elephant_authority"]["guard_remaining_exponent"] = 2.0
        with tempfile.TemporaryDirectory() as directory:
            modified = Path(directory) / "v26.json"
            modified.write_text(json.dumps(value))
            with self.assertRaisesRegex(CampaignError, "other than receiver authority"):
                read_spec(modified)

    def test_v27_freezes_initial_window_priority_and_four_arms(self):
        spec = read_spec(
            self.repo /
            "experiments/campaigns/guard_v27_initial_window_priority_development.json"
        )
        self.assertEqual(spec["mechanism_profile"], "V27")
        self.assertEqual(spec["seeds"], [205, 206, 207, 208, 209])
        self.assertEqual(
            set(spec["arms"]),
            {"guard_k1", "guard_initial_window", "hpcc", "homa"})
        workload = {
            "name": "AliStorage40", "cdf": "AliStorage2019",
            "selected_profile": "primary",
            "attempts": {"primary": {"profile": {
                "topo": "leaf_spine_16_100G_OS4", "hosts": 16,
                "oversubscription": 4, "simul_time": 0.02,
                "netload": 40, "bw": 100,
            }}},
        }
        trace = {"seed": 205, "path": "/tmp/v27-flow.txt", "sha256": "same"}
        for arm, enabled in (("guard_k1", "0"),
                             ("guard_initial_window", "1")):
            command = run_command(self.repo, spec, workload, trace, arm, True)
            self.assertEqual(
                command[command.index("--guard_initial_window_priority") + 1],
                enabled)
            self.assertEqual(
                command[command.index("--guard_receiver_concurrency") + 1], "1")
        self.assertNotIn(
            "--guard_initial_window_priority",
            run_command(self.repo, spec, workload, trace, "hpcc", True))
        self.assertNotIn(
            "--guard_initial_window_priority",
            run_command(self.repo, spec, workload, trace, "homa", True))

    def test_v27_rejects_any_second_guard_change(self):
        path = (self.repo /
                "experiments/campaigns/guard_v27_initial_window_priority_development.json")
        value = json.loads(path.read_text())
        value["arms"]["guard_initial_window"]["guard_remaining_exponent"] = 2.0
        with tempfile.TemporaryDirectory() as directory:
            modified = Path(directory) / "v27.json"
            modified.write_text(json.dumps(value))
            with self.assertRaisesRegex(
                    CampaignError, "other than initial-window priority"):
                read_spec(modified)

    def test_v28_freezes_transport_window_floor_and_four_arms(self):
        spec = read_spec(
            self.repo /
            "experiments/campaigns/guard_v28_transport_window_floor_development.json"
        )
        self.assertEqual(spec["mechanism_profile"], "V28")
        self.assertEqual(spec["seeds"], [210, 211, 212, 213, 214])
        self.assertEqual(
            set(spec["arms"]),
            {"guard_k1", "guard_window_floor", "hpcc", "homa"})
        workload = {
            "name": "AliStorage40", "cdf": "AliStorage2019",
            "selected_profile": "primary",
            "attempts": {"primary": {"profile": {
                "topo": "leaf_spine_16_100G_OS4", "hosts": 16,
                "oversubscription": 4, "simul_time": 0.02,
                "netload": 40, "bw": 100,
            }}},
        }
        trace = {"seed": 210, "path": "/tmp/v28-flow.txt", "sha256": "same"}
        for arm, floor in (("guard_k1", "0"),
                           ("guard_window_floor", "8320")):
            command = run_command(self.repo, spec, workload, trace, arm, True)
            self.assertEqual(
                command[command.index(
                    "--guard_transport_window_floor_rtt_ns") + 1], floor)
            self.assertEqual(
                command[command.index("--guard_receiver_concurrency") + 1], "1")
        self.assertNotIn(
            "--guard_transport_window_floor_rtt_ns",
            run_command(self.repo, spec, workload, trace, "hpcc", True))
        self.assertNotIn(
            "--guard_transport_window_floor_rtt_ns",
            run_command(self.repo, spec, workload, trace, "homa", True))

    def test_v28_rejects_any_second_guard_change(self):
        path = (self.repo /
                "experiments/campaigns/guard_v28_transport_window_floor_development.json")
        value = json.loads(path.read_text())
        value["arms"]["guard_window_floor"]["guard_remaining_exponent"] = 2.0
        with tempfile.TemporaryDirectory() as directory:
            modified = Path(directory) / "v28.json"
            modified.write_text(json.dumps(value))
            with self.assertRaisesRegex(
                    CampaignError, "other than the transport-window floor"):
                read_spec(modified)

    def test_v29_freezes_post_grant_floor_and_four_arms(self):
        spec = read_spec(
            self.repo /
            "experiments/campaigns/guard_v29_post_grant_window_floor_development.json"
        )
        self.assertEqual(spec["mechanism_profile"], "V29")
        self.assertEqual(spec["seeds"], [215, 216, 217, 218, 219])
        self.assertEqual(
            set(spec["arms"]),
            {"guard_k1", "guard_post_grant_window", "hpcc", "homa"})
        workload = {
            "name": "AliStorage40", "cdf": "AliStorage2019",
            "selected_profile": "primary",
            "attempts": {"primary": {"profile": {
                "topo": "leaf_spine_16_100G_OS4", "hosts": 16,
                "oversubscription": 4, "simul_time": 0.02,
                "netload": 40, "bw": 100,
            }}},
        }
        trace = {"seed": 215, "path": "/tmp/v29-flow.txt", "sha256": "same"}
        for arm, floor, after in (
                ("guard_k1", "0", "0"),
                ("guard_post_grant_window", "8320", "1")):
            command = run_command(self.repo, spec, workload, trace, arm, True)
            self.assertEqual(
                command[command.index(
                    "--guard_transport_window_floor_rtt_ns") + 1], floor)
            self.assertEqual(
                command[command.index(
                    "--guard_transport_window_floor_after_first_grant") + 1], after)
        for arm in ("hpcc", "homa"):
            command = run_command(self.repo, spec, workload, trace, arm, True)
            self.assertNotIn(
                "--guard_transport_window_floor_after_first_grant", command)

    def test_v29_rejects_any_second_guard_change(self):
        path = (self.repo /
                "experiments/campaigns/guard_v29_post_grant_window_floor_development.json")
        value = json.loads(path.read_text())
        value["arms"]["guard_post_grant_window"][
            "guard_remaining_exponent"] = 2.0
        with tempfile.TemporaryDirectory() as directory:
            modified = Path(directory) / "v29.json"
            modified.write_text(json.dumps(value))
            with self.assertRaisesRegex(
                    CampaignError, "other than the post-grant window floor"):
                read_spec(modified)

    def test_v30_freezes_bounded_first_window_and_four_arms(self):
        spec = read_spec(
            self.repo /
            "experiments/campaigns/guard_v30_bounded_first_window_development.json"
        )
        self.assertEqual(spec["mechanism_profile"], "V30")
        self.assertEqual(spec["seeds"], [220, 221, 222, 223, 224])
        self.assertEqual(
            set(spec["arms"]),
            {"guard_k1", "guard_bounded_first_window", "hpcc", "homa"})
        workload = {
            "name": "AliStorage40", "cdf": "AliStorage2019",
            "selected_profile": "primary",
            "attempts": {"primary": {"profile": {
                "topo": "leaf_spine_16_100G_OS4", "hosts": 16,
                "oversubscription": 4, "simul_time": 0.02,
                "netload": 40, "bw": 100,
            }}},
        }
        trace = {"seed": 220, "path": "/tmp/v30-flow.txt", "sha256": "same"}
        command = run_command(
            self.repo, spec, workload, trace, "guard_bounded_first_window", True)
        self.assertEqual(command[command.index(
            "--guard_transport_window_floor_rtt_ns") + 1], "8320")
        self.assertEqual(command[command.index(
            "--guard_transport_window_floor_after_first_grant") + 1], "1")
        self.assertEqual(command[command.index(
            "--guard_transport_window_whole_flow_first_gate") + 1], "1")

    def test_v30_rejects_any_second_guard_change(self):
        path = (self.repo /
                "experiments/campaigns/guard_v30_bounded_first_window_development.json")
        value = json.loads(path.read_text())
        value["arms"]["guard_bounded_first_window"][
            "guard_remaining_exponent"] = 2.0
        with tempfile.TemporaryDirectory() as directory:
            modified = Path(directory) / "v30.json"
            modified.write_text(json.dumps(value))
            with self.assertRaisesRegex(
                    CampaignError, "other than the bounded first window"):
                read_spec(modified)

    def test_v31_freezes_ack_slack_window_and_four_arms(self):
        spec = read_spec(
            self.repo /
            "experiments/campaigns/guard_v31_ack_slack_window_development.json"
        )
        self.assertEqual(spec["mechanism_profile"], "V31")
        self.assertEqual(spec["seeds"], [225, 226, 227, 228, 229])
        self.assertEqual(
            set(spec["arms"]),
            {"guard_k1", "guard_ack_slack_window", "hpcc", "homa"})
        workload = {
            "name": "AliStorage40", "cdf": "AliStorage2019",
            "selected_profile": "primary",
            "attempts": {"primary": {"profile": {
                "topo": "leaf_spine_16_100G_OS4", "hosts": 16,
                "oversubscription": 4, "simul_time": 0.02,
                "netload": 40, "bw": 100,
            }}},
        }
        trace = {"seed": 225, "path": "/tmp/v31-flow.txt", "sha256": "same"}
        command = run_command(
            self.repo, spec, workload, trace, "guard_ack_slack_window", True)
        for option, expected in (
                ("--guard_transport_window_floor_rtt_ns", "8320"),
                ("--guard_transport_window_floor_after_first_grant", "1"),
                ("--guard_transport_window_whole_flow_first_gate", "1"),
                ("--guard_transport_window_ack_slack_packets", "16")):
            self.assertEqual(command[command.index(option) + 1], expected)

    def test_v31_rejects_any_second_guard_change(self):
        path = (self.repo /
                "experiments/campaigns/guard_v31_ack_slack_window_development.json")
        value = json.loads(path.read_text())
        value["arms"]["guard_ack_slack_window"][
            "guard_remaining_exponent"] = 2.0
        with tempfile.TemporaryDirectory() as directory:
            modified = Path(directory) / "v31.json"
            modified.write_text(json.dumps(value))
            with self.assertRaisesRegex(
                    CampaignError, "other than the ACK-slack window"):
                read_spec(modified)

    def test_v32_reuses_v31_rule_with_fresh_identities(self):
        spec = read_spec(
            self.repo /
            "experiments/campaigns/guard_v32_ack_slack_window_revalidation.json"
        )
        self.assertEqual(spec["mechanism_profile"], "V32")
        self.assertEqual(spec["seeds"], [230, 231, 232, 233, 234])
        self.assertEqual(
            spec["arms"]["guard_ack_slack_window"], {
                "cc": "guard", "guard_receiver_concurrency": 1,
                "guard_transport_window_floor_rtt_ns": 8320,
                "guard_transport_window_floor_after_first_grant": 1,
                "guard_transport_window_whole_flow_first_gate": 1,
                "guard_transport_window_ack_slack_packets": 16,
            })

    def test_guard_k_aliases_use_full_guard_mechanism_checks(self):
        stats = {
            "grants_sent": 1, "grants_received": 1,
            "hpcc_valid_feedback": 1, "hpcc_actual_rate_changes": 1,
            "reactive_binding_updates": 1,
            "guard_sender_srpt_enabled": 1,
            "guard_sender_srpt_selections": 1,
            "guard_sender_srpt_non_rr": 1,
            "guard_tail_bypass_flows": 1,
            "guard_remaining_refresh_events": 1,
            "guard_adaptive_target_enabled": 0,
            "guard_elephant_receiver_authority_bindings": 1,
        }
        self.assertTrue(all(mechanism_checks("guard_k1", stats).values()))
        self.assertTrue(all(mechanism_checks("guard_k2", stats).values()))
        self.assertTrue(all(mechanism_checks("guard_aging", stats).values()))
        self.assertTrue(all(mechanism_checks("guard_spillover", stats).values()))
        self.assertTrue(all(mechanism_checks("guard_elephant_target", stats).values()))
        self.assertTrue(all(mechanism_checks("guard_elephant_authority", stats).values()))
        self.assertTrue(all(mechanism_checks("guard_initial_window", stats).values()))
        self.assertTrue(all(mechanism_checks("guard_window_floor", stats).values()))
        self.assertTrue(all(mechanism_checks("guard_post_grant_window", stats).values()))
        self.assertTrue(all(mechanism_checks("guard_bounded_first_window", stats).values()))
        self.assertTrue(all(mechanism_checks("guard_ack_slack_window", stats).values()))

    def test_fct_metrics_include_standard_goodput_jain(self):
        rows = [
            {"size": 1000, "duration_ns": 1000, "fct_us": 1,
             "slowdown": 1},
            {"size": 2000, "duration_ns": 2000, "fct_us": 2,
             "slowdown": 2},
        ]
        buckets = [{"name": "all_sizes"}]
        metrics = fct_metrics(rows, buckets)
        self.assertEqual(metrics["overall_flow_goodput_jain"], 1.0)
        self.assertEqual(metrics["all_sizes_flow_goodput_jain"], 1.0)

    def test_v17_mechanism_gate_has_no_performance_artifact_reader(self):
        source = (self.repo / "experiments/analyze_guard_v17_general_mechanisms.py").read_text()
        for forbidden in ("_out_fct", "_out_queue_stats", "parse_fct(",
                          "queue_summary_metrics("):
            self.assertNotIn(forbidden, source)

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
