#!/usr/bin/env python3
"""Aggregate selected OFLM churn tiers across simulator seeds with paired CIs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Dict, Mapping, Sequence

try:
    from experiments.summarize_campaign import confidence_interval
except ModuleNotFoundError:  # Direct execution from experiments/.
    from summarize_campaign import confidence_interval


COMPARISONS = {
    "selective_with_proactive_off": ("00", "10"),
    "selective_with_proactive_on": ("01", "11"),
    "proactive_with_selective_off": ("00", "01"),
    "proactive_with_selective_on": ("10", "11"),
}


class AggregateError(RuntimeError):
    pass


def read_json(path: Path) -> Mapping[str, object]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise AggregateError(f"JSON root must be an object: {path}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def interval(values):
    mean, low, high = confidence_interval(values)
    return {"mean": mean, "ci95_low": low, "ci95_high": high, "n": len(values)}


def flatten_performance(performance: Mapping[str, object]) -> Dict[str, float]:
    output = {
        name: float(performance[name])
        for name in (
            "fct_mean_slowdown", "fct_p99_slowdown", "queue_mean_bytes",
            "queue_p99_bytes", "queue_max_bytes",
        )
    }
    groups = performance.get("fct_groups")
    if not isinstance(groups, dict):
        raise AggregateError("selected performance lacks FCT groups")
    for group, metrics in groups.items():
        output[f"{group}_mean_slowdown"] = float(metrics["mean_slowdown"])
        output[f"{group}_p99_slowdown"] = float(metrics["p99_slowdown"])
    target_queue = performance.get("target_receiver_queue")
    if not isinstance(target_queue, dict):
        raise AggregateError("selected performance lacks target receiver queue metrics")
    for name in (
        "average_bytes", "positive_average_bytes", "p95_bytes", "p99_bytes",
        "max_bytes",
    ):
        output[f"target_queue_{name}"] = float(target_queue[name])
    return output


def aggregate(selections: Mapping[int, Mapping[str, object]]) -> Dict[str, object]:
    if len(selections) < 2:
        raise AggregateError("at least two selected seeds are required")
    flow_hashes: Dict[int, str] = {}
    tiers = set()
    flattened: Dict[int, Dict[str, Dict[str, float]]] = {}
    for seed, selection in selections.items():
        if selection.get("decision") != "selected":
            raise AggregateError(f"seed {seed} did not select its tier")
        flow_hashes[seed] = str(selection.get("flow_sha256", ""))
        tier = selection.get("tier", {})
        tiers.add(json.dumps(tier, sort_keys=True))
        performance = selection.get("post_selection_performance")
        if not isinstance(performance, dict) or set(performance) != {"00", "01", "10", "11"}:
            raise AggregateError(f"seed {seed} lacks four-arm post-selection performance")
        flattened[seed] = {
            combo: flatten_performance(performance[combo])
            for combo in ("00", "01", "10", "11")
        }
    if "" in flow_hashes.values():
        raise AggregateError("selected seeds lack a flow SHA")
    if len(set(flow_hashes.values())) != len(flow_hashes):
        raise AggregateError("selected seeds do not use independent flow SHAs")
    if len(tiers) != 1:
        raise AggregateError("selected seeds use different tiers")

    metric_names = set(flattened[next(iter(flattened))]["00"])
    aggregates: Dict[str, object] = {}
    for combo in ("00", "01", "10", "11"):
        aggregates[combo] = {
            metric: interval([flattened[seed][combo][metric] for seed in sorted(flattened)])
            for metric in sorted(metric_names)
        }
    paired_reductions: Dict[str, object] = {}
    paired_differences: Dict[str, object] = {}
    for name, (before, after) in COMPARISONS.items():
        paired_reductions[name] = {}
        paired_differences[name] = {}
        for metric in sorted(metric_names):
            baselines = []
            after_values = []
            for seed in sorted(flattened):
                baselines.append(flattened[seed][before][metric])
                after_values.append(flattened[seed][after][metric])
            paired_differences[name][metric] = interval([
                baseline - after_value
                for baseline, after_value in zip(baselines, after_values)
            ])
            if any(baseline <= 0 for baseline in baselines):
                paired_reductions[name][metric] = {
                    "status": "undefined_nonpositive_baseline", "n": len(baselines),
                }
            else:
                paired_reductions[name][metric] = interval([
                    1.0 - after_value / baseline
                    for baseline, after_value in zip(baselines, after_values)
                ])

    criterion_names = set(
        selections[next(iter(selections))].get("criterion_values", {})
    )
    mechanism = {
        name: interval([
            float(selections[seed]["criterion_values"][name])
            for seed in sorted(selections)
        ])
        for name in sorted(criterion_names)
    }
    return {
        "schema_version": 1,
        "status": "validated_selected_aggregate",
        "tier": json.loads(next(iter(tiers))),
        "flow_sha256_by_seed": {
            str(seed): flow_hashes[seed] for seed in sorted(flow_hashes)
        },
        "seeds": sorted(selections),
        "mechanism_criterion_values": mechanism,
        "performance_by_combination": aggregates,
        "paired_absolute_differences": paired_differences,
        "paired_relative_reductions": paired_reductions,
    }


def parse_seed_paths(values: Sequence[str]) -> Dict[int, Path]:
    output: Dict[int, Path] = {}
    for value in values:
        if "=" not in value:
            raise AggregateError("--selection must use SEED=PATH")
        raw_seed, raw_path = value.split("=", 1)
        seed = int(raw_seed)
        if seed <= 0 or seed in output:
            raise AggregateError(f"invalid or duplicate seed {seed}")
        output[seed] = Path(raw_path)
    return output


def atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", action="append", default=[])
    parser.add_argument("--json-out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        paths = parse_seed_paths(args.selection)
        result = aggregate({seed: read_json(path) for seed, path in paths.items()})
        result["provenance"] = {
            "selections": {str(seed): str(path.resolve()) for seed, path in paths.items()},
            "selection_sha256": {str(seed): sha256_file(path) for seed, path in paths.items()},
        }
        output = args.json_out.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(output, result)
    except (OSError, ValueError, KeyError, AggregateError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"aggregated {len(result['seeds'])} selected seeds: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
