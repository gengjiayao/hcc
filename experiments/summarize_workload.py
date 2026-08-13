#!/usr/bin/env python3
"""Strictly validate and summarize one bounded custom-workload run.

The custom workload manifest and the private traffic snapshot made by
``run.py --flow_file`` are treated as the source of truth.  No result is
written unless every generated flow has one matching completion record.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys
from typing import Dict, Iterable, List, Mapping, MutableMapping, Sequence, Tuple

try:
    from experiments.summarize_campaign import (
        GUARD_TOTAL_FIELDS,
        PFC_PRIORITY_FIELDS,
        SummaryError,
        parse_guard_stats,
        parse_pfc,
        percentile,
        queue_summary_metrics,
    )
except ModuleNotFoundError:  # Direct execution from experiments/.
    from summarize_campaign import (
        GUARD_TOTAL_FIELDS,
        PFC_PRIORITY_FIELDS,
        SummaryError,
        parse_guard_stats,
        parse_pfc,
        percentile,
        queue_summary_metrics,
    )


HARD_MAX_FLOWS = 25_000
MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
MAX_RUN_ARTIFACT_BYTES = 100 * 1024 * 1024
CSV_FIELDS = (
    "workload", "completion_semantics", "metric", "scope", "value", "unit", "n"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> Mapping[str, object]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise SummaryError(f"JSON root must be an object: {path}")
    return value


def check_regular_file(path: Path, byte_limit: int) -> None:
    if not path.is_file():
        raise SummaryError(f"missing regular file: {path}")
    size = path.stat().st_size
    if size > byte_limit:
        raise SummaryError(f"artifact exceeds byte limit ({size} > {byte_limit}): {path}")


def one_artifact(output_dir: Path, suffix: str) -> Path:
    matches = sorted(output_dir.glob(f"*{suffix}"))
    if len(matches) != 1:
        raise SummaryError(
            f"expected exactly one *{suffix} artifact in {output_dir}, found {len(matches)}"
        )
    check_regular_file(matches[0], MAX_RUN_ARTIFACT_BYTES)
    return matches[0]


def parse_config(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    check_regular_file(path, 1024 * 1024)
    with path.open(encoding="utf-8", errors="strict") as stream:
        for line_number, line in enumerate(stream, 1):
            parts = line.split()
            if not parts:
                continue
            if len(parts) < 2:
                raise SummaryError(f"malformed config row {path}:{line_number}")
            if parts[0] in values:
                raise SummaryError(f"duplicate config key {parts[0]} in {path}")
            values[parts[0]] = " ".join(parts[1:])
    return values


def parse_snapshot(
    path: Path, hosts: int, max_flows: int
) -> Tuple[List[Dict[str, object]], Dict[str, object]]:
    check_regular_file(path, MAX_SNAPSHOT_BYTES)
    flows: List[Dict[str, object]] = []
    total_bytes = 0
    with path.open(encoding="utf-8", errors="strict") as stream:
        header = stream.readline().strip()
        try:
            declared = int(header)
        except ValueError as exc:
            raise SummaryError(f"invalid flow-count header in {path}: {header!r}") from exc
        if not 1 <= declared <= max_flows:
            raise SummaryError(
                f"declared flow count must be in [1, {max_flows}], got {declared}"
            )
        for line_number, line in enumerate(stream, 2):
            parts = line.split()
            if len(parts) != 5:
                raise SummaryError(f"flow row must have five fields: {path}:{line_number}")
            try:
                src, dst, pg, size = map(int, parts[:4])
                start_s = float(parts[4])
            except ValueError as exc:
                raise SummaryError(f"non-numeric flow row {path}:{line_number}") from exc
            if not 0 <= src < hosts or not 0 <= dst < hosts or src == dst:
                raise SummaryError(f"invalid flow endpoints {src}->{dst}: {path}:{line_number}")
            if not 0 <= pg <= 7:
                raise SummaryError(f"invalid priority group {pg}: {path}:{line_number}")
            if size <= 0:
                raise SummaryError(f"non-positive flow size {size}: {path}:{line_number}")
            if not math.isfinite(start_s) or start_s < 0:
                raise SummaryError(f"invalid flow start {start_s}: {path}:{line_number}")
            start_ns = int(round(start_s * 1_000_000_000))
            flows.append({
                "src": src, "dst": dst, "pg": pg, "size": size,
                "start_s": start_s, "start_ns": start_ns,
            })
            total_bytes += size
    if len(flows) != declared:
        raise SummaryError(f"snapshot declares {declared} flows but contains {len(flows)}")
    return flows, {
        "flow_count": len(flows),
        "total_bytes": total_bytes,
        "first_start_s": min(float(flow["start_s"]) for flow in flows),
        "last_start_s": max(float(flow["start_s"]) for flow in flows),
        "sha256": sha256_file(path),
    }


def validate_manifest(
    manifest: Mapping[str, object], snapshot: Mapping[str, object]
) -> Tuple[str, Mapping[str, object]]:
    if manifest.get("schema_version") != 1:
        raise SummaryError(f"unsupported workload manifest schema: {manifest.get('schema_version')}")
    workload = manifest.get("workload")
    allowed = {"incast", "hybrid", "ring-allreduce", "all-to-all"}
    if workload not in allowed:
        raise SummaryError(f"unsupported workload: {workload!r}")
    parameters = manifest.get("parameters")
    validation = manifest.get("validation")
    if not isinstance(parameters, dict) or not isinstance(validation, dict):
        raise SummaryError("manifest must contain parameter and validation objects")
    if validation.get("status") != "passed":
        raise SummaryError("workload manifest validation status is not passed")
    exact_fields = ("flow_count", "total_bytes", "sha256")
    for field in exact_fields:
        if validation.get(field) != snapshot.get(field):
            raise SummaryError(
                f"manifest {field}={validation.get(field)!r} does not match "
                f"snapshot {snapshot.get(field)!r}"
            )
    for field in ("first_start_s", "last_start_s"):
        try:
            difference = abs(float(validation[field]) - float(snapshot[field]))
        except (KeyError, TypeError, ValueError) as exc:
            raise SummaryError(f"manifest has invalid {field}") from exc
        if difference > 0.5e-9:
            raise SummaryError(f"manifest {field} does not match the traffic snapshot")
    return str(workload), parameters


def parse_fct(path: Path) -> List[Dict[str, object]]:
    check_regular_file(path, MAX_RUN_ARTIFACT_BYTES)
    rows: List[Dict[str, object]] = []
    with path.open(encoding="utf-8", errors="strict") as stream:
        for line_number, line in enumerate(stream, 1):
            parts = line.split()
            if not parts:
                continue
            if len(parts) != 8:
                raise SummaryError(f"FCT row must have eight fields: {path}:{line_number}")
            try:
                src, dst, sport, dport = map(int, parts[:4])
                size, start_ns, duration_ns, ideal_ns = map(int, parts[4:])
            except ValueError as exc:
                raise SummaryError(f"non-integer FCT row {path}:{line_number}") from exc
            if min(src, dst, sport, dport, start_ns) < 0 or duration_ns <= 0:
                raise SummaryError(f"negative or zero FCT field: {path}:{line_number}")
            if src == dst or size <= 0 or ideal_ns <= 0:
                raise SummaryError(f"invalid FCT completion: {path}:{line_number}")
            rows.append({
                "src": src, "dst": dst, "size": size, "start_ns": start_ns,
                "duration_ns": duration_ns, "finish_ns": start_ns + duration_ns,
                "ideal_ns": ideal_ns,
                "fct_us": duration_ns / 1000.0,
                "slowdown": max(1.0, duration_ns / float(ideal_ns)),
            })
    return rows


def flow_key(flow: Mapping[str, object]) -> Tuple[int, int, int, int]:
    return tuple(int(flow[field]) for field in ("src", "dst", "size", "start_ns"))


def validate_completions(
    flows: Sequence[Mapping[str, object]], completions: Sequence[Mapping[str, object]], hosts: int
) -> None:
    if len(completions) != len(flows):
        raise SummaryError(
            f"completed flow count {len(completions)} != generated flow count {len(flows)}"
        )
    for row in completions:
        src, dst = int(row["src"]), int(row["dst"])
        if not 0 <= src < hosts or not 0 <= dst < hosts:
            raise SummaryError(f"completed flow has out-of-range endpoint {src}->{dst}")
    expected = Counter(flow_key(flow) for flow in flows)
    observed = Counter(flow_key(row) for row in completions)
    if expected != observed:
        missing = list((expected - observed).elements())[:3]
        unexpected = list((observed - expected).elements())[:3]
        raise SummaryError(
            f"completion tuples differ from snapshot; missing={missing}, unexpected={unexpected}"
        )


def metric(
    workload: str, semantics: str, name: str, value: float,
    unit: str, count: int, scope: str = "all"
) -> Dict[str, object]:
    if not math.isfinite(float(value)):
        raise SummaryError(f"metric {name} is not finite")
    return {
        "workload": workload,
        "completion_semantics": semantics,
        "metric": name,
        "scope": scope,
        "value": value,
        "unit": unit,
        "n": count,
    }


def completion_metrics(
    workload: str, completions: Sequence[Mapping[str, object]]
) -> Tuple[str, List[Dict[str, object]]]:
    if workload == "ring-allreduce":
        semantics = "open_loop_trace"
    elif workload == "all-to-all":
        semantics = "communication_phase"
    else:
        semantics = "workload_trace"

    count = len(completions)
    start_ns = min(int(row["start_ns"]) for row in completions)
    finish_ns = max(int(row["finish_ns"]) for row in completions)
    span_ns = finish_ns - start_ns
    if span_ns <= 0:
        raise SummaryError("workload completion span must be positive")
    result = [metric(
        workload, semantics, "trace_completion_time_us", span_ns / 1000.0, "us", count
    )]
    if workload == "all-to-all":
        result.append(metric(
            workload, semantics, "communication_phase_cct_us",
            span_ns / 1000.0, "us", count,
        ))

    for field, name, unit in (
        ("fct_us", "fct_us", "us"), ("slowdown", "slowdown", "ratio")
    ):
        values = [float(row[field]) for row in completions]
        result.append(metric(
            workload, semantics, f"{name}_mean", statistics.fmean(values), unit, count
        ))
        for label, percent in (("p95", 95), ("p99", 99)):
            result.append(metric(
                workload, semantics, f"{name}_{label}", percentile(values, percent),
                unit, count,
            ))
        if count >= 1000:
            result.append(metric(
                workload, semantics, f"{name}_p999", percentile(values, 99.9),
                unit, count,
            ))

    total_bits = sum(int(row["size"]) for row in completions) * 8
    result.append(metric(
        workload, semantics, "aggregate_goodput_gbps", total_bits / span_ns,
        "Gbps", count,
    ))

    by_receiver: MutableMapping[int, List[Mapping[str, object]]] = defaultdict(list)
    for row in completions:
        by_receiver[int(row["dst"])].append(row)
    receiver_rates: List[float] = []
    for receiver in sorted(by_receiver):
        rows = by_receiver[receiver]
        receiver_span = max(int(row["finish_ns"]) for row in rows) - min(
            int(row["start_ns"]) for row in rows
        )
        if receiver_span <= 0:
            raise SummaryError(f"receiver {receiver} has a non-positive completion span")
        rate = sum(int(row["size"]) for row in rows) * 8 / receiver_span
        receiver_rates.append(rate)
        result.append(metric(
            workload, semantics, "receiver_incast_goodput_gbps", rate, "Gbps",
            len(rows), f"receiver:{receiver}",
        ))
        flow_rates = [
            int(row["size"]) * 8 / int(row["duration_ns"])
            for row in rows
        ]
        flow_rate_sum_squares = sum(value * value for value in flow_rates)
        flow_rate_jain = (
            sum(flow_rates) ** 2 / (len(flow_rates) * flow_rate_sum_squares)
        )
        result.extend([
            metric(
                workload, semantics, "receiver_incast_flow_goodput_jain",
                flow_rate_jain, "ratio", len(flow_rates), f"receiver:{receiver}",
            ),
            metric(
                workload, semantics, "receiver_incast_flow_goodput_min_gbps",
                min(flow_rates), "Gbps", len(flow_rates), f"receiver:{receiver}",
            ),
            metric(
                workload, semantics, "receiver_incast_flow_goodput_max_gbps",
                max(flow_rates), "Gbps", len(flow_rates), f"receiver:{receiver}",
            ),
        ])
    squared_sum = sum(receiver_rates) ** 2
    sum_squares = sum(value * value for value in receiver_rates)
    if not receiver_rates or sum_squares <= 0:
        raise SummaryError("cannot calculate receiver goodput fairness")
    result.extend([
        metric(
            workload, semantics, "receiver_aggregate_goodput_jain",
            squared_sum / (len(receiver_rates) * sum_squares), "ratio", len(receiver_rates),
        ),
        metric(
            workload, semantics, "receiver_aggregate_goodput_min_gbps",
            min(receiver_rates), "Gbps", len(receiver_rates),
        ),
        metric(
            workload, semantics, "receiver_aggregate_goodput_max_gbps",
            max(receiver_rates), "Gbps", len(receiver_rates),
        ),
    ])
    return semantics, result


def instrumentation_metrics(
    workload: str, semantics: str, output_dir: Path
) -> List[Dict[str, object]]:
    queue_path = one_artifact(output_dir, "_out_queue_stats.txt")
    stats_path = one_artifact(output_dir, "_out_guard_stats.txt")
    pfc_path = one_artifact(output_dir, "_out_pfc.txt")
    stats = parse_guard_stats(stats_path)
    pfc = parse_pfc(pfc_path)
    raw_priorities = pfc["pfc_event_priority"]
    summary_priorities = stats["pfc_priority"]
    if set(raw_priorities) - set(summary_priorities):
        raise SummaryError("PFC trace contains priorities absent from GUARD stats")
    for priority, raw in raw_priorities.items():
        summary = summary_priorities[priority]
        for field in ("pause_count", "resume_count"):
            if int(raw[field]) != int(summary[field]):
                raise SummaryError(
                    f"PFC q{priority} {field} trace={raw[field]} stats={summary[field]}"
                )

    result: List[Dict[str, object]] = []
    for name, _category, value, count in queue_summary_metrics(queue_path):
        unit = "samples" if name == "queue_sample_count" else "bytes"
        result.append(metric(workload, semantics, name, value, unit, count))
    for name in GUARD_TOTAL_FIELDS:
        unit = "bytes" if name == "irn_retransmit_bytes" else "count"
        result.append(metric(workload, semantics, name, float(stats[name]), unit, 1))
    for name in ("switch_drops_ingress", "switch_drops_egress", "switch_drops_total"):
        result.append(metric(workload, semantics, name, float(stats[name]), "packets", 1))
    result.extend([
        metric(
            workload, semantics, "pfc_pause_events", float(pfc["pfc_pause_events"]),
            "events", 1,
        ),
        metric(
            workload, semantics, "pfc_resume_events", float(pfc["pfc_resume_events"]),
            "events", 1,
        ),
    ])
    for name in PFC_PRIORITY_FIELDS:
        unit = "ns" if name in ("cumulative_pause_ns", "max_pause_ns") else "count"
        result.append(metric(workload, semantics, f"pfc_{name}", float(stats[f"pfc_{name}"]), unit, 1))
    for priority in sorted(summary_priorities):
        values = summary_priorities[priority]
        for name in PFC_PRIORITY_FIELDS:
            unit = "ns" if name in ("cumulative_pause_ns", "max_pause_ns") else "count"
            result.append(metric(
                workload, semantics, f"pfc_{name}", float(values[name]), unit, 1,
                f"priority:{priority}",
            ))
    return result


def resolve_snapshot(output_dir: Path, requested: Path | None) -> Path:
    if requested is not None:
        return requested.resolve()
    matches = sorted(output_dir.glob("*_input_flow.txt"))
    if len(matches) != 1:
        raise SummaryError(
            f"expected exactly one *_input_flow.txt snapshot in {output_dir}, found {len(matches)}"
        )
    return matches[0]


def summarize(output_dir: Path, manifest_path: Path, snapshot_path: Path | None = None) -> Dict[str, object]:
    output_dir = output_dir.resolve()
    if not output_dir.is_dir():
        raise SummaryError(f"output directory does not exist: {output_dir}")
    manifest_path = manifest_path.resolve()
    check_regular_file(manifest_path, 1024 * 1024)
    manifest = read_json(manifest_path)
    parameters = manifest.get("parameters")
    if not isinstance(parameters, dict):
        raise SummaryError("manifest parameters must be an object")
    try:
        hosts = int(parameters["hosts"])
        manifest_limit = int(parameters["max_flows"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SummaryError("manifest hosts/max_flows must be integers") from exc
    if hosts < 2:
        raise SummaryError("manifest hosts must be at least two")
    if not 1 <= manifest_limit <= HARD_MAX_FLOWS:
        raise SummaryError(f"manifest max_flows must be in [1, {HARD_MAX_FLOWS}]")

    snapshot_path = resolve_snapshot(output_dir, snapshot_path)
    flows, snapshot = parse_snapshot(snapshot_path, hosts, manifest_limit)
    workload, _ = validate_manifest(manifest, snapshot)

    config = parse_config(output_dir / "config.txt")
    if Path(config.get("FLOW_FILE", "")).name != snapshot_path.name:
        raise SummaryError("config FLOW_FILE does not name the analyzed traffic snapshot")
    try:
        configured_limit = int(config["PREFLIGHT_MAX_FLOWS"])
    except (KeyError, ValueError) as exc:
        raise SummaryError("config lacks a valid PREFLIGHT_MAX_FLOWS") from exc
    if configured_limit < len(flows) or configured_limit > HARD_MAX_FLOWS:
        raise SummaryError("config preflight flow limit is inconsistent with the bounded workload")

    completions = parse_fct(one_artifact(output_dir, "_out_fct.txt"))
    validate_completions(flows, completions, hosts)
    semantics, metrics = completion_metrics(workload, completions)
    metrics.extend(instrumentation_metrics(workload, semantics, output_dir))
    return {
        "schema_version": 1,
        "status": "validated_complete",
        "workload": workload,
        "completion_semantics": semantics,
        "interpretation": (
            "scheduled open-loop flow trace; no collective dependency completion claim"
            if workload == "ring-allreduce"
            else "all-to-all communication phase completion"
            if workload == "all-to-all"
            else "workload trace completion"
        ),
        "provenance": {
            "output_directory": str(output_dir),
            "manifest": str(manifest_path),
            "flow_snapshot": str(snapshot_path),
            "flow_sha256": snapshot["sha256"],
        },
        "validation": {
            "generated_flow_count": len(flows),
            "completed_flow_count": len(completions),
            "hosts": hosts,
            "total_flow_bytes": snapshot["total_bytes"],
            "endpoints_sizes_starts_match": True,
        },
        "metrics": metrics,
    }


def atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, path)


def atomic_csv(path: Path, rows: Iterable[Mapping[str, object]]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Strictly summarize a bounded run.py custom-workload output"
    )
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--flow-file", type=Path,
        help="private flow snapshot (default: the sole *_input_flow.txt in OUTPUT_DIR)",
    )
    parser.add_argument("--json-out", type=Path, help="default: OUTPUT_DIR/workload_summary.json")
    parser.add_argument("--csv-out", type=Path, help="default: OUTPUT_DIR/workload_summary.csv")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        summary = summarize(args.output_dir, args.manifest, args.flow_file)
        json_path = (args.json_out or args.output_dir / "workload_summary.json").resolve()
        csv_path = (args.csv_out or args.output_dir / "workload_summary.csv").resolve()
        if json_path == csv_path:
            raise SummaryError("JSON and CSV output paths must differ")
        for path in (json_path, csv_path):
            path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(json_path, summary)
        atomic_csv(csv_path, summary["metrics"])
    except (OSError, ValueError, KeyError, SummaryError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"validated {summary['validation']['completed_flow_count']} completed flows")
    print(f"JSON: {json_path}")
    print(f"CSV: {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
