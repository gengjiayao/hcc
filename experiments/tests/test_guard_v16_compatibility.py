#!/usr/bin/env python3
"""Frozen identity and runtime-PG evidence tests for GUARD V16."""

import json
from pathlib import Path
import tempfile
import unittest

from experiments import analyze_guard_v14_compatibility as analyzer
from experiments import run_guard_v14_compatibility as campaign
from experiments import run_guard_v15_compatibility as v15_runner
from experiments import run_guard_v16_compatibility as runner
from experiments.run_campaign import CampaignError


ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "experiments" / "guard_v16_compatibility.json"


class GuardV16CompatibilityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.spec = runner.read_spec(SPEC)

    def test_delta_changes_only_evidence_identity_and_fresh_seeds(self) -> None:
        prior = v15_runner.read_spec(
            ROOT / "experiments" / "guard_v15_compatibility.json")
        self.assertEqual(self.spec["schema_version"], 16)
        self.assertEqual(self.spec["fresh_seeds"], [149, 150, 151])
        self.assertEqual(self.spec["final_guard"], prior["final_guard"])
        self.assertEqual(
            self.spec["v13_replay_profile"], prior["v13_replay_profile"])
        self.assertFalse(self.spec.get("performance_metrics_permitted", False))

    def test_commands_keep_ack_clock_candidate_and_replay_separate(self) -> None:
        fresh = campaign.run_command(
            ROOT, self.spec, campaign.compatibility_plan(self.spec)[0],
            {"path": "/tmp/fresh.txt"})
        replay = campaign.run_command(
            ROOT, self.spec, campaign.replay_plan(self.spec)[0],
            {"path": "/tmp/replay.txt"})
        option = "--guard_transition_prefix_ack_clock_fallback"
        self.assertEqual(fresh[fresh.index(option) + 1], "1")
        self.assertEqual(replay[replay.index(option) + 1], "0")
        self.assertIn("--smoke", fresh)

    def test_runtime_pg_provenance_accepts_only_frozen_masks(self) -> None:
        valid = {
            "freezes": 13, "mixed_pg_freezes": 3,
            "max_priority_groups": 3,
            "mixed_priority_group_mask": 0xe0,
            "all_priority_group_mask": 0xf0,
        }
        analyzer.validate_v16_pg_provenance("mixed_pg_n15", valid)
        for field, value in (
                ("max_priority_groups", 4),
                ("mixed_priority_group_mask", 0xf0),
                ("all_priority_group_mask", 0xe0)):
            broken = dict(valid)
            broken[field] = value
            with self.assertRaises(Exception):
                analyzer.validate_v16_pg_provenance("mixed_pg_n15", broken)

    def test_nonmixed_provenance_still_requires_internal_consistency(self) -> None:
        analyzer.validate_v16_pg_provenance(
            "same_pg_n4", {"freezes": 4, "mixed_pg_freezes": 0,
                           "max_priority_groups": 1,
                           "mixed_priority_group_mask": 0,
                           "all_priority_group_mask": 1 << 7})
        with self.assertRaises(Exception):
            analyzer.validate_v16_pg_provenance(
                "same_pg_n4", {"freezes": 4, "mixed_pg_freezes": 0,
                               "max_priority_groups": 2,
                               "mixed_priority_group_mask": 0,
                               "all_priority_group_mask": 0xc0})

    def test_delta_hash_rejects_post_registration_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "spec.json"
            delta = json.loads(SPEC.read_text(encoding="utf-8"))
            delta["fresh_seeds"][0] = 146
            path.write_text(json.dumps(delta), encoding="utf-8")
            with self.assertRaisesRegex(CampaignError, "canonical V16"):
                runner.read_spec(path)

    def test_capability_probe_binds_all_runtime_fields(self) -> None:
        tokens = set(self.spec["simulator_capability_probe"]["required_tokens"])
        for token in ("max_priority_groups %lu",
                      "mixed_priority_group_mask %lu",
                      "all_priority_group_mask %lu"):
            self.assertIn(token, tokens)


if __name__ == "__main__":
    unittest.main()
