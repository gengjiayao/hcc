#!/usr/bin/env python3
"""Strict, bounded analysis for paired all-to-all component runs.

The input is an explicit run-index JSON.  Every row names one raw simulator
output and its generated-workload manifest.  The analyzer refuses partial or
mechanistically inactive arms before it writes any result.  Statistics use one
value per simulator seed; paired percentages are calculated within each seed
before the Student-t confidence interval is formed.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import json
import math
import os
from pathlib import Path
import statistics
import sys
from typing import Dict, Iterable, List, Mapping, MutableMapping, Sequence, Tuple

try:
    from experiments.analyze_oflm_churn import parse_lifecycle
    from experiments.summarize_campaign import (
        GUARD_TOTAL_FIELDS,
        PFC_PRIORITY_FIELDS,
        SummaryError,
        confidence_interval,
        parse_guard_stats,
        parse_pfc,
        percentile,
        queue_summary_metrics,
    )
    from experiments.summarize_workload import (
        check_regular_file,
        one_artifact,
        parse_config,
        parse_fct,
        parse_snapshot,
        read_json,
        resolve_snapshot,
        sha256_file,
        summarize,
        validate_completions,
        validate_manifest,
    )
except ModuleNotFoundError:  # Direct execution from experiments/.
    from analyze_oflm_churn import parse_lifecycle
    from summarize_campaign import (
        GUARD_TOTAL_FIELDS,
        PFC_PRIORITY_FIELDS,
        SummaryError,
        confidence_interval,
        parse_guard_stats,
        parse_pfc,
        percentile,
        queue_summary_metrics,
    )
    from summarize_workload import (
        check_regular_file,
        one_artifact,
        parse_config,
        parse_fct,
        parse_snapshot,
        read_json,
        resolve_snapshot,
        sha256_file,
        summarize,
        validate_completions,
        validate_manifest,
    )


ARMS = ("guard", "hpcc", "receiver_only")
CC_MODES = {"guard": 11, "hpcc": 3, "receiver_only": 13}
DEFAULT_SOURCE_LIMIT_BYTES = 100 * 1024 * 1024
ZERO_REQUIRED_FIELDS = (
    "recovery_nacks_generated", "recovery_nacks_received",
    "irn_nacks_generated", "irn_nacks_received", "irn_retransmit_packets",
    "irn_retransmit_bytes", "timeout_recoveries", "switch_drops_ingress",
    "switch_drops_egress", "switch_drops_total",
)

METRIC_UNITS = {
    "communication_phase_cct_us": "us",
    "start_span_us": "us",
    "fct_mean_us": "us",
    "fct_p50_us": "us",
    "fct_p95_us": "us",
    "fct_p99_us": "us",
    "slowdown_mean": "ratio",
    "slowdown_p50": "ratio",
    "slowdown_p95": "ratio",
    "slowdown_p99": "ratio",
    "flow_goodput_jain": "ratio",
    "receiver_goodput_jain": "ratio",
    "queue_sample_count": "samples",
    "queue_bytes_mean": "bytes",
    "queue_bytes_p95": "bytes",
    "queue_bytes_p99": "bytes",
    "queue_bytes_max": "bytes",
    **{field: "bytes" if field == "irn_retransmit_bytes" else "count"
       for field in GUARD_TOTAL_FIELDS},
    "switch_drops_ingress": "packets",
    "switch_drops_egress": "packets",
    "switch_drops_total": "packets",
    "pfc_pause_events": "events",
    "pfc_resume_events": "events",
    "pfc_matched_intervals": "intervals",
    "pfc_cumulative_pause_ns": "ns",
    "pfc_max_pause_ns": "ns",
    "pfc_unmatched_pauses": "count",
    "pfc_unmatched_resumes": "count",
    "lifecycle_rows": "flows",
    "lifecycle_proactive_rows": "flows",
    "output_bytes": "bytes",
}
PER_RUN_PREFIX = (
    "arm", "seed", "output_id", "output_dir", "manifest", "flow_sha256",
    "flow_count", "completed_flow_count", "priority_group", "flow_size_bytes",
)
PER_RUN_FIELDS = PER_RUN_PREFIX + tuple(METRIC_UNITS)
PERCENT_METRICS = (
    "communication_phase_cct_us", "fct_mean_us", "fct_p50_us", "fct_p95_us",
    "fct_p99_us", "slowdown_mean", "slowdown_p50", "slowdown_p95",
    "slowdown_p99", "flow_goodput_jain", "receiver_goodput_jain",
    "queue_bytes_mean", "queue_bytes_p95", "queue_bytes_p99", "queue_bytes_max",
)
PAIRINGS = (
    ("guard_vs_hpcc", "guard", "hpcc"),
    ("guard_vs_receiver_only", "guard", "receiver_only"),
    ("receiver_only_vs_hpcc", "receiver_only", "hpcc"),
)
CI_FIELDS = (
    "arm", "metric", "unit", "n", "mean", "ci95_low", "ci95_high",
    "ci95_half_width",
)
PAIR_FIELDS = (
    "comparison", "arm_a", "arm_b", "metric", "unit", "n", "mean",
    "ci95_low", "ci95_high", "ci95_half_width",
)


def _integer(value: object, label: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise SummaryError(f"{label} must be an integer") from exc
    return result


def _positive_integer(value: object, label: str) -> int:
    result = _integer(value, label)
    if result <= 0:
        raise SummaryError(f"{label} must be positive")
    return result


def _resolve(index_path: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise SummaryError(f"{label} must be a nonempty path string")
    path = Path(value)
    return (index_path.parent / path).resolve() if not path.is_absolute() else path.resolve()


def _directory_bytes(directory: Path) -> int:
    if not directory.is_dir():
        raise SummaryError(f"output directory does not exist: {directory}")
    total = 0
    for path in directory.iterdir():
        if path.is_file():
            total += path.stat().st_size
    return total


def _jain(values: Sequence[float], label: str) -> float:
    if not values or any(value <= 0 or not math.isfinite(value) for value in values):
        raise SummaryError(f"{label} requires finite positive values")
    denominator = len(values) * sum(value * value for value in values)
    if denominator <= 0:
        raise SummaryError(f"{label} has a non-positive denominator")
    return sum(values) ** 2 / denominator


def validate_flow_design(
    flows: Sequence[Mapping[str, object]], hosts: int, expected_flows: int,
    expected_pg: int, expected_size: int, max_jitter_us: float,
) -> None:
    """Require one fixed-size PG flow for every ordered host pair."""
    if len(flows) != expected_flows or expected_flows != hosts * (hosts - 1):
        raise SummaryError("all-to-all flow count must equal hosts*(hosts-1)")
    observed_pairs = [(int(flow["src"]), int(flow["dst"])) for flow in flows]
    expected_pairs = {(src, dst) for src in range(hosts) for dst in range(hosts) if src != dst}
    if len(set(observed_pairs)) != expected_flows or set(observed_pairs) != expected_pairs:
        raise SummaryError("traffic must contain every ordered pair exactly once")
    priorities = {int(flow["pg"]) for flow in flows}
    if priorities != {expected_pg}:
        raise SummaryError(f"all flows must use priority group {expected_pg}, got {priorities}")
    sizes = {int(flow["size"]) for flow in flows}
    if sizes != {expected_size}:
        raise SummaryError(f"all flows must have size {expected_size}, got {sizes}")
    starts = [int(flow["start_ns"]) for flow in flows]
    if (max(starts) - min(starts)) / 1000.0 > max_jitter_us + 0.002:
        raise SummaryError("traffic start span exceeds the preregistered jitter bound")


def validate_mechanism(
    arm: str, stats: Mapping[str, object], expected_flows: int,
    lifecycle_rows: Sequence[Mapping[str, object]],
) -> None:
    """Require each component arm to exercise exactly its intended controller."""
    for field in ZERO_REQUIRED_FIELDS:
        if int(stats[field]) != 0:
            raise SummaryError(f"{arm}: {field} must be zero, got {stats[field]}")

    grants = int(stats["grants_sent"]) + int(stats["grants_received"])
    hpcc = (
        int(stats["hpcc_feedback_updates"]), int(stats["hpcc_valid_feedback"]),
        int(stats["hpcc_rate_updates_applied"]), int(stats["hpcc_actual_rate_changes"]),
    )
    binding = (
        int(stats["reactive_binding_updates"]),
        int(stats["reactive_binding_rate_changes"]),
        int(stats["grant_binding_updates"]),
    )
    registrations = int(stats["registrations"])
    selected = int(stats["selected_registrations"])
    proactive = int(stats["proactive_releases"])

    if arm == "guard":
        if grants <= 0 or min(hpcc) <= 0 or min(binding) <= 0:
            raise SummaryError("guard must exercise grants, valid HPCC changes, and both bindings")
        if (registrations, selected, proactive) != (expected_flows,) * 3:
            raise SummaryError("guard must select, register, and proactively release every flow")
        if not int(stats["int_hops_before_strip"]) > int(stats["int_hops_after_strip"]) > 0:
            raise SummaryError("guard must show bounded last-hop INT stripping with fabric hops retained")
        if int(stats["int_records_stripped"]) <= 0:
            raise SummaryError("guard must report stripped INT records")
    elif arm == "hpcc":
        if grants != 0 or registrations != 0 or min(hpcc) <= 0:
            raise SummaryError("hpcc must have no grants/registration and actual HPCC changes")
        if any(binding):
            raise SummaryError("hpcc must not use GUARD binding counters")
    elif arm == "receiver_only":
        if grants <= 0 or any(hpcc) or any(binding):
            raise SummaryError("receiver-only must use grants and have zero HPCC/binding activity")
        if (registrations, selected, proactive) != (expected_flows,) * 3:
            raise SummaryError("receiver-only must select, register, and proactively release every flow")
    else:
        raise SummaryError(f"unknown arm: {arm}")

    expected_lifecycle = 0 if arm == "hpcc" else expected_flows
    if len(lifecycle_rows) != expected_lifecycle:
        raise SummaryError(
            f"{arm}: lifecycle rows {len(lifecycle_rows)} != {expected_lifecycle}"
        )
    if lifecycle_rows:
        reasons = Counter(str(row["release_reason"]) for row in lifecycle_rows)
        if reasons != Counter({"proactive": expected_flows}):
            raise SummaryError(f"{arm}: lifecycle must contain only proactive releases")


def _validate_pfc(raw: Mapping[str, object], stats: Mapping[str, object], pg: int) -> None:
    raw_priorities = raw["pfc_event_priority"]
    summary_priorities = stats["pfc_priority"]
    if not isinstance(raw_priorities, dict) or not isinstance(summary_priorities, dict):
        raise SummaryError("PFC priority data has an invalid type")
    if set(raw_priorities) - set(summary_priorities):
        raise SummaryError("raw PFC trace contains a priority absent from stats")
    for priority, counts in raw_priorities.items():
        for field in ("pause_count", "resume_count"):
            if int(counts[field]) != int(summary_priorities[priority][field]):
                raise SummaryError(f"PFC q{priority} {field} differs between raw trace and stats")
        if priority != pg and (int(counts["pause_count"]) or int(counts["resume_count"])):
            raise SummaryError(f"PFC activity on q{priority} conflicts with data priority q{pg}")
    if int(raw["pfc_pause_events"]) != sum(
        int(values["pause_count"]) for values in summary_priorities.values()
    ):
        raise SummaryError("aggregate raw pause count differs from stats")
    if int(raw["pfc_resume_events"]) != sum(
        int(values["resume_count"]) for values in summary_priorities.values()
    ):
        raise SummaryError("aggregate raw resume count differs from stats")


def _completion_metrics(
    completions: Sequence[Mapping[str, object]], hosts: int,
) -> Dict[str, float]:
    starts = [int(row["start_ns"]) for row in completions]
    finishes = [int(row["finish_ns"]) for row in completions]
    fct = [float(row["fct_us"]) for row in completions]
    slowdown = [float(row["slowdown"]) for row in completions]
    flow_rates = [int(row["size"]) * 8 / int(row["duration_ns"]) for row in completions]
    by_receiver: MutableMapping[int, List[Mapping[str, object]]] = defaultdict(list)
    for row in completions:
        by_receiver[int(row["dst"])].append(row)
    if set(by_receiver) != set(range(hosts)) or any(
        len(rows) != hosts - 1 for rows in by_receiver.values()
    ):
        raise SummaryError("completion rows do not contain hosts-1 flows per receiver")
    receiver_rates = []
    for rows in by_receiver.values():
        span = max(int(row["finish_ns"]) for row in rows) - min(
            int(row["start_ns"]) for row in rows
        )
        if span <= 0:
            raise SummaryError("receiver completion span must be positive")
        receiver_rates.append(sum(int(row["size"]) for row in rows) * 8 / span)
    return {
        "communication_phase_cct_us": (max(finishes) - min(starts)) / 1000.0,
        "start_span_us": (max(starts) - min(starts)) / 1000.0,
        "fct_mean_us": statistics.fmean(fct),
        "fct_p50_us": percentile(fct, 50),
        "fct_p95_us": percentile(fct, 95),
        "fct_p99_us": percentile(fct, 99),
        "slowdown_mean": statistics.fmean(slowdown),
        "slowdown_p50": percentile(slowdown, 50),
        "slowdown_p95": percentile(slowdown, 95),
        "slowdown_p99": percentile(slowdown, 99),
        "flow_goodput_jain": _jain(flow_rates, "flow goodput Jain index"),
        "receiver_goodput_jain": _jain(receiver_rates, "receiver goodput Jain index"),
    }


def _artifact_hashes(output_dir: Path, lifecycle: Path | None) -> Dict[str, str]:
    paths = {
        "config": output_dir / "config.txt",
        "flow": resolve_snapshot(output_dir, None),
        "fct": one_artifact(output_dir, "_out_fct.txt"),
        "queue": one_artifact(output_dir, "_out_queue_stats.txt"),
        "pfc": one_artifact(output_dir, "_out_pfc.txt"),
        "guard_stats": one_artifact(output_dir, "_out_guard_stats.txt"),
    }
    if lifecycle is not None:
        paths["lifecycle"] = lifecycle
    return {name: sha256_file(path) for name, path in paths.items()}


def analyze_run(
    index_path: Path, entry: Mapping[str, object], design: Mapping[str, object],
) -> Tuple[Dict[str, object], Dict[str, object]]:
    arm = str(entry.get("arm", ""))
    seed = _positive_integer(entry.get("seed"), "run seed")
    output_dir = _resolve(index_path, entry.get("output_dir"), "output_dir")
    manifest_path = _resolve(index_path, entry.get("manifest"), "manifest")
    source_bytes = _directory_bytes(output_dir)

    # Reuse the general bounded validator first; this verifies manifest/hash,
    # preflight limits, exact completions, queue, PFC, and raw stats structure.
    summarize(output_dir, manifest_path)
    manifest = read_json(manifest_path)
    parameters = manifest.get("parameters")
    if manifest.get("workload") != "all-to-all" or not isinstance(parameters, dict):
        raise SummaryError("manifest must describe an all-to-all workload")

    hosts = _positive_integer(design.get("hosts"), "design hosts")
    expected_flows = _positive_integer(design.get("flows"), "design flows")
    expected_pg = _integer(design.get("priority_group"), "design priority_group")
    expected_size = _positive_integer(design.get("flow_size_bytes"), "design flow_size_bytes")
    try:
        max_jitter_us = float(design.get("max_jitter_us"))
    except (TypeError, ValueError) as exc:
        raise SummaryError("design max_jitter_us must be numeric") from exc
    if max_jitter_us < 0 or not math.isfinite(max_jitter_us):
        raise SummaryError("design max_jitter_us must be finite and nonnegative")

    for key, expected in (
        ("hosts", hosts), ("priority_group", expected_pg),
        ("flow_bytes", expected_size), ("seed", seed),
    ):
        if _integer(parameters.get(key), f"manifest parameters.{key}") != expected:
            raise SummaryError(f"manifest parameter {key} differs from the run index")
    if abs(float(parameters.get("all_to_all_jitter_us", -1)) - max_jitter_us) > 1e-12:
        raise SummaryError("manifest all-to-all jitter differs from the run index")

    snapshot_path = resolve_snapshot(output_dir, None)
    flows, snapshot = parse_snapshot(snapshot_path, hosts, expected_flows)
    workload, _ = validate_manifest(manifest, snapshot)
    if workload != "all-to-all":
        raise SummaryError("private snapshot does not validate as all-to-all")
    validate_flow_design(flows, hosts, expected_flows, expected_pg, expected_size, max_jitter_us)

    config = parse_config(output_dir / "config.txt")
    expected_topology = str(design.get("topology_file", ""))
    checks = {
        "CC_MODE": CC_MODES.get(arm), "RANDOM_SEED": seed, "ENABLE_PFC": 1,
        "ENABLE_IRN": 0,
    }
    if expected_topology:
        checks["TOPOLOGY_FILE"] = expected_topology
    for key, expected in checks.items():
        if expected is None or str(config.get(key, "")) != str(expected):
            raise SummaryError(f"{arm}/seed{seed}: config {key} != {expected}")
    if Path(config.get("FLOW_FILE", "")).name != snapshot_path.name:
        raise SummaryError("config FLOW_FILE does not identify the private snapshot")

    fct_path = one_artifact(output_dir, "_out_fct.txt")
    completions = parse_fct(fct_path)
    validate_completions(flows, completions, hosts)
    stats_path = one_artifact(output_dir, "_out_guard_stats.txt")
    stats = parse_guard_stats(stats_path)
    pfc = parse_pfc(one_artifact(output_dir, "_out_pfc.txt"))
    _validate_pfc(pfc, stats, expected_pg)

    lifecycle_path: Path | None = None
    lifecycle_rows: List[Mapping[str, object]] = []
    lifecycle_enabled = _integer(config.get("GUARD_LIFECYCLE_TRACE", -1), "lifecycle config")
    if arm == "hpcc":
        if lifecycle_enabled != 0 or list(output_dir.glob("*_out_guard_lifecycle.csv")):
            raise SummaryError("hpcc must have lifecycle tracing disabled and no lifecycle artifact")
    else:
        if lifecycle_enabled != 1:
            raise SummaryError(f"{arm} must have lifecycle tracing enabled")
        limit = _positive_integer(
            config.get("GUARD_LIFECYCLE_TRACE_MAX_LINES"), "lifecycle trace line limit"
        )
        if limit < expected_flows:
            raise SummaryError("lifecycle trace line limit cannot hold all expected rows")
        lifecycle_path = one_artifact(output_dir, "_out_guard_lifecycle.csv")
        lifecycle_rows = parse_lifecycle(lifecycle_path)
        if any(int(row["size_bytes"]) != expected_size for row in lifecycle_rows):
            raise SummaryError("lifecycle contains an unexpected flow size")
        receiver_counts = Counter(int(row["receiver_node"]) for row in lifecycle_rows)
        if receiver_counts != Counter({receiver: hosts - 1 for receiver in range(hosts)}):
            raise SummaryError("lifecycle does not contain hosts-1 flows per receiver")
    validate_mechanism(arm, stats, expected_flows, lifecycle_rows)

    queue_values = {
        name: value for name, _scope, value, _count
        in queue_summary_metrics(one_artifact(output_dir, "_out_queue_stats.txt"))
    }
    metrics: Dict[str, object] = _completion_metrics(completions, hosts)
    metrics.update(queue_values)
    metrics.update({field: int(stats[field]) for field in GUARD_TOTAL_FIELDS})
    metrics.update({
        "switch_drops_ingress": int(stats["switch_drops_ingress"]),
        "switch_drops_egress": int(stats["switch_drops_egress"]),
        "switch_drops_total": int(stats["switch_drops_total"]),
        "pfc_pause_events": int(pfc["pfc_pause_events"]),
        "pfc_resume_events": int(pfc["pfc_resume_events"]),
        "pfc_matched_intervals": int(stats["pfc_matched_intervals"]),
        "pfc_cumulative_pause_ns": int(stats["pfc_cumulative_pause_ns"]),
        "pfc_max_pause_ns": int(stats["pfc_max_pause_ns"]),
        "pfc_unmatched_pauses": int(stats["pfc_unmatched_pauses"]),
        "pfc_unmatched_resumes": int(stats["pfc_unmatched_resumes"]),
        "lifecycle_rows": len(lifecycle_rows),
        "lifecycle_proactive_rows": sum(
            str(row["release_reason"]) == "proactive" for row in lifecycle_rows
        ),
        "output_bytes": source_bytes,
    })
    output_id = output_dir.name
    row: Dict[str, object] = {
        "arm": arm, "seed": seed, "output_id": output_id,
        "output_dir": str(output_dir), "manifest": str(manifest_path),
        "flow_sha256": snapshot["sha256"], "flow_count": len(flows),
        "completed_flow_count": len(completions), "priority_group": expected_pg,
        "flow_size_bytes": expected_size,
        **metrics,
    }
    audit = {
        "arm": arm,
        "seed": seed,
        "output_id": output_id,
        "output_dir": str(output_dir),
        "manifest": str(manifest_path),
        "flow_sha256": snapshot["sha256"],
        "source_bytes": source_bytes,
        "artifact_sha256": _artifact_hashes(output_dir, lifecycle_path),
        "checks": {
            "generated_and_completed": f"{len(flows)}/{len(completions)}",
            "all_ordered_pairs_once": True,
            "priority_group": expected_pg,
            "flow_size_bytes": expected_size,
            "config_and_seed_match": True,
            "raw_pfc_matches_stats": True,
            "zero_drops_and_recovery": True,
            "controller_mechanism_active": True,
            "lifecycle_complete_and_untruncated": arm == "hpcc" or len(lifecycle_rows) == expected_flows,
        },
    }
    return row, audit


def _ci(values: Sequence[float]) -> Tuple[float, float, float, float]:
    mean, low, high = confidence_interval(values)
    half = high - mean if len(values) > 1 else math.nan
    return mean, low, high, half


def aggregate_arm_rows(rows: Sequence[Mapping[str, object]]) -> List[Dict[str, object]]:
    result: List[Dict[str, object]] = []
    for arm in ARMS:
        selected = sorted((row for row in rows if row["arm"] == arm), key=lambda row: int(row["seed"]))
        if not selected:
            raise SummaryError(f"no rows for arm {arm}")
        for metric, unit in METRIC_UNITS.items():
            values = [float(row[metric]) for row in selected]
            mean, low, high, half = _ci(values)
            result.append({
                "arm": arm, "metric": metric, "unit": unit, "n": len(values),
                "mean": mean, "ci95_low": low, "ci95_high": high,
                "ci95_half_width": half,
            })
    return result


def paired_rows(
    rows: Sequence[Mapping[str, object]], percent: bool = False,
) -> List[Dict[str, object]]:
    by_arm_seed = {(str(row["arm"]), int(row["seed"])): row for row in rows}
    seeds = sorted({int(row["seed"]) for row in rows})
    metrics = PERCENT_METRICS if percent else tuple(METRIC_UNITS)
    result: List[Dict[str, object]] = []
    for comparison, arm_a, arm_b in PAIRINGS:
        for metric in metrics:
            values = []
            for seed in seeds:
                a = float(by_arm_seed[(arm_a, seed)][metric])
                b = float(by_arm_seed[(arm_b, seed)][metric])
                if percent:
                    if b == 0:
                        raise SummaryError(
                            f"cannot form paired percent for {comparison}/{metric}: zero denominator"
                        )
                    values.append((a - b) / b * 100.0)
                else:
                    values.append(a - b)
            mean, low, high, half = _ci(values)
            result.append({
                "comparison": comparison, "arm_a": arm_a, "arm_b": arm_b,
                "metric": metric, "unit": "percent" if percent else METRIC_UNITS[metric],
                "n": len(values), "mean": mean, "ci95_low": low,
                "ci95_high": high, "ci95_half_width": half,
            })
    return result


def validate_index(index: Mapping[str, object]) -> Tuple[Mapping[str, object], List[Mapping[str, object]]]:
    if index.get("schema_version") != 1:
        raise SummaryError("run index schema_version must be 1")
    design = index.get("design")
    runs = index.get("runs")
    if not isinstance(design, dict) or not isinstance(runs, list):
        raise SummaryError("run index must contain design object and runs array")
    seeds_value = design.get("seeds")
    if not isinstance(seeds_value, list) or not seeds_value:
        raise SummaryError("design seeds must be a nonempty array")
    seeds = tuple(_positive_integer(seed, "design seed") for seed in seeds_value)
    if len(set(seeds)) != len(seeds):
        raise SummaryError("design seeds must be unique")
    expected = {(arm, seed) for arm in ARMS for seed in seeds}
    observed: List[Tuple[str, int]] = []
    typed_runs: List[Mapping[str, object]] = []
    for value in runs:
        if not isinstance(value, dict):
            raise SummaryError("every run-index entry must be an object")
        observed.append((str(value.get("arm", "")), _positive_integer(value.get("seed"), "run seed")))
        typed_runs.append(value)
    if len(observed) != len(set(observed)):
        raise SummaryError("run index contains duplicate arm/seed entries")
    if set(observed) != expected:
        raise SummaryError(f"run index arms/seeds differ; missing={sorted(expected-set(observed))}")
    return design, typed_runs


def _atomic_csv(path: Path, fieldnames: Sequence[str], rows: Iterable[Mapping[str, object]]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, path)


def analyze_index(
    index_path: Path, analysis_dir: Path, source_limit_bytes: int,
) -> Mapping[str, object]:
    index_path = index_path.resolve()
    check_regular_file(index_path, 1024 * 1024)
    index = read_json(index_path)
    design, entries = validate_index(index)
    rows: List[Dict[str, object]] = []
    audits: List[Dict[str, object]] = []
    for entry in sorted(entries, key=lambda item: (int(item["seed"]), ARMS.index(str(item["arm"])))):
        row, audit = analyze_run(index_path, entry, design)
        rows.append(row)
        audits.append(audit)

    expected_seeds = sorted(_positive_integer(seed, "design seed") for seed in design["seeds"])
    hashes: Dict[int, str] = {}
    for seed in expected_seeds:
        selected = [row for row in rows if int(row["seed"]) == seed]
        flow_hashes = {str(row["flow_sha256"]) for row in selected}
        manifests = {str(row["manifest"]) for row in selected}
        if len(flow_hashes) != 1 or len(manifests) != 1:
            raise SummaryError(f"seed {seed}: arms do not share one manifest and flow hash")
        hashes[seed] = next(iter(flow_hashes))
    if len(set(hashes.values())) != len(hashes):
        raise SummaryError("different simulator seeds must have distinct traffic hashes")
    source_total = sum(int(row["output_bytes"]) for row in rows)
    if source_total > source_limit_bytes:
        raise SummaryError(
            f"raw source exceeds aggregate bound ({source_total} > {source_limit_bytes})"
        )

    arm_ci = aggregate_arm_rows(rows)
    paired = paired_rows(rows, percent=False)
    paired_percent = paired_rows(rows, percent=True)
    output_names = {
        "per_run": "per_run.csv", "arm_ci": "arm_ci.csv",
        "paired_difference": "paired_difference.csv",
        "paired_percent": "paired_percent.csv", "admission": "admission.json",
    }
    admission: Dict[str, object] = {
        "schema_version": 1,
        "status": "admitted",
        "completion_semantics": "all-to-all communication-phase CCT",
        "index": str(index_path),
        "design": design,
        "source_limit_bytes": source_limit_bytes,
        "source_bytes": source_total,
        "run_count": len(rows),
        "seed_count": len(expected_seeds),
        "paired_flow_sha256": {str(seed): hashes[seed] for seed in expected_seeds},
        "paired_percent_definition": "per-seed (arm_a-arm_b)/arm_b*100, then t95 across seeds",
        "pfc_transition_accounting": {
            f"{row['arm']}/seed{row['seed']}": {
                "unmatched_pauses": int(row["pfc_unmatched_pauses"]),
                "unmatched_resumes": int(row["pfc_unmatched_resumes"]),
            }
            for row in rows
            if int(row["pfc_unmatched_pauses"]) or int(row["pfc_unmatched_resumes"])
        },
        "checks": {
            "exact_arm_seed_matrix": True,
            "same_seed_manifest_and_hash": True,
            "different_seed_hashes": True,
            "all_runs_complete": True,
            "all_flows_pg4": int(design["priority_group"]) == 4,
            "controller_mechanisms_active": True,
            "no_switch_drop_or_recovery": True,
            "unmatched_pfc_transitions_preserved_not_suppressed": True,
            "raw_source_within_bound": True,
        },
        "outputs": output_names,
        "runs": audits,
    }

    analysis_dir.mkdir(parents=True, exist_ok=True)
    # Admission is all-or-nothing: files are written only after every raw run,
    # pairing, metric, and source-size check has passed.
    _atomic_csv(analysis_dir / output_names["per_run"], PER_RUN_FIELDS, rows)
    _atomic_csv(analysis_dir / output_names["arm_ci"], CI_FIELDS, arm_ci)
    _atomic_csv(analysis_dir / output_names["paired_difference"], PAIR_FIELDS, paired)
    _atomic_csv(analysis_dir / output_names["paired_percent"], PAIR_FIELDS, paired_percent)
    _atomic_json(analysis_dir / output_names["admission"], admission)
    return admission


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Strictly validate and aggregate paired all-to-all component runs"
    )
    parser.add_argument("run_index", type=Path, help="explicit JSON index of raw runs")
    parser.add_argument(
        "--analysis-dir", type=Path,
        help="output directory (default: RUN_INDEX parent/analysis)",
    )
    parser.add_argument(
        "--source-byte-limit", type=int, default=DEFAULT_SOURCE_LIMIT_BYTES,
        help="aggregate raw-source limit (default: 100 MiB)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.source_byte_limit <= 0:
        print("ERROR: --source-byte-limit must be positive", file=sys.stderr)
        return 2
    analysis_dir = (args.analysis_dir or args.run_index.parent / "analysis").resolve()
    try:
        admission = analyze_index(args.run_index, analysis_dir, args.source_byte_limit)
    except (OSError, KeyError, TypeError, ValueError, SummaryError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"admitted {admission['run_count']} runs across {admission['seed_count']} paired seeds")
    print(f"raw source bytes: {admission['source_bytes']}/{admission['source_limit_bytes']}")
    print(f"analysis: {analysis_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
