#!/usr/bin/env python3
"""Synthetic, no-ns-3 tests for the V14 compatibility campaign tools."""

from __future__ import annotations

import ast
import copy
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from experiments import analyze_guard_v14_compatibility as analyzer
from experiments import report_guard_v14_compatibility as reporter
from experiments import run_guard_v14_compatibility as runner
from experiments.run_campaign import CampaignError, sha256_file
from experiments.summarize_campaign import SummaryError


REPO = Path(__file__).resolve().parents[2]
SPEC_PATH = REPO / "experiments" / "guard_v14_compatibility.json"
CAPACITY = 100_000_000_000
FORBIDDEN_PREFIXES = ("_out_fct", "_out_queue", "_out_qlen", "_out_voq")


def trace_row(time_ns: int, event: str, flow_id: int, generation: int,
              *, phase: str, transaction: int, ack_required: int,
              grant_rate: int = 50_000_000_000,
              revision: int = 1,
              frozen_draining_bps: int = 10_000_000_000,
              live_draining_bps: int | None = None,
              capacity_recompute_pending: int = 0) -> dict[str, object]:
    authoritative = event in ("sent", "ack_received", "ack_retire_close")
    if live_draining_bps is None:
        live_draining_bps = frozen_draining_bps
    row: dict[str, object] = {
        field: 0 for field in analyzer.TRACE_FIELDS_V14
        if field not in analyzer.TRACE_STRING_FIELDS
    }
    row.update({
        "time_ns": time_ns, "event": event, "set_change": "none",
        "host_node": 15 if authoritative or event == "ack_retired" else 0,
        "flow_id": flow_id,
        "data_source_ip": 0, "data_destination_ip": 15,
        "active_flows": 2 if authoritative else 0,
        "line_rate_bps": CAPACITY, "grant_rate_bps": grant_rate,
        "serialized_bytes": 60, "generation": generation,
        "ack_required": ack_required,
        "transaction_id": (transaction if authoritative or
                           event == "ack_retired" else 0),
        "grant_phase": phase, "subject_role": "incumbent",
        "drain_outcome": "ready", "activation_register_ns": 0,
    })
    if authoritative:
        frozen_allocatable = CAPACITY - frozen_draining_bps
        row.update({
            "allocation_revision": revision, "progress_revision": revision,
            "membership_revision": 1, "snapshot_valid": 1,
            "frozen_capacity_bps": CAPACITY, "frozen_active_records": 2,
            "frozen_draining_records": 1 if frozen_draining_bps else 0,
            "frozen_draining_reserved_bps": frozen_draining_bps,
            "frozen_allocatable_bps": frozen_allocatable,
            "frozen_encoded_target_bps": frozen_allocatable - 2_000_000,
            "live_active_records": 2,
            "live_draining_records": 1 if live_draining_bps else 0,
            "live_draining_reserved_bps": live_draining_bps,
            "capacity_recompute_pending": capacity_recompute_pending,
            "reason_mask": 1,
        })
    return row


def required_exchange(flow_id: int, generation: int, base: int,
                      phase: str, transaction: int, *, revision: int = 1,
                      frozen_draining_bps: int = 10_000_000_000,
                      live_draining_bps: int | None = None,
                      capacity_recompute_pending: int = 0) -> list[dict[str, object]]:
    provenance = {
        "revision": revision,
        "frozen_draining_bps": frozen_draining_bps,
        "live_draining_bps": live_draining_bps,
        "capacity_recompute_pending": capacity_recompute_pending,
    }
    return [
        trace_row(base + 10, "sent", flow_id, generation, phase=phase,
                  transaction=transaction, ack_required=1, **provenance),
        trace_row(base + 20, "received", flow_id, generation, phase=phase,
                  transaction=transaction, ack_required=1, **provenance),
        trace_row(base + 30, "ack_sent", flow_id, generation, phase=phase,
                  transaction=transaction, ack_required=1, grant_rate=0,
                  **provenance),
        trace_row(base + 40, "ack_received", flow_id, generation, phase=phase,
                  transaction=transaction, ack_required=1, grant_rate=0,
                  **provenance),
    ]


