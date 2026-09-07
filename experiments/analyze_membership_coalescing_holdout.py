#!/usr/bin/env python3
"""Admit all coalescing mechanisms before reading holdout performance."""

from __future__ import annotations

from collections import Counter, deque
import argparse
import csv
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
from typing import Dict, List, Mapping, Sequence, Tuple

try:
    from experiments.run_campaign import CampaignError, directory_size, sha256_file
    from experiments.run_membership_coalescing_holdout import (
        CONTROL_ARM, GUARD_OPTIONS, holdout_axes, matrix, pilot_admission_path,
        pilot_matrix, read_spec, replay_admission_path, replay_matrix, run_command,
        validate_pilot_admission, validate_replay_admission,
    )
    from experiments.summarize_campaign import (
        SummaryError, parse_guard_stats, parse_port_queue_summaries,
    )
    from experiments.summarize_workload import (
        atomic_json, one_artifact, parse_config, parse_fct, parse_snapshot,
        validate_completions,
    )
except ModuleNotFoundError:  # Direct execution from experiments/.
    from run_campaign import CampaignError, directory_size, sha256_file
    from run_membership_coalescing_holdout import (
        CONTROL_ARM, GUARD_OPTIONS, holdout_axes, matrix, pilot_admission_path,
        pilot_matrix, read_spec, replay_admission_path, replay_matrix, run_command,
        validate_pilot_admission, validate_replay_admission,
    )
    from summarize_campaign import (
        SummaryError, parse_guard_stats, parse_port_queue_summaries,
    )
    from summarize_workload import (
        atomic_json, one_artifact, parse_config, parse_fct, parse_snapshot,
        validate_completions,
    )


TRACE_FIELDS = (
    "time_ns", "event", "set_change", "host_node", "flow_id",
    "data_source_ip", "data_destination_ip", "active_flows",
    "line_rate_bps", "grant_rate_bps", "next_seq", "serialized_bytes",
    "generation", "pending_acks",
)
TRACE_FIELDS_V7 = TRACE_FIELDS + ("ack_required",)
TRACE_FIELDS_V11 = TRACE_FIELDS_V7 + (
    "transaction_id", "grant_phase", "membership_target_n", "subject_role",
)
TRACE_FIELDS_V12 = TRACE_FIELDS_V11 + (
    "prefix_target_bytes", "prefix_observed_bytes", "drain_outcome",
    "activation_batch_index", "activation_batch_size",
    "activation_register_ns",
)
TRACE_EVENTS = (
    "sent", "received", "grant_stale", "grant_generation_zero",
    "grant_generation_mismatch", "ack_sent", "ack_received", "ack_stale",
)
SET_CHANGES = (
    "registration", "release", "membership_batch", "reliability_refresh",
    "demand", "cap_report", "none",
)
COALESCING_FIELDS = (
    "window_ns", "changes_deferred", "batches", "max_batch",
    "empty_cancellations", "timer_reschedules", "max_windows",
    "reliability_rtts", "refresh_events", "refresh_grants",
    "first_grant_gated", "first_grant_released", "generation",
    "ack_sent", "ack_bytes_sent", "ack_received", "ack_bytes_received",
    "ack_stale", "stale_grants", "fully_acked_batches", "pending",
    "retry_grants", "progress_events_coalesced",
)
COALESCING_FIELDS_V7 = (
    *COALESCING_FIELDS[:19], "ack_required_batches", "ack_optional_batches",
    "ack_required_grants", "ack_optional_grants", "generation_zero_rejected",
    "generation_mismatch_rejected", *COALESCING_FIELDS[19:],
)
COALESCING_FIELDS_V8 = (
    *COALESCING_FIELDS_V7[:6], "release_threshold_deferrals",
    *COALESCING_FIELDS_V7[6:],
)
COALESCING_FIELDS_V9 = (
    *COALESCING_FIELDS_V8[:7],
    "initial_collection_starts", "initial_collection_flushes",
    "initial_collection_deferred_changes", "initial_collection_cancellations",
    "initial_collection_wait_ns", "initial_collection_max_wait_ns",
    *COALESCING_FIELDS_V8[7:],
)
COALESCING_FIELDS_V10 = (
    *COALESCING_FIELDS_V8[:7],
    "initial_collection_quiet_ns", "initial_collection_full_deadline",
    "initial_collection_starts", "initial_collection_flushes",
    "initial_collection_deferred_changes", "initial_collection_reschedules",
    "initial_collection_cancellations", "initial_collection_quiet_flushes",
    "initial_collection_hard_flushes", "initial_collection_wait_ns",
    "initial_collection_max_wait_ns", *COALESCING_FIELDS_V8[7:],
)
SMALL_SET_FIELDS_V11 = (
    "enabled", "limit", "transactions",
    "accepted_fast_prepare_batches", "accepted_fast_prepare_grants",
    "accepted_fast_prepare_acks", "accepted_fast_activate_batches",
    "accepted_fast_activate_grants", "accepted_fast_activate_acks",
    "accepted_transition_prepare_batches", "accepted_transition_prepare_grants",
    "accepted_transition_prepare_acks", "accepted_transition_activate_batches",
    "accepted_transition_activate_grants", "accepted_transition_activate_acks",
    "accepted_release_batches", "accepted_release_grants",
    "queued_membership_changes", "high_transitions", "high_collection_flushes",
    "waiters_activated", "post_transition_fast_grants", "barrier_violations",
    "early_unlocks", "join_queue_max", "high_transition_registered_n",
    "high_transition_waiters", "prefix_close_ns", "collection_flush_ns",
    "transition_prepare_start_ns", "last_transaction_close_ns",
    "fast_prepare_wire_grants", "fast_activate_wire_grants",
    "transition_prepare_wire_grants", "transition_activate_wire_grants",
    "release_wire_grants", "fast_prepare_wire_acks",
    "fast_activate_wire_acks", "transition_prepare_wire_acks",
    "transition_activate_wire_acks", "unattributed_grant_frames",
    "unattributed_ack_frames", "wire_grant_frames", "wire_ack_frames",
    "control_frames", "global_grants_sent", "global_acks_sent",
    "wire_reconciled", "terminal_phase", "terminal_pending_acks",
    "terminal_pending_membership", "terminal_waiters",
    "terminal_transaction_waiters", "terminal_ready_waiters",
    "terminal_collection_ready", "terminal_initial_flushed",
    "terminal_transition_committed", "terminal_revision",
    "terminal_consumed_revision",
)
V11_PHASES = {
    "none", "fast_prepare", "fast_activate", "transition_prepare",
    "transition_activate", "release",
}
V11_ROLES = {"none", "incumbent", "waiter"}
V12_DRAIN_OUTCOMES = {"none", "ready", "timeout"}
TRANSITION_PREFIX_FIELDS_V12 = (
    "enabled", "starts", "ready", "timeouts", "degraded",
    "waiters_required", "waiters_ready", "target_bytes", "received_bytes",
    "remaining_bytes", "wait_ns", "max_wait_ns", "start_ns", "deadline_ns",
    "ready_ns", "fallback_batches", "fallback_max_batch",
    "fallback_closed_batches", "order_violations", "barrier_violations",
    "terminal_timer", "terminal_waiting", "terminal_resolved",
    "terminal_timed_out", "terminal_fallback", "terminal_cursor",
    "terminal_cohort", "terminal_future_queue", "terminal_current_batch",
    "terminal_targets", "terminal_holds",
)
TRANSITION_WATCHDOG_FIELDS_V13 = (
    "enabled", "records", "non_reconstructable", "inconsistent",
    "rounded_payload_budget_bytes", "packet_count", "header_per_packet",
    "wire_bytes", "incumbent_count", "occupancy_bps", "capacity_bps",
    "residual_bps", "serialization_ns", "max_rtt_ns", "delay_ns",
    "start_ns", "deadline_ns", "terminal_budget",
)
LIFECYCLE_FIELDS = {
    "flow_id", "size_bytes", "receiver_node", "first_rx_ns", "register_ns",
    "release_ns", "complete_ns", "release_reason", "remaining_bytes_at_release",
    "active_before_register", "active_after_register", "active_before_release",
    "active_after_release",
}
RECOVERY_FIELDS = (
    "recovery_nacks_generated", "recovery_nacks_received",
    "irn_nacks_generated", "irn_nacks_received", "irn_retransmit_packets",
    "irn_retransmit_bytes",
)
CONFIG_KEYS = {
    "guard_lambda": "GUARD_LAMBDA",
    "guard_beta": "GUARD_EWMA_BETA",
    "guard_gamma": "GUARD_RELEASE_GAMMA",
    **{
        option: option.upper()
        for option in GUARD_OPTIONS
        if option not in ("guard_lambda", "guard_beta", "guard_gamma")
    },
}


def read_json(path: Path) -> Mapping[str, object]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise SummaryError(f"JSON root must be an object: {path}")
    return value


