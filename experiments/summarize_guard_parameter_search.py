#!/usr/bin/env python3
"""Admit, aggregate, and select from the frozen stage-1 GUARD grid."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys
from typing import Dict, List, Mapping, Sequence, Tuple

try:
    from experiments.run_campaign import sha256_file, write_json
    from experiments.run_guard_parameter_search import read_spec
    from experiments.summarize_guard_feature_ablation import (
        AnalysisError, analyze_run, mean_ci,
    )
except ModuleNotFoundError:  # Direct execution from experiments/.
    from run_campaign import sha256_file, write_json
    from run_guard_parameter_search import read_spec
    from summarize_guard_feature_ablation import AnalysisError, analyze_run, mean_ci


def read_json(path: Path) -> Mapping[str, object]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise AnalysisError(f"JSON root is not an object: {path}")
    return value


def write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    fields = sorted({field for row in rows for field in row})
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def paired_percent(
    indexed: Mapping[Tuple[int, str], Mapping[str, object]], seeds: Sequence[int],
    candidate: str, baseline: str, metric: str,
) -> Mapping[str, float]:
    values = []
    for seed in seeds:
        left = float(indexed[(seed, candidate)]["metrics"][metric])
        right = float(indexed[(seed, baseline)]["metrics"][metric])
        if right == 0:
            raise AnalysisError(f"baseline {metric} is zero for seed {seed}")
        values.append((left - right) / right * 100.0)
    return mean_ci(values)


def select_candidate(
    spec: Mapping[str, object], runs: Sequence[Mapping[str, object]],
) -> Mapping[str, object]:
    seeds = tuple(map(int, spec["seeds"]))
    selection = dict(spec["selection"])
    baseline = str(selection["baseline_arm"])
    indexed = {(int(row["seed"]), str(row["arm"])): row for row in runs}
    primary = str(selection["primary_metric"])
    primary_limit = float(selection["require_primary_paired_percent_ci95_high_below"])
    constraints = {
        str(metric): float(limit)
        for metric, limit in dict(selection["constraints"]).items()
    }
    tie_break = tuple(map(str, selection["tie_break_metrics"]))
    candidates = []
    for arm in spec["arms"]:
        arm = str(arm)
        if arm == baseline:
            continue
        metric_stats = {
            metric: paired_percent(indexed, seeds, arm, baseline, metric)
            for metric in set((primary,) + tie_break + tuple(constraints))
        }
        gates = {
            f"{primary}_ci95_high_below_{primary_limit}":
                float(metric_stats[primary]["ci95_high"]) < primary_limit,
        }
        for metric, limit in constraints.items():
            gates[f"{metric}_ci95_high_at_most_{limit}"] = (
                float(metric_stats[metric]["ci95_high"]) <= limit
            )
        candidates.append({
            "arm": arm,
            "eligible": all(gates.values()),
            "gates": gates,
            "paired_percent_vs_baseline": metric_stats,
            "tie_break_key": [float(metric_stats[metric]["mean"]) for metric in tie_break],
        })
    eligible = [row for row in candidates if row["eligible"]]
    winner = min(eligible, key=lambda row: tuple(row["tie_break_key"])) if eligible else None
    return {
        "schema_version": 1,
        "baseline_arm": baseline,
        "selected_arm": winner["arm"] if winner else baseline,
        "status": "eligible_candidate_selected" if winner else "no_candidate_beats_frozen_gates",
        "selection_used_performance_direction": True,
        "requires_fresh_holdout_before_claim": True,
        "candidates": candidates,
    }


def admission(
    campaign: Path, spec: Mapping[str, object], preflight: Mapping[str, object],
) -> Mapping[str, object]:
    seed = int(dict(spec["admission"])["seed"])
    workload = list(preflight["workloads"])[0]
    trace = next(row for row in workload["traces"] if int(row["seed"]) == seed)
    arms = []
    for arm in spec["arms"]:
        row = analyze_run(campaign, spec, preflight, workload, trace, str(arm))
        arms.append({
            "arm": arm, "seed": seed, "passed": row["passed"],
            "failures": row["failures"], "output_id": row["output_id"],
            "flow_sha256": row["flow_sha256"],
        })
    hashes = {row["flow_sha256"] for row in arms}
    all_passed = all(bool(row["passed"]) for row in arms) and len(hashes) == 1
    return {
        "schema_version": 1,
        "gate": "first seed mechanism and safety only; no performance metric inspected",
        "seed": seed,
        "preflight_sha256": sha256_file(campaign / "preflight.json"),
        "flow_hash_matched": len(hashes) == 1,
        "all_arms_passed": all_passed,
        "decision": "extend_all_grid_points" if all_passed else "stop_without_performance",
        "arms": arms,
    }


def formal(
    campaign: Path, spec: Mapping[str, object], preflight: Mapping[str, object],
    export_dir: Path | None = None,
) -> None:
    admission_row = read_json(campaign / "summary" / "admission.json")
    if admission_row.get("all_arms_passed") is not True:
        raise AnalysisError("formal aggregation blocked by failed admission")
    workload = list(preflight["workloads"])[0]
    runs: List[Mapping[str, object]] = []
    for trace in workload["traces"]:
        for arm in spec["arms"]:
            row = analyze_run(campaign, spec, preflight, workload, trace, str(arm))
            if row["passed"] is not True:
                raise AnalysisError(
                    f"formal validation failed for seed{trace['seed']}/{arm}: {row['failures']}")
            runs.append(row)
    if len({row["git_sha"] for row in runs}) != 1:
        raise AnalysisError("formal grid spans multiple simulator revisions")
    seeds = tuple(map(int, spec["seeds"]))
    indexed = {(int(row["seed"]), str(row["arm"])): row for row in runs}
    baseline = str(dict(spec["selection"])["baseline_arm"])
    metric_names = sorted(set.intersection(*(set(row["metrics"]) for row in runs)))
    metrics = []
    per_seed = []
    for row in runs:
        for metric in metric_names:
            per_seed.append({
                "seed": row["seed"], "arm": row["arm"], "metric": metric,
                "value": row["metrics"][metric],
            })
    for arm in spec["arms"]:
        arm = str(arm)
        for metric in metric_names:
            values = [float(indexed[(seed, arm)]["metrics"][metric]) for seed in seeds]
            metrics.append({
                "analysis": "arm_mean", "comparison": arm, "metric": metric,
                **mean_ci(values),
            })
            if arm != baseline and all(float(indexed[(seed, baseline)]["metrics"][metric]) != 0
                                       for seed in seeds):
                metrics.append({
                    "analysis": "paired_percent", "comparison": f"{arm}_minus_{baseline}",
                    "metric": metric,
                    **paired_percent(indexed, seeds, arm, baseline, metric),
                })
    decision = select_candidate(spec, runs)
    summary = campaign / "summary"
    write_csv(summary / "parameter_runs.csv", [{
        "seed": row["seed"], "arm": row["arm"], "git_sha": row["git_sha"],
        "flow_sha256": row["flow_sha256"], "flow_count": row["flow_count"],
        "output_id": row["output_id"], "output_dir": row["output_dir"],
        "output_bytes": row["output_bytes"], "elapsed_seconds": row["elapsed_seconds"],
        "passed": row["passed"],
    } for row in runs])
    write_csv(summary / "parameter_per_seed.csv", per_seed)
    write_csv(summary / "parameter_ci.csv", metrics)
    write_json(summary / "selection.json", decision)
    if export_dir is not None:
        export_dir.mkdir(parents=True, exist_ok=True)
        portable_runs = [{
            "seed": row["seed"], "arm": row["arm"], "git_sha": row["git_sha"],
            "flow_sha256": row["flow_sha256"], "flow_count": row["flow_count"],
            "output_id": row["output_id"],
            "output_dir": f"mix/output/{row['output_id']}",
            "output_bytes": row["output_bytes"], "elapsed_seconds": row["elapsed_seconds"],
            "passed": row["passed"],
        } for row in runs]
        write_json(export_dir / "spec.json", spec)
        write_json(export_dir / "admission.json", admission_row)
        write_json(export_dir / "selection.json", decision)
        write_csv(export_dir / "run_registry.csv", portable_runs)
        write_csv(export_dir / "per_seed.csv", per_seed)
        write_csv(export_dir / "ci.csv", metrics)
        (export_dir / "README.md").write_text(
            "# GUARD development parameter search\n\n"
            "This directory preserves a frozen development-stage search. It is not a "
            "held-out performance claim. `selection.json` applies the preregistered "
            "paired-CI gates; raw outputs remain under the repository-relative locators "
            "in `run_registry.csv`.\n",
            encoding="utf-8",
        )
        tracked = (
            "README.md", "admission.json", "ci.csv", "per_seed.csv",
            "run_registry.csv", "selection.json", "spec.json",
        )
        write_json(export_dir / "manifest.json", {
            "schema_version": 1,
            "development_only": True,
            "simulator_git_shas": sorted({str(row["git_sha"]) for row in runs}),
            "seeds": list(map(int, spec["seeds"])),
            "raw_outputs_in_git": False,
            "raw_locator_rule": "run_registry output_dir is relative to the guard repository",
            "files": {
                name: {"bytes": (export_dir / name).stat().st_size,
                       "sha256": sha256_file(export_dir / name)}
                for name in tracked
            },
        })
    print(
        f"validated {len(runs)} runs; selection={decision['selected_arm']} "
        f"status={decision['status']}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign", type=Path)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--admission-only", action="store_true")
    parser.add_argument("--export-dir", type=Path)
    args = parser.parse_args(argv)
    campaign = args.campaign.resolve()
    spec_path = args.spec.resolve()
    spec = read_spec(spec_path)
    preflight = read_json(campaign / "preflight.json")
    if preflight.get("spec_sha256") != sha256_file(spec_path):
        raise AnalysisError("spec changed after preflight")
    summary = campaign / "summary"
    summary.mkdir(parents=True, exist_ok=True)
    admitted = admission(campaign, spec, preflight)
    write_json(summary / "admission.json", admitted)
    if args.admission_only:
        print(f"admission all_arms_passed={admitted['all_arms_passed']}")
        return 0 if admitted["all_arms_passed"] else 1
    formal(
        campaign, spec, preflight,
        args.export_dir.resolve() if args.export_dir else None,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AnalysisError, KeyError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
