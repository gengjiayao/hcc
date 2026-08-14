#!/usr/bin/env python3
"""Validate and aggregate the five-seed GUARD endpoint-feature ablation."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
import json
import math
from pathlib import Path
import statistics
from typing import Dict, List, Mapping, MutableMapping, Sequence

try:
    from experiments.run_campaign import directory_size, sha256_file, write_json
    from experiments.run_guard_feature_ablation import EXPECTED_ARMS, read_spec
    from experiments.summarize_campaign import (
        GUARD_TOTAL_FIELDS, parse_guard_stats, parse_pfc, queue_summary_metrics,
    )
    from experiments.summarize_general_workloads import fct_metrics
    from experiments.summarize_workload import (
        parse_config, parse_fct, parse_snapshot, validate_completions,
    )
except ModuleNotFoundError:  # Direct execution from experiments/.
    from run_campaign import directory_size, sha256_file, write_json
    from run_guard_feature_ablation import EXPECTED_ARMS, read_spec
    from summarize_campaign import (
        GUARD_TOTAL_FIELDS, parse_guard_stats, parse_pfc, queue_summary_metrics,
    )
    from summarize_general_workloads import fct_metrics
    from summarize_workload import parse_config, parse_fct, parse_snapshot, validate_completions


T95_DF4 = 2.7764451051977987
ZERO_RECOVERY_FIELDS = (
    "switch_drops_ingress", "switch_drops_egress", "switch_drops_total",
    "recovery_nacks_generated", "recovery_nacks_received",
    "irn_nacks_generated", "irn_nacks_received", "irn_retransmit_packets",
    "irn_retransmit_bytes", "timeout_recoveries",
)


class AnalysisError(RuntimeError):
    pass


def read_json(path: Path) -> Mapping[str, object]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise AnalysisError(f"JSON root is not an object: {path}")
    return value


def one_output_file(output: Path, suffix: str) -> Path:
    matches = list(output.glob(f"*{suffix}"))
    if len(matches) != 1:
        raise AnalysisError(f"expected one *{suffix} in {output}, got {len(matches)}")
    return matches[0]


def mean_ci(values: Sequence[float]) -> Dict[str, float]:
    if len(values) != 5:
        raise AnalysisError(f"five-seed interval requires 5 values, got {len(values)}")
    mean = statistics.fmean(values)
    half = T95_DF4 * statistics.stdev(values) / math.sqrt(5)
    return {
        "n": 5, "mean": mean, "ci95_low": mean - half,
        "ci95_high": mean + half, "ci95_half_width": half,
    }


def analyze_run(
    campaign: Path, spec: Mapping[str, object], preflight: Mapping[str, object],
    workload: Mapping[str, object], trace: Mapping[str, object], arm: str,
) -> Dict[str, object]:
    seed = int(trace["seed"])
    manifest_path = campaign / "runs" / str(workload["name"]) / f"seed{seed}" / arm / "manifest.json"
    manifest = read_json(manifest_path)
    failures: List[str] = []
    if manifest.get("status") != "completed" or manifest.get("git_dirty") is not False:
        failures.append("run did not complete from a clean revision")
    output = Path(str(manifest["output_dir"]))
    output_id = str(manifest["output_id"])
    if directory_size(output) >= int(dict(spec["limits"])["run_bytes"]):
        failures.append("run artifact reached 100 MiB bound")
    source = Path(str(trace["path"]))
    snapshot = output / f"{output_id}_input_flow.txt"
    if sha256_file(source) != trace["sha256"] or sha256_file(snapshot) != trace["sha256"]:
        failures.append("flow snapshot hash mismatch")
    flows, snapshot_meta = parse_snapshot(snapshot, int(dict(spec["defaults"])["hosts"]), 10000)
    if snapshot_meta["sha256"] != trace["sha256"]:
        failures.append("parsed flow hash mismatch")
    if Counter(int(row["pg"]) for row in flows) != Counter({3: len(flows)}):
        failures.append("input is not uniformly PG3")
    fct = parse_fct(output / f"{output_id}_out_fct.txt")
    validate_completions(flows, fct, int(dict(spec["defaults"])["hosts"]))
    if len(fct) != int(trace["flow_count"]):
        failures.append("not every flow completed")

    config = parse_config(output / "config.txt")
    expected_config = {
        "CC_MODE": 3 if arm == "hpcc" else 11,
        "ENABLE_PFC": 1,
        "ENABLE_IRN": 0,
        "RANDOM_SEED": seed,
        "PREFLIGHT_MAX_FLOWS": 10000,
        "MONITOR_PROFILE": "bulk",
    }
    if arm != "hpcc":
        controls = dict(spec["defaults"])
        controls.update(dict(spec["arms"][arm]))
        expected_config.update({
            "GUARD_LAMBDA": controls["guard_lambda"],
            "GUARD_SIZE_PRIORITY": controls["guard_size_priority"],
            "GUARD_SENDER_SRPT": controls["guard_sender_srpt"],
            "GUARD_WORK_CONSERVING": controls["guard_work_conserving"],
        })
    for key, expected in expected_config.items():
        if config.get(key) != str(expected):
            failures.append(f"{key}={config.get(key)}, expected {expected}")

    stats = parse_guard_stats(output / f"{output_id}_out_guard_stats.txt")
    if any(int(stats[field]) != 0 for field in ZERO_RECOVERY_FIELDS):
        failures.append("drop or recovery counter is nonzero")
    if arm == "hpcc":
        if int(stats["grants_sent"]) + int(stats["grants_received"]) != 0:
            failures.append("HPCC sent or received GUARD grants")
        if int(stats["hpcc_valid_feedback"]) == 0 or int(stats["hpcc_actual_rate_changes"]) == 0:
            failures.append("HPCC feedback path was inactive")
    else:
        required = (
            "grants_sent", "grants_received", "hpcc_valid_feedback",
            "hpcc_actual_rate_changes", "reactive_binding_updates",
        )
        if any(int(stats[field]) == 0 for field in required):
            failures.append("a GUARD controller path was inactive")
        expected_srpt = int(dict(spec["arms"])[arm]["guard_sender_srpt"])
        if int(stats["guard_sender_srpt_enabled"]) != expected_srpt:
            failures.append("sender SRPT activity flag disagrees with configuration")

    metrics: Dict[str, float] = fct_metrics(fct, spec["flow_size_buckets"])
    starts = [float(row["start_ns"]) for row in fct]
    finishes = [float(row["finish_ns"]) for row in fct]
    span_ns = max(finishes) - min(starts)
    metrics["completion_span_us"] = span_ns / 1000.0
    metrics["aggregate_goodput_gbps"] = sum(float(row["size"]) for row in fct) * 8 / span_ns
    for name, scope, value, _count in queue_summary_metrics(
        output / f"{output_id}_out_queue_stats.txt"
    ):
        if scope == "all":
            metrics[name] = float(value)
    for field in GUARD_TOTAL_FIELDS:
        metrics[field] = float(stats[field])
    for field in (
        "guard_sender_srpt_enabled", "guard_srpt_quantum_packets",
        "guard_sender_srpt_selections", "guard_sender_srpt_non_rr",
        "guard_sender_srpt_forced_rr",
    ):
        metrics[field] = float(stats[field])
    pfc = parse_pfc(output / f"{output_id}_out_pfc.txt")
    metrics["pfc_pause_events"] = float(pfc["pfc_pause_events"])
    metrics["pfc_resume_events"] = float(pfc["pfc_resume_events"])
    return {
        "workload": workload["name"], "seed": seed, "arm": arm,
        "git_sha": manifest["git_sha"], "flow_sha256": trace["sha256"],
        "flow_count": trace["flow_count"], "output_id": output_id,
        "output_dir": str(output), "output_bytes": directory_size(output),
        "elapsed_seconds": manifest["elapsed_seconds"], "passed": not failures,
        "failures": failures, "metrics": metrics,
    }


def write_csv(path: Path, rows: Sequence[Mapping[str, object]], fields: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def analyze(campaign: Path, spec_path: Path) -> None:
    spec = read_spec(spec_path)
    preflight = read_json(campaign / "preflight.json")
    if preflight.get("spec_sha256") != sha256_file(spec_path):
        raise AnalysisError("spec changed after preflight")
    runs = []
    for workload in preflight["workloads"]:
        for trace in workload["traces"]:
            for arm in EXPECTED_ARMS:
                runs.append(analyze_run(campaign, spec, preflight, workload, trace, arm))
    rejected = [row for row in runs if not row["passed"]]
    if rejected:
        reasons = [f"{r['workload']}/s{r['seed']}/{r['arm']}: {r['failures']}" for r in rejected]
        raise AnalysisError("\n".join(reasons))
    shas = {row["git_sha"] for row in runs}
    if len(shas) != 1:
        raise AnalysisError(f"runs used multiple simulator revisions: {shas}")

    summary = campaign / "summary"
    summary.mkdir(parents=True, exist_ok=True)
    run_fields = (
        "workload", "seed", "arm", "git_sha", "flow_sha256", "flow_count",
        "output_id", "output_dir", "output_bytes", "elapsed_seconds", "passed", "failures",
    )
    write_csv(summary / "feature_ablation_runs.csv", [
        {**{field: row[field] for field in run_fields if field != "failures"},
         "failures": "; ".join(row["failures"])} for row in runs
    ], run_fields)

    metric_names = sorted(set.intersection(*[set(row["metrics"]) for row in runs]))
    long_rows = []
    for row in runs:
        for metric in metric_names:
            long_rows.append({
                "workload": row["workload"], "seed": row["seed"], "arm": row["arm"],
                "metric": metric, "value": row["metrics"][metric],
            })
    write_csv(
        summary / "feature_ablation_per_seed.csv", long_rows,
        ("workload", "seed", "arm", "metric", "value"),
    )

    ci_rows = []
    indexed = {(row["workload"], row["seed"], row["arm"]): row for row in runs}
    for workload in [row["name"] for row in preflight["workloads"]]:
        for arm in EXPECTED_ARMS:
            for metric in metric_names:
                values = [float(indexed[(workload, seed, arm)]["metrics"][metric]) for seed in range(1, 6)]
                ci_rows.append({
                    "analysis": "arm_mean", "workload": workload,
                    "comparison": arm, "metric": metric, **mean_ci(values),
                })
        for left, right in spec["comparisons"]:
            for metric in metric_names:
                left_values = [float(indexed[(workload, seed, left)]["metrics"][metric]) for seed in range(1, 6)]
                right_values = [float(indexed[(workload, seed, right)]["metrics"][metric]) for seed in range(1, 6)]
                differences = [a - b for a, b in zip(left_values, right_values)]
                percents = [(a - b) / b * 100.0 for a, b in zip(left_values, right_values) if b != 0]
                ci_rows.append({
                    "analysis": "paired_difference", "workload": workload,
                    "comparison": f"{left}_minus_{right}", "metric": metric,
                    **mean_ci(differences),
                })
                if len(percents) == 5:
                    ci_rows.append({
                        "analysis": "paired_percent", "workload": workload,
                        "comparison": f"{left}_minus_{right}", "metric": metric,
                        **mean_ci(percents),
                    })
    ci_fields = (
        "analysis", "workload", "comparison", "metric", "n", "mean",
        "ci95_low", "ci95_high", "ci95_half_width",
    )
    write_csv(summary / "feature_ablation_ci.csv", ci_rows, ci_fields)
    report = {
        "schema_version": 1, "status": "valid", "simulator_sha": next(iter(shas)),
        "spec_sha256": sha256_file(spec_path),
        "preflight_sha256": sha256_file(campaign / "preflight.json"),
        "run_count": len(runs), "workloads": [row["name"] for row in preflight["workloads"]],
        "arms": list(EXPECTED_ARMS), "all_runs_passed": True,
    }
    write_json(summary / "feature_ablation_admission.json", report)
    print(f"validated {len(runs)} runs at {report['simulator_sha']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign", type=Path)
    parser.add_argument("--spec", type=Path,
                        default=Path(__file__).parent / "campaigns/guard_feature_ablation.json")
    args = parser.parse_args()
    analyze(args.campaign.resolve(), args.spec.resolve())


if __name__ == "__main__":
    try:
        main()
    except AnalysisError as error:
        print(f"error: {error}")
        raise SystemExit(2)
