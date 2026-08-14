#!/usr/bin/env python3
"""Validate and aggregate frozen GUARD/HPCC/Homa collective runs."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Dict, List, Mapping, Sequence

try:
    from experiments.run_campaign import sha256_file, write_json
    from experiments.summarize_campaign import SummaryError
except ModuleNotFoundError:
    from run_campaign import sha256_file, write_json
    from summarize_campaign import SummaryError


ARMS = ("guard", "hpcc", "homa")
SEEDS = (1, 2, 3, 4, 5)
CC_MODES = {"guard": 11, "hpcc": 3, "homa": 12}
T95_DF4 = 2.7764451051977987
ZERO_GENERIC_RECOVERY = (
    "recovery_nacks_generated", "recovery_nacks_received", "irn_nacks_generated",
    "irn_nacks_received", "irn_retransmit_packets", "irn_retransmit_bytes",
    "timeout_recoveries",
)
METRICS = (
    "trace_completion_time_us", "fct_us_mean", "fct_us_p95", "fct_us_p99",
    "slowdown_mean", "slowdown_p95", "slowdown_p99", "aggregate_goodput_gbps",
    "queue_bytes_mean", "queue_bytes_p95", "queue_bytes_p99", "queue_bytes_max",
    "grants_sent", "grants_received", "hpcc_actual_rate_changes",
    "hpcc_valid_feedback", "reactive_binding_updates", "pfc_matched_intervals",
    "pfc_cumulative_pause_ns", "switch_drops_total", "homa_data_packets",
    "homa_data_bytes", "homa_grants_sent", "homa_grants_received",
    "homa_retransmit_packets", "homa_resends_sent", "homa_resends_received",
    "homa_completion_notices_sent", "homa_completion_notices_received",
    "homa_messages_tracked", "homa_messages_completed", "homa_max_pending_messages",
    "homa_completed_message_ids", "homa_duplicate_data_after_completion",
    "homa_completion_notices_replayed",
    *ZERO_GENERIC_RECOVERY,
)


def metric_map(summary: Mapping[str, object]) -> Dict[tuple[str, str], float]:
    return {
        (str(row["metric"]), str(row["scope"])): float(row["value"])
        for row in summary["metrics"]
    }


def parse_config(path: Path) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="strict").splitlines():
        fields = line.split()
        if len(fields) >= 2:
            result[fields[0]] = fields[1]
    return result


def mean_ci(values: Sequence[float]) -> Dict[str, float]:
    if len(values) != 5:
        raise SummaryError(f"formal confidence interval requires five seeds, got {len(values)}")
    mean = statistics.fmean(values)
    half = T95_DF4 * statistics.stdev(values) / math.sqrt(5)
    return {
        "n": 5, "mean": mean, "ci95_low": mean - half,
        "ci95_high": mean + half, "ci95_half_width": half,
    }


def validate_mechanism(
    arm: str, values: Mapping[str, float], expected_flows: int, used_priorities: int,
) -> List[str]:
    failures: List[str] = []
    grants = values["grants_sent"] + values["grants_received"]
    if arm == "guard":
        if grants <= 0:
            failures.append("guard_grants_zero")
        if values["hpcc_actual_rate_changes"] <= 0:
            failures.append("guard_actual_hpcc_changes_zero")
        if values["homa_data_packets"] != 0:
            failures.append("guard_homa_activity_nonzero")
    elif arm == "hpcc":
        if grants != 0:
            failures.append("hpcc_grants_nonzero")
        if values["hpcc_actual_rate_changes"] <= 0:
            failures.append("hpcc_actual_rate_changes_zero")
        if values["homa_data_packets"] != 0:
            failures.append("hpcc_homa_activity_nonzero")
    else:
        if grants != 0 or values["hpcc_actual_rate_changes"] != 0:
            failures.append("homa_guard_or_hpcc_activity_nonzero")
        if values["homa_messages_completed"] != expected_flows:
            failures.append("homa_completed_message_count_mismatch")
        if values["homa_messages_tracked"] != expected_flows:
            failures.append("homa_tracked_message_count_mismatch")
        if values["homa_completed_message_ids"] != expected_flows:
            failures.append("homa_completed_tombstone_count_mismatch")
        if values["homa_data_packets"] <= 0 or values["homa_grants_sent"] <= 0:
            failures.append("homa_native_data_or_grants_zero")
        if values["homa_completion_notices_received"] != expected_flows:
            failures.append("homa_completion_notice_receive_count_mismatch")
        if values["homa_completion_notices_replayed"] != values["homa_duplicate_data_after_completion"]:
            failures.append("homa_completion_replay_count_mismatch")
        expected_sent = expected_flows + values["homa_completion_notices_replayed"]
        if values["homa_completion_notices_sent"] != expected_sent:
            failures.append("homa_completion_notice_send_count_mismatch")
        if used_priorities < 2:
            failures.append("homa_multiple_priorities_not_exercised")
    return failures


def analyze_run(run_manifest: Path, expected_flows: int) -> tuple[Dict[str, object], Dict[str, object]]:
    manifest = json.loads(run_manifest.read_text(encoding="utf-8"))
    if manifest.get("status") != "completed":
        raise SummaryError(f"run is not completed: {run_manifest}")
    summary_path = Path(manifest["summary_json"])
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    values_by_scope = metric_map(summary)
    values = {metric: values_by_scope.get((metric, "all"), 0.0) for metric in METRICS}
    output = Path(manifest["output_dir"])
    config = parse_config(output / "config.txt")
    arm, seed = str(manifest["arm"]), int(manifest["seed"])
    used_priorities = sum(
        values_by_scope.get(("homa_data_packets", f"priority:{priority}"), 0) > 0
        for priority in range(8)
    )
    failures: List[str] = []
    if summary.get("status") != "validated_complete":
        failures.append("summary_not_validated_complete")
    if int(summary["validation"]["completed_flow_count"]) != expected_flows:
        failures.append("completed_flow_count_mismatch")
    flow_sha = str(manifest["traffic"]["flow_sha256"])
    if summary["provenance"]["flow_sha256"] != flow_sha:
        failures.append("summary_flow_hash_mismatch")
    if config.get("CC_MODE") != str(CC_MODES[arm]) or config.get("RANDOM_SEED") != str(seed):
        failures.append("config_arm_or_seed_mismatch")
    if config.get("MONITOR_PROFILE") != "bulk":
        failures.append("monitor_profile_not_bulk")
    if values["switch_drops_total"] != 0:
        failures.append("switch_drops_nonzero")
    for field in ZERO_GENERIC_RECOVERY:
        if values[field] != 0:
            failures.append(f"{field}_nonzero")
    failures.extend(validate_mechanism(arm, values, expected_flows, used_priorities))
    row: Dict[str, object] = {
        "workload": manifest["workload"], "seed": seed, "arm": arm,
        "output_id": manifest["output_id"], "output_dir": str(output),
        "flow_sha256": flow_sha, "simulator_sha": manifest["git_sha"],
        "expected_flows": expected_flows, "used_homa_priorities": used_priorities,
        "output_bytes": manifest["output_bytes"], **values,
    }
    audit = {
        "workload": manifest["workload"], "seed": seed, "arm": arm,
        "output_id": manifest["output_id"], "flow_sha256": flow_sha,
        "passed": not failures, "failures": failures,
    }
    return row, audit


def analyze(results: Path, seed1_only: bool) -> tuple[List[Dict[str, object]], Dict[str, object]]:
    preflight_path = results / "preflight.json"
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    campaign = json.loads((results / "campaign.json").read_text(encoding="utf-8"))
    expected = {
        name: int(config["expected_flows"])
        for name, config in campaign["workloads"].items()
    }
    rows: List[Dict[str, object]] = []
    audits: List[Dict[str, object]] = []
    seeds = (1,) if seed1_only else SEEDS
    for workload in campaign["workloads"]:
        for seed in seeds:
            for arm in ARMS:
                run_manifest = results / "runs" / workload / f"seed{seed}" / arm / "manifest.json"
                row, audit = analyze_run(run_manifest, expected[workload])
                rows.append(row)
                audits.append(audit)
            seed_hashes = {row["flow_sha256"] for row in rows if row["workload"] == workload and row["seed"] == seed}
            if len(seed_hashes) != 1:
                raise SummaryError(f"{workload}/seed{seed}: arm flow hashes differ")
    if not seed1_only:
        for workload in campaign["workloads"]:
            hashes = {row["flow_sha256"] for row in rows if row["workload"] == workload}
            if len(hashes) != 5:
                raise SummaryError(f"{workload}: five seeds must have distinct hashes")
    decisions = {
        workload: {
            "passed": all(audit["passed"] for audit in audits if audit["workload"] == workload),
            "failed_runs": [audit for audit in audits if audit["workload"] == workload and not audit["passed"]],
        }
        for workload in campaign["workloads"]
    }
    admission = {
        "schema": "guard-hpcc-homa-collectives-v1",
        "seed_scope": [1] if seed1_only else list(SEEDS),
        "preflight_sha256": sha256_file(preflight_path),
        "simulator_sha": preflight["git_sha"], "workloads": decisions,
        "all_passed": all(decision["passed"] for decision in decisions.values()),
        "runs": audits,
    }
    return rows, admission


def aggregate(rows: Sequence[Mapping[str, object]]) -> List[Dict[str, object]]:
    output: List[Dict[str, object]] = []
    for workload in sorted({str(row["workload"]) for row in rows}):
        workload_rows = [row for row in rows if row["workload"] == workload]
        for arm in ARMS:
            arm_rows = [row for row in workload_rows if row["arm"] == arm]
            for metric in METRICS:
                output.append({
                    "workload": workload, "analysis": "arm_mean", "comparison": arm,
                    "metric": metric, **mean_ci([float(row[metric]) for row in arm_rows]),
                })
        for left, right in (("guard", "hpcc"), ("guard", "homa"), ("homa", "hpcc")):
            for metric in METRICS:
                differences: List[float] = []
                percentages: List[float] = []
                for seed in SEEDS:
                    lhs = float(next(row[metric] for row in workload_rows if row["arm"] == left and row["seed"] == seed))
                    rhs = float(next(row[metric] for row in workload_rows if row["arm"] == right and row["seed"] == seed))
                    differences.append(lhs - rhs)
                    if rhs != 0:
                        percentages.append(100.0 * (lhs - rhs) / rhs)
                comparison = f"{left}_minus_{right}"
                output.append({
                    "workload": workload, "analysis": "paired_difference",
                    "comparison": comparison, "metric": metric, **mean_ci(differences),
                })
                if len(percentages) == 5:
                    output.append({
                        "workload": workload, "analysis": "paired_percent",
                        "comparison": comparison, "metric": metric, **mean_ci(percentages),
                    })
    return output


def write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("--seed1-only", action="store_true")
    args = parser.parse_args(argv)
    results = args.results.resolve()
    analysis = results / "analysis"
    analysis.mkdir(parents=True, exist_ok=True)
    rows, admission = analyze(results, args.seed1_only)
    suffix = "seed1" if args.seed1_only else "formal"
    write_csv(analysis / f"runs-{suffix}.csv", rows)
    write_json(analysis / f"admission-{suffix}.json", admission)
    if not args.seed1_only and admission["all_passed"]:
        write_csv(analysis / "collective-ci.csv", aggregate(rows))
    print(json.dumps({"all_passed": admission["all_passed"], "workloads": admission["workloads"]}, indent=2))
    return 0 if admission["all_passed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SummaryError, OSError, ValueError, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(2)