def discover_manifests(campaign_dir: Path, spec: Mapping[str, object],
                       scope: str = "holdout") -> Dict[Tuple[int, int, str], Mapping[str, object]]:
    found: Dict[Tuple[int, int, str], Mapping[str, object]] = {}
    if scope == "holdout":
        paths = sorted((campaign_dir / "runs").glob("n*/seed*/*/manifest.json"))
        plan = matrix(spec)
    elif scope == "pilot":
        paths = sorted((campaign_dir / "pilot").rglob("manifest.json"))
        plan = pilot_matrix(spec)
    elif scope == "replay":
        paths = sorted((campaign_dir / "replay").rglob("manifest.json"))
        plan = replay_matrix(spec)
    else:
        raise SummaryError(f"unknown analysis scope: {scope}")
    for path in paths:
        row = read_json(path)
        try:
            identity = (
                int(row["seed"]), int(row["active_flows"]), str(row["controller"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SummaryError(f"malformed run identity: {path}") from exc
        if identity in found:
            raise SummaryError(f"duplicate run manifest: {identity}")
        found[identity] = row
    expected = set(plan)
    observed = set(found)
    if observed != expected or len(found) != len(plan):
        pilot_seed = int(spec["candidate_pilot"]["seed"])
        label = ("40-run holdout" if scope == "holdout" else
                 f"{len(plan)}-run replay" if scope == "replay" else
                 f"{len(plan)}-run seed-{pilot_seed} pilot")
        raise SummaryError(
            f"performance remains sealed: {label} matrix incomplete; "
            f"missing={sorted(expected - observed)}, unexpected={sorted(observed - expected)}")
    return found


def parse_bounded_trace(path: Path, max_lines: int,
                        schema_version: int = 6,
                        v11_enabled: bool = False
                        ) -> Tuple[List[Dict[str, object]], Dict[str, int]]:
    lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    fields = (TRACE_FIELDS_V12 if v11_enabled and schema_version in (12, 13) else
              TRACE_FIELDS_V11 if v11_enabled else
              TRACE_FIELDS_V7 if schema_version >= 7 else TRACE_FIELDS)
    if len(lines) < 2 or tuple(lines[0].split(",")) != fields:
        raise SummaryError(f"unexpected grant trace schema: {path}")
    footer = lines[-1].split()
    if (len(footer) != 7 or footer[:2] != ["#", "attempted"] or
            footer[3] != "written" or footer[5] != "truncated"):
        raise SummaryError(f"malformed grant trace footer: {path}")
    counts = {
        "attempted": int(footer[2]), "written": int(footer[4]),
        "truncated": int(footer[6]),
    }
    data = lines[1:-1]
    if (counts["written"] != len(data) or
            counts["attempted"] != counts["written"] + counts["truncated"] or
            counts["written"] > max_lines or counts["truncated"] != 0):
        raise SummaryError(f"grant trace count/cap/truncation failure: {path}")
    rows: List[Dict[str, object]] = []
    allowed_changes = (set(SET_CHANGES) | (V11_PHASES - {"none"})
                       if v11_enabled else set(SET_CHANGES))
    for line_number, raw in enumerate(csv.DictReader([lines[0], *data]), 2):
        if raw["event"] not in TRACE_EVENTS or raw["set_change"] not in allowed_changes:
            raise SummaryError(f"invalid grant event/change: {path}:{line_number}")
        row: Dict[str, object] = {"event": raw["event"], "set_change": raw["set_change"]}
        if v11_enabled:
            if raw["grant_phase"] not in V11_PHASES or raw["subject_role"] not in V11_ROLES:
                raise SummaryError(f"invalid V11 phase/role: {path}:{line_number}")
            row["grant_phase"] = raw["grant_phase"]
            row["subject_role"] = raw["subject_role"]
            if schema_version in (12, 13):
                if raw["drain_outcome"] not in V12_DRAIN_OUTCOMES:
                    raise SummaryError(
                        f"invalid V12 drain outcome: {path}:{line_number}")
                row["drain_outcome"] = raw["drain_outcome"]
        try:
            for field in fields:
                if field not in ("event", "set_change", "grant_phase", "subject_role",
                                 "drain_outcome"):
                    row[field] = int(raw[field])
        except (TypeError, ValueError) as exc:
            raise SummaryError(f"non-integer grant trace field: {path}:{line_number}") from exc
        rows.append(row)
    return rows, counts


def parse_coalescing_stats(path: Path, schema_version: int = 6) -> Dict[str, object]:
    matches = []
    for line in path.read_text(encoding="utf-8", errors="strict").splitlines():
        parts = line.split()
        if parts[:1] == ["guard_membership_coalescing"]:
            matches.append(parts[1:])
    fields = (COALESCING_FIELDS_V10 if schema_version >= 10 else
              COALESCING_FIELDS_V9 if schema_version == 9 else
              COALESCING_FIELDS_V8 if schema_version == 8 else
              COALESCING_FIELDS_V7 if schema_version == 7 else COALESCING_FIELDS)
    if len(matches) != 1:
        raise SummaryError(f"expected one complete membership stats row: {path}")
    tokens = matches[0]
    # A V9 campaign remains reproducible on the V10 simulator, whose stats row
    # appends mode/reason fields while retaining fixed-full-deadline behavior.
    if (schema_version == 9 and len(tokens) == 2 * len(COALESCING_FIELDS_V10) and
            tuple(tokens[::2]) == COALESCING_FIELDS_V10):
        fields = COALESCING_FIELDS_V10
    if len(tokens) != 2 * len(fields):
        raise SummaryError(f"expected one complete membership stats row: {path}")
    if tuple(tokens[::2]) != fields:
        raise SummaryError(f"membership stats labels changed: {path}")
    result: Dict[str, object] = {}
    for field, raw in zip(tokens[::2], tokens[1::2]):
        try:
            result[field] = float(raw) if field == "reliability_rtts" else int(raw)
        except ValueError as exc:
            raise SummaryError(f"invalid membership stat {field}: {path}") from exc
    return result


def parse_small_set_stats(path: Path) -> Dict[str, int]:
    matches = []
    for line in path.read_text(encoding="utf-8", errors="strict").splitlines():
        parts = line.split()
        if parts[:1] == ["guard_small_set_fastpath"]:
            matches.append(parts[1:])
    if len(matches) != 1:
        raise SummaryError(f"expected one complete V11 small-set stats row: {path}")
    tokens = matches[0]
    if (len(tokens) != 2 * len(SMALL_SET_FIELDS_V11) or
            tuple(tokens[::2]) != SMALL_SET_FIELDS_V11):
        raise SummaryError(f"V11 small-set stats labels changed: {path}")
    try:
        return {field: int(raw) for field, raw in zip(tokens[::2], tokens[1::2])}
    except ValueError as exc:
        raise SummaryError(f"invalid V11 small-set stat: {path}") from exc


def parse_transition_prefix_stats(path: Path) -> Dict[str, int]:
    """Parse the single fixed-label V12 exact-prefix stats row."""
    matches = []
    for line in path.read_text(encoding="utf-8", errors="strict").splitlines():
        parts = line.split()
        if parts[:1] == ["guard_transition_prefix"]:
            matches.append(parts[1:])
    if len(matches) != 1:
        raise SummaryError(f"expected one complete V12 transition-prefix row: {path}")
    tokens = matches[0]
    if (len(tokens) != 2 * len(TRANSITION_PREFIX_FIELDS_V12) or
            tuple(tokens[::2]) != TRANSITION_PREFIX_FIELDS_V12):
        raise SummaryError(f"V12 transition-prefix stats labels changed: {path}")
    try:
        return {field: int(raw)
                for field, raw in zip(tokens[::2], tokens[1::2])}
    except ValueError as exc:
        raise SummaryError(f"invalid V12 transition-prefix stat: {path}") from exc


def parse_transition_watchdog_stats(path: Path) -> Dict[str, int] | None:
    """Parse V13's optional, fixed-label wire-watchdog evidence row."""
    matches = []
    for line in path.read_text(encoding="utf-8", errors="strict").splitlines():
        parts = line.split()
        if parts[:1] == ["guard_transition_prefix_watchdog"]:
            matches.append(parts[1:])
    if not matches:
        return None
    if len(matches) != 1:
        raise SummaryError(f"expected at most one complete V13 watchdog row: {path}")
    tokens = matches[0]
    if (len(tokens) != 2 * len(TRANSITION_WATCHDOG_FIELDS_V13) or
            tuple(tokens[::2]) != TRANSITION_WATCHDOG_FIELDS_V13):
        raise SummaryError(f"V13 watchdog stats labels changed: {path}")
    try:
        result = {field: int(raw)
                  for field, raw in zip(tokens[::2], tokens[1::2])}
    except ValueError as exc:
        raise SummaryError(f"invalid V13 watchdog stat: {path}") from exc
    if (any(value < 0 for value in result.values()) or
            any(result[field] not in (0, 1) for field in (
                "enabled", "non_reconstructable", "inconsistent"))):
        raise SummaryError(f"invalid V13 watchdog stat domain: {path}")
    return result


def parse_lifecycle(path: Path, max_lines: int) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    with path.open(encoding="utf-8", errors="strict", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or set(reader.fieldnames) != LIFECYCLE_FIELDS:
            raise SummaryError(f"unexpected lifecycle schema: {path}")
        for line_number, raw in enumerate(reader, 2):
            row: Dict[str, object] = {"release_reason": raw["release_reason"]}
            try:
                for field in LIFECYCLE_FIELDS - {"release_reason"}:
                    row[field] = int(raw[field])
            except (TypeError, ValueError) as exc:
                raise SummaryError(f"non-integer lifecycle field: {path}:{line_number}") from exc
            rows.append(row)
    if len(rows) > max_lines:
        raise SummaryError(f"lifecycle trace exceeds {max_lines} rows: {path}")
    return rows


def _numeric_config(config: Mapping[str, str], key: str, expected: object) -> None:
    try:
        actual = float(config[key])
        wanted = float(expected)
    except (KeyError, TypeError, ValueError) as exc:
        raise SummaryError(f"config lacks numeric {key}") from exc
    if actual != wanted:
        raise SummaryError(f"config {key}={config[key]!r}, expected {expected!r}")


def validate_config(config: Mapping[str, str], spec: Mapping[str, object], seed: int,
                    arm: str) -> None:
    pilot = dict(spec["candidate_pilot"])
    limits = dict(spec["resource_limits"])
    exact = {
        "TOPOLOGY_FILE": f"config/{pilot['topology']}.txt",
        "MONITOR_PROFILE": "bulk", "CC_MODE": "11", "LB_MODE": "0",
        "ENABLE_PFC": "1", "ENABLE_IRN": "0", "BUFFER_SIZE": "9",
        "GUARD_LIFECYCLE_TRACE": "1", "GUARD_CONTROLLER_TRACE": "0",
        "GUARD_GRANT_TRACE": "1",
    }
    for key, wanted in exact.items():
        if config.get(key) != wanted:
            raise SummaryError(f"config {key}={config.get(key)!r}, expected {wanted!r}")
    numeric = {
        "ANALYSIS_WARMUP_TIME": pilot["analysis_warmup_s"],
        "PREFLIGHT_MAX_FLOWS": limits["max_flows"], "RANDOM_SEED": seed,
        "GUARD_LIFECYCLE_TRACE_MAX_LINES": limits["lifecycle_trace_max_lines"],
        "GUARD_GRANT_TRACE_MAX_LINES": limits["grant_trace_max_lines"],
        "GUARD_MEMBERSHIP_COALESCE_NS": pilot["membership_quiet_ns"][arm],
        "GUARD_MEMBERSHIP_COALESCE_MAX_WINDOWS": pilot["membership_max_windows"],
        "GUARD_GRANT_RELIABILITY_RTTS": pilot["grant_reliability_service_rounds"][arm],
    }
    if int(spec.get("schema_version", 0)) >= 10:
        numeric.update({
            "GUARD_INITIAL_COLLECTION_FULL_DEADLINE":
                pilot["initial_collection_full_deadline"][arm],
            "GUARD_INITIAL_COLLECTION_QUIET_NS":
                pilot["initial_collection_quiet_ns"][arm],
        })
    if int(spec.get("schema_version", 0)) in (11, 12, 13):
        numeric["GUARD_SMALL_SET_FASTPATH_LIMIT"] = pilot[
            "small_set_fastpath_limit"][arm]
    if int(spec.get("schema_version", 0)) in (12, 13):
        numeric["GUARD_TRANSITION_PREFIX_BARRIER"] = pilot[
            "transition_prefix_barrier"][arm]
    if int(spec.get("schema_version", 0)) == 13:
        numeric["PACKET_PAYLOAD_SIZE"] = spec[
            "transition_prefix_watchdog_protocol"]["mtu_bytes"]
        watchdog_expected = pilot["transition_prefix_wire_watchdog"][arm]
        # run.py deliberately omits the default-off key to preserve the V12
        # config shape. The frozen manifest CLI is validated separately and
        # still must contain an explicit zero for the V13 control arm.
        if (float(watchdog_expected) != 0.0 or
                "GUARD_TRANSITION_PREFIX_WIRE_WATCHDOG" in config):
            _numeric_config(
                config, "GUARD_TRANSITION_PREFIX_WIRE_WATCHDOG",
                watchdog_expected)
    numeric.update({CONFIG_KEYS[key]: value for key, value in GUARD_OPTIONS.items()})
    for key, wanted in numeric.items():
        _numeric_config(config, key, wanted)


def parse_pfc_intervals(path: Path) -> Dict[int, Dict[str, int]]:
    priorities: Dict[int, Dict[str, int]] = {}
    active: Dict[Tuple[int, int, int], int] = {}
    with path.open(encoding="utf-8", errors="strict") as stream:
        for line_number, line in enumerate(stream, 1):
            parts = line.split()
            if not parts or parts[0].startswith("#"):
                continue
            if len(parts) != 7 or parts[5] not in ("0", "1"):
                raise SummaryError(f"malformed PFC row {path}:{line_number}")
            try:
                time_ns, node_id, _node_type, if_index, priority, event, _pause = map(
                    int, parts)
            except ValueError as exc:
                raise SummaryError(f"non-integer PFC row {path}:{line_number}") from exc
            row = priorities.setdefault(priority, {
                "pause_count": 0, "resume_count": 0, "matched_intervals": 0,
                "cumulative_pause_ns": 0, "max_pause_ns": 0,
                "unmatched_pauses": 0, "unmatched_resumes": 0,
            })
            key = (node_id, if_index, priority)
            if event == 1:
                row["pause_count"] += 1
                active.setdefault(key, time_ns)
            else:
                row["resume_count"] += 1
                if key not in active:
                    row["unmatched_resumes"] += 1
                else:
                    duration = time_ns - active.pop(key)
                    if duration < 0:
                        raise SummaryError(f"negative PFC interval {path}:{line_number}")
                    row["matched_intervals"] += 1
                    row["cumulative_pause_ns"] += duration
                    row["max_pause_ns"] = max(row["max_pause_ns"], duration)
    for _node, _interface, priority in active:
        priorities[priority]["unmatched_pauses"] += 1
    return priorities


def pfc_audit(stats: Mapping[str, object], path: Path) -> Dict[str, int]:
    raw_priorities = parse_pfc_intervals(path)
    summary_priorities = stats.get("pfc_priority", {})
    for priority in set(raw_priorities) | set(summary_priorities):
        for field in (
                "pause_count", "resume_count", "matched_intervals",
                "cumulative_pause_ns", "max_pause_ns", "unmatched_pauses",
                "unmatched_resumes"):
            left = int(dict(raw_priorities.get(priority, {})).get(field, 0))
            right = int(dict(summary_priorities.get(priority, {})).get(field, 0))
            if left != right:
                raise SummaryError(f"PFC q{priority} {field}: trace={left}, stats={right}")
    result = {
        "pause_events": sum(int(row["pause_count"]) for row in summary_priorities.values()),
        "resume_events": sum(int(row["resume_count"]) for row in summary_priorities.values()),
        "matched_intervals": sum(int(row["matched_intervals"])
                                 for row in summary_priorities.values()),
        "cumulative_pause_ns": sum(int(row["cumulative_pause_ns"])
                                   for row in summary_priorities.values()),
        "max_pause_ns": max(
            (int(row["max_pause_ns"]) for row in summary_priorities.values()), default=0),
        "unmatched_pauses": sum(int(row["unmatched_pauses"])
                                 for row in summary_priorities.values()),
        "unmatched_resumes": sum(int(row["unmatched_resumes"])
                                  for row in summary_priorities.values()),
    }
    if result["unmatched_pauses"] or result["unmatched_resumes"]:
        raise SummaryError("PFC trace contains unmatched pause/resume events")
    return result


def lifecycle_audit(rows: Sequence[Mapping[str, object]], count: int,
                    receiver: int) -> Tuple[Dict[str, object], set[int]]:
    if len(rows) != count:
        raise SummaryError(f"lifecycle rows {len(rows)} != N={count}")
    ids = {int(row["flow_id"]) for row in rows}
    if len(ids) != count:
        raise SummaryError("lifecycle flow IDs are not unique")
    events: List[Tuple[int, int]] = []
    for row in rows:
        flow_id = int(row["flow_id"])
        if (int(row["receiver_node"]) != receiver or
                row["release_reason"] != "completion" or
                int(row["remaining_bytes_at_release"]) != 0 or
                int(row["release_ns"]) != int(row["complete_ns"])):
            raise SummaryError(f"flow {flow_id} lacks completion-only lifecycle release")
        events.extend([(int(row["register_ns"]), 1), (int(row["release_ns"]), -1)])
    active = maximum = 0
    for _time, change in sorted(events, key=lambda item: (item[0], -item[1])):
        active += change
        maximum = max(maximum, active)
        if active < 0:
            raise SummaryError("lifecycle active set became negative")
    if active != 0 or maximum != count:
        raise SummaryError(f"lifecycle maximum active set {maximum}, expected {count}")
    return {
        "rows": len(rows), "max_active_flows": maximum,
        "completion_only_releases": count,
    }, ids


def _event_map(rows: Sequence[Mapping[str, object]], event: str) -> Dict[Tuple[int, int], Mapping[str, object]]:
    selected = [row for row in rows if row["event"] == event]
    result: Dict[Tuple[int, int], Mapping[str, object]] = {}
    for row in selected:
        key = (int(row["generation"]), int(row["flow_id"]))
        if key in result:
            raise SummaryError(f"duplicate {event} for generation/flow {key}")
        result[key] = row
    return result


def selective_generation_closure(
        events: Mapping[str, Sequence[Mapping[str, object]]],
        membership: Mapping[str, object],
        geometric_release: bool = False) -> Tuple[List[int], List[Dict[str, object]]]:
    """Validate the V7 ACK-required/optional policy generation by generation."""
    maps = {event: _event_map(events[event], event)
            for event in ("sent", "received", "ack_sent", "ack_received")}
    grant_keys = set(maps["sent"])
    if not grant_keys or set(maps["received"]) != grant_keys:
        raise SummaryError("generation/flow grant identities do not close")
    generations = sorted({generation for generation, _flow_id in grant_keys})
    if generations != list(range(1, int(membership["generation"]) + 1)):
        raise SummaryError("membership generations are not contiguous from one")
    if len(generations) != int(membership["batches"]):
        raise SummaryError("membership generation count differs from batch count")

    conservative_upper: Dict[int, int] = {}
    required_keys: set[Tuple[int, int]] = set()
    details: List[Dict[str, object]] = []
    required_count = optional_count = 0
    required_grants = optional_grants = 0
    previous_vector_active: int | None = None
    for generation in generations:
        current_rows = {
            flow_id: maps["sent"][(generation, flow_id)]
            for gen, flow_id in grant_keys if gen == generation
        }
        current = {flow_id: int(row["grant_rate_bps"])
                   for flow_id, row in current_rows.items()}
        if any(int(row["active_flows"]) != len(current)
               for row in current_rows.values()):
            raise SummaryError(
                f"generation {generation} active_flows does not match its grant set")
        joins = set(current) - set(conservative_upper)
        releases = set(conservative_upper) - set(current)
        decreases = {flow_id for flow_id in set(current) & set(conservative_upper)
                     if current[flow_id] < conservative_upper[flow_id]}
        required = bool(joins or decreases)
        if generation == generations[0] and not required:
            raise SummaryError("initial generation must require ACK closure")
        grant_rows = [maps[event][(generation, flow_id)]
                      for event in ("sent", "received") for flow_id in current]
        values = {int(row["ack_required"]) for row in grant_rows}
        if values != {int(required)}:
            raise SummaryError(
                f"generation {generation} ack_required disagrees with join/decrease policy")
        keys = {(generation, flow_id) for flow_id in current}
        if required:
            required_count += 1
            required_grants += len(keys)
            required_keys |= keys
            if any(key not in maps["ack_sent"] or key not in maps["ack_received"]
                   for key in keys):
                raise SummaryError(f"ACK-required generation {generation} does not fully close")
            for key in sorted(keys):
                ordered = (maps["sent"][key], maps["received"][key],
                           maps["ack_sent"][key], maps["ack_received"][key])
                if {int(row["ack_required"]) for row in ordered} != {1}:
                    raise SummaryError(f"generation {generation} ACK row lacks ack_required=1")
                if any(int(row["serialized_bytes"]) != 60 for row in ordered):
                    raise SummaryError(f"generation {generation} control frame is not 60 bytes")
                times = tuple(int(row["time_ns"]) for row in ordered)
                if times != tuple(sorted(times)):
                    raise SummaryError(f"grant/ACK event order is invalid for {key}: {times}")
            final_ack = max((maps["ack_received"][key] for key in keys),
                            key=lambda row: int(row["time_ns"]))
            if int(final_ack["pending_acks"]) != 0:
                raise SummaryError(f"generation {generation} does not terminate at pending=0")
            classification = "join_or_decrease_ack_required"
        else:
            optional_count += 1
            optional_grants += len(keys)
            if not releases:
                raise SummaryError(
                    f"ACK-optional generation {generation} is not a pure release")
            if any(key in maps["ack_sent"] or key in maps["ack_received"] for key in keys):
                raise SummaryError(f"ACK-optional generation {generation} emitted ACK frames")
            if any(int(row["pending_acks"]) != 0 for row in grant_rows):
                raise SummaryError(f"ACK-optional generation {generation} has pending ACK state")
            if (geometric_release and
                    (previous_vector_active is None or
                     len(current) > previous_vector_active // 2)):
                limit = (-1 if previous_vector_active is None else
                         previous_vector_active // 2)
                raise SummaryError(
                    f"ACK-optional release generation {generation} active={len(current)} "
                    f"exceeds floor(previous_active/2)={limit}")
            classification = "pure_release_non_decreasing_ack_optional"
        details.append({
            "generation": generation, "ack_required": required,
            "classification": classification, "joins": len(joins),
            "releases": len(releases), "decreases": len(decreases),
            "previous_vector_active": previous_vector_active,
            "active_flows": len(current),
            "grant_frames_sent": len(keys),
            "ack_frames_sent": len(keys) if required else 0,
        })
        if required:
            conservative_upper = dict(current)
        else:
            conservative_upper = {
                flow_id: max(conservative_upper[flow_id], rate)
                for flow_id, rate in current.items()
            }
        previous_vector_active = len(current)

    if set(maps["ack_sent"]) != required_keys or set(maps["ack_received"]) != required_keys:
        raise SummaryError("ACK frames exist outside the ACK-required generation/flow set")
    if (int(membership["ack_required_batches"]) != required_count or
            int(membership["ack_optional_batches"]) != optional_count or
            int(membership["ack_required_grants"]) != required_grants or
            int(membership["ack_optional_grants"]) != optional_grants or
            int(membership["fully_acked_batches"]) != required_count or
            required_count + optional_count != int(membership["batches"])):
        raise SummaryError("selective-ACK batch counters do not match per-generation evidence")
    return generations, details


def generation_audit(rows: Sequence[Mapping[str, object]], stats: Mapping[str, object],
                     membership: Mapping[str, object], count: int, arm: str,
                     receiver: int, flow_ids: set[int], error_limit: int,
                     schema_version: int = 6,
                     registration_times: Mapping[int, int] | None = None
                     ) -> Dict[str, object]:
    events = {event: [row for row in rows if row["event"] == event]
              for event in TRACE_EVENTS}
    sent = events["sent"]
    received = events["received"]
    sent_multiset = Counter((int(row["generation"]), int(row["flow_id"]),
                             int(row["grant_rate_bps"]), int(row["serialized_bytes"]),
                             int(row.get("ack_required", 0)))
                            for row in sent)
    received_multiset = Counter((int(row["generation"]), int(row["flow_id"]),
                                 int(row["grant_rate_bps"]), int(row["serialized_bytes"]),
                                 int(row.get("ack_required", 0)))
                                for row in received)
    if sent_multiset != received_multiset:
        raise SummaryError("generation-tagged grant send/receive multisets differ")
    if len(sent) != int(stats["grants_sent"]) or len(received) != int(stats["grants_received"]):
        raise SummaryError("grant trace counts differ from packet counters")
    if len(sent) != len(received):
        raise SummaryError("grant sends do not equal receives")
    if any(int(row["serialized_bytes"]) != 60 for row in sent + received):
        raise SummaryError("grant frame size differs from 60 bytes")
    if sum(int(row["serialized_bytes"]) for row in sent) != int(stats["grant_bytes_sent"]):
        raise SummaryError("grant trace bytes differ from stats")
    stale_events = (events["grant_stale"] + events["grant_generation_zero"] +
                    events["grant_generation_mismatch"] + events["ack_stale"])
    retry_rows = [row for row in sent if row["set_change"] == "reliability_refresh"]
    if stale_events or retry_rows:
        raise SummaryError("normal-path stale or retry trace event is nonzero")
    initial_keys: set[int] = set()
    late_registrations = 0
    registration_values = sorted(map(int, registration_times.values())) if registration_times else []
    first_registration_ns = registration_values[0] if registration_values else -1
    last_registration_ns = registration_values[-1] if registration_values else -1
    registration_span_ns = (
        last_registration_ns - first_registration_ns if registration_values else -1)
    max_registration_gap_ns = max(
        (right - left for left, right in zip(registration_values, registration_values[1:])),
        default=0)
    first_flush_ns = -1
    initial_send_timestamp_count = 0
    if arm == CONTROL_ARM:
        if any(int(row["generation"]) != 0 for row in sent + received):
            raise SummaryError("control grant has a nonzero generation")
        if events["ack_sent"] or events["ack_received"]:
            raise SummaryError("control emitted generation ACKs")
        if any(int(membership[field]) != 0 for field in (
                "batches", "generation", "ack_sent", "ack_received", "ack_stale",
                "stale_grants", "fully_acked_batches", "pending", "retry_grants")):
            raise SummaryError("control generation/ACK counters are nonzero")
        if schema_version >= 7 and (
                any(int(row["ack_required"]) != 0 for row in sent + received) or
                int(membership["ack_required_batches"]) != 0 or
                int(membership["ack_optional_batches"]) != 0 or
                int(membership["ack_required_grants"]) != 0 or
                int(membership["ack_optional_grants"]) != 0 or
                int(membership["generation_zero_rejected"]) != 0 or
                int(membership["generation_mismatch_rejected"]) != 0):
            raise SummaryError("control selective-ACK fields are nonzero")
        generations: List[int] = []
        generation_details: List[Dict[str, object]] = []
    else:
        if schema_version >= 7:
            generations, generation_details = selective_generation_closure(
                events, membership, geometric_release=schema_version >= 8)
            maps = {event: _event_map(rows, event)
                    for event in ("sent", "received", "ack_sent", "ack_received")}
        else:
            generation_details = []
            maps = {event: _event_map(rows, event)
                    for event in ("sent", "received", "ack_sent", "ack_received")}
        keys = set(maps["sent"])
        if schema_version == 6 and (not keys or any(set(mapping) != keys for mapping in maps.values())):
            raise SummaryError("generation/flow identities do not close across grant and ACK events")
        for key in sorted(keys) if schema_version == 6 else ():
            grant_send = maps["sent"][key]
            grant_receive = maps["received"][key]
            ack_send = maps["ack_sent"][key]
            ack_receive = maps["ack_received"][key]
            times = tuple(int(row["time_ns"])
                          for row in (grant_send, grant_receive, ack_send, ack_receive))
            if times != tuple(sorted(times)):
                raise SummaryError(f"grant/ACK event order is invalid for {key}: {times}")
            if int(grant_send["serialized_bytes"]) != 60 or int(ack_send["serialized_bytes"]) != 60:
                raise SummaryError(f"control frame size differs from 60 bytes for {key}")
        if schema_version == 6:
            generations = sorted({generation for generation, _flow_id in keys})
        if schema_version >= 8:
            if not generations:
                raise SummaryError(f"V{schema_version} candidate has no membership generation")
            initial_generation = generations[0]
            initial_keys = {
                flow_id for generation, flow_id in maps["sent"]
                if generation == initial_generation
            }
            if initial_keys != flow_ids or len(initial_keys) != count:
                raise SummaryError(
                    f"V{schema_version} initial generation covers {len(initial_keys)} "
                    f"flows, expected all N={count}")
            if registration_times is None or set(registration_times) != flow_ids:
                raise SummaryError(
                    f"V{schema_version} lifecycle registration evidence is unavailable")
            initial_send_times = {
                int(maps["sent"][(initial_generation, flow_id)]["time_ns"])
                for flow_id in initial_keys
            }
            initial_send_timestamp_count = len(initial_send_times)
            first_flush_ns = min(initial_send_times)
            late_registrations = sum(
                int(register_ns) > first_flush_ns
                for register_ns in registration_times.values())
            if late_registrations:
                raise SummaryError(
                    f"V{schema_version} has {late_registrations} registration(s) after "
                    "initial generation emission")
            if schema_version in (9, 10):
                if initial_send_timestamp_count != 1:
                    raise SummaryError(
                        f"V{schema_version} initial generation does not share one send timestamp")
                if first_registration_ns < 0:
                    raise SummaryError(
                        f"V{schema_version} initial registration timestamp is unavailable")
                if (schema_version == 9 and
                        first_flush_ns - first_registration_ns != 62400):
                    raise SummaryError(
                        "V9 initial flush is not exactly 62400 ns after first registration")
                if last_registration_ns > first_flush_ns:
                    raise SummaryError(
                        f"V{schema_version} last registration follows the initial flush")
            later = generation_details[1:]
            if any(bool(detail["ack_required"]) or int(detail["joins"]) != 0
                   for detail in later):
                raise SummaryError(
                    f"V{schema_version} generations after the initial batch must be "
                    "ACK-optional releases "
                    "with no registrations")
        if schema_version == 6 and generations != list(range(1, int(membership["generation"]) + 1)):
            raise SummaryError("membership generations are not contiguous from one")
        if schema_version == 6 and (len(generations) != int(membership["batches"]) or
                int(membership["fully_acked_batches"]) != int(membership["batches"])):
            raise SummaryError("membership batches are not all fully acknowledged")
        for generation in generations if schema_version == 6 else ():
            generation_keys = {key for key in keys if key[0] == generation}
            final_ack = max(
                (maps["ack_received"][key] for key in generation_keys),
                key=lambda row: int(row["time_ns"]))
            if int(final_ack["pending_acks"]) != 0:
                raise SummaryError(f"generation {generation} does not terminate at pending=0")
        if (len(events["ack_sent"]) != int(membership["ack_sent"]) or
                len(events["ack_received"]) != int(membership["ack_received"])):
            raise SummaryError("ACK trace counts differ from membership stats")
        if sum(int(row["serialized_bytes"]) for row in events["ack_sent"]) != int(
                membership["ack_bytes_sent"]):
            raise SummaryError("ACK send bytes differ from membership stats")
        if sum(int(row["serialized_bytes"]) for row in events["ack_received"]) != int(
                membership["ack_bytes_received"]):
            raise SummaryError("ACK receive bytes differ from membership stats")
        frames = len(sent) + len(events["ack_sent"])
        if ((schema_version >= 8 and frames >= 3 * count) or
                (schema_version < 8 and frames > 3 * count)):
            relation = "must be below" if schema_version >= 8 else "exceed"
            raise SummaryError(
                f"candidate control frames F={frames} {relation} 3N={3 * count}")
    max_active = [row for row in sent if int(row["host_node"]) == receiver and
                  int(row["flow_id"]) in flow_ids and int(row["active_flows"]) == count and
                  row["set_change"] != "demand"]
    final_by_flow: Dict[int, Mapping[str, object]] = {}
    for row in max_active:
        flow_id = int(row["flow_id"])
        if flow_id not in final_by_flow or int(row["time_ns"]) >= int(final_by_flow[flow_id]["time_ns"]):
            final_by_flow[flow_id] = row
    if set(final_by_flow) != flow_ids:
        raise SummaryError("not every target flow has a max-active C/N grant")
    line_rates = {int(row["line_rate_bps"]) for row in final_by_flow.values()}
    if len(line_rates) != 1:
        raise SummaryError("max-active grants disagree on receiver line rate")
    line_rate = next(iter(line_rates))
    expected = line_rate / count
    error = max(abs(int(row["grant_rate_bps"]) - expected)
                for row in final_by_flow.values())
    if error > error_limit:
        raise SummaryError(f"final C/N error {error}bps exceeds {error_limit}bps")
    return {
        "grant_generations": generations,
        "grant_frames_sent": len(sent),
        "grant_frames_received": len(received),
        "grant_ack_frames_sent": len(events["ack_sent"]),
        "grant_ack_frames_received": len(events["ack_received"]),
        "total_control_frames_sent": len(sent) + len(events["ack_sent"]),
        "pending_generations": 0,
        "retry_grants": len(retry_rows), "stale_events": len(stale_events),
        "fully_acked_membership_batches": int(membership["fully_acked_batches"]),
        "ack_required_batches": int(membership.get("ack_required_batches", len(generations))),
        "ack_optional_batches": int(membership.get("ack_optional_batches", 0)),
        "ack_required_grants": int(membership.get("ack_required_grants", len(events["ack_sent"]))),
        "ack_optional_grants": int(membership.get("ack_optional_grants", 0)),
        "generation_zero_rejected": int(membership.get("generation_zero_rejected", 0)),
        "generation_mismatch_rejected": int(
            membership.get("generation_mismatch_rejected", 0)),
        "release_threshold_deferrals": int(
            membership.get("release_threshold_deferrals", 0)),
        "initial_generation_flows": len(initial_keys),
        "initial_generation_send_timestamp_count": initial_send_timestamp_count,
        "post_initial_registration_events": late_registrations,
        "first_registration_ns": first_registration_ns,
        "last_registration_ns": last_registration_ns,
        "first_flush_ns": first_flush_ns,
        "first_flush_minus_first_registration_ns": (
            first_flush_ns - first_registration_ns
            if first_flush_ns >= 0 and first_registration_ns >= 0 else -1),
        "first_flush_minus_last_registration_ns": (
            first_flush_ns - last_registration_ns
            if first_flush_ns >= 0 and last_registration_ns >= 0 else -1),
        "registration_span_ns": registration_span_ns,
        "max_registration_gap_ns": max_registration_gap_ns,
        "generation_contract": generation_details,
        "line_rate_bps": line_rate, "final_c_over_n_bps": expected,
        "final_c_over_n_error_bps_max": error,
    }


def v11_phase_audit(rows: Sequence[Mapping[str, object]],
                    stats: Mapping[str, object],
                    membership: Mapping[str, object],
                    small: Mapping[str, int], count: int, receiver: int,
                    flow_ids: set[int], registration_times: Mapping[int, int],
                    error_limit: int,
                    frame_bound: int) -> Dict[str, object]:
    """Reconcile every V11 safe-activation phase with raw wire evidence."""
    events = {event: [row for row in rows if row["event"] == event]
              for event in TRACE_EVENTS}
    if any(events[event] for event in (
            "grant_stale", "grant_generation_zero", "grant_generation_mismatch",
            "ack_stale")):
        raise SummaryError("V11 normal path contains stale/rejected trace events")
    sent = _event_map(rows, "sent")
    received = _event_map(rows, "received")
    ack_sent = _event_map(rows, "ack_sent")
    ack_received = _event_map(rows, "ack_received")
    if not sent or set(sent) != set(received):
        raise SummaryError("V11 grant generation/flow identities do not close")
    if (len(sent) != int(stats["grants_sent"]) or
            len(received) != int(stats["grants_received"]) or
            sum(int(row["serialized_bytes"]) for row in sent.values()) !=
                int(stats["grant_bytes_sent"])):
        raise SummaryError("V11 grant trace and global packet counters differ")
    required = {key for key, row in sent.items()
                if str(row["grant_phase"]) != "release"}
    if set(ack_sent) != required or set(ack_received) != required:
        raise SummaryError("V11 ACK identities differ from required prepare/activate grants")
    if (len(ack_sent) != int(membership["ack_sent"]) or
            len(ack_received) != int(membership["ack_received"]) or
            sum(int(row["serialized_bytes"]) for row in ack_sent.values()) !=
                int(membership["ack_bytes_sent"]) or
            sum(int(row["serialized_bytes"]) for row in ack_received.values()) !=
                int(membership["ack_bytes_received"])):
        raise SummaryError("V11 ACK trace and membership counters differ")

    expected_role = {
        "fast_prepare": "incumbent", "fast_activate": "waiter",
        "transition_prepare": "incumbent", "transition_activate": "waiter",
        "release": "incumbent",
    }
    for key in sorted(sent):
        grant = sent[key]
        arrival = received[key]
        phase = str(grant["grant_phase"])
        if (phase not in expected_role or str(grant["subject_role"]) != expected_role[phase] or
                str(arrival["grant_phase"]) != phase or
                str(arrival["subject_role"]) != expected_role[phase] or
                str(grant["set_change"]) != phase or
                str(arrival["set_change"]) != "none" or
                int(grant["generation"]) <= 0 or int(grant["serialized_bytes"]) != 60 or
                int(arrival["serialized_bytes"]) != 60 or
                int(arrival["transaction_id"]) != 0 or
                int(arrival["membership_target_n"]) != 0):
            raise SummaryError(f"V11 grant phase/role/wire schema is invalid for {key}")
        if (int(grant["grant_rate_bps"]) != int(arrival["grant_rate_bps"]) or
                int(grant["ack_required"]) != (phase != "release") or
                int(arrival["ack_required"]) != (phase != "release")):
            raise SummaryError(f"V11 grant payload/ACK policy differs for {key}")
        if phase == "release":
            if (int(grant["transaction_id"]) != 0 or
                    int(grant["membership_target_n"]) <= 0):
                raise SummaryError(f"V11 optional release authority is invalid for {key}")
            times = (int(grant["time_ns"]), int(arrival["time_ns"]))
        else:
            final_ack = ack_received[key]
            sender_ack = ack_sent[key]
            if (str(sender_ack["grant_phase"]) != phase or
                    str(final_ack["grant_phase"]) != phase or
                    str(sender_ack["subject_role"]) != expected_role[phase] or
                    str(final_ack["subject_role"]) != expected_role[phase] or
                    int(sender_ack["transaction_id"]) != 0 or
                    int(sender_ack["membership_target_n"]) != 0 or
                    int(final_ack["transaction_id"]) != int(grant["transaction_id"]) or
                    int(final_ack["membership_target_n"]) !=
                        int(grant["membership_target_n"]) or
                    int(grant["transaction_id"]) <= 0 or
                    int(grant["membership_target_n"]) <= 0 or
                    int(sender_ack["serialized_bytes"]) != 60 or
                    int(final_ack["serialized_bytes"]) != 60):
                raise SummaryError(f"V11 required phase authority is invalid for {key}")
            times = tuple(int(row["time_ns"])
                          for row in (grant, arrival, sender_ack, final_ack))
        if times != tuple(sorted(times)):
            raise SummaryError(f"V11 grant/ACK ordering is invalid for {key}: {times}")

    transactions: Dict[int, Dict[str, object]] = {}
    for key in required:
        row = sent[key]
        transaction = int(row["transaction_id"])
        phase = str(row["grant_phase"])
        record = transactions.setdefault(transaction, {
            "target_n": int(row["membership_target_n"]), "phases": {},
        })
        if int(record["target_n"]) != int(row["membership_target_n"]):
            raise SummaryError(f"V11 transaction {transaction} target changed")
        phases = record["phases"]
        assert isinstance(phases, dict)
        phases.setdefault(phase, set()).add(int(row["flow_id"]))
    if sorted(transactions) != list(range(1, len(transactions) + 1)):
        raise SummaryError("V11 transaction IDs are not contiguous from one")
    transaction_contract: List[Dict[str, object]] = []
    previous_close = -1
    transition_transactions = 0
    incumbents: set[int] = set()
    for transaction in sorted(transactions):
        record = transactions[transaction]
        phases = record["phases"]
        assert isinstance(phases, dict)
        kinds = {"transition" if str(phase).startswith("transition_") else "fast"
                 for phase in phases}
        if len(kinds) != 1:
            raise SummaryError(f"V11 transaction {transaction} mixes fast/transition phases")
        kind = next(iter(kinds))
        prepare_name = f"{kind}_prepare"
        activate_name = f"{kind}_activate"
        activate = set(phases.get(activate_name, set()))
        prepare = set(phases.get(prepare_name, set()))
        if not activate or set(phases) - {prepare_name, activate_name} or prepare & activate:
            raise SummaryError(f"V11 transaction {transaction} phase sets are invalid")
        if (prepare != incumbents or activate & incumbents or
                int(record["target_n"]) != len(incumbents) + len(activate)):
            raise SummaryError(
                f"V11 transaction {transaction} does not prepare incumbents then activate waiters")
        phase_keys = {phase: [key for key in required
                              if int(sent[key]["transaction_id"]) == transaction and
                              str(sent[key]["grant_phase"]) == phase]
                      for phase in phases}
        first_send = min(int(sent[key]["time_ns"])
                         for keys in phase_keys.values() for key in keys)
        activate_first = min(int(sent[key]["time_ns"])
                             for key in phase_keys[activate_name])
        prepare_last_ack = max((int(ack_received[key]["time_ns"])
                                for key in phase_keys.get(prepare_name, ())),
                               default=-1)
        close_ns = max(int(ack_received[key]["time_ns"])
                       for key in phase_keys[activate_name])
        if (previous_close >= 0 and first_send < previous_close) or (
                prepare_last_ack >= 0 and activate_first < prepare_last_ack):
            raise SummaryError(f"V11 transaction {transaction} crossed an ACK barrier")
        previous_close = close_ns
        if kind == "transition":
            transition_transactions += 1
            if prepare | activate != flow_ids:
                raise SummaryError("V11 transition prepare/activate union != lifecycle")
        elif int(record["target_n"]) > 4:
            raise SummaryError("V11 fast transaction exceeds K=4")
        incumbents.update(activate)
        transaction_contract.append({
            "transaction_id": transaction, "kind": kind,
            "membership_target_n": int(record["target_n"]),
            "prepare_flows": sorted(prepare), "activate_flows": sorted(activate),
            "first_send_ns": first_send, "prepare_last_ack_ns": prepare_last_ack,
            "activate_first_send_ns": activate_first, "close_ns": close_ns,
        })
    if incumbents != flow_ids:
        raise SummaryError("V11 activation phases do not cover each lifecycle flow exactly once")

    phase_prefixes = {
        "fast_prepare": "fast_prepare", "fast_activate": "fast_activate",
        "transition_prepare": "transition_prepare",
        "transition_activate": "transition_activate", "release": "release",
    }
    phase_summary: Dict[str, Dict[str, int]] = {}
    for phase, prefix in phase_prefixes.items():
        phase_grants = [row for row in sent.values() if row["grant_phase"] == phase]
        phase_acks = [row for row in ack_received.values() if row["grant_phase"] == phase]
        batches = len({int(row["generation"]) for row in phase_grants})
        if (int(small[f"accepted_{prefix}_batches"]) != batches or
                int(small[f"accepted_{prefix}_grants"]) != len(phase_grants) or
                (phase != "release" and
                 int(small[f"accepted_{prefix}_acks"]) != len(phase_acks))):
            raise SummaryError(f"V11 accepted {phase} counters differ from trace")
        wire_grants = int(small[f"{prefix}_wire_grants"])
        if wire_grants != len(phase_grants):
            raise SummaryError(f"V11 wire {phase} grant counter differs from trace")
        wire_acks = 0
        if phase != "release":
            wire_acks = int(small[f"{prefix}_wire_acks"])
            if wire_acks != len([row for row in ack_sent.values()
                                if row["grant_phase"] == phase]):
                raise SummaryError(f"V11 wire {phase} ACK counter differs from trace")
        phase_summary[phase] = {
            "batches": batches, "grants": len(phase_grants), "acks": len(phase_acks),
            "wire_grants": wire_grants, "wire_acks": wire_acks,
        }
    if (int(small["accepted_release_grants"]) != phase_summary["release"]["grants"] or
            int(small["wire_grant_frames"]) != len(sent) or
            int(small["wire_ack_frames"]) != len(ack_sent) or
            int(small["control_frames"]) != len(sent) + len(ack_sent) or
            int(small["global_grants_sent"]) != int(stats["grants_sent"]) or
            int(small["global_acks_sent"]) != len(ack_sent) or
            int(small["transactions"]) != len(transactions) or
            int(small["waiters_activated"]) != count or
            int(small["control_frames"]) > frame_bound):
        raise SummaryError("V11 transaction/wire/frame-cap counters do not reconcile")
    if (int(small["enabled"]) != 1 or int(small["limit"]) != 4 or
            int(small["wire_reconciled"]) != 1 or
            int(small["unattributed_grant_frames"]) != 0 or
            int(small["unattributed_ack_frames"]) != 0 or
            int(small["barrier_violations"]) != 0 or
            int(small["early_unlocks"]) != 0 or
            int(small["post_transition_fast_grants"]) != 0):
        raise SummaryError("V11 safety/reconciliation counter failed")
    terminal_fields = (
        "terminal_phase", "terminal_pending_acks", "terminal_pending_membership",
        "terminal_waiters", "terminal_transaction_waiters", "terminal_ready_waiters",
        "terminal_collection_ready", "terminal_initial_flushed",
        "terminal_transition_committed", "terminal_revision",
        "terminal_consumed_revision",
    )
    if any(int(small[field]) != 0 for field in terminal_fields):
        raise SummaryError("V11 terminal phase/revision/waiter state is not empty")
    if count <= 4:
        if (transition_transactions != 0 or int(small["high_transitions"]) != 0 or
                int(small["high_collection_flushes"]) != 0 or
                any(int(membership[field]) != 0 for field in (
                    "initial_collection_starts", "initial_collection_flushes",
                    "initial_collection_deferred_changes",
                    "initial_collection_reschedules",
                    "initial_collection_cancellations",
                    "initial_collection_quiet_flushes",
                    "initial_collection_hard_flushes", "initial_collection_wait_ns",
                    "initial_collection_max_wait_ns")) or
                any(int(small[field]) != 0 for field in (
                    "prefix_close_ns", "collection_flush_ns",
                    "transition_prepare_start_ns"))):
            raise SummaryError("V11 low-N run unexpectedly entered high-fan-in mode")
    else:
        registrations = sorted(map(int, registration_times.values()))
        if len(registrations) != count:
            raise SummaryError("V11 high transition lacks lifecycle registration times")
        first_high_registration = registrations[4]
        last_registration = registrations[-1]
        collection_flush = int(small["collection_flush_ns"])
        first_delta = collection_flush - first_high_registration
        last_delta = collection_flush - last_registration
        quiet_flushes = int(membership["initial_collection_quiet_flushes"])
        hard_flushes = int(membership["initial_collection_hard_flushes"])
        if (transition_transactions != 1 or int(small["high_transitions"]) != 1 or
                int(small["high_collection_flushes"]) !=
                    1 + int(small["accepted_release_batches"]) or
                int(small["high_transition_registered_n"]) != count or
                int(small["high_transition_waiters"]) !=
                    len(next(row for row in transaction_contract
                             if row["kind"] == "transition")["activate_flows"]) or
                int(small["prefix_close_ns"]) <= 0 or
                int(small["collection_flush_ns"]) <= 0 or
                int(small["transition_prepare_start_ns"]) <= 0 or
                int(small["prefix_close_ns"]) > int(small["transition_prepare_start_ns"]) or
                int(small["collection_flush_ns"]) >
                    int(small["transition_prepare_start_ns"]) or
                int(membership["initial_collection_quiet_ns"]) != 16640 or
                int(membership["initial_collection_full_deadline"]) != 0 or
                int(membership["initial_collection_starts"]) != 1 or
                int(membership["initial_collection_flushes"]) != 1 or
                int(membership["initial_collection_cancellations"]) != 0 or
                quiet_flushes + hard_flushes != 1 or
                int(membership["initial_collection_wait_ns"]) != first_delta or
                int(membership["initial_collection_max_wait_ns"]) != first_delta or
                not 0 < first_delta <= 83200 or not 0 <= last_delta <= 16640 or
                (quiet_flushes == 1 and
                 (hard_flushes != 0 or last_delta != 16640 or first_delta >= 83200)) or
                (hard_flushes == 1 and
                 (quiet_flushes != 0 or first_delta != 83200 or last_delta > 16640))):
            raise SummaryError("V11 high transition/collection barrier failed")

    # Low mode releases immediately; only post-transition high-mode releases
    # retain V10's geometric active-count rule.
    releases = sorted((row for row in sent.values()
                       if row["grant_phase"] == "release"),
                      key=lambda row: (int(row["time_ns"]), int(row["generation"]),
                                       int(row["flow_id"])))
    if count > 4:
        targets: List[int] = []
        for row in releases:
            target = int(row["membership_target_n"])
            if not targets or target != targets[-1]:
                targets.append(target)
        previous = count
        for target in targets:
            if target > previous // 2:
                raise SummaryError("V11 post-transition release is not geometric")
            previous = target

    final_rows = [row for row in sent.values()
                  if int(row["membership_target_n"]) == count and
                  int(row["flow_id"]) in flow_ids]
    final_by_flow: Dict[int, Mapping[str, object]] = {}
    for row in final_rows:
        flow_id = int(row["flow_id"])
        if flow_id not in final_by_flow or int(row["time_ns"]) >= int(
                final_by_flow[flow_id]["time_ns"]):
            final_by_flow[flow_id] = row
    if set(final_by_flow) != flow_ids:
        raise SummaryError("V11 final target-N phase does not cover every lifecycle flow")
    line_rates = {int(row["line_rate_bps"]) for row in final_by_flow.values()}
    if len(line_rates) != 1:
        raise SummaryError("V11 final target-N grants disagree on line rate")
    line_rate = next(iter(line_rates))
    expected = line_rate / count
    error = max(abs(int(row["grant_rate_bps"]) - expected)
                for row in final_by_flow.values())
    if error > error_limit:
        raise SummaryError(f"V11 final C/N error {error}bps exceeds {error_limit}bps")
    return {
        "grant_generations": sorted({int(row["generation"]) for row in sent.values()}),
        "grant_frames_sent": len(sent), "grant_frames_received": len(received),
        "grant_ack_frames_sent": len(ack_sent),
        "grant_ack_frames_received": len(ack_received),
        "total_control_frames_sent": len(sent) + len(ack_sent),
        "frame_bound": frame_bound, "pending_generations": 0,
        "retry_grants": 0, "stale_events": 0,
        "transaction_contract": transaction_contract,
        "phase_contract": phase_summary,
        "transition_union_closed": transition_transactions == (count > 4),
        "barriers_closed": True, "wire_reconciled": True,
        "line_rate_bps": line_rate, "final_c_over_n_bps": expected,
        "final_c_over_n_error_bps_max": error,
    }


def v12_prefix_audit(rows: Sequence[Mapping[str, object]],
                     prefix: Mapping[str, int], count: int, receiver: int,
                     flow_ids: set[int], registration_times: Mapping[int, int],
                     generation: Mapping[str, object]) -> Dict[str, object]:
    """Require V12's normal exact-prefix path and reject every fallback path."""
    terminal = (
        "terminal_timer", "terminal_waiting", "terminal_resolved",
        "terminal_timed_out", "terminal_fallback", "terminal_cursor",
        "terminal_cohort", "terminal_future_queue", "terminal_current_batch",
        "terminal_targets", "terminal_holds",
    )
    if (int(prefix["enabled"]) != 1 or
            any(int(prefix[field]) != 0 for field in terminal) or
            any(int(prefix[field]) != 0 for field in (
                "timeouts", "degraded", "fallback_batches",
                "fallback_max_batch", "fallback_closed_batches",
                "order_violations", "barrier_violations"))):
        raise SummaryError("V12 prefix/fallback/terminal counters are not normal-path clean")
    sent = [row for row in rows if row["event"] == "sent"]
    authoritative = [row for row in sent
                     if str(row["grant_phase"]) == "transition_activate"]
    non_activation = [row for row in rows
                      if not (row["event"] in ("sent", "ack_received") and
                              str(row["grant_phase"]) == "transition_activate")]
    if any(
            int(row["prefix_target_bytes"]) != 0 or
            int(row["prefix_observed_bytes"]) != 0 or
            str(row["drain_outcome"]) != "none" or
            int(row["activation_batch_index"]) != 0 or
            int(row["activation_batch_size"]) != 0 or
            int(row["activation_register_ns"]) != -1
            for row in non_activation):
        raise SummaryError("V12 prefix evidence leaked outside authoritative activation sends")
    if any(int(row["host_node"]) != receiver for row in sent):
        raise SummaryError("V12 grant trace is not receiver-authoritative on one receiver NIC")
    destination_ips = {int(row["data_destination_ip"]) for row in sent}
    if len(destination_ips) != 1:
        raise SummaryError("V12 trace spans multiple data destinations")
    if count <= 4:
        if (authoritative or any(int(prefix[field]) != 0 for field in (
                "starts", "ready", "waiters_required", "waiters_ready",
                "target_bytes", "received_bytes", "remaining_bytes",
                "wait_ns", "max_wait_ns", "start_ns", "deadline_ns",
                "ready_ns"))):
            raise SummaryError("V12 low-N run unexpectedly entered the prefix barrier")
        return {
            "normal_path": True, "prefix_started": False,
            "activation_flows": [], "activation_generation": None,
            "activation_batch_size": 0, "same_receiver_nic": True,
            "same_priority_group": True,
        }
    if (int(prefix["starts"]) != 1 or int(prefix["ready"]) != 1 or
            int(prefix["waiters_required"]) <= 0 or
            int(prefix["waiters_ready"]) != int(prefix["waiters_required"]) or
            int(prefix["remaining_bytes"]) != 0 or
            int(prefix["target_bytes"]) <= 0 or
            int(prefix["received_bytes"]) != int(prefix["target_bytes"]) or
            int(prefix["start_ns"]) <= 0 or
            int(prefix["deadline_ns"]) < int(prefix["start_ns"]) or
            not int(prefix["start_ns"]) <= int(prefix["ready_ns"]) <= int(
                prefix["deadline_ns"]) or
            int(prefix["wait_ns"]) !=
                int(prefix["ready_ns"]) - int(prefix["start_ns"]) or
            int(prefix["max_wait_ns"]) != int(prefix["wait_ns"])):
        raise SummaryError("V12 high-N exact-prefix stats do not prove one ready transition")
    actual = len(authoritative)
    if actual != int(prefix["waiters_required"]) or actual == 0:
        raise SummaryError("V12 activation cohort differs from the prefix waiter cohort")
    activation_flows = [int(row["flow_id"]) for row in authoritative]
    if (len(set(activation_flows)) != actual or
            not set(activation_flows).issubset(flow_ids) or
            activation_flows != sorted(
                activation_flows,
                key=lambda flow: (registration_times[flow], flow))):
        raise SummaryError("V12 transition activation order/cohort is invalid")
    generations = {int(row["generation"]) for row in authoritative}
    transactions = {int(row["transaction_id"]) for row in authoritative}
    targets = {int(row["membership_target_n"]) for row in authoritative}
    if (len(generations) != 1 or min(generations) <= 0 or
            len(transactions) != 1 or min(transactions) <= 0 or
            targets != {count} or
            any(int(row["ack_required"]) != 1 or
                int(row["prefix_target_bytes"]) <= 0 or
                int(row["prefix_observed_bytes"]) <
                    int(row["prefix_target_bytes"]) or
                str(row["drain_outcome"]) != "ready" or
                int(row["activation_batch_index"]) != 1 or
                int(row["activation_batch_size"]) != actual or
                int(row["activation_register_ns"]) !=
                    registration_times[int(row["flow_id"])]
                for row in authoritative)):
        raise SummaryError("V12 raw transition activation lacks exact-prefix closure")
    transition_batches = dict(generation["phase_contract"])[
        "transition_activate"]
    if (int(transition_batches["batches"]) != 1 or
            int(transition_batches["grants"]) != actual or
            int(transition_batches["acks"]) != actual):
        raise SummaryError("V12 ready cohort is not one required ACK-closed generation")
    return {
        "normal_path": True, "prefix_started": True,
        "prefix_start_ns": int(prefix["start_ns"]),
        "prefix_ready_ns": int(prefix["ready_ns"]),
        "prefix_deadline_ns": int(prefix["deadline_ns"]),
        "activation_flows": activation_flows,
        "activation_generation": next(iter(generations)),
        "activation_transaction": next(iter(transactions)),
        "activation_batch_index": 1, "activation_batch_size": actual,
        "same_receiver_nic": True, "same_priority_group": True,
    }


def _frozen_topology_base_rtts(topology_text: str,
                               flows: Sequence[Mapping[str, object]],
                               mtu_bytes: int) -> Dict[int, int]:
    """Recompute simulator base RTTs from the frozen uniform-link topology."""
    lines = topology_text.splitlines()
    if len(lines) < 2:
        raise SummaryError("V13 topology is incomplete")
    try:
        node_count, _switch_count, link_count = map(int, lines[0].split())
    except (TypeError, ValueError) as exc:
        raise SummaryError("V13 topology header is malformed") from exc
    if len(lines) != link_count + 2:
        raise SummaryError("V13 topology link count changed")
    graph: Dict[int, List[int]] = {node: [] for node in range(node_count)}
    for line in lines[2:]:
        parts = line.split()
        if len(parts) != 5 or not parts[2].endswith("Gbps") or not parts[3].endswith("ns"):
            raise SummaryError("V13 topology link schema changed")
        try:
            source, destination = map(int, parts[:2])
            rate_bps = int(parts[2][:-4]) * 1_000_000_000
            delay_ns = int(parts[3][:-2])
            error_rate = float(parts[4])
        except ValueError as exc:
            raise SummaryError("V13 topology link is malformed") from exc
        if (not 0 <= source < node_count or not 0 <= destination < node_count or
                rate_bps != 100_000_000_000 or delay_ns != 1000 or error_rate != 0.0):
            raise SummaryError("V13 frozen topology link parameters changed")
        graph[source].append(destination)
        graph[destination].append(source)

    def hops(source: int, destination: int) -> int:
        pending = deque([(source, 0)])
        visited = {source}
        while pending:
            node, distance = pending.popleft()
            if node == destination:
                return distance
            for neighbor in graph[node]:
                if neighbor not in visited:
                    visited.add(neighbor)
                    pending.append((neighbor, distance + 1))
        raise SummaryError(f"V13 topology disconnects {source}->{destination}")

    result: Dict[int, int] = {}
    for flow_id, flow in enumerate(flows):
        source = int(flow["src"])
        destination = int(flow["dst"])
        path_hops = hops(source, destination)
        # network-load-balance computes 2*one-way propagation plus one-way
        # payload serialization, with integer division independently per hop.
        serialization_per_hop = mtu_bytes * 8_000_000_000 // 100_000_000_000
        result[flow_id] = path_hops * (2 * 1000 + serialization_per_hop)
    return result


def v13_watchdog_audit(rows: Sequence[Mapping[str, object]],
                       lifecycle_rows: Sequence[Mapping[str, object]],
                       flows: Sequence[Mapping[str, object]],
                       watchdog: Mapping[str, int], count: int,
                       prefix: Mapping[str, int],
                       prefix_proof: Mapping[str, object],
                       generation: Mapping[str, object],
                       spec: Mapping[str, object],
                       topology_text: str) -> Dict[str, object]:
    """Independently reconstruct V13's one same-source watchdog budget."""
    if int(watchdog["enabled"]) != 1:
        raise SummaryError("V13 candidate watchdog is not enabled")
    low = count in tuple(map(int, spec["candidate_admission"]["low_n_counts"]))
    if low:
        if any(int(watchdog[field]) != 0
               for field in TRANSITION_WATCHDOG_FIELDS_V13
               if field != "enabled"):
            raise SummaryError("V13 low-N run unexpectedly recorded a watchdog budget")
        return {
            "independently_reconstructed": True,
            "budget_started": False,
            "records": 0,
        }

    gate = dict(spec["candidate_admission"])
    if (int(watchdog["records"]) != int(gate["watchdog_records_required"]) or
            int(watchdog["non_reconstructable"]) !=
                int(gate["watchdog_non_reconstructable_max"]) or
            int(watchdog["inconsistent"]) !=
                int(gate["watchdog_inconsistent_max"]) or
            int(watchdog["terminal_budget"]) !=
                int(gate["watchdog_terminal_budget_max"]) or
            prefix_proof.get("prefix_started") is not True):
        raise SummaryError("V13 watchdog record cannot be reconstructed as one budget")

    lifecycle_by_flow = {int(row["flow_id"]): row for row in lifecycle_rows}
    if (set(lifecycle_by_flow) != set(range(count)) or len(flows) != count or
            any(int(lifecycle_by_flow[index]["size_bytes"]) != int(flows[index]["size"])
                for index in range(count))):
        raise SummaryError("V13 flow IDs/sizes differ between snapshot and lifecycle evidence")
    activation = [row for row in rows
                  if row["event"] == "sent" and
                  str(row["grant_phase"]) == "transition_activate"]
    activation_flows = set(map(int, prefix_proof["activation_flows"]))
    if ({int(row["flow_id"]) for row in activation} != activation_flows or
            len(activation) != len(activation_flows)):
        raise SummaryError("V13 raw prefix targets do not identify one waiter cohort")
    transactions = [row for row in generation["transaction_contract"]
                    if row.get("kind") == "transition"]
    if len(transactions) != 1:
        raise SummaryError("V13 watchdog lacks one transition transaction")
    prepare_last_ack_ns = int(transactions[0]["prepare_last_ack_ns"])
    start_ns = int(prefix_proof["prefix_start_ns"])
    ready_ns = int(prefix_proof["prefix_ready_ns"])
    activation_first_send_ns = min(int(row["time_ns"]) for row in activation)
    if not (0 <= prepare_last_ack_ns <= start_ns <= ready_ns <=
            activation_first_send_ns):
        raise SummaryError(
            "V13 watchdog did not start after prepare ACK closure and before activation")

    protocol = dict(spec["transition_prefix_watchdog_protocol"])
    mtu = int(protocol["mtu_bytes"])
    header = int(protocol["data_header_bytes"])
    raw_targets: Dict[int, int] = {}
    rounded_payload = packet_count = wire_bytes = 0
    for row in activation:
        flow_id = int(row["flow_id"])
        target = int(row["prefix_target_bytes"])
        size = int(lifecycle_by_flow[flow_id]["size_bytes"])
        if target <= 0 or target > size:
            raise SummaryError("V13 raw prefix target is outside its flow size")
        packets = target // mtu + (1 if target % mtu else 0)
        payload = min(size, packets * mtu)
        raw_targets[flow_id] = target
        packet_count += packets
        rounded_payload += payload
        wire_bytes += payload + packets * header
    if sum(raw_targets.values()) != int(prefix["target_bytes"]):
        raise SummaryError("V13 raw waiter targets differ from prefix aggregate")

    incumbents = [row for row in rows
                  if row["event"] == "sent" and
                  str(row["grant_phase"]) == "transition_prepare"]
    incumbent_flows = {int(row["flow_id"]) for row in incumbents}
    if (len(incumbents) != len(incumbent_flows) or not incumbent_flows or
            incumbent_flows & activation_flows or
            incumbent_flows | activation_flows != set(range(count))):
        raise SummaryError("V13 incumbent/watchdog cohorts do not partition the lifecycle")
    occupancy = sum(int(row["grant_rate_bps"]) for row in incumbents)
    capacity = int(generation["line_rate_bps"])
    residual = capacity - occupancy
    if occupancy <= 0 or residual <= 0:
        raise SummaryError("V13 acknowledged incumbent occupancy leaves no residual service")
    serialization_ns = (wire_bytes * 8_000_000_000 + residual - 1) // residual
    rtts = _frozen_topology_base_rtts(topology_text, flows, mtu)
    if set(rtts) != incumbent_flows | activation_flows:
        raise SummaryError("V13 RTT evidence does not cover the transition cohort")
    max_rtt_ns = max(rtts.values())
    delay_ns = max_rtt_ns + serialization_ns
    deadline_ns = start_ns + delay_ns
    expected = {
        "rounded_payload_budget_bytes": rounded_payload,
        "packet_count": packet_count,
        "header_per_packet": header,
        "wire_bytes": wire_bytes,
        "incumbent_count": len(incumbents),
        "occupancy_bps": occupancy,
        "capacity_bps": capacity,
        "residual_bps": residual,
        "serialization_ns": serialization_ns,
        "max_rtt_ns": max_rtt_ns,
        "delay_ns": delay_ns,
        "start_ns": start_ns,
        "deadline_ns": deadline_ns,
    }
    if any(int(watchdog[field]) != value for field, value in expected.items()):
        raise SummaryError("V13 watchdog fields differ from independent reconstruction")
    if int(prefix["deadline_ns"]) != deadline_ns:
        raise SummaryError("V13 watchdog deadline differs from the live prefix deadline")
    return {
        "independently_reconstructed": True,
        "budget_started": True,
        "records": int(watchdog["records"]),
        "prepare_last_ack_ns": prepare_last_ack_ns,
        "activation_first_send_ns": activation_first_send_ns,
        "raw_target_bytes_by_flow": {
            str(flow): raw_targets[flow] for flow in sorted(raw_targets)},
        "base_rtt_ns_by_flow": {
            str(flow): rtts[flow] for flow in sorted(rtts)},
        **expected,
    }


def validate_v10_initial_collection(membership: Mapping[str, object],
                                    generation: Mapping[str, object], count: int,
                                    context: object) -> None:
    """Reconcile V10's sliding quiet timer, hard deadline, and first vector."""
    quiet_ns = 16640
    hard_ns = 83200
    quiet_flushes = int(membership.get("initial_collection_quiet_flushes", -1))
    hard_flushes = int(membership.get("initial_collection_hard_flushes", -1))
    first_delta = int(generation["first_flush_minus_first_registration_ns"])
    last_delta = int(generation["first_flush_minus_last_registration_ns"])
    common_failed = (
        int(membership.get("initial_collection_quiet_ns", -1)) != quiet_ns or
        int(membership.get("initial_collection_full_deadline", -1)) != 0 or
        int(membership.get("initial_collection_starts", -1)) != 1 or
        int(membership.get("initial_collection_flushes", -1)) != 1 or
        int(membership.get("initial_collection_deferred_changes", -1)) != count - 1 or
        int(membership.get("initial_collection_reschedules", -1)) != count - 1 or
        int(membership.get("initial_collection_cancellations", -1)) != 0 or
        quiet_flushes + hard_flushes != 1 or
        int(membership.get("max_batch", -1)) != count or
        int(membership.get("initial_collection_wait_ns", -1)) != first_delta or
        int(membership.get("initial_collection_max_wait_ns", -1)) != first_delta or
        int(generation["initial_generation_flows"]) != count or
        int(generation["initial_generation_send_timestamp_count"]) != 1 or
        int(generation["post_initial_registration_events"]) != 0 or
        int(generation["last_registration_ns"]) > int(generation["first_flush_ns"]) or
        not 0 < first_delta <= hard_ns or not 0 <= last_delta <= quiet_ns)
    quiet_failed = quiet_flushes == 1 and (
        hard_flushes != 0 or last_delta != quiet_ns or first_delta >= hard_ns)
    hard_failed = hard_flushes == 1 and (
        quiet_flushes != 0 or first_delta != hard_ns or last_delta > quiet_ns)
    if common_failed or quiet_failed or hard_failed:
        raise SummaryError(f"V10 sliding initial-collection contract failed: {context}")


def _validate_exact_simulator_repo(repo: Path, simulator_sha: str,
                                   identity: object) -> Path:
    """Require a relocated simulator worktree to be the exact clean frozen tree."""
    if (not repo.is_absolute() or str(repo.resolve()) != str(repo) or
            not repo.is_dir() or not (repo / "run.py").is_file()):
        raise SummaryError(f"relocated simulator repo is invalid: {identity}")

    def git_output(*arguments: str) -> str:
        result = subprocess.run(
            ["git", *arguments], cwd=repo, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if result.returncode:
            raise SummaryError(f"relocated simulator repo is not a worktree: {identity}")
        return result.stdout.strip()

    top = Path(git_output("rev-parse", "--show-toplevel")).resolve()
    if top != repo or git_output("rev-parse", "HEAD") != simulator_sha:
        raise SummaryError(
            f"relocated simulator repo does not match frozen SHA: {identity}")
    if git_output("status", "--porcelain", "--untracked-files=normal"):
        raise SummaryError(f"relocated simulator repo is not clean: {identity}")
    return repo


def _validate_frozen_cli(manifest: Mapping[str, object],
                         spec: Mapping[str, object],
                         preflight: Mapping[str, object],
                         traffic: Mapping[str, object], arm: str,
                         simulator_sha: str, identity: object) -> None:
    """Permit only run.py relocation to a clean exact-SHA V11 worktree."""
    preflight_repo = Path(str(preflight["repo"])).resolve()
    expected = run_command(preflight_repo, spec, traffic, arm)
    observed = manifest.get("cli")
    manifest_repo_raw = manifest.get("repo")
    if not isinstance(observed, list) or not all(
            isinstance(argument, str) for argument in observed):
        raise SummaryError(f"frozen CLI changed: {identity}")
    if manifest_repo_raw == str(preflight_repo):
        if observed != expected:
            raise SummaryError(f"frozen CLI changed: {identity}")
        return
    if int(spec.get("schema_version", 0)) not in (11, 12, 13) or not isinstance(
            manifest_repo_raw, str):
        raise SummaryError(f"frozen CLI changed: {identity}")
    manifest_repo = _validate_exact_simulator_repo(
        Path(manifest_repo_raw), simulator_sha, identity)
    relocated = run_command(manifest_repo, spec, traffic, arm)
    launcher_index = 3
    if (len(observed) != len(expected) or len(relocated) != len(expected) or
            Path(expected[launcher_index]) != preflight_repo / "run.py" or
            Path(relocated[launcher_index]) != manifest_repo / "run.py" or
            observed != relocated or
            any(observed[index] != expected[index]
                for index in range(len(expected)) if index != launcher_index)):
        raise SummaryError(f"frozen CLI changed beyond run.py relocation: {identity}")


def validate_manifest_identity(manifest: Mapping[str, object], spec: Mapping[str, object],
                               spec_path: Path, campaign_dir: Path,
                               preflight: Mapping[str, object],
                               scope: str = "holdout") -> Tuple[int, int, str, Mapping[str, object]]:
    seed = int(manifest["seed"])
    count = int(manifest["active_flows"])
    arm = str(manifest["controller"])
    identity = (seed, count, arm)
    plan = (matrix(spec) if scope == "holdout" else
            replay_matrix(spec) if scope == "replay" else pilot_matrix(spec))
    if (scope not in ("holdout", "pilot", "replay") or
            manifest.get("scope") != scope or
            identity not in plan or int(manifest.get("ordinal", -1)) != plan.index(identity) + 1):
        raise SummaryError(f"run identity/ordinal mismatch: {identity}")
    if (manifest.get("schema_version") != spec.get("schema_version") or
            manifest.get("status") != "completed"):
        raise SummaryError(f"run schema/status mismatch: {identity}")
    if manifest.get("stop_reason") != "completed" or manifest.get("git_dirty"):
        raise SummaryError(f"run did not terminate cleanly: {identity}")
    simulator_sha = str(preflight["simulator_git_sha"])
    if manifest.get("simulator_git_sha") != simulator_sha:
        raise SummaryError(f"simulator SHA mismatch: {identity}")
    if (manifest.get("spec_sha256") != sha256_file(spec_path) or
            manifest.get("preflight_sha256") != sha256_file(campaign_dir / "preflight.json")):
        raise SummaryError(f"spec/preflight hash mismatch: {identity}")
    traffic = [row for row in preflight["traffic"]
               if row.get("scope") == scope and int(row["seed"]) == seed and
               int(row["active_flows"]) == count]
    if len(traffic) != 1:
        raise SummaryError(f"missing unique traffic identity: {identity}")
    row = traffic[0]
    if (manifest.get("flow_path") != row["path"] or
            manifest.get("flow_sha256") != row["sha256"] or
            int(manifest.get("flow_count", -1)) != count):
        raise SummaryError(f"run/preflight traffic mismatch: {identity}")
    flow_path = Path(str(row["path"]))
    if not flow_path.is_file() or sha256_file(flow_path) != row["sha256"]:
        raise SummaryError(f"frozen traffic changed: {identity}")
    _validate_frozen_cli(
        manifest, spec, preflight, row, arm, simulator_sha, identity)
    return seed, count, arm, row


def validate_mechanism_run(manifest: Mapping[str, object], spec: Mapping[str, object],
                           spec_path: Path, campaign_dir: Path,
                           preflight: Mapping[str, object],
                           scope: str = "holdout") -> Tuple[Dict[str, object], Path]:
    seed, count, arm, traffic = validate_manifest_identity(
        manifest, spec, spec_path, campaign_dir, preflight, scope)
    identity = (seed, count, arm)
    limits = dict(spec["resource_limits"])
    if manifest.get("resource_limits") != limits:
        raise SummaryError(f"resource limits changed: {identity}")
    if (float(manifest.get("elapsed_seconds", math.inf)) > limits["wall_time_seconds_per_run"] or
            int(manifest.get("max_rss_kib", limits["rss_kib_per_run"] + 1)) > limits["rss_kib_per_run"]):
        raise SummaryError(f"wall/RSS cap exceeded: {identity}")
    output_dir = Path(str(manifest.get("output_dir", "")))
    output_id = str(manifest.get("output_id", ""))
    launcher = Path(str(manifest.get("launcher_log", "")))
    if not output_dir.is_dir() or output_dir.name != output_id or not launcher.is_file():
        raise SummaryError(f"missing output/log: {identity}")
    output_bytes = directory_size(output_dir)
    if (output_bytes != int(manifest.get("output_bytes", -1)) or
            output_bytes + launcher.stat().st_size > limits["artifact_bytes_per_run"]):
        raise SummaryError(f"artifact size drift/cap violation: {identity}")
    config = parse_config(output_dir / "config.txt")
    validate_config(config, spec, seed, arm)
    snapshot = one_artifact(output_dir, "_input_flow.txt")
    flows, snapshot_summary = parse_snapshot(snapshot, 16, int(limits["max_flows"]))
    if snapshot_summary["sha256"] != traffic["sha256"] or len(flows) != count:
        raise SummaryError(f"private traffic snapshot mismatch: {identity}")
    if int(spec["schema_version"]) in (12, 13) and (
            any(int(flow["dst"]) != int(spec["candidate_pilot"]["receiver"])
                for flow in flows) or
            any(int(flow["pg"]) != int(spec["candidate_pilot"]["priority_group"])
                for flow in flows)):
        raise SummaryError(f"V12 traffic spans receiver NIC/priority provenance: {identity}")
    stats_path = one_artifact(output_dir, "_out_guard_stats.txt")
    stats = parse_guard_stats(stats_path)
    schema_version = int(spec["schema_version"])
    membership = parse_coalescing_stats(stats_path, schema_version)
    small = parse_small_set_stats(stats_path) if schema_version in (11, 12, 13) else None
    prefix = (parse_transition_prefix_stats(stats_path)
              if schema_version in (12, 13) and arm != CONTROL_ARM else None)
    watchdog = (parse_transition_watchdog_stats(stats_path)
                if schema_version == 13 else None)
    if schema_version == 13 and ((arm == CONTROL_ARM) != (watchdog is None)):
        raise SummaryError(f"V13 watchdog row presence differs from its arm: {identity}")
    recovery = sum(int(stats[field]) for field in RECOVERY_FIELDS)
    if int(stats["switch_drops_total"]) != 0 or recovery != 0 or int(stats["timeout_recoveries"]) != 0:
        raise SummaryError(f"drop/recovery/timeout activity is nonzero: {identity}")
    if (int(stats["registrations"]) != count or int(stats["selected_registrations"]) != count or
            int(stats["completion_releases"]) != count or int(stats["proactive_releases"]) != 0 or
            int(stats["max_active_flows"]) != count or
            manifest.get("completed_flow_count") is not None or
            manifest.get("completion_validation") != "deferred_to_lifecycle_and_stats"):
        raise SummaryError(f"registration/completion counters differ from N={count}: {identity}")
    expected_window = int(spec["candidate_pilot"]["membership_quiet_ns"][arm])
    expected_retry = float(spec["candidate_pilot"]["grant_reliability_service_rounds"][arm])
    if (int(membership["window_ns"]) != expected_window or
            int(membership["max_windows"]) != int(spec["candidate_pilot"]["membership_max_windows"]) or
            float(membership["reliability_rtts"]) != expected_retry):
        raise SummaryError(f"membership stats/config mismatch: {identity}")
    if (int(membership["refresh_events"]) != 0 or int(membership["refresh_grants"]) != 0 or
            int(membership["retry_grants"]) != 0 or int(membership["ack_stale"]) != 0 or
            int(membership["stale_grants"]) != 0 or int(membership["pending"]) != 0):
        raise SummaryError(f"retry/stale/pending counter is nonzero: {identity}")
    if schema_version >= 7 and (
            int(membership["generation_zero_rejected"]) != 0 or
            int(membership["generation_mismatch_rejected"]) != 0):
        raise SummaryError(f"zero/mismatched generation rejection is nonzero: {identity}")
    if arm == CONTROL_ARM:
        if (int(membership["first_grant_gated"]) != 0 or
                int(membership["first_grant_released"]) != 0):
            raise SummaryError(f"control first-grant gate counters are nonzero: {identity}")
        if schema_version in (9, 10, 11, 12, 13) and any(int(membership[field]) != 0 for field in (
                "initial_collection_starts", "initial_collection_flushes",
                "initial_collection_deferred_changes",
                "initial_collection_cancellations", "initial_collection_wait_ns",
                "initial_collection_max_wait_ns")):
            raise SummaryError(f"control initial-collection counters are nonzero: {identity}")
        if schema_version >= 10 and (
                int(membership["initial_collection_quiet_ns"]) != 0 or
                int(membership["initial_collection_full_deadline"]) != 1 or
                int(membership["initial_collection_reschedules"]) != 0 or
                int(membership["initial_collection_quiet_flushes"]) != 0 or
                int(membership["initial_collection_hard_flushes"]) != 0):
            raise SummaryError(f"control V10 initial mode/reason counters changed: {identity}")
        if schema_version in (11, 12, 13):
            assert small is not None
            zero_small = (
                "transactions", "accepted_fast_prepare_batches",
                "accepted_fast_prepare_grants", "accepted_fast_prepare_acks",
                "accepted_fast_activate_batches", "accepted_fast_activate_grants",
                "accepted_fast_activate_acks", "accepted_transition_prepare_batches",
                "accepted_transition_prepare_grants", "accepted_transition_prepare_acks",
                "accepted_transition_activate_batches",
                "accepted_transition_activate_grants",
                "accepted_transition_activate_acks", "accepted_release_batches",
                "accepted_release_grants", "high_transitions",
                "high_collection_flushes", "post_transition_fast_grants",
                "barrier_violations", "early_unlocks", "unattributed_grant_frames",
                "unattributed_ack_frames", "wire_grant_frames", "wire_ack_frames",
                "control_frames", "terminal_phase", "terminal_pending_acks",
                "terminal_pending_membership", "terminal_waiters",
                "terminal_transaction_waiters", "terminal_ready_waiters",
                "terminal_collection_ready", "terminal_initial_flushed",
                "terminal_transition_committed", "terminal_revision",
                "terminal_consumed_revision",
            )
            if (int(small["enabled"]) != 0 or int(small["limit"]) != 0 or
                    int(small["wire_reconciled"]) != 1 or
                    any(int(small[field]) != 0 for field in zero_small)):
                raise SummaryError(
                    f"control V{schema_version} small-set counters are nonzero: {identity}")
    else:
        if (int(membership["first_grant_gated"]) != count or
                int(membership["first_grant_released"]) != count):
            raise SummaryError(f"first-grant gate counters differ from N={count}: {identity}")
        # V11's partial prepare/activate generations intentionally bypass the
        # legacy coalescing batch counter. Its nonempty-work proof comes from
        # guard_small_set_fastpath transactions and raw phase closure below.
        if schema_version <= 10 and int(membership["batches"]) <= 0:
            raise SummaryError(f"candidate has no membership batch: {identity}")
        if schema_version == 9:
            wait_ns = int(spec["initial_collection_protocol"]["derived_wait_ns"])
            if (int(membership["initial_collection_starts"]) != 1 or
                    int(membership["initial_collection_flushes"]) != 1 or
                    int(membership["initial_collection_cancellations"]) != 0 or
                    int(membership["initial_collection_wait_ns"]) != wait_ns or
                    int(membership["initial_collection_max_wait_ns"]) != wait_ns or
                    int(membership["max_batch"]) != count):
                raise SummaryError(
                    f"V9 fixed initial-collection counters differ from N={count}: {identity}")
            if "initial_collection_full_deadline" in membership and (
                    int(membership["initial_collection_quiet_ns"]) != 12480 or
                    int(membership["initial_collection_full_deadline"]) != 1 or
                    int(membership["initial_collection_reschedules"]) != 0 or
                    int(membership["initial_collection_quiet_flushes"]) != 0 or
                    int(membership["initial_collection_hard_flushes"]) != 1):
                raise SummaryError(
                    f"V9 compatibility mode is not fixed-full-deadline: {identity}")
        elif schema_version == 10:
            if int(membership["max_batch"]) != count:
                raise SummaryError(
                    f"V10 initial collection max batch differs from N={count}: {identity}")
        elif schema_version in (11, 12, 13):
            if small is None or int(small["enabled"]) != 1:
                raise SummaryError(
                    f"V{schema_version} candidate fastpath is not enabled: {identity}")
        elif count == 15 and int(membership["max_batch"]) < int(
                spec["candidate_admission"]["coalesced_max_batch_min"]):
            raise SummaryError(f"N=15 candidate batch is below the frozen minimum: {identity}")
    lifecycle_rows = parse_lifecycle(
        one_artifact(output_dir, "_out_guard_lifecycle.csv"),
        int(limits["lifecycle_trace_max_lines"]))
    lifecycle, flow_ids = lifecycle_audit(
        lifecycle_rows, count, int(spec["candidate_pilot"]["receiver"]))
    trace, trace_footer = parse_bounded_trace(
        one_artifact(output_dir, "_out_guard_grants.csv"),
        int(limits["grant_trace_max_lines"]), schema_version,
        v11_enabled=schema_version in (11, 12, 13) and arm != CONTROL_ARM)
    if schema_version in (11, 12, 13) and arm != CONTROL_ARM:
        assert small is not None
        generation = v11_phase_audit(
            trace, stats, membership, small, count,
            int(spec["candidate_pilot"]["receiver"]), flow_ids,
            {int(row["flow_id"]): int(row["register_ns"])
             for row in lifecycle_rows},
            int(spec["candidate_admission"]["final_c_over_n_error_bps_max"]),
            int(spec["candidate_admission"]["frame_bounds"][str(count)]))
        if schema_version in (12, 13):
            assert prefix is not None
            generation["transition_prefix"] = v12_prefix_audit(
                trace, prefix, count, int(spec["candidate_pilot"]["receiver"]),
                flow_ids,
                {int(row["flow_id"]): int(row["register_ns"])
                 for row in lifecycle_rows}, generation)
        if schema_version == 13:
            assert prefix is not None and watchdog is not None
            topology_relative = (
                f"config/{spec['candidate_pilot']['topology']}.txt")
            topology_result = subprocess.run(
                ["git", "show",
                 f"{preflight['simulator_git_sha']}:{topology_relative}"],
                cwd=Path(str(preflight["repo"])), text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if topology_result.returncode:
                raise SummaryError("V13 frozen topology is unavailable at simulator SHA")
            generation["transition_prefix_watchdog"] = v13_watchdog_audit(
                trace, lifecycle_rows, flows, watchdog, count, prefix,
                generation["transition_prefix"], generation, spec,
                topology_result.stdout)
    else:
        generation = generation_audit(
            trace, stats, membership, count, arm,
            int(spec["candidate_pilot"]["receiver"]), flow_ids,
            int(spec["candidate_admission"].get(
                "final_c_over_n_error_bps_max", 1000000)), schema_version,
            {int(row["flow_id"]): int(row["register_ns"])
             for row in lifecycle_rows})
    if schema_version in (9, 10) and arm != CONTROL_ARM:
        details = list(generation["generation_contract"])
        if (int(generation["initial_generation_flows"]) != count or
                int(generation["initial_generation_send_timestamp_count"]) != 1 or
                int(generation["post_initial_registration_events"]) != 0 or
                (schema_version == 9 and
                 int(generation["first_flush_minus_first_registration_ns"]) !=
                    int(spec["initial_collection_protocol"]["derived_wait_ns"])) or
                int(generation["last_registration_ns"]) >
                    int(generation["first_flush_ns"]) or
                int(generation["ack_required_batches"]) != 1 or
                int(generation["ack_required_grants"]) != count or
                not details or not bool(details[0]["ack_required"]) or
                int(details[0]["active_flows"]) != count or
                any(bool(row["ack_required"]) or int(row["joins"]) != 0
                    for row in details[1:])):
            raise SummaryError(
                f"V{schema_version} initial/later generation contract failed: {identity}")
        if schema_version == 10:
            validate_v10_initial_collection(membership, generation, count, identity)
    pfc = pfc_audit(stats, one_artifact(output_dir, "_out_pfc.txt"))
    if arm != CONTROL_ARM:
        pfc_limit = int(
            spec["candidate_admission"]["pfc_events_max"] if scope == "pilot" else
            spec["scalability_holdout"]["coalesced_pfc_events_max"])
        if pfc["pause_events"] > pfc_limit or pfc["resume_events"] > pfc_limit:
            raise SummaryError(f"candidate PFC activity exceeds {pfc_limit}: {identity}")
    return {
        "ordinal": (matrix(spec) if scope == "holdout" else
                    replay_matrix(spec) if scope == "replay" else
                    pilot_matrix(spec)).index(identity) + 1,
        "scope": scope,
        "seed": seed, "active_flows": count, "controller": arm,
        "simulator_git_sha": preflight["simulator_git_sha"],
        "flow_sha256": traffic["sha256"], "output_id": output_id,
        "output_dir": str(output_dir.resolve()), "completed_flows": count,
        "switch_drops": 0, "recovery_events": recovery, "timeout_recoveries": 0,
        "grant_trace": trace_footer, "membership": membership,
        "generation_ack": generation, "lifecycle": lifecycle, "pfc": pfc,
        "safe_activation": small,
        "transition_prefix": prefix,
        "transition_prefix_watchdog": watchdog,
    }, output_dir


def target_queue(path: Path, receiver: int) -> Dict[str, object]:
    matches = [row for row in parse_port_queue_summaries(path)
               if int(row["neighbor_id"]) == receiver]
    if len(matches) != 1:
        raise SummaryError(f"expected one switch egress queue to receiver {receiver}, found {len(matches)}")
    row = matches[0]
    samples = int(row["samples"])
    mean = float(row["average_bytes"])
    p99 = float(row["p99_bytes"])
    if samples <= 0 or not all(math.isfinite(value) and value >= 0 for value in (mean, p99)):
        raise SummaryError("receiver queue summary is empty or invalid")
    return {
        "node_id": int(row["node_id"]), "if_index": int(row["if_index"]),
        "neighbor_id": receiver, "samples": samples,
        "time_sampled_mean_bytes": mean, "time_sampled_p99_bytes": p99,
    }


def performance_metrics(output_dir: Path, flow_path: Path, count: int,
                        receiver: int, max_flows: int) -> Dict[str, object]:
    flows, _snapshot = parse_snapshot(flow_path, 16, max_flows)
    completions = parse_fct(one_artifact(output_dir, "_out_fct.txt"))
    validate_completions(flows, completions, 16)
    if len(completions) != count:
        raise SummaryError(f"FCT completion count {len(completions)} != N={count}")
    fcts = [float(row["fct_us"]) for row in completions]
    rates = [int(row["size"]) * 8 / int(row["duration_ns"]) for row in completions]
    span_ns = max(int(row["finish_ns"]) for row in completions) - min(
        int(row["start_ns"]) for row in completions)
    sum_squares = sum(value * value for value in rates)
    if not fcts or span_ns <= 0 or sum_squares <= 0:
        raise SummaryError("FCT/completion/fairness inputs are degenerate")
    queue = target_queue(one_artifact(output_dir, "_out_queue_stats.txt"), receiver)
    return {
        "mean_fct_us": statistics.fmean(fcts),
        "completion_span_us": span_ns / 1000.0,
        "flow_goodput_jain": sum(rates) ** 2 / (len(rates) * sum_squares),
        "receiver_queue_time_sampled_mean_bytes": queue["time_sampled_mean_bytes"],
        "receiver_queue_time_sampled_p99_bytes": queue["time_sampled_p99_bytes"],
        "receiver_queue_samples": queue["samples"],
        "receiver_queue_port": {
            "node_id": queue["node_id"], "if_index": queue["if_index"],
            "neighbor_id": queue["neighbor_id"],
        },
    }


def _validate_preflight(spec_path: Path, campaign_dir: Path,
                        spec: Mapping[str, object], formal: bool) -> Mapping[str, object]:
    preflight = read_json(campaign_dir / "preflight.json")
    if (preflight.get("schema_version") != spec.get("schema_version") or
            preflight.get("spec_execution_armed") is not True or
            preflight.get("spec_sha256") != sha256_file(spec_path) or
            preflight.get("run_count") != len(matrix(spec)) or
            preflight.get("pilot_run_count") != len(pilot_matrix(spec))):
        raise SummaryError("preflight/spec identity is invalid")
    if int(spec["schema_version"]) in (9, 10, 11, 12, 13) and (
            preflight.get("replay_run_count") != len(replay_matrix(spec)) or
            len(preflight.get("replay_run_order", [])) != len(replay_matrix(spec))):
        raise SummaryError(
            f"V{spec['schema_version']} preflight replay identity is invalid")
    if formal and preflight.get("formal_execution_armed") is not True:
        raise SummaryError("formal analysis is blocked until pilot admission is sealed")
    if not formal and (preflight.get("formal_execution_armed") or
                       preflight.get("pilot_admission") is not None):
        raise SummaryError("pilot analysis must precede formal arming")
    protocol_key = ("safe_activation_protocol" if int(spec["schema_version"]) >= 11
                    else "generation_ack_protocol")
    minimum = str(spec[protocol_key]["minimum_simulator_commit"])
    repo = Path(str(preflight["repo"]))
    if subprocess.run(
            ["git", "merge-base", "--is-ancestor", minimum,
             str(preflight["simulator_git_sha"])], cwd=repo).returncode:
        raise SummaryError("preflight simulator SHA predates the generation-ACK minimum")
    return preflight


def _validate_replay_preflight(spec_path: Path, campaign_dir: Path,
                               spec: Mapping[str, object]) -> Mapping[str, object]:
    preflight = _validate_preflight(spec_path, campaign_dir, spec, formal=False)
    if int(spec["schema_version"]) not in (9, 10, 11, 12, 13):
        raise SummaryError("known-failure replay analysis exists only for schema V9+")
    if (preflight.get("pilot_execution_armed") is not False or
            preflight.get("known_replay_admission") is not None or
            preflight.get("pilot_admission") is not None or
            preflight.get("formal_execution_armed") is not False):
        raise SummaryError("replay analysis must precede every sealed later phase")
    return preflight


def _validate_candidate_gate(candidate: Mapping[str, object],
                             spec: Mapping[str, object]) -> None:
    gate = dict(spec["candidate_admission"])
    generation = dict(candidate["generation_ack"])
    membership = dict(candidate["membership"])
    if int(spec["schema_version"]) in (11, 12, 13):
        small = dict(candidate["safe_activation"])
        count = int(candidate["active_flows"])
        terminal = tuple(map(str, gate["terminal_zero_fields"]))
        if (int(candidate["completed_flows"]) != count or
                int(candidate["switch_drops"]) > int(gate["switch_drops_max"]) or
                int(candidate["recovery_events"]) > int(gate["recovery_events_max"]) or
                int(candidate["timeout_recoveries"]) > int(gate["timeout_recoveries_max"]) or
                int(candidate["grant_trace"]["truncated"]) >
                    int(gate["grant_trace_truncated_max"]) or
                int(candidate["pfc"]["pause_events"]) > int(gate["pfc_events_max"]) or
                int(candidate["pfc"]["resume_events"]) > int(gate["pfc_events_max"]) or
                int(generation["total_control_frames_sent"]) >
                    int(gate["frame_bounds"][str(count)]) or
                int(generation["pending_generations"]) >
                    int(gate["pending_grant_acks_max"]) or
                int(generation["retry_grants"]) >
                    int(gate["normal_path_retry_grants_max"]) or
                int(generation["stale_events"]) != 0 or
                int(membership["generation_zero_rejected"]) >
                    int(gate["generation_zero_rejected_max"]) or
                int(membership["generation_mismatch_rejected"]) >
                    int(gate["generation_mismatch_rejected_max"]) or
                int(membership["first_grant_gated"]) != count or
                int(membership["first_grant_released"]) != count or
                int(small["enabled"]) != int(gate["small_set_fastpath_enabled"]) or
                int(small["limit"]) != int(gate["small_set_fastpath_limit"]) or
                int(small["barrier_violations"]) > int(gate["barrier_violations_max"]) or
                int(small["early_unlocks"]) > int(gate["early_unlocks_max"]) or
                int(small["post_transition_fast_grants"]) >
                    int(gate["post_transition_fast_grants_max"]) or
                int(small["unattributed_grant_frames"]) >
                    int(gate["unattributed_grant_frames_max"]) or
                int(small["unattributed_ack_frames"]) >
                    int(gate["unattributed_ack_frames_max"]) or
                int(small["wire_reconciled"]) != int(gate["wire_reconciled"]) or
                any(int(small[field]) != 0 for field in terminal) or
                generation.get("barriers_closed") is not True or
                generation.get("transition_union_closed") is not True or
                float(generation["final_c_over_n_error_bps_max"]) >
                    float(gate["final_c_over_n_error_bps_max"])):
            raise SummaryError(
                f"V{spec['schema_version']} candidate failed safe two-stage admission")
        if int(spec["schema_version"]) in (12, 13):
            prefix = dict(candidate["transition_prefix"])
            proof = dict(generation["transition_prefix"])
            low = count in tuple(map(int, gate["low_n_counts"]))
            terminal_prefix = tuple(map(str, gate["terminal_prefix_zero_fields"]))
            if (int(prefix["enabled"]) != int(gate["prefix_barrier_enabled"]) or
                    int(prefix["starts"]) != int(
                        gate["low_n_prefix_starts"] if low else
                        gate["high_n_prefix_starts"]) or
                    int(prefix["ready"]) != int(
                        gate["low_n_prefix_ready"] if low else
                        gate["high_n_prefix_ready"]) or
                    int(prefix["timeouts"]) > int(gate["prefix_timeouts_max"]) or
                    int(prefix["degraded"]) > int(gate["prefix_degraded_max"]) or
                    int(prefix["remaining_bytes"]) >
                        int(gate["prefix_remaining_bytes_max"]) or
                    int(prefix["fallback_batches"]) >
                        int(gate["fallback_batches_max"]) or
                    int(prefix["fallback_closed_batches"]) >
                        int(gate["fallback_closed_batches_max"]) or
                    int(prefix["order_violations"]) >
                        int(gate["fallback_order_violations_max"]) or
                    int(prefix["barrier_violations"]) >
                        int(gate["prefix_barrier_violations_max"]) or
                    any(int(prefix[field]) != 0 for field in terminal_prefix) or
                    (not low and int(prefix["waiters_ready"]) !=
                     int(prefix["waiters_required"])) or
                    proof.get("normal_path") is not True or
                    (low and proof.get("prefix_started") is not False) or
                    (not low and (
                        proof.get("prefix_started") is not True or
                        int(proof["activation_batch_index"]) != int(
                            gate["normal_transition_activation_batch_index"]) or
                        int(proof["activation_batch_size"]) !=
                            int(prefix["waiters_required"])))):
                raise SummaryError(
                    f"V{spec['schema_version']} candidate failed exact-prefix admission")
        if int(spec["schema_version"]) == 13:
            watchdog = dict(candidate["transition_prefix_watchdog"])
            watchdog_proof = dict(generation["transition_prefix_watchdog"])
            low = count in tuple(map(int, gate["low_n_counts"]))
            expected_records = int(
                gate["low_n_watchdog_records"] if low else
                gate["high_n_watchdog_records"])
            if (int(watchdog["enabled"]) !=
                    int(gate["prefix_wire_watchdog_enabled"]) or
                    int(watchdog["records"]) != expected_records or
                    int(watchdog["non_reconstructable"]) !=
                        int(gate["watchdog_non_reconstructable_max"]) or
                    int(watchdog["inconsistent"]) !=
                        int(gate["watchdog_inconsistent_max"]) or
                    int(watchdog["terminal_budget"]) !=
                        int(gate["watchdog_terminal_budget_max"]) or
                    watchdog_proof.get("independently_reconstructed") is not True or
                    bool(watchdog_proof.get("budget_started")) == low):
                raise SummaryError("V13 candidate failed wire-watchdog admission")
        return
    if (int(candidate["completed_flows"]) != int(gate["completed_flows"]) or
            int(candidate["switch_drops"]) > int(gate["switch_drops_max"]) or
            int(candidate["recovery_events"]) > int(gate["recovery_events_max"]) or
            int(candidate["timeout_recoveries"]) > int(gate["timeout_recoveries_max"]) or
            int(candidate["grant_trace"]["truncated"]) >
                int(gate["grant_trace_truncated_max"]) or
            int(generation["total_control_frames_sent"]) >
                int(gate["coalesced_control_frames_max"]) or
            int(generation["pending_generations"]) >
                int(gate["pending_grant_acks_max"]) or
            int(generation["retry_grants"]) >
                int(gate["normal_path_retry_grants_max"]) or
            int(generation["stale_events"]) != 0 or
            int(membership["first_grant_gated"]) !=
                int(gate["first_grant_gated_flows"]) or
            int(membership["first_grant_released"]) !=
                int(gate["first_grant_gate_releases"])):
        raise SummaryError("candidate failed the frozen common admission thresholds")
    if int(spec["schema_version"]) == 9:
        if (int(membership["initial_collection_starts"]) !=
                int(gate["initial_collection_starts"]) or
                int(membership["initial_collection_flushes"]) !=
                    int(gate["initial_collection_flushes"]) or
                int(membership["initial_collection_cancellations"]) >
                    int(gate["initial_collection_cancellations_max"]) or
                int(membership["initial_collection_wait_ns"]) !=
                    int(gate["initial_collection_wait_ns"]) or
                int(membership["initial_collection_max_wait_ns"]) !=
                    int(gate["initial_collection_max_wait_ns"]) or
                int(membership["max_batch"]) != int(gate["coalesced_max_batch"]) or
                int(generation["first_flush_minus_first_registration_ns"]) !=
                    int(gate["first_flush_minus_first_registration_ns"]) or
                int(generation["initial_generation_send_timestamp_count"]) != 1 or
                int(generation["ack_required_batches"]) !=
                    int(gate["ack_required_batches"]) or
                int(generation["ack_required_grants"]) !=
                    int(gate["ack_required_grants"]) or
                int(generation["ack_optional_batches"]) <
                    int(gate["ack_optional_generations_min"]) or
                int(generation["release_threshold_deferrals"]) <
                    int(gate["release_threshold_deferrals_min"])):
            raise SummaryError("V9 candidate failed fixed initial-collection admission")
    elif int(spec["schema_version"]) == 10:
        validate_v10_initial_collection(
            membership, generation, int(candidate["active_flows"]), "candidate admission")
        if (int(generation["ack_required_batches"]) !=
                int(gate["ack_required_batches"]) or
                int(generation["ack_required_grants"]) !=
                    int(gate["ack_required_grants"]) or
                int(generation["ack_optional_batches"]) <
                    int(gate["ack_optional_generations_min"]) or
                int(generation["release_threshold_deferrals"]) <
                    int(gate["release_threshold_deferrals_min"])):
            raise SummaryError("V10 candidate failed sliding initial admission")
    else:
        selective_failed = int(spec["schema_version"]) >= 7 and (
            int(generation["ack_required_batches"]) <
                int(gate["ack_required_generations_min"]) or
            int(generation["ack_optional_batches"]) <
                int(gate["ack_optional_generations_min"]) or
            (int(spec["schema_version"]) == 8 and
             int(generation["release_threshold_deferrals"]) <
                int(gate["release_threshold_deferrals_min"])))
        if (selective_failed or int(membership["max_batch"]) <
                int(gate["coalesced_max_batch_min"])):
            raise SummaryError("pilot candidate failed versioned admission thresholds")


def analyze_replay(spec_path: Path, campaign_dir: Path) -> Mapping[str, object]:
    """Admit every frozen candidate-only replay without reading performance."""
    spec = read_spec(spec_path)
    preflight = _validate_replay_preflight(spec_path, campaign_dir, spec)
    manifests = discover_manifests(campaign_dir, spec, "replay")
    candidates = []
    for identity in replay_matrix(spec):
        try:
            candidate, _output = validate_mechanism_run(
                manifests[identity], spec, spec_path, campaign_dir, preflight, "replay")
        except (OSError, ValueError, KeyError, TypeError, SummaryError) as exc:
            raise SummaryError(f"known-failure replay rejected; {identity}: {exc}") from exc
        _validate_candidate_gate(candidate, spec)
        candidates.append(candidate)
    replay = dict(spec["known_failure_replay"])
    if int(spec["schema_version"]) in (11, 12, 13):
        expected = {(int(row["seed"]), int(row["active_flows"])):
                    str(row["flow_sha256"]) for row in replay["identities"]}
        observed = {(int(row["seed"]), int(row["active_flows"])):
                    str(row["flow_sha256"]) for row in candidates}
        if observed != expected:
            raise SummaryError(
                f"V{spec['schema_version']} replay did not use all four sealed flow hashes")
        if int(spec["schema_version"]) in (12, 13):
            n15 = next(row for row in candidates if int(row["active_flows"]) == 15)
            proof = dict(n15["generation_ack"]["transition_prefix"])
            if (proof.get("normal_path") is not True or
                    proof.get("prefix_started") is not True or
                    int(n15["transition_prefix"]["timeouts"]) != 0 or
                    int(n15["transition_prefix"]["fallback_batches"]) != 0):
                raise SummaryError(
                    f"V{spec['schema_version']} known replay N=15 did not use normal ready path")
        if int(spec["schema_version"]) == 13:
            n8 = next(row for row in candidates if int(row["active_flows"]) == 8)
            proof = dict(n8["generation_ack"]["transition_prefix_watchdog"])
            exact = dict(spec["transition_prefix_watchdog_protocol"][
                "known_replay_n8"])
            if any(int(proof[field]) != int(value)
                   for field, value in exact.items()):
                raise SummaryError("V13 known replay N=8 watchdog budget changed")
    elif candidates[0]["flow_sha256"] != replay["flow_sha256"]:
        raise SummaryError("known-failure replay did not use the sealed V8 flow hash")
    result = {
        "schema_version": int(spec["schema_version"]),
        "scope": "replay", "status": "admitted",
        "passed": True, "spec_sha256": sha256_file(spec_path),
        "replay_preflight_sha256": sha256_file(campaign_dir / "preflight.json"),
        "simulator_git_sha": preflight["simulator_git_sha"],
        "run_count": len(candidates),
        "mechanism_admission_passed": True, "performance_emitted": False,
        "runs": candidates,
    }
    if int(spec["schema_version"]) in (11, 12, 13):
        result["flow_sha256_by_n"] = {
            str(row["active_flows"]): row["flow_sha256"] for row in candidates}
    else:
        result["flow_sha256"] = candidates[0]["flow_sha256"]
        result["v8_observed_initial_generation_flows"] = replay[
            "v8_observed_initial_generation_flows"]
        result[f"v{spec['schema_version']}_initial_generation_flows"] = (
            candidates[0]["generation_ack"]["initial_generation_flows"])
    return result


def analyze_pilot(spec_path: Path, campaign_dir: Path) -> Mapping[str, object]:
    """Admit only the frozen mechanism pilot; never open performance artifacts."""
    spec = read_spec(spec_path)
    preflight = _validate_preflight(spec_path, campaign_dir, spec, formal=False)
    replay_digest = None
    if int(spec["schema_version"]) in (9, 10, 11, 12, 13):
        try:
            validate_replay_admission(spec_path, spec, preflight, campaign_dir)
        except (OSError, ValueError, KeyError, TypeError, CampaignError) as exc:
            raise SummaryError(f"pilot analysis is blocked by replay admission: {exc}") from exc
        replay_digest = sha256_file(replay_admission_path(spec, campaign_dir))
    manifests = discover_manifests(campaign_dir, spec, "pilot")
    admitted: List[Tuple[Dict[str, object], Path]] = []
    for identity in pilot_matrix(spec):
        try:
            admitted.append(validate_mechanism_run(
                manifests[identity], spec, spec_path, campaign_dir, preflight, "pilot"))
        except (OSError, ValueError, KeyError, TypeError, SummaryError) as exc:
            raise SummaryError(f"pilot rejected; {identity}: {exc}") from exc
    rows = [row for row, _output in admitted]
    pilot_hashes: Dict[int, str] = {}
    for count in sorted({int(row["active_flows"]) for row in rows}):
        hashes = {str(row["flow_sha256"]) for row in rows
                  if int(row["active_flows"]) == count}
        if len(hashes) != 1:
            raise SummaryError(f"pilot N={count} paired traffic hash differs")
        pilot_hashes[count] = next(iter(hashes))
        candidate = next(row for row in rows
                         if int(row["active_flows"]) == count and
                         row["controller"] != CONTROL_ARM)
        _validate_candidate_gate(candidate, spec)
    result = {
        "schema_version": int(spec["schema_version"]),
        "scope": "pilot", "status": "admitted", "passed": True,
        "spec_sha256": sha256_file(spec_path),
        "pilot_preflight_sha256": sha256_file(campaign_dir / "preflight.json"),
        "simulator_git_sha": preflight["simulator_git_sha"],
        "known_replay_admission_sha256": replay_digest,
        "run_count": len(rows),
        "mechanism_admission_passed": True, "performance_emitted": False,
        "runs": rows,
    }
    if int(spec["schema_version"]) in (11, 12, 13):
        result["flow_sha256_by_n"] = {
            str(count): digest for count, digest in pilot_hashes.items()}
    else:
        result["flow_sha256"] = next(iter(pilot_hashes.values()))
    return result


def analyze(spec_path: Path, campaign_dir: Path) -> Mapping[str, object]:
    spec = read_spec(spec_path)
    preflight = _validate_preflight(spec_path, campaign_dir, spec, formal=True)
    try:
        validate_pilot_admission(spec_path, spec, preflight, campaign_dir)
    except (OSError, ValueError, KeyError, TypeError, CampaignError) as exc:
        raise SummaryError(f"formal analysis is blocked by pilot admission: {exc}") from exc
    pilot_digest = sha256_file(pilot_admission_path(spec, campaign_dir))
    replay_digest = (sha256_file(replay_admission_path(spec, campaign_dir))
                     if int(spec["schema_version"]) in (9, 10, 11, 12, 13) else None)
    manifests = discover_manifests(campaign_dir, spec, "holdout")
    admitted: List[Tuple[Dict[str, object], Path]] = []
    for identity in matrix(spec):
        try:
            admitted.append(validate_mechanism_run(
                manifests[identity], spec, spec_path, campaign_dir, preflight))
        except (OSError, ValueError, KeyError, TypeError, SummaryError) as exc:
            raise SummaryError(f"performance remains sealed; {identity}: {exc}") from exc
    seeds, counts, _arms = holdout_axes(spec)
    for seed in seeds:
        for count in counts:
            hashes = {row[0]["flow_sha256"] for row in admitted
                      if row[0]["seed"] == seed and row[0]["active_flows"] == count}
            if len(hashes) != 1:
                raise SummaryError(f"performance remains sealed; paired hash mismatch N={count} seed={seed}")
    hashes = {
        next(row[0]["flow_sha256"] for row in admitted
             if row[0]["seed"] == seed and row[0]["active_flows"] == count)
        for seed in seeds for count in counts
    }
    if len(hashes) != len(seeds) * len(counts):
        raise SummaryError("performance remains sealed; seed/N traffic inputs are not distinct")
    # Performance artifacts are first opened here, after all 40 mechanism rows pass.
    receiver = int(spec["candidate_pilot"]["receiver"])
    max_flows = int(spec["resource_limits"]["max_flows"])
    runs: List[Dict[str, object]] = []
    for mechanism, output_dir in admitted:
        run = dict(mechanism)
        run["performance"] = performance_metrics(
            output_dir, one_artifact(output_dir, "_input_flow.txt"),
            int(run["active_flows"]), receiver, max_flows)
        runs.append(run)
    return {
        "schema_version": int(spec["schema_version"]),
        "status": "validated_full_matrix_performance_unsealed",
        "spec_sha256": sha256_file(spec_path),
        "preflight_sha256": sha256_file(campaign_dir / "preflight.json"),
        "pilot_admission_sha256": pilot_digest,
        "known_replay_admission_sha256": replay_digest,
        "simulator_git_sha": preflight["simulator_git_sha"],
        "run_count": len(runs), "mechanism_admission_passed": True,
        "performance_unsealed": True, "runs": runs,
        "scope": {
            "membership_trace": "synchronized receiver-share N<=15 only",
            "delivery_evidence": str(spec["holdout_execution"]["delivery_evidence"]),
            "proactive_release": "not exercised because guard_proactive_release=0",
        },
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--campaign-dir", type=Path, required=True)
    parser.add_argument("--json-out", type=Path, required=True)
    parser.add_argument("--scope", choices=("replay", "pilot", "holdout"), required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    output = args.json_out.resolve()
    try:
        spec_path = args.spec.resolve()
        campaign_dir = args.campaign_dir.resolve()
        if args.scope == "replay":
            spec = read_spec(spec_path)
            expected = replay_admission_path(spec, campaign_dir).resolve()
            if output != expected:
                raise SummaryError(f"replay output must be the exact campaign artifact {expected}")
            result = analyze_replay(spec_path, campaign_dir)
        elif args.scope == "pilot":
            expected = pilot_admission_path(read_spec(spec_path), campaign_dir).resolve()
            if output != expected:
                raise SummaryError(f"pilot output must be the exact campaign artifact {expected}")
            result = analyze_pilot(spec_path, campaign_dir)
        else:
            result = analyze(spec_path, campaign_dir)
        output.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(output, result)
    except (OSError, ValueError, KeyError, TypeError, CampaignError, SummaryError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.scope == "replay":
        print(f"known-failure replay admitted without reading performance: {output}")
    elif args.scope == "pilot":
        print(f"pilot mechanism admitted without reading performance: {output}")
    else:
        print(f"validated all 40 mechanisms; performance unsealed: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
