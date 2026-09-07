#!/usr/bin/env python3
"""Synthetic, no-ns-3 tests for the V6--V13 coalescing holdout tools."""

from __future__ import annotations

import contextlib
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from experiments import aggregate_membership_coalescing_holdout as aggregator
from experiments import analyze_membership_coalescing_holdout as analyzer
from experiments import run_membership_coalescing_holdout as runner
from experiments.run_campaign import CampaignError, sha256_file
from experiments.summarize_campaign import SummaryError


REPO = Path(__file__).resolve().parents[2]
SPEC_PATH = REPO / "experiments" / "membership_coalescing_ladder_v6.json"
SPEC_V7_PATH = REPO / "experiments" / "membership_coalescing_ladder_v7.json"
SPEC_V8_PATH = REPO / "experiments" / "membership_coalescing_ladder_v8.json"
SPEC_V9_PATH = REPO / "experiments" / "membership_coalescing_ladder_v9.json"
SPEC_V10_PATH = REPO / "experiments" / "membership_coalescing_ladder_v10.json"
SPEC_V11_PATH = REPO / "experiments" / "membership_coalescing_ladder_v11.json"
SPEC_V12_PATH = REPO / "experiments" / "membership_coalescing_ladder_v12.json"
SPEC_V13_PATH = REPO / "experiments" / "membership_coalescing_ladder_v13.json"


def trace_row(time_ns: int, event: str, flow_id: int, generation: int,
              rate: int = 0, active: int = 0, pending: int = 0,
              change: str = "none") -> dict[str, object]:
    return {
        "time_ns": time_ns, "event": event, "set_change": change,
        "host_node": 15 if event in ("sent", "ack_received") else flow_id,
        "flow_id": flow_id, "data_source_ip": flow_id,
        "data_destination_ip": 15, "active_flows": active,
        "line_rate_bps": 100_000_000_000, "grant_rate_bps": rate,
        "next_seq": 0, "serialized_bytes": 60, "generation": generation,
        "pending_acks": pending,
    }


def candidate_trace(count: int = 2, generations: int = 1) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    rate = 100_000_000_000 // count // 1_000_000 * 1_000_000
    for generation in range(1, generations + 1):
        base = generation * 100
        for flow_id in range(count):
            rows.extend([
                trace_row(base + 10, "sent", flow_id, generation, rate, count, count,
                          "membership_batch"),
                trace_row(base + 20, "received", flow_id, generation, rate),
                trace_row(base + 30, "ack_sent", flow_id, generation),
                trace_row(base + 40 + flow_id, "ack_received", flow_id, generation,
                          pending=count - flow_id - 1),
            ])
    return rows


def generation_stats(count: int = 2, generations: int = 1) -> tuple[dict[str, int], dict[str, int]]:
    grants = count * generations
    return (
        {"grants_sent": grants, "grants_received": grants,
         "grant_bytes_sent": grants * 60},
        {"generation": generations, "batches": generations,
         "fully_acked_batches": generations, "ack_sent": grants,
         "ack_received": grants, "ack_bytes_sent": grants * 60,
         "ack_bytes_received": grants * 60},
    )


def selective_trace() -> list[dict[str, object]]:
    """One ACK-required join generation followed by one ACK-optional release."""
    rows: list[dict[str, object]] = []
    for flow_id in (0, 1):
        generation_rows = [
            trace_row(10 + flow_id, "sent", flow_id, 1, 50_000_000_000, 2, 2,
                      "membership_batch"),
            trace_row(20 + flow_id, "received", flow_id, 1, 50_000_000_000),
            trace_row(30 + flow_id, "ack_sent", flow_id, 1),
            trace_row(40 + flow_id, "ack_received", flow_id, 1,
                      pending=1 - flow_id),
        ]
        for row in generation_rows:
            row["ack_required"] = 1
        rows.extend(generation_rows)
    for row in (
            trace_row(100, "sent", 1, 2, 100_000_000_000, 1, 0,
                      "membership_batch"),
            trace_row(110, "received", 1, 2, 100_000_000_000)):
        row["ack_required"] = 0
        rows.append(row)
    return rows


def selective_stats() -> tuple[dict[str, int], dict[str, int]]:
    return (
        {"grants_sent": 3, "grants_received": 3, "grant_bytes_sent": 180},
        {"generation": 2, "batches": 2, "fully_acked_batches": 1,
         "ack_required_batches": 1, "ack_optional_batches": 1,
         "ack_required_grants": 2, "ack_optional_grants": 1,
         "generation_zero_rejected": 0, "generation_mismatch_rejected": 0,
         "ack_sent": 2, "ack_received": 2,
         "ack_bytes_sent": 120, "ack_bytes_received": 120},
    )


def geometric_trace_n8(optional_counts: tuple[int, int] = (3, 1)) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for flow_id in range(8):
        generation_rows = [
            trace_row(10 + flow_id, "sent", flow_id, 1, 12_500_000_000, 8, 8,
                      "membership_batch"),
            trace_row(30 + flow_id, "received", flow_id, 1, 12_500_000_000),
            trace_row(50 + flow_id, "ack_sent", flow_id, 1),
            trace_row(70 + flow_id, "ack_received", flow_id, 1,
                      pending=7 - flow_id),
        ]
        for row in generation_rows:
            row["ack_required"] = 1
        rows.extend(generation_rows)
    for generation, active, base in (
            (2, optional_counts[0], 200),
            (3, optional_counts[1], 300)):
        flow_ids = range(active)
        rate = 100_000_000_000 // active // 1_000_000 * 1_000_000
        for flow_id in flow_ids:
            for row in (
                    trace_row(base + flow_id, "sent", flow_id, generation, rate,
                              len(flow_ids), 0, "membership_batch"),
                    trace_row(base + 20 + flow_id, "received", flow_id,
                              generation, rate)):
                row["ack_required"] = 0
                rows.append(row)
    return rows


def geometric_stats_n8() -> tuple[dict[str, int], dict[str, int]]:
    return (
        {"grants_sent": 12, "grants_received": 12, "grant_bytes_sent": 720},
        {"generation": 3, "batches": 3, "fully_acked_batches": 1,
         "ack_required_batches": 1, "ack_optional_batches": 2,
         "ack_required_grants": 8, "ack_optional_grants": 4,
         "generation_zero_rejected": 0, "generation_mismatch_rejected": 0,
         "release_threshold_deferrals": 4,
         "ack_sent": 8, "ack_received": 8,
         "ack_bytes_sent": 480, "ack_bytes_received": 480},
    )


def v9_geometric_trace_n8() -> list[dict[str, object]]:
    rows = geometric_trace_n8()
    for row in rows:
        generation = int(row["generation"])
        flow_id = int(row["flow_id"])
        if generation == 1:
            offsets = {"sent": 0, "received": 10, "ack_sent": 20,
                       "ack_received": 30 + flow_id}
            row["time_ns"] = 62_410 + offsets[str(row["event"])]
        elif generation == 2:
            row["time_ns"] = 70_000 + (0 if row["event"] == "sent" else 20) + flow_id
        else:
            row["time_ns"] = 80_000 + (0 if row["event"] == "sent" else 20) + flow_id
    return rows


def v10_geometric_trace_n8() -> list[dict[str, object]]:
    rows = geometric_trace_n8()
    for row in rows:
        generation = int(row["generation"])
        flow_id = int(row["flow_id"])
        if generation == 1:
            offsets = {"sent": 0, "received": 10, "ack_sent": 20,
                       "ack_received": 30 + flow_id}
            row["time_ns"] = 23_650 + offsets[str(row["event"])]
        elif generation == 2:
            row["time_ns"] = 30_000 + (0 if row["event"] == "sent" else 20) + flow_id
        else:
            row["time_ns"] = 40_000 + (0 if row["event"] == "sent" else 20) + flow_id
    return rows


def v11_phase_row(time_ns: int, event: str, flow_id: int, generation: int,
                  phase: str, target: int, transaction: int,
                  rate: int, role: str) -> dict[str, object]:
    row = trace_row(time_ns, event, flow_id, generation, rate,
                    target if event in ("sent", "ack_received") else 0,
                    change=phase if event == "sent" else "none")
    row["ack_required"] = int(phase != "release")
    row["transaction_id"] = transaction if event in ("sent", "ack_received") else 0
    row["grant_phase"] = phase
    row["membership_target_n"] = target if event in ("sent", "ack_received") else 0
    row["subject_role"] = role
    if event in ("ack_sent", "ack_received"):
        row["grant_rate_bps"] = 0
    return row


def v11_required_phase(rows: list[dict[str, object]], base: int,
                       generation: int, transaction: int, phase: str,
                       target: int, flows: list[int]) -> int:
    role = "incumbent" if phase.endswith("prepare") else "waiter"
    rate = 100_000_000_000 // target // 1_000_000 * 1_000_000
    for offset, flow_id in enumerate(flows):
        start = base + offset
        rows.extend([
            v11_phase_row(start, "sent", flow_id, generation, phase,
                          target, transaction, rate, role),
            v11_phase_row(start + 10, "received", flow_id, generation, phase,
                          target, transaction, rate, role),
            v11_phase_row(start + 20, "ack_sent", flow_id, generation, phase,
                          target, transaction, rate, role),
            v11_phase_row(start + 30, "ack_received", flow_id, generation, phase,
                          target, transaction, rate, role),
        ])
    return generation + 1


def v11_fixture(count: int) -> tuple[list[dict[str, object]], dict[str, int],
                                     dict[str, object], dict[str, int],
                                     dict[int, int]]:
    rows: list[dict[str, object]] = []
    generation = 1
    base = 100
    incumbents: list[int] = []
    fast_count = min(count, 4)
    for flow_id in range(fast_count):
        transaction = flow_id + 1
        if incumbents:
            generation = v11_required_phase(
                rows, base, generation, transaction, "fast_prepare",
                flow_id + 1, list(incumbents))
            base += 100
        generation = v11_required_phase(
            rows, base, generation, transaction, "fast_activate",
            flow_id + 1, [flow_id])
        base += 100
        incumbents.append(flow_id)
    if count > 4:
        transaction = 5
        base = max(24_000, (count - 1) * 1_000 + 17_000)
        transition_start = base
        generation = v11_required_phase(
            rows, base, generation, transaction, "transition_prepare",
            count, list(incumbents))
        base += 100
        waiters = list(range(4, count))
        generation = v11_required_phase(
            rows, base, generation, transaction, "transition_activate",
            count, waiters)
        incumbents.extend(waiters)
    sent = [row for row in rows if row["event"] == "sent"]
    ack_sent = [row for row in rows if row["event"] == "ack_sent"]
    stats = {
        "grants_sent": len(sent), "grants_received": len(sent),
        "grant_bytes_sent": 60 * len(sent),
    }
    membership: dict[str, object] = {
        field: 0 for field in analyzer.COALESCING_FIELDS_V10}
    membership.update({
        "ack_sent": len(ack_sent), "ack_received": len(ack_sent),
        "ack_bytes_sent": 60 * len(ack_sent),
        "ack_bytes_received": 60 * len(ack_sent),
        "initial_collection_quiet_ns": 16_640,
        "initial_collection_full_deadline": 0,
    })
    small = {field: 0 for field in analyzer.SMALL_SET_FIELDS_V11}
    phase_prefix = {
        "fast_prepare": "fast_prepare", "fast_activate": "fast_activate",
        "transition_prepare": "transition_prepare",
        "transition_activate": "transition_activate", "release": "release",
    }
    for phase, prefix in phase_prefix.items():
        grants = [row for row in sent if row["grant_phase"] == phase]
        acks = [row for row in rows
                if row["event"] == "ack_received" and row["grant_phase"] == phase]
        small[f"accepted_{prefix}_batches"] = len({row["generation"] for row in grants})
        small[f"accepted_{prefix}_grants"] = len(grants)
        if phase != "release":
            small[f"accepted_{prefix}_acks"] = len(acks)
            small[f"{prefix}_wire_acks"] = len(acks)
        small[f"{prefix}_wire_grants"] = len(grants)
    small.update({
        "enabled": 1, "limit": 4, "transactions": 5 if count > 4 else count,
        "waiters_activated": count, "wire_grant_frames": len(sent),
        "wire_ack_frames": len(ack_sent),
        "control_frames": len(sent) + len(ack_sent),
        "global_grants_sent": len(sent), "global_acks_sent": len(ack_sent),
        "wire_reconciled": 1,
        "last_transaction_close_ns": max(row["time_ns"] for row in rows),
    })
    registrations = {flow_id: flow_id * 1_000 for flow_id in range(count)}
    if count > 4:
        flush = registrations[count - 1] + 16_640
        membership.update({
            "initial_collection_starts": 1, "initial_collection_flushes": 1,
            "initial_collection_deferred_changes": count - 5,
            "initial_collection_reschedules": count - 5,
            "initial_collection_quiet_flushes": 1,
            "initial_collection_wait_ns": flush - registrations[4],
            "initial_collection_max_wait_ns": flush - registrations[4],
        })
        small.update({
            "high_transitions": 1, "high_collection_flushes": 1,
            "high_transition_registered_n": count,
            "high_transition_waiters": count - 4,
            "prefix_close_ns": 1_000, "collection_flush_ns": flush,
            "transition_prepare_start_ns": transition_start,
        })
    return rows, stats, membership, small, registrations


def v12_fixture(count: int) -> tuple[list[dict[str, object]], dict[str, int],
                                     dict[str, object], dict[str, int],
                                     dict[str, int], dict[int, int]]:
    rows, stats, membership, small, registrations = v11_fixture(count)
    for row in rows:
        row.update({
            "prefix_target_bytes": 0, "prefix_observed_bytes": 0,
            "drain_outcome": "none", "activation_batch_index": 0,
            "activation_batch_size": 0, "activation_register_ns": -1,
        })
    prefix = {field: 0 for field in analyzer.TRANSITION_PREFIX_FIELDS_V12}
    prefix["enabled"] = 1
    if count > 4:
        activation = [row for row in rows
                      if row["event"] == "sent" and
                      row["grant_phase"] == "transition_activate"]
        batch_size = len(activation)
        for row in rows:
            if (row["event"] in ("sent", "ack_received") and
                    row["grant_phase"] == "transition_activate"):
                row.update({
                    "prefix_target_bytes": 4096,
                    "prefix_observed_bytes": 4096,
                    "drain_outcome": "ready", "activation_batch_index": 1,
                    "activation_batch_size": batch_size,
                    "activation_register_ns": registrations[int(row["flow_id"])],
                })
        prefix.update({
            "starts": 1, "ready": 1,
            "waiters_required": batch_size, "waiters_ready": batch_size,
            "target_bytes": 4096 * batch_size,
            "received_bytes": 4096 * batch_size, "remaining_bytes": 0,
            "wait_ns": 50, "max_wait_ns": 50,
            "start_ns": 100, "deadline_ns": 200, "ready_ns": 150,
        })
    return rows, stats, membership, small, prefix, registrations


