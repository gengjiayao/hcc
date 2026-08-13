#!/usr/bin/env python3
"""Audit a bounded, preregistered DCQCN-like qualification ladder.

This tool intentionally emits no latency, slowdown, or throughput metrics.  A
qualification run can authorize a later matched campaign, but it is not itself
a performance comparison.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
from pathlib import Path
import sys
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

try:
    from experiments.summarize_campaign import (
        PFC_PRIORITY_FIELDS,
        SummaryError,
        parse_guard_stats,
        parse_pfc,
    )
    from experiments.summarize_workload import (
        HARD_MAX_FLOWS,
        MAX_RUN_ARTIFACT_BYTES,
        check_regular_file,
        one_artifact,
        parse_config,
        parse_fct,
        parse_snapshot,
        read_json,
        resolve_snapshot,
        sha256_file,
        validate_completions,
        validate_manifest,
    )
except ModuleNotFoundError:  # Direct execution from experiments/.
    from summarize_campaign import (
        PFC_PRIORITY_FIELDS,
        SummaryError,
        parse_guard_stats,
        parse_pfc,
    )
    from summarize_workload import (
        HARD_MAX_FLOWS,
        MAX_RUN_ARTIFACT_BYTES,
        check_regular_file,
        one_artifact,
        parse_config,
        parse_fct,
        parse_snapshot,
        read_json,
        resolve_snapshot,
        sha256_file,
        validate_completions,
        validate_manifest,
    )


DCQCN_FIELDS = (
    "cnp_generated_ecn",
    "cnp_generated_ooo",
    "cnp_received",
    "alpha_updates",
    "alpha_cnp_updates",
    "rate_decrease_events",
    "actual_rate_decreases",
    "rate_increase_events",
    "actual_rate_increases",
)
CSV_FIELDS = (
    "level", "output_id", "flow_sha256", "kmin_100g", "kmax_100g",
    "pmax_100g", "generated_flow_count", "completed_flow_count",
    *DCQCN_FIELDS,
    "cnp_trace_unique", "recovery_nacks_generated", "recovery_nacks_received",
    "irn_nacks_generated", "irn_nacks_received", "irn_retransmit_packets",
    "irn_retransmit_bytes", "timeout_recoveries", "switch_drops_ingress",
    "switch_drops_egress", "switch_drops_total", "pfc_q4_pause_count",
    "pfc_q4_resume_count", "pfc_q4_matched_intervals",
    "pfc_q4_cumulative_pause_ns", "pfc_q4_max_pause_ns",
    "pfc_q4_unmatched_pauses", "pfc_q4_unmatched_resumes", "output_bytes",
    "mechanism_pass", "completion_pass", "safety_pass", "storage_pass",
    "admitted", "exclusion_reason",
)
REQUIRED_CONFIG = {
    "CC_MODE": "1",
    "ENABLE_PFC": "1",
    "ENABLE_IRN": "0",
    "RANDOM_SEED": "1",
    "MONITOR_PROFILE": "bulk",
    "ANALYSIS_WARMUP_TIME": "0.0",
    "PREFLIGHT_MAX_FLOWS": "1000",
    "ERROR_RATE_PER_LINK": "0.0",
}
EXPECTED_TOPOLOGY = "leaf_spine_16_100G_OS4.txt"
ACTIVE_RATE_BPS = 100_000_000_000


def parse_dcqcn_stats(path: Path, expected_nodes: int = 16) -> Dict[str, int]:
    """Parse cumulative mode-1 counters and cross-check node/total rows."""
    check_regular_file(path, MAX_RUN_ARTIFACT_BYTES)
    nodes: Dict[int, Dict[str, int]] = {}
    total: Dict[str, int] | None = None
    header_seen = False
    with path.open(encoding="utf-8", errors="strict") as stream:
        for line_number, line in enumerate(stream, 1):
            parts = line.split()
            if not parts:
                continue
            if parts[0] == "dcqcn" and parts[1:] == ["node_id", *DCQCN_FIELDS]:
                if header_seen:
                    raise SummaryError(f"duplicate DCQCN header: {path}:{line_number}")
                header_seen = True
            elif parts[0] == "dcqcn" and len(parts) == len(DCQCN_FIELDS) + 2:
                try:
                    node_id = int(parts[1])
                    values = tuple(map(int, parts[2:]))
                except ValueError as exc:
                    raise SummaryError(f"non-integer DCQCN row: {path}:{line_number}") from exc
                if node_id in nodes or min(values) < 0:
                    raise SummaryError(f"invalid DCQCN node row: {path}:{line_number}")
                nodes[node_id] = dict(zip(DCQCN_FIELDS, values))
            elif parts[0] == "dcqcn_total":
                if total is not None or len(parts) != len(DCQCN_FIELDS) + 1:
                    raise SummaryError(f"invalid DCQCN total row: {path}:{line_number}")
                try:
                    values = tuple(map(int, parts[1:]))
                except ValueError as exc:
                    raise SummaryError(f"non-integer DCQCN total: {path}:{line_number}") from exc
                if min(values) < 0:
                    raise SummaryError(f"negative DCQCN total: {path}:{line_number}")
                total = dict(zip(DCQCN_FIELDS, values))
    if not header_seen or total is None:
        raise SummaryError(f"missing cumulative DCQCN rows: {path}")
    if set(nodes) != set(range(expected_nodes)):
        raise SummaryError(f"DCQCN rows do not cover nodes [0,{expected_nodes}): {path}")
    for field in DCQCN_FIELDS:
        node_sum = sum(node[field] for node in nodes.values())
        if node_sum != total[field]:
            raise SummaryError(
                f"DCQCN {field} node sum {node_sum} != total {total[field]}: {path}"
            )
    return total


def parse_cnp_trace(path: Path) -> Dict[str, int]:
    """Sum legacy 100-us CNP buckets without treating them as performance data."""
    check_regular_file(path, MAX_RUN_ARTIFACT_BYTES)
    totals = {"cnp_generated_ecn": 0, "cnp_generated_ooo": 0, "cnp_trace_unique": 0}
    rows = 0
    with path.open(encoding="utf-8", errors="strict") as stream:
        for line_number, line in enumerate(stream, 1):
            parts = line.split()
            if not parts:
                continue
            if len(parts) != 5:
                raise SummaryError(f"malformed CNP trace row: {path}:{line_number}")
            try:
                time_ns, node_id, ecn, ooo, unique = map(int, parts)
            except ValueError as exc:
                raise SummaryError(f"non-integer CNP trace row: {path}:{line_number}") from exc
            if time_ns < 0 or not 0 <= node_id < 16 or min(ecn, ooo, unique) < 0:
                raise SummaryError(f"invalid CNP trace values: {path}:{line_number}")
            if not max(ecn, ooo) <= unique <= ecn + ooo:
                raise SummaryError(f"inconsistent CNP bucket union: {path}:{line_number}")
            totals["cnp_generated_ecn"] += ecn
            totals["cnp_generated_ooo"] += ooo
            totals["cnp_trace_unique"] += unique
            rows += 1
    if rows == 0:
        raise SummaryError(f"empty CNP trace: {path}")
    return totals


def parse_threshold_map(value: str, name: str) -> Dict[int, float]:
    parts = value.split()
    try:
        count = int(parts[0])
    except (IndexError, ValueError) as exc:
        raise SummaryError(f"invalid {name} header") from exc
    if count <= 0 or len(parts) != 1 + 2 * count:
        raise SummaryError(f"invalid {name} map length")
    result: Dict[int, float] = {}
    for index in range(count):
        try:
            rate = int(parts[1 + 2 * index])
            threshold = float(parts[2 + 2 * index])
        except ValueError as exc:
            raise SummaryError(f"non-numeric {name} entry") from exc
        if rate in result or rate <= 0 or not math.isfinite(threshold):
            raise SummaryError(f"invalid {name} entry")
        result[rate] = threshold
    return result


def qualification_status(rows: Sequence[Mapping[str, object]]) -> Tuple[str | None, str]:
    selected = next((str(row["level"]) for row in rows if row["admitted"]), None)
    return selected, "qualified_pilot" if selected is not None else "invalid_no_safe_level"


def output_bytes(output_dir: Path) -> int:
    total = 0
    for path in output_dir.iterdir():
        if path.is_file():
            check_regular_file(path, MAX_RUN_ARTIFACT_BYTES)
            total += path.stat().st_size
    return total


def _require_config(config: Mapping[str, str]) -> None:
    for key, expected in REQUIRED_CONFIG.items():
        if config.get(key) != expected:
            raise SummaryError(f"config {key}={config.get(key)!r}, expected {expected!r}")
    if Path(config.get("TOPOLOGY_FILE", "")).name != EXPECTED_TOPOLOGY:
        raise SummaryError("qualification run uses the wrong topology")
    try:
        start = float(config["FLOWGEN_START_TIME"])
        stop = float(config["FLOWGEN_STOP_TIME"])
    except (KeyError, ValueError) as exc:
        raise SummaryError("invalid flow-generation interval") from exc
    if not math.isclose(start, 2.0) or not math.isclose(stop - start, 0.010):
        raise SummaryError("qualification run must use the frozen 10-ms trace interval")


def _validate_pfc(stats: Mapping[str, object], raw: Mapping[str, object]) -> None:
    priorities = stats["pfc_priority"]
    raw_priorities = raw["pfc_event_priority"]
    if set(raw_priorities) - set(priorities):
        raise SummaryError("PFC trace contains priorities absent from summary")
    for priority, values in raw_priorities.items():
        for raw_field, summary_field in (
            ("pause_count", "pause_count"), ("resume_count", "resume_count")
        ):
            if int(values[raw_field]) != int(priorities[priority][summary_field]):
                raise SummaryError(f"PFC priority {priority} does not match summary")


def audit_run(
    run: Mapping[str, object],
    level: Mapping[str, object],
    admission: Mapping[str, object],
) -> Dict[str, object]:
    output_dir = Path(str(run["output_dir"])).resolve()
    manifest_path = Path(str(run["manifest"])).resolve()
    if not output_dir.is_dir():
        raise SummaryError(f"missing output directory: {output_dir}")
    manifest = read_json(manifest_path)
    parameters = manifest.get("parameters")
    if not isinstance(parameters, dict):
        raise SummaryError("manifest parameters must be an object")
    try:
        hosts = int(parameters["hosts"])
        max_flows = int(parameters["max_flows"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SummaryError("invalid manifest hosts/max_flows") from exc
    if hosts != 16 or not 1 <= max_flows <= HARD_MAX_FLOWS:
        raise SummaryError("qualification manifest must describe the frozen 16-host workload")
    if parameters.get("seed") != 1 or parameters.get("priority_group") != 4:
        raise SummaryError("qualification manifest must be seed 1 and PG4")

    snapshot_path = resolve_snapshot(output_dir, None)
    flows, snapshot = parse_snapshot(snapshot_path, hosts, max_flows)
    workload, _ = validate_manifest(manifest, snapshot)
    if workload != "hybrid" or any(int(flow["pg"]) != 4 for flow in flows):
        raise SummaryError("qualification traffic must be the frozen all-PG4 hybrid trace")

    config = parse_config(output_dir / "config.txt")
    _require_config(config)
    if Path(config.get("FLOW_FILE", "")).name != snapshot_path.name:
        raise SummaryError("config FLOW_FILE does not name the private flow snapshot")
    maps = {
        "kmin": parse_threshold_map(config.get("KMIN_MAP", ""), "KMIN_MAP"),
        "kmax": parse_threshold_map(config.get("KMAX_MAP", ""), "KMAX_MAP"),
        "pmax": parse_threshold_map(config.get("PMAX_MAP", ""), "PMAX_MAP"),
    }
    expected = {
        "kmin": float(level["kmin_100g"]),
        "kmax": float(level["kmax_100g"]),
        "pmax": float(level["pmax_100g"]),
    }
    for name, threshold in expected.items():
        if ACTIVE_RATE_BPS not in maps[name] or not math.isclose(
            maps[name][ACTIVE_RATE_BPS], threshold, abs_tol=1e-12
        ):
            raise SummaryError(f"{name} 100-Gb/s value is not the preregistered threshold")

    completions = parse_fct(one_artifact(output_dir, "_out_fct.txt"))
    validate_completions(flows, completions, hosts)
    stats_path = one_artifact(output_dir, "_out_guard_stats.txt")
    dcqcn = parse_dcqcn_stats(stats_path, hosts)
    cnp = parse_cnp_trace(one_artifact(output_dir, "_out_cnp.txt"))
    for field in ("cnp_generated_ecn", "cnp_generated_ooo"):
        if cnp[field] != dcqcn[field]:
            raise SummaryError(f"CNP trace {field} does not match cumulative counters")
    stats = parse_guard_stats(stats_path)
    if dcqcn["cnp_generated_ooo"] != int(stats["recovery_nacks_generated"]):
        raise SummaryError("OOO CNP generation does not match recovery NACK generation")
    raw_pfc = parse_pfc(one_artifact(output_dir, "_out_pfc.txt"))
    _validate_pfc(stats, raw_pfc)
    pfc_q4 = stats["pfc_priority"].get(4)
    if pfc_q4 is None:
        raise SummaryError("missing PG4 PFC summary")

    bytes_used = output_bytes(output_dir)
    generated = len(flows)
    completed = len(completions)
    mechanism_pass = (
        dcqcn["cnp_generated_ecn"] >= int(admission["cnp_generated_ecn_min"])
        and dcqcn["cnp_received"] >= int(admission["cnp_received_min"])
        and dcqcn["actual_rate_decreases"] >= int(admission["actual_rate_decreases_min"])
    )
    completion_pass = (
        generated == int(admission["completed_flows"])
        and completed == int(admission["completed_flows"])
    )
    recovery_fields = (
        "recovery_nacks_generated", "recovery_nacks_received", "irn_nacks_generated",
        "irn_nacks_received", "irn_retransmit_packets", "irn_retransmit_bytes",
        "timeout_recoveries",
    )
    safety_pass = (
        int(stats["switch_drops_total"]) == int(admission["switch_drops_total"])
        and sum(int(stats[field]) for field in recovery_fields)
        == int(admission["recovery_counters_total"])
    )
    storage_pass = bytes_used < int(admission["output_bytes_max"])
    admitted = mechanism_pass and completion_pass and safety_pass and storage_pass
    failures = [
        name for name, passed in (
            ("mechanism", mechanism_pass), ("completion", completion_pass),
            ("safety", safety_pass), ("storage", storage_pass),
        ) if not passed
    ]
    row: Dict[str, object] = {
        "level": str(level["level"]),
        "output_id": output_dir.name,
        "flow_sha256": str(snapshot["sha256"]),
        "kmin_100g": int(expected["kmin"]),
        "kmax_100g": int(expected["kmax"]),
        "pmax_100g": expected["pmax"],
        "generated_flow_count": generated,
        "completed_flow_count": completed,
        **dcqcn,
        "cnp_trace_unique": cnp["cnp_trace_unique"],
        "recovery_nacks_generated": int(stats["recovery_nacks_generated"]),
        "recovery_nacks_received": int(stats["recovery_nacks_received"]),
        "irn_nacks_generated": int(stats["irn_nacks_generated"]),
        "irn_nacks_received": int(stats["irn_nacks_received"]),
        "irn_retransmit_packets": int(stats["irn_retransmit_packets"]),
        "irn_retransmit_bytes": int(stats["irn_retransmit_bytes"]),
        "timeout_recoveries": int(stats["timeout_recoveries"]),
        "switch_drops_ingress": int(stats["switch_drops_ingress"]),
        "switch_drops_egress": int(stats["switch_drops_egress"]),
        "switch_drops_total": int(stats["switch_drops_total"]),
        **{f"pfc_q4_{field}": int(pfc_q4[field]) for field in PFC_PRIORITY_FIELDS},
        "output_bytes": bytes_used,
        "mechanism_pass": mechanism_pass,
        "completion_pass": completion_pass,
        "safety_pass": safety_pass,
        "storage_pass": storage_pass,
        "admitted": admitted,
        "exclusion_reason": "" if admitted else "+".join(failures),
    }
    return row


def audit(index_path: Path) -> Tuple[List[Dict[str, object]], Dict[str, object]]:
    index = read_json(index_path)
    if index.get("schema_version") != 1:
        raise SummaryError("unsupported run-index schema")
    ladder_path = Path(str(index.get("ladder", ""))).resolve()
    ladder = read_json(ladder_path)
    if ladder.get("schema_version") != 1 or ladder.get("qualification_seed") != 1:
        raise SummaryError("unsupported or non-seed-1 ladder")
    levels = ladder.get("ladder")
    runs = index.get("runs")
    admission = ladder.get("admission")
    if not isinstance(levels, list) or not isinstance(runs, list) or not isinstance(admission, dict):
        raise SummaryError("ladder/index has invalid structure")
    if [level.get("level") for level in levels] != ["L0", "L1", "L2"]:
        raise SummaryError("qualification ladder must be exactly L0,L1,L2")
    by_level = {str(run.get("level")): run for run in runs if isinstance(run, dict)}
    if set(by_level) != {"L0", "L1", "L2"} or len(runs) != 3:
        raise SummaryError("run index must contain one run for each ladder level")
    rows = [audit_run(by_level[str(level["level"])], level, admission) for level in levels]
    flow_hashes = {str(row["flow_sha256"]) for row in rows}
    manifests = {sha256_file(Path(str(by_level[str(level["level"])]["manifest"]))) for level in levels}
    if len(flow_hashes) != 1 or len(manifests) != 1:
        raise SummaryError("qualification levels do not share one exact frozen trace/manifest")
    selected, status = qualification_status(rows)
    report: Dict[str, object] = {
        "schema_version": 1,
        "status": status,
        "qualification_seed": 1,
        "selected_level": selected,
        "flow_sha256": next(iter(flow_hashes)),
        "manifest_sha256": next(iter(manifests)),
        "selection_rule": ladder.get("selection_rule"),
        "formal_campaign_authorized": selected is not None,
        "performance_eligible": False,
        "paired_comparison_performed": False,
        "performance_metrics_emitted": False,
        "exclusion_reason": (
            None if selected is not None else
            "All preregistered levels had switch drops and/or recovery/timeout activity."
        ),
        "mode_scope": {
            "label": "repository DCQCN-like (Mellanox-style mode 1)",
            "supported_path": (
                "switch probabilistic ECN marking; ECN/OOO feedback bit on ACK/NACK; "
                "sender alpha, periodic decrease, fast/additive/hyper increase state"
            ),
            "limitations": [
                "Feedback is piggybacked on ACK/NACK rather than standalone rate-limited CNP packets.",
                "Out-of-order recovery can set the same feedback bit and is counted separately.",
                "Defaults are repository-specific (including EWMA gain 1/256) and not NIC-validated.",
                "PMAX_MAP is checked at the active 100-Gb/s rate; integer formatting changes lower-rate entries.",
                "The qualification pilot is not a standards-conformance or hardware validation.",
            ],
        },
        "runs": rows,
        "source": {
            "run_index": str(index_path.resolve()),
            "ladder": str(ladder_path),
        },
    }
    return rows, report


def atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, path)


def atomic_csv(path: Path, rows: Iterable[Mapping[str, object]]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit a preregistered DCQCN-like qualification ladder")
    parser.add_argument("run_index", type=Path)
    parser.add_argument("--analysis-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        rows, report = audit(args.run_index)
        args.analysis_dir.mkdir(parents=True, exist_ok=True)
        csv_path = args.analysis_dir / "qualification_runs.csv"
        json_path = args.analysis_dir / "admission.json"
        atomic_csv(csv_path, rows)
        atomic_json(json_path, report)
    except (OSError, ValueError, KeyError, TypeError, SummaryError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"status: {report['status']}")
    print(f"formal campaign authorized: {report['formal_campaign_authorized']}")
    print(f"CSV: {csv_path.resolve()}")
    print(f"JSON: {json_path.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
