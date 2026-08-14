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
    from experiments.run_guard_feature_ablation import read_spec
    from experiments.summarize_campaign import (
        GUARD_TOTAL_FIELDS, parse_guard_stats, parse_pfc, queue_summary_metrics,
    )
    from experiments.summarize_general_workloads import fct_metrics
    from experiments.summarize_workload import (
        parse_config, parse_fct, parse_snapshot, validate_completions,
    )
except ModuleNotFoundError:  # Direct execution from experiments/.
    from run_campaign import directory_size, sha256_file, write_json
    from run_guard_feature_ablation import read_spec
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
    expected_flows = int(trace["flow_count"])
    if len(fct) == expected_flows:
        validate_completions(flows, fct, int(dict(spec["defaults"])["hosts"]))
    else:
        failures.append(
            f"completed {len(fct)} of {expected_flows} flows; "
            "exclude this seed from latency comparisons"
        )

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
        config_names = {
            "guard_lambda": "GUARD_LAMBDA",
            "guard_size_priority": "GUARD_SIZE_PRIORITY",
            "guard_sender_srpt": "GUARD_SENDER_SRPT",
            "guard_work_conserving": "GUARD_WORK_CONSERVING",
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
        }
        expected_config.update({
            config_name: controls[option]
            for option, config_name in config_names.items() if option in controls
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
        controls = dict(spec["defaults"])
        controls.update(dict(spec["arms"])[arm])
        expected_srpt = int(controls["guard_sender_srpt"])
        if int(stats["guard_sender_srpt_enabled"]) != expected_srpt:
            failures.append("sender SRPT activity flag disagrees with configuration")
        flag_fields = {
            "guard_one_rtt_bypass": "guard_one_rtt_bypass_enabled",
            "guard_tail_bypass": "guard_tail_bypass_enabled",
            "guard_remaining_aware": "guard_remaining_aware",
        }
        for option, field in flag_fields.items():
            if option in controls and int(stats[field]) != int(controls[option]):
                failures.append(f"{field} disagrees with configuration")
        if int(controls.get("guard_one_rtt_bypass", 0)):
            if int(stats["guard_one_rtt_bypass_flows"]) == 0:
                failures.append("one-RTT bypass did not exercise any flow")
        if int(controls.get("guard_tail_bypass", 0)):
            if int(stats["guard_tail_bypass_flows"]) == 0:
                failures.append("tail bypass did not exercise any flow")
        if int(controls.get("guard_remaining_aware", 0)):
            if int(stats["guard_remaining_refresh_events"]) == 0:
                failures.append("remaining-aware grants never refreshed")
        if int(controls.get("guard_ack_interval_packets", 1)) == 1:
            if int(stats["guard_long_acks_suppressed"]) != 0:
                failures.append("ACK coalescing disabled but long ACKs were suppressed")

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
        "guard_sender_srpt_forced_rr", "guard_one_rtt_bypass_enabled",
        "guard_one_rtt_bypass_flows", "guard_one_rtt_bypass_feedbacks",
        "guard_one_rtt_acks_suppressed", "guard_long_acks_suppressed",
        "guard_ack_interval_packets", "guard_fixed_window",
        "guard_tail_bypass_enabled", "guard_tail_bypass_bdps",
        "guard_tail_bypass_flows", "guard_tail_bypass_feedbacks",
        "guard_remaining_aware", "guard_min_share_fraction",
        "guard_remaining_exponent", "guard_grant_refresh_bdps",
        "guard_remaining_refresh_events",
    ):
        metrics[field] = float(stats[field])
    pfc = parse_pfc(output / f"{output_id}_out_pfc.txt")
    metrics["pfc_pause_events"] = float(pfc["pfc_pause_events"])
    metrics["pfc_resume_events"] = float(pfc["pfc_resume_events"])
    return {
        "workload": workload["name"], "seed": seed, "arm": arm,
        "git_sha": manifest["git_sha"], "flow_sha256": trace["sha256"],
        "flow_count": trace["flow_count"], "output_id": output_id,
        "completed_flow_count": len(fct),
        "completion_fraction": len(fct) / float(expected_flows),
        "output_dir": str(output), "output_bytes": directory_size(output),
        "elapsed_seconds": manifest["elapsed_seconds"], "passed": not failures,
        "failures": failures, "metrics": metrics,
    }


def write_csv(path: Path, rows: Sequence[Mapping[str, object]], fields: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def cohort_passes(
    indexed: Mapping[tuple, Mapping[str, object]], workload: str,
    arm: str, seeds: Sequence[int],
) -> bool:
    """Return whether every preregistered seed in an arm passed admission."""
    return all(bool(indexed[(workload, seed, arm)]["passed"]) for seed in seeds)


def export_portable(
    export_dir: Path, spec: Mapping[str, object], preflight: Mapping[str, object],
    report: Mapping[str, object], runs: Sequence[Mapping[str, object]],
    per_seed_rows: Sequence[Mapping[str, object]], ci_rows: Sequence[Mapping[str, object]],
    run_fields: Sequence[str], ci_fields: Sequence[str],
) -> None:
    """Write bounded feature-ablation evidence with repository-relative locators."""
    export_dir.mkdir(parents=True, exist_ok=True)
    registry = []
    for row in runs:
        portable = {
            **{field: row[field] for field in run_fields if field != "failures"},
            "failures": "; ".join(row["failures"]),
        }
        portable["output_dir"] = f"mix/output/{row['output_id']}"
        registry.append(portable)
    files = {
        "spec.json": spec,
        "preflight.json": preflight,
        "admission.json": report,
    }
    for name, payload in files.items():
        write_json(export_dir / name, payload)
    write_csv(export_dir / "run_registry.csv", registry, run_fields)
    write_csv(
        export_dir / "per_seed.csv", per_seed_rows,
        ("workload", "seed", "arm", "metric", "value"),
    )
    write_csv(export_dir / "ci.csv", ci_rows, ci_fields)
    tracked = sorted(files) + ["ci.csv", "per_seed.csv", "run_registry.csv"]
    manifest = {
        "schema_version": 1,
        "description": "Fresh-seed one-factor ablation of optimized GUARD",
        "simulator_git_shas": sorted({str(row["git_sha"]) for row in runs}),
        "seeds": list(map(int, spec["seeds"])),
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


def analyze(campaign: Path, spec_path: Path, export_dir: Path | None = None) -> None:
    spec = read_spec(spec_path)
    preflight = read_json(campaign / "preflight.json")
    if preflight.get("spec_sha256") != sha256_file(spec_path):
        raise AnalysisError("spec changed after preflight")
    runs = []
    arms = tuple(map(str, spec["arms"]))
    seeds = tuple(map(int, spec["seeds"]))
    for workload in preflight["workloads"]:
        for trace in workload["traces"]:
            for arm in arms:
                runs.append(analyze_run(campaign, spec, preflight, workload, trace, arm))
    rejected = [row for row in runs if not row["passed"]]
    shas = {row["git_sha"] for row in runs}
    if len(shas) != 1:
        raise AnalysisError(f"runs used multiple simulator revisions: {shas}")

    summary = campaign / "summary"
    summary.mkdir(parents=True, exist_ok=True)
    run_fields = (
        "workload", "seed", "arm", "git_sha", "flow_sha256", "flow_count",
        "completed_flow_count", "completion_fraction",
        "output_id", "output_dir", "output_bytes", "elapsed_seconds", "passed", "failures",
    )
    write_csv(summary / "feature_ablation_runs.csv", [
        {**{field: row[field] for field in run_fields if field != "failures"},
         "failures": "; ".join(row["failures"])} for row in runs
    ], run_fields)

    admitted_runs = [row for row in runs if row["passed"]]
    metric_names = sorted(set.intersection(*[set(row["metrics"]) for row in admitted_runs]))
    long_rows = []
    for row in admitted_runs:
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
    eligible_cohorts = []
    rejected_cohorts = []
    for workload in [row["name"] for row in preflight["workloads"]]:
        for arm in arms:
            if not cohort_passes(indexed, workload, arm, seeds):
                rejected_cohorts.append({"workload": workload, "arm": arm})
                continue
            eligible_cohorts.append({"workload": workload, "arm": arm})
            for metric in metric_names:
                values = [float(indexed[(workload, seed, arm)]["metrics"][metric])
                          for seed in seeds]
                ci_rows.append({
                    "analysis": "arm_mean", "workload": workload,
                    "comparison": arm, "metric": metric, **mean_ci(values),
                })
        for left, right in spec["comparisons"]:
            if not (cohort_passes(indexed, workload, left, seeds)
                    and cohort_passes(indexed, workload, right, seeds)):
                continue
            for metric in metric_names:
                left_values = [float(indexed[(workload, seed, left)]["metrics"][metric])
                               for seed in seeds]
                right_values = [float(indexed[(workload, seed, right)]["metrics"][metric])
                                for seed in seeds]
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
        "schema_version": 1,
        "status": "valid" if not rejected else "valid_with_rejected_cohorts",
        "simulator_sha": next(iter(shas)),
        "spec_sha256": sha256_file(spec_path),
        "preflight_sha256": sha256_file(campaign / "preflight.json"),
        "run_count": len(runs), "admitted_run_count": len(admitted_runs),
        "rejected_run_count": len(rejected),
        "workloads": [row["name"] for row in preflight["workloads"]],
        "arms": list(arms), "seeds": list(seeds), "all_runs_passed": not rejected,
        "eligible_cohorts": eligible_cohorts, "rejected_cohorts": rejected_cohorts,
        "rejected_runs": [
            {"workload": row["workload"], "seed": row["seed"], "arm": row["arm"],
             "completed_flow_count": row["completed_flow_count"],
             "flow_count": row["flow_count"], "failures": row["failures"]}
            for row in rejected
        ],
    }
    write_json(summary / "feature_ablation_admission.json", report)
    if export_dir is not None:
        export_portable(
            export_dir, spec, preflight, report, runs, long_rows, ci_rows,
            run_fields, ci_fields,
        )
    print(
        f"admitted {len(admitted_runs)}/{len(runs)} runs at {report['simulator_sha']}; "
        f"rejected {len(rejected_cohorts)} workload/arm cohorts"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign", type=Path)
    parser.add_argument("--spec", type=Path,
                        default=Path(__file__).parent / "campaigns/guard_feature_ablation.json")
    parser.add_argument("--export-dir", type=Path)
    args = parser.parse_args()
    analyze(
        args.campaign.resolve(), args.spec.resolve(),
        args.export_dir.resolve() if args.export_dir else None,
    )


if __name__ == "__main__":
    try:
        main()
    except AnalysisError as error:
        print(f"error: {error}")
        raise SystemExit(2)
