#!/usr/bin/env python3
"""Freeze and serially execute a membership-coalescing holdout.

The runner owns only input generation and simulation execution.  It never
parses or emits FCT or queue metrics; those remain sealed for the analyzer
until all 40 preregistered runs are complete and pass mechanism checks.

Execution is fail-closed: the frozen spec must explicitly require its
generation-ACK policy and the exact frozen pilot must produce a
machine-validated admission artifact before any of the 40 formal runs can
start. Aggregate data progress is not delivery evidence.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time
from typing import Dict, Iterable, List, Mapping, MutableMapping, Sequence, Tuple

try:
    from experiments.run_campaign import (
        CampaignError, campaign_storage, directory_size,
        git_revision, output_directories, sha256_file, write_json,
    )
except ModuleNotFoundError:  # Direct execution from experiments/.
    from run_campaign import (
        CampaignError, campaign_storage, directory_size,
        git_revision, output_directories, sha256_file, write_json,
    )


CONTROL_ARM = "control"
HOSTS = 16
BASE_TIME_S = 2.0
REQUIRED_ACK_EVIDENCE = (
    "grant_generation", "grant_ack_sent", "grant_ack_received",
    "pending_generations", "retries", "fully_acked_membership_batches",
)
V7_REQUIRED_ACK_EVIDENCE = (
    "grant_generation", "ack_required", "grant_ack_sent", "grant_ack_received",
    "pending_generations", "retries", "ack_required_batches",
    "ack_optional_batches", "ack_required_grants", "ack_optional_grants",
    "generation_zero_rejected", "generation_mismatch_rejected",
    "fully_acked_membership_batches",
)
V8_REQUIRED_ACK_EVIDENCE = (
    "grant_generation", "ack_required", "previous_vector_active", "active_flows",
    "release_threshold_deferrals", "initial_generation_flows",
    "post_initial_registration_events", "grant_ack_sent", "grant_ack_received",
    "pending_generations", "retries", "ack_required_batches",
    "ack_optional_batches", "ack_required_grants", "ack_optional_grants",
    "generation_zero_rejected", "generation_mismatch_rejected",
    "fully_acked_membership_batches",
)
V9_REQUIRED_ACK_EVIDENCE = (
    "initial_collection_starts", "initial_collection_flushes",
    "initial_collection_cancellations", "initial_collection_wait_ns",
    "initial_collection_max_wait_ns", "first_registration_ns",
    "last_registration_ns", "first_flush_ns", "registration_span_ns",
    "max_registration_gap_ns", "initial_generation_flows",
    "initial_generation_send_timestamp_count", "post_initial_registration_events",
    "grant_generation", "ack_required", "previous_vector_active", "active_flows",
    "release_threshold_deferrals", "grant_ack_sent", "grant_ack_received",
    "pending_generations", "retries", "fully_acked_membership_batches",
)
V10_REQUIRED_ACK_EVIDENCE = (
    "initial_collection_full_deadline", "initial_collection_starts",
    "initial_collection_flushes", "initial_collection_deferred_changes",
    "initial_collection_reschedules", "initial_collection_cancellations",
    "initial_collection_quiet_flushes", "initial_collection_hard_flushes",
    "initial_collection_wait_ns", "initial_collection_max_wait_ns",
    "first_registration_ns", "last_registration_ns", "first_flush_ns",
    "registration_span_ns", "max_registration_gap_ns",
    "initial_generation_flows", "initial_generation_send_timestamp_count",
    "post_initial_registration_events", "grant_generation", "ack_required",
    "previous_vector_active", "active_flows", "release_threshold_deferrals",
    "grant_ack_sent", "grant_ack_received", "pending_generations", "retries",
    "fully_acked_membership_batches",
)
V11_REQUIRED_ACK_EVIDENCE = (
    "transaction_id", "grant_phase", "membership_target_n", "subject_role",
    "accepted_fast_prepare_batches", "accepted_fast_activate_batches",
    "accepted_transition_prepare_batches",
    "accepted_transition_activate_batches", "accepted_release_batches",
    "prefix_close_ns", "collection_flush_ns", "transition_prepare_start_ns",
    "wire_grant_frames", "wire_ack_frames", "wire_reconciled",
    "terminal_phase", "terminal_pending_acks", "terminal_pending_membership",
)
V12_REQUIRED_ACK_EVIDENCE = V11_REQUIRED_ACK_EVIDENCE + (
    "prefix_target_bytes", "prefix_observed_bytes", "drain_outcome",
    "activation_batch_index", "activation_batch_size",
    "guard_transition_prefix",
)
V13_REQUIRED_ACK_EVIDENCE = V12_REQUIRED_ACK_EVIDENCE + (
    "guard_transition_prefix_watchdog",
)

# Every behavior-affecting GUARD option is explicit so defaults cannot drift.
GUARD_OPTIONS: Mapping[str, object] = {
    "guard_lambda": 1.0,
    "guard_beta": 0.125,
    "guard_gamma": 1.0,
    "guard_selective_registration": 1,
    "guard_proactive_release": 0,
    "guard_keep_last_hop_int": 0,
    "guard_size_priority": 0,
    "guard_sender_srpt": 0,
    "guard_one_rtt_bypass": 1,
    "guard_tail_bypass": 0,
    "guard_tail_bypass_bdps": 8.0,
    "guard_tail_congestion_gate": 0,
    "guard_tail_safe_ratio": 0.9,
    "guard_tail_safe_samples": 2,
    "guard_adaptive_fabric_target": 0,
    "guard_target_floor": 0.95,
    "guard_queue_budget_bdps": 0.5,
    "guard_adaptive_target_max_bdps": 0.0,
    "guard_ack_interval_packets": 8,
    "guard_fixed_window": 1,
    "guard_remaining_aware": 0,
    "guard_min_share_fraction": 0.0,
    "guard_remaining_exponent": 0.0,
    "guard_receiver_concurrency": 0,
    "guard_concurrency_min_bdps": 8.0,
    "guard_grant_refresh_bdps": 0.0,
    "guard_srpt_quantum_packets": 64,
    "guard_work_conserving": 0,
    "guard_cap_aware_reclaim": 0,
    "guard_cap_headroom": 1.1,
    "guard_cap_min_share_fraction": 0.25,
    "guard_rebalance_interval_us": 200,
    "guard_demand_threshold": 0.75,
    "guard_receiver_util_threshold": 0.75,
}


def _load_spec(path: Path) -> Mapping[str, object]:
    with path.open(encoding="utf-8") as stream:
        spec = json.load(stream)
    if not isinstance(spec, dict):
        raise CampaignError("membership-coalescing formal spec root must be an object")
    return spec


def _read_v6_spec(spec: Mapping[str, object]) -> Mapping[str, object]:
    if spec.get("schema_version") != 6:
        raise CampaignError("membership-coalescing V6 spec must use schema version 6")
    pilot = dict(spec.get("candidate_pilot", {}))
    holdout = dict(spec.get("scalability_holdout", {}))
    limits = dict(spec.get("resource_limits", {}))
    controllers = tuple(map(str, holdout.get("controllers", ())))
    if controllers != (CONTROL_ARM, "membership_coalescing_v6"):
        raise CampaignError("v6 requires control and membership_coalescing_v6")
    coalesced_arm = controllers[1]
    expected_pilot = {
        "topology": "leaf_spine_16_100G_OS4", "receiver": 15,
        "flow_bytes": 8388608, "priority_group": 4,
        "arrival_jitter_us": 1.0, "controllers": list(controllers),
        "membership_max_windows": 5,
        "guard_fixed_window": 1, "guard_selective_registration": 1,
        "guard_proactive_release": 0, "guard_size_priority": 0,
        "guard_remaining_aware": 0, "guard_remaining_exponent": 0.0,
        "guard_grant_refresh_bdps": 0.0,
        "first_grant_confirmation_frontier": "explicit_generation_ack",
        "pfc": 1, "irn": 0,
        "simul_time_s": 0.02, "analysis_warmup_s": 0.0,
        "monitor_profile": "bulk",
    }
    for field, expected in expected_pilot.items():
        if pilot.get(field) != expected:
            raise CampaignError(f"frozen candidate_pilot.{field} changed")
    if int(pilot.get("seed", -1)) != 95 or int(pilot.get("active_flows", -1)) != 15:
        raise CampaignError("v6 candidate must freeze seed 95 at N=15")
    quiet = dict(pilot.get("membership_quiet_ns", {}))
    retry = dict(pilot.get("grant_reliability_service_rounds", {}))
    if (set(quiet) != set(controllers) or int(quiet[CONTROL_ARM]) != 0 or
            int(quiet[coalesced_arm]) <= 0):
        raise CampaignError("candidate must freeze a positive coalescing window against control=0")
    if (set(retry) != set(controllers) or float(retry[CONTROL_ARM]) != 0.0 or
            float(retry[coalesced_arm]) < 1.0):
        raise CampaignError("candidate must freeze service-round retry against control=0")
    seeds = tuple(map(int, holdout.get("seeds", ())))
    counts = tuple(map(int, holdout.get("active_flow_counts", ())))
    if seeds != (96, 97, 98, 99, 100):
        raise CampaignError("v6 holdout seeds must be (96, 97, 98, 99, 100)")
    if counts != (2, 4, 8, 15):
        raise CampaignError("holdout flow counts must be (2, 4, 8, 15)")
    expected_admission = {
        "completed_flows": 15, "switch_drops_max": 0,
        "recovery_events_max": 0, "timeout_recoveries_max": 0,
        "pfc_events_max": 0, "grant_trace_truncated_max": 0,
        "grants_sent_must_equal_received": True,
        "grant_acks_sent_must_equal_received": True,
        "membership_batches_must_equal_fully_acked": True,
        "pending_grant_acks_max": 0, "normal_path_retry_grants_max": 0,
        "stale_grants_max": 0, "stale_grant_acks_max": 0,
        "final_c_over_n_error_bps_max": 1000000,
        "coalesced_control_frames_max": 45, "coalesced_max_batch_min": 12,
        "first_grant_gated_flows": 15, "first_grant_gate_releases": 15,
    }
    if spec.get("candidate_admission") != expected_admission:
        raise CampaignError("frozen candidate_admission changed")
    expected_non_regression = {
        "mean_fct_percent_ci_upper": 1.0,
        "completion_span_percent_ci_upper": 1.0,
        "receiver_queue_mean_percent_ci_upper": 5.0,
        "receiver_queue_p99_percent_ci_upper": 5.0,
        "jain_difference_ci_lower": -0.005,
    }
    if (holdout.get("required_control_frame_envelope") !=
            "F_s(N) = grant sends + grant-ACK sends <= 3N for every coalesced seed and N" or
            holdout.get("coalesced_pfc_events_max") != 0 or
            holdout.get("paired_non_regression") != expected_non_regression):
        raise CampaignError("frozen holdout mechanism/performance gates changed")
    expected_limits = {
        "max_flows": 1000, "grant_trace_max_lines": 4096,
        "lifecycle_trace_max_lines": 32,
        "artifact_bytes_per_run": 16777216,
        "wall_time_seconds_per_run": 1800,
        "rss_kib_per_run": 8388608, "campaign_bytes": 7516192768,
    }
    if limits != expected_limits:
        raise CampaignError("frozen resource_limits changed")
    protocol = dict(spec.get("generation_ack_protocol", {}))
    minimum = protocol.get("minimum_simulator_commit")
    if not isinstance(minimum, str) or len(minimum) != 40:
        raise CampaignError("v6 must freeze a full generation-ACK simulator commit")
    if not execution_armed(spec):
        raise CampaignError("v6 holdout_execution contract is not armed")
    return spec


def _read_v7_spec(spec: Mapping[str, object]) -> Mapping[str, object]:
    pilot = dict(spec.get("candidate_pilot", {}))
    holdout = dict(spec.get("scalability_holdout", {}))
    controllers = tuple(map(str, holdout.get("controllers", ())))
    if controllers != (CONTROL_ARM, "membership_coalescing_v7"):
        raise CampaignError("v7 requires control and membership_coalescing_v7")
    expected_pilot = {
        "seed": 101, "topology": "leaf_spine_16_100G_OS4", "receiver": 15,
        "active_flows": 15, "flow_bytes": 8388608, "priority_group": 4,
        "arrival_jitter_us": 1.0, "controllers": list(controllers),
        "membership_quiet_ns": {CONTROL_ARM: 0, controllers[1]: 12480},
        "membership_max_windows": 5,
        "grant_reliability_service_rounds": {CONTROL_ARM: 0.0, controllers[1]: 2.0},
        "guard_fixed_window": 1, "guard_selective_registration": 1,
        "guard_proactive_release": 0, "guard_size_priority": 0,
        "guard_remaining_aware": 0, "guard_remaining_exponent": 0.0,
        "guard_grant_refresh_bdps": 0.0,
        "first_grant_confirmation_frontier": "selective_generation_ack",
        "pfc": 1, "irn": 0, "simul_time_s": 0.02,
        "analysis_warmup_s": 0.0, "monitor_profile": "bulk",
    }
    if pilot != expected_pilot:
        raise CampaignError("frozen V7 candidate_pilot changed")
    if (tuple(map(int, holdout.get("seeds", ()))) != (102, 103, 104, 105, 106) or
            tuple(map(int, holdout.get("active_flow_counts", ()))) != (2, 4, 8, 15)):
        raise CampaignError("v7 holdout must freeze seeds 102--106 and N={2,4,8,15}")
    expected_admission = {
        "completed_flows": 15, "switch_drops_max": 0,
        "recovery_events_max": 0, "timeout_recoveries_max": 0,
        "pfc_events_max": 0, "grant_trace_truncated_max": 0,
        "grants_sent_must_equal_received": True,
        "ack_required_generations_must_fully_close": True,
        "ack_optional_generations_must_have_zero_ack_frames": True,
        "ack_required_grants_must_match_trace": True,
        "ack_optional_grants_must_match_trace": True,
        "ack_required_generations_min": 1, "ack_optional_generations_min": 1,
        "pending_grant_acks_max": 0, "normal_path_retry_grants_max": 0,
        "stale_grants_max": 0, "stale_grant_acks_max": 0,
        "generation_zero_rejected_max": 0,
        "generation_mismatch_rejected_max": 0,
        "final_c_over_n_error_bps_max": 1000000,
        "coalesced_control_frames_max": 45, "coalesced_max_batch_min": 12,
        "first_grant_gated_flows": 15, "first_grant_gate_releases": 15,
    }
    if spec.get("candidate_admission") != expected_admission:
        raise CampaignError("frozen V7 candidate_admission changed")
    expected_non_regression = {
        "mean_fct_percent_ci_upper": 1.0,
        "completion_span_percent_ci_upper": 1.0,
        "receiver_queue_mean_percent_ci_upper": 5.0,
        "receiver_queue_p99_percent_ci_upper": 5.0,
        "jain_difference_ci_lower": -0.005,
    }
    if (holdout.get("required_control_frame_envelope") !=
            "F_s(N) = grant sends + grant-ACK sends <= 3N for every coalesced seed and N" or
            holdout.get("coalesced_pfc_events_max") != 0 or
            holdout.get("paired_non_regression") != expected_non_regression):
        raise CampaignError("frozen V7 holdout gates changed")
    expected_limits = {
        "max_flows": 1000, "grant_trace_max_lines": 4096,
        "lifecycle_trace_max_lines": 32, "artifact_bytes_per_run": 16777216,
        "wall_time_seconds_per_run": 1800, "rss_kib_per_run": 8388608,
        "campaign_bytes": 7516192768,
    }
    if spec.get("resource_limits") != expected_limits:
        raise CampaignError("frozen V7 resource_limits changed")
    protocol = spec.get("generation_ack_protocol")
    expected_protocol = {
        "minimum_simulator_commit": "6c9c88441b602ab2824785d8c5de73d90f2c6a52",
        "policy": "selective_membership_generation_ack_v1",
        "grant_identity": "receiver-local membership generation plus flow ID",
        "ack_required_rule": "a generation requires ACK closure when any flow is first granted or its encoded target is below its conservative acknowledged upper bound",
        "ack_elision_rule": "an otherwise optional generation, including a pure-release generation with non-decreasing continuing grants, sets ack_required=0 and emits no ACK",
        "retry_semantics": "retry only unacknowledged flows in the current ACK-required generation",
        "terminal_condition": "every ACK-required generation is fully acknowledged; every ACK-elided generation has zero pending/retry state",
    }
    if protocol != expected_protocol:
        raise CampaignError("frozen V7 selective-ACK protocol changed")
    probe = spec.get("simulator_capability_probe")
    if probe != {
            "status_at_base_commit": "present",
            "source": "scratch/network-load-balance.cc",
            "required_tokens": ["generation,pending_acks,ack_required",
                                "ack_required_batches", "ack_optional_batches",
                                "ack_required_grants", "ack_optional_grants",
                                "generation_zero_rejected",
                                "generation_mismatch_rejected"]}:
        raise CampaignError("frozen V7 simulator capability probe changed")
    if not execution_armed(spec):
        raise CampaignError("v7 holdout_execution contract is not armed")
    return spec


def _read_v8_spec(spec: Mapping[str, object]) -> Mapping[str, object]:
    pilot = dict(spec.get("candidate_pilot", {}))
    holdout = dict(spec.get("scalability_holdout", {}))
    controllers = tuple(map(str, holdout.get("controllers", ())))
    if controllers != (CONTROL_ARM, "membership_coalescing_v8"):
        raise CampaignError("v8 requires control and membership_coalescing_v8")
    expected_pilot = {
        "seed": 107, "topology": "leaf_spine_16_100G_OS4", "receiver": 15,
        "active_flows": 15, "flow_bytes": 8388608, "priority_group": 4,
        "arrival_jitter_us": 1.0, "controllers": list(controllers),
        "membership_quiet_ns": {CONTROL_ARM: 0, controllers[1]: 12480},
        "membership_max_windows": 5,
        "grant_reliability_service_rounds": {CONTROL_ARM: 0.0, controllers[1]: 2.0},
        "guard_fixed_window": 1, "guard_selective_registration": 1,
        "guard_proactive_release": 0, "guard_size_priority": 0,
        "guard_remaining_aware": 0, "guard_remaining_exponent": 0.0,
        "guard_grant_refresh_bdps": 0.0,
        "first_grant_confirmation_frontier": "selective_generation_ack",
        "pfc": 1, "irn": 0, "simul_time_s": 0.02,
        "analysis_warmup_s": 0.0, "monitor_profile": "bulk",
    }
    if pilot != expected_pilot:
        raise CampaignError("frozen V8 candidate_pilot changed")
    if (tuple(map(int, holdout.get("seeds", ()))) != (108, 109, 110, 111, 112) or
            tuple(map(int, holdout.get("active_flow_counts", ()))) != (2, 4, 8, 15)):
        raise CampaignError("v8 holdout must freeze seeds 108--112 and N={2,4,8,15}")
    expected_admission = {
        "completed_flows": 15, "switch_drops_max": 0,
        "recovery_events_max": 0, "timeout_recoveries_max": 0,
        "pfc_events_max": 0, "grant_trace_truncated_max": 0,
        "grants_sent_must_equal_received": True,
        "initial_generation_must_require_ack": True,
        "initial_generation_must_include_all_registered_flows": True,
        "subsequent_generations_must_be_optional_release_only": True,
        "post_initial_registration_events_max": 0,
        "ack_required_generations_must_fully_close": True,
        "ack_optional_generations_must_have_zero_ack_frames": True,
        "ack_required_grants_must_match_trace": True,
        "ack_optional_grants_must_match_trace": True,
        "optional_release_active_must_be_at_most_floor_previous_vector_active_over_two": True,
        "ack_required_generations_min": 1, "ack_optional_generations_min": 1,
        "release_threshold_deferrals_min": 1,
        "pending_grant_acks_max": 0, "normal_path_retry_grants_max": 0,
        "stale_grants_max": 0, "stale_grant_acks_max": 0,
        "generation_zero_rejected_max": 0,
        "generation_mismatch_rejected_max": 0,
        "final_c_over_n_error_bps_max": 1000000,
        "coalesced_control_frames_max": 44, "coalesced_max_batch_min": 12,
        "first_grant_gated_flows": 15, "first_grant_gate_releases": 15,
    }
    if spec.get("candidate_admission") != expected_admission:
        raise CampaignError("frozen V8 candidate_admission changed")
    if (holdout.get("required_control_frame_envelope") !=
            "F_s(N) = grant sends + grant-ACK sends < 3N for every coalesced seed and N" or
            holdout.get("coalesced_pfc_events_max") != 0 or
            holdout.get("paired_non_regression") != {
                "mean_fct_percent_ci_upper": 1.0,
                "completion_span_percent_ci_upper": 1.0,
                "receiver_queue_mean_percent_ci_upper": 5.0,
                "receiver_queue_p99_percent_ci_upper": 5.0,
                "jain_difference_ci_lower": -0.005}):
        raise CampaignError("frozen V8 holdout gates changed")
    if spec.get("resource_limits") != {
            "max_flows": 1000, "grant_trace_max_lines": 4096,
            "lifecycle_trace_max_lines": 32, "artifact_bytes_per_run": 16777216,
            "wall_time_seconds_per_run": 1800, "rss_kib_per_run": 8388608,
            "campaign_bytes": 7516192768}:
        raise CampaignError("frozen V8 resource_limits changed")
    if spec.get("generation_ack_protocol") != {
            "minimum_simulator_commit": "51cabdd8b61d45c0c43455665212d6f23443bb32",
            "policy": "selective_membership_generation_ack_with_geometric_release_v1",
            "grant_identity": "receiver-local membership generation plus flow ID",
            "ack_required_rule": "a generation requires ACK closure when any flow is first granted or its encoded target is below its conservative acknowledged upper bound",
            "ack_optional_rule": "an otherwise optional pure-release generation sets ack_required=0 and emits no ACK",
            "geometric_release_rule": "an optional pure-release generation may emit only when its active count is at most floor(the previous emitted vector active count divided by two)",
            "retry_semantics": "retry only unacknowledged flows in the current ACK-required generation",
            "terminal_condition": "every ACK-required generation is fully acknowledged; every ACK-optional generation has zero pending/retry state"}:
        raise CampaignError("frozen V8 geometric selective-ACK protocol changed")
    if spec.get("simulator_capability_probe") != {
            "status_at_base_commit": "present", "source": "scratch/network-load-balance.cc",
            "required_tokens": ["generation,pending_acks,ack_required",
                                "release_threshold_deferrals",
                                "GUARD membership coalescing currently requires",
                                "GUARD_PROACTIVE_RELEASE 0", "ack_required_batches",
                                "ack_optional_batches", "ack_required_grants",
                                "ack_optional_grants", "generation_zero_rejected",
                                "generation_mismatch_rejected"]}:
        raise CampaignError("frozen V8 simulator capability probe changed")
    if not execution_armed(spec):
        raise CampaignError("v8 holdout_execution contract is not armed")
    return spec


def _read_v9_spec(spec: Mapping[str, object]) -> Mapping[str, object]:
    pilot = dict(spec.get("candidate_pilot", {}))
    holdout = dict(spec.get("scalability_holdout", {}))
    controllers = tuple(map(str, holdout.get("controllers", ())))
    candidate = "membership_coalescing_v9"
    if controllers != (CONTROL_ARM, candidate):
        raise CampaignError("v9 requires control and membership_coalescing_v9")
    expected_pilot = {
        "seed": 113, "topology": "leaf_spine_16_100G_OS4", "receiver": 15,
        "active_flows": 15, "flow_bytes": 8388608, "priority_group": 4,
        "arrival_jitter_us": 1.0, "controllers": list(controllers),
        "membership_quiet_ns": {CONTROL_ARM: 0, candidate: 12480},
        "membership_max_windows": 5, "initial_collection_wait_ns": 62400,
        "grant_reliability_service_rounds": {CONTROL_ARM: 0.0, candidate: 2.0},
        "guard_fixed_window": 1, "guard_selective_registration": 1,
        "guard_proactive_release": 0, "guard_size_priority": 0,
        "guard_remaining_aware": 0, "guard_remaining_exponent": 0.0,
        "guard_grant_refresh_bdps": 0.0,
        "first_grant_confirmation_frontier":
            "fixed_initial_collection_then_selective_generation_ack",
        "pfc": 1, "irn": 0, "simul_time_s": 0.02,
        "analysis_warmup_s": 0.0, "monitor_profile": "bulk",
    }
    if pilot != expected_pilot:
        raise CampaignError("frozen V9 candidate_pilot changed")
    if (tuple(map(int, holdout.get("seeds", ()))) != (114, 115, 116, 117, 118) or
            tuple(map(int, holdout.get("active_flow_counts", ()))) != (2, 4, 8, 15)):
        raise CampaignError("v9 holdout must freeze seeds 114--118 and N={2,4,8,15}")
    if spec.get("known_failure_replay") != {
            "seed": 108, "active_flows": 15, "controller": candidate,
            "flow_sha256":
                "1993661ded923372651ee25a4d9e5e2fb18cad0b0c5182edb758e71d80abc7a9",
            "v8_observed_initial_generation_flows": 13,
            "v8_late_registration_gaps_ns": [13050, 13490],
            "performance_must_remain_sealed": True,
            "admission_artifact": "known_replay_admission_v9.json"}:
        raise CampaignError("frozen V9 known-failure replay changed")
    if spec.get("initial_collection_protocol") != {
            "scope": "the first emitted vector in each receiver-local empty-to-nonempty membership epoch",
            "quiet_ns": 12480, "max_windows": 5, "derived_wait_ns": 62400,
            "timer_rule": "schedule exactly once at first_registration_ns + derived_wait_ns; intervening changes accumulate without cancelling or rescheduling it",
            "flush_rule": "the first grant send timestamp equals the fixed collection deadline and every registered flow is included in that generation",
            "post_initial_rule": "after the first vector, retain the V8 sliding quiet timer, hard deadline, selective ACK, and geometric completion-release behavior",
            "empty_epoch_rule": "returning to an empty registered set resets only the epoch marker; membership generations remain monotonic"}:
        raise CampaignError("frozen V9 initial-collection protocol changed")
    if spec.get("candidate_admission") != {
            "completed_flows": 15, "switch_drops_max": 0,
            "recovery_events_max": 0, "timeout_recoveries_max": 0,
            "pfc_events_max": 0, "grant_trace_truncated_max": 0,
            "initial_collection_starts": 1, "initial_collection_flushes": 1,
            "initial_collection_cancellations_max": 0,
            "initial_collection_wait_ns": 62400,
            "initial_collection_max_wait_ns": 62400,
            "first_flush_minus_first_registration_ns": 62400,
            "last_registration_must_not_follow_first_flush": True,
            "initial_generation_must_share_one_send_timestamp": True,
            "initial_generation_must_include_all_registered_flows": True,
            "initial_generation_must_require_ack": True,
            "subsequent_generations_must_be_optional_release_only": True,
            "post_initial_registration_events_max": 0,
            "ack_required_batches": 1, "ack_required_grants": 15,
            "ack_optional_generations_min": 1,
            "release_threshold_deferrals_min": 1,
            "pending_grant_acks_max": 0, "normal_path_retry_grants_max": 0,
            "stale_grants_max": 0, "stale_grant_acks_max": 0,
            "generation_zero_rejected_max": 0,
            "generation_mismatch_rejected_max": 0,
            "final_c_over_n_error_bps_max": 1000000,
            "coalesced_control_frames_max": 44, "coalesced_max_batch": 15,
            "first_grant_gated_flows": 15, "first_grant_gate_releases": 15}:
        raise CampaignError("frozen V9 candidate_admission changed")
    if (holdout.get("required_control_frame_envelope") !=
            "F_s(N) = grant sends + grant-ACK sends < 3N for every coalesced seed and N" or
            holdout.get("coalesced_pfc_events_max") != 0 or
            holdout.get("paired_non_regression") != {
                "mean_fct_percent_ci_upper": 1.0,
                "completion_span_percent_ci_upper": 1.0,
                "receiver_queue_mean_percent_ci_upper": 5.0,
                "receiver_queue_p99_percent_ci_upper": 5.0,
                "jain_difference_ci_lower": -0.005}):
        raise CampaignError("frozen V9 holdout gates changed")
    if spec.get("resource_limits") != {
            "max_flows": 1000, "grant_trace_max_lines": 4096,
            "lifecycle_trace_max_lines": 32, "artifact_bytes_per_run": 16777216,
            "wall_time_seconds_per_run": 1800, "rss_kib_per_run": 8388608,
            "campaign_bytes": 1073741824}:
        raise CampaignError("frozen V9 resource_limits changed")
    if spec.get("generation_ack_protocol") != {
            "minimum_simulator_commit":
                "92c93e2917242b1cb17c25971f50bf9f9df641d8",
            "policy":
                "fixed_initial_collection_with_selective_ack_and_geometric_release_v1",
            "grant_identity": "receiver-local membership generation plus flow ID",
            "ack_required_rule": "the initial generation requires ACK closure; a later generation would require ACK closure if any continuing target decreased",
            "ack_optional_rule": "every admitted post-initial generation is a pure completion-release generation with non-decreasing continuing grants, ack_required=0, and no ACK",
            "geometric_release_rule": "a post-initial release generation may emit only when its active count is at most floor(the previous emitted vector active count divided by two)",
            "retry_semantics": "retry only unacknowledged flows in the current ACK-required generation",
            "terminal_condition": "the initial generation is fully acknowledged; every later generation has zero pending/retry state"}:
        raise CampaignError("frozen V9 protocol/minimum simulator changed")
    expected_probe_tokens = [
        "generation,pending_acks,ack_required", "release_threshold_deferrals",
        "initial_collection_starts", "initial_collection_flushes",
        "initial_collection_deferred_changes", "initial_collection_cancellations",
        "initial_collection_wait_ns", "initial_collection_max_wait_ns",
        "ack_required_batches", "ack_optional_batches", "ack_required_grants",
        "ack_optional_grants", "generation_zero_rejected",
        "generation_mismatch_rejected",
    ]
    if spec.get("simulator_capability_probe") != {
            "status_at_base_commit": "present",
            "source": "scratch/network-load-balance.cc",
            "required_tokens": expected_probe_tokens}:
        raise CampaignError("frozen V9 simulator capability probe changed")
    if not execution_armed(spec):
        raise CampaignError("v9 replay/pilot/holdout execution contract is not armed")
    return spec


def _read_v10_spec(spec: Mapping[str, object]) -> Mapping[str, object]:
    pilot = dict(spec.get("candidate_pilot", {}))
    holdout = dict(spec.get("scalability_holdout", {}))
    controllers = tuple(map(str, holdout.get("controllers", ())))
    candidate = "membership_coalescing_v10"
    if controllers != (CONTROL_ARM, candidate):
        raise CampaignError("v10 requires control and membership_coalescing_v10")
    expected_pilot = {
        "seed": 119, "topology": "leaf_spine_16_100G_OS4", "receiver": 15,
        "active_flows": 15, "flow_bytes": 8388608, "priority_group": 4,
        "arrival_jitter_us": 1.0, "controllers": list(controllers),
        "membership_quiet_ns": {CONTROL_ARM: 0, candidate: 12480},
        "membership_max_windows": 5,
        "initial_collection_full_deadline": {CONTROL_ARM: 1, candidate: 0},
        "initial_collection_quiet_ns": {CONTROL_ARM: 0, candidate: 16640},
        "initial_collection_hard_ns": 83200,
        "grant_reliability_service_rounds": {CONTROL_ARM: 0.0, candidate: 2.0},
        "guard_fixed_window": 1, "guard_selective_registration": 1,
        "guard_proactive_release": 0, "guard_size_priority": 0,
        "guard_remaining_aware": 0, "guard_remaining_exponent": 0.0,
        "guard_grant_refresh_bdps": 0.0,
        "first_grant_confirmation_frontier":
            "sliding_initial_collection_then_selective_generation_ack",
        "pfc": 1, "irn": 0, "simul_time_s": 0.02,
        "analysis_warmup_s": 0.0, "monitor_profile": "bulk",
    }
    if pilot != expected_pilot:
        raise CampaignError("frozen V10 candidate_pilot changed")
    if (tuple(map(int, holdout.get("seeds", ()))) != (120, 121, 122, 123, 124) or
            tuple(map(int, holdout.get("active_flow_counts", ()))) != (2, 4, 8, 15)):
        raise CampaignError("v10 holdout must freeze seeds 120--124 and N={2,4,8,15}")
    if spec.get("known_failure_replay") != {
            "seed": 108, "active_flows": 15, "controller": candidate,
            "flow_sha256":
                "1993661ded923372651ee25a4d9e5e2fb18cad0b0c5182edb758e71d80abc7a9",
            "v8_observed_initial_generation_flows": 13,
            "v8_late_registration_gaps_ns": [13050, 13490],
            "performance_must_remain_sealed": True,
            "admission_artifact": "known_replay_admission_v10.json"}:
        raise CampaignError("frozen V10 known-failure replay changed")
    if spec.get("initial_collection_protocol") != {
            "scope": "the first emitted vector in each receiver-local empty-to-nonempty membership epoch",
            "mode": "sliding_quiet_with_hard_deadline",
            "base_cross_leaf_rtt_ns": 8320, "quiet_multiplier": 2,
            "quiet_ns": 16640, "hard_max_windows": 5,
            "derived_hard_ns": 83200,
            "timer_rule": "schedule at min(last_registration_ns + quiet_ns, first_registration_ns + derived_hard_ns); every later initial registration reschedules only the quiet target",
            "hard_tie_rule": "a quiet target equal to the hard deadline is classified as a hard flush",
            "flush_rule": "a non-hard flush occurs exactly quiet_ns after the last registration; a hard flush occurs exactly derived_hard_ns after the first registration",
            "post_initial_rule": "after the first vector, retain the V8 12480-ns sliding quiet timer, five-window hard deadline, selective ACK, and geometric completion-release behavior",
            "empty_epoch_rule": "returning to an empty registered set resets only the epoch marker; membership generations remain monotonic"}:
        raise CampaignError("frozen V10 initial-collection protocol changed")
    if spec.get("candidate_admission") != {
            "completed_flows": 15, "switch_drops_max": 0,
            "recovery_events_max": 0, "timeout_recoveries_max": 0,
            "pfc_events_max": 0, "grant_trace_truncated_max": 0,
            "initial_collection_full_deadline": 0,
            "initial_collection_starts": 1, "initial_collection_flushes": 1,
            "initial_collection_deferred_changes": 14,
            "initial_collection_reschedules": 14,
            "initial_collection_cancellations_max": 0,
            "initial_collection_flush_reason_count": 1,
            "first_flush_minus_first_registration_ns_max": 83200,
            "first_flush_minus_last_registration_ns_max": 16640,
            "quiet_flush_minus_last_registration_ns": 16640,
            "hard_flush_minus_first_registration_ns": 83200,
            "last_registration_must_not_follow_first_flush": True,
            "initial_generation_must_share_one_send_timestamp": True,
            "initial_generation_must_include_all_registered_flows": True,
            "initial_generation_must_require_ack": True,
            "subsequent_generations_must_be_optional_release_only": True,
            "post_initial_registration_events_max": 0,
            "ack_required_batches": 1, "ack_required_grants": 15,
            "ack_optional_generations_min": 1,
            "release_threshold_deferrals_min": 1,
            "pending_grant_acks_max": 0, "normal_path_retry_grants_max": 0,
            "stale_grants_max": 0, "stale_grant_acks_max": 0,
            "generation_zero_rejected_max": 0,
            "generation_mismatch_rejected_max": 0,
            "final_c_over_n_error_bps_max": 1000000,
            "coalesced_control_frames_max": 44, "coalesced_max_batch": 15,
            "first_grant_gated_flows": 15, "first_grant_gate_releases": 15}:
        raise CampaignError("frozen V10 candidate_admission changed")
    if (holdout.get("required_control_frame_envelope") !=
            "F_s(N) = grant sends + grant-ACK sends < 3N for every coalesced seed and N" or
            holdout.get("coalesced_pfc_events_max") != 0 or
            holdout.get("paired_non_regression") != {
                "mean_fct_percent_ci_upper": 1.0,
                "completion_span_percent_ci_upper": 1.0,
                "receiver_queue_mean_percent_ci_upper": 5.0,
                "receiver_queue_p99_percent_ci_upper": 5.0,
                "jain_difference_ci_lower": -0.005}):
        raise CampaignError("frozen V10 holdout gates changed")
    if spec.get("resource_limits") != {
            "max_flows": 1000, "grant_trace_max_lines": 4096,
            "lifecycle_trace_max_lines": 32, "artifact_bytes_per_run": 16777216,
            "wall_time_seconds_per_run": 1800, "rss_kib_per_run": 8388608,
            "campaign_bytes": 1073741824}:
        raise CampaignError("frozen V10 resource_limits changed")
    if spec.get("generation_ack_protocol") != {
            "minimum_simulator_commit":
                "dc252d964a64617065a74ec9dc9d31a4b5339b21",
            "policy":
                "sliding_initial_collection_with_selective_ack_and_geometric_release_v1",
            "grant_identity": "receiver-local membership generation plus flow ID",
            "ack_required_rule": "the initial generation requires ACK closure; a later generation would require ACK closure if any continuing target decreased",
            "ack_optional_rule": "every admitted post-initial generation is a pure completion-release generation with non-decreasing continuing grants, ack_required=0, and no ACK",
            "geometric_release_rule": "a post-initial release generation may emit only when its active count is at most floor(the previous emitted vector active count divided by two)",
            "retry_semantics": "retry only unacknowledged flows in the current ACK-required generation",
            "terminal_condition": "the initial generation is fully acknowledged; every later generation has zero pending/retry state"}:
        raise CampaignError("frozen V10 protocol/minimum simulator changed")
    expected_probe_tokens = [
        "generation,pending_acks,ack_required", "release_threshold_deferrals",
        "initial_collection_full_deadline", "initial_collection_quiet_ns",
        "initial_collection_starts", "initial_collection_flushes",
        "initial_collection_deferred_changes", "initial_collection_reschedules",
        "initial_collection_cancellations", "initial_collection_quiet_flushes",
        "initial_collection_hard_flushes", "initial_collection_wait_ns",
        "initial_collection_max_wait_ns", "ack_required_batches",
        "ack_optional_batches", "ack_required_grants", "ack_optional_grants",
        "generation_zero_rejected", "generation_mismatch_rejected",
    ]
    if spec.get("simulator_capability_probe") != {
            "status_at_base_commit": "present",
            "source": "scratch/network-load-balance.cc",
            "required_tokens": expected_probe_tokens}:
        raise CampaignError("frozen V10 simulator capability probe changed")
    if not execution_armed(spec):
        raise CampaignError("v10 replay/pilot/holdout execution contract is not armed")
    return spec


def _read_v11_spec(spec: Mapping[str, object]) -> Mapping[str, object]:
    """Validate the frozen V11 identity and fail closed on gate drift."""
    candidate = "membership_coalescing_v11"
    pilot = dict(spec.get("candidate_pilot", {}))
    holdout = dict(spec.get("scalability_holdout", {}))
    controllers = tuple(map(str, holdout.get("controllers", ())))
    if controllers != (CONTROL_ARM, candidate):
        raise CampaignError("v11 requires control and membership_coalescing_v11")
    expected_pilot = {
        "seed": 125, "topology": "leaf_spine_16_100G_OS4", "receiver": 15,
        "active_flow_counts": [4, 15], "flow_bytes": 8388608,
        "priority_group": 4, "arrival_jitter_us": 1.0,
        "controllers": list(controllers),
        "small_set_fastpath_limit": {CONTROL_ARM: 0, candidate: 4},
        "membership_quiet_ns": {CONTROL_ARM: 0, candidate: 12480},
        "membership_max_windows": 5,
        "initial_collection_full_deadline": {CONTROL_ARM: 1, candidate: 0},
        "initial_collection_quiet_ns": {CONTROL_ARM: 0, candidate: 16640},
        "initial_collection_hard_ns": 83200,
        "grant_reliability_service_rounds": {CONTROL_ARM: 0.0, candidate: 2.0},
        "guard_fixed_window": 1, "guard_selective_registration": 1,
        "guard_proactive_release": 0, "guard_size_priority": 0,
        "guard_remaining_aware": 0, "guard_remaining_exponent": 0.0,
        "guard_grant_refresh_bdps": 0.0,
        "first_grant_confirmation_frontier":
            "phase_tagged_prepare_ack_barrier_then_activation_ack",
        "pfc": 1, "irn": 0, "simul_time_s": 0.02,
        "analysis_warmup_s": 0.0, "monitor_profile": "bulk",
    }
    if pilot != expected_pilot:
        raise CampaignError("frozen V11 candidate_pilot changed")
    if (tuple(map(int, holdout.get("seeds", ()))) != (126, 127, 128, 129, 130) or
            tuple(map(int, holdout.get("active_flow_counts", ()))) != (2, 4, 8, 15)):
        raise CampaignError("v11 holdout must freeze seeds 126--130 and N={2,4,8,15}")
    replay = dict(spec.get("known_failure_replay", {}))
    expected_replay = (
        (120, 2, "57a6835cbd93248b6331ce4f018751ba8dbca389f8cfa292e250901cced306e9"),
        (120, 4, "fbbcd5ec93970aa02f8935eca9572b315bb979ec5208ec589c706051c7579ea0"),
        (120, 8, "75bcbf0353fd46d53d283363b5a6677081aa1f06f60f6b61fd45f5826eb3c86e"),
        (120, 15, "77cd632be5757954ca2c24b161d7ac7e699b30abbbd801ebaae6dfa7ff5fb882"),
    )
    observed_replay = tuple(
        (int(row.get("seed", -1)), int(row.get("active_flows", -1)),
         str(row.get("flow_sha256", "")))
        for row in replay.get("identities", ()))
    if (replay.get("controller") != candidate or observed_replay != expected_replay or
            replay.get("performance_must_remain_sealed") is not True or
            replay.get("admission_artifact") != "known_replay_admission_v11.json"):
        raise CampaignError("frozen V11 replay identities changed")
    admission = dict(spec.get("candidate_admission", {}))
    if (admission.get("frame_bounds") != {"2": 7, "4": 26, "8": 43, "15": 64} or
            admission.get("small_set_fastpath_limit") != 4 or
            admission.get("required_phases_must_close_in_order") is not True or
            admission.get("transition_prepare_activate_union_must_equal_lifecycle") is not True or
            admission.get("release_phases_must_be_optional") is not True or
            admission.get("post_transition_release_phases_must_be_geometric") is not True):
        raise CampaignError("frozen V11 phase/frame admission changed")
    correction = dict(holdout.get("small_queue_measurement_correction", {}))
    advantage = dict(holdout.get("n15_advantage_preservation", {}))
    if (correction.get("status") !=
            "first_preregistered_for_v11_not_retroactive_to_v10" or
            correction.get("active_flow_counts") != [2, 4, 8] or
            correction.get("mean_absolute_bytes_ci_upper") != 60.0 or
            correction.get("p99_absolute_bytes_ci_upper") != 60.0 or
            advantage != {
                "receiver_queue_mean_percent_ci_upper": -10.0,
                "receiver_queue_p99_percent_ci_upper": -20.0,
                "candidate_pfc_events_max": 0,
                "candidate_control_frames_max": 64,
                "candidate_to_control_frames_ratio_max": 0.30}):
        raise CampaignError("frozen V11 performance gates changed")
    if spec.get("resource_limits") != {
            "max_flows": 1000, "grant_trace_max_lines": 4096,
            "lifecycle_trace_max_lines": 32, "artifact_bytes_per_run": 16777216,
            "wall_time_seconds_per_run": 1800, "rss_kib_per_run": 8388608,
            "campaign_bytes": 1073741824}:
        raise CampaignError("frozen V11 resource_limits changed")
    protocol = dict(spec.get("safe_activation_protocol", {}))
    if (protocol.get("minimum_simulator_commit") !=
            "946cbbaa418d71580f747e10ca164fc7e041538f" or
            protocol.get("fast_join_limit") != 4 or
            protocol.get("policy") !=
            "serialized_prepare_then_activate_with_high_fan_in_collection_v1"):
        raise CampaignError("frozen V11 safe-activation protocol changed")
    if not execution_armed(spec):
        raise CampaignError("v11 replay/pilot/holdout execution contract is not armed")
    return spec


def _read_v12_spec(spec: Mapping[str, object]) -> Mapping[str, object]:
    """Validate every V12 behavior, identity, resource, and admission gate."""
    candidate = "membership_coalescing_v12"
    if spec.get("supersedes") != "membership_coalescing_ladder_v11.json":
        raise CampaignError("frozen V12 predecessor changed")
    pilot = dict(spec.get("candidate_pilot", {}))
    expected_pilot = {
        "seed": 131, "topology": "leaf_spine_16_100G_OS4", "receiver": 15,
        "active_flow_counts": [4, 15], "flow_bytes": 8388608,
        "priority_group": 4, "arrival_jitter_us": 1.0,
        "controllers": [CONTROL_ARM, candidate],
        "small_set_fastpath_limit": {CONTROL_ARM: 0, candidate: 4},
        "transition_prefix_barrier": {CONTROL_ARM: 0, candidate: 1},
        "membership_quiet_ns": {CONTROL_ARM: 0, candidate: 12480},
        "membership_max_windows": 5,
        "initial_collection_full_deadline": {CONTROL_ARM: 1, candidate: 0},
        "initial_collection_quiet_ns": {CONTROL_ARM: 0, candidate: 16640},
        "initial_collection_hard_ns": 83200,
        "grant_reliability_service_rounds": {CONTROL_ARM: 0.0, candidate: 2.0},
        "guard_fixed_window": 1, "guard_selective_registration": 1,
        "guard_proactive_release": 0, "guard_size_priority": 0,
        "guard_remaining_aware": 0, "guard_remaining_exponent": 0.0,
        "guard_grant_refresh_bdps": 0.0,
        "first_grant_confirmation_frontier":
            "phase_tagged_prepare_ack_barrier_then_activation_ack",
        "pfc": 1, "irn": 0, "simul_time_s": 0.02,
        "analysis_warmup_s": 0.0, "monitor_profile": "bulk",
    }
    if pilot != expected_pilot:
        raise CampaignError("frozen V12 candidate_pilot changed")
    holdout = dict(spec.get("scalability_holdout", {}))
    if (holdout.get("seeds") != [132, 133, 134, 135, 136] or
            holdout.get("active_flow_counts") != [2, 4, 8, 15] or
            holdout.get("controllers") != [CONTROL_ARM, candidate] or
            int(holdout.get("coalesced_pfc_events_max", -1)) != 0 or
            holdout.get("paired_non_regression") != {
                "mean_fct_percent_ci_upper": 1.0,
                "completion_span_percent_ci_upper": 1.0,
                "jain_difference_ci_lower": -0.005} or
            holdout.get("small_queue_measurement_correction") != {
                "status": "retained_from_v11_without_relaxation",
                "active_flow_counts": [2, 4, 8],
                "mean_absolute_bytes_ci_upper": 60.0,
                "p99_absolute_bytes_ci_upper": 60.0,
                "percent_metrics_reported_but_not_admission_gates": True} or
            holdout.get("n15_advantage_preservation") != {
                "receiver_queue_mean_percent_ci_upper": -10.0,
                "receiver_queue_p99_percent_ci_upper": -20.0,
                "candidate_pfc_events_max": 0,
                "candidate_control_frames_max": 64,
                "candidate_to_control_frames_ratio_max": 0.30} or
            holdout.get("n15_per_seed_stability") != {
                "receiver_queue_mean_percent_max": -10.0,
                "receiver_queue_p99_percent_max": -20.0,
                "required_seed_count": 5}):
        raise CampaignError("frozen V12 holdout identity/performance gates changed")
    if (set(holdout) != {
            "seeds", "active_flow_counts", "controllers",
            "required_control_frame_envelope", "coalesced_pfc_events_max",
            "paired_non_regression", "small_queue_measurement_correction",
            "n15_advantage_preservation", "n15_per_seed_stability"} or
            holdout.get("required_control_frame_envelope") !=
                "F<=7,26,43,64 at N=2,4,8,15 respectively; the low-N values safely include immediate per-completion optional release, and the high-N values are frozen event-pattern ceilings rather than a universal asymptotic claim"):
        raise CampaignError("frozen V12 holdout envelope changed")
    replay = dict(spec.get("known_failure_replay", {}))
    expected_replay = (
        (126, 2, "55cb59f42c008a37d0e24555fbdc33f1bec478e2816e2ba3f79eccdde9e971a2"),
        (126, 4, "87a41f2df3f9a2e8d70498c0db29764ddc7fb53a96f38ae13022d6c627f2949b"),
        (126, 8, "4d98144057c964d54299c5005883de6e13d01bb3491602b6a0b13d87e4b496f4"),
        (126, 15, "430550e35b0d5c53c07ac1da5a408a6b31c24b71e257b9cdffcfbc252c67437f"),
    )
    observed_replay = tuple(
        (int(row.get("seed", -1)), int(row.get("active_flows", -1)),
         str(row.get("flow_sha256", "")))
        for row in replay.get("identities", ()))
    if (replay.get("controller") != candidate or
            replay.get("source") != "V11 seed-126 formal identities" or
            replay.get("performance_must_remain_sealed") is not True or
            replay.get("admission_artifact") !=
                "known_replay_admission_v12.json" or
            observed_replay != expected_replay):
        raise CampaignError("frozen V12 replay identities changed")
    gate = dict(spec.get("candidate_admission", {}))
    exact_gate_fields = {
        "completed_flows_must_equal_n": True,
        "switch_drops_max": 0, "recovery_events_max": 0,
        "timeout_recoveries_max": 0, "pfc_events_max": 0,
        "grant_trace_truncated_max": 0, "small_set_fastpath_enabled": 1,
        "small_set_fastpath_limit": 4,
        "phase_tags": ["fast_prepare", "fast_activate", "transition_prepare",
                       "transition_activate", "release"],
        "barrier_violations_max": 0, "early_unlocks_max": 0,
        "post_transition_fast_grants_max": 0,
        "unattributed_grant_frames_max": 0, "unattributed_ack_frames_max": 0,
        "wire_reconciled": 1, "low_n_counts": [2, 4],
        "high_n_counts": [8, 15],
        "frame_bounds": {"2": 7, "4": 26, "8": 43, "15": 64},
        "low_n_requires_no_transition_or_collection": True,
        "high_n_requires_one_transition_and_collection": True,
        "required_phases_must_close_in_order": True,
        "transition_prepare_activate_union_must_equal_lifecycle": True,
        "release_phases_must_be_optional": True,
        "post_transition_release_phases_must_be_geometric": True,
        "pending_grant_acks_max": 0, "normal_path_retry_grants_max": 0,
        "stale_grants_max": 0, "stale_grant_acks_max": 0,
        "generation_zero_rejected_max": 0,
        "generation_mismatch_rejected_max": 0,
        "final_c_over_n_error_bps_max": 1000000,
        "first_grant_gated_flows_must_equal_n": True,
        "first_grant_gate_releases_must_equal_n": True,
        "same_receiver_nic_priority_group_provenance_required": True,
        "prefix_barrier_enabled": 1, "low_n_prefix_starts": 0,
        "low_n_prefix_ready": 0, "high_n_prefix_starts": 1,
        "high_n_prefix_ready": 1, "prefix_timeouts_max": 0,
        "prefix_degraded_max": 0, "prefix_remaining_bytes_max": 0,
        "fallback_batches_max": 0, "fallback_closed_batches_max": 0,
        "fallback_order_violations_max": 0,
        "prefix_barrier_violations_max": 0,
        "high_n_waiters_ready_must_equal_required": True,
        "normal_transition_activation_batch_index": 1,
        "normal_transition_activation_single_generation": True,
    }
    if any(gate.get(key) != value for key, value in exact_gate_fields.items()):
        raise CampaignError("frozen V12 mechanism admission changed")
    if gate.get("terminal_zero_fields") != [
            "terminal_phase", "terminal_pending_acks",
            "terminal_pending_membership", "terminal_waiters",
            "terminal_transaction_waiters", "terminal_ready_waiters",
            "terminal_collection_ready", "terminal_initial_flushed",
            "terminal_transition_committed", "terminal_revision",
            "terminal_consumed_revision"] or gate.get(
                "terminal_prefix_zero_fields") != [
                    "terminal_timer", "terminal_waiting", "terminal_resolved",
                    "terminal_timed_out", "terminal_fallback", "terminal_cursor",
                    "terminal_cohort", "terminal_future_queue",
                    "terminal_current_batch", "terminal_targets", "terminal_holds"]:
        raise CampaignError("frozen V12 terminal admission changed")
    if (gate.get("low_n_frame_bound_derivation") !=
            "serialized safe admission is at most N(N+1) frames and immediate per-completion optional release is at most N(N-1)/2 grant frames, for total F <= (3N^2+N)/2" or
            set(gate) != set(exact_gate_fields) | {
                "terminal_zero_fields", "terminal_prefix_zero_fields",
                "low_n_frame_bound_derivation"}):
        raise CampaignError("frozen V12 mechanism gate set changed")
    protocol = dict(spec.get("safe_activation_protocol", {}))
    prefix = dict(spec.get("transition_prefix_protocol", {}))
    if protocol != {
            "minimum_simulator_commit":
                "e5a57fa399c8ee4b5d3190d172771343baace632",
            "policy": "serialized_prepare_then_exact_prefix_activate_v1",
            "fast_join_limit": 4,
            "join_order": "ascending register_ns with flow_id as the tie breaker",
            "fast_join_rule": "for each admitted small-set join, fully ACK every required incumbent prepare grant before sending the required newcomer activation grant",
            "high_transition_rule": "the fifth registration irreversibly enters high-fan-in collection; transition prepare begins only after both collection flush and serialized small-set closure, then activation waits for the exact receiver-observed prefix of every frozen waiter",
            "transition_union_rule": "transition prepare incumbent flow IDs and transition activation waiter flow IDs are disjoint and their union is every registered lifecycle flow",
            "release_rule": "after safe activation closure, every admitted release generation is ACK-optional, contains no join, and never decreases a continuing target; low-mode release is optional and immediate, while post-transition high-mode release obeys the geometric active-count rule",
            "retry_semantics": "wire frame accounting includes every retry and duplicate ACK; the frozen normal path admits neither",
            "terminal_condition": "phase, pending ACKs, pending membership, waiter snapshots, collection state, revision delta, prefix timer, activation cohort, batch cursor, target map, and held references are all zero"}:
        raise CampaignError("frozen V12 safe-activation protocol changed")
    if prefix != {
            "enabled": 1,
            "scope": "the single N>4 high-fan-in transition activation",
            "cohort_provenance": "every incumbent and waiter belongs to the same receiver NIC, receiver capacity, and priority group; the frozen workload uses receiver 15 and priority group 4",
            "target_rule": "for each frozen live waiter, target_bytes=min(flow_size_bytes,sender_exact_qp_window_bytes)",
            "ready_rule": "every frozen live waiter has receiver_next_expected_seq greater than or equal to target_bytes",
            "normal_activation_rule": "after exact-prefix readiness, send one required transition_activate generation to the complete live waiter cohort in ascending register_ns and flow_id order",
            "normal_activation_evidence": "each receiver-authoritative transition_activate sent row has prefix_observed_bytes>=prefix_target_bytes, drain_outcome=ready, activation_batch_index=1, activation_batch_size equal to the actual waiter cohort, and the same nonzero generation and transaction target",
            "fallback_policy": "a nominal liveness timeout may use ordered ACK-clocked microbatches of at most four, but any timeout, degraded transition, fallback batch, fallback closure, order violation, or barrier violation rejects the campaign",
            "known_replay_n15_outcome": "ready",
            "low_n_rule": "N in {2,4} must not start, resolve, timeout, or retain any prefix-barrier state"}:
        raise CampaignError("frozen V12 prefix protocol/minimum simulator changed")
    if spec.get("initial_collection_protocol") != {
            "scope": "the first high-fan-in waiter snapshot after the fifth registration",
            "mode": "sliding_quiet_with_hard_deadline",
            "base_cross_leaf_rtt_ns": 8320, "quiet_multiplier": 2,
            "quiet_ns": 16640, "hard_max_windows": 5,
            "derived_hard_ns": 83200,
            "timer_rule": "schedule at min(last_waiter_registration_ns + quiet_ns, transition_registration_ns + derived_hard_ns)",
            "barrier_rule": "transition_prepare_start_ns is not earlier than collection_flush_ns or prefix_close_ns",
            "hard_tie_rule": "a quiet target equal to the hard deadline is classified as a hard flush",
            "post_initial_rule": "retain the 12480-ns post-initial window and geometric completion-release behavior"}:
        raise CampaignError("frozen V12 collection protocol changed")
    required_tokens = [
        "GUARD_SMALL_SET_FASTPATH_LIMIT", "GUARD_TRANSITION_PREFIX_BARRIER",
        "transaction_id,grant_phase,membership_target_n,subject_role",
        "prefix_target_bytes,prefix_observed_bytes,drain_outcome",
        "activation_batch_index,activation_batch_size",
        "guard_small_set_fastpath enabled", "guard_transition_prefix enabled",
        "accepted_fast_prepare_batches", "accepted_fast_activate_batches",
        "accepted_transition_prepare_batches",
        "accepted_transition_activate_batches", "accepted_release_batches",
        "high_transitions", "post_transition_fast_grants",
        "barrier_violations", "early_unlocks", "prefix_close_ns",
        "collection_flush_ns", "transition_prepare_start_ns",
        "fast_prepare_wire_grants", "transition_activate_wire_acks",
        "unattributed_grant_frames", "wire_reconciled", "terminal_phase",
        "terminal_consumed_revision",
    ]
    if spec.get("simulator_capability_probe") != {
            "status_at_base_commit": "present",
            "source": "scratch/network-load-balance.cc",
            "required_tokens": required_tokens}:
        raise CampaignError("frozen V12 simulator capability probe changed")
    if spec.get("resource_limits") != {
            "max_flows": 1000, "grant_trace_max_lines": 4096,
            "lifecycle_trace_max_lines": 32, "artifact_bytes_per_run": 16777216,
            "wall_time_seconds_per_run": 1800, "rss_kib_per_run": 8388608,
            "campaign_bytes": 1073741824}:
        raise CampaignError("frozen V12 resource_limits changed")
    if not execution_armed(spec):
        raise CampaignError("v12 replay/pilot/holdout execution contract is not armed")
    return spec


def _read_v13_spec(spec: Mapping[str, object]) -> Mapping[str, object]:
    """Validate V13 by freezing its delta and the complete V12 predecessor."""
    candidate = "membership_coalescing_v13"
    predecessor = "membership_coalescing_v12"
    if spec.get("supersedes") != "membership_coalescing_ladder_v12.json":
        raise CampaignError("frozen V13 predecessor changed")
    protocol = dict(spec.get("safe_activation_protocol", {}))
    if (protocol.get("minimum_simulator_commit") !=
            "b5c3e51e5ee225d52872e63711699e670e688519" or
            protocol.get("policy") !=
            "serialized_prepare_then_exact_prefix_wire_watchdog_activate_v1"):
        raise CampaignError("frozen V13 simulator minimum/policy changed")
    expected_watchdog = {
        "enabled": 1,
        "scope": "the single N>4 high-fan-in transition activation after prepare ACK closure",
        "raw_target_rule": "for each frozen live waiter, target_bytes=min(flow_size_bytes,sender_exact_qp_window_bytes)",
        "packet_count_rule": "packets[f]=ceil(target_bytes[f]/MTU)",
        "rounded_payload_rule": "payload[f]=min(flow_size_bytes[f],packets[f]*MTU)",
        "wire_budget_rule": "W=sum(payload[f]+packets[f]*data_header_bytes)",
        "occupancy_rule": "O=sum(acknowledged incumbent guard_grant_upper_bound_bps after transition prepare ACK closure)",
        "residual_rule": "R=receiver_capacity_bps-O and R must be positive",
        "deadline_rule": "delay_ns=max_exact_base_rtt_ns+ceil(W*8000000000/R); deadline_ns=start_ns+delay_ns",
        "progress_rule": "observed receiver prefix progress is not subtracted from W; ordinary DATA progress may satisfy exact-prefix readiness and cancel the watchdog",
        "reconstruction_rule": "records=1, non_reconstructable=0, and inconsistent=0 are required before the scalar fields may be treated as one same-source budget",
        "mtu_bytes": 1000,
        "data_header_bytes": 90,
        "data_header_source": "CustomHeader::GetStaticWholeHeaderSize() for the frozen CC11 build",
        "known_replay_n8": {
            "rounded_payload_budget_bytes": 728000,
            "packet_count": 728,
            "header_per_packet": 90,
            "wire_bytes": 793520,
            "occupancy_bps": 12500000000,
            "capacity_bps": 100000000000,
            "residual_bps": 87500000000,
            "serialization_ns": 72551,
            "max_rtt_ns": 8320,
            "delay_ns": 80871,
        },
    }
    if spec.get("transition_prefix_watchdog_protocol") != expected_watchdog:
        raise CampaignError("frozen V13 wire-watchdog protocol changed")
    pilot = dict(spec.get("candidate_pilot", {}))
    if (pilot.get("seed") != 137 or
            pilot.get("controllers") != [CONTROL_ARM, candidate] or
            pilot.get("transition_prefix_wire_watchdog") != {
                CONTROL_ARM: 0, candidate: 1}):
        raise CampaignError("frozen V13 pilot identity/watchdog changed")
    holdout = dict(spec.get("scalability_holdout", {}))
    if (holdout.get("seeds") != [138, 139, 140, 141, 142] or
            holdout.get("controllers") != [CONTROL_ARM, candidate]):
        raise CampaignError("frozen V13 formal identities changed")
    replay = dict(spec.get("known_failure_replay", {}))
    if (replay.get("controller") != candidate or
            replay.get("admission_artifact") !=
                "known_replay_admission_v13.json"):
        raise CampaignError("frozen V13 replay identity changed")
    gate = dict(spec.get("candidate_admission", {}))
    v13_gate = {
        "prefix_wire_watchdog_enabled": 1,
        "watchdog_records_required": 1,
        "watchdog_non_reconstructable_max": 0,
        "watchdog_inconsistent_max": 0,
        "watchdog_terminal_budget_max": 0,
        "watchdog_independent_reconstruction_required": True,
        "control_watchdog_records": 0,
        "low_n_watchdog_records": 0,
        "high_n_watchdog_records": 1,
    }
    if any(gate.get(key) != value for key, value in v13_gate.items()):
        raise CampaignError("frozen V13 watchdog admission changed")
    selection = (
        "K=4, V11 safe phases, the V12 exact-prefix barrier, the V13 full-wire "
        "residual-service watchdog, V11 seed-126 formal replay inputs, fresh "
        "pilot and formal identities, frame ceilings, unchanged 60-byte "
        "low-queue correction, aggregate N=15 gates, and per-seed N=15 "
        "stability gates are frozen before replay. Replay and pilot are "
        "mechanism-only. Formal performance remains sealed until all 40 "
        "mechanism cells pass. A failure rejects V13 and cannot select another "
        "parameter, watchdog rule, seed, N, arm, queue gate, or subset.")
    if spec.get("selection_policy") != selection:
        raise CampaignError("frozen V13 selection policy changed")
    required_tokens = [
        "GUARD_SMALL_SET_FASTPATH_LIMIT", "GUARD_TRANSITION_PREFIX_BARRIER",
        "GUARD_TRANSITION_PREFIX_WIRE_WATCHDOG",
        "CustomHeader::GetStaticWholeHeaderSize()",
        "transaction_id,grant_phase,membership_target_n,subject_role",
        "prefix_target_bytes,prefix_observed_bytes,drain_outcome",
        "activation_batch_index,activation_batch_size",
        "guard_small_set_fastpath enabled", "guard_transition_prefix enabled",
        "guard_transition_prefix_watchdog enabled",
        "records %lu non_reconstructable %u inconsistent %u",
        "rounded_payload_budget_bytes %lu packet_count %lu",
        "header_per_packet %lu wire_bytes %lu",
        "accepted_fast_prepare_batches", "accepted_fast_activate_batches",
        "accepted_transition_prepare_batches",
        "accepted_transition_activate_batches", "accepted_release_batches",
        "high_transitions", "post_transition_fast_grants",
        "barrier_violations", "early_unlocks", "prefix_close_ns",
        "collection_flush_ns", "transition_prepare_start_ns",
        "fast_prepare_wire_grants", "transition_activate_wire_acks",
        "unattributed_grant_frames", "wire_reconciled", "terminal_phase",
        "terminal_consumed_revision",
    ]
    if spec.get("simulator_capability_probe") != {
            "status_at_base_commit": "present",
            "source": "scratch/network-load-balance.cc",
            "required_tokens": required_tokens}:
        raise CampaignError("frozen V13 simulator capability probe changed")
    scope = list(spec.get("scope_limits", ()))
    if scope != [
            "V13 is a preregistered test of the full-wire residual-service watchdog layered on V12 exact-prefix activation and V11 safety, not an admitted default algorithm.",
            "The 60-byte small-queue absolute CI gate is retained unchanged from V11.",
            "The fifth registration selects high-fan-in mode irreversibly within the epoch; no parameter or performance-based threshold selection is allowed.",
            "The frame ceilings include retries and are only for the frozen one-epoch completion-only identities through N=15; V13 makes no universal sub-3N claim.",
            "Remaining-aware refresh, proactive release, receiver concurrency, size priority, and tail bypass remain outside this isolation experiment.",
            "Performance metrics remain sealed until replay, pilot, and every formal mechanism cell pass."]:
        raise CampaignError("frozen V13 scope limits changed")

    # Normalize only the explicitly checked V13 delta, then reuse the complete
    # V12 validator so every inherited behavior and gate remains fail-closed.
    inherited = copy.deepcopy(spec)
    inherited["schema_version"] = 12
    inherited["supersedes"] = "membership_coalescing_ladder_v11.json"
    inherited.pop("transition_prefix_watchdog_protocol")
    inherited["selection_policy"] = (
        "K=4, V11 safe phases, the V12 exact-prefix barrier, V11 seed-126 "
        "formal replay inputs, fresh pilot and formal identities, frame "
        "ceilings, unchanged 60-byte low-queue correction, aggregate N=15 "
        "gates, and per-seed N=15 stability gates are frozen before replay. "
        "Replay and pilot are mechanism-only. Formal performance remains "
        "sealed until all 40 mechanism cells pass. A failure rejects V12 and "
        "cannot select another parameter, prefix rule, seed, N, arm, queue "
        "gate, or subset.")
    inherited_protocol = dict(inherited["safe_activation_protocol"])
    inherited_protocol["minimum_simulator_commit"] = (
        "e5a57fa399c8ee4b5d3190d172771343baace632")
    inherited_protocol["policy"] = (
        "serialized_prepare_then_exact_prefix_activate_v1")
    inherited["safe_activation_protocol"] = inherited_protocol
    inherited_replay = dict(inherited["known_failure_replay"])
    inherited_replay["controller"] = predecessor
    inherited_replay["admission_artifact"] = "known_replay_admission_v12.json"
    inherited["known_failure_replay"] = inherited_replay
    inherited_pilot = dict(inherited["candidate_pilot"])
    inherited_pilot["seed"] = 131
    inherited_pilot["controllers"] = [CONTROL_ARM, predecessor]
    inherited_pilot.pop("transition_prefix_wire_watchdog")
    for field in (
            "small_set_fastpath_limit", "transition_prefix_barrier",
            "membership_quiet_ns", "initial_collection_full_deadline",
            "initial_collection_quiet_ns", "grant_reliability_service_rounds"):
        values = dict(inherited_pilot[field])
        values[predecessor] = values.pop(candidate)
        inherited_pilot[field] = values
    inherited["candidate_pilot"] = inherited_pilot
    inherited_holdout = dict(inherited["scalability_holdout"])
    inherited_holdout["seeds"] = [132, 133, 134, 135, 136]
    inherited_holdout["controllers"] = [CONTROL_ARM, predecessor]
    inherited["scalability_holdout"] = inherited_holdout
    inherited_gate = dict(inherited["candidate_admission"])
    for field in v13_gate:
        inherited_gate.pop(field)
    inherited["candidate_admission"] = inherited_gate
    inherited_execution = dict(inherited["holdout_execution"])
    inherited_execution["delivery_evidence"] = (
        "phase_tagged_serialized_prepare_exact_prefix_and_activation_ack_closure")
    inherited_execution["known_replay_admission_artifact"] = (
        "known_replay_admission_v12.json")
    inherited_execution["pilot_admission_artifact"] = "pilot_admission_v12.json"
    inherited_execution["required_generation_ack_evidence"] = list(
        V12_REQUIRED_ACK_EVIDENCE)
    inherited["holdout_execution"] = inherited_execution
    inherited_probe = dict(inherited["simulator_capability_probe"])
    v13_tokens = {
        "GUARD_TRANSITION_PREFIX_WIRE_WATCHDOG",
        "CustomHeader::GetStaticWholeHeaderSize()",
        "guard_transition_prefix_watchdog enabled",
        "records %lu non_reconstructable %u inconsistent %u",
        "rounded_payload_budget_bytes %lu packet_count %lu",
        "header_per_packet %lu wire_bytes %lu",
    }
    inherited_probe["required_tokens"] = [
        token for token in inherited_probe["required_tokens"]
        if token not in v13_tokens]
    inherited["simulator_capability_probe"] = inherited_probe
    inherited["scope_limits"] = [
        "V12 is a preregistered test of exact-prefix transition activation layered on V11 safety, not an admitted default algorithm.",
        *scope[1:3],
        "The frame ceilings include retries and are only for the frozen one-epoch completion-only identities through N=15; V12 makes no universal sub-3N claim.",
        *scope[4:],
    ]
    _read_v12_spec(inherited)
    return spec


def read_spec(path: Path) -> Mapping[str, object]:
    spec = _load_spec(path)
    if spec.get("schema_version") == 6:
        return _read_v6_spec(spec)
    if spec.get("schema_version") == 7:
        return _read_v7_spec(spec)
    if spec.get("schema_version") == 8:
        return _read_v8_spec(spec)
    if spec.get("schema_version") == 9:
        return _read_v9_spec(spec)
    if spec.get("schema_version") == 10:
        return _read_v10_spec(spec)
    if spec.get("schema_version") == 11:
        return _read_v11_spec(spec)
    if spec.get("schema_version") == 12:
        return _read_v12_spec(spec)
    if spec.get("schema_version") == 13:
        return _read_v13_spec(spec)
    raise CampaignError("membership-coalescing spec must use schema version 6--13")


def execution_armed(spec: Mapping[str, object]) -> bool:
    execution = spec.get("holdout_execution", {})
    if not isinstance(execution, dict):
        return False
    if spec.get("schema_version") == 6:
        expected = {
        "status": "armed",
        "delivery_evidence": "per_membership_generation_grant_ack",
        "pilot_admission_required": True,
        "pilot_admission_artifact": "pilot_admission.json",
        "required_generation_ack_evidence": list(REQUIRED_ACK_EVIDENCE),
        }
    elif spec.get("schema_version") == 7:
        expected = {
            "status": "armed",
            "delivery_evidence": "selective_per_membership_generation_grant_ack",
            "pilot_admission_required": True,
            "pilot_admission_artifact": "pilot_admission_v7.json",
            "required_generation_ack_evidence": list(V7_REQUIRED_ACK_EVIDENCE),
        }
    elif spec.get("schema_version") == 8:
        expected = {
            "status": "armed",
            "delivery_evidence": "selective_generation_ack_with_geometric_release",
            "pilot_admission_required": True,
            "pilot_admission_artifact": "pilot_admission_v8.json",
            "required_generation_ack_evidence": list(V8_REQUIRED_ACK_EVIDENCE),
        }
    elif spec.get("schema_version") == 9:
        expected = {
            "status": "armed",
            "delivery_evidence":
                "fixed_initial_collection_with_selective_ack_and_geometric_release",
            "known_replay_admission_required": True,
            "known_replay_admission_artifact": "known_replay_admission_v9.json",
            "pilot_admission_required": True,
            "pilot_admission_artifact": "pilot_admission_v9.json",
            "required_generation_ack_evidence": list(V9_REQUIRED_ACK_EVIDENCE),
        }
    elif spec.get("schema_version") == 10:
        expected = {
            "status": "armed",
            "delivery_evidence":
                "sliding_initial_collection_with_selective_ack_and_geometric_release",
            "known_replay_admission_required": True,
            "known_replay_admission_artifact": "known_replay_admission_v10.json",
            "pilot_admission_required": True,
            "pilot_admission_artifact": "pilot_admission_v10.json",
            "required_generation_ack_evidence": list(V10_REQUIRED_ACK_EVIDENCE),
        }
    elif spec.get("schema_version") == 11:
        expected = {
            "status": "armed",
            "delivery_evidence":
                "phase_tagged_serialized_prepare_and_activation_ack_closure",
            "known_replay_admission_required": True,
            "known_replay_admission_artifact": "known_replay_admission_v11.json",
            "pilot_admission_required": True,
            "pilot_admission_artifact": "pilot_admission_v11.json",
            "required_generation_ack_evidence": list(V11_REQUIRED_ACK_EVIDENCE),
        }
    elif spec.get("schema_version") == 12:
        expected = {
            "status": "armed",
            "delivery_evidence":
                "phase_tagged_serialized_prepare_exact_prefix_and_activation_ack_closure",
            "known_replay_admission_required": True,
            "known_replay_admission_artifact": "known_replay_admission_v12.json",
            "pilot_admission_required": True,
            "pilot_admission_artifact": "pilot_admission_v12.json",
            "required_generation_ack_evidence": list(V12_REQUIRED_ACK_EVIDENCE),
        }
    elif spec.get("schema_version") == 13:
        expected = {
            "status": "armed",
            "delivery_evidence":
                "phase_tagged_serialized_prepare_exact_prefix_wire_watchdog_and_activation_ack_closure",
            "known_replay_admission_required": True,
            "known_replay_admission_artifact": "known_replay_admission_v13.json",
            "pilot_admission_required": True,
            "pilot_admission_artifact": "pilot_admission_v13.json",
            "required_generation_ack_evidence": list(V13_REQUIRED_ACK_EVIDENCE),
        }
    else:
        return False
    return execution == expected


def assert_execution_armed(spec: Mapping[str, object]) -> None:
    """Reject formal runs until a frozen generation-ACK contract arms them."""
    if not execution_armed(spec):
        raise CampaignError(
            "formal holdout is disarmed until a frozen spec requires per-membership-"
            "generation grant ACK evidence; progress_confirmed is not delivery evidence")


def holdout_axes(spec: Mapping[str, object]) -> Tuple[Tuple[int, ...], Tuple[int, ...], Tuple[str, ...]]:
    holdout = dict(spec["scalability_holdout"])
    return (
        tuple(map(int, holdout["seeds"])),
        tuple(map(int, holdout["active_flow_counts"])),
        tuple(map(str, holdout["controllers"])),
    )


def matrix(spec: Mapping[str, object]) -> List[Tuple[int, int, str]]:
    """Return the exact 40-run order; no subset/filter interface is exposed."""
    seeds, counts, arms = holdout_axes(spec)
    return [(seed, count, arm) for seed in seeds for count in counts for arm in arms]


def pilot_matrix(spec: Mapping[str, object]) -> List[Tuple[int, int, str]]:
    """Return the exact qualification pilot; it is not a holdout subset."""
    pilot = dict(spec["candidate_pilot"])
    arms = tuple(map(str, pilot["controllers"]))
    if int(spec.get("schema_version", 0)) in (11, 12, 13):
        return [(int(pilot["seed"]), int(count), arm)
                for count in pilot["active_flow_counts"] for arm in arms]
    return [(int(pilot["seed"]), int(pilot["active_flows"]), arm) for arm in arms]


def replay_matrix(spec: Mapping[str, object]) -> List[Tuple[int, int, str]]:
    """Return the exact candidate-only replay frozen by V9 and later."""
    schema_version = int(spec.get("schema_version", 0))
    if schema_version not in (9, 10, 11, 12, 13):
        return []
    replay = dict(spec["known_failure_replay"])
    if schema_version in (11, 12, 13):
        arm = str(replay["controller"])
        return [(int(row["seed"]), int(row["active_flows"]), arm)
                for row in replay["identities"]]
    return [(int(replay["seed"]), int(replay["active_flows"]),
             str(replay["controller"]))]


def assert_simulator_capability(repo: Path, spec: Mapping[str, object]) -> None:
    """Fail before input generation/run when the frozen raw schema is absent."""
    schema_version = int(spec.get("schema_version", 0))
    if schema_version < 7:
        return
    probe = dict(spec["simulator_capability_probe"])
    source = repo / str(probe["source"])
    if not source.is_file():
        raise CampaignError(
            f"V{schema_version} simulator capability source is missing: {source}")
    raw = source.read_text(encoding="utf-8", errors="strict")
    missing = [token for token in probe["required_tokens"] if str(token) not in raw]
    if missing:
        raise CampaignError(
            f"V{schema_version} simulator capability is absent; refusing to launch: "
            + ", ".join(map(str, missing)))


def clean_revision(repo: Path, spec: Mapping[str, object]) -> str:
    """Return the exact clean revision that a preflight or run will freeze."""
    sha, dirty = git_revision(repo)
    if dirty:
        raise CampaignError("refusing to use a dirty guard worktree")
    protocol_key = ("safe_activation_protocol" if int(spec["schema_version"]) >= 11
                    else "generation_ack_protocol")
    minimum = str(dict(spec[protocol_key])["minimum_simulator_commit"])
    if subprocess.run(
            ["git", "merge-base", "--is-ancestor", minimum, sha], cwd=repo).returncode:
        raise CampaignError(
            f"schema V{spec['schema_version']} requires simulator commit {minimum}; found {sha}")
    assert_simulator_capability(repo, spec)
    return sha


def _flow_paths(campaign_dir: Path, scope: str, seed: int, count: int) -> Tuple[Path, Path]:
    stem = campaign_dir / "flows" / f"{scope}-receiver-share-n{count}-s{seed}"
    return stem.with_suffix(".txt"), stem.with_suffix(".manifest.json")


def _generate_flow(repo: Path, output: Path, manifest: Path, seed: int, count: int,
                   pilot: Mapping[str, object], max_flows: int) -> None:
    command = [
        sys.executable, str(repo / "experiments" / "generate_workload.py"),
        "--workload", "receiver-share", "--output", str(output),
        "--manifest", str(manifest), "--hosts", str(HOSTS),
        "--duration-ms", str(float(pilot["simul_time_s"]) * 1000.0),
        "--base-time", str(BASE_TIME_S),
        "--priority-group", str(pilot["priority_group"]),
        "--max-flows", str(max_flows), "--seed", str(seed),
        "--flow-bytes", str(pilot["flow_bytes"]),
        "--receiver-share-destination", str(pilot["receiver"]),
        "--receiver-share-flows", str(count),
        "--receiver-share-background-flows", "0",
        "--receiver-share-jitter-us", str(pilot["arrival_jitter_us"]),
    ]
    result = subprocess.run(command, cwd=repo, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    if result.returncode:
        raise CampaignError(f"workload generator failed: {result.stdout[-2000:]}")


def _traffic_identities(spec: Mapping[str, object]) -> List[Tuple[str, int, int]]:
    """Return unique frozen inputs, independently of the two-arm run matrix."""
    schema_version = int(spec["schema_version"])
    pilot = dict(spec["candidate_pilot"])
    seeds, counts, _arms = holdout_axes(spec)
    identities: List[Tuple[str, int, int]] = []
    if schema_version in (9, 10):
        replay = dict(spec["known_failure_replay"])
        identities.append(("replay", int(replay["seed"]),
                           int(replay["active_flows"])))
    elif schema_version in (11, 12, 13):
        identities.extend(
            ("replay", int(row["seed"]), int(row["active_flows"]))
            for row in dict(spec["known_failure_replay"])["identities"])
    if schema_version in (11, 12, 13):
        identities.extend(("pilot", int(pilot["seed"]), int(count))
                          for count in pilot["active_flow_counts"])
    else:
        identities.append(("pilot", int(pilot["seed"]), int(pilot["active_flows"])))
    identities.extend(("holdout", seed, count) for seed in seeds for count in counts)
    return identities


def _replay_hashes(spec: Mapping[str, object]) -> Mapping[Tuple[int, int], str]:
    replay = dict(spec["known_failure_replay"])
    if int(spec["schema_version"]) in (11, 12, 13):
        return {(int(row["seed"]), int(row["active_flows"])):
                str(row["flow_sha256"]) for row in replay["identities"]}
    return {(int(replay["seed"]), int(replay["active_flows"])):
            str(replay["flow_sha256"])}


def preflight(spec_path: Path, spec: Mapping[str, object], repo: Path,
              campaign_dir: Path) -> Mapping[str, object]:
    sha = clean_revision(repo, spec)
    if campaign_dir.exists():
        raise CampaignError("campaign directory already exists; use --resume")
    (campaign_dir / "flows").mkdir(parents=True)
    pilot = dict(spec["candidate_pilot"])
    max_flows = int(dict(spec["resource_limits"])["max_flows"])
    traffic: List[Dict[str, object]] = []
    identities = _traffic_identities(spec)
    replay_hashes = (_replay_hashes(spec) if replay_matrix(spec) else {})
    for scope, seed, count in identities:
        output, manifest = _flow_paths(campaign_dir, scope, seed, count)
        _generate_flow(repo, output, manifest, seed, count, pilot, max_flows)
        metadata = json.loads(manifest.read_text(encoding="utf-8"))
        validation = dict(metadata.get("validation", {}))
        if validation.get("status") != "passed" or int(validation.get("flow_count", -1)) != count:
            raise CampaignError(f"generated workload failed read-back: N={count} seed={seed}")
        digest = sha256_file(output)
        if digest != validation.get("sha256"):
            raise CampaignError(f"generated workload hash mismatch: {output}")
        traffic.append({
            "scope": scope,
            "seed": seed, "active_flows": count, "flow_count": count,
            "path": str(output.resolve()), "manifest_path": str(manifest.resolve()),
            "sha256": digest, "bytes": output.stat().st_size,
        })
        if scope == "replay":
            expected = replay_hashes[(seed, count)]
            if digest != expected:
                raise CampaignError(
                    f"V{spec['schema_version']} replay flow hash changed: "
                    f"expected {expected}, found {digest}")
    if len({str(row["sha256"]) for row in traffic}) != len(traffic):
        raise CampaignError("pilot and holdout traffic inputs must be distinct")
    frozen = {
        "schema_version": int(spec["schema_version"]),
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "repo": str(repo), "simulator_git_sha": sha, "git_dirty": False,
        "spec_path": str(spec_path), "spec_sha256": sha256_file(spec_path),
        "pilot_run_count": len(pilot_matrix(spec)), "pilot_run_order": [
            {"ordinal": index, "seed": seed, "active_flows": count, "controller": arm}
            for index, (seed, count, arm) in enumerate(pilot_matrix(spec), 1)],
        "replay_run_count": len(replay_matrix(spec)), "replay_run_order": [
            {"ordinal": index, "seed": seed, "active_flows": count,
             "controller": arm}
            for index, (seed, count, arm) in enumerate(replay_matrix(spec), 1)],
        "run_count": len(matrix(spec)), "run_order": [
            {"ordinal": index, "seed": seed, "active_flows": count, "controller": arm}
            for index, (seed, count, arm) in enumerate(matrix(spec), 1)],
        "traffic": traffic,
        "spec_execution_armed": execution_armed(spec),
        "pilot_execution_armed": int(spec["schema_version"]) not in (9, 10, 11, 12, 13),
        "formal_execution_armed": False,
        "known_replay_admission": None,
        "pilot_admission": None,
    }
    write_json(campaign_dir / "campaign.json", spec)
    write_json(campaign_dir / "preflight.json", frozen)
    return frozen


def load_preflight(campaign_dir: Path, spec_path: Path,
                   spec: Mapping[str, object]) -> Mapping[str, object]:
    path = campaign_dir / "preflight.json"
    if not path.is_file():
        raise CampaignError("missing preflight.json; run --phase preflight first")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not manifest.get("simulator_git_sha") or manifest.get("git_dirty"):
        raise CampaignError("preflight does not identify a clean simulator revision")
    if manifest.get("spec_sha256") != sha256_file(spec_path):
        raise CampaignError("spec changed after preflight")
    expected_order = [
        {"ordinal": index, "seed": seed, "active_flows": count, "controller": arm}
        for index, (seed, count, arm) in enumerate(matrix(spec), 1)]
    if (manifest.get("run_count") != len(matrix(spec)) or
            manifest.get("run_order") != expected_order):
        raise CampaignError("preflight run matrix/order changed")
    expected_pilot_order = [
        {"ordinal": index, "seed": seed, "active_flows": count, "controller": arm}
        for index, (seed, count, arm) in enumerate(pilot_matrix(spec), 1)]
    if (manifest.get("pilot_run_count") != len(pilot_matrix(spec)) or
            manifest.get("pilot_run_order") != expected_pilot_order or
            manifest.get("spec_execution_armed") is not True):
        raise CampaignError("preflight pilot matrix/order or frozen contract changed")
    expected_replay_order = [
        {"ordinal": index, "seed": seed, "active_flows": count,
         "controller": arm}
        for index, (seed, count, arm) in enumerate(replay_matrix(spec), 1)]
    if (manifest.get("replay_run_count") != len(replay_matrix(spec)) or
            manifest.get("replay_run_order") != expected_replay_order):
        raise CampaignError("preflight replay matrix/order changed")
    traffic = list(manifest.get("traffic", []))
    expected_identities = _traffic_identities(spec)
    expected_traffic_count = len(expected_identities)
    if len(traffic) != expected_traffic_count:
        raise CampaignError(
            "preflight must contain the exact replay, pilot, and holdout traffic inputs")
    expected_traffic = set(expected_identities)
    observed_traffic = {
        (str(row.get("scope")), int(row["seed"]), int(row["active_flows"]))
        for row in traffic
    }
    if observed_traffic != expected_traffic:
        raise CampaignError("preflight traffic identities changed")
    for row in traffic:
        flow_path = Path(str(row["path"]))
        flow_manifest = Path(str(row["manifest_path"]))
        if (not flow_path.is_file() or not flow_manifest.is_file() or
                sha256_file(flow_path) != row.get("sha256")):
            raise CampaignError(f"frozen traffic changed: {flow_path}")
        if (row.get("scope") == "replay" and
                row.get("sha256") != _replay_hashes(spec)[
                    (int(row["seed"]), int(row["active_flows"]))]):
            raise CampaignError(
                f"frozen V{spec['schema_version']} replay traffic hash differs from sealed V8 input")
    return manifest


def _traffic(preflight_manifest: Mapping[str, object], scope: str, seed: int,
             count: int) -> Mapping[str, object]:
    matches = [row for row in preflight_manifest["traffic"]
               if row.get("scope") == scope and int(row["seed"]) == seed and
               int(row["active_flows"]) == count]
    if len(matches) != 1:
        raise CampaignError(
            f"missing unique frozen {scope} traffic for seed={seed}, N={count}")
    return matches[0]


def run_command(repo: Path, spec: Mapping[str, object], traffic: Mapping[str, object],
                arm: str) -> List[str]:
    pilot = dict(spec["candidate_pilot"])
    limits = dict(spec["resource_limits"])
    command = [
        "env", "PYENV_VERSION=2.7.18", sys.executable, str(repo / "run.py"),
        "--cc", "guard", "--lb", "fecmp", "--pfc", str(pilot["pfc"]),
        "--irn", str(pilot["irn"]), "--topo", str(pilot["topology"]),
        "--simul_time", str(pilot["simul_time_s"]), "--netload", "40",
        "--bw", "100", "--cdf", "AliStorage2019", "--seed", str(traffic["seed"]),
        "--flow_file", str(traffic["path"]), "--max_flows", str(limits["max_flows"]),
        "--analysis_warmup", str(pilot["analysis_warmup_s"]), "--buffer", "9",
        "--monitor_profile", str(pilot["monitor_profile"]),
    ]
    for option, value in GUARD_OPTIONS.items():
        command.extend([f"--{option}", str(value)])
    command.extend([
        "--guard_membership_coalesce_ns", str(pilot["membership_quiet_ns"][arm]),
        "--guard_membership_coalesce_max_windows", str(pilot["membership_max_windows"]),
        "--guard_grant_reliability_rtts",
        str(pilot["grant_reliability_service_rounds"][arm]),
        "--guard_lifecycle_trace", "1", "--guard_lifecycle_max_lines",
        str(limits["lifecycle_trace_max_lines"]),
        "--guard_grant_trace", "1", "--guard_grant_max_lines",
        str(limits["grant_trace_max_lines"]),
    ])
    if int(spec.get("schema_version", 0)) >= 10:
        command.extend([
            "--guard_initial_collection_full_deadline",
            str(pilot["initial_collection_full_deadline"][arm]),
            "--guard_initial_collection_quiet_ns",
            str(pilot["initial_collection_quiet_ns"][arm]),
        ])
    if int(spec.get("schema_version", 0)) in (11, 12, 13):
        command.extend([
            "--guard_small_set_fastpath_limit",
            str(pilot["small_set_fastpath_limit"][arm]),
        ])
    if int(spec.get("schema_version", 0)) in (12, 13):
        command.extend([
            "--guard_transition_prefix_barrier",
            str(pilot["transition_prefix_barrier"][arm]),
        ])
    if int(spec.get("schema_version", 0)) == 13:
        command.extend([
            "--guard_transition_prefix_wire_watchdog",
            str(pilot["transition_prefix_wire_watchdog"][arm]),
        ])
    return command


def _process_group_rss_kib(process_group: int) -> int:
    total_pages = 0
    page_kib = os.sysconf("SC_PAGE_SIZE") // 1024
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw_stat = (entry / "stat").read_text()
            stat = raw_stat[raw_stat.rfind(")") + 2:].split()
            if int(stat[2]) != process_group:
                continue
            total_pages += int((entry / "statm").read_text().split()[1])
        except (FileNotFoundError, PermissionError, IndexError, ValueError):
            continue
    return total_pages * page_kib


def _terminate(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def execute_limited(command: Sequence[str], repo: Path, log_path: Path,
                    baseline: Mapping[str, Path], campaign_dir: Path,
                    prior_outputs: Iterable[Path], limits: Mapping[str, object]) -> Tuple[int, str, int, int]:
    """Execute one process group while enforcing wall, RSS, and byte caps."""
    started = time.monotonic()
    reason = "completed"
    max_rss_kib = 0
    with log_path.open("wb") as log:
        process = subprocess.Popen(command, cwd=repo, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        while process.poll() is None:
            elapsed = time.monotonic() - started
            current = output_directories(repo)
            new_outputs = [path for key, path in current.items() if key not in baseline]
            run_bytes = log_path.stat().st_size + sum(directory_size(path) for path in new_outputs)
            total_bytes = campaign_storage(campaign_dir, prior_outputs, []) + sum(
                directory_size(path) for path in new_outputs)
            max_rss_kib = max(max_rss_kib, _process_group_rss_kib(process.pid))
            if elapsed > int(limits["wall_time_seconds_per_run"]):
                reason = "wall-time-cap"
            elif max_rss_kib > int(limits["rss_kib_per_run"]):
                reason = "rss-cap"
            elif run_bytes > int(limits["artifact_bytes_per_run"]):
                reason = "run-storage-cap"
            elif total_bytes > int(limits["campaign_bytes"]):
                reason = "campaign-storage-cap"
            if reason != "completed":
                _terminate(process)
                break
            time.sleep(1)
        returncode = process.wait()
    return returncode, reason, int(time.monotonic() - started), max_rss_kib


def _existing_outputs(campaign_dir: Path) -> List[Path]:
    outputs: List[Path] = []
    paths = list((campaign_dir / "replay").rglob("manifest.json"))
    paths.extend((campaign_dir / "pilot").rglob("manifest.json"))
    paths.extend((campaign_dir / "runs").glob("n*/seed*/*/manifest.json"))
    for path in paths:
        row = json.loads(path.read_text(encoding="utf-8"))
        if row.get("output_dir"):
            outputs.append(Path(str(row["output_dir"])))
    return outputs


def pilot_admission_path(spec: Mapping[str, object], campaign_dir: Path) -> Path:
    name = str(dict(spec["holdout_execution"])["pilot_admission_artifact"])
    if Path(name).name != name:
        raise CampaignError("pilot admission artifact must be a campaign-root filename")
    return campaign_dir / name


def replay_admission_path(spec: Mapping[str, object], campaign_dir: Path) -> Path:
    name = str(dict(spec["holdout_execution"])["known_replay_admission_artifact"])
    if Path(name).name != name:
        raise CampaignError("replay admission artifact must be a campaign-root filename")
    return campaign_dir / name


def validate_replay_admission(spec_path: Path, spec: Mapping[str, object],
                              preflight_manifest: Mapping[str, object],
                              campaign_dir: Path) -> Mapping[str, object]:
    schema_version = int(spec.get("schema_version", 0))
    if schema_version not in (9, 10, 11, 12, 13):
        raise CampaignError("known-failure replay admission exists only for schema V9+")
    if preflight_manifest.get("pilot_execution_armed") is not True:
        raise CampaignError("pilot execution is blocked until the replay is admitted and sealed")
    frozen = preflight_manifest.get("known_replay_admission")
    if not isinstance(frozen, dict):
        raise CampaignError("preflight has no frozen replay admission artifact")
    path = replay_admission_path(spec, campaign_dir).resolve()
    if frozen.get("path") != str(path) or not path.is_file():
        raise CampaignError("frozen replay admission path is missing or changed")
    if frozen.get("sha256") != sha256_file(path):
        raise CampaignError("replay admission artifact changed after pilot arming")
    admission = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(admission, dict) or
            admission.get("schema_version") != schema_version or
            admission.get("scope") != "replay" or
            admission.get("status") != "admitted" or admission.get("passed") is not True or
            admission.get("performance_emitted") is not False or
            admission.get("run_count") != len(replay_matrix(spec)) or
            admission.get("spec_sha256") != sha256_file(spec_path) or
            admission.get("simulator_git_sha") !=
                preflight_manifest.get("simulator_git_sha")):
        raise CampaignError(
            f"replay admission artifact is not a valid V{schema_version} admission")
    return admission


def validate_pilot_admission(spec_path: Path, spec: Mapping[str, object],
                             preflight_manifest: Mapping[str, object],
                             campaign_dir: Path) -> Mapping[str, object]:
    if int(spec.get("schema_version", 0)) in (9, 10, 11, 12, 13):
        validate_replay_admission(spec_path, spec, preflight_manifest, campaign_dir)
    if preflight_manifest.get("formal_execution_armed") is not True:
        raise CampaignError("formal execution is blocked until the pilot is admitted and sealed")
    frozen = preflight_manifest.get("pilot_admission")
    if not isinstance(frozen, dict):
        raise CampaignError("preflight has no frozen pilot admission artifact")
    path = pilot_admission_path(spec, campaign_dir).resolve()
    if frozen.get("path") != str(path) or not path.is_file():
        raise CampaignError("frozen pilot admission path is missing or changed")
    digest = sha256_file(path)
    if frozen.get("sha256") != digest:
        raise CampaignError("pilot admission artifact changed after formal arming")
    admission = json.loads(path.read_text(encoding="utf-8"))
    replay_digest = (sha256_file(replay_admission_path(spec, campaign_dir))
                     if int(spec.get("schema_version", 0)) in (9, 10, 11, 12, 13) else None)
    if (not isinstance(admission, dict) or
            admission.get("schema_version") != spec.get("schema_version") or
            admission.get("scope") != "pilot" or admission.get("status") != "admitted" or
            admission.get("passed") is not True or admission.get("performance_emitted") is not False or
            admission.get("run_count") != len(pilot_matrix(spec)) or
            admission.get("spec_sha256") != sha256_file(spec_path) or
            admission.get("simulator_git_sha") != preflight_manifest.get("simulator_git_sha") or
            (int(spec.get("schema_version", 0)) in (9, 10, 11, 12, 13) and
             admission.get("known_replay_admission_sha256") != replay_digest)):
        raise CampaignError(
            f"pilot admission artifact is not a valid V{spec.get('schema_version')} admission")
    return admission


def recompute_replay_admission(spec_path: Path,
                               campaign_dir: Path) -> Mapping[str, object]:
    """Re-run the mechanism-only known-failure replay analyzer."""
    try:
        from experiments.analyze_membership_coalescing_holdout import analyze_replay
    except ModuleNotFoundError:  # Direct execution from experiments/.
        from analyze_membership_coalescing_holdout import analyze_replay
    return analyze_replay(spec_path, campaign_dir)


def seal_replay(spec_path: Path, spec: Mapping[str, object],
                preflight_manifest: Mapping[str, object], campaign_dir: Path,
                supplied_path: Path) -> Mapping[str, object]:
    """Bind replay evidence before allowing the fresh pilot to launch."""
    schema_version = int(spec.get("schema_version", 0))
    if schema_version not in (9, 10, 11, 12, 13):
        raise CampaignError("known-failure replay sealing exists only for schema V9+")
    if (preflight_manifest.get("pilot_execution_armed") or
            preflight_manifest.get("known_replay_admission") or
            preflight_manifest.get("pilot_admission") or
            preflight_manifest.get("formal_execution_armed")):
        raise CampaignError("replay admission is already sealed or a later phase started")
    expected = replay_admission_path(spec, campaign_dir).resolve()
    if supplied_path.resolve() != expected or not expected.is_file():
        raise CampaignError(f"replay admission must be the exact artifact {expected}")
    admission = json.loads(expected.read_text(encoding="utf-8"))
    before = sha256_file(campaign_dir / "preflight.json")
    if (not isinstance(admission, dict) or
            admission.get("schema_version") != schema_version or
            admission.get("scope") != "replay" or
            admission.get("status") != "admitted" or admission.get("passed") is not True or
            admission.get("performance_emitted") is not False or
            admission.get("run_count") != len(replay_matrix(spec)) or
            admission.get("replay_preflight_sha256") != before or
            admission.get("spec_sha256") != sha256_file(spec_path) or
            admission.get("simulator_git_sha") !=
                preflight_manifest.get("simulator_git_sha")):
        raise CampaignError("replay analyzer artifact does not admit this exact preflight")
    try:
        recomputed = recompute_replay_admission(spec_path, campaign_dir)
    except Exception as exc:
        raise CampaignError(f"replay mechanism revalidation failed: {exc}") from exc
    if admission != recomputed:
        raise CampaignError("replay artifact differs from fresh machine revalidation")
    updated = dict(preflight_manifest)
    updated["known_replay_admission"] = {
        "path": str(expected), "sha256": sha256_file(expected),
    }
    updated["pilot_execution_armed"] = True
    updated["replay_sealed_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    write_json(campaign_dir / "preflight.json", updated)
    return updated


def recompute_pilot_admission(spec_path: Path,
                              campaign_dir: Path) -> Mapping[str, object]:
    """Re-run the mechanism-only analyzer so a hand-written PASS cannot arm formal runs."""
    try:
        from experiments.analyze_membership_coalescing_holdout import analyze_pilot
    except ModuleNotFoundError:  # Direct execution from experiments/.
        from analyze_membership_coalescing_holdout import analyze_pilot
    return analyze_pilot(spec_path, campaign_dir)


def seal_pilot(spec_path: Path, spec: Mapping[str, object],
               preflight_manifest: Mapping[str, object], campaign_dir: Path,
               supplied_path: Path) -> Mapping[str, object]:
    """Atomically bind a passing pilot artifact into the formal preflight."""
    if preflight_manifest.get("formal_execution_armed") or preflight_manifest.get("pilot_admission"):
        raise CampaignError("pilot admission is already sealed")
    if int(spec.get("schema_version", 0)) in (9, 10, 11, 12, 13):
        validate_replay_admission(spec_path, spec, preflight_manifest, campaign_dir)
    expected = pilot_admission_path(spec, campaign_dir).resolve()
    if supplied_path.resolve() != expected or not expected.is_file():
        raise CampaignError(f"pilot admission must be the exact artifact {expected}")
    admission = json.loads(expected.read_text(encoding="utf-8"))
    before = sha256_file(campaign_dir / "preflight.json")
    replay_digest = (sha256_file(replay_admission_path(spec, campaign_dir))
                     if int(spec.get("schema_version", 0)) in (9, 10, 11, 12, 13) else None)
    if (not isinstance(admission, dict) or
            admission.get("schema_version") != spec.get("schema_version") or
            admission.get("scope") != "pilot" or admission.get("status") != "admitted" or
            admission.get("passed") is not True or admission.get("performance_emitted") is not False or
            admission.get("run_count") != len(pilot_matrix(spec)) or
            admission.get("pilot_preflight_sha256") != before or
            admission.get("spec_sha256") != sha256_file(spec_path) or
            admission.get("simulator_git_sha") != preflight_manifest.get("simulator_git_sha") or
            (int(spec.get("schema_version", 0)) in (9, 10, 11, 12, 13) and
             admission.get("known_replay_admission_sha256") != replay_digest)):
        raise CampaignError("pilot analyzer artifact does not admit this exact preflight")
    try:
        recomputed = recompute_pilot_admission(spec_path, campaign_dir)
    except Exception as exc:
        raise CampaignError(f"pilot mechanism revalidation failed: {exc}") from exc
    if admission != recomputed:
        raise CampaignError("pilot artifact differs from fresh machine revalidation")
    updated = dict(preflight_manifest)
    updated["pilot_admission"] = {
        "path": str(expected), "sha256": sha256_file(expected),
    }
    updated["formal_execution_armed"] = True
    updated["pilot_sealed_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    write_json(campaign_dir / "preflight.json", updated)
    return updated


def execute(spec_path: Path, spec: Mapping[str, object], preflight_manifest: Mapping[str, object],
            repo: Path, campaign_dir: Path, resume: bool, scope: str) -> int:
    assert_execution_armed(spec)
    if scope == "holdout":
        validate_pilot_admission(spec_path, spec, preflight_manifest, campaign_dir)
        plan = matrix(spec)
        root = campaign_dir / "runs"
    elif scope == "pilot":
        if preflight_manifest.get("formal_execution_armed"):
            raise CampaignError("pilot cannot be rerun after formal execution is armed")
        if int(spec.get("schema_version", 0)) in (9, 10, 11, 12, 13):
            validate_replay_admission(spec_path, spec, preflight_manifest, campaign_dir)
        plan = pilot_matrix(spec)
        root = campaign_dir / "pilot"
    elif scope == "replay":
        if int(spec.get("schema_version", 0)) not in (9, 10, 11, 12, 13):
            raise CampaignError("known-failure replay exists only for schema V9+")
        if (preflight_manifest.get("pilot_execution_armed") or
                preflight_manifest.get("known_replay_admission")):
            raise CampaignError("replay cannot rerun after pilot execution is armed")
        plan = replay_matrix(spec)
        root = campaign_dir / "replay"
    else:
        raise CampaignError(f"unknown execution scope: {scope}")
    sha = clean_revision(repo, spec)
    if sha != preflight_manifest.get("simulator_git_sha"):
        raise CampaignError(
            "simulator Git SHA changed after preflight: "
            f"{preflight_manifest.get('simulator_git_sha')} -> {sha}")
    limits = dict(spec["resource_limits"])
    prior_outputs = _existing_outputs(campaign_dir)
    failures = 0
    for ordinal, (seed, count, arm) in enumerate(plan, 1):
        if clean_revision(repo, spec) != sha:
            raise CampaignError(f"simulator Git SHA changed during the {scope} campaign")
        include_count = (scope == "holdout" or
                         int(spec.get("schema_version", 0)) in (11, 12, 13))
        run_dir = ((root / f"n{count}" / f"seed{seed}" / arm) if include_count
                   else (root / f"seed{seed}" / arm))
        manifest_path = run_dir / "manifest.json"
        if manifest_path.exists():
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
            if resume and previous.get("status") == "completed":
                print(f"[{ordinal}/{len(plan)}] skip N={count} seed={seed} {arm}")
                continue
            raise CampaignError(f"run manifest exists; only completed runs may resume: {manifest_path}")
        run_dir.mkdir(parents=True, exist_ok=False)
        traffic = _traffic(preflight_manifest, scope, seed, count)
        command = run_command(repo, spec, traffic, arm)
        baseline = output_directories(repo)
        started = dt.datetime.now(dt.timezone.utc).isoformat()
        print(f"[{ordinal}/{len(plan)}] N={count} seed={seed} {arm}")
        returncode, reason, elapsed, max_rss = execute_limited(
            command, repo, run_dir / "launcher.log", baseline, campaign_dir,
            prior_outputs, limits)
        after = output_directories(repo)
        new_outputs = [path for key, path in after.items() if key not in baseline]
        output = new_outputs[0] if len(new_outputs) == 1 else None
        if output is not None:
            prior_outputs.append(output)
        # Never open FCT or queue artifacts here.  Completion is admitted from
        # lifecycle/stats in the analyzer's mechanism-only phase.
        status = ("completed" if returncode == 0 and reason == "completed" and
                  output is not None else "failed")
        failures += status != "completed"
        row: MutableMapping[str, object] = {
            "schema_version": int(spec["schema_version"]), "ordinal": ordinal,
            "scope": scope,
            "seed": seed, "active_flows": count, "controller": arm,
            "status": status, "returncode": returncode, "stop_reason": reason,
            "started_at": started,
            "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "elapsed_seconds": elapsed, "max_rss_kib": max_rss,
            "repo": str(repo), "simulator_git_sha": sha, "git_dirty": False,
            "spec_path": str(spec_path), "spec_sha256": sha256_file(spec_path),
            "preflight_sha256": sha256_file(campaign_dir / "preflight.json"),
            "flow_path": traffic["path"], "flow_sha256": traffic["sha256"],
            "flow_count": traffic["flow_count"], "cli": command,
            "cli_shell": shlex.join(command), "output_id": output.name if output else None,
            "output_dir": str(output.resolve()) if output else None,
            "output_bytes": directory_size(output) if output else 0,
            "completed_flow_count": None,
            "completion_validation": "deferred_to_lifecycle_and_stats",
            "launcher_log": str((run_dir / "launcher.log").resolve()),
            "resource_limits": limits,
            "performance_metrics_emitted": False,
        }
        write_json(manifest_path, row)
        if campaign_storage(campaign_dir, prior_outputs, []) > int(limits["campaign_bytes"]):
            raise CampaignError("campaign storage cap exceeded")
        if status != "completed":
            print(f"run failed: N={count} seed={seed} {arm}; campaign stopped", file=sys.stderr)
            return 1
    return 1 if failures else 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--campaign-dir", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--phase", choices=("preflight", "replay", "seal-replay", "pilot",
                            "seal-pilot", "run"), required=True)
    parser.add_argument("--replay-admission", type=Path)
    parser.add_argument("--pilot-admission", type=Path)
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    repo = args.repo.resolve()
    spec_path = args.spec.resolve()
    campaign_dir = args.campaign_dir.resolve()
    spec = read_spec(spec_path)
    if args.phase == "preflight":
        manifest = load_preflight(campaign_dir, spec_path, spec) if args.resume else preflight(
            spec_path, spec, repo, campaign_dir)
        replay_note = f"{manifest['replay_run_count']} replay, " if manifest[
            "replay_run_count"] else ""
        print(
            f"preflight complete: {replay_note}{manifest['pilot_run_count']} pilot and "
            f"{manifest['run_count']} formal runs frozen; ns-3 was not launched; "
            "later execution remains blocked pending sealed admission artifacts")
        return 0
    manifest = load_preflight(campaign_dir, spec_path, spec)
    if args.phase == "replay":
        if args.replay_admission is not None or args.pilot_admission is not None:
            raise CampaignError("admission paths are invalid with --phase replay")
        return execute(spec_path, spec, manifest, repo, campaign_dir, args.resume, "replay")
    if args.phase == "seal-replay":
        if args.replay_admission is None:
            raise CampaignError("--phase seal-replay requires --replay-admission")
        if args.pilot_admission is not None:
            raise CampaignError("--pilot-admission is invalid with --phase seal-replay")
        seal_replay(spec_path, spec, manifest, campaign_dir, args.replay_admission)
        print("known-failure replay admission sealed; pilot execution is now armed")
        return 0
    if args.phase == "pilot":
        if args.replay_admission is not None or args.pilot_admission is not None:
            raise CampaignError("admission paths are invalid with --phase pilot")
        return execute(spec_path, spec, manifest, repo, campaign_dir, args.resume, "pilot")
    if args.phase == "seal-pilot":
        if args.pilot_admission is None:
            raise CampaignError("--phase seal-pilot requires --pilot-admission")
        seal_pilot(spec_path, spec, manifest, campaign_dir, args.pilot_admission)
        print("pilot admission sealed; formal execution is now armed")
        return 0
    if args.pilot_admission is not None or args.replay_admission is not None:
        raise CampaignError("admission paths are valid only with their seal phase")
    return execute(spec_path, spec, manifest, repo, campaign_dir, args.resume, "holdout")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CampaignError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
