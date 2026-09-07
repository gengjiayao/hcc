#!/usr/bin/env python3
"""Frozen identity and mixed-vector evidence tests for GUARD V17."""

import json
from pathlib import Path
import tempfile
import unittest

from experiments import analyze_guard_v14_compatibility as analyzer
from experiments import run_guard_v14_compatibility as campaign
from experiments import run_guard_v16_compatibility as v16_runner
from experiments import run_guard_v17_compatibility as runner
from experiments.run_campaign import CampaignError


ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "experiments" / "guard_v17_compatibility.json"


class GuardV17CompatibilityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.spec = runner.read_spec(SPEC)

    def test_delta_preserves_mechanism_and_uses_fresh_seeds(self) -> None:
        prior = v16_runner.read_spec(
            ROOT / "experiments" / "guard_v16_compatibility.json")
        self.assertEqual(self.spec["schema_version"], 17)
        self.assertEqual(self.spec["fresh_seeds"], [152, 153, 154])
        self.assertEqual(self.spec["final_guard"], prior["final_guard"])
        self.assertEqual(
            self.spec["v13_replay_profile"], prior["v13_replay_profile"])

    def test_mixed_gate_accepts_arrival_grouping_variation(self) -> None:
        for vector in (
                {"freezes": 13, "mixed_pg_freezes": 3,
                 "max_priority_groups": 3,
                 "mixed_priority_group_mask": 0xe0,
                 "all_priority_group_mask": 0xf0},
                {"freezes": 15, "mixed_pg_freezes": 5,
                 "max_priority_groups": 2,
                 "mixed_priority_group_mask": 0xc0,
                 "all_priority_group_mask": 0xf0}):
            analyzer.validate_v17_pg_provenance("mixed_pg_n15", vector)

    def test_mixed_gate_rejects_missing_class_or_no_mixed_vector(self) -> None:
        bad = (
            {"freezes": 13, "mixed_pg_freezes": 3,
             "max_priority_groups": 3, "mixed_priority_group_mask": 0xe0,
             "all_priority_group_mask": 0xe0},
            {"freezes": 13, "mixed_pg_freezes": 0,
             "max_priority_groups": 1, "mixed_priority_group_mask": 0,
             "all_priority_group_mask": 0xf0},
        )
        for vector in bad:
            with self.assertRaises(Exception):
                analyzer.validate_v17_pg_provenance("mixed_pg_n15", vector)

    def test_cli_keeps_candidate_only_ack_clock_mode(self) -> None:
        command = campaign.run_command(
            ROOT, self.spec, campaign.compatibility_plan(self.spec)[0],
            {"path": "/tmp/fresh.txt"})
        option = "--guard_transition_prefix_ack_clock_fallback"
        self.assertEqual(command[command.index(option) + 1], "1")
        self.assertIn("--smoke", command)

    def test_delta_hash_is_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "spec.json"
            delta = json.loads(SPEC.read_text(encoding="utf-8"))
            delta["pg_provenance_policy"]["arrival_grouping_constraint"] = "three"
            path.write_text(json.dumps(delta), encoding="utf-8")
            with self.assertRaisesRegex(CampaignError, "canonical V17"):
                runner.read_spec(path)


if __name__ == "__main__":
    unittest.main()
