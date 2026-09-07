#!/usr/bin/env python3
"""Frozen identity and admission tests for GUARD V15 compatibility."""

import copy
import json
from pathlib import Path
import tempfile
import unittest

from experiments import analyze_guard_v14_compatibility as base_analyzer
from experiments import run_guard_v14_compatibility as base_runner
from experiments import run_guard_v15_compatibility as runner
from experiments.run_campaign import CampaignError


ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "experiments" / "guard_v15_compatibility.json"


class GuardV15CompatibilityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.spec = runner.read_spec(SPEC)

    def test_delta_expands_to_one_explicit_new_policy(self) -> None:
        self.assertEqual(self.spec["schema_version"], 15)
        self.assertEqual(self.spec["fresh_seeds"], [146, 147, 148])
        final = self.spec["final_guard"]
        replay = self.spec["v13_replay_profile"]
        self.assertEqual(final["guard_transition_prefix_fail_closed"], 0)
        self.assertEqual(final["guard_transition_prefix_ack_clock_fallback"], 1)
        self.assertEqual(replay["guard_transition_prefix_ack_clock_fallback"], 0)
        self.assertEqual(
            set(final), set(base_runner.profile_keys(self.spec)))

    def test_v14_profile_and_command_order_remain_unchanged(self) -> None:
        v14 = base_runner.read_spec(
            ROOT / "experiments" / "guard_v14_compatibility.json")
        self.assertEqual(base_runner.profile_keys(v14), base_runner.PROFILE_KEYS)
        self.assertNotIn(
            "guard_transition_prefix_ack_clock_fallback",
            v14["final_guard"])

    def test_final_and_replay_cli_separate_the_new_policy(self) -> None:
        fresh_identity = base_runner.compatibility_plan(self.spec)[0]
        fresh_traffic = {"path": "/tmp/fresh.txt"}
        fresh = base_runner.run_command(
            ROOT, self.spec, fresh_identity, fresh_traffic)
        replay_identity = base_runner.replay_plan(self.spec)[0]
        replay = base_runner.run_command(
            ROOT, self.spec, replay_identity, {"path": "/tmp/replay.txt"})
        option = "--guard_transition_prefix_ack_clock_fallback"
        self.assertEqual(fresh[fresh.index(option) + 1], "1")
        self.assertEqual(replay[replay.index(option) + 1], "0")
        fail_closed = "--guard_transition_prefix_fail_closed"
        self.assertEqual(fresh[fresh.index(fail_closed) + 1], "0")

    def test_delta_hash_rejects_post_registration_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "spec.json"
            delta = json.loads(SPEC.read_text(encoding="utf-8"))
            delta["fresh_seeds"][0] = 143
            path.write_text(json.dumps(delta), encoding="utf-8")
            with self.assertRaisesRegex(CampaignError, "canonical V15"):
                runner.read_spec(path)

    def test_transition_audit_accepts_only_ready_or_closed_fallback(self) -> None:
        common = {
            "receiver_node": 15, "receiver_nic": 1, "epoch": 1,
            "priority_group_set_hash": 1, "target_vector_hash": 2,
            "target_vector_entries": 5, "capacity_bps": 100000000000,
            "active_upper_bound_bps": 50000000000,
            "draining_upper_bound_bps": 20000000000,
            "active_plus_draining_upper_bound_bps": 70000000000,
            "draining_wire_upper_bound_bytes": 1000,
            "start_ns": 100, "deadline_ns": 200,
            "deadline_policy": "wire_residual_watchdog",
        }
        ready = {**common, "transaction": 1, "prefix_target_bytes": 1000,
                 "prefix_observed_bytes": 1000, "deadline_outcome": "ready",
                 "terminal_closure": "activation_ack_closed"}
        fallback = {**common, "transaction": 2, "prefix_target_bytes": 1000,
                    "prefix_observed_bytes": 400,
                    "deadline_outcome": "timeout_fallback",
                    "terminal_closure": "fallback_activation_ack_closed"}
        stats = {"records": 2, "attempted": 2, "written": 2,
                 "truncated": 0}
        small = {"high_transitions": 2}
        prefix = {"starts": 2}
        result = base_analyzer.audit_transition_rows(
            [ready, fallback], stats, small, prefix, 100000000000,
            False, True)
        self.assertEqual(result["records"], 2)
        broken = copy.deepcopy(fallback)
        broken["terminal_closure"] = "activation_ack_closed"
        with self.assertRaisesRegex(Exception, "did not close normally"):
            base_analyzer.audit_transition_rows(
                [ready, broken], stats, small, prefix, 100000000000,
                False, True)


if __name__ == "__main__":
    unittest.main()
