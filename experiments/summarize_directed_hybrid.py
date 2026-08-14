#!/usr/bin/env python3
"""Strictly aggregate the frozen GUARD/HPCC/Homa directed-hybrid matrix."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

try:
    from experiments.summarize_campaign import SummaryError
    from experiments.summarize_workload import parse_fct
except ModuleNotFoundError:
    from summarize_campaign import SummaryError
    from summarize_workload import parse_fct


ARMS = ("guard", "hpcc", "homa")
CC_MODES = {"guard": 11, "hpcc": 3, "homa": 12}
T95_DF4 = 2.7764451051977987
ZERO_FIELDS = (
    "switch_drops_total", "recovery_nacks_generated", "recovery_nacks_received",
    "irn_nacks_generated", "irn_nacks_received", "irn_retransmit_packets",
    "irn_retransmit_bytes", "timeout_recoveries",
)
SUMMARY_FIELDS = (
    "trace_completion_time_us", "fct_us_mean", "fct_us_p95", "fct_us_p99",
    "slowdown_mean", "slowdown_p95", "slowdown_p99",
    "aggregate_goodput_gbps", "queue_bytes_mean", "queue_bytes_p95",
    "queue_bytes_p99", "queue_bytes_max", "grants_sent", "grants_received",
    "hpcc_actual_rate_changes", "hpcc_valid_feedback", "reactive_binding_updates",
    "grant_binding_updates", "reactive_binding_rate_changes",
    "grant_binding_rate_changes",
    "switch_drops_total", "recovery_nacks_generated", "recovery_nacks_received",
    "irn_nacks_generated", "irn_nacks_received", "irn_retransmit_packets",
    "irn_retransmit_bytes", "timeout_recoveries", "pfc_matched_intervals",
    "pfc_cumulative_pause_ns", "homa_data_packets", "homa_data_bytes",
    "homa_retransmit_packets", "homa_grants_sent", "homa_grants_received",
    "homa_resends_sent", "homa_resends_received",
    "homa_completion_notices_sent", "homa_completion_notices_received",
    "homa_messages_tracked", "homa_messages_completed", "homa_max_pending_messages",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def mean_ci(values: Sequence[float]) -> Dict[str, float]:
    if len(values) != 5:
        raise SummaryError(f"formal t95 requires five values, got {len(values)}")
    mean = statistics.fmean(values)
    half_width = T95_DF4 * statistics.stdev(values) / math.sqrt(5)
    return {
        "n": 5, "mean": mean, "ci95_low": mean - half_width,
        "ci95_high": mean + half_width, "ci95_half_width": half_width,
        "t_critical": T95_DF4,
    }


def parse_config(path: Path) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="strict").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            result[parts[0]] = parts[1]
    return result


def metric_map(summary: Mapping[str, object]) -> Dict[Tuple[str, str], float]:
    return {
        (str(row["metric"]), str(row["scope"])): float(row["value"])
        for row in summary["metrics"]
    }


def parse_flow_geometry(path: Path) -> List[Tuple[int, int, int, int, float]]:
    lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    if not lines or int(lines[0]) != 15 or len(lines) != 16:
        raise SummaryError(f"directed hybrid must contain exactly 15 flows: {path}")
    flows = []
    for line in lines[1:]:
        src, dst, pg, size, start = line.split()
        flows.append((int(src), int(dst), int(pg), int(size), float(start)))
    endpoints = {(src, dst) for src, dst, _pg, _size, _start in flows}
    expected = {(src, src + 8) for src in range(8)} | {(src, 8) for src in range(9, 16)}
    if endpoints != expected:
        raise SummaryError(f"directed hybrid endpoint geometry mismatch: {path}")
    if {pg for _src, _dst, pg, _size, _start in flows} != {4}:
        raise SummaryError(f"directed hybrid must use PG4: {path}")
    if {size for _src, _dst, _pg, size, _start in flows} != {2 * 1024 * 1024}:
        raise SummaryError(f"directed hybrid must use 2 MiB flows: {path}")
    starts = [start for _src, _dst, _pg, _size, start in flows]
    if max(starts) - min(starts) > 0.5e-6 + 1e-12:
        raise SummaryError(f"directed hybrid jitter exceeds 0.5 us: {path}")
    return flows


def one_output(driver: Path) -> Path:
    matches = re.findall(r"^Config filename:(.*)/config\.txt$", driver.read_text(), re.M)
    if len(matches) != 1:
        raise SummaryError(f"cannot resolve one output directory from {driver}")
    return Path(matches[0]).resolve()


def port_values(metrics: Mapping[Tuple[str, str], float], scope: str, prefix: str) -> Dict[str, float]:
    return {
        f"{prefix}_{name}": metrics[(f"queue_port_{name}", scope)]
        for name in ("average_bytes", "positive_average_bytes", "p95_bytes",
                     "p99_bytes", "max_bytes", "tx_bytes")
    }


def analyze(results: Path, simulator_sha: str) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    rows: List[Dict[str, object]] = []
    admissions: List[Dict[str, object]] = []
    for seed in range(1, 6):
        flow = results / "flows" / f"directed-hybrid-s{seed}.txt"
        manifest_path = results / "flows" / f"directed-hybrid-s{seed}.txt.manifest.json"
        parse_flow_geometry(flow)
        manifest = json.loads(manifest_path.read_text())
        flow_sha = sha256_file(flow)
        if manifest["validation"]["sha256"] != flow_sha:
            raise SummaryError(f"seed {seed} manifest hash mismatch")
        for arm in ARMS:
            label = f"formal-{arm}-s{seed}"
            driver = results / "logs" / f"{label}.driver.log"
            output = one_output(driver)
            output_id = output.name
            summary_path = results / "summaries" / f"{label}.json"
            summary = json.loads(summary_path.read_text())
            metrics = metric_map(summary)
            config = parse_config(output / "config.txt")
            fct = parse_fct(output / f"{output_id}_out_fct.txt")
            receiver = [entry for entry in fct if int(entry["dst"]) == 8]
            fabric = [entry for entry in fct if int(entry["src"]) < 8]
            if len(fct) != 15 or len(receiver) != 8 or len(fabric) != 8:
                raise SummaryError(f"{label} completion scopes are incomplete")
            row: Dict[str, object] = {
                "seed": seed, "arm": arm, "output_id": output_id,
                "simulator_sha": simulator_sha, "flow_sha256": flow_sha,
                "output_dir": str(output), "summary_json": str(summary_path.resolve()),
                "driver_log": str(driver.resolve()),
                "receiver8_completion_span_us": (
                    max(float(entry["finish_ns"]) for entry in receiver)
                    - min(float(entry["start_ns"]) for entry in receiver)
                ) / 1000.0,
                "fabric_completion_span_us": (
                    max(float(entry["finish_ns"]) for entry in fabric)
                    - min(float(entry["start_ns"]) for entry in fabric)
                ) / 1000.0,
                "receiver8_goodput_gbps": metrics[("receiver_incast_goodput_gbps", "receiver:8")],
                "receiver8_flow_goodput_jain": metrics[("receiver_incast_flow_goodput_jain", "receiver:8")],
                "receiver8_flow_goodput_min_gbps": metrics[("receiver_incast_flow_goodput_min_gbps", "receiver:8")],
                "receiver8_flow_goodput_max_gbps": metrics[("receiver_incast_flow_goodput_max_gbps", "receiver:8")],
            }
            row.update({name: metrics[(name, "all")] for name in SUMMARY_FIELDS})
            row.update(port_values(metrics, "switch:16:if:9:to:18", "fabric_uplink"))
            row.update(port_values(metrics, "switch:17:if:1:to:8", "receiver_downlink"))
            used_homa_priorities = sum(
                metrics.get(("homa_data_packets", f"priority:{priority}"), 0) > 0
                for priority in range(8)
            )
            row["homa_used_data_priorities"] = used_homa_priorities
            config_checks = {
                "cc_mode": config.get("CC_MODE") == str(CC_MODES[arm]),
                "seed": config.get("RANDOM_SEED") == str(seed),
                "bulk": config.get("MONITOR_PROFILE") == "bulk",
                "pfc_irn": config.get("ENABLE_PFC") == "1" and config.get("ENABLE_IRN") == "0",
            }
            if arm == "guard":
                config_checks.update({
                    "lambda": config.get("GUARD_LAMBDA") == "1.4",
                    "size_srpt_adaptive": (
                        config.get("GUARD_SIZE_PRIORITY") == "1"
                        and config.get("GUARD_SENDER_SRPT") == "1"
                        and config.get("GUARD_WORK_CONSERVING") == "1"
                    ),
                })
                mechanism = (
                    row["grants_sent"] > 0 and row["hpcc_actual_rate_changes"] > 0
                    and row["reactive_binding_updates"] > 0
                    and row["grant_binding_updates"] > 0
                    and row["homa_data_packets"] == 0
                )
            elif arm == "hpcc":
                mechanism = (
                    row["grants_sent"] == 0 and row["hpcc_actual_rate_changes"] > 0
                    and row["homa_data_packets"] == 0
                )
            else:
                mechanism = (
                    row["grants_sent"] == 0 and row["hpcc_actual_rate_changes"] == 0
                    and row["homa_messages_tracked"] == 15
                    and row["homa_messages_completed"] == 15
                    and row["homa_completion_notices_sent"] == 15
                    and row["homa_completion_notices_received"] == 15
                    and row["homa_grants_sent"] > 0 and used_homa_priorities >= 2
                )
            checks = {
                "complete_and_exact": (
                    summary["status"] == "validated_complete"
                    and summary["validation"]["completed_flow_count"] == 15
                    and summary["provenance"]["flow_sha256"] == flow_sha
                ),
                "config": all(config_checks.values()),
                "mechanism": mechanism,
                "zero_drop_recovery": all(float(row[field]) == 0 for field in ZERO_FIELDS),
                "bounded_output": sum(path.stat().st_size for path in output.iterdir()) < 100 * 1024 * 1024,
            }
            failures = [name for name, passed in checks.items() if not passed]
            row["passed"] = not failures
            admissions.append({
                "seed": seed, "arm": arm, "output_id": output_id,
                "checks": checks, "config_checks": config_checks,
                "failures": failures, "passed": not failures,
            })
            rows.append(row)
    for seed in range(1, 6):
        if len({row["flow_sha256"] for row in rows if row["seed"] == seed}) != 1:
            raise SummaryError(f"seed {seed} arms have different flow hashes")
    if len({row["flow_sha256"] for row in rows}) != 5:
        raise SummaryError("five formal seeds must use five distinct traces")
    failed = [row for row in admissions if not row["passed"]]
    if failed:
        raise SummaryError(f"directed-hybrid admission failed: {failed}")
    return rows, admissions


def aggregate(rows: Sequence[Mapping[str, object]]) -> List[Dict[str, object]]:
    non_metrics = {
        "seed", "arm", "output_id", "simulator_sha", "flow_sha256", "output_dir",
        "summary_json", "driver_log", "passed",
    }
    metrics = sorted(
        key for key, value in rows[0].items()
        if key not in non_metrics and isinstance(value, (int, float))
    )
    output: List[Dict[str, object]] = []
    for arm in ARMS:
        arm_rows = [row for row in rows if row["arm"] == arm]
        for name in metrics:
            output.append({
                "analysis": "arm_mean", "comparison": arm, "metric": name,
                **mean_ci([float(row[name]) for row in arm_rows]),
            })
    for left, right in (("guard", "hpcc"), ("guard", "homa"), ("homa", "hpcc")):
        comparison = f"{left}_minus_{right}"
        for name in metrics:
            differences = []
            percentages = []
            for seed in range(1, 6):
                lhs = float(next(row[name] for row in rows if row["seed"] == seed and row["arm"] == left))
                rhs = float(next(row[name] for row in rows if row["seed"] == seed and row["arm"] == right))
                differences.append(lhs - rhs)
                if rhs != 0:
                    percentages.append(100.0 * (lhs - rhs) / rhs)
            output.append({
                "analysis": "paired_difference", "comparison": comparison,
                "metric": name, **mean_ci(differences),
            })
            if len(percentages) == 5:
                output.append({
                    "analysis": "paired_percent", "comparison": comparison,
                    "metric": name, **mean_ci(percentages),
                })
    return output


def write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("--simulator-sha", required=True)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    results = args.results.resolve()
    output = (args.output_dir or results / "analysis").resolve()
    output.mkdir(parents=True, exist_ok=True)
    rows, admissions = analyze(results, args.simulator_sha)
    ci = aggregate(rows)
    write_csv(output / "directed_hybrid_runs.csv", rows)
    write_csv(output / "directed_hybrid_ci.csv", ci)
    (output / "admission.json").write_text(json.dumps({
        "schema": "guard-hpcc-homa-directed-hybrid-v1",
        "simulator_sha": args.simulator_sha, "all_passed": True,
        "runs": admissions,
    }, indent=2, sort_keys=True) + "\n")
    print(f"validated {len(rows)} runs; all admission checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
