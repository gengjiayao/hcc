#!/usr/bin/env python3
"""Aggregate selected paired PFC/IRN recovery seeds with Student-t CIs."""

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
except ModuleNotFoundError:
    from summarize_campaign import confidence_interval


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
        name: float(performance[name]) for name in (
            "duration_mean_us", "duration_p95_us", "duration_p99_us",
            "slowdown_mean", "slowdown_p95", "slowdown_p99",
            "queue_average_bytes", "queue_p95_bytes", "queue_p99_bytes",
            "queue_max_bytes",
        )
    }
    target = performance.get("target_receiver_queue")
    if not isinstance(target, dict):
        raise AggregateError("selected performance lacks target receiver queue")
    for name in (
        "average_bytes", "positive_average_bytes", "p95_bytes", "p99_bytes",
        "max_bytes",
    ):
        output[f"target_queue_{name}"] = float(target[name])
    return output


def aggregate(selections: Mapping[int, Mapping[str, object]]) -> Dict[str, object]:
    if len(selections) < 2:
        raise AggregateError("at least two selected seeds are required")
    flow_hashes: Dict[int, str] = {}
    tiers = set()
    performance: Dict[int, Dict[str, Dict[str, float]]] = {}
    for seed, selection in selections.items():
        if selection.get("decision") != "selected":
            raise AggregateError(f"seed {seed} did not select its tier")
        flow_hash = str(selection.get("flow_sha256", ""))
        if not flow_hash:
            raise AggregateError(f"seed {seed} lacks flow SHA")
        flow_hashes[seed] = flow_hash
        tiers.add(json.dumps(selection.get("tier"), sort_keys=True))
        post = selection.get("post_selection_performance")
        if not isinstance(post, dict) or set(post) != {"pfc", "irn"}:
            raise AggregateError(f"seed {seed} lacks paired post-selection performance")
        performance[seed] = {
            arm: flatten_performance(post[arm]) for arm in ("pfc", "irn")
        }
    if len(tiers) != 1:
        raise AggregateError("selected seeds use different tiers")

    names = sorted(performance[next(iter(performance))]["pfc"])
    by_arm = {
        arm: {
            name: interval([performance[seed][arm][name] for seed in sorted(performance)])
            for name in names
        }
        for arm in ("pfc", "irn")
    }
    differences = {}
    reductions = {}
    for name in names:
        pfc_values = [performance[seed]["pfc"][name] for seed in sorted(performance)]
        irn_values = [performance[seed]["irn"][name] for seed in sorted(performance)]
        differences[name] = interval([
            pfc - irn for pfc, irn in zip(pfc_values, irn_values)
        ])
        if any(value <= 0 for value in pfc_values):
            reductions[name] = {
                "status": "undefined_nonpositive_baseline", "n": len(pfc_values),
            }
        else:
            reductions[name] = interval([
                1.0 - irn / pfc for pfc, irn in zip(pfc_values, irn_values)
            ])

    mechanism_names = sorted(
        selections[next(iter(selections))]["mechanism_by_arm"]["pfc"])
    mechanism = {
        arm: {
            name: interval([
                float(selections[seed]["mechanism_by_arm"][arm][name])
                for seed in sorted(selections)
            ])
            for name in mechanism_names
        }
        for arm in ("pfc", "irn")
    }
    return {
        "schema_version": 1, "status": "validated_selected_aggregate",
        "tier": json.loads(next(iter(tiers))), "seeds": sorted(selections),
        "flow_sha256_by_seed": {str(seed): flow_hashes[seed] for seed in sorted(flow_hashes)},
        "mechanism_by_arm": mechanism, "performance_by_arm": by_arm,
        "paired_absolute_pfc_minus_irn": differences,
        "paired_relative_irn_reduction_vs_pfc": reductions,
    }


def parse_seed_paths(values: Sequence[str]) -> Dict[int, Path]:
    result: Dict[int, Path] = {}
    for value in values:
        if "=" not in value:
            raise AggregateError("--selection must use SEED=PATH")
        raw_seed, raw_path = value.split("=", 1)
        seed = int(raw_seed)
        if seed <= 0 or seed in result:
            raise AggregateError(f"invalid or duplicate seed {seed}")
        result[seed] = Path(raw_path)
    return result


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
            "selection_sha256": {
                str(seed): sha256_file(path) for seed, path in paths.items()
            },
        }
        output = args.json_out.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(output, result)
    except (OSError, ValueError, KeyError, AggregateError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"aggregated {len(result['seeds'])} paired recovery seeds: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
