#!/usr/bin/env python3
"""Fail-closed, mechanism-only analysis for the GUARD V14 campaign."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import json
import math
from pathlib import Path
import re
import sys
from typing import Dict, List, Mapping, Sequence, Tuple

try:
    from experiments.run_campaign import CampaignError, directory_size, sha256_file
    from experiments.run_guard_v14_compatibility import (
        FRESH_SCOPE, PROFILE_KEYS, REPLAY_SCOPE, _load_gate, compatibility_plan,
        load_preflight, read_spec, replay_plan, run_command,
        validate_sealed_replay,
    )
    from experiments.analyze_membership_coalescing_holdout import (
        parse_bounded_trace, parse_coalescing_stats, parse_lifecycle,
        parse_small_set_stats, parse_transition_prefix_stats,
        parse_transition_watchdog_stats,
    )
    from experiments.summarize_campaign import (
        SummaryError, parse_guard_stats, parse_pfc,
    )
    from experiments.summarize_workload import (
        atomic_json, one_artifact, parse_config, parse_snapshot,
    )
except ModuleNotFoundError:  # Direct execution from experiments/.
    from run_campaign import CampaignError, directory_size, sha256_file
    from run_guard_v14_compatibility import (
        FRESH_SCOPE, PROFILE_KEYS, REPLAY_SCOPE, _load_gate, compatibility_plan,
        load_preflight, read_spec, replay_plan, run_command,
        validate_sealed_replay,
    )
    from analyze_membership_coalescing_holdout import (
        parse_bounded_trace, parse_coalescing_stats, parse_lifecycle,
        parse_small_set_stats, parse_transition_prefix_stats,
        parse_transition_watchdog_stats,
    )
    from summarize_campaign import SummaryError, parse_guard_stats, parse_pfc
    from summarize_workload import atomic_json, one_artifact, parse_config, parse_snapshot


TRACE_FIELDS_V14 = (
    "time_ns", "event", "set_change", "host_node", "flow_id",
    "data_source_ip", "data_destination_ip", "active_flows",
    "line_rate_bps", "grant_rate_bps", "next_seq", "serialized_bytes",
    "generation", "pending_acks", "ack_required", "transaction_id",
    "grant_phase", "membership_target_n", "subject_role",
    "prefix_target_bytes", "prefix_observed_bytes", "drain_outcome",
    "activation_batch_index", "activation_batch_size", "activation_register_ns",
    "allocation_revision", "progress_revision", "membership_revision",
    "snapshot_valid", "frozen_capacity_bps", "frozen_active_records",
    "frozen_draining_records", "frozen_draining_reserved_bps",
    "frozen_allocatable_bps", "frozen_encoded_target_bps",
    "live_active_records", "live_draining_records",
    "live_draining_reserved_bps", "capacity_recompute_pending", "reason_mask",
)
TRACE_STRING_FIELDS = {
    "event", "set_change", "grant_phase", "subject_role", "drain_outcome",
}
TRACE_EVENTS = {
    "sent", "received", "grant_stale", "grant_generation_zero",
    "grant_generation_mismatch", "ack_sent", "ack_received", "ack_stale",
    "ack_retire_close", "ack_retired",
}
PHASES = {
    "none", "fast_prepare", "fast_activate", "transition_prepare",
    "transition_activate", "release",
}
ROLES = {"none", "incumbent", "waiter"}
DRAIN_OUTCOMES = {"none", "ready", "timeout"}
MIXED_FIELDS = (
    "enabled", "freezes", "mixed_pg_freezes", "max_entries",
    "prepare_decreases", "activation_waiters", "activation_increases",
    "release_required", "release_optional", "last_hash_xor",
    "terminal_records", "terminal_holds", "terminal_ledger",
)
REFRESH_FIELDS = (
    "enabled", "progress_requests", "progress_transactions", "progress_commits",
    "progress_busy_deferrals", "draining_requests", "draining_pending",
    "draining_direct", "boundary_commits", "completion_releases",
    "max_draining_records", "max_draining_reserved_bps",
    "terminal_draining_records", "terminal_draining_reserved_bps",
    "terminal_progress_dirty",
)
RETIRED_ACK_FIELDS = ("closures", "received", "peak", "terminal", "overflow")
AUDIT_STATS_FIELDS = ("enabled", "records", "attempted", "written", "truncated", "max_records")
AUDIT_FIELDS = (
    "receiver_node", "receiver_nic", "epoch", "transaction",
    "priority_group_set_hash", "target_vector_hash", "target_vector_entries",
    "capacity_bps", "active_upper_bound_bps", "draining_upper_bound_bps",
    "active_plus_draining_upper_bound_bps", "draining_wire_upper_bound_bytes",
    "prefix_target_bytes", "prefix_observed_bytes", "start_ns", "deadline_ns",
    "deadline_policy", "deadline_outcome", "terminal_closure",
)
AUDIT_STRING_FIELDS = {"deadline_policy", "deadline_outcome", "terminal_closure"}
RECOVERY_FIELDS = (
    "recovery_nacks_generated", "recovery_nacks_received",
    "irn_nacks_generated", "irn_nacks_received", "irn_retransmit_packets",
    "irn_retransmit_bytes",
)
OPTIONAL_CONFIG_KEYS = {
    "guard_transition_prefix_wire_watchdog",
    "guard_transition_prefix_fail_closed", "guard_mixed_pg_vector_fastpath",
    "guard_serialized_progress_refresh", "guard_serialized_draining",
}
CONFIG_KEYS = {
    "guard_lambda": "GUARD_LAMBDA", "guard_beta": "GUARD_EWMA_BETA",
    "guard_gamma": "GUARD_RELEASE_GAMMA",
    **{key: key.upper() for key in PROFILE_KEYS
       if key not in ("guard_lambda", "guard_beta", "guard_gamma")},
}
COMPLETION_RE = re.compile(
    r"finished so far:\s*(?P<finished>\d+)/ total:\s*(?P<total>\d+)")


def _read_json(path: Path) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SummaryError(f"cannot read JSON: {path}") from exc
    if not isinstance(value, dict):
        raise SummaryError(f"JSON root must be an object: {path}")
    return value


def _named_stats(path: Path, prefix: str, fields: Sequence[str]) -> Dict[str, int]:
    matches = []
    for line in path.read_text(encoding="utf-8", errors="strict").splitlines():
        parts = line.split()
        if parts[:1] == [prefix]:
            matches.append(parts[1:])
    if len(matches) != 1:
        raise SummaryError(f"expected one {prefix} stats row: {path}")
    tokens = matches[0]
    if len(tokens) != 2 * len(fields) or tuple(tokens[::2]) != tuple(fields):
        raise SummaryError(f"{prefix} stats labels changed: {path}")
    try:
        result = {field: int(raw) for field, raw in zip(tokens[::2], tokens[1::2])}
    except ValueError as exc:
        raise SummaryError(f"non-integer {prefix} stats row: {path}") from exc
    if any(value < 0 for value in result.values()):
        raise SummaryError(f"negative {prefix} statistic: {path}")
    return result


def parse_v14_trace(path: Path, max_lines: int) -> Tuple[List[Dict[str, object]], Dict[str, int]]:
    lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    if len(lines) < 2 or tuple(lines[0].split(",")) != TRACE_FIELDS_V14:
        raise SummaryError(f"unexpected V14 grant trace schema: {path}")
    footer = lines[-1].split()
    if (len(footer) != 7 or footer[:2] != ["#", "attempted"] or
            footer[3] != "written" or footer[5] != "truncated"):
        raise SummaryError(f"malformed grant trace footer: {path}")
    try:
        counts = {"attempted": int(footer[2]), "written": int(footer[4]),
                  "truncated": int(footer[6])}
    except ValueError as exc:
        raise SummaryError(f"non-integer grant trace footer: {path}") from exc
    data = lines[1:-1]
    if (counts["written"] != len(data) or
            counts["attempted"] != counts["written"] + counts["truncated"] or
            counts["written"] > max_lines or counts["truncated"] != 0):
        raise SummaryError(f"grant trace count/cap/truncation failure: {path}")
    rows: List[Dict[str, object]] = []
    for line_number, raw in enumerate(csv.DictReader([lines[0], *data]), 2):
        if (raw["event"] not in TRACE_EVENTS or raw["grant_phase"] not in PHASES or
                raw["subject_role"] not in ROLES or
                raw["drain_outcome"] not in DRAIN_OUTCOMES):
            raise SummaryError(f"invalid trace enum: {path}:{line_number}")
        row: Dict[str, object] = {field: raw[field] for field in TRACE_STRING_FIELDS}
        try:
            for field in TRACE_FIELDS_V14:
                if field not in TRACE_STRING_FIELDS:
                    row[field] = int(raw[field])
        except (TypeError, ValueError) as exc:
            raise SummaryError(f"non-integer trace field: {path}:{line_number}") from exc
        if any(int(row[field]) < 0 for field in TRACE_FIELDS_V14
               if field not in TRACE_STRING_FIELDS and field != "activation_register_ns"):
            raise SummaryError(f"negative trace field: {path}:{line_number}")
        rows.append(row)
    return rows, counts


def parse_transition_audit(path: Path, max_records: int) -> Tuple[List[Dict[str, object]], Dict[str, int]]:
    lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    if len(lines) < 2 or tuple(lines[0].split(",")) != AUDIT_FIELDS:
        raise SummaryError(f"unexpected transition audit schema: {path}")
    footer = lines[-1].split()
    if (len(footer) != 9 or footer[:2] != ["#", "records"] or
            footer[3] != "attempted" or footer[5] != "written" or
            footer[7] != "truncated"):
        raise SummaryError(f"malformed transition audit footer: {path}")
    try:
        counts = {"records": int(footer[2]), "attempted": int(footer[4]),
                  "written": int(footer[6]), "truncated": int(footer[8])}
    except ValueError as exc:
        raise SummaryError(f"non-integer transition audit footer: {path}") from exc
    data = lines[1:-1]
    if (counts["records"] != counts["attempted"] or
            counts["attempted"] != counts["written"] + counts["truncated"] or
            counts["written"] != len(data) or counts["written"] > max_records or
            counts["truncated"] != 0):
        raise SummaryError(f"transition audit count/cap/truncation failure: {path}")
    rows: List[Dict[str, object]] = []
    for line_number, raw in enumerate(csv.DictReader([lines[0], *data]), 2):
        row: Dict[str, object] = {field: raw[field] for field in AUDIT_STRING_FIELDS}
        try:
            for field in AUDIT_FIELDS:
                if field not in AUDIT_STRING_FIELDS:
                    row[field] = int(raw[field])
        except (TypeError, ValueError) as exc:
            raise SummaryError(f"non-integer transition audit field: {path}:{line_number}") from exc
        if any(int(row[field]) < 0 for field in AUDIT_FIELDS
               if field not in AUDIT_STRING_FIELDS):
            raise SummaryError(f"negative transition audit field: {path}:{line_number}")
        rows.append(row)
    return rows, counts


def _expected_plan(spec: Mapping[str, object], scope: str) -> List[Dict[str, object]]:
    if scope == REPLAY_SCOPE:
        return replay_plan(spec)
    if scope == FRESH_SCOPE:
        return compatibility_plan(spec)
    raise SummaryError(f"unknown V14 analysis scope: {scope}")


def discover_manifests(campaign_dir: Path, spec: Mapping[str, object],
                       scope: str) -> List[Mapping[str, object]]:
    paths = sorted((campaign_dir / "runs" / scope).glob("*/manifest.json"))
    plan = _expected_plan(spec, scope)
    found: Dict[Tuple[str, int], Mapping[str, object]] = {}
    for path in paths:
        row = _read_json(path)
        try:
            identity = (str(row["scenario"]), int(row["seed"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise SummaryError(f"malformed run identity: {path}") from exc
        if identity in found:
            raise SummaryError(f"duplicate run identity: {identity}")
        found[identity] = row
    expected = {(str(row["scenario"]), int(row["seed"])) for row in plan}
    if set(found) != expected or len(found) != len(plan):
        raise SummaryError(
            f"mechanism matrix incomplete for {scope}: missing={sorted(expected-set(found))}, "
            f"unexpected={sorted(set(found)-expected)}")
    return [found[(str(row["scenario"]), int(row["seed"]))] for row in plan]


def _profile(spec: Mapping[str, object], scope: str, scenario: str) -> Dict[str, object]:
    profile = dict(spec["v13_replay_profile" if scope == REPLAY_SCOPE else "final_guard"])
    if scope == FRESH_SCOPE:
        matches = [row for row in spec["fresh_scenarios"] if row["name"] == scenario]
        if len(matches) != 1:
            raise SummaryError(f"missing scenario profile: {scenario}")
        profile.update(matches[0]["feature_overrides"])
    return profile


def _numeric_config(config: Mapping[str, str], key: str, expected: object,
                    optional_zero: bool = False) -> None:
    if key not in config and optional_zero and float(expected) == 0.0:
        return
    try:
        actual = float(config[key])
        wanted = float(expected)
    except (KeyError, TypeError, ValueError) as exc:
        raise SummaryError(f"config lacks numeric {key}") from exc
    if actual != wanted:
        raise SummaryError(f"config {key}={actual}, expected {wanted}")


def validate_config(config: Mapping[str, str], spec: Mapping[str, object],
                    manifest: Mapping[str, object]) -> None:
    topology = dict(spec["topology"])
    limits = dict(spec["resource_limits"])
    exact = {
        "CC_MODE": "11", "LB_MODE": "0", "ENABLE_PFC": "1",
        "ENABLE_IRN": "0", "BUFFER_SIZE": "9", "MONITOR_PROFILE": "bulk",
        "TOPOLOGY_FILE": f"config/{topology['name']}.txt",
        "GUARD_LIFECYCLE_TRACE": "1", "GUARD_CONTROLLER_TRACE": "0",
        "GUARD_GRANT_TRACE": "1",
    }
    for key, expected in exact.items():
        if config.get(key) != expected:
            raise SummaryError(f"config {key}={config.get(key)!r}, expected {expected!r}")
    numeric = {
        "RANDOM_SEED": manifest["seed"], "PREFLIGHT_MAX_FLOWS": limits["max_flows"],
        "ANALYSIS_WARMUP_TIME": topology["analysis_warmup_s"],
        "GUARD_LIFECYCLE_TRACE_MAX_LINES": limits["lifecycle_trace_max_lines"],
        "GUARD_GRANT_TRACE_MAX_LINES": limits["grant_trace_max_lines"],
    }
    profile = _profile(spec, str(manifest["scope"]), str(manifest["scenario"]))
    numeric.update({CONFIG_KEYS[key]: value for key, value in profile.items()})
    if manifest["scope"] == FRESH_SCOPE:
        numeric.update({
            "GUARD_TRANSITION_AUDIT": 1,
            "GUARD_TRANSITION_AUDIT_MAX_RECORDS": limits[
                "transition_audit_max_records"],
        })
    for key, expected in numeric.items():
        profile_key = next((name for name, config_key in CONFIG_KEYS.items()
                            if config_key == key), None)
        _numeric_config(config, key, expected,
                        profile_key in OPTIONAL_CONFIG_KEYS if profile_key else False)


def _traffic(preflight: Mapping[str, object], manifest: Mapping[str, object]) -> Mapping[str, object]:
    matches = [row for row in preflight["traffic"]
               if row["scope"] == manifest["scope"] and
               row["scenario"] == manifest["scenario"] and
               int(row["seed"]) == int(manifest["seed"])]
    if len(matches) != 1:
        raise SummaryError("manifest lacks one frozen traffic identity")
    return matches[0]


def validate_manifest(manifest: Mapping[str, object], spec_path: Path,
                      spec: Mapping[str, object], campaign_dir: Path,
                      preflight: Mapping[str, object], scope: str) -> Tuple[Path, Mapping[str, object]]:
    identity = (manifest.get("scenario"), manifest.get("seed"))
    if (manifest.get("schema_version") != 14 or manifest.get("scope") != scope or
            manifest.get("status") != "completed" or manifest.get("returncode") != 0 or
            manifest.get("stop_reason") != "completed" or manifest.get("git_dirty") or
            manifest.get("performance_metrics_emitted") is not False or
            manifest.get("completion_validation") != "deferred_to_analyzer"):
        raise SummaryError(f"run did not finish in sealed mechanism-only state: {identity}")
    if (manifest.get("repo") != preflight["repo"] or
            manifest.get("simulator_git_sha") != preflight["simulator_git_sha"] or
            manifest.get("spec_sha256") != sha256_file(spec_path) or
            manifest.get("preflight_sha256") != sha256_file(campaign_dir / "preflight.json") or
            manifest.get("stage_gate_sha256") != sha256_file(
                campaign_dir / "stage-gate.json") or
            manifest.get("resource_limits") != spec["resource_limits"]):
        raise SummaryError(f"manifest provenance changed: {identity}")
    traffic = _traffic(preflight, manifest)
    expected_identity = {
        "scope": scope, "seed": int(manifest["seed"]),
        "scenario": str(manifest["scenario"]),
        "flow_count": int(traffic["flow_count"]), "profile": str(manifest["profile"]),
    }
    expected_cli = run_command(Path(str(preflight["repo"])), spec,
                               expected_identity, traffic)
    if (manifest.get("flow_path") != traffic["path"] or
            manifest.get("flow_sha256") != traffic["sha256"] or
            int(manifest.get("flow_count", -1)) != int(traffic["flow_count"]) or
            manifest.get("cli") != expected_cli or
            manifest.get("cli_sha256") != traffic["cli_sha256"]):
        raise SummaryError(f"traffic or CLI seal changed: {identity}")
    output = Path(str(manifest.get("output_dir", "")))
    launcher = Path(str(manifest.get("launcher_log", "")))
    limits = dict(spec["resource_limits"])
    if (not output.is_dir() or output.name != manifest.get("output_id") or
            not launcher.is_file() or
            int(manifest.get("max_rss_kib", limits["rss_kib_per_run"] + 1)) >
                int(limits["rss_kib_per_run"]) or
            float(manifest.get("elapsed_seconds", math.inf)) >
                int(limits["wall_time_seconds_per_run"])):
        raise SummaryError(f"output/log/resource evidence failed: {identity}")
    actual_bytes = directory_size(output)
    if (actual_bytes != int(manifest.get("output_bytes", -1)) or
            actual_bytes + launcher.stat().st_size > int(limits["artifact_bytes_per_run"])):
        raise SummaryError(f"artifact size drift or cap violation: {identity}")
    return output, traffic


def _completion_from_config_log(path: Path, expected: int) -> Dict[str, int]:
    matches = [COMPLETION_RE.search(line) for line in
               path.read_text(encoding="utf-8", errors="strict").splitlines()]
    values = [(int(match.group("finished")), int(match.group("total")))
              for match in matches if match]
    if not values or values[-1] != (expected, expected):
        raise SummaryError(f"simulation completion marker is not {expected}/{expected}: {path}")
    if any(finished > total for finished, total in values):
        raise SummaryError(f"invalid completion marker: {path}")
    return {"finished": values[-1][0], "total": values[-1][1]}


def _lifecycle_audit(rows: Sequence[Mapping[str, object]], expected: int) -> Dict[str, int]:
    if len(rows) != expected or len({int(row["flow_id"]) for row in rows}) != expected:
        raise SummaryError(f"lifecycle rows/IDs differ from registered count {expected}")
    proactive = completion = 0
    for row in rows:
        register = int(row["register_ns"])
        release = int(row["release_ns"])
        complete = int(row["complete_ns"])
        if min(register, release, complete) < 0 or not register <= release <= complete:
            raise SummaryError(f"lifecycle ordering failed for flow {row['flow_id']}")
        if row["release_reason"] == "completion":
            completion += 1
            if int(row["remaining_bytes_at_release"]) != 0 or release != complete:
                raise SummaryError("completion lifecycle release is not contiguous completion")
        elif row["release_reason"] == "proactive":
            proactive += 1
            if int(row["remaining_bytes_at_release"]) <= 0:
                raise SummaryError("proactive lifecycle release lacks remaining bytes")
        else:
            raise SummaryError(f"nonterminal lifecycle release: {row['release_reason']}")
    return {"rows": expected, "completion_releases": completion,
            "proactive_releases": proactive}


def _grant_key(row: Mapping[str, object]) -> Tuple[int, int]:
    return int(row["generation"]), int(row["flow_id"])


def trace_closure(rows: Sequence[Mapping[str, object]], fresh: bool,
                  receiver_capacity: int) -> Dict[str, int]:
    ordinals = {id(row): ordinal for ordinal, row in enumerate(rows)}
    bad_events = {"grant_stale", "grant_generation_zero",
                  "grant_generation_mismatch", "ack_stale"}
    if any(row["event"] in bad_events for row in rows):
        raise SummaryError("grant trace contains stale/rejected generation activity")
    by_event = {event: defaultdict(list) for event in (
        "sent", "received", "ack_sent", "ack_received",
        "ack_retire_close", "ack_retired",
    )}
    for row in rows:
        if row["event"] in by_event:
            by_event[str(row["event"])][_grant_key(row)].append(row)
    if not by_event["sent"] or set(by_event["sent"]) != set(by_event["received"]):
        raise SummaryError("sent/received grant identities do not close")
    required_keys = set()
    optional_keys = set()
    for key, sent_rows in by_event["sent"].items():
        received_rows = by_event["received"][key]
        if len(sent_rows) != 1 or len(received_rows) != 1:
            raise SummaryError(f"retry or duplicate grant identity: {key}")
        sent, received = sent_rows[0], received_rows[0]
        if (int(sent["grant_rate_bps"]) != int(received["grant_rate_bps"]) or
                int(sent["ack_required"]) != int(received["ack_required"]) or
                int(sent["serialized_bytes"]) != 60 or
                int(received["serialized_bytes"]) != 60 or
                int(sent["time_ns"]) > int(received["time_ns"])):
            raise SummaryError(f"grant wire evidence disagrees: {key}")
        if int(sent["ack_required"]) == 1:
            required_keys.add(key)
        else:
            optional_keys.add(key)
    ordinary_ack_keys = set(by_event["ack_received"])
    retired_ack_keys = set(by_event["ack_retired"])
    retired_close_keys = set(by_event["ack_retire_close"])
    if (set(by_event["ack_sent"]) != required_keys or
            ordinary_ack_keys & retired_ack_keys or
            ordinary_ack_keys | retired_ack_keys != required_keys or
            retired_close_keys != retired_ack_keys):
        raise SummaryError("ACK evidence differs from required grant identities")
    for key in required_keys:
        terminal_event = "ack_retired" if key in retired_ack_keys else "ack_received"
        required_events = ["sent", "received", "ack_sent", terminal_event]
        if key in retired_ack_keys:
            required_events.append("ack_retire_close")
        if any(len(by_event[event][key]) != 1 for event in required_events):
            raise SummaryError(f"ACK retry or duplicate identity: {key}")
        ordered = [by_event[event][key][0] for event in required_events]
        wire_order = [by_event[event][key][0] for event in
                      ("sent", "received", "ack_sent", terminal_event)]
        causal_order = [(int(row["time_ns"]), ordinals[id(row)])
                        for row in wire_order]
        safety_order = ([by_event[event][key][0] for event in
                         ("sent", "ack_retire_close", "ack_retired")]
                        if key in retired_ack_keys else [])
        safety_causal_order = [(int(row["time_ns"]), ordinals[id(row)])
                               for row in safety_order]
        if (causal_order != sorted(causal_order) or
                safety_causal_order != sorted(safety_causal_order)):
            raise SummaryError(f"grant/ACK order failed: {key}")
        wire_rows = wire_order
        if any(int(row["serialized_bytes"]) != 60 for row in wire_rows):
            raise SummaryError(f"control frame is not 60 bytes: {key}")
        if fresh:
            phase = str(wire_rows[0]["grant_phase"])
            endpoints = (int(wire_rows[0]["data_source_ip"]),
                         int(wire_rows[0]["data_destination_ip"]))
            if (phase == "none" or
                    any(str(row["grant_phase"]) != phase for row in ordered) or
                    any((int(row["data_source_ip"]),
                         int(row["data_destination_ip"])) != endpoints
                        for row in ordered) or
                    any(int(row["ack_required"]) != 1 for row in ordered)):
                raise SummaryError(
                    f"required ACK chain changed exact phase/endpoint identity: {key}")
            sent_transaction = int(wire_rows[0]["transaction_id"])
            if (sent_transaction <= 0 or
                    int(by_event["received"][key][0]["transaction_id"]) != 0 or
                    int(by_event["ack_sent"][key][0]["transaction_id"]) != 0):
                raise SummaryError(
                    f"required ACK chain changed transaction provenance: {key}")
            if terminal_event == "ack_received":
                terminal = by_event[terminal_event][key][0]
                if (int(terminal["transaction_id"]) != sent_transaction or
                        int(terminal["grant_rate_bps"]) != 0):
                    raise SummaryError(
                        f"live ACK changed transaction/target provenance: {key}")
            else:
                close = by_event["ack_retire_close"][key][0]
                retired = by_event["ack_retired"][key][0]
                target = int(by_event["sent"][key][0]["grant_rate_bps"])
                if (target <= 0 or
                        int(by_event["received"][key][0]["grant_rate_bps"]) != target or
                        int(close["grant_rate_bps"]) != target or
                        int(retired["grant_rate_bps"]) != target or
                        int(close["transaction_id"]) != sent_transaction or
                        int(retired["transaction_id"]) != sent_transaction or
                        int(close["host_node"]) !=
                            int(by_event["sent"][key][0]["host_node"]) or
                        int(retired["host_node"]) !=
                            int(by_event["sent"][key][0]["host_node"]) or
                        str(close["subject_role"]) !=
                            str(by_event["sent"][key][0]["subject_role"]) or
                        str(retired["subject_role"]) !=
                            str(by_event["sent"][key][0]["subject_role"])):
                    raise SummaryError(
                        f"retired ACK changed transaction/action/target provenance: {key}")
        if key in retired_ack_keys and int(
                by_event["ack_retire_close"][key][0]["serialized_bytes"]) != 0:
            raise SummaryError(f"retired safety closure is not a local event: {key}")
    all_ack_keys = set().union(*(set(by_event[event]) for event in (
        "ack_sent", "ack_received", "ack_retire_close", "ack_retired")))
    if optional_keys & all_ack_keys:
        raise SummaryError("optional grant emitted an ACK")

    frozen_sent = []
    draining_sent = []
    recomputed_revisions = 0
    if fresh:
        snapshot_fields = (
            "allocation_revision", "progress_revision", "membership_revision",
            "snapshot_valid", "frozen_capacity_bps", "frozen_active_records",
            "frozen_draining_records", "frozen_draining_reserved_bps",
            "frozen_allocatable_bps", "frozen_encoded_target_bps",
            "live_active_records", "live_draining_records",
            "live_draining_reserved_bps", "capacity_recompute_pending",
            "reason_mask",
        )
        for row in rows:
            authoritative = row["event"] in (
                "sent", "ack_received", "ack_retire_close")
            if not authoritative:
                if any(int(row[field]) != 0 for field in snapshot_fields):
                    raise SummaryError(
                        "sender/non-authoritative row exposes receiver snapshot state")
                continue
            if int(row["snapshot_valid"]) != 1:
                raise SummaryError("receiver-authoritative row lacks a frozen snapshot")
            frozen_capacity = int(row["frozen_capacity_bps"])
            frozen_draining = int(row["frozen_draining_reserved_bps"])
            frozen_allocatable = int(row["frozen_allocatable_bps"])
            frozen_encoded = int(row["frozen_encoded_target_bps"])
            live_draining = int(row["live_draining_reserved_bps"])
            recompute_pending = int(row["capacity_recompute_pending"])
            if (int(row["line_rate_bps"]) != receiver_capacity or
                    frozen_capacity != receiver_capacity or
                    frozen_draining + frozen_allocatable != frozen_capacity or
                    frozen_encoded > frozen_allocatable or
                    int(row["allocation_revision"]) <= 0 or
                    int(row["frozen_active_records"]) <= 0 or
                    int(row["frozen_active_records"]) > 64 or
                    int(row["frozen_draining_records"]) > 64 or
                    int(row["live_active_records"]) > 64 or
                    int(row["live_draining_records"]) > 64 or
                    (int(row["frozen_draining_records"]) == 0) !=
                        (frozen_draining == 0) or
                    (int(row["live_draining_records"]) == 0) !=
                        (live_draining == 0) or
                    recompute_pending not in (0, 1) or
                    int(row["reason_mask"]) <= 0):
                raise SummaryError(
                    "receiver frozen-allocation trace row violates frozen D+A=C")
            if row["event"] == "sent" and int(row["grant_rate_bps"]) > frozen_allocatable:
                raise SummaryError("receiver grant exceeds frozen allocatable capacity")
            if live_draining > frozen_draining:
                raise SummaryError("live D exceeds the frozen draining reservation")
            if live_draining < frozen_draining and recompute_pending != 1:
                raise SummaryError("live D fell below frozen D without capacity recompute pending")

        tuple_fields = (
            "progress_revision", "membership_revision", "frozen_capacity_bps",
            "frozen_active_records", "frozen_draining_records",
            "frozen_draining_reserved_bps", "frozen_allocatable_bps",
            "frozen_encoded_target_bps", "reason_mask",
        )
        by_host_revision: Dict[Tuple[int, int], List[Mapping[str, object]]] = defaultdict(list)
        host_revision_order: Dict[int, List[int]] = defaultdict(list)
        last_revision_by_host: Dict[int, int] = {}
        for row in rows:
            if int(row["snapshot_valid"]) != 1:
                continue
            host = int(row["host_node"])
            revision = int(row["allocation_revision"])
            key = (host, revision)
            if (host in last_revision_by_host and
                    revision != last_revision_by_host[host] and
                    revision in host_revision_order[host]):
                raise SummaryError("allocation revision reappeared after a newer snapshot")
            if not by_host_revision[key]:
                host_revision_order[host].append(revision)
            by_host_revision[key].append(row)
            last_revision_by_host[host] = revision
        for key, revision_rows in by_host_revision.items():
            frozen_tuples = {
                tuple(int(row[field]) for field in tuple_fields)
                for row in revision_rows
            }
            if len(frozen_tuples) != 1:
                raise SummaryError(f"frozen snapshot tuple drifted within allocation revision {key}")
        for host, revisions in host_revision_order.items():
            if revisions != sorted(revisions) or len(revisions) != len(set(revisions)):
                raise SummaryError("allocation revisions are not strictly increasing")
            for index, revision in enumerate(revisions):
                revision_rows = by_host_revision[(host, revision)]
                needs_recompute = any(
                    int(row["live_active_records"]) > 0 and
                    int(row["live_draining_reserved_bps"]) <
                        int(row["frozen_draining_reserved_bps"])
                    for row in revision_rows)
                if not needs_recompute:
                    continue
                if index + 1 >= len(revisions):
                    raise SummaryError("capacity-dirty frozen revision lacks a later recompute")
                next_revision = revisions[index + 1]
                if next_revision != revision + 1:
                    raise SummaryError("capacity recompute skipped an allocation revision")
                next_first = by_host_revision[(host, next_revision)][0]
                if (int(next_first["frozen_draining_reserved_bps"]) !=
                        int(next_first["live_draining_reserved_bps"])):
                    raise SummaryError("next allocation revision did not refreeze live D")
                recomputed_revisions += 1

        for row in by_event["sent"].values():
            sent = row[0]
            frozen_sent.append(sent)
            if (int(sent["frozen_draining_records"]) > 0 and
                    int(sent["frozen_draining_reserved_bps"]) > 0):
                draining_sent.append(sent)
        if not frozen_sent:
            raise SummaryError("fresh V14 trace has no authoritative frozen allocation")
        prepare = [row[0] for row in by_event["sent"].values()
                   if row[0]["grant_phase"] in ("fast_prepare", "transition_prepare")]
        activation = [row[0] for row in by_event["sent"].values()
                      if row[0]["grant_phase"] in ("fast_activate", "transition_activate")]
        for before in prepare:
            key = _grant_key(before)
            if key not in required_keys:
                raise SummaryError("prepare decrease is not ACK-required")
            safety_event = ("ack_retire_close" if key in retired_close_keys
                            else "ack_received")
            safety = by_event[safety_event][key][0]
            ack_order = (int(safety["time_ns"]), ordinals[id(safety)])
            later = [row for row in activation
                     if int(row["host_node"]) == int(before["host_node"]) and
                     int(row["transaction_id"]) == int(before["transaction_id"])]
            if any((int(row["time_ns"]), ordinals[id(row)]) <= ack_order
                   for row in later):
                raise SummaryError("activation/increase preceded prepare-decrease ACK closure")
    return {
        "grant_frames": len(by_event["sent"]), "required_grants": len(required_keys),
        "optional_grants": len(optional_keys), "frozen_allocation_sent_rows": len(frozen_sent),
        "receiver_sent_rows_with_draining": len(draining_sent),
        "capacity_recomputed_revisions": recomputed_revisions,
        "retired_ack_closures": len(retired_close_keys),
        "retired_ack_wire_receipts": len(retired_ack_keys),
    }


def validate_retired_ack_closure(stats: Mapping[str, int],
                                 trace: Mapping[str, int]) -> None:
    if (int(stats["closures"]) != int(stats["received"]) or
            int(stats["terminal"]) != 0 or int(stats["overflow"]) != 0 or
            int(stats["closures"]) != int(trace["retired_ack_closures"]) or
            int(stats["received"]) != int(trace["retired_ack_wire_receipts"])):
        raise SummaryError("V14 retired ACK safety/wire closures do not close")


def _fnv_words(words: Sequence[int]) -> int:
    value = 14695981039346656037
    for word in (len(words), *words):
        for byte in range(8):
            value ^= (int(word) >> (byte * 8)) & 0xff
            value = (value * 1099511628211) & ((1 << 64) - 1)
    return value


def audit_transition_rows(rows: Sequence[Mapping[str, object]], stats: Mapping[str, int],
                          small: Mapping[str, int], prefix: Mapping[str, int], capacity: int,
                          mixed_pg_expected: bool) -> Dict[str, object]:
    if (len(rows) != int(stats["records"]) or int(stats["records"]) !=
            int(stats["attempted"]) or int(stats["written"]) != len(rows) or
            int(stats["truncated"]) != 0 or int(prefix["starts"]) != len(rows) or
            int(small["high_transitions"]) != len(rows)):
        raise SummaryError(
            "high transitions, prefix starts, and terminal audit rows do not close")
    identities = set()
    for row in rows:
        identity = (int(row["receiver_node"]), int(row["receiver_nic"]),
                    int(row["epoch"]), int(row["transaction"]))
        if identity in identities:
            raise SummaryError(f"duplicate transition audit identity: {identity}")
        identities.add(identity)
        if (int(row["priority_group_set_hash"]) == 0 or
                int(row["target_vector_hash"]) == 0 or
                int(row["target_vector_entries"]) <= 0 or
                int(row["capacity_bps"]) != capacity or
                not 0 <= int(row["active_upper_bound_bps"]) < capacity or
                int(row["prefix_observed_bytes"]) < int(row["prefix_target_bytes"]) or
                int(row["start_ns"]) > int(row["deadline_ns"]) or
                row["deadline_policy"] != "wire_residual_watchdog" or
                row["deadline_outcome"] != "ready" or
                row["terminal_closure"] != "activation_ack_closed"):
            raise SummaryError(f"transition audit row did not close normally: {identity}")
        if (int(row["active_plus_draining_upper_bound_bps"]) !=
                int(row["active_upper_bound_bps"]) +
                int(row["draining_upper_bound_bps"])):
            raise SummaryError(f"transition audit upper-bound identity failed: {identity}")
        # active_plus_draining is a wire-prefix upper bound and is deliberately
        # not compared with capacity.
    if mixed_pg_expected and rows:
        expected_hash = _fnv_words([4, 5, 6, 7])
        if any(int(row["priority_group_set_hash"]) != expected_hash for row in rows):
            raise SummaryError("mixed-PG audit hash differs from frozen size/BDP classes")
    per_receiver: Dict[int, set[int]] = defaultdict(set)
    for row in rows:
        per_receiver[int(row["receiver_node"])].add(int(row["epoch"]))
    return {
        "records": len(rows), "distinct_receivers": len(per_receiver),
        "epochs_per_receiver": {str(receiver): len(epochs)
                                for receiver, epochs in sorted(per_receiver.items())},
    }


def _zero_terminal(small: Mapping[str, int], vector: Mapping[str, int] | None,
                   refresh: Mapping[str, int] | None, prefix: Mapping[str, int],
                   watchdog: Mapping[str, int]) -> None:
    small_fields = (
        "terminal_phase", "terminal_pending_acks", "terminal_pending_membership",
        "terminal_waiters", "terminal_transaction_waiters", "terminal_ready_waiters",
        "terminal_collection_ready", "terminal_initial_flushed",
        "terminal_transition_committed", "terminal_revision",
        "terminal_consumed_revision",
    )
    prefix_fields = tuple(field for field in prefix if field.startswith("terminal_"))
    if (any(int(small[field]) != 0 for field in small_fields) or
            any(int(prefix[field]) != 0 for field in prefix_fields) or
            int(watchdog["terminal_budget"]) != 0):
        raise SummaryError("fastpath/prefix/watchdog terminal state is nonzero")
    if vector is not None and any(int(vector[field]) != 0 for field in
                                  ("terminal_records", "terminal_holds", "terminal_ledger")):
        raise SummaryError("target-vector terminal state is nonzero")
    if refresh is not None and any(int(refresh[field]) != 0 for field in
                                   ("terminal_draining_records",
                                    "terminal_draining_reserved_bps",
                                    "terminal_progress_dirty")):
        raise SummaryError("refresh/draining terminal state is nonzero")


def _requirements(spec: Mapping[str, object], scenario: str) -> Mapping[str, object]:
    matches = [row["requirements"] for row in spec["fresh_scenarios"]
               if row["name"] == scenario]
    if len(matches) != 1:
        raise SummaryError(f"missing scenario requirements: {scenario}")
    return matches[0]


def _apply_requirements(scenario: str, requirements: Mapping[str, object],
                        stats: Mapping[str, object], small: Mapping[str, int],
                        prefix: Mapping[str, int], watchdog: Mapping[str, int],
                        vector: Mapping[str, int], refresh: Mapping[str, int],
                        trace: Mapping[str, int], audit: Mapping[str, object]) -> None:
    values: Dict[str, int] = {
        "registered_flows": int(stats["registrations"]),
        "high_transitions": int(small["high_transitions"]),
        "prefix_starts": int(prefix["starts"]),
        "watchdog_records": int(watchdog["records"]),
        "transition_audit_records": int(audit["records"]),
        "mixed_pg_freezes": int(vector["mixed_pg_freezes"]),
        "sender_srpt_selections": int(stats["guard_sender_srpt_selections"]),
        "sender_srpt_non_rr": int(stats["guard_sender_srpt_non_rr"]),
        "one_rtt_bypass_flows": int(stats["guard_one_rtt_bypass_flows"]),
        "tail_bypass_flows": int(stats["guard_tail_bypass_flows"]),
        "proactive_releases": int(stats["proactive_releases"]),
        "draining_requests": int(refresh["draining_requests"]),
        "draining_pending": int(refresh["draining_pending"]),
        "draining_boundary_commits": int(refresh["boundary_commits"]),
        "draining_completion_releases": int(refresh["completion_releases"]),
        "max_draining_records": int(refresh["max_draining_records"]),
        "max_draining_reserved_bps": int(refresh["max_draining_reserved_bps"]),
        "receiver_sent_rows_with_draining": int(trace[
            "receiver_sent_rows_with_draining"]),
        "progress_requests": int(refresh["progress_requests"]),
        "progress_transactions": int(refresh["progress_transactions"]),
        "progress_commits": int(refresh["progress_commits"]),
        "progress_busy_deferrals": int(refresh["progress_busy_deferrals"]),
        "distinct_audit_receivers": int(audit["distinct_receivers"]),
    }
    for key, expected in requirements.items():
        if key == "epochs_per_audit_receiver_min":
            continue
        if key.endswith("_min"):
            field = key[:-4]
            if values.get(field, -1) < int(expected):
                raise SummaryError(f"{scenario}: {field}={values.get(field)} < {expected}")
        elif key.endswith("_exact"):
            field = key[:-6]
            if values.get(field) != int(expected):
                raise SummaryError(f"{scenario}: {field}={values.get(field)} != {expected}")
    if requirements.get("draining_requests_must_equal_direct_plus_pending") and (
            int(refresh["draining_requests"]) !=
            int(refresh["draining_direct"]) + int(refresh["draining_pending"])):
        raise SummaryError(f"{scenario}: drain request classification does not close")
    if requirements.get("draining_completion_releases_must_equal_requests") and (
            int(refresh["completion_releases"]) != int(refresh["draining_requests"])):
        raise SummaryError(f"{scenario}: draining reservations did not all complete")
    epochs_min = requirements.get("epochs_per_audit_receiver_min")
    if epochs_min is not None and (not audit["epochs_per_receiver"] or any(
            int(count) < int(epochs_min) for count in audit["epochs_per_receiver"].values())):
        raise SummaryError(f"{scenario}: per-receiver audit epochs are below {epochs_min}")


def validate_run(manifest: Mapping[str, object], spec_path: Path,
                 spec: Mapping[str, object], campaign_dir: Path,
                 preflight: Mapping[str, object], scope: str) -> Mapping[str, object]:
    output, traffic = validate_manifest(
        manifest, spec_path, spec, campaign_dir, preflight, scope)
    count = int(traffic["flow_count"])
    config = parse_config(output / "config.txt")
    validate_config(config, spec, manifest)
    snapshot_path = one_artifact(output, "_input_flow.txt")
    _flows, snapshot = parse_snapshot(
        snapshot_path, int(spec["topology"]["hosts"]),
        int(spec["resource_limits"]["max_flows"]))
    if snapshot["flow_count"] != count or snapshot["sha256"] != traffic["sha256"]:
        raise SummaryError("private input snapshot differs from frozen traffic")
    completion = _completion_from_config_log(output / "config.log", count)
    stats_path = one_artifact(output, "_out_guard_stats.txt")
    stats = parse_guard_stats(stats_path)
    recovery = sum(int(stats[field]) for field in RECOVERY_FIELDS)
    if (int(stats["switch_drops_total"]) != 0 or recovery != 0 or
            int(stats["timeout_recoveries"]) != 0):
        raise SummaryError("drop/recovery/timeout recovery is nonzero")
    pfc_trace = parse_pfc(one_artifact(output, "_out_pfc.txt"))
    if (int(stats["pfc_pause_count"]) != 0 or int(stats["pfc_resume_count"]) != 0 or
            int(pfc_trace["pfc_pause_events"]) != 0 or
            int(pfc_trace["pfc_resume_events"]) != 0):
        raise SummaryError("PFC activity is nonzero")
    membership = parse_coalescing_stats(stats_path, 13)
    for field in ("ack_stale", "stale_grants", "generation_zero_rejected",
                  "generation_mismatch_rejected", "pending", "retry_grants"):
        if int(membership[field]) != 0:
            raise SummaryError(f"membership {field} is nonzero")
    if (int(stats["grants_sent"]) != int(stats["grants_received"]) or
            int(membership["ack_sent"]) != int(membership["ack_received"])):
        raise SummaryError("global grant/ACK counters do not close")
    small = parse_small_set_stats(stats_path)
    prefix = parse_transition_prefix_stats(stats_path)
    watchdog = parse_transition_watchdog_stats(stats_path)
    if watchdog is None:
        raise SummaryError("wire watchdog stats row is absent")
    if (int(small["enabled"]) != 1 or int(small["limit"]) != 4 or
            int(small["barrier_violations"]) != 0 or int(small["early_unlocks"]) != 0 or
            int(small["unattributed_grant_frames"]) != 0 or
            int(small["unattributed_ack_frames"]) != 0 or
            int(small["wire_reconciled"]) != 1 or int(prefix["enabled"]) != 1 or
            int(prefix["timeouts"]) != 0 or int(prefix["degraded"]) != 0 or
            int(prefix["remaining_bytes"]) != 0 or int(prefix["fallback_batches"]) != 0 or
            int(prefix["fallback_closed_batches"]) != 0 or
            int(prefix["order_violations"]) != 0 or
            int(prefix["barrier_violations"]) != 0 or int(watchdog["enabled"]) != 1):
        raise SummaryError("safe fastpath/prefix mechanism gate failed")

    requirements = ({"registered_flows": count} if scope == REPLAY_SCOPE else
                    _requirements(spec, str(manifest["scenario"])))
    registered = int(requirements["registered_flows"])
    if (int(stats["registrations"]) != registered or
            int(stats["selected_registrations"]) != registered or
            int(stats["max_active_flows"]) <= 0):
        raise SummaryError(f"registered/selected count differs from {registered}")
    lifecycle_rows = parse_lifecycle(
        one_artifact(output, "_out_guard_lifecycle.csv"),
        int(spec["resource_limits"]["lifecycle_trace_max_lines"]))
    lifecycle = _lifecycle_audit(lifecycle_rows, registered)

    trace_path = one_artifact(output, "_out_guard_grants.csv")
    if scope == REPLAY_SCOPE:
        trace_rows, trace_footer = parse_bounded_trace(
            trace_path, int(spec["resource_limits"]["grant_trace_max_lines"]),
            13, v11_enabled=True)
        trace = trace_closure(trace_rows, False,
                              int(spec["topology"]["receiver_capacity_bps"]))
        vector = refresh = retired_ack = None
        if int(watchdog["records"]) > 1 or int(watchdog["non_reconstructable"]) != 0 or int(
                watchdog["inconsistent"]) != 0:
            raise SummaryError("single-transition V13 replay watchdog is not reconstructable")
        n = count
        expected_transitions = 0 if n <= 4 else 1
        if (int(small["high_transitions"]) != expected_transitions or
                int(prefix["starts"]) != expected_transitions or
                int(prefix["ready"]) != expected_transitions or
                int(watchdog["records"]) != expected_transitions or
                int(stats["proactive_releases"]) != 0 or
                int(stats["completion_releases"]) != n or
                lifecycle["completion_releases"] != n):
            raise SummaryError(f"V13 replay path changed for N={n}")
        _zero_terminal(small, None, None, prefix, watchdog)
        audit = {"records": 0, "distinct_receivers": 0, "epochs_per_receiver": {}}
    else:
        trace_rows, trace_footer = parse_v14_trace(
            trace_path, int(spec["resource_limits"]["grant_trace_max_lines"]))
        trace = trace_closure(trace_rows, True,
                              int(spec["topology"]["receiver_capacity_bps"]))
        vector = _named_stats(stats_path, "guard_mixed_pg_vector", MIXED_FIELDS)
        refresh = _named_stats(stats_path, "guard_refresh_draining", REFRESH_FIELDS)
        retired_ack = _named_stats(
            stats_path, "guard_retired_ack", RETIRED_ACK_FIELDS)
        audit_stats = _named_stats(stats_path, "guard_transition_audit", AUDIT_STATS_FIELDS)
        if (int(vector["enabled"]) != 1 or int(vector["freezes"]) <= 0 or
                int(refresh["enabled"]) != 1 or
                int(refresh["progress_transactions"]) != int(refresh["progress_commits"])):
            raise SummaryError("V14 vector/refresh bundle did not close")
        validate_retired_ack_closure(retired_ack, trace)
        audit_rows, audit_footer = parse_transition_audit(
            one_artifact(output, "_out_guard_transition_audit.csv"),
            int(spec["resource_limits"]["transition_audit_max_records"]))
        if any(audit_footer[key] != audit_stats[key] for key in
               ("records", "attempted", "written", "truncated")):
            raise SummaryError("transition audit footer/stats counters disagree")
        audit = audit_transition_rows(
            audit_rows, audit_stats, small, prefix,
            int(spec["topology"]["receiver_capacity_bps"]),
            str(manifest["scenario"]) == "mixed_pg_n15")
        _zero_terminal(small, vector, refresh, prefix, watchdog)
        _apply_requirements(str(manifest["scenario"]), requirements, stats, small,
                            prefix, watchdog, vector, refresh, trace, audit)
        # Multiple transitions or receivers legitimately make the legacy V13
        # aggregate non-reconstructable; per-transition rows above replace it.

    return {
        "ordinal": int(manifest["ordinal"]), "scope": scope,
        "scenario": str(manifest["scenario"]), "seed": int(manifest["seed"]),
        "flow_count": count, "registered_flows": registered,
        "flow_sha256": str(traffic["sha256"]), "output_id": manifest["output_id"],
        "completion": completion, "switch_drops": 0, "recovery_events": recovery,
        "pfc_events": 0, "grant_trace": trace_footer,
        "grant_closure": trace, "lifecycle": lifecycle,
        "safe_activation": small, "transition_prefix": prefix,
        "transition_watchdog": watchdog, "target_vector": vector,
        "refresh_draining": refresh, "retired_ack": retired_ack,
        "transition_audit": audit,
        "feature_signals": {
            "sender_srpt_selections": int(stats["guard_sender_srpt_selections"]),
            "sender_srpt_non_rr": int(stats["guard_sender_srpt_non_rr"]),
            "one_rtt_bypass_flows": int(stats["guard_one_rtt_bypass_flows"]),
            "tail_bypass_flows": int(stats["guard_tail_bypass_flows"]),
            "proactive_releases": int(stats["proactive_releases"]),
        },
        "performance_emitted": False,
    }


def analyze(spec_path: Path, campaign_dir: Path, scope: str) -> Mapping[str, object]:
    spec_path = spec_path.resolve()
    campaign_dir = campaign_dir.resolve()
    spec = read_spec(spec_path)
    preflight_path = campaign_dir / "preflight.json"
    if preflight_path.is_symlink():
        raise SummaryError("preflight.json must not be a symlink")
    preflight_raw = _read_json(preflight_path)
    repo = Path(str(preflight_raw.get("repo", "")))
    try:
        preflight = load_preflight(spec_path, spec, repo, campaign_dir)
    except CampaignError as exc:
        raise SummaryError(str(exc)) from exc
    gate = _load_gate(campaign_dir, preflight)
    stage_gate_sha = sha256_file(campaign_dir / "stage-gate.json")
    if scope == FRESH_SCOPE:
        try:
            _path, _replay, replay_sha = validate_sealed_replay(
                campaign_dir, gate, preflight)
        except CampaignError as exc:
            raise SummaryError(str(exc)) from exc
    else:
        if gate.get("replay_admission") is not None or gate.get(
                "compatibility_execution_armed") is not False:
            raise SummaryError("replay analysis requires the unarmed pre-seal stage gate")
        replay_sha = None
    manifests = discover_manifests(campaign_dir, spec, scope)
    runs = [validate_run(row, spec_path, spec, campaign_dir, preflight, scope)
            for row in manifests]
    return {
        "schema_version": 14, "scope": scope, "status": "admitted",
        "passed": True, "run_count": len(runs),
        "spec_sha256": sha256_file(spec_path),
        "preflight_sha256": sha256_file(campaign_dir / "preflight.json"),
        "stage_gate_sha256": stage_gate_sha,
        "simulator_git_sha": preflight["simulator_git_sha"],
        "replay_admission_sha256": replay_sha,
        "mechanism_admission_passed": True, "performance_emitted": False,
        "runs": runs,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--campaign-dir", type=Path, required=True)
    parser.add_argument("--scope", choices=(REPLAY_SCOPE, FRESH_SCOPE), required=True)
    parser.add_argument("--json-out", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = analyze(args.spec, args.campaign_dir, args.scope)
        atomic_json(args.json_out, result)
    except (SummaryError, CampaignError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"admitted {result['run_count']} {args.scope} mechanisms; performance sealed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
