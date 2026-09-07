#!/usr/bin/env python3
"""Mechanism-only gate for a V17 GUARD/HPCC/Homa development matrix.

This program intentionally never opens FCT or queue artifacts.  It creates
the admission file consumed by ``run_general_workloads.py`` and, after all
five seeds exist, a separate seal that authorizes the performance summarizer.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
from typing import Dict, List, Mapping, Sequence

try:
    from experiments.analyze_guard_v14_compatibility import (
        MIXED_FIELDS_V16, REFRESH_FIELDS, RETIRED_ACK_FIELDS, _named_stats,
        _zero_terminal, validate_v16_pg_provenance,
    )
    from experiments.analyze_membership_coalescing_holdout import (
        parse_coalescing_stats, parse_small_set_stats,
        parse_transition_prefix_stats, parse_transition_watchdog_stats,
    )
    from experiments.run_campaign import directory_size, sha256_file, write_json
    from experiments.summarize_campaign import SummaryError, parse_guard_stats, parse_pfc
    from experiments.summarize_general_workloads import (
        ZERO_RECOVERY_FIELDS, homa_completion_checks, mechanism_checks,
        validate_config,
    )
    from experiments.summarize_workload import parse_config, parse_snapshot
except ModuleNotFoundError:
    from analyze_guard_v14_compatibility import (
        MIXED_FIELDS_V16, REFRESH_FIELDS, RETIRED_ACK_FIELDS, _named_stats,
        _zero_terminal, validate_v16_pg_provenance,
    )
    from analyze_membership_coalescing_holdout import (
        parse_coalescing_stats, parse_small_set_stats,
        parse_transition_prefix_stats, parse_transition_watchdog_stats,
    )
    from run_campaign import directory_size, sha256_file, write_json
    from summarize_campaign import SummaryError, parse_guard_stats, parse_pfc
    from summarize_general_workloads import (
        ZERO_RECOVERY_FIELDS, homa_completion_checks, mechanism_checks,
        validate_config,
    )
    from summarize_workload import parse_config, parse_snapshot


COMPLETION_RE = re.compile(
    r"finished so far:\s*(?P<finished>\d+)/ total:\s*(?P<total>\d+)"
)


class MechanismError(RuntimeError):
    """A frozen run does not meet its non-performance admission contract."""


def prefix_fallback_closes(prefix: Mapping[str, object]) -> bool:
    timeouts = int(prefix["timeouts"])
    remaining = int(prefix["remaining_bytes"])
    accounting_closes = (
        int(prefix["target_bytes"]) ==
        int(prefix["received_bytes"]) + remaining)
    return (
        int(prefix["degraded"]) == timeouts and
        int(prefix["fallback_batches"]) ==
            int(prefix["fallback_closed_batches"]) and
        (timeouts == 0 or int(prefix["fallback_batches"]) >= timeouts) and
        ((timeouts == 0 and remaining == 0) or
         (timeouts > 0 and remaining > 0)) and
        accounting_closes)


def read_json(path: Path) -> Mapping[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise MechanismError(f"JSON root must be an object: {path}")
    return value


def one_artifact(output: Path, suffix: str) -> Path:
    matches = list(output.glob(f"*{suffix}"))
    if len(matches) != 1:
        raise MechanismError(f"expected one *{suffix} under {output}, got {len(matches)}")
    return matches[0]


def completion_from_log(path: Path, expected: int) -> Dict[str, int]:
    matches = list(COMPLETION_RE.finditer(path.read_text(encoding="utf-8", errors="strict")))
    if not matches:
        raise MechanismError(f"missing completion marker: {path}")
    finished = int(matches[-1].group("finished"))
    total = int(matches[-1].group("total"))
    if finished != expected or total != expected:
        raise MechanismError(f"completion is {finished}/{total}, expected {expected}/{expected}")
    return {"finished": finished, "total": total}


def guard_v17_checks(stats_path: Path, stats: Mapping[str, object],
                     profile: str,
                     expected_concurrency: int | None = None,
                     expected_adaptive: int | None = None,
                     expected_aging_rtts: float | None = None,
                     expected_spillover: int | None = None,
                     expected_spillover_enter: int | None = None,
                     expected_spillover_exit: int | None = None,
                     expected_elephant_target: int | None = None,
                     expected_elephant_target_scale: float | None = None,
                     expected_elephant_authority: int | None = None,
                     expected_initial_window_priority: int | None = None,
                     expected_transport_window_floor_rtt_ns: int | None = None,
                     expected_transport_window_after_first: int | None = None,
                     expected_transport_window_whole_flow: int | None = None,
                     expected_transport_window_ack_slack: int | None = None,
                     expected_cap_triggered_refresh: int | None = None) -> Dict[str, object]:
    membership = parse_coalescing_stats(stats_path, 13)
    small = parse_small_set_stats(stats_path)
    prefix = parse_transition_prefix_stats(stats_path)
    watchdog = parse_transition_watchdog_stats(stats_path)
    if watchdog is None:
        raise MechanismError("missing V17 watchdog stats")
    vector = _named_stats(stats_path, "guard_mixed_pg_vector", MIXED_FIELDS_V16)
    refresh = _named_stats(stats_path, "guard_refresh_draining", REFRESH_FIELDS)
    retired = _named_stats(stats_path, "guard_retired_ack", RETIRED_ACK_FIELDS)
    ack_clock = _named_stats(
        stats_path, "guard_transition_prefix_ack_clock_fallback", ("enabled",))
    capacity_admission = None
    if profile in ("V18", "V19", "V20", "V21", "V22", "V23", "V24", "V25",
                   "V26", "V27", "V28", "V29", "V30", "V31", "V32", "V34"):
        capacity_admission = _named_stats(
            stats_path, "guard_capacity_admission",
            ("enabled", "deferrals", "resumes", "max_waiters",
             "terminal_blocked"))

    for field in ("ack_stale", "stale_grants", "generation_zero_rejected",
                  "generation_mismatch_rejected", "pending", "retry_grants"):
        if int(membership[field]) != 0:
            raise MechanismError(f"membership {field} is nonzero")
    if (int(stats["grants_sent"]) <= 0 or
            int(stats["grants_sent"]) != int(stats["grants_received"]) or
            int(membership["ack_sent"]) != int(membership["ack_received"])):
        raise MechanismError("grant or generation-ACK counters do not close")
    if (int(small["enabled"]) != 1 or int(small["limit"]) != 4 or
            int(small["barrier_violations"]) != 0 or
            int(small["early_unlocks"]) != 0 or
            int(small["unattributed_grant_frames"]) != 0 or
            int(small["unattributed_ack_frames"]) != 0 or
            int(small["wire_reconciled"]) != 1):
        raise MechanismError("small-set/vector wire accounting did not close")
    if (int(prefix["enabled"]) != 1 or int(prefix["order_violations"]) != 0 or
            int(prefix["barrier_violations"]) != 0 or
            (int(prefix["timeouts"]) == 0 and
             int(prefix["fallback_batches"]) != 0) or
            not prefix_fallback_closes(prefix)):
        raise MechanismError("prefix/ACK-clock fallback did not close")
    if int(watchdog["enabled"]) != 1 or int(ack_clock["enabled"]) != 1:
        raise MechanismError("V17 watchdog or ACK-clock fallback is disabled")
    validate_v16_pg_provenance("generic-runtime-provenance", vector)
    if (int(vector["enabled"]) != 1 or int(vector["freezes"]) <= 0 or
            int(refresh["enabled"]) != 1 or
            int(refresh["progress_transactions"]) != int(refresh["progress_commits"]) or
            int(refresh["draining_requests"]) !=
                int(refresh["draining_direct"]) + int(refresh["draining_pending"]) or
            int(refresh["completion_releases"]) != int(refresh["draining_requests"])):
        raise MechanismError("mixed vector or serialized refresh/draining did not close")
    if (int(retired["closures"]) != int(retired["received"]) or
            int(retired["terminal"]) != 0 or int(retired["overflow"]) != 0):
        raise MechanismError("retired generation ACK records did not close")
    _zero_terminal(small, vector, refresh, prefix, watchdog)
    if capacity_admission is not None and (
            int(capacity_admission["enabled"]) != 1 or
            int(capacity_admission["deferrals"]) <= 0 or
            int(capacity_admission["deferrals"]) !=
                int(capacity_admission["resumes"]) or
            int(capacity_admission["max_waiters"]) <= 0 or
            int(capacity_admission["terminal_blocked"]) != 0):
        raise MechanismError("V18 capacity admission did not defer and close")
    if profile == "V19" and (
            int(stats["guard_receiver_concurrency"]) != 1 or
            float(stats["guard_concurrency_min_bdps"]) != 12.0 or
            int(stats["guard_concurrency_limited_allocations"]) <= 0 or
            int(stats["guard_concurrency_max_deferred_flows"]) <= 0):
        raise MechanismError("V19 frozen bounded-elephant service did not trigger")
    if profile == "V20" and (
            expected_concurrency not in (1, 2) or
            int(stats["guard_receiver_concurrency"]) != expected_concurrency or
            float(stats["guard_concurrency_min_bdps"]) != 12.0 or
            int(stats["guard_concurrency_limited_allocations"]) <= 0 or
            int(stats["guard_concurrency_max_deferred_flows"]) <= 0):
        raise MechanismError(
            "V20 frozen K=1/K=2 bounded-elephant service did not trigger")
    if profile == "V21" and (
            expected_concurrency not in (1, 2) or expected_adaptive not in (0, 1) or
            int(stats["guard_receiver_concurrency"]) != expected_concurrency or
            int(stats["guard_adaptive_elephant_concurrency"]) != expected_adaptive or
            float(stats["guard_concurrency_min_bdps"]) != 12.0 or
            int(stats["guard_concurrency_limited_allocations"]) <= 0 or
            int(stats["guard_concurrency_max_deferred_flows"]) <= 0 or
            (expected_adaptive == 1 and (
                int(stats["guard_adaptive_concurrency_promotions"]) <= 0 or
                int(stats["guard_adaptive_concurrency_max_effective"]) != 2)) or
            (expected_adaptive == 0 and (
                int(stats["guard_adaptive_concurrency_promotions"]) != 0 or
                int(stats["guard_adaptive_concurrency_max_effective"]) != 0))):
        raise MechanismError(
            "V21 adaptive elephant concurrency did not match its frozen arm")
    if profile == "V22" and (
            expected_concurrency != 1 or expected_aging_rtts not in (0.0, 2.0) or
            int(stats["guard_receiver_concurrency"]) != 1 or
            int(stats["guard_adaptive_elephant_concurrency"]) != 0 or
            float(stats["guard_concurrency_min_bdps"]) != 12.0 or
            int(stats["guard_concurrency_limited_allocations"]) <= 0 or
            int(stats["guard_concurrency_max_deferred_flows"]) <= 0 or
            int(stats["guard_elephant_aging_enabled"]) !=
                (1 if expected_aging_rtts > 0.0 else 0) or
            float(stats["guard_elephant_aging_rtts"]) != expected_aging_rtts or
            (expected_aging_rtts > 0.0 and (
                int(stats["guard_elephant_aging_rotations"]) <= 0 or
                int(stats["guard_elephant_aging_max_wait_ns"]) <= 0)) or
            (expected_aging_rtts == 0.0 and (
                int(stats["guard_elephant_aging_rotations"]) != 0 or
                int(stats["guard_elephant_aging_max_wait_ns"]) != 0)) or
            int(stats["guard_elephant_aging_active_deferred"]) != 0):
        raise MechanismError(
            "V22 fixed-K aging did not match its frozen arm or close terminal state")
    if profile in ("V23", "V24") and (
            expected_concurrency != 1 or expected_spillover not in (0, 1) or
            expected_spillover_enter not in range(1, 9) or
            expected_spillover_exit not in range(1, 9) or
            int(stats["guard_receiver_concurrency"]) != 1 or
            int(stats["guard_adaptive_elephant_concurrency"]) != 0 or
            float(stats["guard_elephant_aging_rtts"]) != 0.0 or
            float(stats["guard_concurrency_min_bdps"]) != 12.0 or
            int(stats["guard_concurrency_limited_allocations"]) <= 0 or
            int(stats["guard_concurrency_max_deferred_flows"]) <= 0 or
            int(stats["guard_elephant_spillover_enabled"]) != expected_spillover or
            int(stats["guard_elephant_spillover_enter_reports"]) !=
                expected_spillover_enter or
            int(stats["guard_elephant_spillover_exit_reports"]) !=
                expected_spillover_exit or
            int(stats["guard_elephant_spillover_under_grant_percent"]) != 5 or
            int(stats["guard_elephant_spillover_headroom_percent"]) != 10 or
            (expected_spillover == 1 and (
                int(stats["guard_cap_reports_sent"]) <= 0 or
                int(stats["guard_fabric_bound_reports"]) <= 0 or
                int(stats["guard_elephant_spillover_refresh_requests"]) <= 0 or
                int(stats["guard_elephant_spillover_vectors"]) <= 0 or
                int(stats["guard_elephant_spillover_max_bps"]) <= 0)) or
            (expected_spillover == 0 and (
                int(stats["guard_cap_reports_sent"]) != 0 or
                int(stats["guard_elephant_spillover_refresh_requests"]) != 0 or
                int(stats["guard_elephant_spillover_vectors"]) != 0 or
                int(stats["guard_elephant_spillover_max_bps"]) != 0))):
        raise MechanismError(
            f"{profile} cap-qualified spillover did not match its frozen arm")
    if profile == "V25" and (
            expected_concurrency != 1 or expected_elephant_target not in (0, 1) or
            expected_elephant_target_scale != 11.0 / 9.0 or
            int(stats["guard_receiver_concurrency"]) != 1 or
            int(stats["guard_adaptive_elephant_concurrency"]) != 0 or
            float(stats["guard_elephant_aging_rtts"]) != 0.0 or
            int(stats["guard_elephant_spillover_enabled"]) != 0 or
            int(stats["guard_elephant_fabric_target_enabled"]) !=
                expected_elephant_target or
            abs(float(stats["guard_elephant_fabric_target_scale"]) -
                expected_elephant_target_scale) > 1e-9 or
            float(stats["guard_elephant_fabric_target_threshold_bdps"]) != 12.0 or
            (expected_elephant_target == 1 and (
                int(stats["guard_elephant_fabric_target_updates"]) <= 0 or
                float(stats["guard_elephant_fabric_target_max_effective"]) <= 0.0)) or
            (expected_elephant_target == 0 and (
                int(stats["guard_elephant_fabric_target_updates"]) != 0 or
                float(stats["guard_elephant_fabric_target_max_effective"]) != 0.0))):
        raise MechanismError(
            "V25 class-scoped fabric target did not match its frozen arm")
    if profile == "V26" and (
            expected_concurrency != 1 or expected_elephant_authority not in (0, 1) or
            int(stats["guard_receiver_concurrency"]) != 1 or
            int(stats["guard_adaptive_elephant_concurrency"]) != 0 or
            float(stats["guard_elephant_aging_rtts"]) != 0.0 or
            int(stats["guard_elephant_spillover_enabled"]) != 0 or
            int(stats["guard_elephant_fabric_target_enabled"]) != 0 or
            int(stats["guard_elephant_receiver_authority_enabled"]) !=
                expected_elephant_authority or
            float(stats["guard_elephant_receiver_authority_threshold_bdps"]) != 12.0 or
            (expected_elephant_authority == 1 and (
                int(stats["guard_elephant_receiver_authority_bindings"]) <= 0 or
                int(stats["guard_elephant_receiver_authority_rate_changes"]) <= 0 or
                int(stats["guard_elephant_receiver_authority_max_released_bps"]) <= 0)) or
            (expected_elephant_authority == 0 and (
                int(stats["guard_elephant_receiver_authority_bindings"]) != 0 or
                int(stats["guard_elephant_receiver_authority_rate_changes"]) != 0 or
                int(stats["guard_elephant_receiver_authority_max_released_bps"]) != 0))):
        raise MechanismError(
            "V26 selected-elephant receiver authority did not match its frozen arm")
    initial_activity = tuple(int(stats[field]) for field in (
        "guard_initial_window_priority_flows",
        "guard_initial_window_priority_packets",
        "guard_initial_window_priority_bytes",
        "guard_initial_window_priority_transitions"))
    if profile == "V27" and (
            expected_concurrency != 1 or expected_initial_window_priority not in (0, 1) or
            int(stats["guard_receiver_concurrency"]) != 1 or
            int(stats["guard_adaptive_elephant_concurrency"]) != 0 or
            float(stats["guard_elephant_aging_rtts"]) != 0.0 or
            int(stats["guard_elephant_spillover_enabled"]) != 0 or
            int(stats["guard_elephant_fabric_target_enabled"]) != 0 or
            int(stats["guard_elephant_receiver_authority_enabled"]) != 0 or
            int(stats["guard_initial_window_priority_enabled"]) !=
                expected_initial_window_priority or
            int(stats["guard_initial_window_priority_pg"]) != 3 or
            (expected_initial_window_priority == 1 and (
                any(value <= 0 for value in initial_activity[:3]) or
                initial_activity[3] != initial_activity[0])) or
            (expected_initial_window_priority == 0 and any(initial_activity))):
        raise MechanismError(
            "V27 initial-window priority did not match its frozen arm or close")
    window_floor_activity = tuple(int(stats[field]) for field in (
        "guard_transport_window_floor_raised_flows",
        "guard_transport_window_floor_extra_bytes",
        "guard_transport_window_floor_max_bytes"))
    if profile == "V28" and (
            expected_concurrency != 1 or
            expected_transport_window_floor_rtt_ns not in (0, 8320) or
            int(stats["guard_receiver_concurrency"]) != 1 or
            int(stats["guard_adaptive_elephant_concurrency"]) != 0 or
            int(stats["guard_initial_window_priority_enabled"]) != 0 or
            int(stats["guard_transport_window_floor_enabled"]) !=
                (1 if expected_transport_window_floor_rtt_ns else 0) or
            int(stats["guard_transport_window_floor_rtt_ns"]) !=
                expected_transport_window_floor_rtt_ns or
            int(stats["guard_transport_window_floor_after_first_grant"]) != 0 or
            int(stats["guard_transport_window_whole_flow_first_gate"]) != 0 or
            int(stats["guard_transport_window_ack_slack_packets"]) != 0 or
            int(stats["guard_transport_window_ack_slack_limited_flows"]) != 0 or
            (expected_transport_window_floor_rtt_ns == 8320 and (
                window_floor_activity[0] <= 0 or window_floor_activity[1] <= 0 or
                window_floor_activity[2] != 104000)) or
            (expected_transport_window_floor_rtt_ns == 0 and
                any(window_floor_activity))):
        raise MechanismError(
            "V28 diameter-window floor did not match its frozen arm")
    if profile == "V29" and (
            expected_concurrency != 1 or
            (expected_transport_window_floor_rtt_ns,
             expected_transport_window_after_first) not in ((0, 0), (8320, 1)) or
            int(stats["guard_receiver_concurrency"]) != 1 or
            int(stats["guard_adaptive_elephant_concurrency"]) != 0 or
            int(stats["guard_initial_window_priority_enabled"]) != 0 or
            int(stats["guard_transport_window_floor_enabled"]) !=
                (1 if expected_transport_window_floor_rtt_ns else 0) or
            int(stats["guard_transport_window_floor_rtt_ns"]) !=
                expected_transport_window_floor_rtt_ns or
            int(stats["guard_transport_window_floor_after_first_grant"]) !=
                expected_transport_window_after_first or
            int(stats["guard_transport_window_whole_flow_first_gate"]) != 0 or
            int(stats["guard_transport_window_ack_slack_packets"]) != 0 or
            int(stats["guard_transport_window_ack_slack_limited_flows"]) != 0 or
            (expected_transport_window_floor_rtt_ns == 8320 and (
                window_floor_activity[0] <= 0 or window_floor_activity[1] <= 0 or
                window_floor_activity[2] != 104000)) or
            (expected_transport_window_floor_rtt_ns == 0 and
                any(window_floor_activity))):
        raise MechanismError(
            "V29 post-grant window floor did not match its frozen arm")
    if profile == "V30" and (
            expected_concurrency != 1 or
            (expected_transport_window_floor_rtt_ns,
             expected_transport_window_after_first,
             expected_transport_window_whole_flow) not in ((0, 0, 0), (8320, 1, 1)) or
            int(stats["guard_receiver_concurrency"]) != 1 or
            int(stats["guard_adaptive_elephant_concurrency"]) != 0 or
            int(stats["guard_initial_window_priority_enabled"]) != 0 or
            int(stats["guard_transport_window_floor_enabled"]) !=
                (1 if expected_transport_window_floor_rtt_ns else 0) or
            int(stats["guard_transport_window_floor_rtt_ns"]) !=
                expected_transport_window_floor_rtt_ns or
            int(stats["guard_transport_window_floor_after_first_grant"]) !=
                expected_transport_window_after_first or
            int(stats["guard_transport_window_whole_flow_first_gate"]) !=
                expected_transport_window_whole_flow or
            int(stats["guard_transport_window_ack_slack_packets"]) != 0 or
            int(stats["guard_transport_window_ack_slack_limited_flows"]) != 0 or
            (expected_transport_window_floor_rtt_ns == 8320 and (
                window_floor_activity[0] <= 0 or window_floor_activity[1] <= 0 or
                window_floor_activity[2] != 104000 or
                int(stats["guard_transport_window_whole_flow_raised_flows"]) <= 0)) or
            (expected_transport_window_floor_rtt_ns == 0 and (
                any(window_floor_activity) or
                int(stats["guard_transport_window_whole_flow_raised_flows"]) != 0))):
        raise MechanismError(
            "V30 bounded whole-flow first window did not match its frozen arm")
    if profile in ("V31", "V32", "V34") and (
            expected_concurrency != 1 or
            (expected_transport_window_floor_rtt_ns,
             expected_transport_window_after_first,
             expected_transport_window_whole_flow,
             expected_transport_window_ack_slack) not in
                ((0, 0, 0, 0), (8320, 1, 1, 16)) or
            int(stats["guard_receiver_concurrency"]) != 1 or
            int(stats["guard_adaptive_elephant_concurrency"]) != 0 or
            int(stats["guard_initial_window_priority_enabled"]) != 0 or
            int(stats["guard_transport_window_floor_enabled"]) !=
                (1 if expected_transport_window_floor_rtt_ns else 0) or
            int(stats["guard_transport_window_floor_rtt_ns"]) !=
                expected_transport_window_floor_rtt_ns or
            int(stats["guard_transport_window_floor_after_first_grant"]) !=
                expected_transport_window_after_first or
            int(stats["guard_transport_window_whole_flow_first_gate"]) !=
                expected_transport_window_whole_flow or
            int(stats["guard_transport_window_ack_slack_packets"]) !=
                expected_transport_window_ack_slack or
            (expected_transport_window_floor_rtt_ns == 8320 and (
                window_floor_activity[0] <= 0 or window_floor_activity[1] <= 0 or
                window_floor_activity[2] != 104000 or
                int(stats["guard_transport_window_whole_flow_raised_flows"]) <= 0 or
                int(stats["guard_transport_window_ack_slack_limited_flows"]) <= 0)) or
            (expected_transport_window_floor_rtt_ns == 0 and (
                any(window_floor_activity) or
                int(stats["guard_transport_window_whole_flow_raised_flows"]) != 0 or
                int(stats["guard_transport_window_ack_slack_limited_flows"]) != 0))):
        raise MechanismError(
            f"{profile} path-adaptive ACK-slack window did not match its frozen arm")
    if profile == "V34" and (
            expected_cap_triggered_refresh not in (0, 1) or
            int(stats["guard_size_class_elephant_concurrency"]) != 0 or
            int(stats["guard_elephant_spillover_enabled"]) != 0 or
            int(stats["guard_elephant_spillover_refresh_requests"]) != 0 or
            int(stats["guard_elephant_spillover_vectors"]) != 0 or
            int(stats["guard_cap_triggered_refresh_enabled"]) !=
                expected_cap_triggered_refresh or
            (expected_cap_triggered_refresh == 1 and (
                int(stats["guard_cap_reports_sent"]) <= 0 or
                int(stats["guard_cap_reports_received"]) <= 0 or
                int(stats["guard_cap_reports_received"]) >
                    int(stats["guard_cap_reports_sent"]) or
                int(stats["guard_fabric_bound_reports"]) <= 0 or
                int(stats["guard_cap_triggered_refresh_requests"]) <= 0)) or
            (expected_cap_triggered_refresh == 0 and (
                int(stats["guard_cap_reports_sent"]) != 0 or
                int(stats["guard_cap_reports_received"]) != 0 or
                int(stats["guard_cap_triggered_refresh_requests"]) != 0))):
        raise MechanismError(
            "V34 cap-triggered refresh did not match its frozen arm")
    return {
        "membership": membership, "safe_activation": small,
        "transition_prefix": prefix, "watchdog": watchdog,
        "target_vector": vector, "refresh_draining": refresh,
        "retired_ack": retired, "ack_clock": ack_clock,
        "capacity_admission": capacity_admission,
        "cap_triggered_refresh": {
            field: stats[field] for field in (
                "guard_cap_triggered_refresh_enabled",
                "guard_cap_triggered_refresh_requests",
                "guard_cap_reports_sent", "guard_cap_reports_received",
                "guard_fabric_bound_reports")
        },
        "initial_window_priority": {
            field: stats[field] for field in (
                "guard_initial_window_priority_enabled",
                "guard_initial_window_priority_flows",
                "guard_initial_window_priority_packets",
                "guard_initial_window_priority_bytes",
                "guard_initial_window_priority_transitions",
                "guard_initial_window_priority_pg")
        },
        "transport_window_floor": {
            field: stats[field] for field in (
                "guard_transport_window_floor_enabled",
                "guard_transport_window_floor_rtt_ns",
                "guard_transport_window_floor_raised_flows",
                "guard_transport_window_floor_extra_bytes",
                "guard_transport_window_floor_max_bytes",
                "guard_transport_window_floor_after_first_grant")
                + ("guard_transport_window_whole_flow_first_gate",
                   "guard_transport_window_whole_flow_raised_flows",
                   "guard_transport_window_ack_slack_packets",
                   "guard_transport_window_ack_slack_limited_flows")
        },
    }


def validate_run(campaign: Path, spec: Mapping[str, object], preflight: Mapping[str, object],
                 workload: Mapping[str, object], seed: int, arm: str) -> Dict[str, object]:
    manifest_path = campaign / "runs" / str(workload["name"]) / f"seed{seed}" / arm / "manifest.json"
    manifest = read_json(manifest_path)
    if manifest.get("status") != "completed" or manifest.get("git_dirty") is not False:
        raise MechanismError(f"run is not a clean completion: {manifest_path}")
    if str(manifest.get("git_sha")) != str(preflight["git_sha"]):
        raise MechanismError("simulator revision differs from sealed preflight")
    output = Path(str(manifest["output_dir"]))
    if directory_size(output) >= int(dict(spec["limits"])["run_bytes"]):
        raise MechanismError("run output reached its byte cap")
    trace = next(row for row in workload["selected_traces"] if int(row["seed"]) == seed)
    snapshot = one_artifact(output, "_input_flow.txt")
    profile = workload["attempts"][workload["selected_profile"]]["profile"]
    flows, metadata = parse_snapshot(snapshot, int(profile["hosts"]), 10_000)
    if (metadata["sha256"] != trace["sha256"] or
            Counter(int(flow["pg"]) for flow in flows) != Counter({3: len(flows)})):
        raise MechanismError("private flow snapshot hash or PG differs from preflight")
    completion = completion_from_log(output / "config.log", int(trace["flow_count"]))
    config = parse_config(output / "config.txt")
    errors = validate_config(config, manifest, arm, False, snapshot, spec)
    if errors:
        raise MechanismError("; ".join(errors))
    stats_path = one_artifact(output, "_out_guard_stats.txt")
    stats = parse_guard_stats(stats_path)
    pfc = parse_pfc(one_artifact(output, "_out_pfc.txt"))
    if any(int(stats[field]) != 0 for field in ZERO_RECOVERY_FIELDS):
        raise MechanismError("drop/recovery/timeout counter is nonzero")
    if (int(pfc["pfc_pause_events"]) != 0 or int(pfc["pfc_resume_events"]) != 0 or
            int(stats["pfc_pause_count"]) != 0 or int(stats["pfc_resume_count"]) != 0):
        raise MechanismError("PFC is nonzero")
    base_checks = mechanism_checks(arm, stats)
    if not all(base_checks.values()):
        raise MechanismError(f"base mechanism gate failed: {base_checks}")
    detail: Mapping[str, object] = {}
    if arm in ("guard", "guard_k1", "guard_k2", "guard_adaptive", "guard_aging",
               "guard_spillover", "guard_elephant_target",
               "guard_elephant_authority", "guard_initial_window",
               "guard_window_floor", "guard_post_grant_window",
               "guard_bounded_first_window", "guard_ack_slack_window",
               "guard_cap_refresh"):
        controls = dict(spec["defaults"])
        controls.update(dict(spec["arms"])[arm])
        detail = guard_v17_checks(
            stats_path, stats, str(spec["mechanism_profile"]),
            int(controls.get("guard_receiver_concurrency", 0)),
            int(controls.get("guard_adaptive_elephant_concurrency", 0)),
            float(controls.get("guard_elephant_aging_rtts", 0.0)),
            int(controls.get("guard_elephant_cap_spillover", 0)),
            int(controls.get("guard_elephant_spillover_enter_reports", 3)),
            int(controls.get("guard_elephant_spillover_exit_reports", 1)),
            int(controls.get("guard_elephant_fabric_target", 0)),
            float(controls.get("guard_elephant_fabric_target_scale", 1.0)),
            int(controls.get("guard_elephant_receiver_authority", 0)),
            int(controls.get("guard_initial_window_priority", 0)),
            int(controls.get("guard_transport_window_floor_rtt_ns", 0)),
            int(controls.get(
                "guard_transport_window_floor_after_first_grant", 0)),
            int(controls.get(
                "guard_transport_window_whole_flow_first_gate", 0)),
            int(controls.get(
                "guard_transport_window_ack_slack_packets", 0)),
            int(controls.get("guard_cap_triggered_refresh", 0)))
    elif arm == "homa":
        homa_checks = homa_completion_checks(stats, int(trace["flow_count"]))
        if not all(homa_checks.values()):
            raise MechanismError(f"Homa completion gate failed: {homa_checks}")
    return {
        "workload": workload["name"], "seed": seed, "arm": arm,
        "passed": True, "flow_sha256": trace["sha256"],
        "flow_count": trace["flow_count"], "completion": completion,
        "output_id": manifest["output_id"], "output_bytes": directory_size(output),
        "base_checks": base_checks, "detail": detail,
        "performance_emitted": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign_dir", type=Path)
    parser.add_argument("--phase", choices=("admission", "formal"), required=True)
    args = parser.parse_args(argv)
    campaign = args.campaign_dir.resolve()
    spec = read_json(campaign / "campaign.json")
    preflight = read_json(campaign / "preflight.json")
    if (spec.get("development_only") is not True or
            spec.get("mechanism_profile") not in (
                "V17", "V18", "V19", "V20", "V21", "V22", "V23", "V24", "V25",
                "V26", "V27", "V28", "V29", "V30", "V31", "V32", "V34")):
        raise MechanismError(
            "this analyzer only admits a frozen V17--V34 development spec")
    seeds = [int(spec["seeds"][0])] if args.phase == "admission" else list(map(int, spec["seeds"]))
    workloads = [row for row in preflight["workloads"] if row["decision"] == "included"]
    rows: List[Dict[str, object]] = []
    failures: List[Dict[str, object]] = []
    for workload in workloads:
        for seed in seeds:
            for arm in spec["arms"]:
                try:
                    rows.append(validate_run(campaign, spec, preflight, workload, seed, arm))
                except (MechanismError, SummaryError, OSError, KeyError, ValueError,
                        StopIteration) as exc:
                    failures.append({"workload": workload["name"], "seed": seed,
                                     "arm": arm, "error": str(exc)})
    expected = len(workloads) * len(seeds) * len(spec["arms"])
    hashes_close = all(len({row["flow_sha256"] for row in rows
                            if row["workload"] == workload["name"] and
                            row["seed"] == seed}) == 1
                       for workload in workloads for seed in seeds)
    passed = len(rows) == expected and not failures and hashes_close
    report = {
        "schema_version": 1, "phase": args.phase, "passed": passed,
        "expected_runs": expected, "admitted_runs": len(rows),
        "flow_hashes_close_within_seed": hashes_close,
        "preflight_sha256": sha256_file(campaign / "preflight.json"),
        "simulator_git_sha": preflight["git_sha"], "rows": rows,
        "failures": failures, "performance_emitted": False,
        "performance_unsealed": args.phase == "formal" and passed,
    }
    summary = campaign / "summary"
    summary.mkdir(parents=True, exist_ok=True)
    write_json(summary / f"mechanism-{args.phase}.json", report)
    if args.phase == "admission":
        decisions = {}
        for workload in workloads:
            selected = [row for row in rows if row["workload"] == workload["name"]]
            workload_passed = passed and len(selected) == len(spec["arms"])
            decisions[str(workload["name"])] = {
                "passed": workload_passed,
                "flow_hash_matched": hashes_close,
                "arms": selected,
                "decision": "extend_to_five_seeds" if workload_passed
                            else "exclude_without_performance",
            }
        write_json(summary / "admission.json", {
            "schema_version": 1,
            "gate": f"seed-{seeds[0]} V17 mechanisms before performance",
            "preflight_sha256": report["preflight_sha256"],
            "workloads": decisions, "all_selected_passed": passed,
            "performance_emitted": False,
        })
    print(f"{args.phase}: admitted {len(rows)}/{expected}; performance sealed")
    return 0 if passed else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MechanismError as error:
        print(f"error: {error}")
        raise SystemExit(2)