def v13_fixture(count: int) -> tuple[
        list[dict[str, object]], dict[str, int], dict[str, object],
        dict[str, int], dict[str, int], dict[str, int], dict[int, int],
        list[dict[str, int]], list[dict[str, int]]]:
    rows, stats, membership, small, prefix, registrations = v12_fixture(count)
    watchdog = {field: 0 for field in analyzer.TRANSITION_WATCHDOG_FIELDS_V13}
    watchdog["enabled"] = 1
    lifecycle = [{"flow_id": flow, "size_bytes": 8_388_608}
                 for flow in range(count)]
    flows = [{"src": flow, "dst": 15, "size": 8_388_608}
             for flow in range(count)]
    if count > 4:
        prepare_last_ack = max(
            int(row["time_ns"]) for row in rows
            if row["event"] == "ack_received" and
            row["grant_phase"] == "transition_prepare")
        prefix.update({
            "start_ns": prepare_last_ack,
            "ready_ns": prepare_last_ack + 10,
            "wait_ns": 10,
            "max_wait_ns": 10,
        })
        waiters = count - 4
        target = 4096
        packets_per_waiter = 5
        payload_per_waiter = 5000
        rounded_payload = waiters * payload_per_waiter
        packet_count = waiters * packets_per_waiter
        wire = rounded_payload + packet_count * 90
        occupancy = sum(
            int(row["grant_rate_bps"]) for row in rows
            if row["event"] == "sent" and
            row["grant_phase"] == "transition_prepare")
        residual = 100_000_000_000 - occupancy
        serialization = (wire * 8_000_000_000 + residual - 1) // residual
        delay = 8320 + serialization
        prefix["deadline_ns"] = int(prefix["start_ns"]) + delay
        watchdog.update({
            "records": 1,
            "rounded_payload_budget_bytes": rounded_payload,
            "packet_count": packet_count, "header_per_packet": 90,
            "wire_bytes": wire, "incumbent_count": 4,
            "occupancy_bps": occupancy,
            "capacity_bps": 100_000_000_000, "residual_bps": residual,
            "serialization_ns": serialization, "max_rtt_ns": 8320,
            "delay_ns": delay, "start_ns": int(prefix["start_ns"]),
            "deadline_ns": int(prefix["deadline_ns"]),
        })
        self_targets = [row for row in rows
                        if row["event"] == "sent" and
                        row["grant_phase"] == "transition_activate"]
        assert all(int(row["prefix_target_bytes"]) == target
                   for row in self_targets)
    return (rows, stats, membership, small, prefix, watchdog, registrations,
            lifecycle, flows)


def synthetic_v11_analysis() -> dict[str, object]:
    spec = runner.read_spec(SPEC_V11_PATH)
    terminal = tuple(spec["candidate_admission"]["terminal_zero_fields"])
    rows: list[dict[str, object]] = []
    for seed, count, arm in runner.matrix(spec):
        candidate = arm != runner.CONTROL_ARM
        frames = ({2: 6, 4: 20, 8: 40, 15: 60}[count]
                  if candidate else 300)
        queue_mean = 80.0 if candidate and count == 15 else 100.0
        queue_p99 = 70.0 if candidate and count == 15 else 100.0
        evidence: dict[str, object] = {
            "total_control_frames_sent": frames, "pending_generations": 0,
            "retry_grants": 0, "stale_events": 0,
        }
        safe = None
        if candidate:
            evidence.update({
                "phase_contract": {phase: {} for phase in (
                    "fast_prepare", "fast_activate", "transition_prepare",
                    "transition_activate", "release")},
                "barriers_closed": True, "transition_union_closed": True,
            })
            safe = {field: 0 for field in terminal}
            safe.update({
                "enabled": 1, "limit": 4, "control_frames": frames,
                "wire_grant_frames": frames // 2,
                "wire_ack_frames": frames - frames // 2,
                "wire_reconciled": 1, "barrier_violations": 0,
                "early_unlocks": 0, "post_transition_fast_grants": 0,
                "unattributed_grant_frames": 0, "unattributed_ack_frames": 0,
                "high_transitions": int(count > 4),
                "high_collection_flushes": int(count > 4),
                "high_transition_registered_n": count if count > 4 else 0,
            })
        rows.append({
            "seed": seed, "active_flows": count, "controller": arm,
            "flow_sha256": f"{seed}-{count}",
            "performance": {
                "mean_fct_us": 100.0, "completion_span_us": 110.0,
                "receiver_queue_time_sampled_mean_bytes": queue_mean,
                "receiver_queue_time_sampled_p99_bytes": queue_p99,
                "flow_goodput_jain": 1.0, "receiver_queue_samples": 100,
            },
            "generation_ack": evidence, "safe_activation": safe,
            "pfc": {field: 0 for field in (
                "pause_events", "resume_events", "matched_intervals",
                "cumulative_pause_ns", "max_pause_ns")},
        })
    return {
        "schema_version": 11,
        "status": "validated_full_matrix_performance_unsealed",
        "mechanism_admission_passed": True, "performance_unsealed": True,
        "run_count": 40, "spec_sha256": sha256_file(SPEC_V11_PATH),
        "pilot_admission_sha256": "d" * 64,
        "known_replay_admission_sha256": "e" * 64, "runs": rows,
    }


def synthetic_v12_analysis() -> dict[str, object]:
    spec = runner.read_spec(SPEC_V12_PATH)
    terminal = tuple(spec["candidate_admission"]["terminal_zero_fields"])
    prefix_terminal = tuple(
        spec["candidate_admission"]["terminal_prefix_zero_fields"])
    rows: list[dict[str, object]] = []
    for seed, count, arm in runner.matrix(spec):
        candidate = arm != runner.CONTROL_ARM
        frames = ({2: 6, 4: 20, 8: 40, 15: 60}[count]
                  if candidate else 300)
        queue_mean = 80.0 if candidate and count == 15 else 100.0
        queue_p99 = 70.0 if candidate and count == 15 else 100.0
        evidence: dict[str, object] = {
            "total_control_frames_sent": frames, "pending_generations": 0,
            "retry_grants": 0, "stale_events": 0,
        }
        safe = None
        prefix = None
        if candidate:
            phase = {name: {} for name in (
                "fast_prepare", "fast_activate", "transition_prepare",
                "transition_activate", "release")}
            evidence.update({
                "phase_contract": phase, "barriers_closed": True,
                "transition_union_closed": True,
            })
            safe = {field: 0 for field in terminal}
            safe.update({
                "enabled": 1, "limit": 4, "control_frames": frames,
                "wire_grant_frames": frames // 2,
                "wire_ack_frames": frames - frames // 2,
                "wire_reconciled": 1, "barrier_violations": 0,
                "early_unlocks": 0, "post_transition_fast_grants": 0,
                "unattributed_grant_frames": 0,
                "unattributed_ack_frames": 0,
                "high_transitions": int(count > 4),
                "high_collection_flushes": int(count > 4),
                "high_transition_registered_n": count if count > 4 else 0,
            })
            prefix = {field: 0 for field in prefix_terminal}
            prefix.update({
                "enabled": 1, "starts": int(count > 4),
                "ready": int(count > 4), "timeouts": 0, "degraded": 0,
                "remaining_bytes": 0, "fallback_batches": 0,
                "fallback_max_batch": 0, "fallback_closed_batches": 0,
                "order_violations": 0, "barrier_violations": 0,
            })
            if count > 4:
                waiters = count - 4
                prefix.update({
                    "waiters_required": waiters, "waiters_ready": waiters,
                    "target_bytes": 100 * waiters,
                    "received_bytes": 100 * waiters,
                    "wait_ns": 50, "max_wait_ns": 50,
                    "start_ns": 100, "deadline_ns": 200, "ready_ns": 150,
                })
                evidence["transition_prefix"] = {
                    "normal_path": True, "prefix_started": True,
                    "activation_batch_index": 1,
                    "activation_batch_size": waiters,
                }
            else:
                prefix.update({
                    "waiters_required": 0, "waiters_ready": 0,
                    "target_bytes": 0, "received_bytes": 0,
                    "wait_ns": 0, "max_wait_ns": 0,
                    "start_ns": 0, "deadline_ns": 0, "ready_ns": 0,
                })
                evidence["transition_prefix"] = {
                    "normal_path": True, "prefix_started": False,
                    "activation_batch_size": 0,
                }
        rows.append({
            "seed": seed, "active_flows": count, "controller": arm,
            "flow_sha256": f"{seed}-{count}",
            "performance": {
                "mean_fct_us": 100.0, "completion_span_us": 110.0,
                "receiver_queue_time_sampled_mean_bytes": queue_mean,
                "receiver_queue_time_sampled_p99_bytes": queue_p99,
                "flow_goodput_jain": 1.0, "receiver_queue_samples": 100,
            },
            "generation_ack": evidence, "safe_activation": safe,
            "transition_prefix": prefix,
            "pfc": {field: 0 for field in (
                "pause_events", "resume_events", "matched_intervals",
                "cumulative_pause_ns", "max_pause_ns")},
        })
    return {
        "schema_version": 12,
        "status": "validated_full_matrix_performance_unsealed",
        "mechanism_admission_passed": True, "performance_unsealed": True,
        "run_count": 40, "spec_sha256": sha256_file(SPEC_V12_PATH),
        "pilot_admission_sha256": "d" * 64,
        "known_replay_admission_sha256": "e" * 64, "runs": rows,
    }


def synthetic_v13_analysis() -> dict[str, object]:
    """Lift the V12 synthetic matrix onto V13 and add watchdog proofs."""
    payload = synthetic_v12_analysis()
    seed_map = dict(zip((132, 133, 134, 135, 136),
                        (138, 139, 140, 141, 142)))
    for row in payload["runs"]:
        assert isinstance(row, dict)
        row["seed"] = seed_map[int(row["seed"])]
        count = int(row["active_flows"])
        candidate = row["controller"] != runner.CONTROL_ARM
        row["flow_sha256"] = f"{row['seed']}-{count}"
        if not candidate:
            row["transition_prefix_watchdog"] = None
            continue
        row["controller"] = "membership_coalescing_v13"
        watchdog = {
            field: 0 for field in analyzer.TRANSITION_WATCHDOG_FIELDS_V13}
        watchdog["enabled"] = 1
        proof: dict[str, object] = {
            "independently_reconstructed": True,
            "budget_started": count > 4,
            "records": int(count > 4),
        }
        if count > 4:
            values = {
                "rounded_payload_budget_bytes": 100_000 + count,
                "packet_count": 100 + count,
                "header_per_packet": 90,
                "wire_bytes": 110_000 + count,
                "incumbent_count": 1,
                "occupancy_bps": 12_500_000_000,
                "capacity_bps": 100_000_000_000,
                "residual_bps": 87_500_000_000,
                "serialization_ns": 10_000 + count,
                "max_rtt_ns": 8320,
                "delay_ns": 18_320 + count,
                "start_ns": 100,
                "deadline_ns": 18_420 + count,
            }
            watchdog.update({"records": 1, **values})
            proof.update({
                **values,
                "raw_target_bytes_by_flow": {"4": 4096},
                "base_rtt_ns_by_flow": {str(flow): 8320
                                         for flow in range(count)},
            })
        row["transition_prefix_watchdog"] = watchdog
        evidence = row["generation_ack"]
        assert isinstance(evidence, dict)
        evidence["transition_prefix_watchdog"] = proof
    payload.update({
        "schema_version": 13,
        "spec_sha256": sha256_file(SPEC_V13_PATH),
    })
    return payload


def synthetic_analysis(spec: dict[str, object], effect_percent: float = 0.0,
                       spec_path: Path = SPEC_PATH) -> dict[str, object]:
    rows = []
    for seed, count, arm in runner.matrix(spec):
        scale = 1.0 + (effect_percent / 100.0 if arm != runner.CONTROL_ARM else 0.0)
        performance = {
            "mean_fct_us": 100.0 * scale,
            "completion_span_us": 110.0 * scale,
            "receiver_queue_time_sampled_mean_bytes": 10.0 * scale,
            "receiver_queue_time_sampled_p99_bytes": 20.0 * scale,
            "flow_goodput_jain": 1.0,
            "receiver_queue_samples": 100,
        }
        evidence = {
            "total_control_frames_sent": 0 if arm == runner.CONTROL_ARM else 2 * count,
            "pending_generations": 0, "retry_grants": 0, "stale_events": 0,
        }
        if spec["schema_version"] >= 7 and arm != runner.CONTROL_ARM:
            optional = spec["schema_version"] >= 8
            optional_flows = count // 2 if optional else 0
            details = [{
                "generation": 1, "ack_required": True,
                "classification": "join_or_decrease_ack_required",
                "joins": count,
                "previous_vector_active": None, "active_flows": count,
                "grant_frames_sent": count, "ack_frames_sent": count,
            }]
            if optional:
                details.append({
                    "generation": 2, "ack_required": False,
                    "classification": "pure_release_non_decreasing_ack_optional",
                    "joins": 0,
                    "previous_vector_active": count, "active_flows": optional_flows,
                    "grant_frames_sent": optional_flows, "ack_frames_sent": 0,
                })
            evidence.update({
                "total_control_frames_sent": 2 * count + optional_flows,
                "ack_required_batches": 1, "ack_optional_batches": int(optional),
                "ack_required_grants": count, "ack_optional_grants": optional_flows,
                "generation_zero_rejected": 0,
                "generation_mismatch_rejected": 0,
                "fully_acked_membership_batches": 1,
                "release_threshold_deferrals": int(optional),
                "initial_generation_flows": count,
                "initial_generation_send_timestamp_count": 1,
                "post_initial_registration_events": 0,
                "first_registration_ns": 10,
                "last_registration_ns": (
                    10 + (count - 1) * 1000 if spec["schema_version"] == 10
                    else 60_000),
                "first_flush_ns": (
                    16_650 + (count - 1) * 1000
                    if spec["schema_version"] == 10 else 62_410),
                "first_flush_minus_first_registration_ns": (
                    16_640 + (count - 1) * 1000
                    if spec["schema_version"] == 10 else 62_400),
                "first_flush_minus_last_registration_ns": (
                    16_640 if spec["schema_version"] == 10 else 2_410),
                "registration_span_ns": (
                    (count - 1) * 1000 if spec["schema_version"] == 10 else 59_990),
                "max_registration_gap_ns": (
                    1000 if spec["schema_version"] == 10 else 10_000),
                "generation_contract": details,
            })
        membership = {}
        if spec["schema_version"] == 9 and arm != runner.CONTROL_ARM:
            membership = {
                "initial_collection_starts": 1,
                "initial_collection_flushes": 1,
                "initial_collection_cancellations": 0,
                "initial_collection_wait_ns": 62_400,
                "initial_collection_max_wait_ns": 62_400,
                "max_batch": count,
            }
        elif spec["schema_version"] == 10 and arm != runner.CONTROL_ARM:
            first_delta = 16_640 + (count - 1) * 1000
            membership = {
                "initial_collection_quiet_ns": 16_640,
                "initial_collection_full_deadline": 0,
                "initial_collection_starts": 1,
                "initial_collection_flushes": 1,
                "initial_collection_deferred_changes": count - 1,
                "initial_collection_reschedules": count - 1,
                "initial_collection_cancellations": 0,
                "initial_collection_quiet_flushes": 1,
                "initial_collection_hard_flushes": 0,
                "initial_collection_wait_ns": first_delta,
                "initial_collection_max_wait_ns": first_delta,
                "max_batch": count,
            }
        rows.append({
            "seed": seed, "active_flows": count, "controller": arm,
            "flow_sha256": f"{seed}-{count}", "performance": performance,
            "generation_ack": evidence,
            "membership": membership,
            "pfc": {
                "pause_events": 2 if arm == runner.CONTROL_ARM else 0,
                "resume_events": 1 if arm == runner.CONTROL_ARM else 0,
                "matched_intervals": 1 if arm == runner.CONTROL_ARM else 0,
                "cumulative_pause_ns": 100 if arm == runner.CONTROL_ARM else 0,
                "max_pause_ns": 100 if arm == runner.CONTROL_ARM else 0,
            },
        })
    return {
        "schema_version": int(spec["schema_version"]),
        "status": "validated_full_matrix_performance_unsealed",
        "mechanism_admission_passed": True, "performance_unsealed": True,
        "run_count": 40, "spec_sha256": sha256_file(spec_path),
        "pilot_admission_sha256": "d" * 64, "runs": rows,
        "known_replay_admission_sha256": (
            "e" * 64 if spec["schema_version"] in (9, 10) else None),
    }


class RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = runner.read_spec(SPEC_PATH)

    def test_v6_matrix_and_explicit_ack_contract_are_exact(self) -> None:
        self.assertEqual(
            runner.holdout_axes(self.spec),
            ((96, 97, 98, 99, 100), (2, 4, 8, 15),
             ("control", "membership_coalescing_v6")))
        self.assertEqual(len(runner.matrix(self.spec)), 40)
        self.assertEqual(len(set(runner.matrix(self.spec))), 40)
        self.assertEqual(
            runner.pilot_matrix(self.spec),
            [(95, 15, "control"), (95, 15, "membership_coalescing_v6")])
        self.assertTrue(runner.execution_armed(self.spec))
        self.assertEqual(
            self.spec["candidate_pilot"]["first_grant_confirmation_frontier"],
            "explicit_generation_ack")

    def test_ack_contract_is_fail_closed(self) -> None:
        for field in runner.REQUIRED_ACK_EVIDENCE:
            changed = copy.deepcopy(self.spec)
            changed["holdout_execution"]["required_generation_ack_evidence"].remove(field)
            self.assertFalse(runner.execution_armed(changed), field)
        changed = copy.deepcopy(self.spec)
        changed["holdout_execution"]["delivery_evidence"] = "progress_confirmed"
        self.assertFalse(runner.execution_armed(changed))
        changed = copy.deepcopy(self.spec)
        changed["holdout_execution"]["pilot_admission_required"] = False
        self.assertFalse(runner.execution_armed(changed))

    def test_command_is_bulk_bounded_and_paired(self) -> None:
        traffic = {"seed": 96, "path": "/tmp/frozen-n15-s96.txt"}
        commands = {arm: runner.run_command(REPO, self.spec, traffic, arm)
                    for arm in runner.holdout_axes(self.spec)[2]}
        for command in commands.values():
            self.assertEqual(command[:3], ["env", "PYENV_VERSION=2.7.18", sys.executable])
            self.assertEqual(command[command.index("--monitor_profile") + 1], "bulk")
            self.assertEqual(command[command.index("--max_flows") + 1], "1000")
            self.assertEqual(command[command.index("--guard_grant_max_lines") + 1], "4096")
            self.assertEqual(command[command.index("--guard_lifecycle_max_lines") + 1], "32")
            self.assertEqual(command[command.index("--guard_proactive_release") + 1], "0")
        control = commands["control"]
        candidate = commands["membership_coalescing_v6"]
        self.assertEqual(control[control.index("--guard_membership_coalesce_ns") + 1], "0")
        self.assertEqual(candidate[candidate.index("--guard_membership_coalesce_ns") + 1], "12480")

    def test_preflight_records_sha_and_never_emits_performance(self) -> None:
        def fake_generate(_repo: Path, output: Path, manifest: Path, seed: int,
                          count: int, _pilot: object, _max_flows: int) -> None:
            output.write_text(
                f"{count}\n" + f"{seed % 15} 15 4 8388608 2.002{seed:03d}\n" * count,
                encoding="utf-8")
            manifest.write_text(json.dumps({"validation": {
                "status": "passed", "flow_count": count, "sha256": sha256_file(output)}}),
                encoding="utf-8")

        with tempfile.TemporaryDirectory() as temporary:
            campaign = Path(temporary) / "campaign"
            with mock.patch.object(runner, "_generate_flow", side_effect=fake_generate), \
                    mock.patch.object(runner, "clean_revision", return_value="a" * 40):
                preflight = runner.preflight(SPEC_PATH, self.spec, REPO, campaign)
            self.assertEqual(preflight["run_count"], 40)
            self.assertEqual(preflight["pilot_run_count"], 2)
            self.assertEqual(len(preflight["traffic"]), 21)
            self.assertFalse(preflight["formal_execution_armed"])
            self.assertTrue(preflight["spec_execution_armed"])
            self.assertEqual(preflight["simulator_git_sha"], "a" * 40)
            serialized = json.dumps(preflight)
            self.assertNotIn("mean_fct", serialized)
            self.assertNotIn("queue_p99", serialized)

    def test_sha_mismatch_stops_before_process_creation(self) -> None:
        preflight = {"formal_execution_armed": False, "simulator_git_sha": "a" * 40}
        with mock.patch.object(runner, "clean_revision", return_value="b" * 40), \
                mock.patch.object(runner, "execute_limited") as execute:
            with self.assertRaisesRegex(CampaignError, "Git SHA changed"):
                runner.execute(
                    SPEC_PATH, self.spec, preflight, REPO, Path("/tmp/unused"), False, "pilot")
        execute.assert_not_called()

    def test_formal_run_is_blocked_until_machine_pilot_seal(self) -> None:
        preflight = {"formal_execution_armed": False, "simulator_git_sha": "a" * 40}
        with mock.patch.object(runner, "execute_limited") as execute:
            with self.assertRaisesRegex(CampaignError, "blocked until the pilot"):
                runner.execute(
                    SPEC_PATH, self.spec, preflight, REPO, Path("/tmp/unused"), False,
                    "holdout")
        execute.assert_not_called()

    def test_passing_pilot_artifact_is_hash_bound_into_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            campaign = Path(temporary)
            preflight_path = campaign / "preflight.json"
            preflight = {
                "schema_version": 6, "formal_execution_armed": False,
                "pilot_admission": None, "simulator_git_sha": "a" * 40,
            }
            runner.write_json(preflight_path, preflight)
            admission = {
                "schema_version": 6, "scope": "pilot", "status": "admitted",
                "passed": True, "performance_emitted": False, "run_count": 2,
                "pilot_preflight_sha256": sha256_file(preflight_path),
                "spec_sha256": sha256_file(SPEC_PATH),
                "simulator_git_sha": "a" * 40,
            }
            artifact = campaign / "pilot_admission.json"
            runner.write_json(artifact, admission)
            with mock.patch.object(
                    runner, "recompute_pilot_admission", return_value=admission):
                sealed = runner.seal_pilot(
                    SPEC_PATH, self.spec, preflight, campaign, artifact)
            self.assertTrue(sealed["formal_execution_armed"])
            self.assertEqual(sealed["pilot_admission"]["sha256"], sha256_file(artifact))
            runner.validate_pilot_admission(SPEC_PATH, self.spec, sealed, campaign)
            admission["passed"] = False
            runner.write_json(artifact, admission)
            with self.assertRaisesRegex(CampaignError, "changed after formal arming"):
                runner.validate_pilot_admission(SPEC_PATH, self.spec, sealed, campaign)

    def test_hand_written_pass_cannot_arm_formal_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            campaign = Path(temporary)
            preflight = {
                "schema_version": 6, "formal_execution_armed": False,
                "pilot_admission": None, "simulator_git_sha": "a" * 40,
            }
            runner.write_json(campaign / "preflight.json", preflight)
            forged = {
                "schema_version": 6, "scope": "pilot", "status": "admitted",
                "passed": True, "performance_emitted": False, "run_count": 2,
                "pilot_preflight_sha256": sha256_file(campaign / "preflight.json"),
                "spec_sha256": sha256_file(SPEC_PATH),
                "simulator_git_sha": "a" * 40,
            }
            artifact = campaign / "pilot_admission.json"
            runner.write_json(artifact, forged)
            with mock.patch.object(
                    runner, "recompute_pilot_admission",
                    return_value={**forged, "mechanism_admission_passed": True}):
                with self.assertRaisesRegex(CampaignError, "fresh machine revalidation"):
                    runner.seal_pilot(
                        SPEC_PATH, self.spec, preflight, campaign, artifact)

    def test_cli_has_no_subset_or_parallel_escape(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            for extra in (("--max-runs", "1"), ("--workers", "4"), ("--arm", "control")):
                with self.assertRaises(SystemExit):
                    runner.parse_args([
                        str(SPEC_PATH), "--campaign-dir", "/tmp/x", "--phase", "run", *extra])


class V7RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = runner.read_spec(SPEC_V7_PATH)

    def test_v7_axes_contract_and_v6_parameters_are_frozen(self) -> None:
        self.assertEqual(
            runner.holdout_axes(self.spec),
            ((102, 103, 104, 105, 106), (2, 4, 8, 15),
             ("control", "membership_coalescing_v7")))
        self.assertEqual(
            runner.pilot_matrix(self.spec),
            [(101, 15, "control"), (101, 15, "membership_coalescing_v7")])
        self.assertEqual(len(runner.matrix(self.spec)), 40)
        self.assertTrue(runner.execution_armed(self.spec))
        pilot = self.spec["candidate_pilot"]
        self.assertEqual(pilot["membership_quiet_ns"]["membership_coalescing_v7"], 12480)
        self.assertEqual(pilot["grant_reliability_service_rounds"]
                         ["membership_coalescing_v7"], 2.0)
        self.assertEqual(pilot["flow_bytes"], 8388608)

    def test_selective_contract_is_fail_closed(self) -> None:
        changed = copy.deepcopy(self.spec)
        changed["holdout_execution"]["required_generation_ack_evidence"].remove(
            "ack_required")
        self.assertFalse(runner.execution_armed(changed))
        changed = copy.deepcopy(self.spec)
        changed["holdout_execution"]["delivery_evidence"] = (
            "per_membership_generation_grant_ack")
        self.assertFalse(runner.execution_armed(changed))

    def test_current_base_passes_exact_capability_probe(self) -> None:
        runner.assert_simulator_capability(REPO, self.spec)
        with tempfile.TemporaryDirectory() as temporary:
            fake_repo = Path(temporary)
            source = fake_repo / "scratch" / "network-load-balance.cc"
            source.parent.mkdir(parents=True)
            tokens = list(self.spec["simulator_capability_probe"]["required_tokens"])
            source.write_text("\n".join(tokens[:-1]), encoding="utf-8")
            with self.assertRaisesRegex(CampaignError, "capability is absent"):
                runner.assert_simulator_capability(fake_repo, self.spec)


class V8RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = runner.read_spec(SPEC_V8_PATH)

    def test_v8_axes_base_and_v7_parameters_are_frozen(self) -> None:
        self.assertEqual(
            runner.holdout_axes(self.spec),
            ((108, 109, 110, 111, 112), (2, 4, 8, 15),
             ("control", "membership_coalescing_v8")))
        self.assertEqual(
            runner.pilot_matrix(self.spec),
            [(107, 15, "control"), (107, 15, "membership_coalescing_v8")])
        self.assertEqual(len(runner.matrix(self.spec)), 40)
        self.assertTrue(runner.execution_armed(self.spec))
        self.assertEqual(
            self.spec["generation_ack_protocol"]["minimum_simulator_commit"],
            "51cabdd8b61d45c0c43455665212d6f23443bb32")
        self.assertEqual(
            self.spec["scalability_holdout"]["paired_non_regression"],
            runner.read_spec(SPEC_V7_PATH)["scalability_holdout"]
            ["paired_non_regression"])

    def test_proactive_release_is_explicitly_zero_and_mutation_fails_closed(self) -> None:
        traffic = {"seed": 107, "path": "/tmp/frozen-n15-s107.txt"}
        command = runner.run_command(
            REPO, self.spec, traffic, "membership_coalescing_v8")
        self.assertEqual(command[command.index("--guard_proactive_release") + 1], "0")
        changed = copy.deepcopy(self.spec)
        changed["candidate_pilot"]["guard_proactive_release"] = 1
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "invalid-v8.json"
            path.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaisesRegex(CampaignError, "candidate_pilot changed"):
                runner.read_spec(path)

    def test_current_base_has_geometric_and_proactive_safety_capabilities(self) -> None:
        runner.assert_simulator_capability(REPO, self.spec)
        with tempfile.TemporaryDirectory() as temporary:
            fake_repo = Path(temporary)
            source = fake_repo / "scratch" / "network-load-balance.cc"
            source.parent.mkdir(parents=True)
            tokens = list(self.spec["simulator_capability_probe"]["required_tokens"])
            source.write_text("\n".join(tokens[:-1]), encoding="utf-8")
            with self.assertRaisesRegex(CampaignError, "capability is absent"):
                runner.assert_simulator_capability(fake_repo, self.spec)


class V9RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = runner.read_spec(SPEC_V9_PATH)

    def test_v9_replay_pilot_formal_axes_and_deadline_are_exact(self) -> None:
        self.assertEqual(
            runner.replay_matrix(self.spec),
            [(108, 15, "membership_coalescing_v9")])
        self.assertEqual(
            runner.pilot_matrix(self.spec),
            [(113, 15, "control"), (113, 15, "membership_coalescing_v9")])
        self.assertEqual(
            runner.holdout_axes(self.spec),
            ((114, 115, 116, 117, 118), (2, 4, 8, 15),
             ("control", "membership_coalescing_v9")))
        self.assertEqual(len(runner.matrix(self.spec)), 40)
        self.assertEqual(
            self.spec["known_failure_replay"]["flow_sha256"],
            "1993661ded923372651ee25a4d9e5e2fb18cad0b0c5182edb758e71d80abc7a9")
        self.assertEqual(
            self.spec["initial_collection_protocol"]["derived_wait_ns"],
            5 * 12_480)
        self.assertEqual(
            self.spec["generation_ack_protocol"]["minimum_simulator_commit"],
            "92c93e2917242b1cb17c25971f50bf9f9df641d8")
        self.assertTrue(runner.execution_armed(self.spec))

    def test_v9_contract_and_wait_are_fail_closed(self) -> None:
        changed = copy.deepcopy(self.spec)
        changed["holdout_execution"]["required_generation_ack_evidence"].remove(
            "first_flush_ns")
        self.assertFalse(runner.execution_armed(changed))
        changed = copy.deepcopy(self.spec)
        changed["initial_collection_protocol"]["derived_wait_ns"] = 12_480
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "changed.json"
            path.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaisesRegex(CampaignError, "initial-collection protocol changed"):
                runner.read_spec(path)

    def test_v9_preflight_generates_exact_sealed_replay_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            campaign = Path(temporary) / "campaign"
            with mock.patch.object(
                    runner, "clean_revision",
                    return_value="92c93e2917242b1cb17c25971f50bf9f9df641d8"):
                preflight = runner.preflight(
                    SPEC_V9_PATH, self.spec, REPO, campaign)
            replay = next(row for row in preflight["traffic"]
                          if row["scope"] == "replay")
            self.assertEqual(replay["sha256"],
                             self.spec["known_failure_replay"]["flow_sha256"])
            self.assertEqual(preflight["replay_run_count"], 1)
            self.assertEqual(preflight["pilot_run_count"], 2)
            self.assertEqual(preflight["run_count"], 40)
            self.assertEqual(len(preflight["traffic"]), 22)
            self.assertFalse(preflight["pilot_execution_armed"])
            self.assertFalse(preflight["formal_execution_armed"])

    def test_replay_seal_is_hash_bound_before_pilot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            campaign = Path(temporary)
            preflight = {
                "schema_version": 9, "pilot_execution_armed": False,
                "formal_execution_armed": False,
                "known_replay_admission": None, "pilot_admission": None,
                "simulator_git_sha": "9" * 40,
            }
            runner.write_json(campaign / "preflight.json", preflight)
            admission = {
                "schema_version": 9, "scope": "replay", "status": "admitted",
                "passed": True, "performance_emitted": False, "run_count": 1,
                "replay_preflight_sha256": sha256_file(campaign / "preflight.json"),
                "spec_sha256": sha256_file(SPEC_V9_PATH),
                "simulator_git_sha": "9" * 40,
            }
            artifact = campaign / "known_replay_admission_v9.json"
            runner.write_json(artifact, admission)
            with mock.patch.object(
                    runner, "recompute_replay_admission", return_value=admission):
                sealed = runner.seal_replay(
                    SPEC_V9_PATH, self.spec, preflight, campaign, artifact)
            self.assertTrue(sealed["pilot_execution_armed"])
            self.assertFalse(sealed["formal_execution_armed"])
            runner.validate_replay_admission(
                SPEC_V9_PATH, self.spec, sealed, campaign)
            admission["passed"] = False
            runner.write_json(artifact, admission)
            with self.assertRaisesRegex(CampaignError, "changed after pilot arming"):
                runner.validate_replay_admission(
                    SPEC_V9_PATH, self.spec, sealed, campaign)

    def test_pilot_is_blocked_before_replay_seal(self) -> None:
        preflight = {
            "pilot_execution_armed": False, "formal_execution_armed": False,
            "known_replay_admission": None, "simulator_git_sha": "9" * 40,
        }
        with mock.patch.object(runner, "execute_limited") as execute:
            with self.assertRaisesRegex(CampaignError, "blocked until the replay"):
                runner.execute(
                    SPEC_V9_PATH, self.spec, preflight, REPO,
                    Path("/tmp/unused-v9"), False, "pilot")
        execute.assert_not_called()


class V10RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = runner.read_spec(SPEC_V10_PATH)

    def test_v10_axes_topology_derived_timers_and_minimum_are_exact(self) -> None:
        self.assertEqual(
            runner.replay_matrix(self.spec),
            [(108, 15, "membership_coalescing_v10")])
        self.assertEqual(
            runner.pilot_matrix(self.spec),
            [(119, 15, "control"), (119, 15, "membership_coalescing_v10")])
        self.assertEqual(
            runner.holdout_axes(self.spec),
            ((120, 121, 122, 123, 124), (2, 4, 8, 15),
             ("control", "membership_coalescing_v10")))
        protocol = self.spec["initial_collection_protocol"]
        self.assertEqual(protocol["quiet_ns"], 2 * protocol["base_cross_leaf_rtt_ns"])
        self.assertEqual(protocol["derived_hard_ns"], 5 * protocol["quiet_ns"])
        self.assertEqual(
            self.spec["generation_ack_protocol"]["minimum_simulator_commit"],
            "dc252d964a64617065a74ec9dc9d31a4b5339b21")
        self.assertTrue(runner.execution_armed(self.spec))
        runner.assert_simulator_capability(REPO, self.spec)

    def test_v10_command_separates_initial_and_post_initial_windows(self) -> None:
        traffic = {"seed": 108, "path": "/tmp/frozen-v10-replay.txt"}
        candidate = runner.run_command(
            REPO, self.spec, traffic, "membership_coalescing_v10")
        self.assertEqual(
            candidate[candidate.index("--guard_membership_coalesce_ns") + 1], "12480")
        self.assertEqual(
            candidate[candidate.index("--guard_initial_collection_quiet_ns") + 1],
            "16640")
        self.assertEqual(
            candidate[candidate.index("--guard_initial_collection_full_deadline") + 1],
            "0")
        v9 = runner.read_spec(SPEC_V9_PATH)
        legacy = runner.run_command(REPO, v9, traffic, "membership_coalescing_v9")
        self.assertNotIn("--guard_initial_collection_quiet_ns", legacy)
        self.assertNotIn("--guard_initial_collection_full_deadline", legacy)

    def test_v10_mode_and_flush_reason_evidence_are_fail_closed(self) -> None:
        changed = copy.deepcopy(self.spec)
        changed["candidate_pilot"]["initial_collection_full_deadline"][
            "membership_coalescing_v10"] = 1
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "changed-v10.json"
            path.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaisesRegex(CampaignError, "candidate_pilot changed"):
                runner.read_spec(path)
        changed = copy.deepcopy(self.spec)
        changed["holdout_execution"]["required_generation_ack_evidence"].remove(
            "initial_collection_hard_flushes")
        self.assertFalse(runner.execution_armed(changed))

    def test_v10_preflight_reuses_exact_replay_and_freezes_43_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            campaign = Path(temporary) / "campaign"
            with mock.patch.object(
                    runner, "clean_revision",
                    return_value="dc252d964a64617065a74ec9dc9d31a4b5339b21"):
                preflight = runner.preflight(
                    SPEC_V10_PATH, self.spec, REPO, campaign)
            replay = next(row for row in preflight["traffic"]
                          if row["scope"] == "replay")
            self.assertEqual(
                replay["sha256"],
                "1993661ded923372651ee25a4d9e5e2fb18cad0b0c5182edb758e71d80abc7a9")
            self.assertEqual(preflight["replay_run_count"], 1)
            self.assertEqual(preflight["pilot_run_count"], 2)
            self.assertEqual(preflight["run_count"], 40)
            self.assertEqual(len(preflight["traffic"]), 22)
            self.assertFalse(preflight["pilot_execution_armed"])
            self.assertFalse(preflight["formal_execution_armed"])


class V11RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = runner.read_spec(SPEC_V11_PATH)

    def test_v11_replay_pilot_formal_and_corrected_caps_are_exact(self) -> None:
        self.assertEqual(
            runner.replay_matrix(self.spec),
            [(120, count, "membership_coalescing_v11")
             for count in (2, 4, 8, 15)])
        self.assertEqual(
            runner.pilot_matrix(self.spec),
            [(125, count, arm) for count in (4, 15)
             for arm in ("control", "membership_coalescing_v11")])
        self.assertEqual(
            runner.holdout_axes(self.spec),
            ((126, 127, 128, 129, 130), (2, 4, 8, 15),
             ("control", "membership_coalescing_v11")))
        self.assertEqual(
            self.spec["candidate_admission"]["frame_bounds"],
            {"2": 7, "4": 26, "8": 43, "15": 64})
        self.assertEqual(
            self.spec["safe_activation_protocol"]["minimum_simulator_commit"],
            "946cbbaa418d71580f747e10ca164fc7e041538f")
        self.assertTrue(runner.execution_armed(self.spec))
        runner.assert_simulator_capability(REPO, self.spec)

    def test_v11_command_isolates_small_set_candidate(self) -> None:
        traffic = {"seed": 125, "path": "/tmp/frozen-v11-pilot.txt"}
        control = runner.run_command(REPO, self.spec, traffic, "control")
        candidate = runner.run_command(
            REPO, self.spec, traffic, "membership_coalescing_v11")
        for command, limit, initial, membership in (
                (control, "0", "0", "0"),
                (candidate, "4", "16640", "12480")):
            self.assertEqual(
                command[command.index("--guard_small_set_fastpath_limit") + 1], limit)
            self.assertEqual(
                command[command.index("--guard_initial_collection_quiet_ns") + 1], initial)
            self.assertEqual(
                command[command.index("--guard_membership_coalesce_ns") + 1], membership)
        self.assertEqual(candidate[
            candidate.index("--guard_initial_collection_full_deadline") + 1], "0")

    def test_v11_frame_cap_and_phase_contract_are_fail_closed(self) -> None:
        changed = copy.deepcopy(self.spec)
        changed["candidate_admission"]["frame_bounds"]["4"] = 23
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "changed.json"
            path.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaisesRegex(CampaignError, "phase/frame admission"):
                runner.read_spec(path)
        changed = copy.deepcopy(self.spec)
        changed["holdout_execution"]["required_generation_ack_evidence"].remove(
            "transaction_id")
        self.assertFalse(runner.execution_armed(changed))

    def test_v11_preflight_freezes_26_inputs_and_four_replay_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            campaign = Path(temporary) / "campaign"
            with mock.patch.object(
                    runner, "clean_revision",
                    return_value="946cbbaa418d71580f747e10ca164fc7e041538f"):
                preflight = runner.preflight(
                    SPEC_V11_PATH, self.spec, REPO, campaign)
        self.assertEqual(preflight["replay_run_count"], 4)
        self.assertEqual(preflight["pilot_run_count"], 4)
        self.assertEqual(preflight["run_count"], 40)
        self.assertEqual(len(preflight["traffic"]), 26)
        observed = {int(row["active_flows"]): row["sha256"]
                    for row in preflight["traffic"] if row["scope"] == "replay"}
        expected = {int(row["active_flows"]): row["flow_sha256"]
                    for row in self.spec["known_failure_replay"]["identities"]}
        self.assertEqual(observed, expected)
        self.assertFalse(preflight["pilot_execution_armed"])
        self.assertFalse(preflight["formal_execution_armed"])

    def test_v11_replay_seal_requires_joint_four_run_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            campaign = Path(temporary)
            artifact = campaign / "known_replay_admission_v11.json"
            artifact.write_text(json.dumps({
                "schema_version": 11, "scope": "replay", "status": "admitted",
                "passed": True, "performance_emitted": False, "run_count": 1,
                "spec_sha256": sha256_file(SPEC_V11_PATH),
                "simulator_git_sha": "9" * 40,
            }), encoding="utf-8")
            preflight = {
                "pilot_execution_armed": True,
                "known_replay_admission": {
                    "path": str(artifact.resolve()), "sha256": sha256_file(artifact)},
                "simulator_git_sha": "9" * 40,
            }
            with self.assertRaisesRegex(CampaignError, "valid V11 admission"):
                runner.validate_replay_admission(
                    SPEC_V11_PATH, self.spec, preflight, campaign)


class V12RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = runner.read_spec(SPEC_V12_PATH)

    def test_v12_identities_flags_and_resource_bounds_are_exact(self) -> None:
        self.assertEqual(
            runner.replay_matrix(self.spec),
            [(126, count, "membership_coalescing_v12")
             for count in (2, 4, 8, 15)])
        self.assertEqual(
            runner.pilot_matrix(self.spec),
            [(131, count, arm) for count in (4, 15)
             for arm in ("control", "membership_coalescing_v12")])
        self.assertEqual(
            runner.holdout_axes(self.spec),
            ((132, 133, 134, 135, 136), (2, 4, 8, 15),
             ("control", "membership_coalescing_v12")))
        self.assertEqual(
            self.spec["safe_activation_protocol"]["minimum_simulator_commit"],
            "e5a57fa399c8ee4b5d3190d172771343baace632")
        self.assertEqual(
            self.spec["candidate_admission"]["frame_bounds"],
            {"2": 7, "4": 26, "8": 43, "15": 64})
        self.assertTrue(runner.execution_armed(self.spec))
        runner.assert_simulator_capability(REPO, self.spec)

        traffic = {"seed": 131, "path": "/tmp/frozen-v12-pilot.txt"}
        control = runner.run_command(REPO, self.spec, traffic, "control")
        candidate = runner.run_command(
            REPO, self.spec, traffic, "membership_coalescing_v12")
        for command, small, prefix in (
                (control, "0", "0"), (candidate, "4", "1")):
            self.assertEqual(command[
                command.index("--guard_small_set_fastpath_limit") + 1], small)
            self.assertEqual(command[
                command.index("--guard_transition_prefix_barrier") + 1], prefix)

    def test_v12_preflight_regenerates_all_four_frozen_replay_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            campaign = Path(temporary) / "campaign"
            with mock.patch.object(
                    runner, "clean_revision",
                    return_value="e5a57fa399c8ee4b5d3190d172771343baace632"):
                preflight = runner.preflight(
                    SPEC_V12_PATH, self.spec, REPO, campaign)
        self.assertEqual(preflight["replay_run_count"], 4)
        self.assertEqual(preflight["pilot_run_count"], 4)
        self.assertEqual(preflight["run_count"], 40)
        self.assertEqual(len(preflight["traffic"]), 26)
        observed = {int(row["active_flows"]): row["sha256"]
                    for row in preflight["traffic"] if row["scope"] == "replay"}
        expected = {int(row["active_flows"]): row["flow_sha256"]
                    for row in self.spec["known_failure_replay"]["identities"]}
        self.assertEqual(observed, expected)
        self.assertFalse(preflight["pilot_execution_armed"])
        self.assertFalse(preflight["formal_execution_armed"])

    def test_v12_gate_seed_prefix_and_execution_drift_fail_closed(self) -> None:
        mutations = []
        changed = copy.deepcopy(self.spec)
        changed["candidate_pilot"]["transition_prefix_barrier"][
            "membership_coalescing_v12"] = 0
        mutations.append(changed)
        changed = copy.deepcopy(self.spec)
        changed["scalability_holdout"]["seeds"][0] = 131
        mutations.append(changed)
        changed = copy.deepcopy(self.spec)
        changed["scalability_holdout"]["n15_per_seed_stability"][
            "receiver_queue_mean_percent_max"] = -9.0
        mutations.append(changed)
        changed = copy.deepcopy(self.spec)
        changed["candidate_admission"]["fallback_batches_max"] = 1
        mutations.append(changed)
        for ordinal, payload in enumerate(mutations):
            with self.subTest(ordinal=ordinal), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "changed.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(CampaignError):
                    runner.read_spec(path)
        changed = copy.deepcopy(self.spec)
        changed["holdout_execution"]["required_generation_ack_evidence"].remove(
            "prefix_target_bytes")
        self.assertFalse(runner.execution_armed(changed))


class V13RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = runner.read_spec(SPEC_V13_PATH)

    def test_v13_identities_dual_flags_and_resource_bounds_are_exact(self) -> None:
        self.assertEqual(
            runner.replay_matrix(self.spec),
            [(126, count, "membership_coalescing_v13")
             for count in (2, 4, 8, 15)])
        self.assertEqual(
            runner.pilot_matrix(self.spec),
            [(137, count, arm) for count in (4, 15)
             for arm in ("control", "membership_coalescing_v13")])
        self.assertEqual(
            runner.holdout_axes(self.spec),
            ((138, 139, 140, 141, 142), (2, 4, 8, 15),
             ("control", "membership_coalescing_v13")))
        self.assertEqual(
            self.spec["resource_limits"], {
                "max_flows": 1000, "grant_trace_max_lines": 4096,
                "lifecycle_trace_max_lines": 32,
                "artifact_bytes_per_run": 16_777_216,
                "wall_time_seconds_per_run": 1800,
                "rss_kib_per_run": 8_388_608,
                "campaign_bytes": 1_073_741_824,
            })
        self.assertTrue(runner.execution_armed(self.spec))
        runner.assert_simulator_capability(REPO, self.spec)
        traffic = {"seed": 137, "path": "/tmp/frozen-v13-pilot.txt"}
        for arm, expected in (("control", "0"),
                              ("membership_coalescing_v13", "1")):
            command = runner.run_command(REPO, self.spec, traffic, arm)
            self.assertEqual(command[
                command.index("--guard_transition_prefix_barrier") + 1],
                expected)
            self.assertEqual(command[
                command.index("--guard_transition_prefix_wire_watchdog") + 1],
                expected)

    def test_v13_preflight_freezes_four_replay_four_pilot_and_forty_formal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            campaign = Path(temporary) / "campaign"
            with mock.patch.object(
                    runner, "clean_revision",
                    return_value=self.spec[
                        "safe_activation_protocol"]["minimum_simulator_commit"]):
                preflight = runner.preflight(
                    SPEC_V13_PATH, self.spec, REPO, campaign)
        self.assertEqual(preflight["replay_run_count"], 4)
        self.assertEqual(preflight["pilot_run_count"], 4)
        self.assertEqual(preflight["run_count"], 40)
        self.assertEqual(len(preflight["traffic"]), 26)
        self.assertFalse(preflight["pilot_execution_armed"])
        self.assertFalse(preflight["formal_execution_armed"])

    def test_v13_watchdog_seed_gate_and_execution_drift_fail_closed(self) -> None:
        mutations = []
        for path, value in (
                (("candidate_pilot", "transition_prefix_wire_watchdog",
                  "membership_coalescing_v13"), 0),
                (("scalability_holdout", "seeds", 0), 137),
                (("candidate_admission", "watchdog_records_required"), 2),
                (("transition_prefix_watchdog_protocol", "data_header_bytes"), 89),
                (("transition_prefix_watchdog_protocol", "known_replay_n8",
                  "wire_bytes"), 793519),
                (("safe_activation_protocol", "minimum_simulator_commit"),
                 "0" * 40),
                (("safe_activation_protocol", "policy"), "weakened")):
            changed = copy.deepcopy(self.spec)
            target: object = changed
            for key in path[:-1]:
                target = target[key]  # type: ignore[index]
            target[path[-1]] = value  # type: ignore[index]
            mutations.append(changed)
        for ordinal, payload in enumerate(mutations):
            with self.subTest(ordinal=ordinal), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "changed.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(CampaignError):
                    runner.read_spec(path)
        for token in (
                "GUARD_TRANSITION_PREFIX_WIRE_WATCHDOG",
                "guard_transition_prefix_watchdog enabled",
                "records %lu non_reconstructable %u inconsistent %u"):
            changed = copy.deepcopy(self.spec)
            changed["simulator_capability_probe"]["required_tokens"].remove(token)
            with self.subTest(token=token), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "changed.json"
                path.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaises(CampaignError):
                    runner.read_spec(path)
        changed = copy.deepcopy(self.spec)
        changed["holdout_execution"]["required_generation_ack_evidence"].remove(
            "guard_transition_prefix_watchdog")
        self.assertFalse(runner.execution_armed(changed))


class AnalyzerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = runner.read_spec(SPEC_PATH)

    def test_generation_four_event_closure_passes(self) -> None:
        stats, membership = generation_stats()
        result = analyzer.generation_audit(
            candidate_trace(), stats, membership, 2, "membership_coalescing_v6",
            15, {0, 1}, 1_000_000)
        self.assertEqual(result["grant_generations"], [1])
        self.assertEqual(result["total_control_frames_sent"], 4)
        self.assertEqual(result["pending_generations"], 0)

    def test_v7_join_closes_and_pure_release_elides_ack(self) -> None:
        stats, membership = selective_stats()
        result = analyzer.generation_audit(
            selective_trace(), stats, membership, 2, "membership_coalescing_v7",
            15, {0, 1}, 1_000_000, 7)
        self.assertEqual(result["ack_required_batches"], 1)
        self.assertEqual(result["ack_optional_batches"], 1)
        self.assertEqual(result["total_control_frames_sent"], 5)
        self.assertEqual(
            [row["classification"] for row in result["generation_contract"]],
            ["join_or_decrease_ack_required",
             "pure_release_non_decreasing_ack_optional"])

    def test_v7_join_cannot_elide_ack(self) -> None:
        stats, membership = selective_stats()
        rows = selective_trace()
        for row in rows:
            if row["generation"] == 1:
                row["ack_required"] = 0
        with self.assertRaisesRegex(SummaryError, "join/decrease policy"):
            analyzer.generation_audit(
                rows, stats, membership, 2, "membership_coalescing_v7",
                15, {0, 1}, 1_000_000, 7)

    def test_v7_decrease_generation_requires_full_ack_closure(self) -> None:
        rows = candidate_trace(generations=2)
        for row in rows:
            row["ack_required"] = 1
            if row["generation"] == 2 and row["event"] in ("sent", "received"):
                row["grant_rate_bps"] = 40_000_000_000
        events = {event: [row for row in rows if row["event"] == event]
                  for event in analyzer.TRACE_EVENTS}
        membership = {
            "generation": 2, "batches": 2, "ack_required_batches": 2,
            "ack_optional_batches": 0, "ack_required_grants": 4,
            "ack_optional_grants": 0, "fully_acked_batches": 2,
        }
        _generations, details = analyzer.selective_generation_closure(
            events, membership)
        self.assertEqual(details[1]["decreases"], 2)
        missing = [row for row in rows
                   if not (row["generation"] == 2 and row["event"] == "ack_received" and
                           row["flow_id"] == 1)]
        events = {event: [row for row in missing if row["event"] == event]
                  for event in analyzer.TRACE_EVENTS}
        with self.assertRaisesRegex(SummaryError, "does not fully close"):
            analyzer.selective_generation_closure(events, membership)

    def test_v7_optional_release_rejects_ack_pending_and_bad_counters(self) -> None:
        stats, membership = selective_stats()
        rows = selective_trace()
        rows[-2]["pending_acks"] = 1
        with self.assertRaisesRegex(SummaryError, "pending ACK state"):
            analyzer.generation_audit(
                rows, stats, membership, 2, "membership_coalescing_v7",
                15, {0, 1}, 1_000_000, 7)
        rows = selective_trace()
        stray = trace_row(120, "ack_sent", 1, 2)
        stray["ack_required"] = 0
        rows.append(stray)
        with self.assertRaisesRegex(SummaryError, "emitted ACK frames"):
            analyzer.generation_audit(
                rows, stats, membership, 2, "membership_coalescing_v7",
                15, {0, 1}, 1_000_000, 7)
        changed = copy.deepcopy(membership)
        changed["ack_optional_batches"] = 0
        with self.assertRaisesRegex(SummaryError, "batch counters"):
            analyzer.generation_audit(
                selective_trace(), stats, changed, 2, "membership_coalescing_v7",
                15, {0, 1}, 1_000_000, 7)

    def test_v8_geometric_8_3_1_trace_closes_with_twenty_frames(self) -> None:
        stats, membership = geometric_stats_n8()
        result = analyzer.generation_audit(
            geometric_trace_n8(), stats, membership, 8,
            "membership_coalescing_v8", 15, set(range(8)), 1_000_000, 8,
            {flow_id: 0 for flow_id in range(8)})
        self.assertEqual(result["total_control_frames_sent"], 20)
        self.assertEqual(result["initial_generation_flows"], 8)
        self.assertEqual(result["post_initial_registration_events"], 0)
        self.assertEqual(
            [row["active_flows"] for row in result["generation_contract"]],
            [8, 3, 1])
        self.assertEqual(
            [row["previous_vector_active"]
             for row in result["generation_contract"]],
            [None, 8, 3])

    def test_v8_rejects_non_geometric_release(self) -> None:
        stats, membership = geometric_stats_n8()
        membership = copy.deepcopy(membership)
        stats = copy.deepcopy(stats)
        membership["ack_optional_grants"] = 6
        stats["grants_sent"] = stats["grants_received"] = 14
        stats["grant_bytes_sent"] = 840
        with self.assertRaisesRegex(SummaryError, "exceeds floor"):
            analyzer.generation_audit(
                geometric_trace_n8((5, 1)), stats, membership, 8,
                "membership_coalescing_v8", 15, set(range(8)), 1_000_000, 8,
                {flow_id: 0 for flow_id in range(8)})

    def test_v8_rejects_incomplete_initial_generation_and_late_registration(self) -> None:
        stats, membership = geometric_stats_n8()
        rows = [row for row in geometric_trace_n8()
                if not (row["generation"] == 1 and row["flow_id"] == 7)]
        for row in rows:
            if row["generation"] == 1:
                row["active_flows"] = 7
                if row["event"] == "ack_received" and row["flow_id"] == 6:
                    row["pending_acks"] = 0
        changed_stats = dict(stats, grants_sent=11, grants_received=11,
                             grant_bytes_sent=660)
        changed_membership = dict(
            membership, ack_required_grants=7, ack_sent=7, ack_received=7,
            ack_bytes_sent=420, ack_bytes_received=420)
        with self.assertRaisesRegex(SummaryError, "initial generation covers 7 flows"):
            analyzer.generation_audit(
                rows, changed_stats, changed_membership, 8,
                "membership_coalescing_v8", 15, set(range(8)), 1_000_000, 8,
                {flow_id: 0 for flow_id in range(8)})
        with self.assertRaisesRegex(SummaryError, "registration.*after initial"):
            analyzer.generation_audit(
                geometric_trace_n8(), stats, membership, 8,
                "membership_coalescing_v8", 15, set(range(8)), 1_000_000, 8,
                {**{flow_id: 0 for flow_id in range(8)}, 7: 1000})

    def test_v9_fixed_initial_flush_timing_and_geometric_closure(self) -> None:
        stats, membership = geometric_stats_n8()
        registrations = {flow_id: 10 + flow_id * 1000 for flow_id in range(8)}
        result = analyzer.generation_audit(
            v9_geometric_trace_n8(), stats, membership, 8,
            "membership_coalescing_v9", 15, set(range(8)), 1_000_000, 9,
            registrations)
        self.assertEqual(result["initial_generation_flows"], 8)
        self.assertEqual(result["initial_generation_send_timestamp_count"], 1)
        self.assertEqual(result["first_registration_ns"], 10)
        self.assertEqual(result["first_flush_ns"], 62_410)
        self.assertEqual(result["first_flush_minus_first_registration_ns"], 62_400)
        self.assertEqual(result["last_registration_ns"], 7_010)
        self.assertEqual(result["max_registration_gap_ns"], 1000)
        self.assertEqual(result["total_control_frames_sent"], 20)

    def test_v9_rejects_early_flush_and_split_initial_send_timestamp(self) -> None:
        stats, membership = geometric_stats_n8()
        registrations = {flow_id: 10 + flow_id for flow_id in range(8)}
        rows = v9_geometric_trace_n8()
        rows[0]["time_ns"] = 62_409
        with self.assertRaisesRegex(SummaryError, "one send timestamp"):
            analyzer.generation_audit(
                rows, stats, membership, 8, "membership_coalescing_v9",
                15, set(range(8)), 1_000_000, 9, registrations)
        registrations[0] = 11
        with self.assertRaisesRegex(SummaryError, "not exactly 62400 ns"):
            analyzer.generation_audit(
                v9_geometric_trace_n8(), stats, membership, 8,
                "membership_coalescing_v9", 15, set(range(8)), 1_000_000, 9,
                registrations)

    def test_v9_membership_stats_schema_includes_initial_collection(self) -> None:
        values: dict[str, object] = {field: 0 for field in analyzer.COALESCING_FIELDS_V9}
        values["reliability_rtts"] = 2.0
        values.update({
            "window_ns": 12_480, "max_windows": 5,
            "initial_collection_starts": 1,
            "initial_collection_flushes": 1,
            "initial_collection_deferred_changes": 7,
            "initial_collection_wait_ns": 62_400,
            "initial_collection_max_wait_ns": 62_400,
        })
        line = "guard_membership_coalescing " + " ".join(
            f"{field} {values[field]}" for field in analyzer.COALESCING_FIELDS_V9)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "stats.txt"
            path.write_text(line + "\n", encoding="utf-8")
            parsed = analyzer.parse_coalescing_stats(path, 9)
        self.assertEqual(parsed["initial_collection_wait_ns"], 62_400)
        self.assertEqual(parsed["initial_collection_cancellations"], 0)

    def test_v10_sliding_quiet_and_hard_timing_are_mutually_exclusive(self) -> None:
        stats, membership = geometric_stats_n8()
        registrations = {flow_id: 10 + flow_id * 1000 for flow_id in range(8)}
        result = analyzer.generation_audit(
            v10_geometric_trace_n8(), stats, membership, 8,
            "membership_coalescing_v10", 15, set(range(8)), 1_000_000, 10,
            registrations)
        quiet_membership = dict(membership, initial_collection_quiet_ns=16_640,
                                initial_collection_full_deadline=0,
                                initial_collection_starts=1,
                                initial_collection_flushes=1,
                                initial_collection_deferred_changes=7,
                                initial_collection_reschedules=7,
                                initial_collection_cancellations=0,
                                initial_collection_quiet_flushes=1,
                                initial_collection_hard_flushes=0,
                                initial_collection_wait_ns=23_640,
                                initial_collection_max_wait_ns=23_640,
                                max_batch=8)
        analyzer.validate_v10_initial_collection(
            quiet_membership, result, 8, "quiet fixture")
        self.assertEqual(result["first_flush_minus_last_registration_ns"], 16_640)

        hard = copy.deepcopy(result)
        hard.update({
            "first_flush_ns": 83_210,
            "first_flush_minus_first_registration_ns": 83_200,
            "last_registration_ns": 80_000,
            "first_flush_minus_last_registration_ns": 3_210,
        })
        hard_membership = dict(
            quiet_membership, initial_collection_quiet_flushes=0,
            initial_collection_hard_flushes=1,
            initial_collection_wait_ns=83_200,
            initial_collection_max_wait_ns=83_200)
        analyzer.validate_v10_initial_collection(
            hard_membership, hard, 8, "hard fixture")
        broken = dict(quiet_membership, initial_collection_hard_flushes=1)
        with self.assertRaisesRegex(SummaryError, "sliding initial"):
            analyzer.validate_v10_initial_collection(broken, result, 8, "two reasons")

    def test_v10_stats_schema_and_v9_extended_compatibility(self) -> None:
        values: dict[str, object] = {
            field: 0 for field in analyzer.COALESCING_FIELDS_V10}
        values["reliability_rtts"] = 2.0
        values.update({
            "window_ns": 12_480, "max_windows": 5,
            "initial_collection_quiet_ns": 16_640,
            "initial_collection_full_deadline": 0,
            "initial_collection_starts": 1,
            "initial_collection_flushes": 1,
            "initial_collection_deferred_changes": 7,
            "initial_collection_reschedules": 7,
            "initial_collection_quiet_flushes": 1,
            "initial_collection_wait_ns": 23_640,
            "initial_collection_max_wait_ns": 23_640,
        })
        line = "guard_membership_coalescing " + " ".join(
            f"{field} {values[field]}" for field in analyzer.COALESCING_FIELDS_V10)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "stats.txt"
            path.write_text(line + "\n", encoding="utf-8")
            parsed_v10 = analyzer.parse_coalescing_stats(path, 10)
            parsed_v9 = analyzer.parse_coalescing_stats(path, 9)
        self.assertEqual(parsed_v10["initial_collection_quiet_ns"], 16_640)
        self.assertEqual(parsed_v10["initial_collection_quiet_flushes"], 1)
        self.assertEqual(parsed_v9["initial_collection_reschedules"], 7)

    def test_missing_ack_or_pending_generation_fails(self) -> None:
        stats, membership = generation_stats()
        missing = [row for row in candidate_trace()
                   if not (row["event"] == "ack_received" and row["flow_id"] == 1)]
        with self.assertRaisesRegex(SummaryError, "do not close"):
            analyzer.generation_audit(
                missing, stats, membership, 2, "membership_coalescing_v6",
                15, {0, 1}, 1_000_000)
        pending = candidate_trace()
        for row in pending:
            if row["event"] == "ack_received" and row["flow_id"] == 1:
                row["pending_acks"] = 1
        with self.assertRaisesRegex(SummaryError, "pending=0"):
            analyzer.generation_audit(
                pending, stats, membership, 2, "membership_coalescing_v6",
                15, {0, 1}, 1_000_000)

    def test_retry_stale_and_control_frame_envelope_fail(self) -> None:
        stats, membership = generation_stats()
        retry = candidate_trace()
        retry[0]["set_change"] = "reliability_refresh"
        with self.assertRaisesRegex(SummaryError, "stale or retry"):
            analyzer.generation_audit(
                retry, stats, membership, 2, "membership_coalescing_v6",
                15, {0, 1}, 1_000_000)
        stats2, membership2 = generation_stats(generations=2)
        with self.assertRaisesRegex(SummaryError, "exceed 3N"):
            analyzer.generation_audit(
                candidate_trace(generations=2), stats2, membership2, 2,
                "membership_coalescing_v6", 15, {0, 1}, 1_000_000)

    def test_control_requires_zero_generation_and_ack(self) -> None:
        rows = []
        for flow_id in (0, 1):
            rows.extend([
                trace_row(10, "sent", flow_id, 0, 50_000_000_000, 2,
                          change="registration"),
                trace_row(20, "received", flow_id, 0, 50_000_000_000),
            ])
        stats = {"grants_sent": 2, "grants_received": 2, "grant_bytes_sent": 120}
        membership = {field: 0 for field in (
            "batches", "generation", "ack_sent", "ack_received", "ack_stale",
            "stale_grants", "fully_acked_batches", "pending", "retry_grants")}
        result = analyzer.generation_audit(
            rows, stats, membership, 2, "control", 15, {0, 1}, 1_000_000)
        self.assertEqual(result["grant_generations"], [])
        rows.append(trace_row(30, "ack_sent", 0, 1))
        with self.assertRaisesRegex(SummaryError, "control emitted"):
            analyzer.generation_audit(
                rows, stats, membership, 2, "control", 15, {0, 1}, 1_000_000)

    def test_control_pfc_is_observed_not_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "pfc.txt"
            path.write_text(
                "# time_ns node_id node_type if_index q_index event advertised_pause_us\n"
                "100 16 1 1 4 1 5\n200 16 1 1 4 0 0\n",
                encoding="utf-8")
            stats = {"pfc_priority": {4: {
                "pause_count": 1, "resume_count": 1, "matched_intervals": 1,
                "cumulative_pause_ns": 100, "max_pause_ns": 100,
                "unmatched_pauses": 0, "unmatched_resumes": 0}}}
            observed = analyzer.pfc_audit(stats, path)
            self.assertEqual(observed["matched_intervals"], 1)
            self.assertEqual(observed["cumulative_pause_ns"], 100)
            changed = copy.deepcopy(stats)
            changed["pfc_priority"][4]["max_pause_ns"] = 99
            with self.assertRaisesRegex(SummaryError, "trace=100, stats=99"):
                analyzer.pfc_audit(changed, path)

    def test_pilot_admission_never_opens_performance(self) -> None:
        identities = runner.pilot_matrix(self.spec)
        manifests = {identity: {} for identity in identities}
        with tempfile.TemporaryDirectory() as temporary:
            campaign = Path(temporary)
            (campaign / "preflight.json").write_text(json.dumps({
                "schema_version": 6, "spec_execution_armed": True,
                "formal_execution_armed": False, "pilot_admission": None,
                "spec_sha256": sha256_file(SPEC_PATH), "run_count": 40,
                "pilot_run_count": 2, "repo": str(REPO),
                "simulator_git_sha": "2" * 40,
            }), encoding="utf-8")
            common = {
                "completed_flows": 15, "switch_drops": 0,
                "recovery_events": 0, "timeout_recoveries": 0,
                "grant_trace": {"truncated": 0},
            }
            control = dict(common, seed=95, active_flows=15, controller="control",
                           flow_sha256="same", generation_ack={}, membership={})
            candidate = dict(
                common, seed=95, active_flows=15,
                controller="membership_coalescing_v6", flow_sha256="same",
                generation_ack={"total_control_frames_sent": 30,
                                "pending_generations": 0, "retry_grants": 0,
                                "stale_events": 0},
                membership={"max_batch": 15, "first_grant_gated": 15,
                            "first_grant_released": 15})
            admitted = [(control, campaign), (candidate, campaign)]
            completed = mock.Mock(returncode=0)
            with mock.patch.object(analyzer, "discover_manifests", return_value=manifests), \
                    mock.patch.object(analyzer, "validate_mechanism_run", side_effect=admitted), \
                    mock.patch.object(analyzer, "performance_metrics") as performance, \
                    mock.patch.object(analyzer.subprocess, "run", return_value=completed):
                result = analyzer.analyze_pilot(SPEC_PATH, campaign)
            self.assertTrue(result["passed"])
            self.assertFalse(result["performance_emitted"])
            performance.assert_not_called()

    def test_last_mechanism_failure_keeps_performance_sealed(self) -> None:
        identities = runner.matrix(self.spec)
        manifests = {identity: {} for identity in identities}
        with tempfile.TemporaryDirectory() as temporary:
            campaign = Path(temporary)
            (campaign / "preflight.json").write_text(json.dumps({
                "schema_version": 6, "spec_execution_armed": True,
                "formal_execution_armed": True,
                "spec_sha256": sha256_file(SPEC_PATH), "run_count": 40,
                "pilot_run_count": 2,
                "repo": str(REPO), "simulator_git_sha": "2" * 40,
            }), encoding="utf-8")
            calls = 0

            def validate(*_args: object) -> tuple[dict[str, object], Path]:
                nonlocal calls
                identity = identities[calls]
                calls += 1
                if calls == 40:
                    raise SummaryError("generation 7 pending")
                seed, count, arm = identity
                return ({"seed": seed, "active_flows": count, "controller": arm,
                         "flow_sha256": f"{seed}-{count}"}, campaign), campaign

            completed = mock.Mock(returncode=0)
            with mock.patch.object(analyzer, "discover_manifests", return_value=manifests), \
                    mock.patch.object(analyzer, "validate_mechanism_run", side_effect=validate), \
                    mock.patch.object(analyzer, "validate_pilot_admission"), \
                    mock.patch.object(analyzer, "pilot_admission_path",
                                      return_value=campaign / "pilot_admission.json"), \
                    mock.patch.object(analyzer, "sha256_file", side_effect=lambda path: (
                        "d" * 64 if Path(path).name == "pilot_admission.json" else
                        sha256_file(Path(path)))), \
                    mock.patch.object(analyzer, "performance_metrics") as performance, \
                    mock.patch.object(analyzer.subprocess, "run", return_value=completed):
                with self.assertRaisesRegex(SummaryError, "performance remains sealed"):
                    analyzer.analyze(SPEC_PATH, campaign)
            performance.assert_not_called()
            self.assertEqual(calls, 40)


class V11AnalyzerTests(unittest.TestCase):
    def test_v11_exact_sha_worktree_allows_only_run_py_relocation(self) -> None:
        spec = runner.read_spec(SPEC_V11_PATH)
        frozen_sha = "389b2974b6e2712cdc0044b7fb5e7d066db3bfd9"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            simulator = root / "simulator"
            clone = subprocess.run(
                ["git", "clone", "--quiet", "--shared", "--no-checkout",
                 str(REPO), str(simulator)], text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            self.assertEqual(clone.returncode, 0, clone.stdout)
            checkout = subprocess.run(
                ["git", "checkout", "--quiet", "--detach", frozen_sha],
                cwd=simulator, text=True, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT)
            self.assertEqual(checkout.returncode, 0, checkout.stdout)
            flow = root / "pilot-flow.txt"
            flow.write_text("frozen\n", encoding="utf-8")
            traffic = {
                "scope": "pilot", "seed": 125, "active_flows": 4,
                "flow_count": 4, "path": str(flow), "sha256": sha256_file(flow),
            }
            preflight_path = root / "preflight.json"
            preflight_path.write_text("{}\n", encoding="utf-8")
            preflight = {
                "repo": str(REPO), "simulator_git_sha": frozen_sha,
                "traffic": [traffic],
            }
            manifest = {
                "seed": 125, "active_flows": 4, "controller": "control",
                "scope": "pilot", "ordinal": 1, "schema_version": 11,
                "status": "completed", "stop_reason": "completed",
                "git_dirty": False, "simulator_git_sha": frozen_sha,
                "spec_sha256": sha256_file(SPEC_V11_PATH),
                "preflight_sha256": sha256_file(preflight_path),
                "flow_path": str(flow), "flow_sha256": sha256_file(flow),
                "flow_count": 4, "repo": str(simulator.resolve()),
                "cli": runner.run_command(simulator.resolve(), spec, traffic, "control"),
            }
            identity = analyzer.validate_manifest_identity(
                manifest, spec, SPEC_V11_PATH, root, preflight, "pilot")
            self.assertEqual(identity[:3], (125, 4, "control"))
            self.assertEqual(
                manifest["cli"][3], str(simulator.resolve() / "run.py"))

            changed = copy.deepcopy(manifest)
            option = changed["cli"].index("--guard_membership_coalesce_ns") + 1
            changed["cli"][option] = "1"
            with self.assertRaisesRegex(SummaryError, "beyond run.py relocation"):
                analyzer.validate_manifest_identity(
                    changed, spec, SPEC_V11_PATH, root, preflight, "pilot")

            (simulator / "untracked-v11-test").write_text("dirty\n", encoding="utf-8")
            with self.assertRaisesRegex(SummaryError, "not clean"):
                analyzer.validate_manifest_identity(
                    manifest, spec, SPEC_V11_PATH, root, preflight, "pilot")

    def test_v11_n2_zero_legacy_batches_reaches_phase_audit(self) -> None:
        spec = runner.read_spec(SPEC_V11_PATH)
        rows, phase_stats, membership, small, registrations = v11_fixture(2)
        membership.update({
            "window_ns": 12_480, "max_windows": 5, "reliability_rtts": 2.0,
            "first_grant_gated": 2, "first_grant_released": 2,
        })
        self.assertEqual(membership["batches"], 0)
        stats = dict(phase_stats)
        stats.update({
            "switch_drops_total": 0, "timeout_recoveries": 0,
            "registrations": 2, "selected_registrations": 2,
            "completion_releases": 2, "proactive_releases": 0,
            "max_active_flows": 2,
            **{field: 0 for field in analyzer.RECOVERY_FIELDS},
        })
        lifecycle_rows = [
            {"flow_id": flow_id, "register_ns": register_ns}
            for flow_id, register_ns in registrations.items()]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "output"
            output.mkdir()
            launcher = root / "launcher.log"
            launcher.write_text("", encoding="utf-8")
            manifest = {
                "resource_limits": spec["resource_limits"],
                "elapsed_seconds": 0, "max_rss_kib": 0,
                "output_dir": str(output), "output_id": output.name,
                "launcher_log": str(launcher), "output_bytes": 0,
                "completion_validation": "deferred_to_lifecycle_and_stats",
            }
            with mock.patch.object(
                    analyzer, "validate_manifest_identity",
                    return_value=(120, 2, "membership_coalescing_v11",
                                  {"sha256": "same"})), \
                    mock.patch.object(analyzer, "directory_size", return_value=0), \
                    mock.patch.object(analyzer, "parse_config", return_value={}), \
                    mock.patch.object(analyzer, "validate_config"), \
                    mock.patch.object(analyzer, "one_artifact", return_value=root / "raw"), \
                    mock.patch.object(
                        analyzer, "parse_snapshot",
                        return_value=([object(), object()], {"sha256": "same"})), \
                    mock.patch.object(analyzer, "parse_guard_stats", return_value=stats), \
                    mock.patch.object(
                        analyzer, "parse_coalescing_stats", return_value=membership), \
                    mock.patch.object(analyzer, "parse_small_set_stats", return_value=small), \
                    mock.patch.object(
                        analyzer, "parse_lifecycle", return_value=lifecycle_rows), \
                    mock.patch.object(
                        analyzer, "lifecycle_audit", return_value=({}, {0, 1})), \
                    mock.patch.object(
                        analyzer, "parse_bounded_trace",
                        return_value=(rows, {"attempted": len(rows),
                                             "written": len(rows), "truncated": 0})), \
                    mock.patch.object(
                        analyzer, "pfc_audit",
                        return_value={"pause_events": 0, "resume_events": 0}), \
                    mock.patch.object(
                        analyzer, "v11_phase_audit",
                        wraps=analyzer.v11_phase_audit) as phase_audit:
                admitted, admitted_output = analyzer.validate_mechanism_run(
                    manifest, spec, SPEC_V11_PATH, root,
                    {"simulator_git_sha": "9" * 40}, "replay")
        phase_audit.assert_called_once()
        self.assertEqual(admitted["generation_ack"]["total_control_frames_sent"], 6)
        self.assertEqual(admitted_output.name, "output")

    def test_v11_low_and_high_phase_closure_pass(self) -> None:
        for count, bound in ((2, 7), (8, 43), (15, 64)):
            rows, stats, membership, small, registrations = v11_fixture(count)
            result = analyzer.v11_phase_audit(
                rows, stats, membership, small, count, 15, set(range(count)),
                registrations, 1_000_000, bound)
            self.assertTrue(result["barriers_closed"])
            self.assertTrue(result["transition_union_closed"])
            self.assertLessEqual(result["total_control_frames_sent"], bound)
            self.assertEqual(
                sum(len(row["activate_flows"])
                    for row in result["transaction_contract"]), count)

    def test_v11_rejects_early_activation_and_incomplete_transition_union(self) -> None:
        rows, stats, membership, small, registrations = v11_fixture(2)
        broken = copy.deepcopy(rows)
        activation = next(row for row in broken
                          if row["event"] == "sent" and
                          row["grant_phase"] == "fast_activate" and
                          row["transaction_id"] == 2)
        activation["time_ns"] = 220
        with self.assertRaisesRegex(SummaryError, "crossed an ACK barrier"):
            analyzer.v11_phase_audit(
                broken, stats, membership, small, 2, 15, {0, 1},
                registrations, 1_000_000, 7)

        rows, stats, membership, small, registrations = v11_fixture(8)
        broken = [row for row in rows
                  if not (row["flow_id"] == 7 and
                          row["grant_phase"] == "transition_activate")]
        with self.assertRaises(SummaryError):
            analyzer.v11_phase_audit(
                broken, stats, membership, small, 8, 15, set(range(8)),
                registrations, 1_000_000, 43)

    def test_v11_stats_and_19_column_trace_are_fail_closed(self) -> None:
        values = {field: 0 for field in analyzer.SMALL_SET_FIELDS_V11}
        values.update({"enabled": 1, "limit": 4, "wire_reconciled": 1})
        stats_line = "guard_small_set_fastpath " + " ".join(
            f"{field} {values[field]}" for field in analyzer.SMALL_SET_FIELDS_V11)
        row = v11_phase_row(
            100, "sent", 0, 1, "fast_activate", 1, 1,
            100_000_000_000, "waiter")
        trace_line = ",".join(str(row[field]) for field in analyzer.TRACE_FIELDS_V11)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stats_path = root / "stats.txt"
            stats_path.write_text(stats_line + "\n", encoding="utf-8")
            parsed = analyzer.parse_small_set_stats(stats_path)
            self.assertEqual(parsed["limit"], 4)
            trace_path = root / "grants.csv"
            trace_path.write_text(
                ",".join(analyzer.TRACE_FIELDS_V11) + "\n" + trace_line +
                "\n# attempted 1 written 1 truncated 0\n", encoding="utf-8")
            parsed_rows, footer = analyzer.parse_bounded_trace(
                trace_path, 4096, 11, v11_enabled=True)
            self.assertEqual(parsed_rows[0]["grant_phase"], "fast_activate")
            self.assertEqual(footer["written"], 1)
            changed = stats_line.replace("terminal_phase 0", "renamed_phase 0")
            stats_path.write_text(changed + "\n", encoding="utf-8")
            with self.assertRaisesRegex(SummaryError, "labels changed"):
                analyzer.parse_small_set_stats(stats_path)

    def test_v11_frame_cap_includes_wire_retries(self) -> None:
        rows, stats, membership, small, registrations = v11_fixture(2)
        small["control_frames"] = 8
        with self.assertRaisesRegex(SummaryError, "frame-cap counters"):
            analyzer.v11_phase_audit(
                rows, stats, membership, small, 2, 15, {0, 1},
                registrations, 1_000_000, 7)


class V12AnalyzerTests(unittest.TestCase):
    def audit(self, count: int) -> tuple[dict[str, object], list[dict[str, object]],
                                         dict[str, int], dict[int, int]]:
        rows, stats, membership, small, prefix, registrations = v12_fixture(count)
        generation = analyzer.v11_phase_audit(
            rows, stats, membership, small, count, 15, set(range(count)),
            registrations, 1_000_000, {2: 7, 4: 26, 8: 43, 15: 64}[count])
        proof = analyzer.v12_prefix_audit(
            rows, prefix, count, 15, set(range(count)), registrations, generation)
        return proof, rows, prefix, registrations

    def test_v12_low_and_high_normal_prefix_paths_pass(self) -> None:
        low, _rows, _prefix, _registrations = self.audit(2)
        self.assertFalse(low["prefix_started"])
        for count in (8, 15):
            proof, _rows, prefix, _registrations = self.audit(count)
            self.assertTrue(proof["normal_path"])
            self.assertTrue(proof["prefix_started"])
            self.assertEqual(proof["activation_batch_index"], 1)
            self.assertEqual(
                proof["activation_batch_size"], prefix["waiters_required"])

    def test_v12_timeout_fallback_raw_prefix_and_terminal_fail_closed(self) -> None:
        rows, stats, membership, small, prefix, registrations = v12_fixture(8)
        generation = analyzer.v11_phase_audit(
            rows, stats, membership, small, 8, 15, set(range(8)),
            registrations, 1_000_000, 43)
        mutations: list[tuple[list[dict[str, object]], dict[str, int]]] = []
        changed_prefix = copy.deepcopy(prefix)
        changed_prefix["timeouts"] = 1
        mutations.append((copy.deepcopy(rows), changed_prefix))
        changed_prefix = copy.deepcopy(prefix)
        changed_prefix["fallback_batches"] = 1
        mutations.append((copy.deepcopy(rows), changed_prefix))
        changed_prefix = copy.deepcopy(prefix)
        changed_prefix["terminal_cursor"] = 1
        mutations.append((copy.deepcopy(rows), changed_prefix))
        changed_rows = copy.deepcopy(rows)
        activation = next(row for row in changed_rows
                          if row["event"] == "sent" and
                          row["grant_phase"] == "transition_activate")
        activation["prefix_observed_bytes"] = (
            int(activation["prefix_target_bytes"]) - 1)
        mutations.append((changed_rows, copy.deepcopy(prefix)))
        changed_rows = copy.deepcopy(rows)
        activation = next(row for row in changed_rows
                          if row["event"] == "sent" and
                          row["grant_phase"] == "transition_activate")
        activation["activation_batch_size"] = 3
        mutations.append((changed_rows, copy.deepcopy(prefix)))
        changed_rows = copy.deepcopy(rows)
        activation = next(row for row in changed_rows
                          if row["event"] == "sent" and
                          row["grant_phase"] == "transition_activate")
        activation["generation"] = 99
        mutations.append((changed_rows, copy.deepcopy(prefix)))
        for ordinal, (changed_rows, changed_prefix) in enumerate(mutations):
            with self.subTest(ordinal=ordinal), self.assertRaises(SummaryError):
                analyzer.v12_prefix_audit(
                    changed_rows, changed_prefix, 8, 15, set(range(8)),
                    registrations, generation)

    def test_v12_fixed_stats_and_trace_schemas_are_fail_closed(self) -> None:
        rows, _stats, _membership, _small, prefix, _registrations = v12_fixture(8)
        row = next(item for item in rows if item["event"] == "sent" and
                   item["grant_phase"] == "transition_activate")
        stats_line = "guard_transition_prefix " + " ".join(
            f"{field} {prefix[field]}"
            for field in analyzer.TRANSITION_PREFIX_FIELDS_V12)
        trace_line = ",".join(str(row[field]) for field in analyzer.TRACE_FIELDS_V12)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stats_path = root / "stats.txt"
            stats_path.write_text(stats_line + "\n", encoding="utf-8")
            parsed = analyzer.parse_transition_prefix_stats(stats_path)
            self.assertEqual(parsed["ready"], 1)
            trace_path = root / "trace.csv"
            trace_path.write_text(
                ",".join(analyzer.TRACE_FIELDS_V12) + "\n" + trace_line +
                "\n# attempted 1 written 1 truncated 0\n", encoding="utf-8")
            parsed_rows, footer = analyzer.parse_bounded_trace(
                trace_path, 4096, 12, v11_enabled=True)
            self.assertEqual(parsed_rows[0]["drain_outcome"], "ready")
            self.assertEqual(footer["written"], 1)
            stats_path.write_text(
                stats_line.replace("terminal_cursor 0", "renamed_cursor 0") + "\n",
                encoding="utf-8")
            with self.assertRaisesRegex(SummaryError, "labels changed"):
                analyzer.parse_transition_prefix_stats(stats_path)


class V13AnalyzerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = runner.read_spec(SPEC_V13_PATH)
        self.topology = (REPO / "config" /
                         "leaf_spine_16_100G_OS4.txt").read_text(encoding="utf-8")

    def config(self, arm: str, watchdog: int | None) -> dict[str, str]:
        pilot = self.spec["candidate_pilot"]
        limits = self.spec["resource_limits"]
        config = {
            "TOPOLOGY_FILE": "config/leaf_spine_16_100G_OS4.txt",
            "MONITOR_PROFILE": "bulk", "CC_MODE": "11", "LB_MODE": "0",
            "ENABLE_PFC": "1", "ENABLE_IRN": "0", "BUFFER_SIZE": "9",
            "GUARD_LIFECYCLE_TRACE": "1", "GUARD_CONTROLLER_TRACE": "0",
            "GUARD_GRANT_TRACE": "1", "ANALYSIS_WARMUP_TIME": "0",
            "PREFLIGHT_MAX_FLOWS": str(limits["max_flows"]),
            "RANDOM_SEED": "137",
            "GUARD_LIFECYCLE_TRACE_MAX_LINES": str(
                limits["lifecycle_trace_max_lines"]),
            "GUARD_GRANT_TRACE_MAX_LINES": str(limits["grant_trace_max_lines"]),
            "GUARD_MEMBERSHIP_COALESCE_NS": str(
                pilot["membership_quiet_ns"][arm]),
            "GUARD_MEMBERSHIP_COALESCE_MAX_WINDOWS": str(
                pilot["membership_max_windows"]),
            "GUARD_GRANT_RELIABILITY_RTTS": str(
                pilot["grant_reliability_service_rounds"][arm]),
            "GUARD_INITIAL_COLLECTION_FULL_DEADLINE": str(
                pilot["initial_collection_full_deadline"][arm]),
            "GUARD_INITIAL_COLLECTION_QUIET_NS": str(
                pilot["initial_collection_quiet_ns"][arm]),
            "GUARD_SMALL_SET_FASTPATH_LIMIT": str(
                pilot["small_set_fastpath_limit"][arm]),
            "GUARD_TRANSITION_PREFIX_BARRIER": str(
                pilot["transition_prefix_barrier"][arm]),
            "PACKET_PAYLOAD_SIZE": "1000",
        }
        if watchdog is not None:
            config["GUARD_TRANSITION_PREFIX_WIRE_WATCHDOG"] = str(watchdog)
        config.update({analyzer.CONFIG_KEYS[key]: str(value)
                       for key, value in runner.GUARD_OPTIONS.items()})
        return config

    def test_v13_config_binds_true_mtu_and_candidate_watchdog(self) -> None:
        arm = "membership_coalescing_v13"
        config = self.config(arm, 1)
        analyzer.validate_config(config, self.spec, 137, arm)
        config["PACKET_PAYLOAD_SIZE"] = "999"
        with self.assertRaisesRegex(SummaryError, "PACKET_PAYLOAD_SIZE"):
            analyzer.validate_config(config, self.spec, 137, arm)

    def test_v13_control_may_omit_only_its_default_off_watchdog_key(self) -> None:
        control = self.config("control", None)
        analyzer.validate_config(control, self.spec, 137, "control")
        explicit_zero = self.config("control", 0)
        analyzer.validate_config(explicit_zero, self.spec, 137, "control")
        explicit_one = self.config("control", 1)
        with self.assertRaisesRegex(
                SummaryError, "GUARD_TRANSITION_PREFIX_WIRE_WATCHDOG"):
            analyzer.validate_config(explicit_one, self.spec, 137, "control")

    def test_v13_candidate_watchdog_key_cannot_be_omitted(self) -> None:
        candidate = self.config("membership_coalescing_v13", None)
        with self.assertRaisesRegex(
                SummaryError, "GUARD_TRANSITION_PREFIX_WIRE_WATCHDOG"):
            analyzer.validate_config(
                candidate, self.spec, 137, "membership_coalescing_v13")

    def audit(self, count: int) -> tuple[
            dict[str, object], list[dict[str, object]], dict[str, int]]:
        (rows, stats, membership, small, prefix, watchdog, registrations,
         lifecycle, flows) = v13_fixture(count)
        generation = analyzer.v11_phase_audit(
            rows, stats, membership, small, count, 15, set(range(count)),
            registrations, 1_000_000,
            {2: 7, 4: 26, 8: 43, 15: 64}[count])
        prefix_proof = analyzer.v12_prefix_audit(
            rows, prefix, count, 15, set(range(count)), registrations, generation)
        generation["transition_prefix"] = prefix_proof
        proof = analyzer.v13_watchdog_audit(
            rows, lifecycle, flows, watchdog, count, prefix, prefix_proof,
            generation, self.spec, self.topology)
        return proof, rows, watchdog

    def test_v13_low_zero_and_high_independent_budget_pass(self) -> None:
        low, _rows, _watchdog = self.audit(2)
        self.assertTrue(low["independently_reconstructed"])
        self.assertFalse(low["budget_started"])
        high, _rows, watchdog = self.audit(8)
        self.assertTrue(high["budget_started"])
        self.assertEqual(high["records"], 1)
        self.assertEqual(high["rounded_payload_budget_bytes"], 20_000)
        self.assertEqual(high["packet_count"], 20)
        self.assertEqual(high["wire_bytes"], 21_800)
        self.assertEqual(high["occupancy_bps"], 50_000_000_000)
        self.assertEqual(high["residual_bps"], 50_000_000_000)
        self.assertEqual(high["max_rtt_ns"], 8320)
        self.assertEqual(high["deadline_ns"], watchdog["deadline_ns"])

    def test_v13_record_provenance_scalar_and_terminal_drift_fail_closed(self) -> None:
        base = v13_fixture(8)
        (rows, stats, membership, small, prefix, watchdog, registrations,
         lifecycle, flows) = base
        generation = analyzer.v11_phase_audit(
            rows, stats, membership, small, 8, 15, set(range(8)),
            registrations, 1_000_000, 43)
        prefix_proof = analyzer.v12_prefix_audit(
            rows, prefix, 8, 15, set(range(8)), registrations, generation)
        generation["transition_prefix"] = prefix_proof
        mutations = []
        for field, value in (
                ("records", 2), ("non_reconstructable", 1),
                ("inconsistent", 1), ("wire_bytes", watchdog["wire_bytes"] + 1),
                ("terminal_budget", 1), ("non_reconstructable", -1),
                ("inconsistent", -1), ("terminal_budget", -1)):
            changed = copy.deepcopy(watchdog)
            changed[field] = value
            mutations.append(changed)
        for ordinal, changed in enumerate(mutations):
            with self.subTest(ordinal=ordinal), self.assertRaises(SummaryError):
                analyzer.v13_watchdog_audit(
                    rows, lifecycle, flows, changed, 8, prefix, prefix_proof,
                    generation, self.spec, self.topology)

        changed_proof = copy.deepcopy(prefix_proof)
        transition = next(row for row in generation["transaction_contract"]
                          if row["kind"] == "transition")
        changed_proof["prefix_start_ns"] = int(
            transition["prepare_last_ack_ns"]) - 1
        with self.assertRaisesRegex(SummaryError, "after prepare ACK closure"):
            analyzer.v13_watchdog_audit(
                rows, lifecycle, flows, watchdog, 8, prefix, changed_proof,
                generation, self.spec, self.topology)

    def test_v13_raw_target_topology_and_fixed_stats_schema_fail_closed(self) -> None:
        (rows, stats, membership, small, prefix, watchdog, registrations,
         lifecycle, flows) = v13_fixture(8)
        generation = analyzer.v11_phase_audit(
            rows, stats, membership, small, 8, 15, set(range(8)),
            registrations, 1_000_000, 43)
        prefix_proof = analyzer.v12_prefix_audit(
            rows, prefix, 8, 15, set(range(8)), registrations, generation)
        generation["transition_prefix"] = prefix_proof
        changed_rows = copy.deepcopy(rows)
        activation = next(row for row in changed_rows
                          if row["event"] == "sent" and
                          row["grant_phase"] == "transition_activate")
        activation["prefix_target_bytes"] = 9_000_000
        with self.assertRaisesRegex(SummaryError, "outside its flow size"):
            analyzer.v13_watchdog_audit(
                changed_rows, lifecycle, flows, watchdog, 8, prefix,
                prefix_proof, generation, self.spec, self.topology)
        with self.assertRaisesRegex(SummaryError, "topology"):
            analyzer.v13_watchdog_audit(
                rows, lifecycle, flows, watchdog, 8, prefix, prefix_proof,
                generation, self.spec,
                self.topology.replace("100Gbps", "99Gbps", 1))

        stats_line = "guard_transition_prefix_watchdog " + " ".join(
            f"{field} {watchdog[field]}"
            for field in analyzer.TRANSITION_WATCHDOG_FIELDS_V13)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "stats.txt"
            path.write_text(stats_line + "\n", encoding="utf-8")
            parsed = analyzer.parse_transition_watchdog_stats(path)
            self.assertIsNotNone(parsed)
            assert parsed is not None
            self.assertEqual(parsed["records"], 1)
            path.write_text(
                stats_line.replace("records 1", "renamed_records 1") + "\n",
                encoding="utf-8")
            with self.assertRaisesRegex(SummaryError, "labels changed"):
                analyzer.parse_transition_watchdog_stats(path)
            path.write_text(stats_line.replace("inconsistent 0", "inconsistent -1") +
                            "\n", encoding="utf-8")
            with self.assertRaisesRegex(SummaryError, "stat domain"):
                analyzer.parse_transition_watchdog_stats(path)


class AggregatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = runner.read_spec(SPEC_PATH)

    def write_analysis(self, root: Path, effect: float = 0.0) -> Path:
        path = root / "analysis.json"
        path.write_text(json.dumps(synthetic_analysis(self.spec, effect)), encoding="utf-8")
        return path

    def test_five_seed_paired_t95_and_control_pfc_observation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            analysis = self.write_analysis(Path(temporary))
            rows, intervals, admission = aggregator.aggregate(SPEC_PATH, analysis)
        self.assertEqual(len(rows), 20)
        self.assertEqual(len(intervals), 20)
        self.assertTrue(admission["passed"])
        self.assertEqual(admission["student_t_critical_95_df4"], aggregator.T95_DF4)
        self.assertEqual(rows[0]["control_pfc_matched_intervals"], 1)

    def test_non_regression_threshold_is_executed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            analysis = self.write_analysis(Path(temporary), effect=2.0)
            _rows, _intervals, admission = aggregator.aggregate(SPEC_PATH, analysis)
        self.assertFalse(admission["passed"])
        self.assertEqual(admission["status"], "rejected_non_regression")

    def test_zero_denominator_and_incomplete_matrix_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = synthetic_analysis(self.spec)
            payload["runs"][0]["performance"]["mean_fct_us"] = 0
            path = root / "zero.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(SummaryError, "zero denominator"):
                aggregator.aggregate(SPEC_PATH, path)
            payload = synthetic_analysis(self.spec)
            payload["runs"].pop()
            path = root / "missing.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(SummaryError, "identities differ"):
                aggregator.aggregate(SPEC_PATH, path)

    def test_paired_zero_queue_percent_is_exact_equality(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = synthetic_analysis(self.spec)
            for run in payload["runs"]:
                run["performance"]["receiver_queue_time_sampled_p99_bytes"] = 0
            path = root / "both-zero.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            rows, intervals, admission = aggregator.aggregate(SPEC_PATH, path)
        self.assertTrue(admission["passed"])
        self.assertTrue(all(
            row["paired_receiver_queue_time_sampled_p99_bytes_percent"] == 0
            for row in rows))
        self.assertTrue(all(
            row["mean"] == 0 and row["ci95_low"] == 0 and row["ci95_high"] == 0
            for row in intervals
            if row["metric"] == "receiver_queue_time_sampled_p99_bytes"))


class V7AggregatorTests(unittest.TestCase):
    def test_v7_keeps_v6_thresholds_and_revalidates_selective_evidence(self) -> None:
        spec = runner.read_spec(SPEC_V7_PATH)
        self.assertEqual(
            spec["scalability_holdout"]["paired_non_regression"],
            runner.read_spec(SPEC_PATH)["scalability_holdout"]["paired_non_regression"])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = synthetic_analysis(spec, spec_path=SPEC_V7_PATH)
            path = root / "v7.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            rows, intervals, admission = aggregator.aggregate(SPEC_V7_PATH, path)
            self.assertEqual(len(rows), 20)
            self.assertEqual(len(intervals), 20)
            self.assertEqual(admission["schema_version"], 7)
            self.assertTrue(admission["passed"])
            candidate = next(row for row in payload["runs"]
                             if row["controller"] != runner.CONTROL_ARM)
            candidate["generation_ack"]["generation_contract"][0]["ack_frames_sent"] = 0
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(SummaryError, "ACK-required generation"):
                aggregator.aggregate(SPEC_V7_PATH, path)


class V8AggregatorTests(unittest.TestCase):
    def test_v8_revalidates_geometric_lifecycle_and_strict_frame_evidence(self) -> None:
        spec = runner.read_spec(SPEC_V8_PATH)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "v8.json"
            payload = synthetic_analysis(spec, spec_path=SPEC_V8_PATH)
            path.write_text(json.dumps(payload), encoding="utf-8")
            rows, intervals, admission = aggregator.aggregate(SPEC_V8_PATH, path)
            self.assertEqual(len(rows), 20)
            self.assertEqual(len(intervals), 20)
            self.assertTrue(admission["passed"])

            candidate = next(row for row in payload["runs"]
                             if row["controller"] != runner.CONTROL_ARM)
            candidate["generation_ack"]["generation_contract"][1][
                "active_flows"] = candidate["active_flows"] // 2 + 1
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(SummaryError, "geometric bound"):
                aggregator.aggregate(SPEC_V8_PATH, path)

            payload = synthetic_analysis(spec, spec_path=SPEC_V8_PATH)
            candidate = next(row for row in payload["runs"]
                             if row["controller"] != runner.CONTROL_ARM)
            candidate["generation_ack"]["post_initial_registration_events"] = 1
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(SummaryError, "lifecycle evidence"):
                aggregator.aggregate(SPEC_V8_PATH, path)

            payload = synthetic_analysis(spec, spec_path=SPEC_V8_PATH)
            candidate = next(row for row in payload["runs"]
                             if row["controller"] != runner.CONTROL_ARM)
            candidate["generation_ack"]["total_control_frames_sent"] = (
                3 * int(candidate["active_flows"]))
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(SummaryError, "mechanism evidence regressed"):
                aggregator.aggregate(SPEC_V8_PATH, path)


class V9AggregatorTests(unittest.TestCase):
    def test_v9_revalidates_frozen_initial_collection_before_ci(self) -> None:
        spec = runner.read_spec(SPEC_V9_PATH)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "v9.json"
            payload = synthetic_analysis(spec, spec_path=SPEC_V9_PATH)
            path.write_text(json.dumps(payload), encoding="utf-8")
            rows, intervals, admission = aggregator.aggregate(SPEC_V9_PATH, path)
            self.assertEqual(len(rows), 20)
            self.assertEqual(len(intervals), 20)
            self.assertTrue(admission["passed"])
            self.assertEqual(
                admission["known_replay_admission_sha256"], "e" * 64)

            candidate = next(row for row in payload["runs"]
                             if row["controller"] != runner.CONTROL_ARM)
            candidate["generation_ack"][
                "first_flush_minus_first_registration_ns"] = 62_399
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(SummaryError, "fixed initial"):
                aggregator.aggregate(SPEC_V9_PATH, path)

    def test_v9_requires_replay_seal_and_keeps_v8_ci_gate(self) -> None:
        spec = runner.read_spec(SPEC_V9_PATH)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "v9.json"
            payload = synthetic_analysis(
                spec, effect_percent=2.0, spec_path=SPEC_V9_PATH)
            payload["known_replay_admission_sha256"] = None
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(SummaryError, "replay admission"):
                aggregator.aggregate(SPEC_V9_PATH, path)

            payload["known_replay_admission_sha256"] = "e" * 64
            path.write_text(json.dumps(payload), encoding="utf-8")
            _rows, _intervals, admission = aggregator.aggregate(SPEC_V9_PATH, path)
            self.assertFalse(admission["passed"])
            self.assertEqual(admission["status"], "rejected_non_regression")


class V10AggregatorTests(unittest.TestCase):
    def test_v10_revalidates_quiet_and_hard_flushes_before_ci(self) -> None:
        spec = runner.read_spec(SPEC_V10_PATH)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "v10.json"
            payload = synthetic_analysis(spec, spec_path=SPEC_V10_PATH)
            path.write_text(json.dumps(payload), encoding="utf-8")
            rows, intervals, admission = aggregator.aggregate(SPEC_V10_PATH, path)
            self.assertEqual(len(rows), 20)
            self.assertEqual(len(intervals), 20)
            self.assertTrue(admission["passed"])
            self.assertEqual(admission["known_replay_admission_sha256"], "e" * 64)

            candidate = next(row for row in payload["runs"]
                             if row["controller"] != runner.CONTROL_ARM)
            candidate["generation_ack"][
                "first_flush_minus_last_registration_ns"] = 16_639
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(SummaryError, "sliding initial"):
                aggregator.aggregate(SPEC_V10_PATH, path)

            payload = synthetic_analysis(spec, spec_path=SPEC_V10_PATH)
            for candidate in (row for row in payload["runs"]
                              if row["controller"] != runner.CONTROL_ARM):
                evidence = candidate["generation_ack"]
                membership = candidate["membership"]
                evidence.update({
                    "first_flush_ns": 83_210,
                    "first_flush_minus_first_registration_ns": 83_200,
                    "last_registration_ns": 82_210,
                    "first_flush_minus_last_registration_ns": 1_000,
                })
                membership.update({
                    "initial_collection_quiet_flushes": 0,
                    "initial_collection_hard_flushes": 1,
                    "initial_collection_wait_ns": 83_200,
                    "initial_collection_max_wait_ns": 83_200,
                })
            path.write_text(json.dumps(payload), encoding="utf-8")
            _rows, _intervals, hard_admission = aggregator.aggregate(
                SPEC_V10_PATH, path)
            self.assertTrue(hard_admission["passed"])

    def test_v10_requires_replay_seal_and_keeps_frozen_ci(self) -> None:
        spec = runner.read_spec(SPEC_V10_PATH)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "v10.json"
            payload = synthetic_analysis(
                spec, effect_percent=2.0, spec_path=SPEC_V10_PATH)
            payload["known_replay_admission_sha256"] = None
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(SummaryError, "replay admission"):
                aggregator.aggregate(SPEC_V10_PATH, path)
            payload["known_replay_admission_sha256"] = "e" * 64
            path.write_text(json.dumps(payload), encoding="utf-8")
            _rows, _intervals, admission = aggregator.aggregate(SPEC_V10_PATH, path)
            self.assertFalse(admission["passed"])
            self.assertEqual(admission["status"], "rejected_non_regression")


class V11AggregatorTests(unittest.TestCase):
    def write(self, root: Path, payload: dict[str, object]) -> Path:
        path = root / "v11.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_v11_absolute_small_queue_and_n15_advantage_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = self.write(Path(temporary), synthetic_v11_analysis())
            rows, intervals, admission = aggregator.aggregate(SPEC_V11_PATH, path)
        self.assertEqual(len(rows), 20)
        self.assertEqual(len(intervals), 26)
        self.assertTrue(admission["passed"])
        self.assertEqual(
            admission["small_queue_measurement_correction"]["status"],
            "first_preregistered_for_v11_not_retroactive_to_v10")
        absolute = [row for row in intervals
                    if row["effect"] ==
                    "paired_candidate_minus_control_absolute_bytes"]
        self.assertEqual(len(absolute), 6)
        self.assertTrue(all(row["ci95_high"] == 0 for row in absolute))

    def test_v11_small_queue_uses_60_byte_absolute_gate(self) -> None:
        payload = synthetic_v11_analysis()
        for row in payload["runs"]:
            if row["controller"] != runner.CONTROL_ARM and row["active_flows"] in (2, 4, 8):
                row["performance"]["receiver_queue_time_sampled_mean_bytes"] = 161.0
        with tempfile.TemporaryDirectory() as temporary:
            path = self.write(Path(temporary), payload)
            _rows, intervals, admission = aggregator.aggregate(SPEC_V11_PATH, path)
        self.assertFalse(admission["passed"])
        failed = next(row for row in intervals
                      if row["active_flows"] == 2 and row["metric"].endswith("mean_bytes") and
                      row["effect"].endswith("absolute_bytes"))
        self.assertEqual(failed["threshold"], 60.0)
        self.assertEqual(failed["ci95_high"], 61.0)

    def test_v11_n15_queue_and_frame_ratio_gates_are_independent(self) -> None:
        payload = synthetic_v11_analysis()
        for row in payload["runs"]:
            if row["active_flows"] == 15 and row["controller"] != runner.CONTROL_ARM:
                row["performance"]["receiver_queue_time_sampled_mean_bytes"] = 95.0
                row["performance"]["receiver_queue_time_sampled_p99_bytes"] = 85.0
        with tempfile.TemporaryDirectory() as temporary:
            path = self.write(Path(temporary), payload)
            _rows, _intervals, admission = aggregator.aggregate(SPEC_V11_PATH, path)
        self.assertFalse(admission["passed"])
        queue_failures = [gate for gate in admission["gates"]
                          if gate["active_flows"] == 15 and
                          str(gate["metric"]).startswith("receiver_queue")]
        self.assertTrue(queue_failures)
        self.assertTrue(all(not gate["passed"] for gate in queue_failures))

        payload = synthetic_v11_analysis()
        for row in payload["runs"]:
            if row["active_flows"] == 15 and row["controller"] == runner.CONTROL_ARM:
                row["generation_ack"]["total_control_frames_sent"] = 199
        with tempfile.TemporaryDirectory() as temporary:
            path = self.write(Path(temporary), payload)
            _rows, _intervals, admission = aggregator.aggregate(SPEC_V11_PATH, path)
        ratio = next(gate for gate in admission["gates"]
                     if gate["metric"] == "candidate_to_control_frames_ratio")
        self.assertFalse(ratio["passed"])
        self.assertGreater(ratio["observed"], 0.30)

    def test_v11_revalidates_phase_and_terminal_mechanism_before_ci(self) -> None:
        payload = synthetic_v11_analysis()
        candidate = next(row for row in payload["runs"]
                         if row["controller"] != runner.CONTROL_ARM)
        candidate["safe_activation"]["terminal_waiters"] = 1
        with tempfile.TemporaryDirectory() as temporary:
            path = self.write(Path(temporary), payload)
            with self.assertRaisesRegex(SummaryError, "safe two-stage evidence"):
                aggregator.aggregate(SPEC_V11_PATH, path)


class V12AggregatorTests(unittest.TestCase):
    def write(self, root: Path, payload: dict[str, object]) -> Path:
        path = root / "v12.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_v12_unchanged_ci_gates_and_five_seed_stability_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = self.write(Path(temporary), synthetic_v12_analysis())
            rows, intervals, admission = aggregator.aggregate(SPEC_V12_PATH, path)
        self.assertEqual(len(rows), 20)
        self.assertEqual(len(intervals), 26)
        self.assertTrue(admission["passed"])
        stability = [gate for gate in admission["gates"]
                     if gate.get("effect") == "per_seed_percent"]
        self.assertEqual(len(stability), 10)
        self.assertEqual({int(gate["seed"]) for gate in stability},
                         {132, 133, 134, 135, 136})
        self.assertTrue(all(gate["passed"] for gate in stability))
        self.assertEqual(
            admission["small_queue_measurement_correction"]["status"],
            "retained_from_v11_without_relaxation")

    def test_v12_per_seed_gate_can_reject_while_aggregate_ci_passes(self) -> None:
        payload = synthetic_v12_analysis()
        candidate = next(row for row in payload["runs"]
                         if row["seed"] == 132 and row["active_flows"] == 15 and
                         row["controller"] != runner.CONTROL_ARM)
        candidate["performance"][
            "receiver_queue_time_sampled_mean_bytes"] = 91.0
        with tempfile.TemporaryDirectory() as temporary:
            path = self.write(Path(temporary), payload)
            _rows, intervals, admission = aggregator.aggregate(SPEC_V12_PATH, path)
        aggregate_mean = next(
            row for row in intervals if row["active_flows"] == 15 and
            row["metric"] == "receiver_queue_time_sampled_mean_bytes")
        self.assertTrue(aggregate_mean["passed"])
        failed = [gate for gate in admission["gates"]
                  if gate.get("effect") == "per_seed_percent" and
                  gate.get("seed") == 132 and str(gate["metric"]).endswith(
                      "mean_bytes")]
        self.assertEqual(len(failed), 1)
        self.assertFalse(failed[0]["passed"])
        self.assertFalse(admission["passed"])

    def test_v12_prefix_fallback_or_terminal_regression_blocks_ci(self) -> None:
        for field in ("fallback_batches", "terminal_cursor"):
            payload = synthetic_v12_analysis()
            candidate = next(row for row in payload["runs"]
                             if row["active_flows"] == 15 and
                             row["controller"] != runner.CONTROL_ARM)
            candidate["transition_prefix"][field] = 1
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                path = self.write(Path(temporary), payload)
                with self.assertRaisesRegex(SummaryError, "exact-prefix evidence"):
                    aggregator.aggregate(SPEC_V12_PATH, path)


class V13AggregatorTests(unittest.TestCase):
    def write(self, root: Path, payload: dict[str, object]) -> Path:
        path = root / "v13.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_v13_unchanged_ci_stability_and_watchdog_gates_pass(self) -> None:
        spec = runner.read_spec(SPEC_V13_PATH)
        predecessor = runner.read_spec(SPEC_V12_PATH)
        self.assertEqual(
            spec["scalability_holdout"]["paired_non_regression"],
            predecessor["scalability_holdout"]["paired_non_regression"])
        self.assertEqual(
            spec["scalability_holdout"]["n15_advantage_preservation"],
            predecessor["scalability_holdout"]["n15_advantage_preservation"])
        with tempfile.TemporaryDirectory() as temporary:
            path = self.write(Path(temporary), synthetic_v13_analysis())
            rows, intervals, admission = aggregator.aggregate(SPEC_V13_PATH, path)
        self.assertEqual(len(rows), 20)
        self.assertEqual(len(intervals), 26)
        self.assertTrue(admission["passed"])
        stability = [gate for gate in admission["gates"]
                     if gate.get("effect") == "per_seed_percent"]
        self.assertEqual({int(gate["seed"]) for gate in stability},
                         {138, 139, 140, 141, 142})

    def test_v13_record_provenance_and_reconstruction_regressions_block_ci(self) -> None:
        mutations = []
        for field, value in (
                ("records", 2), ("non_reconstructable", 1),
                ("inconsistent", 1), ("terminal_budget", 1),
                ("wire_bytes", 1), ("non_reconstructable", -1),
                ("inconsistent", -1), ("terminal_budget", -1)):
            payload = synthetic_v13_analysis()
            candidate = next(row for row in payload["runs"]
                             if row["active_flows"] == 15 and
                             row["controller"] != runner.CONTROL_ARM)
            if field == "wire_bytes":
                candidate["transition_prefix_watchdog"][field] += value
            else:
                candidate["transition_prefix_watchdog"][field] = value
            mutations.append(payload)
        payload = synthetic_v13_analysis()
        control = next(row for row in payload["runs"]
                       if row["controller"] == runner.CONTROL_ARM)
        control["transition_prefix_watchdog"] = {"enabled": 0, "records": 0}
        mutations.append(payload)
        for ordinal, payload in enumerate(mutations):
            with self.subTest(ordinal=ordinal), tempfile.TemporaryDirectory() as temporary:
                path = self.write(Path(temporary), payload)
                with self.assertRaisesRegex(SummaryError, "watchdog"):
                    aggregator.aggregate(SPEC_V13_PATH, path)

    def test_v13_per_seed_n15_queue_gate_remains_a_conjunction(self) -> None:
        payload = synthetic_v13_analysis()
        candidate = next(row for row in payload["runs"]
                         if row["seed"] == 138 and row["active_flows"] == 15 and
                         row["controller"] != runner.CONTROL_ARM)
        candidate["performance"][
            "receiver_queue_time_sampled_mean_bytes"] = 91.0
        with tempfile.TemporaryDirectory() as temporary:
            path = self.write(Path(temporary), payload)
            _rows, intervals, admission = aggregator.aggregate(SPEC_V13_PATH, path)
        aggregate_mean = next(
            row for row in intervals if row["active_flows"] == 15 and
            row["metric"] == "receiver_queue_time_sampled_mean_bytes")
        self.assertTrue(aggregate_mean["passed"])
        self.assertFalse(admission["passed"])
        failed = [gate for gate in admission["gates"]
                  if gate.get("effect") == "per_seed_percent" and
                  gate.get("seed") == 138 and not gate["passed"]]
        self.assertEqual(len(failed), 1)


if __name__ == "__main__":
    unittest.main()
