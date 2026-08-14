from pathlib import Path
import unittest

from experiments.run_guard_feature_ablation import read_spec, run_command


class GuardFeatureAblationTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(__file__).resolve().parents[2]
        self.spec_path = self.repo / "experiments/campaigns/guard_feature_ablation.json"
        self.spec = read_spec(self.spec_path)

    def test_switch_tuples_are_one_factor_and_core(self):
        expected = {
            "guard": (1, 1, 1),
            "no_priority": (0, 1, 1),
            "no_srpt": (1, 0, 1),
            "no_reclaim": (1, 1, 0),
            "core": (0, 0, 0),
        }
        for arm, values in expected.items():
            row = self.spec["arms"][arm]
            actual = tuple(row[field] for field in (
                "guard_size_priority", "guard_sender_srpt", "guard_work_conserving"
            ))
            self.assertEqual(actual, values)

    def test_guard_commands_freeze_every_feature_switch(self):
        workload = self.spec["workloads"][0]
        trace = {"seed": 1, "path": "/tmp/frozen-flow.txt"}
        for arm in ("guard", "no_priority", "no_srpt", "no_reclaim", "core"):
            command = run_command(self.repo, self.spec, workload, trace, arm)
            for field in (
                "guard_size_priority", "guard_sender_srpt", "guard_work_conserving"
            ):
                option = f"--{field}"
                self.assertIn(option, command)
                self.assertEqual(
                    command[command.index(option) + 1], str(self.spec["arms"][arm][field])
                )

    def test_hpcc_command_has_no_guard_options(self):
        workload = self.spec["workloads"][0]
        trace = {"seed": 1, "path": "/tmp/frozen-flow.txt"}
        command = run_command(self.repo, self.spec, workload, trace, "hpcc")
        self.assertEqual(command[command.index("--cc") + 1], "hpcc")
        self.assertFalse(any(value.startswith("--guard_") for value in command))


if __name__ == "__main__":
    unittest.main()
