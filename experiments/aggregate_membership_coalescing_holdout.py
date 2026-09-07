#!/usr/bin/env python3
"""Aggregate five paired coalescing seeds and execute frozen non-regression gates."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Dict, List, Mapping, Sequence, Tuple

try:
    from experiments.run_campaign import sha256_file, write_json
    from experiments.run_membership_coalescing_holdout import (
        CONTROL_ARM, holdout_axes, matrix, read_spec,
    )
    from experiments.summarize_campaign import SummaryError
except ModuleNotFoundError:  # Direct execution from experiments/.
    from run_campaign import sha256_file, write_json
    from run_membership_coalescing_holdout import (
        CONTROL_ARM, holdout_axes, matrix, read_spec,
    )
    from summarize_campaign import SummaryError


T95_DF4 = 2.7764451051977987
PERCENT_METRICS = (
    ("mean_fct_us", "mean_fct_percent_ci_upper"),
    ("completion_span_us", "completion_span_percent_ci_upper"),
    ("receiver_queue_time_sampled_mean_bytes", "receiver_queue_mean_percent_ci_upper"),
    ("receiver_queue_time_sampled_p99_bytes", "receiver_queue_p99_percent_ci_upper"),
)
JAIN_METRIC = ("flow_goodput_jain", "jain_difference_ci_lower")


def read_json(path: Path) -> Mapping[str, object]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise SummaryError(f"JSON root must be an object: {path}")
    return value


def paired_t95(values: Sequence[float]) -> Tuple[float, float, float, float]:
    if len(values) != 5 or not all(math.isfinite(value) for value in values):
        raise SummaryError("paired t95 requires exactly five finite observations")
    mean = statistics.fmean(values)
    half = T95_DF4 * statistics.stdev(values) / math.sqrt(5)
    return mean, mean - half, mean + half, half


def _validate_v10_initial(membership: Mapping[str, object],
                          evidence: Mapping[str, object], count: int,
                          seed: int) -> None:
    quiet_ns = 16640
    hard_ns = 83200
    quiet_flushes = int(membership.get("initial_collection_quiet_flushes", -1))
    hard_flushes = int(membership.get("initial_collection_hard_flushes", -1))
    first_delta = int(evidence.get("first_flush_minus_first_registration_ns", -1))
    last_delta = int(evidence.get("first_flush_minus_last_registration_ns", -1))
    common_failed = (
        int(membership.get("initial_collection_quiet_ns", -1)) != quiet_ns or
        int(membership.get("initial_collection_full_deadline", -1)) != 0 or
        int(membership.get("initial_collection_starts", -1)) != 1 or
        int(membership.get("initial_collection_flushes", -1)) != 1 or
        int(membership.get("initial_collection_deferred_changes", -1)) != count - 1 or
        int(membership.get("initial_collection_reschedules", -1)) != count - 1 or
        int(membership.get("initial_collection_cancellations", -1)) != 0 or
        quiet_flushes + hard_flushes != 1 or
        int(membership.get("initial_collection_wait_ns", -1)) != first_delta or
        int(membership.get("initial_collection_max_wait_ns", -1)) != first_delta or
        int(membership.get("max_batch", -1)) != count or
        int(evidence.get("initial_generation_send_timestamp_count", -1)) != 1 or
        int(evidence.get("ack_required_batches", -1)) != 1 or
        int(evidence.get("ack_required_grants", -1)) != count or
        int(evidence.get("last_registration_ns", -1)) >
            int(evidence.get("first_flush_ns", -1)) or
        not 0 < first_delta <= hard_ns or not 0 <= last_delta <= quiet_ns)
    quiet_failed = quiet_flushes == 1 and (
        hard_flushes != 0 or last_delta != quiet_ns or first_delta >= hard_ns)
    hard_failed = hard_flushes == 1 and (
        quiet_flushes != 0 or first_delta != hard_ns or last_delta > quiet_ns)
    if common_failed or quiet_failed or hard_failed:
        raise SummaryError(
            f"V10 sliding initial-collection evidence regressed: N={count} seed={seed}")


def validate_runs(spec: Mapping[str, object], analysis: Mapping[str, object]) -> Dict[Tuple[int, int, str], Mapping[str, object]]:
    pilot_digest = analysis.get("pilot_admission_sha256")
    replay_digest = analysis.get("known_replay_admission_sha256")
    if (analysis.get("schema_version") != spec.get("schema_version") or
            analysis.get("status") != "validated_full_matrix_performance_unsealed" or
            not analysis.get("mechanism_admission_passed") or
            not analysis.get("performance_unsealed") or
            int(analysis.get("run_count", -1)) != 40 or
            not isinstance(pilot_digest, str) or len(pilot_digest) != 64 or
            any(character not in "0123456789abcdef" for character in pilot_digest)):
        raise SummaryError("analysis is not an admitted 40-run matrix for this spec")
    if int(spec.get("schema_version", 0)) in (9, 10, 11, 12, 13) and (
            not isinstance(replay_digest, str) or len(replay_digest) != 64 or
            any(character not in "0123456789abcdef" for character in replay_digest)):
        raise SummaryError(
            f"V{spec['schema_version']} analysis lacks its sealed known-replay admission digest")
    runs = analysis.get("runs")
    if not isinstance(runs, list):
        raise SummaryError("analysis runs must be a list")
    found: Dict[Tuple[int, int, str], Mapping[str, object]] = {}
    for row in runs:
        if not isinstance(row, dict):
            raise SummaryError("analysis run row must be an object")
        identity = (int(row["seed"]), int(row["active_flows"]), str(row["controller"]))
        if identity in found:
            raise SummaryError(f"duplicate analysis run: {identity}")
        performance = row.get("performance")
        if not isinstance(performance, dict):
            raise SummaryError(f"run lacks performance metrics: {identity}")
        for metric, _threshold in PERCENT_METRICS:
            value = float(performance[metric])
            if not math.isfinite(value) or value < 0:
                raise SummaryError(f"invalid {metric}: {identity}")
        jain = float(performance[JAIN_METRIC[0]])
        if not math.isfinite(jain) or not 0 < jain <= 1:
            raise SummaryError(f"invalid Jain index: {identity}")
        if int(performance.get("receiver_queue_samples", 0)) <= 0:
            raise SummaryError(f"receiver queue sample count is zero: {identity}")
        found[identity] = row
    expected = set(matrix(spec))
    if set(found) != expected or len(found) != 40:
        raise SummaryError("analysis run identities differ from the frozen matrix")
    seeds, counts, arms = holdout_axes(spec)
    candidate = arms[1]
    for seed in seeds:
        for count in counts:
            control = found[(seed, count, CONTROL_ARM)]
            coalesced = found[(seed, count, candidate)]
            if control["flow_sha256"] != coalesced["flow_sha256"]:
                raise SummaryError(f"paired traffic hash mismatch: N={count} seed={seed}")
            evidence = coalesced["generation_ack"]
            if int(spec.get("schema_version", 0)) == 13 and (
                    control.get("transition_prefix_watchdog") is not None):
                raise SummaryError(
                    f"V13 control unexpectedly emitted watchdog evidence: "
                    f"N={count} seed={seed}")
            if int(spec.get("schema_version", 0)) in (11, 12, 13):
                safe = coalesced.get("safe_activation")
                if not isinstance(safe, dict):
                    raise SummaryError(
                        f"V{spec['schema_version']} candidate lacks safe-activation evidence: "
                        f"N={count} seed={seed}")
                bound = int(spec["candidate_admission"]["frame_bounds"][str(count)])
                terminal = tuple(map(str, spec["candidate_admission"][
                    "terminal_zero_fields"]))
                phase = evidence.get("phase_contract")
                required_phases = {
                    "fast_prepare", "fast_activate", "transition_prepare",
                    "transition_activate", "release",
                }
                if (not isinstance(phase, dict) or set(phase) != required_phases or
                        int(evidence["total_control_frames_sent"]) > bound or
                        int(evidence["total_control_frames_sent"]) !=
                            int(safe["control_frames"]) or
                        int(evidence["pending_generations"]) != 0 or
                        int(evidence["retry_grants"]) != 0 or
                        int(evidence["stale_events"]) != 0 or
                        evidence.get("barriers_closed") is not True or
                        evidence.get("transition_union_closed") is not True or
                        int(safe["enabled"]) != 1 or int(safe["limit"]) != 4 or
                        int(safe["wire_reconciled"]) != 1 or
                        int(safe["barrier_violations"]) != 0 or
                        int(safe["early_unlocks"]) != 0 or
                        int(safe["post_transition_fast_grants"]) != 0 or
                        int(safe["unattributed_grant_frames"]) != 0 or
                        int(safe["unattributed_ack_frames"]) != 0 or
                        int(safe["wire_grant_frames"]) +
                            int(safe["wire_ack_frames"]) != int(safe["control_frames"]) or
                        any(int(safe[field]) != 0 for field in terminal) or
                        int(coalesced["pfc"]["pause_events"]) != 0 or
                        int(coalesced["pfc"]["resume_events"]) != 0):
                    raise SummaryError(
                        f"V{spec['schema_version']} safe two-stage evidence regressed: "
                        f"N={count} seed={seed}")
                high_failed = (count > 4 and (
                    int(safe["high_transitions"]) != 1 or
                    int(safe["high_collection_flushes"]) !=
                        1 + int(safe.get("accepted_release_batches", 0)) or
                    int(safe["high_transition_registered_n"]) != count))
                low_failed = (count <= 4 and (
                    int(safe["high_transitions"]) != 0 or
                    int(safe["high_collection_flushes"]) != 0))
                if high_failed or low_failed:
                    raise SummaryError(
                        f"V{spec['schema_version']} high/low mode evidence regressed: "
                        f"N={count} seed={seed}")
                if int(spec.get("schema_version", 0)) in (12, 13):
                    prefix = coalesced.get("transition_prefix")
                    proof = evidence.get("transition_prefix")
                    if not isinstance(prefix, dict) or not isinstance(proof, dict):
                        raise SummaryError(
                            f"V{spec['schema_version']} candidate lacks prefix evidence: "
                            f"N={count} seed={seed}")
                    terminal_prefix = tuple(map(
                        str, spec["candidate_admission"][
                            "terminal_prefix_zero_fields"]))
                    common_failed = (
                        int(prefix["enabled"]) != 1 or
                        int(prefix["timeouts"]) != 0 or
                        int(prefix["degraded"]) != 0 or
                        int(prefix["remaining_bytes"]) != 0 or
                        int(prefix["fallback_batches"]) != 0 or
                        int(prefix["fallback_max_batch"]) != 0 or
                        int(prefix["fallback_closed_batches"]) != 0 or
                        int(prefix["order_violations"]) != 0 or
                        int(prefix["barrier_violations"]) != 0 or
                        any(int(prefix[field]) != 0 for field in terminal_prefix) or
                        proof.get("normal_path") is not True)
                    low_failed = count <= 4 and (
                        int(prefix["starts"]) != 0 or int(prefix["ready"]) != 0 or
                        proof.get("prefix_started") is not False)
                    high_failed = count > 4 and (
                        int(prefix["starts"]) != 1 or int(prefix["ready"]) != 1 or
                        int(prefix["waiters_required"]) <= 0 or
                        int(prefix["waiters_ready"]) !=
                            int(prefix["waiters_required"]) or
                        proof.get("prefix_started") is not True or
                        int(proof["activation_batch_index"]) != 1 or
                        int(proof["activation_batch_size"]) !=
                            int(prefix["waiters_required"]))
                    if common_failed or low_failed or high_failed:
                        raise SummaryError(
                            f"V{spec['schema_version']} exact-prefix evidence regressed: "
                            f"N={count} seed={seed}")
                if int(spec.get("schema_version", 0)) == 13:
                    watchdog = coalesced.get("transition_prefix_watchdog")
                    proof = evidence.get("transition_prefix_watchdog")
                    if not isinstance(watchdog, dict) or not isinstance(proof, dict):
                        raise SummaryError(
                            f"V13 candidate lacks watchdog evidence: N={count} seed={seed}")
                    high = count > 4
                    scalar_fields = (
                        "rounded_payload_budget_bytes", "packet_count",
                        "header_per_packet", "wire_bytes", "incumbent_count",
                        "occupancy_bps", "capacity_bps", "residual_bps",
                        "serialization_ns", "max_rtt_ns", "delay_ns",
                        "start_ns", "deadline_ns",
                    )
                    common_failed = (
                        int(watchdog["enabled"]) != 1 or
                        int(watchdog["records"]) != (1 if high else 0) or
                        int(watchdog["non_reconstructable"]) != 0 or
                        int(watchdog["inconsistent"]) != 0 or
                        int(watchdog["terminal_budget"]) != 0 or
                        proof.get("independently_reconstructed") is not True or
                        bool(proof.get("budget_started")) != high)
                    low_failed = not high and any(
                        int(watchdog[field]) != 0 for field in scalar_fields)
                    high_failed = high and (
                        any(int(watchdog[field]) != int(proof[field])
                            for field in scalar_fields) or
                        int(proof.get("records", -1)) != 1 or
                        not isinstance(proof.get("raw_target_bytes_by_flow"), dict) or
                        not isinstance(proof.get("base_rtt_ns_by_flow"), dict))
                    if common_failed or low_failed or high_failed:
                        raise SummaryError(
                            f"V13 watchdog reconstruction regressed: N={count} seed={seed}")
                continue
            frame_limit_violated = (
                int(evidence["total_control_frames_sent"]) >= 3 * count
                if int(spec.get("schema_version", 0)) >= 8 else
                int(evidence["total_control_frames_sent"]) > 3 * count)
            if (frame_limit_violated or
                    int(evidence["pending_generations"]) != 0 or
                    int(evidence["retry_grants"]) != 0 or
                    int(evidence["stale_events"]) != 0):
                raise SummaryError(f"candidate mechanism evidence regressed: N={count} seed={seed}")
            if int(spec.get("schema_version", 0)) >= 7:
                details = evidence.get("generation_contract")
                if not isinstance(details, list) or not details:
                    raise SummaryError(
                        f"candidate lacks selective-ACK generation evidence: N={count} seed={seed}")
                required = sum(bool(row.get("ack_required")) for row in details)
                optional = len(details) - required
                required_grants = sum(int(row["grant_frames_sent"]) for row in details
                                      if bool(row.get("ack_required")))
                optional_grants = sum(int(row["grant_frames_sent"]) for row in details
                                      if not bool(row.get("ack_required")))
                if (required != int(evidence["ack_required_batches"]) or
                        optional != int(evidence["ack_optional_batches"]) or
                        required_grants != int(evidence["ack_required_grants"]) or
                        optional_grants != int(evidence["ack_optional_grants"]) or
                        int(evidence["generation_zero_rejected"]) != 0 or
                        int(evidence["generation_mismatch_rejected"]) != 0 or
                        int(evidence["fully_acked_membership_batches"]) != required):
                    raise SummaryError(
                        f"selective-ACK batch evidence regressed: N={count} seed={seed}")
                for index, row in enumerate(details):
                    if bool(row["ack_required"]):
                        if (row.get("classification") != "join_or_decrease_ack_required" or
                                int(row["ack_frames_sent"]) != int(row["grant_frames_sent"])):
                            raise SummaryError(
                                f"ACK-required generation evidence regressed: N={count} seed={seed}")
                    elif (row.get("classification") !=
                          "pure_release_non_decreasing_ack_optional" or
                          int(row["ack_frames_sent"]) != 0):
                        raise SummaryError(
                            f"ACK-optional generation evidence regressed: N={count} seed={seed}")
                    if int(spec.get("schema_version", 0)) >= 8:
                        previous = row.get("previous_vector_active")
                        active = int(row["active_flows"])
                        if index == 0 and not bool(row["ack_required"]):
                            raise SummaryError(
                                f"initial V{spec['schema_version']} generation is optional: "
                                f"N={count} seed={seed}")
                        if (not bool(row["ack_required"]) and
                                (previous is None or active > int(previous) // 2)):
                            raise SummaryError(
                                f"V{spec['schema_version']} optional release violates "
                                "geometric bound: "
                                f"N={count} seed={seed}")
                if (int(spec.get("schema_version", 0)) >= 8 and
                        int(evidence.get("release_threshold_deferrals", -1)) < 0):
                    raise SummaryError(
                        f"V{spec['schema_version']} release deferral evidence is missing: "
                        f"N={count} seed={seed}")
                if int(spec.get("schema_version", 0)) >= 8:
                    first = details[0]
                    if (int(evidence.get("initial_generation_flows", -1)) != count or
                            int(evidence.get("post_initial_registration_events", -1)) != 0 or
                            int(first.get("active_flows", -1)) != count or
                            int(first.get("joins", -1)) != count or
                            not bool(first.get("ack_required")) or
                            any(bool(row.get("ack_required")) or
                                int(row.get("joins", -1)) != 0
                                for row in details[1:])):
                        raise SummaryError(
                            f"V{spec['schema_version']} initial/all-release lifecycle "
                            "evidence regressed: "
                            f"N={count} seed={seed}")
                if int(spec.get("schema_version", 0)) == 9:
                    membership = dict(coalesced["membership"])
                    wait_ns = int(spec["initial_collection_protocol"]["derived_wait_ns"])
                    if (int(membership.get("initial_collection_starts", -1)) != 1 or
                            int(membership.get("initial_collection_flushes", -1)) != 1 or
                            int(membership.get("initial_collection_cancellations", -1)) != 0 or
                            int(membership.get("initial_collection_wait_ns", -1)) != wait_ns or
                            int(membership.get("initial_collection_max_wait_ns", -1)) != wait_ns or
                            int(membership.get("max_batch", -1)) != count or
                            int(evidence.get("initial_generation_send_timestamp_count", -1)) != 1 or
                            int(evidence.get("first_flush_minus_first_registration_ns", -1)) !=
                                wait_ns or
                            int(evidence.get("last_registration_ns", -1)) >
                                int(evidence.get("first_flush_ns", -1)) or
                            int(evidence.get("ack_required_batches", -1)) != 1 or
                            int(evidence.get("ack_required_grants", -1)) != count):
                        raise SummaryError(
                            f"V9 fixed initial-collection evidence regressed: "
                            f"N={count} seed={seed}")
                elif int(spec.get("schema_version", 0)) == 10:
                    _validate_v10_initial(
                        dict(coalesced["membership"]), evidence, count, seed)
    return found


def aggregate(spec_path: Path, analysis_path: Path) -> Tuple[List[Dict[str, object]], List[Dict[str, object]], Dict[str, object]]:
    spec = read_spec(spec_path)
    analysis = read_json(analysis_path)
    if analysis.get("spec_sha256") != sha256_file(spec_path):
        raise SummaryError("analysis/spec hash mismatch")
    runs = validate_runs(spec, analysis)
    replay_digest = analysis.get("known_replay_admission_sha256")
    seeds, counts, arms = holdout_axes(spec)
    candidate = arms[1]
    thresholds = dict(spec["scalability_holdout"]["paired_non_regression"])
    schema_version = int(spec["schema_version"])
    per_seed: List[Dict[str, object]] = []
    deltas: Dict[Tuple[int, str], List[float]] = {}
    absolute_deltas: Dict[Tuple[int, str], List[float]] = {}
    for count in counts:
        for seed in seeds:
            control = runs[(seed, count, CONTROL_ARM)]
            coalesced = runs[(seed, count, candidate)]
            left = control["performance"]
            right = coalesced["performance"]
            row: Dict[str, object] = {
                "active_flows": count, "seed": seed,
                "flow_sha256": control["flow_sha256"],
                "control": CONTROL_ARM, "candidate": candidate,
                "control_total_control_frames":
                    control["generation_ack"]["total_control_frames_sent"],
                "candidate_total_control_frames": coalesced["generation_ack"]["total_control_frames_sent"],
            }
            control_frames = int(row["control_total_control_frames"])
            candidate_frames = int(row["candidate_total_control_frames"])
            row["candidate_to_control_frames_ratio"] = (
                candidate_frames / control_frames if control_frames > 0 else None)
            for field in (
                    "pause_events", "resume_events", "matched_intervals",
                    "cumulative_pause_ns", "max_pause_ns"):
                row[f"control_pfc_{field}"] = control["pfc"][field]
                row[f"candidate_pfc_{field}"] = coalesced["pfc"][field]
            for metric, _threshold in PERCENT_METRICS:
                baseline = float(left[metric])
                value = float(right[metric])
                if baseline == 0:
                    if value != 0 and not (schema_version in (11, 12, 13) and count in (2, 4, 8) and
                                           metric.startswith("receiver_queue_")):
                        raise SummaryError(
                            f"zero denominator with positive candidate for {metric}: "
                            f"N={count} seed={seed}")
                    # An empty queue in both paired runs is exact equality, not
                    # an undefined regression hidden behind an epsilon.
                    change = 0.0 if value == 0 else None
                else:
                    change = (value - baseline) / baseline * 100.0
                row[f"control_{metric}"] = baseline
                row[f"candidate_{metric}"] = value
                row[f"paired_{metric}_percent"] = change
                if change is not None:
                    deltas.setdefault((count, metric), []).append(change)
                if metric.startswith("receiver_queue_"):
                    difference = value - baseline
                    row[f"paired_{metric}_absolute_bytes"] = difference
                    absolute_deltas.setdefault((count, metric), []).append(difference)
            jain_metric = JAIN_METRIC[0]
            jain_difference = float(right[jain_metric]) - float(left[jain_metric])
            row[f"control_{jain_metric}"] = float(left[jain_metric])
            row[f"candidate_{jain_metric}"] = float(right[jain_metric])
            row["paired_flow_goodput_jain_difference"] = jain_difference
            deltas.setdefault((count, jain_metric), []).append(jain_difference)
            per_seed.append(row)
    intervals: List[Dict[str, object]] = []
    gates: List[Dict[str, object]] = []
    for count in counts:
        for metric, threshold_name in PERCENT_METRICS:
            if schema_version in (11, 12, 13) and metric.startswith("receiver_queue_"):
                percent_values = deltas.get((count, metric), [])
                if len(percent_values) == 5:
                    mean, low, high, half = paired_t95(percent_values)
                    interval = {
                        "active_flows": count, "metric": metric,
                        "effect": "paired_candidate_minus_control_percent",
                        "n": 5, "mean": mean, "ci95_low": low,
                        "ci95_high": high, "ci95_half_width": half,
                    }
                else:
                    interval = {
                        "active_flows": count, "metric": metric,
                        "effect": "paired_candidate_minus_control_percent",
                        "n": len(percent_values), "estimable": False,
                        "reason": "zero_control_queue_denominator",
                    }
                if count in (2, 4, 8):
                    interval.update({
                        "threshold_name": "reported_only_percent_not_admission",
                        "threshold": None, "gate_bound": "none", "passed": None,
                    })
                    intervals.append(interval)
                    correction = dict(spec["scalability_holdout"][
                        "small_queue_measurement_correction"])
                    absolute_values = absolute_deltas[(count, metric)]
                    abs_mean, abs_low, abs_high, abs_half = paired_t95(absolute_values)
                    limit_name = ("mean_absolute_bytes_ci_upper" if metric.endswith(
                        "mean_bytes") else "p99_absolute_bytes_ci_upper")
                    limit = float(correction[limit_name])
                    passed = abs_high <= limit
                    intervals.append({
                        "active_flows": count, "metric": metric,
                        "effect": "paired_candidate_minus_control_absolute_bytes",
                        "n": 5, "mean": abs_mean, "ci95_low": abs_low,
                        "ci95_high": abs_high, "ci95_half_width": abs_half,
                        "threshold_name": limit_name, "threshold": limit,
                        "gate_bound": "upper", "passed": passed,
                    })
                    gates.append({
                        "active_flows": count, "metric": metric,
                        "effect": "absolute_bytes", "bound": "upper",
                        "observed": abs_high, "threshold": limit, "passed": passed,
                    })
                    continue
                if len(percent_values) != 5:
                    raise SummaryError(f"N=15 percent queue effect is not estimable: {metric}")
                advantage = dict(spec["scalability_holdout"][
                    "n15_advantage_preservation"])
                limit_name = ("receiver_queue_mean_percent_ci_upper" if metric.endswith(
                    "mean_bytes") else "receiver_queue_p99_percent_ci_upper")
                limit = float(advantage[limit_name])
                passed = float(interval["ci95_high"]) <= limit
                interval.update({
                    "threshold_name": limit_name, "threshold": limit,
                    "gate_bound": "upper", "passed": passed,
                })
                intervals.append(interval)
                gates.append({
                    "active_flows": count, "metric": metric,
                    "effect": "percent", "bound": "upper",
                    "observed": interval["ci95_high"], "threshold": limit,
                    "passed": passed,
                })
                continue
            values = deltas[(count, metric)]
            mean, low, high, half = paired_t95(values)
            limit = float(thresholds[threshold_name])
            passed = high <= limit
            intervals.append({
                "active_flows": count, "metric": metric,
                "effect": "paired_candidate_minus_control_percent",
                "n": 5, "mean": mean, "ci95_low": low, "ci95_high": high,
                "ci95_half_width": half, "threshold_name": threshold_name,
                "threshold": limit, "gate_bound": "upper", "passed": passed,
            })
            gates.append({
                "active_flows": count, "metric": metric,
                "bound": "upper", "observed": high, "threshold": limit,
                "passed": passed,
            })
        metric, threshold_name = JAIN_METRIC
        mean, low, high, half = paired_t95(deltas[(count, metric)])
        limit = float(thresholds[threshold_name])
        passed = low >= limit
        intervals.append({
            "active_flows": count, "metric": metric,
            "effect": "paired_candidate_minus_control_difference",
            "n": 5, "mean": mean, "ci95_low": low, "ci95_high": high,
            "ci95_half_width": half, "threshold_name": threshold_name,
            "threshold": limit, "gate_bound": "lower", "passed": passed,
        })
        gates.append({
            "active_flows": count, "metric": metric,
            "bound": "lower", "observed": low, "threshold": limit,
            "passed": passed,
        })
    if schema_version in (11, 12, 13):
        advantage = dict(spec["scalability_holdout"]["n15_advantage_preservation"])
        n15_rows = [row for row in per_seed if int(row["active_flows"]) == 15]
        frame_max = max(int(row["candidate_total_control_frames"]) for row in n15_rows)
        ratios = [row["candidate_to_control_frames_ratio"] for row in n15_rows]
        if any(value is None for value in ratios):
            raise SummaryError("N=15 control-frame ratio has a zero control denominator")
        ratio_max = max(float(value) for value in ratios)
        candidate_pfc_max = max(
            max(int(row["candidate_pfc_pause_events"]),
                int(row["candidate_pfc_resume_events"])) for row in n15_rows)
        for metric, observed, threshold in (
                ("candidate_control_frames", frame_max,
                 float(advantage["candidate_control_frames_max"])),
                ("candidate_to_control_frames_ratio", ratio_max,
                 float(advantage["candidate_to_control_frames_ratio_max"])),
                ("candidate_pfc_events", candidate_pfc_max,
                 float(advantage["candidate_pfc_events_max"]))):
            passed = observed <= threshold
            gates.append({
                "active_flows": 15, "metric": metric, "bound": "upper",
                "observed": observed, "threshold": threshold, "passed": passed,
            })
        if schema_version in (12, 13):
            stability = dict(spec["scalability_holdout"]["n15_per_seed_stability"])
            if len(n15_rows) != int(stability["required_seed_count"]):
                raise SummaryError(
                    f"V{schema_version} N=15 stability set is not exactly five seeds")
            for row in n15_rows:
                seed = int(row["seed"])
                for metric, threshold_name in (
                        ("receiver_queue_time_sampled_mean_bytes",
                         "receiver_queue_mean_percent_max"),
                        ("receiver_queue_time_sampled_p99_bytes",
                         "receiver_queue_p99_percent_max")):
                    observed = row.get(f"paired_{metric}_percent")
                    if observed is None or not math.isfinite(float(observed)):
                        raise SummaryError(
                            f"V{schema_version} N=15 per-seed stability is not estimable: "
                            f"seed={seed} metric={metric}")
                    threshold = float(stability[threshold_name])
                    passed_seed = float(observed) <= threshold
                    gates.append({
                        "active_flows": 15, "seed": seed, "metric": metric,
                        "effect": "per_seed_percent", "bound": "upper",
                        "observed": float(observed), "threshold": threshold,
                        "passed": passed_seed,
                    })
    passed = all(bool(gate["passed"]) for gate in gates)
    admission = {
        "schema_version": int(spec["schema_version"]),
        "status": "admitted" if passed else "rejected_non_regression",
        "passed": passed, "paired_seed_count": 5,
        "student_t_critical_95_df4": T95_DF4,
        "spec_sha256": sha256_file(spec_path),
        "analysis_sha256": sha256_file(analysis_path),
        "pilot_admission_sha256": analysis["pilot_admission_sha256"],
        "known_replay_admission_sha256": replay_digest,
        "mechanism_admission_revalidated": True,
        "small_queue_measurement_correction": (
            spec["scalability_holdout"].get("small_queue_measurement_correction")
            if schema_version in (11, 12, 13) else None),
        "n15_per_seed_stability": (
            spec["scalability_holdout"].get("n15_per_seed_stability")
            if schema_version in (12, 13) else None),
        "gates": gates,
    }
    return per_seed, intervals, admission


def write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    if not rows:
        raise SummaryError(f"refusing to write empty CSV: {path}")
    fields: List[str] = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("analysis", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        per_seed, intervals, admission = aggregate(
            args.spec.resolve(), args.analysis.resolve())
        output = args.output_dir.resolve()
        output.mkdir(parents=True, exist_ok=True)
        write_csv(output / "per_seed.csv", per_seed)
        write_csv(output / "paired_t95.csv", intervals)
        write_json(output / "admission.json", admission)
    except (OSError, ValueError, KeyError, TypeError, SummaryError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"paired V{admission['schema_version']} admission: {admission['status']}")
    return 0 if admission["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
