import json
from pathlib import Path
import tempfile
import unittest

from experiments.run_general_workloads import (
    CampaignError,
    choose_profile,
    inspect_flow_file,
    planned_runs,
    read_spec,
    run_command,
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


if __name__ == "__main__":
    unittest.main()
