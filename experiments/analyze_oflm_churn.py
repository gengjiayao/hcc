#!/usr/bin/env python3
"""Validate one OFLM churn run and report mechanism-before-performance metrics."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
import json
import os
from pathlib import Path
import statistics
import sys
from typing import Dict, List, Mapping, Sequence

try:
    from experiments.summarize_campaign import (
        SummaryError, parse_config, parse_guard_stats, parse_port_queue_summaries,
        percentile,
    )
    from experiments.summarize_workload import (
        one_artifact, parse_fct, parse_snapshot, read_json, resolve_snapshot, summarize,
    )
except ModuleNotFoundError:  # Direct execution from experiments/.
    from summarize_campaign import (
        SummaryError, parse_config, parse_guard_stats, parse_port_queue_summaries,
        percentile,
    )
    from summarize_workload import (
        one_artifact, parse_fct, parse_snapshot, read_json, resolve_snapshot, summarize,
    )


LIFECYCLE_FIELDS = (
    "flow_id", "size_bytes", "receiver_node", "first_rx_ns", "register_ns",
    "release_ns", "complete_ns", "release_reason", "remaining_bytes_at_release",
    "active_before_register", "active_after_register", "active_before_release",
    "active_after_release",
)
INTEGER_FIELDS = tuple(field for field in LIFECYCLE_FIELDS if field != "release_reason")


def parse_lifecycle(path: Path) -> List[Dict[str, object]]:
    if not path.is_file():
        raise SummaryError(f"missing lifecycle trace: {path}")
    rows: List[Dict[str, object]] = []
    with path.open(encoding="utf-8", errors="strict", newline="") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != LIFECYCLE_FIELDS:
            raise SummaryError("lifecycle columns do not match the bounded trace schema")
        for line_number, raw in enumerate(reader, 2):
            try:
                row: Dict[str, object] = {
                    field: int(raw[field]) for field in INTEGER_FIELDS
                }
            except (KeyError, TypeError, ValueError) as exc:
                raise SummaryError(f"invalid lifecycle integer at {path}:{line_number}") from exc
            row["release_reason"] = raw["release_reason"]
            if row["release_reason"] not in ("completion", "proactive"):
                raise SummaryError(f"unfinished or invalid lifecycle at {path}:{line_number}")
            if not (
                row["first_rx_ns"] <= row["register_ns"] <= row["release_ns"]
                <= row["complete_ns"]
            ):
                raise SummaryError(f"non-monotonic lifecycle at {path}:{line_number}")
            if row["active_after_register"] - row["active_before_register"] != 1:
                raise SummaryError(f"registration does not add one active flow: {path}:{line_number}")
            if row["active_before_release"] - row["active_after_release"] != 1:
                raise SummaryError(f"release does not remove one active flow: {path}:{line_number}")
            if row["release_reason"] == "completion" and row["remaining_bytes_at_release"] != 0:
                raise SummaryError(f"completion release has remaining bytes: {path}:{line_number}")
            if row["release_reason"] == "proactive" and row["remaining_bytes_at_release"] <= 0:
                raise SummaryError(f"proactive release has no remaining bytes: {path}:{line_number}")
            rows.append(row)
    if len({int(row["flow_id"]) for row in rows}) != len(rows):
        raise SummaryError("lifecycle trace contains duplicate flow IDs")
    return rows


def lifecycle_metrics(rows: Sequence[Mapping[str, object]], elephant_bytes: int) -> Dict[str, object]:
    if not rows:
        raise SummaryError("lifecycle trace contains no registered flows")
    churn = [row for row in rows if int(row["size_bytes"]) != elephant_bytes]
    if not churn:
        raise SummaryError("lifecycle trace contains no churn flows")

    def area_ns(selected: Sequence[Mapping[str, object]]) -> int:
        return sum(int(row["release_ns"]) - int(row["register_ns"]) for row in selected)

    leads = [int(row["complete_ns"]) - int(row["release_ns"]) for row in rows]
    churn_leads = [int(row["complete_ns"]) - int(row["release_ns"]) for row in churn]
    proactive = sum(row["release_reason"] == "proactive" for row in rows)
    return {
        "trace_rows": len(rows),
        "churn_trace_rows": len(churn),
        "proactive_release_fraction": proactive / len(rows),
        "active_set_area_ns": area_ns(rows),
        "churn_active_set_area_ns": area_ns(churn),
        "release_lead_mean_ns": statistics.fmean(leads),
        "release_lead_median_ns": statistics.median(leads),
        "release_lead_p95_ns": percentile(leads, 95),
        "churn_release_lead_mean_ns": statistics.fmean(churn_leads),
        "churn_release_lead_median_ns": statistics.median(churn_leads),
        "churn_release_lead_p95_ns": percentile(churn_leads, 95),
        "trace_max_active_flows": max(int(row["active_after_register"]) for row in rows),
    }


def metric_value(summary: Mapping[str, object], name: str) -> float:
    matches = [
        float(row["value"]) for row in summary["metrics"]
        if row["metric"] == name and row.get("scope") == "all"
    ]
    if len(matches) != 1:
        raise SummaryError(f"expected one all-scope metric {name}, found {len(matches)}")
    return matches[0]


def grouped_fct_metrics(output_dir: Path, bdp_bytes: int,
                        elephant_bytes: int) -> Dict[str, object]:
    completions = parse_fct(one_artifact(output_dir, "_out_fct.txt"))
    groups = {
        "all": completions,
        "elephant": [row for row in completions if int(row["size"]) == elephant_bytes],
        "churn": [row for row in completions if int(row["size"]) != elephant_bytes],
        "le_bdp_churn": [row for row in completions if int(row["size"]) <= bdp_bytes],
        "gt_bdp_churn": [
            row for row in completions
            if bdp_bytes < int(row["size"]) != elephant_bytes
        ],
    }
    result: Dict[str, object] = {}
    for name, rows in groups.items():
        if not rows:
            raise SummaryError(f"FCT group {name} is empty")
        slowdowns = [float(row["slowdown"]) for row in rows]
        result[name] = {
            "flows": len(rows),
            "mean_slowdown": statistics.fmean(slowdowns),
            "p99_slowdown": percentile(slowdowns, 99),
        }
    return result


def target_queue_metrics(path: Path, receiver_node: int) -> Dict[str, object]:
    """Return the unique switch egress queue feeding the workload receiver."""
    matches = [
        row for row in parse_port_queue_summaries(path)
        if int(row["neighbor_id"]) == receiver_node
    ]
    if len(matches) != 1:
        raise SummaryError(
            f"expected one switch egress to receiver {receiver_node}, found {len(matches)}")
    row = matches[0]
    return {
        "node_id": int(row["node_id"]),
        "if_index": int(row["if_index"]),
        "neighbor_id": int(row["neighbor_id"]),
        "samples": int(row["samples"]),
        "positive_samples": int(row["positive_samples"]),
        "average_bytes": float(row["average_bytes"]),
        "positive_average_bytes": float(row["positive_average_bytes"]),
        "p95_bytes": float(row["p95_bytes"]),
        "p99_bytes": float(row["p99_bytes"]),
        "max_bytes": float(row["max_bytes"]),
        "tx_bytes": int(row["tx_bytes"]),
    }


def analyze(output_dir: Path, manifest_path: Path, lifecycle_path: Path,
            artifact_limit_bytes: int) -> Dict[str, object]:
    output_dir = output_dir.resolve()
    manifest_path = manifest_path.resolve()
    lifecycle_path = lifecycle_path.resolve()
    manifest = read_json(manifest_path)
    if manifest.get("workload") != "oflm-churn":
        raise SummaryError("manifest workload must be oflm-churn")
    parameters = manifest.get("parameters")
    if not isinstance(parameters, dict):
        raise SummaryError("manifest parameters must be an object")
    try:
        hosts = int(parameters["hosts"])
        max_flows = int(parameters["max_flows"])
        bdp_bytes = int(parameters["oflm_bdp_bytes"])
        elephant_bytes = int(parameters["oflm_elephant_bytes"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SummaryError("manifest lacks OFLM churn parameters") from exc

    base = summarize(output_dir, manifest_path)
    snapshot_path = resolve_snapshot(output_dir, None)
    flows, _ = parse_snapshot(snapshot_path, hosts, max_flows)
    config = parse_config(output_dir / "config.txt")
    try:
        selective = int(config["GUARD_SELECTIVE_REGISTRATION"])
        proactive = int(config["GUARD_PROACTIVE_RELEASE"])
    except (KeyError, ValueError) as exc:
        raise SummaryError("config lacks valid independent OFLM switches") from exc
    if selective not in (0, 1) or proactive not in (0, 1):
        raise SummaryError("OFLM switches must be 0 or 1")

    rows = parse_lifecycle(lifecycle_path)
    receivers = {int(flow["dst"]) for flow in flows}
    if len(receivers) != 1:
        raise SummaryError("OFLM churn workload must use exactly one receiver")
    receiver_node = next(iter(receivers))
    expected_sizes = Counter(
        int(flow["size"]) for flow in flows
        if not selective or int(flow["size"]) > bdp_bytes
    )
    observed_sizes = Counter(int(row["size_bytes"]) for row in rows)
    if observed_sizes != expected_sizes:
        raise SummaryError(
            f"registered lifecycle sizes differ from selective policy: "
            f"expected={expected_sizes}, observed={observed_sizes}")
    if not proactive and any(row["release_reason"] != "completion" for row in rows):
        raise SummaryError("proactive releases occurred while the component was disabled")

    guard_path = next(output_dir.glob("*_out_guard_stats.txt"), None)
    if guard_path is None:
        raise SummaryError("missing GUARD stats artifact")
    stats = parse_guard_stats(guard_path)
    if int(stats["registrations"]) != len(rows):
        raise SummaryError("GUARD registration total differs from lifecycle row count")
    lifecycle = lifecycle_metrics(rows, elephant_bytes)
    if int(stats["max_active_flows"]) != lifecycle["trace_max_active_flows"]:
        raise SummaryError("GUARD max-active total differs from lifecycle trace")

    artifact_bytes = sum(
        path.stat().st_size for path in output_dir.rglob("*") if path.is_file()
    ) + lifecycle_path.stat().st_size
    if artifact_bytes > artifact_limit_bytes:
        raise SummaryError(
            f"run artifacts exceed limit ({artifact_bytes} > {artifact_limit_bytes})")
    recovery_events = sum(int(stats[name]) for name in (
        "recovery_nacks_generated", "recovery_nacks_received",
        "irn_nacks_generated", "irn_nacks_received", "timeout_recoveries",
    ))
    registrations = int(stats["registrations"])
    mechanism = dict(lifecycle)
    mechanism.update({
        "registrations": registrations,
        "selected_registrations": int(stats["selected_registrations"]),
        "proactive_releases": int(stats["proactive_releases"]),
        "completion_releases": int(stats["completion_releases"]),
        "max_active_flows": int(stats["max_active_flows"]),
        "grants_sent": int(stats["grants_sent"]),
        "grants_per_registration": int(stats["grants_sent"]) / registrations,
    })
    return {
        "schema_version": 1,
        "status": "validated_complete",
        "workload": "oflm-churn",
        "provenance": {
            "output_directory": str(output_dir),
            "manifest": str(manifest_path),
            "flow_snapshot": str(snapshot_path),
            "lifecycle_trace": str(lifecycle_path),
            "flow_sha256": base["provenance"]["flow_sha256"],
        },
        "configuration": {
            "selective_registration": selective,
            "proactive_release": proactive,
            "bdp_bytes": bdp_bytes,
            "elephant_bytes": elephant_bytes,
        },
        "validation": {
            **base["validation"],
            "switch_drops": int(stats["switch_drops_total"]),
            "recovery_events": recovery_events,
            "artifact_bytes": artifact_bytes,
        },
        "mechanism": mechanism,
        "performance": {
            "fct_mean_slowdown": metric_value(base, "slowdown_mean"),
            "fct_p99_slowdown": metric_value(base, "slowdown_p99"),
            "queue_mean_bytes": metric_value(base, "queue_bytes_mean"),
            "queue_p99_bytes": metric_value(base, "queue_bytes_p99"),
            "queue_max_bytes": metric_value(base, "queue_bytes_max"),
            "target_receiver_queue": target_queue_metrics(
                one_artifact(output_dir, "_out_queue_stats.txt"), receiver_node),
            "fct_groups": grouped_fct_metrics(output_dir, bdp_bytes, elephant_bytes),
        },
    }


def atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate one OFLM churn run before cross-combination selection")
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--lifecycle", required=True, type=Path)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--artifact-limit-bytes", type=int, default=100 * 1024 * 1024)
    args = parser.parse_args(argv)
    try:
        if args.artifact_limit_bytes <= 0:
            raise SummaryError("artifact limit must be positive")
        result = analyze(
            args.output_dir, args.manifest, args.lifecycle,
            args.artifact_limit_bytes)
        output = (args.json_out or args.output_dir / "oflm_churn_summary.json").resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(output, result)
    except (OSError, ValueError, KeyError, SummaryError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"validated OFLM churn run: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
