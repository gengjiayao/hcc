#!/usr/bin/env python3
"""Aggregate the frozen five-seed receiver-share mechanism matrix."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import re
import statistics
import sys
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

try:
    from experiments.summarize_campaign import SummaryError, T_975
    from experiments.summarize_workload import atomic_json
except ModuleNotFoundError:
    from summarize_campaign import SummaryError, T_975
    from summarize_workload import atomic_json


ARMS = ("guard", "guard-active-only")
HOMOGENEOUS_LEVELS = ("n2", "n4", "n8", "n15")
METADATA_FIELDS = (
    "scenario", "level", "controller", "seed", "flow_sha256", "output_id",
    "output_bytes", "runtime_seconds", "max_rss_kib",
)


def write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    if not rows:
        raise SummaryError(f"refusing to write empty CSV: {path}")
    fields: List[str] = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def read_index(path: Path) -> List[Dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    required = {
        "tag", "scenario", "level", "seed", "arm", "output_id",
        "output_bytes", "flow_sha256",
    }
    if not rows or not required.issubset(rows[0]):
        raise SummaryError("formal index is empty or has an unexpected schema")
    return rows


def parse_runtime(path: Path) -> Tuple[float, int]:
    text = path.read_text(encoding="utf-8")
    elapsed = re.search(r"Elapsed \(wall clock\) time .*: ([0-9:.]+)", text)
    rss = re.search(r"Maximum resident set size \(kbytes\): ([0-9]+)", text)
    if not elapsed or not rss:
        raise SummaryError(f"missing bounded-resource record in {path}")
    seconds = 0.0
    for part in elapsed.group(1).split(":"):
        seconds = seconds * 60.0 + float(part)
    return seconds, int(rss.group(1))


def metrics_at_all_scope(audit: Mapping[str, object]) -> Dict[str, float]:
    return {
        str(row["metric"]): float(row["value"])
        for row in audit["workload_summary"]["metrics"]
        if row["scope"] == "all"
    }


def flatten(
    index_row: Mapping[str, str], audit: Mapping[str, object], log_dir: Path,
) -> Dict[str, object]:
    if audit.get("status") != "validated_mechanism":
        raise SummaryError(f"audit is not mechanism-valid: {index_row['tag']}")
    if audit["controller"] != index_row["arm"]:
        raise SummaryError(f"controller mismatch: {index_row['tag']}")
    if int(audit["seed"]) != int(index_row["seed"]):
        raise SummaryError(f"seed mismatch: {index_row['tag']}")
    if audit["flow_sha256"] != index_row["flow_sha256"]:
        raise SummaryError(f"flow hash mismatch: {index_row['tag']}")
    runtime, rss = parse_runtime(log_dir / f"{index_row['tag']}.log")
    active = audit["active_set"]
    rate = audit["rate_audit"]
    overhead = audit["control_overhead"]
    health = audit["health"]
    completion = audit["completion"]
    workload = metrics_at_all_scope(audit)
    goodputs = [
        float(row["actual_goodput_gbps"])
        for row in audit["target_flow_actual_goodput_by_source"].values()
    ]
    result: Dict[str, object] = {
        "scenario": index_row["scenario"],
        "level": index_row["level"],
        "controller": index_row["arm"],
        "seed": int(index_row["seed"]),
        "flow_sha256": index_row["flow_sha256"],
        "output_id": index_row["output_id"],
        "output_bytes": int(index_row["output_bytes"]),
        "runtime_seconds": runtime,
        "max_rss_kib": rss,
        "generated_flows": int(completion["generated_flow_count"]),
        "completed_flows": int(completion["completed_flow_count"]),
        "target_max_active_flows": int(active["target_max_active_flows"]),
        "target_active_set_area_flow_us": float(active["target_active_set_area_flow_ns"]) / 1000.0,
        "target_max_active_stable_us": float(active["target_max_active_stable_ns"]) / 1000.0,
        "c_over_n_exact_gbps": float(rate["c_over_n_exact_bps"]) / 1e9,
        "observed_sender_applied_grant_gbps_min": float(rate["max_active_observed_grant_rate_bps_min"]) / 1e9,
        "observed_sender_applied_grant_gbps_max": float(rate["max_active_observed_grant_rate_bps_max"]) / 1e9,
        "c_over_n_error_mbps_max": float(rate["max_active_c_over_n_error_bps_max"]) / 1e6,
        "target_actual_goodput_gbps_mean": statistics.fmean(goodputs),
        "target_actual_goodput_gbps_min": min(goodputs),
        "target_actual_goodput_gbps_max": max(goodputs),
        "target_actual_goodput_gbps_sum": sum(goodputs),
        "target_unused_capacity_gbps": float(rate["line_rate_bps"]) / 1e9 - sum(goodputs),
        "target_grants_received_per_flow_mean": float(rate["target_grants_received_per_flow_mean"]),
        "target_grants_received_per_flow_min": float(rate["target_grants_received_per_flow_min"]),
        "target_grants_received_per_flow_max": float(rate["target_grants_received_per_flow_max"]),
        "grants_sent": int(overhead["grants_sent"]),
        "grants_received": int(overhead["grants_received"]),
        "simulated_serialized_grant_bytes": int(overhead["simulated_serialized_bytes"]),
        "simulated_serialized_bytes_per_grant": float(overhead["simulated_serialized_bytes_per_grant"]),
        "ethernet_equivalent_grant_bytes": int(overhead["ethernet_equivalent_bytes"]),
        "ethernet_equivalent_bytes_per_grant": int(overhead["ethernet_equivalent_bytes_per_grant"]),
        "switch_drops": int(health["switch_drops"]),
        "recovery_events": int(health["recovery_events"]),
        "pfc_pause_events": int(health["pfc_pause_events"]),
        "grant_trace_truncated": int(health["grant_trace"]["truncated"]),
        "trace_completion_time_us": workload["trace_completion_time_us"],
        "fct_us_mean": workload["fct_us_mean"],
        "fct_us_p95": workload["fct_us_p95"],
        "fct_us_p99": workload["fct_us_p99"],
        "aggregate_goodput_gbps": workload["aggregate_goodput_gbps"],
    }
    if index_row["scenario"] == "heterogeneous":
        source_rows = audit["target_flow_actual_goodput_by_source"]
        result["restricted_actual_goodput_gbps"] = float(source_rows["0"]["actual_goodput_gbps"])
        result["unrestricted_actual_goodput_gbps"] = float(source_rows["1"]["actual_goodput_gbps"])
    else:
        result["restricted_actual_goodput_gbps"] = math.nan
        result["unrestricted_actual_goodput_gbps"] = math.nan
    return result


def validate_matrix(rows: Sequence[Mapping[str, object]]) -> None:
    expected = {
        ("homogeneous", level, arm, seed)
        for level in HOMOGENEOUS_LEVELS for arm in ARMS for seed in range(1, 6)
    } | {
        ("heterogeneous", "k3", arm, seed)
        for arm in ARMS for seed in range(1, 6)
    }
    observed = {
        (str(row["scenario"]), str(row["level"]), str(row["controller"]), int(row["seed"]))
        for row in rows
    }
    if observed != expected or len(rows) != len(expected):
        raise SummaryError(
            f"formal matrix mismatch: missing={sorted(expected - observed)}, "
            f"unexpected={sorted(observed - expected)}"
        )
    groups: Dict[Tuple[str, str, int], List[Mapping[str, object]]] = {}
    for row in rows:
        key = (str(row["scenario"]), str(row["level"]), int(row["seed"]))
        groups.setdefault(key, []).append(row)
        if row["generated_flows"] != row["completed_flows"]:
            raise SummaryError(f"incomplete run: {key}")
        for field in ("switch_drops", "recovery_events", "pfc_pause_events", "grant_trace_truncated"):
            if row[field] != 0:
                raise SummaryError(f"health admission failed for {key}: {field}")
    for key, pair in groups.items():
        if len(pair) != 2 or len({row["flow_sha256"] for row in pair}) != 1:
            raise SummaryError(f"controller pair does not share one flow hash: {key}")
    for scenario, level in {(str(row["scenario"]), str(row["level"])) for row in rows}:
        hashes = {
            str(row["flow_sha256"]) for row in rows
            if row["scenario"] == scenario and row["level"] == level
        }
        if len(hashes) != 5:
            raise SummaryError(f"five seeds are not independent for {scenario}/{level}")


def ci(values: Sequence[float]) -> Tuple[float, float, float, float]:
    if len(values) != 5:
        raise SummaryError(f"Student-t interval requires five observations, got {len(values)}")
    mean = statistics.fmean(values)
    half = T_975[4] * statistics.stdev(values) / math.sqrt(5)
    return mean, mean - half, mean + half, half


def aggregate(rows: Sequence[Mapping[str, object]]) -> List[Dict[str, object]]:
    numeric = [
        field for field in rows[0]
        if field not in METADATA_FIELDS and isinstance(rows[0][field], (int, float))
    ]
    result: List[Dict[str, object]] = []
    cells = sorted({(str(row["scenario"]), str(row["level"]), str(row["controller"])) for row in rows})
    for scenario, level, controller in cells:
        selected = [row for row in rows if (row["scenario"], row["level"], row["controller"]) == (scenario, level, controller)]
        for metric in numeric:
            values = [float(row[metric]) for row in selected]
            if not all(math.isfinite(value) for value in values):
                continue
            mean, low, high, half = ci(values)
            result.append({
                "row_type": "mean_t95", "scenario": scenario, "level": level,
                "comparison": controller, "metric": metric, "n": 5,
                "mean": mean, "ci95_low": low, "ci95_high": high,
                "ci95_half_width": half,
            })
    for scenario, level in sorted({(str(row["scenario"]), str(row["level"])) for row in rows}):
        for metric in numeric:
            differences: List[float] = []
            for seed in range(1, 6):
                values = {
                    str(row["controller"]): float(row[metric]) for row in rows
                    if row["scenario"] == scenario and row["level"] == level and row["seed"] == seed
                }
                if not all(math.isfinite(value) for value in values.values()):
                    differences = []
                    break
                differences.append(values["guard"] - values["guard-active-only"])
            if not differences:
                continue
            mean, low, high, half = ci(differences)
            result.append({
                "row_type": "paired_difference_t95", "scenario": scenario,
                "level": level, "comparison": "guard-minus-guard-active-only",
                "metric": metric, "n": 5, "mean": mean, "ci95_low": low,
                "ci95_high": high, "ci95_half_width": half,
            })
    return result


def flow_rows(audits: Iterable[Mapping[str, object]]) -> List[Dict[str, object]]:
    result: List[Dict[str, object]] = []
    for audit in audits:
        for flow_id, flow in audit["rate_audit"]["sender_applied_grants_by_flow"].items():
            for event_index, rate in enumerate(flow["rates_bps"]):
                result.append({
                    "scenario": audit["scenario"],
                    "level": f"n{audit['expected_active_flows']}" if audit["scenario"] == "homogeneous" else "k3",
                    "controller": audit["controller"], "seed": audit["seed"],
                    "flow_id": flow_id, "event_index": event_index,
                    "sender_applied_grant_rate_bps": rate,
                    "final_observed_next_seq": flow["final_next_seq"],
                })
    return result


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate formal receiver-share audits")
    parser.add_argument("--index", required=True, type=Path)
    parser.add_argument("--audit-dir", required=True, type=Path)
    parser.add_argument("--log-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        index = read_index(args.index)
        audits = []
        per_seed = []
        for index_row in index:
            audit_path = args.audit_dir / f"{index_row['tag']}.json"
            with audit_path.open(encoding="utf-8") as stream:
                audit = json.load(stream)
            audits.append(audit)
            per_seed.append(flatten(index_row, audit, args.log_dir))
        validate_matrix(per_seed)
        aggregates = aggregate(per_seed)
        grants = flow_rows(audits)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        write_csv(args.output_dir / "per_seed.csv", per_seed)
        write_csv(args.output_dir / "aggregate_ci.csv", aggregates)
        write_csv(args.output_dir / "per_flow_grant_events.csv", grants)
        atomic_json(args.output_dir / "formal_summary.json", {
            "schema_version": 1, "status": "validated_five_seed_matrix",
            "run_count": len(per_seed), "per_seed": per_seed,
            "aggregate_ci": aggregates,
        })
    except (OSError, ValueError, KeyError, SummaryError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"validated {len(per_seed)} formal runs in {args.output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
