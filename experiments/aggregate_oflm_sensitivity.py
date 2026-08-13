#!/usr/bin/env python3
"""Validate and aggregate the preregistered OFLM beta/gamma sensitivity grid."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Dict, Mapping, MutableMapping, Sequence, Tuple

try:
    from experiments.summarize_campaign import confidence_interval
except ModuleNotFoundError:  # Direct execution from experiments/.
    from summarize_campaign import confidence_interval


CELL_PARAMETERS = {
    "b0-g1": (0.0, 1.0),
    "b0p125-g1": (0.125, 1.0),
    "b0p5-g1": (0.5, 1.0),
    "b0p875-g1": (0.875, 1.0),
    "b0p125-g0p5": (0.125, 0.5),
    "b0p125-g2": (0.125, 2.0),
}
CELL_ORDER = tuple(CELL_PARAMETERS)
BASELINE = "b0p125-g1"
BETA_AXIS = ("b0-g1", BASELINE, "b0p5-g1", "b0p875-g1")
GAMMA_AXIS = ("b0p125-g0p5", BASELINE, "b0p125-g2")
EXPECTED_SEEDS = (1, 2, 3, 4, 5)
ARTIFACT_LIMIT_BYTES = 100 * 1024 * 1024


class SensitivityError(RuntimeError):
    pass


def read_json(path: Path) -> Mapping[str, object]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise SensitivityError(f"JSON root must be an object: {path}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def interval(values: Sequence[float]) -> Dict[str, object]:
    mean, low, high = confidence_interval(values)
    return {"mean": mean, "ci95_low": low, "ci95_high": high, "n": len(values)}


def flatten(summary: Mapping[str, object]) -> Dict[str, float]:
    mechanism = summary["mechanism"]
    validation = summary["validation"]
    diagnostics = summary["controller_diagnostics"]
    performance = summary["performance"]
    output = {
        name: float(mechanism[name]) for name in (
            "release_lead_mean_ns", "release_lead_median_ns",
            "churn_release_lead_mean_ns", "churn_release_lead_median_ns",
            "remaining_bytes_mean", "remaining_bytes_median",
            "churn_remaining_bytes_mean", "churn_remaining_bytes_median",
            "active_set_area_ns", "churn_active_set_area_ns", "grants_sent",
            "grants_per_registration", "max_active_flows",
        )
    }
    output.update({
        "pfc_pause_events": float(validation["pfc_pause_events"]),
        "pfc_resume_events": float(validation["pfc_resume_events"]),
    })
    for name in (
        "hpcc_valid_feedback", "hpcc_rate_updates_applied",
        "hpcc_actual_rate_changes", "reactive_binding_updates",
        "grant_binding_updates", "tie_binding_updates",
    ):
        output[name] = float(diagnostics[name])
    for name in (
        "queue_mean_bytes", "queue_p95_bytes", "queue_p99_bytes", "queue_max_bytes",
    ):
        output[name] = float(performance[name])
    target = performance["target_receiver_queue"]
    for name in ("average_bytes", "p95_bytes", "p99_bytes", "max_bytes"):
        output[f"target_queue_{name}"] = float(target[name])
    groups = performance["fct_groups"]
    for group in ("le_bdp_churn", "gt_bdp_churn", "elephant"):
        metrics = groups[group]
        for name in (
            "mean_fct_us", "p95_fct_us", "p99_fct_us",
            "mean_slowdown", "p95_slowdown", "p99_slowdown",
        ):
            output[f"{group}_{name}"] = float(metrics[name])
    return output


def validate_one(cell: str, seed: int, summary: Mapping[str, object]) -> str:
    if summary.get("status") != "validated_complete" or summary.get("workload") != "oflm-churn":
        raise SensitivityError(f"{cell} seed {seed} is not a validated OFLM churn run")
    beta, gamma = CELL_PARAMETERS[cell]
    config = summary.get("configuration", {})
    expected_config = {
        "beta": beta,
        "gamma": gamma,
        "seed": seed,
        "selective_registration": 1,
        "proactive_release": 1,
        "priority_group": 4,
        "churn_rounds": 8,
        "churn_interval_us": 25.0,
        "churn_jitter_us": 5.0,
        "lifecycle_trace_max_lines": 100,
        "controller_trace_enabled": 0,
    }
    for name, expected in expected_config.items():
        if float(config.get(name, -1)) != float(expected):
            raise SensitivityError(
                f"{cell} seed {seed} has {name}={config.get(name)}, expected {expected}")
    validation = summary.get("validation", {})
    expected_validation = {
        "generated_flow_count": 72,
        "completed_flow_count": 72,
        "switch_drops": 0,
        "recovery_events": 0,
    }
    for name, expected in expected_validation.items():
        if int(validation.get(name, -1)) != expected:
            raise SensitivityError(
                f"{cell} seed {seed} has {name}={validation.get(name)}, expected {expected}")
    if int(validation.get("artifact_bytes", ARTIFACT_LIMIT_BYTES + 1)) >= ARTIFACT_LIMIT_BYTES:
        raise SensitivityError(f"{cell} seed {seed} exceeds the artifact limit")
    mechanism = summary.get("mechanism", {})
    expected_mechanism = {
        "trace_rows": 40,
        "registrations": 40,
        "selected_registrations": 40,
        "proactive_releases": 40,
        "completion_releases": 0,
    }
    for name, expected in expected_mechanism.items():
        if int(mechanism.get(name, -1)) != expected:
            raise SensitivityError(
                f"{cell} seed {seed} has {name}={mechanism.get(name)}, expected {expected}")
    if float(mechanism.get("proactive_release_fraction", -1)) != 1.0:
        raise SensitivityError(f"{cell} seed {seed} did not proactively release every registration")
    diagnostics = summary.get("controller_diagnostics", {})
    if diagnostics.get("status") != "disabled_by_configuration":
        raise SensitivityError(f"{cell} seed {seed} unexpectedly retained a controller trace")
    if int(diagnostics.get("truncated_rows", -1)) != 0:
        raise SensitivityError(f"{cell} seed {seed} reports a truncated controller trace")
    flow_hash = str(summary.get("provenance", {}).get("flow_sha256", ""))
    if not flow_hash:
        raise SensitivityError(f"{cell} seed {seed} lacks a flow hash")
    flatten(summary)  # Validate the complete metric schema before aggregation.
    return flow_hash


def mechanism_gradient(flattened: Mapping[str, Mapping[int, Mapping[str, float]]]) -> Dict[str, object]:
    metrics = (
        "churn_release_lead_mean_ns",
        "churn_remaining_bytes_mean",
        "churn_active_set_area_ns",
    )
    result: Dict[str, object] = {}
    passed = True
    for axis_name, cells in (("beta", BETA_AXIS), ("gamma", GAMMA_AXIS)):
        result[axis_name] = {}
        for metric in metrics:
            values = {cell: flattened[cell][1][metric] for cell in cells}
            span = max(values.values()) - min(values.values())
            baseline = abs(values[BASELINE])
            relative_span = span / baseline if baseline > 0 else None
            changed = span > 0
            passed = passed and changed
            result[axis_name][metric] = {
                "values": values,
                "absolute_span": span,
                "relative_span": relative_span,
                "changed": changed,
            }
    result["passed"] = passed
    return result


def aggregate(summaries: Mapping[str, Mapping[int, Mapping[str, object]]]) -> Dict[str, object]:
    if set(summaries) != set(CELL_ORDER):
        raise SensitivityError("summaries must contain exactly the six preregistered cells")
    seed_sets = {tuple(sorted(by_seed)) for by_seed in summaries.values()}
    if len(seed_sets) != 1:
        raise SensitivityError("all sensitivity cells must contain the same seeds")
    seeds = next(iter(seed_sets))
    if seeds not in ((1,), EXPECTED_SEEDS):
        raise SensitivityError("sensitivity input must be seed1 gate or complete seeds1-5")

    flow_hashes: Dict[int, str] = {}
    flattened: Dict[str, Dict[int, Dict[str, float]]] = {}
    run_provenance: Dict[str, Dict[str, object]] = {}
    for cell in CELL_ORDER:
        flattened[cell] = {}
        run_provenance[cell] = {}
        for seed in seeds:
            summary = summaries[cell][seed]
            flow_hash = validate_one(cell, seed, summary)
            if seed in flow_hashes and flow_hashes[seed] != flow_hash:
                raise SensitivityError(f"seed {seed} cells do not share one flow hash")
            flow_hashes[seed] = flow_hash
            flattened[cell][seed] = flatten(summary)
            run_provenance[cell][str(seed)] = {
                "output_directory": summary["provenance"]["output_directory"],
                "summary": summary["provenance"].get("summary_path", ""),
            }
    if len(set(flow_hashes.values())) != len(flow_hashes):
        raise SensitivityError("different simulator seeds must use independently jittered flow hashes")

    gradient = mechanism_gradient(flattened)
    if not gradient["passed"]:
        raise SensitivityError("seed1 parameters did not change every preregistered mechanism metric")
    if seeds == (1,):
        return {
            "schema_version": 1,
            "status": "seed1_mechanism_gate_passed",
            "cells": CELL_PARAMETERS,
            "baseline": BASELINE,
            "seeds": [1],
            "flow_sha256_by_seed": flow_hashes,
            "mechanism_gradient": gradient,
            "per_seed": flattened,
            "runs": run_provenance,
        }

    metric_names = tuple(sorted(flattened[BASELINE][1]))
    by_cell: Dict[str, object] = {}
    paired: Dict[str, object] = {}
    for cell in CELL_ORDER:
        by_cell[cell] = {
            metric: interval([flattened[cell][seed][metric] for seed in seeds])
            for metric in metric_names
        }
        paired[cell] = {}
        for metric in metric_names:
            differences = [
                flattened[cell][seed][metric] - flattened[BASELINE][seed][metric]
                for seed in seeds
            ]
            baselines = [flattened[BASELINE][seed][metric] for seed in seeds]
            relative = None
            if all(value != 0 for value in baselines):
                relative = interval([
                    100.0 * difference / baseline
                    for difference, baseline in zip(differences, baselines)
                ])
            paired[cell][metric] = {
                "absolute_difference_cell_minus_baseline": interval(differences),
                "relative_percent_cell_minus_baseline": relative,
            }
    return {
        "schema_version": 1,
        "status": "validated_complete_sensitivity",
        "cells": CELL_PARAMETERS,
        "baseline": BASELINE,
        "seeds": list(seeds),
        "flow_sha256_by_seed": flow_hashes,
        "mechanism_gradient": gradient,
        "per_seed": flattened,
        "aggregate_by_cell": by_cell,
        "paired_vs_baseline": paired,
        "runs": run_provenance,
    }


def parse_summary_specs(values: Sequence[str]) -> Dict[str, Dict[int, Path]]:
    output: MutableMapping[str, Dict[int, Path]] = {}
    for value in values:
        if ":" not in value or "=" not in value:
            raise SensitivityError("--summary must use CELL:SEED=PATH")
        raw_cell_seed, raw_path = value.split("=", 1)
        cell, raw_seed = raw_cell_seed.rsplit(":", 1)
        seed = int(raw_seed)
        if cell not in CELL_PARAMETERS or seed not in EXPECTED_SEEDS:
            raise SensitivityError(f"invalid sensitivity cell or seed: {raw_cell_seed}")
        if seed in output.setdefault(cell, {}):
            raise SensitivityError(f"duplicate summary {cell} seed {seed}")
        output[cell][seed] = Path(raw_path)
    return dict(output)


def atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, path)


def write_csv(path: Path, result: Mapping[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        fields = ("row_type", "cell", "beta", "gamma", "seed", "metric", "value",
                  "mean", "ci95_low", "ci95_high", "n")
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for cell in CELL_ORDER:
            beta, gamma = CELL_PARAMETERS[cell]
            for seed, metrics in sorted(result["per_seed"][cell].items(), key=lambda item: int(item[0])):
                for metric, value in sorted(metrics.items()):
                    writer.writerow({
                        "row_type": "per_seed", "cell": cell, "beta": beta,
                        "gamma": gamma, "seed": seed, "metric": metric, "value": value,
                    })
            if "aggregate_by_cell" in result:
                for metric, values in sorted(result["aggregate_by_cell"][cell].items()):
                    writer.writerow({
                        "row_type": "aggregate", "cell": cell, "beta": beta,
                        "gamma": gamma, "metric": metric, **values,
                    })
    os.replace(temporary, path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", action="append", default=[], help="CELL:SEED=PATH")
    parser.add_argument("--json-out", required=True, type=Path)
    parser.add_argument("--csv-out", type=Path)
    args = parser.parse_args(argv)
    try:
        paths = parse_summary_specs(args.summary)
        result = aggregate({
            cell: {seed: read_json(path) for seed, path in by_seed.items()}
            for cell, by_seed in paths.items()
        })
        result["provenance"] = {
            cell: {
                str(seed): {"path": str(path.resolve()), "sha256": sha256_file(path)}
                for seed, path in by_seed.items()
            }
            for cell, by_seed in paths.items()
        }
        output = args.json_out.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(output, result)
        if args.csv_out:
            csv_output = args.csv_out.resolve()
            csv_output.parent.mkdir(parents=True, exist_ok=True)
            write_csv(csv_output, result)
    except (OSError, ValueError, KeyError, SensitivityError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"aggregated OFLM sensitivity: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