def retired_exchange(flow_id: int, generation: int, base: int,
                     phase: str, transaction: int) -> list[dict[str, object]]:
    rows = required_exchange(flow_id, generation, base, phase, transaction)
    rows[3] = trace_row(
        base + 40, "ack_retire_close", flow_id, generation, phase=phase,
        transaction=transaction, ack_required=1)
    rows[3]["serialized_bytes"] = 0
    rows.append(trace_row(
        base + 50, "ack_retired", flow_id, generation, phase=phase,
        transaction=transaction, ack_required=1))
    return rows


class GuardV14CompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = runner.read_spec(SPEC_PATH)

    def scenario(self, name: str) -> dict[str, object]:
        return next(row for row in self.spec["fresh_scenarios"]
                    if row["name"] == name)

    def test_frozen_matrix_and_v13_replay_hashes(self) -> None:
        self.assertEqual(len(runner.replay_plan(self.spec)), 4)
        self.assertEqual(len(runner.compatibility_plan(self.spec)), 24)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for count, expected in runner.REPLAY_HASHES.items():
                path = root / f"n{count}.txt"
                runner._write_flow_file(path, runner._replay_flows(self.spec, count))
                self.assertEqual(sha256_file(path), expected)

    def test_full_spec_is_fail_closed_against_scenario_edits(self) -> None:
        changed = copy.deepcopy(self.spec)
        changed["fresh_scenarios"][0]["parameters"]["flow_bytes"] += 1
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "changed.json"
            path.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaisesRegex(CampaignError, "complete canonical"):
                runner.read_spec(path)

    def test_frozen_global_gates_name_every_provenance_invariant(self) -> None:
        expected = {
            "receiver_authoritative_sent_and_ack_received_require_frozen_snapshot",
            "sender_received_and_ack_sent_snapshot_fields_must_be_zero",
            "frozen_snapshot_tuple_immutable_within_run_monotonic_allocation_revision",
            "frozen_draining_plus_allocatable_must_equal_frozen_receiver_capacity",
            "frozen_encoded_target_sum_and_each_grant_must_not_exceed_frozen_allocatable",
            "live_draining_must_not_exceed_frozen_draining",
            "live_draining_below_frozen_requires_capacity_recompute_pending",
            "next_allocation_revision_must_refreeze_live_draining_after_capacity_dirty_barrier",
        }
        gates = self.spec["global_mechanism_gates"]
        self.assertTrue(expected.issubset(gates))
        self.assertTrue(all(gates[name] is True for name in expected))
        changed = copy.deepcopy(self.spec)
        changed["final_guard"]["guard_ack_interval_packets"] += 1
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "changed-final.json"
            path.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaisesRegex(CampaignError, "complete canonical"):
                runner.read_spec(path)

    def test_fresh_flows_are_cross_tor_and_trace_bounds_have_ack_headroom(self) -> None:
        expected = {
            "same_pg_n4": (4, 548), "same_pg_n15": (15, 4215),
            "mixed_pg_n15": (15, 5175),
            "same_sender_multiflow_srpt": (8, 2376),
            "one_rtt_short_background": (24, 1864),
            "tail_bypass_overlap": (8, 3144),
            "proactive_drain_interleave": (9, 5697),
            "remaining_refresh_dual_receiver_epoch": (26, 5914),
        }
        for scenario in self.spec["fresh_scenarios"]:
            flows = runner._fresh_flows(self.spec, scenario, 143)
            bound = runner._no_fault_trace_bound(self.spec, scenario, flows)
            self.assertEqual((len(flows), bound["max_trace_lines"]),
                             expected[scenario["name"]])
            self.assertEqual(bound["retired_ack_local_closures"],
                             int(scenario["requirements"]["registered_flows"]))
            self.assertLessEqual(bound["max_trace_lines"], 6144)
            self.assertTrue(all(src < 8 and dst >= 8 for src, dst, _pg, _size, _t in flows))
        short = runner._fresh_flows(
            self.spec, self.scenario("one_rtt_short_background"), 143)
        self.assertEqual(sum(size <= 104000 for _s, _d, _p, size, _t in short), 16)

    def test_commands_are_smoke_only_and_freeze_complete_final_bundle(self) -> None:
        traffic = {"path": "/tmp/frozen-flow.txt"}
        for identity in runner.compatibility_plan(self.spec):
            command = runner.run_command(REPO, self.spec, identity, traffic)
            self.assertIn("--smoke", command)
            self.assertEqual(command[command.index("--cc") + 1], "guard")
            for key in runner.PROFILE_KEYS:
                self.assertIn(f"--{key}", command)
            gamma = command[command.index("--guard_gamma") + 1]
            self.assertEqual(gamma, "16.0" if identity["scenario"] ==
                             "proactive_drain_interleave" else "1.0")

    def test_grant_closure_checks_required_ack_order_and_receiver_c_minus_d(self) -> None:
        rows = required_exchange(1, 1, 0, "transition_prepare", 7)
        rows += required_exchange(1, 2, 100, "transition_activate", 7)
        result = analyzer.trace_closure(rows, True, CAPACITY)
        self.assertEqual(result["required_grants"], 2)
        self.assertEqual(result["receiver_sent_rows_with_draining"], 2)
        broken = copy.deepcopy(rows)
        broken[0]["frozen_allocatable_bps"] -= 1
        with self.assertRaisesRegex(SummaryError, "D\+A=C"):
            analyzer.trace_closure(broken, True, CAPACITY)
        early = copy.deepcopy(rows)
        early[4]["time_ns"] = 35
        with self.assertRaisesRegex(SummaryError, "preceded"):
            analyzer.trace_closure(early, True, CAPACITY)
        same_ns = required_exchange(1, 1, 0, "transition_prepare", 7)
        for row in same_ns:
            row["time_ns"] = 10
        analyzer.trace_closure(same_ns, True, CAPACITY)
        wrong_wire_order = copy.deepcopy(same_ns)
        wrong_wire_order[2], wrong_wire_order[3] = (
            wrong_wire_order[3], wrong_wire_order[2])
        with self.assertRaisesRegex(SummaryError, "grant/ACK order"):
            analyzer.trace_closure(wrong_wire_order, True, CAPACITY)
        same_ns_cross_phase = required_exchange(
            1, 1, 0, "transition_prepare", 7)
        same_ns_cross_phase += required_exchange(
            1, 2, 100, "transition_activate", 7)
        same_ns_cross_phase[4]["time_ns"] = same_ns_cross_phase[3]["time_ns"]
        activation = same_ns_cross_phase.pop(4)
        same_ns_cross_phase.insert(3, activation)
        with self.assertRaisesRegex(SummaryError, "preceded"):
            analyzer.trace_closure(same_ns_cross_phase, True, CAPACITY)

    def test_retired_ack_has_distinct_safety_and_wire_closures(self) -> None:
        rows = retired_exchange(1, 1, 0, "transition_prepare", 7)
        rows += required_exchange(2, 2, 100, "transition_activate", 7)
        result = analyzer.trace_closure(rows, True, CAPACITY)
        self.assertEqual(result["retired_ack_closures"], 1)
        self.assertEqual(result["retired_ack_wire_receipts"], 1)

        # Receiver completion can safely retire the local barrier before the
        # already-issued grant reaches the sender.  Wire causality and local
        # safety causality are independent partial orders.
        pre_arrival_close = copy.deepcopy(rows)
        pre_arrival_close[3]["time_ns"] = pre_arrival_close[0]["time_ns"] + 1
        close = pre_arrival_close.pop(3)
        pre_arrival_close.insert(1, close)
        analyzer.trace_closure(pre_arrival_close, True, CAPACITY)

        missing_safety = [row for row in rows
                          if row["event"] != "ack_retire_close"]
        with self.assertRaisesRegex(SummaryError, "ACK evidence"):
            analyzer.trace_closure(missing_safety, True, CAPACITY)
        duplicate_safety = copy.deepcopy(rows)
        duplicate_safety.insert(4, copy.deepcopy(duplicate_safety[3]))
        with self.assertRaisesRegex(SummaryError, "duplicate"):
            analyzer.trace_closure(duplicate_safety, True, CAPACITY)
        early_wire = copy.deepcopy(rows)
        early_wire[4]["time_ns"] = early_wire[3]["time_ns"] - 1
        with self.assertRaisesRegex(SummaryError, "grant/ACK order"):
            analyzer.trace_closure(early_wire, True, CAPACITY)
        close_before_issue = copy.deepcopy(rows)
        close_before_issue[3]["time_ns"] = close_before_issue[0]["time_ns"] - 1
        with self.assertRaisesRegex(SummaryError, "grant/ACK order"):
            analyzer.trace_closure(close_before_issue, True, CAPACITY)
        missing_snapshot = copy.deepcopy(rows)
        missing_snapshot[3]["snapshot_valid"] = 0
        with self.assertRaisesRegex(SummaryError, "lacks a frozen snapshot"):
            analyzer.trace_closure(missing_snapshot, True, CAPACITY)

        for index in (3, 4):
            wrong_phase = copy.deepcopy(rows)
            wrong_phase[index]["grant_phase"] = "fast_prepare"
            with self.assertRaisesRegex(SummaryError, "phase/endpoint"):
                analyzer.trace_closure(wrong_phase, True, CAPACITY)
            wrong_transaction = copy.deepcopy(rows)
            wrong_transaction[index]["transaction_id"] += 1
            with self.assertRaisesRegex(SummaryError, "transaction/action/target"):
                analyzer.trace_closure(wrong_transaction, True, CAPACITY)
            wrong_target = copy.deepcopy(rows)
            wrong_target[index]["grant_rate_bps"] += 1
            with self.assertRaisesRegex(SummaryError, "transaction/action/target"):
                analyzer.trace_closure(wrong_target, True, CAPACITY)
            wrong_endpoint = copy.deepcopy(rows)
            wrong_endpoint[index]["data_source_ip"] += 1
            with self.assertRaisesRegex(SummaryError, "phase/endpoint"):
                analyzer.trace_closure(wrong_endpoint, True, CAPACITY)
            wrong_action = copy.deepcopy(rows)
            wrong_action[index]["subject_role"] = "waiter"
            with self.assertRaisesRegex(SummaryError, "transaction/action/target"):
                analyzer.trace_closure(wrong_action, True, CAPACITY)

    def test_retired_ack_stats_close_and_fail_closed(self) -> None:
        trace = {"retired_ack_closures": 2,
                 "retired_ack_wire_receipts": 2}
        stats = {"closures": 2, "received": 2, "peak": 1,
                 "terminal": 0, "overflow": 0}
        analyzer.validate_retired_ack_closure(stats, trace)
        for field, value in (("received", 1), ("terminal", 1),
                             ("overflow", 1)):
            broken = dict(stats)
            broken[field] = value
            with self.assertRaisesRegex(SummaryError, "do not close"):
                analyzer.validate_retired_ack_closure(broken, trace)
        broken_trace = dict(trace)
        broken_trace["retired_ack_wire_receipts"] = 1
        with self.assertRaisesRegex(SummaryError, "do not close"):
            analyzer.validate_retired_ack_closure(stats, broken_trace)

    def test_trace_provenance_keeps_frozen_d_across_ack_and_refreezes_next_revision(self) -> None:
        frozen_d = 33_776_000_000
        rows = required_exchange(
            1, 1, 0, "transition_prepare", 7,
            frozen_draining_bps=frozen_d)
        # D completes while the first revision's ACK barrier is still live.
        for row in rows[3:]:
            if row["snapshot_valid"] == 1:
                row["live_draining_records"] = 0
                row["live_draining_reserved_bps"] = 0
                row["capacity_recompute_pending"] = 1
        activation = required_exchange(
            2, 2, 100, "transition_activate", 7,
            frozen_draining_bps=frozen_d, live_draining_bps=0,
            capacity_recompute_pending=1)
        next_revision = required_exchange(
            1, 3, 200, "fast_prepare", 8, revision=2,
            frozen_draining_bps=0, live_draining_bps=0,
            capacity_recompute_pending=1)
        rows += activation + next_revision
        result = analyzer.trace_closure(rows, True, CAPACITY)
        self.assertEqual(result["capacity_recomputed_revisions"], 1)
        revision_one = [row for row in rows if row["snapshot_valid"] == 1 and
                        row["allocation_revision"] == 1]
        self.assertEqual({row["frozen_draining_reserved_bps"]
                          for row in revision_one}, {frozen_d})
        self.assertEqual({row["frozen_allocatable_bps"]
                          for row in revision_one}, {CAPACITY - frozen_d})

        tampered = copy.deepcopy(rows)
        tampered[4].update({
            "frozen_draining_records": 0,
            "frozen_draining_reserved_bps": 0,
            "frozen_allocatable_bps": CAPACITY,
            "frozen_encoded_target_bps": CAPACITY - 2_000_000,
        })
        with self.assertRaisesRegex(SummaryError, "tuple drifted"):
            analyzer.trace_closure(tampered, True, CAPACITY)

        no_dirty = copy.deepcopy(rows)
        no_dirty[3]["capacity_recompute_pending"] = 0
        with self.assertRaisesRegex(SummaryError, "without capacity recompute"):
            analyzer.trace_closure(no_dirty, True, CAPACITY)

        grew = copy.deepcopy(rows)
        grew[0]["live_draining_reserved_bps"] = frozen_d + 1
        with self.assertRaisesRegex(SummaryError, "live D exceeds"):
            analyzer.trace_closure(grew, True, CAPACITY)

        stale_next = copy.deepcopy(rows)
        for row in stale_next:
            if row["snapshot_valid"] == 1 and row["allocation_revision"] == 2:
                row["frozen_draining_records"] = 1
                row["frozen_draining_reserved_bps"] = frozen_d
                row["frozen_allocatable_bps"] = CAPACITY - frozen_d
                row["frozen_encoded_target_bps"] = CAPACITY - frozen_d - 2_000_000
        with self.assertRaisesRegex(SummaryError, "refreeze live D"):
            analyzer.trace_closure(stale_next, True, CAPACITY)

        sender_leak = copy.deepcopy(rows)
        sender_leak[1]["snapshot_valid"] = 1
        with self.assertRaisesRegex(SummaryError, "non-authoritative"):
            analyzer.trace_closure(sender_leak, True, CAPACITY)

        missing_ack_snapshot = copy.deepcopy(rows)
        missing_ack_snapshot[3]["snapshot_valid"] = 0
        with self.assertRaisesRegex(SummaryError, "lacks a frozen snapshot"):
            analyzer.trace_closure(missing_ack_snapshot, True, CAPACITY)

    def test_allocation_revisions_are_run_monotonic_across_epochs(self) -> None:
        first_epoch = required_exchange(
            1, 1, 0, "fast_prepare", 1, revision=1)
        second_epoch = required_exchange(
            1, 2, 100, "fast_prepare", 2, revision=2)
        result = analyzer.trace_closure(first_epoch + second_epoch, True, CAPACITY)
        self.assertEqual(result["frozen_allocation_sent_rows"], 2)
        reused = first_epoch + second_epoch + required_exchange(
            1, 3, 200, "fast_prepare", 3, revision=1)
        with self.assertRaisesRegex(SummaryError, "reappeared"):
            analyzer.trace_closure(reused, True, CAPACITY)

    def test_transition_audit_allows_wire_prefix_upper_bound_over_capacity(self) -> None:
        row: dict[str, object] = {
            field: 1 for field in analyzer.AUDIT_FIELDS
            if field not in analyzer.AUDIT_STRING_FIELDS
        }
        row.update({
            "receiver_node": 15, "receiver_nic": 0, "epoch": 1,
            "transaction": 1,
            "priority_group_set_hash": analyzer._fnv_words([4, 5, 6, 7]),
            "target_vector_hash": 7, "target_vector_entries": 15,
            "capacity_bps": CAPACITY, "active_upper_bound_bps": CAPACITY - 1,
            "draining_upper_bound_bps": 3 * CAPACITY,
            "active_plus_draining_upper_bound_bps": 4 * CAPACITY - 1,
            "prefix_target_bytes": 100, "prefix_observed_bytes": 101,
            "start_ns": 10, "deadline_ns": 20,
            "deadline_policy": "wire_residual_watchdog",
            "deadline_outcome": "ready",
            "terminal_closure": "activation_ack_closed",
        })
        stats = {"records": 1, "attempted": 1, "written": 1, "truncated": 0}
        result = analyzer.audit_transition_rows(
            [row], stats, {"high_transitions": 1}, {"starts": 1}, CAPACITY, True)
        self.assertEqual(result["records"], 1)
        with self.assertRaisesRegex(SummaryError, "do not close"):
            analyzer.audit_transition_rows(
                [row], stats, {"high_transitions": 2}, {"starts": 1},
                CAPACITY, True)
        broken = dict(row)
        broken["active_plus_draining_upper_bound_bps"] += 1
        with self.assertRaisesRegex(SummaryError, "identity"):
            analyzer.audit_transition_rows(
                [broken], stats, {"high_transitions": 1}, {"starts": 1},
                CAPACITY, True)

    def test_drain_requirements_close_classification_and_reservations(self) -> None:
        requirements = self.scenario("proactive_drain_interleave")["requirements"]
        stats = {
            "registrations": 9, "guard_sender_srpt_selections": 1,
            "guard_sender_srpt_non_rr": 1, "guard_one_rtt_bypass_flows": 0,
            "guard_tail_bypass_flows": 1, "proactive_releases": 1,
        }
        refresh = {
            "draining_requests": 2, "draining_pending": 1,
            "draining_direct": 1, "boundary_commits": 1,
            "completion_releases": 2, "max_draining_records": 1,
            "max_draining_reserved_bps": 1, "progress_requests": 1,
            "progress_transactions": 1, "progress_commits": 1,
            "progress_busy_deferrals": 1,
        }
        args = (
            "proactive_drain_interleave", requirements, stats,
            {"high_transitions": 1}, {"starts": 1}, {"records": 1},
            {"mixed_pg_freezes": 1}, refresh,
            {"receiver_sent_rows_with_draining": 1},
            {"records": 1, "distinct_receivers": 1,
             "epochs_per_receiver": {"15": 1}},
        )
        analyzer._apply_requirements(*args)
        refresh["completion_releases"] = 1
        with self.assertRaises(SummaryError):
            analyzer._apply_requirements(*args)

    def test_terminal_draining_state_must_be_zero(self) -> None:
        small = {field: 0 for field in (
            "terminal_phase", "terminal_pending_acks", "terminal_pending_membership",
            "terminal_waiters", "terminal_transaction_waiters",
            "terminal_ready_waiters", "terminal_collection_ready",
            "terminal_initial_flushed", "terminal_transition_committed",
            "terminal_revision", "terminal_consumed_revision",
        )}
        vector = {"terminal_records": 0, "terminal_holds": 0,
                  "terminal_ledger": 0}
        refresh = {"terminal_draining_records": 0,
                   "terminal_draining_reserved_bps": 0,
                   "terminal_progress_dirty": 0}
        analyzer._zero_terminal(small, vector, refresh,
                                {"terminal_timer": 0}, {"terminal_budget": 0})
        refresh["terminal_draining_reserved_bps"] = 1
        with self.assertRaisesRegex(SummaryError, "terminal state"):
            analyzer._zero_terminal(small, vector, refresh,
                                    {"terminal_timer": 0}, {"terminal_budget": 0})

    def test_dual_receiver_epoch_requirement_uses_per_receiver_counts(self) -> None:
        requirements = self.scenario(
            "remaining_refresh_dual_receiver_epoch")["requirements"]
        stats = {
            "registrations": 26, "guard_sender_srpt_selections": 1,
            "guard_sender_srpt_non_rr": 1, "guard_one_rtt_bypass_flows": 0,
            "guard_tail_bypass_flows": 1, "proactive_releases": 1,
        }
        refresh = {
            "draining_requests": 0, "draining_pending": 0,
            "draining_direct": 0, "boundary_commits": 0,
            "completion_releases": 0, "max_draining_records": 0,
            "max_draining_reserved_bps": 0, "progress_requests": 1,
            "progress_transactions": 1, "progress_commits": 1,
            "progress_busy_deferrals": 1,
        }
        audit = {"records": 4, "distinct_receivers": 2,
                 "epochs_per_receiver": {"14": 2, "15": 2}}
        args = (
            "remaining_refresh_dual_receiver_epoch", requirements, stats,
            {"high_transitions": 4}, {"starts": 4}, {"records": 4},
            {"mixed_pg_freezes": 1}, refresh,
            {"receiver_sent_rows_with_draining": 0}, audit,
        )
        analyzer._apply_requirements(*args)
        audit["epochs_per_receiver"]["15"] = 1
        with self.assertRaisesRegex(SummaryError, "per-receiver"):
            analyzer._apply_requirements(*args)

    def test_analyzer_artifact_reads_are_an_explicit_mechanism_allowlist(self) -> None:
        source_path = REPO / "experiments" / "analyze_guard_v14_compatibility.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        suffixes = set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and
                    node.func.id == "one_artifact" and len(node.args) >= 2 and
                    isinstance(node.args[1], ast.Constant)):
                suffixes.add(node.args[1].value)
        self.assertEqual(suffixes, {
            "_input_flow.txt", "_out_guard_stats.txt", "_out_pfc.txt",
            "_out_guard_lifecycle.csv", "_out_guard_grants.csv",
            "_out_guard_transition_audit.csv",
        })
        self.assertFalse(any(str(suffix).startswith(FORBIDDEN_PREFIXES)
                             for suffix in suffixes))

    def test_replay_gate_rejects_arbitrary_path_before_hash_or_open(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            forbidden = root / "x_out_fct.txt"
            forbidden.write_text("sealed", encoding="utf-8")
            gate = {"compatibility_execution_armed": True,
                    "replay_admission": {"path": str(forbidden), "sha256": "x"}}
            with mock.patch.object(
                    runner, "sha256_file",
                    side_effect=AssertionError("must reject before hashing")):
                with self.assertRaisesRegex(CampaignError, "fixed campaign file"):
                    runner.validate_sealed_replay(root, gate, {})

    def test_analyzer_and_report_reject_preflight_symlink_before_open(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            forbidden = root / "x_out_fct.txt"
            forbidden.write_text('{"repo":"/tmp"}', encoding="utf-8")
            (root / "preflight.json").symlink_to(forbidden)
            analysis_path = root / "compatibility-admission.json"
            analysis_path.write_text("{}", encoding="utf-8")
            original = Path.open

            def guarded(path: Path, *args: object, **kwargs: object):
                if any(token in path.name for token in FORBIDDEN_PREFIXES):
                    raise AssertionError(f"opened performance artifact: {path}")
                return original(path, *args, **kwargs)

            with mock.patch.object(Path, "open", guarded):
                with self.assertRaisesRegex(SummaryError, "must not be a symlink"):
                    analyzer.analyze(SPEC_PATH, root, runner.FRESH_SCOPE)
                with self.assertRaisesRegex(SummaryError, "must not be a symlink"):
                    reporter.validate_analysis(SPEC_PATH, analysis_path, root)

    def test_report_never_opens_present_performance_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            spec_path = root / "spec.json"
            spec_path.write_bytes(SPEC_PATH.read_bytes())
            replay_path = root / "replay-admission.json"
            preflight_path = root / "preflight.json"
            preflight = {"simulator_git_sha": "abc",
                         "spec_sha256": sha256_file(spec_path)}
            preflight_path.write_text(json.dumps(preflight), encoding="utf-8")
            preflight_sha = sha256_file(preflight_path)
            replay_doc = {
                "schema_version": 14, "scope": runner.REPLAY_SCOPE,
                "status": "admitted", "passed": True,
                "mechanism_admission_passed": True, "performance_emitted": False,
                "run_count": 4, "spec_sha256": sha256_file(spec_path),
                "preflight_sha256": preflight_sha, "simulator_git_sha": "abc",
                "stage_gate_sha256": "pre-seal-gate",
                "runs": [{"scope": runner.REPLAY_SCOPE,
                          "performance_emitted": False} for _ in range(4)],
            }
            replay_path.write_text(json.dumps(replay_doc), encoding="utf-8")
            replay_sha = sha256_file(replay_path)
            (root / "stage-gate.json").write_text(json.dumps({
                "schema_version": 14, "preflight_sha256": preflight_sha,
                "compatibility_execution_armed": True,
                "replay_admission": {
                    "path": str(replay_path), "sha256": replay_sha,
                    "source_stage_gate_sha256": "pre-seal-gate",
                },
            }), encoding="utf-8")
            runs = []
            for ordinal, identity in enumerate(runner.compatibility_plan(self.spec), 1):
                scenario = self.scenario(identity["scenario"])
                count = len(runner._fresh_flows(self.spec, scenario, identity["seed"]))
                registered = int(scenario["requirements"]["registered_flows"])
                runs.append({
                    "ordinal": ordinal, "scenario": identity["scenario"],
                    "seed": identity["seed"], "flow_count": count,
                    "registered_flows": registered,
                    "completion": {"finished": count, "total": count},
                    "grant_closure": {"grant_frames": 1,
                                      "receiver_sent_rows_with_draining": 0},
                    "transition_audit": {"records": 0},
                    "target_vector": {"mixed_pg_freezes": 0},
                    "refresh_draining": {"progress_requests": 0,
                                         "progress_transactions": 0,
                                         "progress_commits": 0,
                                         "draining_requests": 0,
                                         "completion_releases": 0},
                    "feature_signals": {"sender_srpt_selections": 0,
                                        "sender_srpt_non_rr": 0,
                                        "one_rtt_bypass_flows": 0,
                                        "tail_bypass_flows": 0},
                    "performance_emitted": False,
                })
            analysis_path = root / "compatibility-admission.json"
            analysis_doc = {
                "schema_version": 14, "scope": runner.FRESH_SCOPE,
                "status": "admitted", "passed": True,
                "mechanism_admission_passed": True, "performance_emitted": False,
                "run_count": 24, "spec_sha256": sha256_file(spec_path),
                "preflight_sha256": sha256_file(preflight_path),
                "stage_gate_sha256": sha256_file(root / "stage-gate.json"),
                "simulator_git_sha": "abc",
                "replay_admission_sha256": replay_sha, "runs": runs,
            }
            analysis_path.write_text(json.dumps(analysis_doc), encoding="utf-8")
            for name in ("x_out_fct.txt", "x_out_queue_stats.txt",
                         "x_out_qlen.txt", "x_out_voq_per_dst.txt"):
                (root / name).write_text("sealed", encoding="utf-8")
            original = Path.open

            def guarded(path: Path, *args: object, **kwargs: object):
                if any(token in path.name for token in FORBIDDEN_PREFIXES):
                    raise AssertionError(f"opened performance artifact: {path}")
                return original(path, *args, **kwargs)

            with (mock.patch.object(Path, "open", guarded),
                  mock.patch.object(reporter, "analyze", return_value=analysis_doc)):
                result = reporter.report(spec_path, analysis_path, root, root / "report")
            self.assertTrue(result["passed"])
            self.assertFalse(result["performance_emitted"])
            recomputed = copy.deepcopy(analysis_doc)
            recomputed["runs"][0]["grant_closure"]["grant_frames"] = 2
            with mock.patch.object(reporter, "analyze", return_value=recomputed):
                with self.assertRaisesRegex(SummaryError, "revalidation"):
                    reporter.validate_analysis(spec_path, analysis_path, root)


if __name__ == "__main__":
    unittest.main()
