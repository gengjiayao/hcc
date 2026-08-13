#!/usr/bin/env python3
"""Strictly validate one bounded repeated-incast PFC/IRN recovery run."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import statistics
import sys
from typing import Dict, Mapping, Sequence

try:
    from experiments.summarize_campaign import (
        SummaryError, parse_guard_stats, parse_port_queue_summaries, percentile,
    )
    from experiments.summarize_workload import (
        one_artifact, parse_config, parse_fct, parse_snapshot, read_json,
        resolve_snapshot, summarize,
    )
except ModuleNotFoundError:
    from summarize_campaign import (
        SummaryError, parse_guard_stats, parse_port_queue_summaries, percentile,
    )
    from summarize_workload import (
        one_artifact, parse_config, parse_fct, parse_snapshot, read_json,
        resolve_snapshot, summarize,
    )


def fct_metrics(output_dir: Path) -> Dict[str, float]:
    rows = parse_fct(one_artifact(output_dir, "_out_fct.txt"))
    durations = [float(row["fct_us"]) for row in rows]
    slowdowns = [float(row["slowdown"]) for row in rows]
    return {
        "flows": len(rows),
        "duration_mean_us": statistics.fmean(durations),
        "duration_p95_us": percentile(durations, 95),
        "duration_p99_us": percentile(durations, 99),
        "slowdown_mean": statistics.fmean(slowdowns),
        "slowdown_p95": percentile(slowdowns, 95),
        "slowdown_p99": percentile(slowdowns, 99),
    }


def metric_value(summary: Mapping[str, object], name: str) -> float:
    values = [
        float(row["value"]) for row in summary["metrics"]
        if row["metric"] == name and row.get("scope") == "all"
    ]
    if len(values) != 1:
        raise SummaryError(f"expected one all-scope metric {name}, found {len(values)}")
    return values[0]


def target_queue(path: Path, receiver: int) -> Dict[str, object]:
    matches = [
        row for row in parse_port_queue_summaries(path)
        if int(row["neighbor_id"]) == receiver
    ]
    if len(matches) != 1:
        raise SummaryError(
            f"expected one switch egress to receiver {receiver}, found {len(matches)}")
    row = matches[0]
    return {
        "node_id": int(row["node_id"]), "if_index": int(row["if_index"]),
        "neighbor_id": int(row["neighbor_id"]), "samples": int(row["samples"]),
        "positive_samples": int(row["positive_samples"]),
        "average_bytes": float(row["average_bytes"]),
        "positive_average_bytes": float(row["positive_average_bytes"]),
        "p95_bytes": float(row["p95_bytes"]), "p99_bytes": float(row["p99_bytes"]),
        "max_bytes": float(row["max_bytes"]), "tx_bytes": int(row["tx_bytes"]),
    }


def analyze(output_dir: Path, manifest_path: Path,
            artifact_limit_bytes: int) -> Dict[str, object]:
    output_dir = output_dir.resolve()
    manifest_path = manifest_path.resolve()
    manifest = read_json(manifest_path)
    if manifest.get("workload") != "incast":
        raise SummaryError("recovery manifest workload must be incast")
    parameters = manifest.get("parameters")
    if not isinstance(parameters, dict):
        raise SummaryError("manifest parameters must be an object")
    base = summarize(output_dir, manifest_path)
    snapshot_path = resolve_snapshot(output_dir, None)
    flows, snapshot = parse_snapshot(
        snapshot_path, int(parameters["hosts"]), int(parameters["max_flows"]))
    receivers = {int(flow["dst"]) for flow in flows}
    if len(receivers) != 1:
        raise SummaryError("recovery workload must use exactly one receiver")
    receiver = next(iter(receivers))
    config = parse_config(output_dir / "config.txt")
    try:
        pfc = int(config["ENABLE_PFC"])
        irn = int(config["ENABLE_IRN"])
        error_rate = float(config["ERROR_RATE_PER_LINK"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SummaryError("config lacks valid PFC/IRN/error settings") from exc
    if (pfc, irn) not in ((1, 0), (0, 1)):
        raise SummaryError("recovery arm must be exactly PFC-on or IRN-on")

    stats = parse_guard_stats(one_artifact(output_dir, "_out_guard_stats.txt"))
    artifact_bytes = sum(
        path.stat().st_size for path in output_dir.rglob("*") if path.is_file())
    if artifact_bytes > artifact_limit_bytes:
        raise SummaryError(
            f"run artifacts exceed limit ({artifact_bytes} > {artifact_limit_bytes})")
    pfc_unmatched = int(stats["pfc_unmatched_pauses"]) + int(stats["pfc_unmatched_resumes"])
    mechanism_names = (
        "recovery_nacks_generated", "recovery_nacks_received", "irn_nacks_generated",
        "irn_nacks_received", "irn_retransmit_packets", "irn_retransmit_bytes",
        "timeout_recoveries", "switch_drops_ingress", "switch_drops_egress",
        "switch_drops_total", "pfc_pause_count", "pfc_resume_count",
        "pfc_matched_intervals", "pfc_cumulative_pause_ns", "pfc_max_pause_ns",
        "pfc_unmatched_pauses", "pfc_unmatched_resumes",
    )
    mechanism = {name: int(stats[name]) for name in mechanism_names}
    mechanism["pfc_unmatched_events"] = pfc_unmatched
    queue_path = one_artifact(output_dir, "_out_queue_stats.txt")
    performance = fct_metrics(output_dir)
    performance.update({
        "queue_average_bytes": metric_value(base, "queue_bytes_mean"),
        "queue_p95_bytes": metric_value(base, "queue_bytes_p95"),
        "queue_p99_bytes": metric_value(base, "queue_bytes_p99"),
        "queue_max_bytes": metric_value(base, "queue_bytes_max"),
        "target_receiver_queue": target_queue(queue_path, receiver),
    })
    return {
        "schema_version": 1, "status": "validated_complete", "workload": "incast",
        "arm": "pfc" if pfc else "irn",
        "configuration": {
            "pfc": pfc, "irn": irn, "error_rate_per_link": error_rate,
            "buffer_mb": int(config["BUFFER_SIZE"]), "receiver": receiver,
        },
        "provenance": {
            "output_directory": str(output_dir), "manifest": str(manifest_path),
            "flow_snapshot": str(snapshot_path), "flow_sha256": snapshot["sha256"],
        },
        "validation": {
            **base["validation"], "artifact_bytes": artifact_bytes,
        },
        "mechanism": mechanism,
        "performance": performance,
    }


def atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--artifact-limit-bytes", type=int, default=100 * 1024 * 1024)
    args = parser.parse_args(argv)
    try:
        if args.artifact_limit_bytes <= 0:
            raise SummaryError("artifact limit must be positive")
        result = analyze(args.output_dir, args.manifest, args.artifact_limit_bytes)
        output = (args.json_out or args.output_dir / "recovery_summary.json").resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(output, result)
    except (OSError, ValueError, KeyError, SummaryError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"validated recovery run: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
