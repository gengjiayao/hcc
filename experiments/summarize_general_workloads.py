#!/usr/bin/env python3
"""Validate, gate, and aggregate the matched general-workload campaign."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import json
import math
from pathlib import Path
import re
import statistics
from typing import Dict, Iterable, List, Mapping, MutableMapping, Sequence, Tuple

try:
    from experiments.run_campaign import directory_size, sha256_file, write_json
    from experiments.summarize_campaign import (
        GUARD_TOTAL_FIELDS, HOMA_TOTAL_FIELDS,
        PFC_PRIORITY_FIELDS,
        parse_guard_stats,
        parse_pfc,
        percentile,
        queue_summary_metrics,
    )
    from experiments.summarize_workload import (
        SummaryError,
        parse_config,
        parse_fct,
        parse_snapshot,
        validate_completions,
    )
except ModuleNotFoundError:  # Direct execution from experiments/.
    from run_campaign import directory_size, sha256_file, write_json
    from summarize_campaign import (
        GUARD_TOTAL_FIELDS, HOMA_TOTAL_FIELDS,
        PFC_PRIORITY_FIELDS,
        parse_guard_stats,
        parse_pfc,
        percentile,
        queue_summary_metrics,
    )
    from summarize_workload import (
        SummaryError,
        parse_config,
        parse_fct,
        parse_snapshot,
        validate_completions,
    )


ARMS = ("full", "hpcc", "receiver")
CC_MODES = {"full": 11, "guard": 11, "hpcc": 3, "receiver": 13, "homa": 12}
T95_DF4 = 2.7764451051977987
ZERO_RECOVERY_FIELDS = (
    "switch_drops_ingress", "switch_drops_egress", "switch_drops_total",
    "recovery_nacks_generated", "recovery_nacks_received",
    "irn_nacks_generated", "irn_nacks_received",
    "irn_retransmit_packets", "irn_retransmit_bytes", "timeout_recoveries",
)
HPCC_ZERO_FIELDS = (
    "hpcc_feedback_updates", "hpcc_valid_feedback", "hpcc_rate_updates_applied",
    "hpcc_full_computations", "hpcc_fast_computations", "hpcc_actual_rate_changes",
)
GUARD_SCHEDULER_FIELDS = (
    "guard_sender_srpt_enabled", "guard_srpt_quantum_packets",
    "guard_sender_srpt_selections", "guard_sender_srpt_non_rr",
    "guard_sender_srpt_forced_rr",
)
GUARD_OPTIMIZATION_FIELDS = (
    "guard_tail_bypass_enabled", "guard_tail_bypass_bdps",
    "guard_tail_bypass_flows", "guard_tail_bypass_feedbacks",
    "guard_remaining_aware", "guard_min_share_fraction",
    "guard_remaining_exponent", "guard_receiver_concurrency",
    "guard_concurrency_min_bdps", "guard_concurrency_limited_allocations",
    "guard_concurrency_max_deferred_flows", "guard_grant_refresh_bdps",
    "guard_remaining_refresh_events",
)


class AnalysisError(RuntimeError):
    """A formal run violates a frozen workload, mechanism, or pairing invariant."""


def read_json(path: Path) -> Mapping[str, object]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise AnalysisError(f"JSON root must be an object: {path}")
    return value


def flow_scope(size: int, buckets: Sequence[Mapping[str, object]]) -> str:
    matches = []
    for bucket in buckets:
        lower = bucket.get("min_exclusive_bytes")
        upper = bucket.get("max_bytes")
        if (lower is None or size > int(lower)) and (upper is None or size <= int(upper)):
            matches.append(str(bucket["name"]))
    if len(matches) != 1:
        raise AnalysisError(f"flow size {size} maps to {matches}, expected one bucket")
    return matches[0]


def scoped_fct(rows: Sequence[Mapping[str, object]], scope: str) -> Dict[str, float]:
    if not rows:
        return {}
    fcts = [float(row["fct_us"]) for row in rows]
    slowdowns = [float(row["slowdown"]) for row in rows]
    return {
        f"{scope}_flow_count": float(len(rows)),
        f"{scope}_fct_us_mean": statistics.fmean(fcts),
        f"{scope}_fct_us_p95": percentile(fcts, 95),
        f"{scope}_fct_us_p99": percentile(fcts, 99),
        f"{scope}_slowdown_mean": statistics.fmean(slowdowns),
        f"{scope}_slowdown_p95": percentile(slowdowns, 95),
        f"{scope}_slowdown_p99": percentile(slowdowns, 99),
    }


def fct_metrics(
    rows: Sequence[Mapping[str, object]], buckets: Sequence[Mapping[str, object]]
) -> Dict[str, float]:
    metrics = scoped_fct(rows, "overall")
    groups: MutableMapping[str, List[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        groups[flow_scope(int(row["size"]), buckets)].append(row)
    for bucket in buckets:
        name = str(bucket["name"])
        metrics.update(scoped_fct(groups[name], name))
    small = metrics.get("le_8KB_slowdown_mean")
    long = metrics.get("gt_1MB_slowdown_mean")
    if small and long is not None:
        metrics["long_to_small_slowdown_mean_ratio"] = long / small
    return metrics


def parse_controller(
    path: Path,
    flow_sizes: Mapping[int, int],
    buckets: Sequence[Mapping[str, object]],
    max_lines: int,
) -> Tuple[Dict[str, int], Dict[str, float]]:
    comments: List[str] = []
    data_lines: List[str] = []
    with path.open(encoding="utf-8", errors="strict") as stream:
        for line in stream:
            if line.startswith("#"):
                comments.append(line.strip())
            else:
                data_lines.append(line)
    if len(comments) != 1:
        raise AnalysisError(f"controller trace has {len(comments)} footers: {path}")
    match = re.fullmatch(r"# attempted (\d+) written (\d+) truncated (\d+)", comments[0])
    if not match:
        raise AnalysisError(f"malformed controller footer: {path}")
    footer = dict(zip(("attempted", "written", "truncated"), map(int, match.groups())))
    rows = list(csv.DictReader(data_lines))
    if len(rows) != footer["written"]:
        raise AnalysisError("controller footer written count does not match CSV")
    if footer["written"] > max_lines:
        raise AnalysisError("controller trace exceeds its frozen line bound")
    counts: MutableMapping[str, Counter[str]] = defaultdict(Counter)
    observed: MutableMapping[str, set[int]] = defaultdict(set)
    for row in rows:
        try:
            flow_id = int(row["flow_id"])
        except (KeyError, ValueError) as exc:
            raise AnalysisError(f"invalid controller flow id: {path}") from exc
        if flow_id not in flow_sizes:
            raise AnalysisError(f"controller references unknown flow {flow_id}")
        scope = flow_scope(flow_sizes[flow_id], buckets)
        event_type = str(row.get("event_type", ""))
        binding = str(row.get("binding", ""))
        counts[scope]["events"] += 1
        counts[scope][f"event_{event_type}"] += 1
        if event_type != "complete":
            if binding not in ("reactive", "grant", "tie"):
                raise AnalysisError(f"invalid controller binding {binding!r}")
            counts[scope][f"binding_{binding}"] += 1
        observed[scope].add(flow_id)
    metrics: Dict[str, float] = {}
    for bucket in buckets:
        scope = str(bucket["name"])
        values = counts[scope]
        binding_events = sum(values[f"binding_{binding}"] for binding in ("reactive", "grant", "tie"))
        metrics[f"controller_{scope}_events"] = float(values["events"])
        metrics[f"controller_{scope}_flows_observed"] = float(len(observed[scope]))
        for binding in ("reactive", "grant", "tie"):
            value = values[f"binding_{binding}"]
            metrics[f"controller_{scope}_{binding}_events"] = float(value)
            metrics[f"controller_{scope}_{binding}_event_share"] = (
                value / binding_events if binding_events else 0.0
            )
    return footer, metrics


def mean_ci(values: Sequence[float]) -> Dict[str, float]:
    if len(values) != 5:
        raise AnalysisError(f"formal t95 requires five values, got {len(values)}")
    mean = statistics.fmean(values)
    half = T95_DF4 * statistics.stdev(values) / math.sqrt(5)
    return {
        "n": 5, "mean": mean, "ci95_low": mean - half,
        "ci95_high": mean + half, "ci95_half_width": half,
        "t_critical": T95_DF4,
    }


def one_output_file(output: Path, suffix: str) -> Path:
    matches = list(output.glob(f"*{suffix}"))
    if len(matches) != 1:
        raise AnalysisError(f"expected one *{suffix} under {output}, got {len(matches)}")
    return matches[0]


def validate_config(
    config: Mapping[str, str], manifest: Mapping[str, object], arm: str,
    controller_trace: bool, expected_snapshot: Path, spec: Mapping[str, object],
) -> List[str]:
    errors: List[str] = []
    expected = {
        "CC_MODE": CC_MODES[arm],
        "ENABLE_PFC": 1,
        "ENABLE_IRN": 0,
        "RANDOM_SEED": manifest["seed"],
        "PREFLIGHT_MAX_FLOWS": 10000,
        "MONITOR_PROFILE": "bulk",
        "GUARD_CONTROLLER_TRACE": 1 if controller_trace else 0,
    }
    cc = str(dict(spec["arms"])[arm]["cc"])
    if cc in ("guard", "guard-active-only"):
        controls = dict(spec["defaults"])
        controls.update(dict(spec["arms"])[arm])
        config_names = {
            "guard_lambda": "GUARD_LAMBDA",
            "guard_beta": "GUARD_EWMA_BETA",
            "guard_gamma": "GUARD_RELEASE_GAMMA",
            "guard_selective_registration": "GUARD_SELECTIVE_REGISTRATION",
            "guard_proactive_release": "GUARD_PROACTIVE_RELEASE",
            "guard_keep_last_hop_int": "GUARD_KEEP_LAST_HOP_INT",
            "guard_size_priority": "GUARD_SIZE_PRIORITY",
            "guard_sender_srpt": "GUARD_SENDER_SRPT",
            "guard_one_rtt_bypass": "GUARD_ONE_RTT_BYPASS",
            "guard_tail_bypass": "GUARD_TAIL_BYPASS",
            "guard_tail_bypass_bdps": "GUARD_TAIL_BYPASS_BDPS",
            "guard_ack_interval_packets": "GUARD_ACK_INTERVAL_PACKETS",
            "guard_fixed_window": "GUARD_FIXED_WINDOW",
            "guard_remaining_aware": "GUARD_REMAINING_AWARE",
            "guard_min_share_fraction": "GUARD_MIN_SHARE_FRACTION",
            "guard_remaining_exponent": "GUARD_REMAINING_EXPONENT",
            "guard_receiver_concurrency": "GUARD_RECEIVER_CONCURRENCY",
            "guard_concurrency_min_bdps": "GUARD_CONCURRENCY_MIN_BDPS",
            "guard_grant_refresh_bdps": "GUARD_GRANT_REFRESH_BDPS",
            "guard_srpt_quantum_packets": "GUARD_SRPT_QUANTUM_PACKETS",
            "guard_work_conserving": "GUARD_WORK_CONSERVING",
        }
        for option, key in config_names.items():
            if option in controls:
                expected[key] = controls[option]
    for key, value in expected.items():
        actual = config.get(key)
        if str(actual) != str(value):
            errors.append(f"{key}={actual}, expected {value}")
    configured_flow = Path(config.get("FLOW_FILE", ""))
    if not configured_flow.is_absolute():
        configured_flow = Path(manifest["output_dir"]).parents[2] / configured_flow
    if configured_flow.resolve() != expected_snapshot.resolve():
        errors.append("config FLOW_FILE does not name the private snapshot")
    return errors


def mechanism_checks(arm: str, stats: Mapping[str, object]) -> Dict[str, bool]:
    grants_sent = int(stats["grants_sent"])
    grants_received = int(stats["grants_received"])
    if arm in ("full", "guard"):
        checks = {
            "full_grants": grants_sent > 0,
            "full_valid_hpcc": int(stats["hpcc_valid_feedback"]) > 0,
            "full_actual_rate_change": int(stats["hpcc_actual_rate_changes"]) > 0,
            "full_reactive_binding": int(stats["reactive_binding_updates"]) > 0,
        }
        if arm == "guard":
            checks.update({
                "guard_sender_srpt_enabled": int(stats["guard_sender_srpt_enabled"]) == 1,
                "guard_sender_srpt_selected": int(stats["guard_sender_srpt_selections"]) > 0,
                "guard_sender_srpt_non_rr": int(stats["guard_sender_srpt_non_rr"]) > 0,
                "guard_tail_bypass": int(stats["guard_tail_bypass_flows"]) > 0,
                "guard_progress_refresh":
                    int(stats["guard_remaining_refresh_events"]) > 0,
            })
        return checks
    if arm == "hpcc":
        return {
            "hpcc_zero_grants": grants_sent + grants_received == 0,
            "hpcc_valid_feedback": int(stats["hpcc_valid_feedback"]) > 0,
            "hpcc_actual_rate_change": int(stats["hpcc_actual_rate_changes"]) > 0,
        }
    if arm == "receiver":
        return {
        "receiver_grants": grants_sent > 0,
        "receiver_zero_hpcc": all(int(stats[field]) == 0 for field in HPCC_ZERO_FIELDS),
        }
    if arm == "homa":
        used_priorities = sum(
            1 for row in stats["homa_priority"].values()
            if int(row["data_packets"]) > 0
        )
        return {
            "homa_data": int(stats["homa_data_packets"]) > 0,
            "homa_grants": int(stats["homa_grants_sent"]) > 0
                           and int(stats["homa_grants_received"]) > 0,
            "homa_messages": int(stats["homa_messages_tracked"]) > 0
                             and int(stats["homa_messages_completed"]) > 0,
            "homa_completion_notice": int(stats["homa_completion_notices_sent"]) > 0
                                      and int(stats["homa_completion_notices_received"]) > 0,
            "homa_pending_state": int(stats["homa_max_pending_messages"]) > 0,
            "homa_multiple_priorities": used_priorities >= 2,
            "homa_zero_guard_hpcc": grants_sent + grants_received == 0
                                    and all(int(stats[field]) == 0 for field in HPCC_ZERO_FIELDS),
        }
    raise AnalysisError(f"unsupported arm: {arm}")


def homa_completion_checks(stats: Mapping[str, object], flow_count: int) -> Dict[str, bool]:
    replays = int(stats["homa_completion_notices_replayed"])
    duplicates = int(stats["homa_duplicate_data_after_completion"])
    return {
        "homa_tracked_every_flow": int(stats["homa_messages_tracked"]) == flow_count,
        "homa_completed_every_flow": int(stats["homa_messages_completed"]) == flow_count,
        "homa_tombstone_every_flow": int(stats["homa_completed_message_ids"]) == flow_count,
        "homa_notice_received_every_flow":
            int(stats["homa_completion_notices_received"]) == flow_count,
        "homa_completion_replays_close": replays == duplicates,
        "homa_notice_sends_close":
            int(stats["homa_completion_notices_sent"]) == flow_count + replays,
    }


def analyze_run(
    campaign_dir: Path,
    preflight_workload: Mapping[str, object],
    spec: Mapping[str, object],
    seed: int,
    arm: str,
) -> Tuple[Dict[str, object], Dict[str, object]]:
    manifest_path = campaign_dir / "runs" / str(preflight_workload["name"]) / f"seed{seed}" / arm / "manifest.json"
    if not manifest_path.is_file():
        raise AnalysisError(f"missing run manifest: {manifest_path}")
    manifest = read_json(manifest_path)
    if manifest.get("status") != "completed":
        raise AnalysisError(f"run is not completed: {manifest_path}")
    if manifest.get("git_dirty") is not False:
        raise AnalysisError(f"run used a dirty simulator: {manifest_path}")
    trace = next(
        row for row in preflight_workload["selected_traces"] if int(row["seed"]) == seed
    )
    source_flow = Path(trace["path"])
    output = Path(str(manifest["output_dir"]))
    output_id = str(manifest["output_id"])
    if directory_size(output) >= int(dict(spec["limits"])["run_bytes"]):
        raise AnalysisError(f"run artifact is at or over the 100 MiB bound: {output}")
    snapshot = output / f"{output_id}_input_flow.txt"
    if sha256_file(source_flow) != trace["sha256"] or sha256_file(snapshot) != trace["sha256"]:
        raise AnalysisError(f"flow hash mismatch for {preflight_workload['name']}/s{seed}/{arm}")
    profile = preflight_workload["attempts"][preflight_workload["selected_profile"]]["profile"]
    flows, snapshot_meta = parse_snapshot(snapshot, int(profile["hosts"]), 10000)
    if Counter(int(flow["pg"]) for flow in flows) != Counter({3: len(flows)}):
        raise AnalysisError("formal general workload snapshot is not uniformly PG3")
    fct_path = output / f"{output_id}_out_fct.txt"
    fct = parse_fct(fct_path)
    validate_completions(flows, fct, int(profile["hosts"]))
    config = parse_config(output / "config.txt")
    admission = seed == int(dict(spec["admission"])["seed"])
    trace_arm = str(dict(spec["admission"]).get("controller_trace_arm", "full"))
    controller_trace = admission and arm == trace_arm
    errors = validate_config(config, manifest, arm, controller_trace, snapshot, spec)
    stats = parse_guard_stats(output / f"{output_id}_out_guard_stats.txt")
    pfc = parse_pfc(output / f"{output_id}_out_pfc.txt")
    raw_priority = pfc["pfc_event_priority"]
    summary_priority = stats["pfc_priority"]
    for priority in set(raw_priority) | set(summary_priority):
        raw = raw_priority.get(priority, {})
        summary = summary_priority.get(priority, {})
        for field in ("pause_count", "resume_count"):
            if int(raw.get(field, 0)) != int(summary.get(field, 0)):
                errors.append(f"PFC q{priority} {field} raw/summary mismatch")
    checks = {
        "all_flows_completed": len(fct) == int(trace["flow_count"]),
        "flow_snapshot_hash": snapshot_meta["sha256"] == trace["sha256"],
        "pg3_snapshot": all(int(flow["pg"]) == 3 for flow in flows),
        "zero_drop_recovery": all(int(stats[field]) == 0 for field in ZERO_RECOVERY_FIELDS),
        "artifact_under_100MiB": directory_size(output) < int(dict(spec["limits"])["run_bytes"]),
        "config_exact": not errors,
    }
    if admission:
        checks.update(mechanism_checks(arm, stats))
        controls = dict(spec["defaults"])
        controls.update(dict(spec["arms"])[arm])
        if arm == "guard" and int(controls.get("guard_receiver_concurrency", 0)) > 0:
            checks["guard_concurrency_limited"] = (
                int(stats["guard_concurrency_limited_allocations"]) > 0
                and int(stats["guard_concurrency_max_deferred_flows"]) > 0
            )
    if arm == "homa":
        flow_count = int(trace["flow_count"])
        checks.update(homa_completion_checks(stats, flow_count))
    controller_metrics: Dict[str, float] = {}
    controller_footer = {"attempted": 0, "written": 0, "truncated": 0}
    if controller_trace:
        controller_path = output / f"{output_id}_out_guard_controller.csv"
        controller_footer, controller_metrics = parse_controller(
            controller_path,
            {index: int(flow["size"]) for index, flow in enumerate(flows)},
            spec["flow_size_buckets"],
            int(dict(spec["admission"])["full_controller_max_lines"]),
        )
        checks["controller_complete"] = (
            controller_footer["truncated"] == 0
            and controller_footer["attempted"] == controller_footer["written"]
        )
    failures = [name for name, passed in checks.items() if not passed]
    if errors:
        failures.extend(errors)
    metrics: Dict[str, float] = {}
    metrics.update(fct_metrics(fct, spec["flow_size_buckets"]))
    queue_path = output / f"{output_id}_out_queue_stats.txt"
    for metric, scope, value, _count in queue_summary_metrics(queue_path):
        if scope == "all":
            metrics[metric] = float(value)
    for field in GUARD_TOTAL_FIELDS:
        metrics[field] = float(stats[field])
    for field in HOMA_TOTAL_FIELDS:
        metrics[field] = float(stats[field])
    for field in GUARD_SCHEDULER_FIELDS:
        metrics[field] = float(stats[field])
    for field in GUARD_OPTIMIZATION_FIELDS:
        metrics[field] = float(stats[field])
    for field in ZERO_RECOVERY_FIELDS:
        metrics[field] = float(stats[field])
    for field in PFC_PRIORITY_FIELDS:
        values = [int(row[field]) for row in stats["pfc_priority"].values()]
        metrics[f"pfc_{field}"] = float(max(values, default=0) if field == "max_pause_ns" else sum(values))
    metrics["pfc_pause_events"] = float(pfc["pfc_pause_events"])
    metrics["pfc_resume_events"] = float(pfc["pfc_resume_events"])
    row: Dict[str, object] = {
        "workload": preflight_workload["name"], "cdf": preflight_workload["cdf"],
        "seed": seed, "arm": arm, "git_sha": manifest["git_sha"],
        "flow_sha256": trace["sha256"], "flow_count": trace["flow_count"],
        "output_id": output_id, "output_dir": str(output),
        "output_bytes": directory_size(output), "elapsed_seconds": manifest["elapsed_seconds"],
        "passed": not failures, "failures": failures,
        **metrics,
    }
    admission_row = {
        "seed": seed, "arm": arm, "output_id": output_id,
        "passed": not failures, "checks": checks, "failures": failures,
        "mechanism": {field: stats[field] for field in GUARD_TOTAL_FIELDS},
        "drop_recovery": {field: stats[field] for field in ZERO_RECOVERY_FIELDS},
        "pfc": {
            "pause_events": pfc["pfc_pause_events"],
            "resume_events": pfc["pfc_resume_events"],
            "matched_intervals": metrics["pfc_matched_intervals"],
            "cumulative_pause_ns": metrics["pfc_cumulative_pause_ns"],
            "max_pause_ns": metrics["pfc_max_pause_ns"],
        },
        "controller_footer": controller_footer,
        "controller_by_size": controller_metrics,
        "flow_sha256": trace["sha256"],
        "output_bytes": directory_size(output),
    }
    return row, admission_row


def admission_report(
    campaign_dir: Path,
    preflight: Mapping[str, object],
    spec: Mapping[str, object],
) -> Tuple[Dict[str, object], List[Dict[str, object]]]:
    decisions: Dict[str, object] = {}
    rows: List[Dict[str, object]] = []
    admission_seed = int(spec["seeds"][0])
    for workload in preflight["workloads"]:
        if workload["decision"] != "included":
            continue
        arm_rows: List[Dict[str, object]] = []
        arms = tuple(map(str, spec["arms"]))
        for arm in arms:
            try:
                row, admission = analyze_run(
                    campaign_dir, workload, spec, admission_seed, arm)
                rows.append(row)
                arm_rows.append(admission)
            except (AnalysisError, SummaryError, OSError, KeyError, ValueError, StopIteration) as exc:
                arm_rows.append({
                    "seed": admission_seed, "arm": arm,
                    "passed": False, "failures": [str(exc)],
                })
        hashes = {row.get("flow_sha256") for row in arm_rows if row.get("flow_sha256")}
        hash_matched = len(hashes) == 1 and len(arm_rows) == len(arms)
        passed = all(row.get("passed") is True for row in arm_rows) and hash_matched
        decisions[str(workload["name"])] = {
            "passed": passed,
            "flow_hash_matched": hash_matched,
            "arms": arm_rows,
            "decision": "extend_to_five_seeds" if passed else "exclude_without_performance",
        }
    report = {
        "schema_version": 1,
        "gate": f"seed-{admission_seed} mechanisms before performance",
        "preflight_sha256": sha256_file(campaign_dir / "preflight.json"),
        "workloads": decisions,
        "all_selected_passed": all(value["passed"] for value in decisions.values()),
    }
    return report, rows


def aggregate_formal(
    rows: Sequence[Mapping[str, object]],
    comparisons: Sequence[Sequence[str]],
    arms: Sequence[str] = ARMS,
    seeds: Sequence[int] = tuple(range(1, 6)),
) -> Tuple[Dict[str, object], List[Dict[str, object]]]:
    by_workload: Dict[str, object] = {}
    csv_rows: List[Dict[str, object]] = []
    workloads = sorted({str(row["workload"]) for row in rows})
    identity = {
        "seed", "arm", "workload", "cdf", "git_sha", "flow_sha256",
        "output_id", "output_dir", "passed", "failures",
    }
    for workload in workloads:
        workload_rows = [row for row in rows if row["workload"] == workload]
        report: Dict[str, object] = {"by_arm": {}, "paired": {}}
        numeric_by_arm: Dict[str, set[str]] = {}
        for arm in arms:
            arm_rows = [row for row in workload_rows if row["arm"] == arm]
            if len(arm_rows) != len(seeds) or {
                int(row["seed"]) for row in arm_rows
            } != set(map(int, seeds)):
                raise AnalysisError(
                    f"{workload}/{arm} does not contain exactly seeds {list(seeds)}")
            keys = set.intersection(*(
                {key for key, value in row.items() if isinstance(value, (int, float)) and key not in identity}
                for row in arm_rows
            ))
            numeric_by_arm[arm] = keys
            arm_report: Dict[str, object] = {}
            for metric in sorted(keys):
                stats = mean_ci([float(row[metric]) for row in sorted(arm_rows, key=lambda item: item["seed"])])
                arm_report[metric] = stats
                csv_rows.append({
                    "analysis": "arm_mean", "workload": workload,
                    "comparison": arm, "metric": metric, **stats,
                })
            report["by_arm"][arm] = arm_report
        common = set.intersection(*(numeric_by_arm[arm] for arm in arms))
        for pair in comparisons:
            left, right = map(str, pair)
            name = f"{left}_minus_{right}"
            paired: Dict[str, object] = {}
            for metric in sorted(common):
                differences: List[float] = []
                percentages: List[float] = []
                per_seed: List[Dict[str, object]] = []
                for seed in seeds:
                    left_row = next(row for row in workload_rows if row["arm"] == left and row["seed"] == seed)
                    right_row = next(row for row in workload_rows if row["arm"] == right and row["seed"] == seed)
                    if left_row["flow_sha256"] != right_row["flow_sha256"]:
                        raise AnalysisError(f"{workload}/seed{seed}/{name} flow hash mismatch")
                    left_value, right_value = float(left_row[metric]), float(right_row[metric])
                    difference = left_value - right_value
                    differences.append(difference)
                    percent = 100.0 * difference / right_value if right_value != 0 else None
                    if percent is not None:
                        percentages.append(percent)
                    per_seed.append({
                        "seed": seed, "left": left_value, "right": right_value,
                        "difference": difference, "percent_vs_right": percent,
                    })
                difference_stats = mean_ci(differences)
                percent_stats = mean_ci(percentages) if len(percentages) == len(seeds) else None
                paired[metric] = {
                    "per_seed": per_seed, "difference": difference_stats,
                    "percent_vs_right": percent_stats,
                }
                csv_rows.append({
                    "analysis": "paired_difference", "workload": workload,
                    "comparison": name, "metric": metric, **difference_stats,
                })
                if percent_stats:
                    csv_rows.append({
                        "analysis": "paired_percent", "workload": workload,
                        "comparison": name, "metric": metric, **percent_stats,
                    })
            report["paired"][name] = paired
        by_workload[workload] = report
    return by_workload, csv_rows


def write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    fields = sorted({field for row in rows for field in row})
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def export_portable(
    export_dir: Path,
    spec: Mapping[str, object],
    preflight: Mapping[str, object],
    admission: Mapping[str, object],
    formal_rows: Sequence[Mapping[str, object]],
    metric_rows: Sequence[Mapping[str, object]],
) -> None:
    """Write a bounded, path-stable index of a validated campaign."""
    export_dir.mkdir(parents=True, exist_ok=True)
    registry = []
    for row in formal_rows:
        portable = dict(row)
        portable["output_dir"] = f"mix/output/{row['output_id']}"
        registry.append(portable)
    files = {
        "spec.json": spec,
        "preflight.json": preflight,
        "admission.json": admission,
    }
    for name, payload in files.items():
        write_json(export_dir / name, payload)
    write_csv(export_dir / "run_registry.csv", registry)
    write_csv(export_dir / "metrics.csv", metric_rows)
    tracked = sorted(files) + ["metrics.csv", "run_registry.csv"]
    git_shas = sorted({str(row["git_sha"]) for row in formal_rows})
    manifest = {
        "schema_version": 1,
        "description": "Validated five-seed GUARD/HPCC/Homa held-out summary",
        "simulator_git_shas": git_shas,
        "seeds": list(map(int, spec["seeds"])),
        "workloads": sorted({str(row["workload"]) for row in formal_rows}),
        "arms": list(map(str, spec["arms"])),
        "raw_outputs_in_git": False,
        "raw_locator_rule": "run_registry output_dir is relative to the guard repository",
        "files": {
            name: {
                "bytes": (export_dir / name).stat().st_size,
                "sha256": sha256_file(export_dir / name),
            }
            for name in tracked
        },
    }
    write_json(export_dir / "manifest.json", manifest)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign_dir", type=Path)
    parser.add_argument("--admission-only", action="store_true")
    parser.add_argument("--export-dir", type=Path)
    args = parser.parse_args(argv)
    campaign_dir = args.campaign_dir.resolve()
    spec = read_json(campaign_dir / "campaign.json")
    preflight = read_json(campaign_dir / "preflight.json")
    output = campaign_dir / "summary"
    output.mkdir(parents=True, exist_ok=True)
    admission, seed1_rows = admission_report(campaign_dir, preflight, spec)
    write_json(output / "admission.json", admission)
    if seed1_rows:
        write_csv(output / "admission_runs.csv", seed1_rows)
    exclusions = {
        str(workload["name"]): {
            "reason": workload["reason"], "attempts": workload["attempts"]
        }
        for workload in preflight["workloads"] if workload["decision"] == "excluded"
    }
    write_json(output / "exclusions.json", exclusions)
    if args.admission_only:
        passed = sum(bool(row["passed"]) for row in admission["workloads"].values())
        print(f"admission workloads={len(admission['workloads'])} passed={passed}")
        return 0 if admission["all_selected_passed"] else 1
    formal_rows: List[Dict[str, object]] = []
    for workload in preflight["workloads"]:
        name = str(workload["name"])
        if workload["decision"] != "included" or admission["workloads"][name]["passed"] is not True:
            continue
        for seed in map(int, spec["seeds"]):
            for arm in spec["arms"]:
                row, _admission = analyze_run(campaign_dir, workload, spec, seed, arm)
                if row["passed"] is not True:
                    raise AnalysisError(f"formal validation failed: {name}/s{seed}/{arm}: {row['failures']}")
                formal_rows.append(row)
    if not formal_rows:
        raise AnalysisError("no admitted workload has a complete formal matrix")
    aggregates, metric_rows = aggregate_formal(
        formal_rows, spec["comparisons"], tuple(map(str, spec["arms"])),
        tuple(map(int, spec["seeds"])),
    )
    write_csv(output / "general_runs.csv", formal_rows)
    write_csv(output / "general_metrics.csv", metric_rows)
    report = {
        "schema_version": 1,
        "pairing": "left-minus-right, five independent seeds, exact flow SHA within seed",
        "confidence_interval": "two-sided Student t95, df=4",
        "performance": aggregates,
        "admission": admission,
        "exclusions": exclusions,
        "runs": formal_rows,
        "artifacts": {
            "runs_csv": str(output / "general_runs.csv"),
            "metrics_csv": str(output / "general_metrics.csv"),
            "admission_json": str(output / "admission.json"),
            "exclusions_json": str(output / "exclusions.json"),
        },
    }
    write_json(output / "general_report.json", report)
    if args.export_dir:
        export_portable(
            args.export_dir.resolve(), spec, preflight, admission,
            formal_rows, metric_rows,
        )
    print(f"validated {len(formal_rows)} formal runs across {len(aggregates)} workloads")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AnalysisError, SummaryError) as error:
        print(f"error: {error}")
        raise SystemExit(2)
