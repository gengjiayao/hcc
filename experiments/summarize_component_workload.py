#!/usr/bin/env python3
"""Strictly aggregate a matched custom-workload component ablation.

The expected result layout is produced by the bounded component commands used
in ``experiments/README.md``: one PG-matched flow/manifest pair per seed and
``formal-component-{full,hpcc,receiver}-s{seed}`` driver logs and workload
summaries.  Simulator runs remain the independent observations; individual
flows are used only for within-run scoped metrics.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
from typing import Dict, Iterable, List, Mapping, MutableMapping, Sequence, Tuple


ARMS = ("full", "hpcc", "receiver")
CC_MODES = {"full": 11, "hpcc": 3, "receiver": 13}
T95_DF4 = 2.7764451051977987
PORTS = {
    "target_downlink": (16, 0),
    "leaf16_uplink": (16, 18),
    "leaf17_uplink": (17, 18),
    "spine_to_leaf16": (18, 16),
    "spine_to_leaf17": (18, 17),
}
PORT_METRICS = (
    "queue_port_positive_samples", "queue_port_average_bytes",
    "queue_port_positive_average_bytes", "queue_port_p95_bytes",
    "queue_port_p99_bytes", "queue_port_max_bytes", "queue_port_tx_bytes",
)
ZERO_RECOVERY_FIELDS = (
    "switch_drops_ingress", "switch_drops_egress", "switch_drops_total",
    "recovery_nacks_generated", "recovery_nacks_received", "irn_nacks_generated",
    "irn_nacks_received", "irn_retransmit_packets", "irn_retransmit_bytes",
    "timeout_recoveries",
)


class AnalysisError(RuntimeError):
    """The component matrix violates a matching or mechanism invariant."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def percentile(values: Sequence[float], percent: float) -> float:
    if not values:
        raise AnalysisError("cannot calculate a percentile of no values")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percent / 100.0
    lower, upper = math.floor(rank), math.ceil(rank)
    fraction = rank - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def mean_ci(values: Sequence[float]) -> Dict[str, float]:
    if len(values) != 5:
        raise AnalysisError(f"formal t95 requires five seed values, got {len(values)}")
    mean = statistics.fmean(values)
    half_width = T95_DF4 * statistics.stdev(values) / math.sqrt(len(values))
    return {
        "n": len(values), "mean": mean, "ci95_low": mean - half_width,
        "ci95_high": mean + half_width, "ci95_half_width": half_width,
        "t_critical": T95_DF4,
    }


def parse_elapsed(value: str) -> float:
    seconds = 0.0
    for part in value.split(":"):
        seconds = seconds * 60.0 + float(part)
    return seconds


def parse_config(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) >= 2:
            values[parts[0]] = parts[1]
    return values


def parse_flow_file(path: Path) -> List[Dict[str, object]]:
    lines = path.read_text().splitlines()
    try:
        declared = int(lines[0])
    except (IndexError, ValueError) as exc:
        raise AnalysisError(f"invalid flow header: {path}") from exc
    flows: List[Dict[str, object]] = []
    for flow_id, line in enumerate(lines[1:]):
        parts = line.split()
        if len(parts) != 5:
            raise AnalysisError(f"malformed flow row {path}:{flow_id + 2}")
        flows.append({
            "flow_id": flow_id, "src": int(parts[0]), "dst": int(parts[1]),
            "pg": int(parts[2]), "size": int(parts[3]), "start_s": float(parts[4]),
        })
    if declared != len(flows):
        raise AnalysisError(f"flow header/count mismatch in {path}")
    return flows


def parse_fct(path: Path) -> List[Dict[str, float]]:
    rows: List[Dict[str, float]] = []
    for line_number, line in enumerate(path.read_text().splitlines(), 1):
        parts = line.split()
        if len(parts) != 8:
            raise AnalysisError(f"malformed FCT row {path}:{line_number}")
        src, dst, size, start_ns, duration_ns, ideal_ns = (
            int(parts[0]), int(parts[1]), int(parts[4]), int(parts[5]),
            int(parts[6]), int(parts[7]),
        )
        if duration_ns <= 0 or ideal_ns <= 0:
            raise AnalysisError(f"non-positive FCT/ideal at {path}:{line_number}")
        rows.append({
            "src": src, "dst": dst, "size": size, "start_ns": start_ns,
            "duration_ns": duration_ns, "finish_ns": start_ns + duration_ns,
            "fct_us": duration_ns / 1000.0, "slowdown": duration_ns / ideal_ns,
        })
    return rows


def scoped_fct(rows: Sequence[Mapping[str, float]], prefix: str) -> Dict[str, float]:
    if not rows:
        raise AnalysisError(f"empty FCT scope: {prefix}")
    starts = [float(row["start_ns"]) for row in rows]
    finishes = [float(row["finish_ns"]) for row in rows]
    span_ns = max(finishes) - min(starts)
    if span_ns <= 0:
        raise AnalysisError(f"non-positive completion span: {prefix}")
    fcts = [float(row["fct_us"]) for row in rows]
    slowdowns = [float(row["slowdown"]) for row in rows]
    rates = [float(row["size"]) * 8 / float(row["duration_ns"]) for row in rows]
    squared = sum(rates) ** 2
    sum_squares = sum(value * value for value in rates)
    result = {
        f"{prefix}_flow_count": float(len(rows)),
        f"{prefix}_completion_span_us": span_ns / 1000.0,
        f"{prefix}_fct_us_mean": statistics.fmean(fcts),
        f"{prefix}_fct_us_p95": percentile(fcts, 95),
        f"{prefix}_fct_us_p99": percentile(fcts, 99),
        f"{prefix}_slowdown_mean": statistics.fmean(slowdowns),
        f"{prefix}_slowdown_p95": percentile(slowdowns, 95),
        f"{prefix}_slowdown_p99": percentile(slowdowns, 99),
        f"{prefix}_aggregate_goodput_gbps": sum(float(row["size"]) for row in rows) * 8 / span_ns,
        f"{prefix}_flow_goodput_jain": squared / (len(rates) * sum_squares),
        f"{prefix}_flow_goodput_min_gbps": min(rates),
        f"{prefix}_flow_goodput_max_gbps": max(rates),
    }
    return result


def metric_map(summary: Mapping[str, object]) -> Dict[Tuple[str, str], float]:
    return {
        (str(row["metric"]), str(row["scope"])): float(row["value"])
        for row in summary["metrics"]
    }


def find_port_scope(metrics: Mapping[Tuple[str, str], float], node: int, neighbor: int) -> str:
    pattern = re.compile(rf"^switch:{node}:if:\d+:to:{neighbor}$")
    scopes = sorted({scope for _metric, scope in metrics if pattern.match(scope)})
    if len(scopes) != 1:
        raise AnalysisError(f"expected one port {node}->{neighbor}, found {scopes}")
    return scopes[0]


def parse_controller(path: Path, incast_ids: Iterable[int]) -> Tuple[Dict[str, int], List[Dict[str, object]], Dict[str, float]]:
    lines = path.read_text().splitlines()
    comments = [line for line in lines if line.startswith("#")]
    if len(comments) != 1:
        raise AnalysisError(f"controller trace has {len(comments)} footers: {path}")
    match = re.fullmatch(r"# attempted (\d+) written (\d+) truncated (\d+)", comments[0])
    if not match:
        raise AnalysisError(f"malformed controller footer: {path}")
    footer = dict(zip(("attempted", "written", "truncated"), map(int, match.groups())))
    rows = list(csv.DictReader(line for line in lines if not line.startswith("#")))
    if len(rows) != footer["written"]:
        raise AnalysisError(f"controller written count mismatch: {path}")
    by_flow: MutableMapping[int, List[Dict[str, object]]] = defaultdict(list)
    for order, raw in enumerate(rows):
        event: Dict[str, object] = dict(raw)
        event["time_ns"] = int(raw["time_ns"])
        event["order"] = order
        if int(raw["fast_react"]) != 0:
            raise AnalysisError(f"formal controller trace contains fast reaction: {path}")
        by_flow[int(raw["flow_id"])].append(event)

    incast = set(incast_ids)
    timelines: List[Dict[str, object]] = []
    for flow_id in sorted(by_flow):
        all_events = sorted(by_flow[flow_id], key=lambda row: (int(row["time_ns"]), int(row["order"])))
        complete_indexes = [index for index, row in enumerate(all_events) if row["event_type"] == "complete"]
        if len(complete_indexes) != 1:
            raise AnalysisError(f"flow {flow_id} has {len(complete_indexes)} complete events")
        complete_index = complete_indexes[0]
        events = all_events[:complete_index + 1]
        if len(events) < 2:
            raise AnalysisError(f"flow {flow_id} has no timed controller interval")
        durations = {"reactive": 0, "grant": 0, "tie": 0}
        longest = current = 0
        for event, next_event in zip(events[:-1], events[1:]):
            duration = int(next_event["time_ns"]) - int(event["time_ns"])
            binding = str(event["binding"])
            if duration < 0 or binding not in durations:
                raise AnalysisError(f"invalid controller interval for flow {flow_id}")
            durations[binding] += duration
            if binding == "reactive":
                current += duration
            elif duration > 0:
                longest = max(longest, current)
                current = 0
        longest = max(longest, current)
        span = int(events[-1]["time_ns"]) - int(events[0]["time_ns"])
        if span <= 0 or sum(durations.values()) != span:
            raise AnalysisError(f"invalid controller span for flow {flow_id}")
        timelines.append({
            "flow_id": flow_id, "scope": "incast" if flow_id in incast else "background",
            "first_event_time_ns": int(events[0]["time_ns"]),
            "complete_time_ns": int(events[-1]["time_ns"]),
            "observed_binding_span_ns": span,
            "event_count_to_complete": len(events),
            "post_complete_event_count": len(all_events) - complete_index - 1,
            "reactive_time_ns": durations["reactive"],
            "grant_time_ns": durations["grant"], "tie_time_ns": durations["tie"],
            "reactive_time_ratio": durations["reactive"] / span,
            "grant_time_ratio": durations["grant"] / span,
            "tie_time_ratio": durations["tie"] / span,
            "longest_contiguous_reactive_ns": longest,
            "ever_reactive": int(durations["reactive"] > 0),
        })

    if set(by_flow) != set(range(655)):
        raise AnalysisError(f"controller trace does not cover exactly flow IDs 0..654: {path}")
    aggregates: Dict[str, float] = {}
    for scope, selected in (
        ("overall", timelines),
        ("incast", [row for row in timelines if row["scope"] == "incast"]),
    ):
        denominator = sum(int(row["observed_binding_span_ns"]) for row in selected)
        if not selected or denominator <= 0:
            raise AnalysisError(f"empty controller scope {scope}: {path}")
        for binding in ("reactive", "grant", "tie"):
            aggregates[f"{scope}_binding_{binding}_ratio_pooled"] = (
                sum(int(row[f"{binding}_time_ns"]) for row in selected) / denominator
            )
            aggregates[f"{scope}_binding_{binding}_ratio_mean_flow"] = statistics.fmean(
                float(row[f"{binding}_time_ratio"]) for row in selected
            )
        aggregates[f"{scope}_binding_ever_reactive_flows"] = float(sum(
            int(row["ever_reactive"]) for row in selected
        ))
        aggregates[f"{scope}_binding_ever_reactive_fraction"] = statistics.fmean(
            int(row["ever_reactive"]) for row in selected
        )
        aggregates[f"{scope}_binding_longest_reactive_us_mean_flow"] = statistics.fmean(
            int(row["longest_contiguous_reactive_ns"]) for row in selected
        ) / 1000.0
        aggregates[f"{scope}_binding_longest_reactive_us_max_flow"] = max(
            int(row["longest_contiguous_reactive_ns"]) for row in selected
        ) / 1000.0
    aggregates["controller_post_complete_events"] = float(sum(
        int(row["post_complete_event_count"]) for row in timelines
    ))
    return footer, timelines, aggregates


def output_bytes(path: Path) -> int:
    return sum(child.stat().st_size for child in path.rglob("*") if child.is_file())


def analyze(results: Path, simulator_sha: str, expected_pg: int) -> Tuple[List[Dict[str, object]], List[Dict[str, object]], List[Dict[str, object]]]:
    runs: List[Dict[str, object]] = []
    timelines: List[Dict[str, object]] = []
    admissions: List[Dict[str, object]] = []
    for seed in range(1, 6):
        flow_file = results / "flows" / f"hybrid-pg4-10ms-640x1MiB-s{seed}.txt"
        manifest_path = results / "flows" / f"hybrid-pg4-10ms-640x1MiB-s{seed}.manifest.json"
        flows = parse_flow_file(flow_file)
        manifest = json.loads(manifest_path.read_text())
        if manifest["parameters"]["priority_group"] != expected_pg:
            raise AnalysisError(f"seed {seed} manifest priority is not PG{expected_pg}")
        if manifest["validation"]["sha256"] != sha256_file(flow_file):
            raise AnalysisError(f"seed {seed} manifest hash mismatch")
        if len(flows) != 655 or Counter(int(row["pg"]) for row in flows) != Counter({expected_pg: 655}):
            raise AnalysisError(f"seed {seed} flow count/PG mismatch")
        incast_ids = {
            int(row["flow_id"]) for row in flows
            if int(row["size"]) == 512 * 1024 and int(row["dst"]) == 0
        }
        if len(incast_ids) != 15:
            raise AnalysisError(f"seed {seed} has {len(incast_ids)} incast flows")

        for arm in ARMS:
            label = f"formal-component-{arm}-s{seed}"
            driver_path = results / "logs" / f"{label}.driver.log"
            driver = driver_path.read_text()
            output_matches = re.findall(r"^Config filename:(.*)/config\.txt$", driver, re.M)
            if len(output_matches) != 1:
                raise AnalysisError(f"cannot resolve one output directory from {driver_path}")
            output = Path(output_matches[0]).resolve()
            output_id = output.name
            summary_path = results / "summaries" / f"{label}.json"
            summary = json.loads(summary_path.read_text())
            validation = summary["validation"]
            metrics = metric_map(summary)
            config_path = output / "config.txt"
            config = parse_config(config_path)
            expected_config = {
                "CC_MODE": str(CC_MODES[arm]), "ENABLE_PFC": "1", "ENABLE_IRN": "0",
                "RANDOM_SEED": str(seed), "GUARD_LAMBDA": "1.0",
                "GUARD_SELECTIVE_REGISTRATION": "1", "GUARD_PROACTIVE_RELEASE": "1",
                "GUARD_KEEP_LAST_HOP_INT": "0", "MONITOR_PROFILE": "bulk",
                "GUARD_CONTROLLER_TRACE": "1" if arm == "full" else "0",
                "GUARD_CONTROLLER_TRACE_MAX_LINES": "300000" if arm == "full" else "10000",
            }
            config_ok = all(config.get(key) == value for key, value in expected_config.items())
            elapsed_match = re.search(
                r"Elapsed \(wall clock\) time \(h:mm:ss or m:ss\): (\S+)", driver
            )
            rss_match = re.search(r"Maximum resident set size \(kbytes\): (\d+)", driver)
            exit_match = re.search(r"Exit status: (\d+)", driver)
            if not elapsed_match or not rss_match or not exit_match:
                raise AnalysisError(f"missing resource record in {driver_path}")
            fct_path = output / f"{output_id}_out_fct.txt"
            fct_rows = parse_fct(fct_path)
            incast_rows = [
                row for row in fct_rows
                if int(row["size"]) == 512 * 1024 and int(row["dst"]) == 0
            ]
            if len(incast_rows) != 15:
                raise AnalysisError(f"{label} completed {len(incast_rows)} incast flows")
            row: Dict[str, object] = {
                "seed": seed, "arm": arm, "output_id": output_id,
                "simulator_sha": simulator_sha, "flow_sha256": sha256_file(flow_file),
                "runtime_s": parse_elapsed(elapsed_match.group(1)),
                "max_rss_kib": int(rss_match.group(1)), "output_bytes": output_bytes(output),
                "priority_group": expected_pg, "output_dir": str(output),
                "driver_log": str(driver_path.resolve()), "summary_json": str(summary_path.resolve()),
                "summary_csv": str((results / "summaries" / f"{label}.csv").resolve()),
                "flow_file": str(flow_file.resolve()), "manifest": str(manifest_path.resolve()),
                "flow_snapshot": summary["provenance"]["flow_snapshot"],
                "config_path": str(config_path.resolve()),
            }
            for (metric, scope), value in metrics.items():
                if scope == "all":
                    row[metric] = value
            row.update(scoped_fct(fct_rows, "overall"))
            row.update(scoped_fct(incast_rows, "incast"))
            row["grants_per_completed_flow"] = float(row["grants_sent"]) / 655.0
            row["grants_per_mib"] = float(row["grants_sent"]) / (
                float(validation["total_flow_bytes"]) / (1024.0 * 1024.0)
            )
            for port, (node, neighbor) in PORTS.items():
                scope = find_port_scope(metrics, node, neighbor)
                row[f"{port}_scope"] = scope
                for metric in PORT_METRICS:
                    row[f"{port}_{metric}"] = metrics[(metric, scope)]

            controller_ok = True
            if arm == "full":
                controller_path = output / f"{output_id}_out_guard_controller.csv"
                footer, flow_timelines, binding = parse_controller(controller_path, incast_ids)
                row.update({f"controller_{key}": value for key, value in footer.items()})
                row.update(binding)
                row["controller_csv"] = str(controller_path.resolve())
                controller_ok = footer["truncated"] == 0 and footer["attempted"] == footer["written"]
                for timeline in flow_timelines:
                    timeline.update({"seed": seed, "arm": arm, "output_id": output_id})
                    timelines.append(timeline)
            else:
                row.update({"controller_attempted": 0, "controller_written": 0,
                            "controller_truncated": 0, "controller_csv": ""})

            completed_ok = (
                summary["status"] == "validated_complete"
                and validation["generated_flow_count"] == 655
                and validation["completed_flow_count"] == 655
                and len(fct_rows) == 655 and len(incast_rows) == 15
                and validation["endpoints_sizes_starts_match"] is True
            )
            zero_recovery = all(float(row[field]) == 0 for field in ZERO_RECOVERY_FIELDS)
            strip_ok = (
                float(row["int_records_stripped"]) > 0
                and float(row["int_hops_after_strip"]) < float(row["int_hops_before_strip"])
            ) if arm == "full" else float(row["int_records_stripped"]) == 0
            if arm == "full":
                mechanism_ok = (
                    float(row["grants_sent"]) > 0 and float(row["hpcc_valid_feedback"]) > 0
                    and float(row["hpcc_full_computations"]) > 0
                    and float(row["hpcc_actual_rate_changes"]) > 0
                    and float(row["reactive_binding_updates"]) > 0
                    and float(row["overall_binding_reactive_ratio_pooled"]) > 0
                )
            elif arm == "hpcc":
                mechanism_ok = (
                    float(row["grants_sent"]) == 0 and float(row["grants_received"]) == 0
                    and float(row["hpcc_valid_feedback"]) > 0
                    and float(row["hpcc_full_computations"]) > 0
                    and float(row["hpcc_actual_rate_changes"]) > 0
                )
            else:
                mechanism_ok = (
                    float(row["grants_sent"]) > 0 and float(row["grants_received"]) > 0
                    and all(float(row[field]) == 0 for field in (
                        "hpcc_feedback_updates", "hpcc_valid_feedback",
                        "hpcc_rate_updates_applied", "hpcc_full_computations",
                        "hpcc_fast_computations", "hpcc_actual_rate_changes",
                    ))
                )
            checks = {
                "completed_655_and_incast_15": completed_ok,
                "flow_snapshot_hash": summary["provenance"]["flow_sha256"] == row["flow_sha256"],
                "pg4_input_and_manifest": expected_pg == 4,
                "config_exact": config_ok, "exit_zero": exit_match.group(1) == "0",
                "mechanism_triggered": mechanism_ok, "drop_recovery_zero": zero_recovery,
                "last_hop_strip_semantics": strip_ok, "controller_complete": controller_ok,
                "resource_bounds": (
                    float(row["runtime_s"]) < 1800
                    and int(row["max_rss_kib"]) < 8 * 1024 * 1024
                    and int(row["output_bytes"]) < 100 * 1024 * 1024
                ),
            }
            failures = [name for name, passed in checks.items() if not passed]
            admissions.append({
                "seed": seed, "arm": arm, "output_id": output_id,
                "passed": not failures, "checks": checks, "failures": failures,
                "pfc_pause_events": row["pfc_pause_events"],
                "pfc_pause_count": row["pfc_pause_count"],
            })
            runs.append(row)

    for seed in range(1, 6):
        hashes = {str(row["flow_sha256"]) for row in runs if row["seed"] == seed}
        if len(hashes) != 1:
            raise AnalysisError(f"seed {seed} arms do not have the same flow hash")
    if len({str(row["flow_sha256"]) for row in runs}) != 5:
        raise AnalysisError("the five seeds do not have five independent flow hashes")
    failures = [row for row in admissions if not row["passed"]]
    if failures:
        raise AnalysisError(f"formal admission failures: {failures}")
    return runs, timelines, admissions


def confidence_intervals(runs: Sequence[Mapping[str, object]]) -> Tuple[Dict[str, object], List[Dict[str, object]]]:
    numeric_by_arm = {
        arm: sorted({
            key for row in runs if row["arm"] == arm
            for key, value in row.items()
            if isinstance(value, (int, float)) and key != "seed"
        })
        for arm in ARMS
    }
    common_numeric = sorted(set.intersection(*(
        set(numeric_by_arm[arm]) for arm in ARMS
    )))
    report: Dict[str, object] = {"by_arm": {}, "paired": {}}
    csv_rows: List[Dict[str, object]] = []
    for arm in ARMS:
        arm_runs = [row for row in runs if row["arm"] == arm]
        arm_report: Dict[str, object] = {}
        for metric in numeric_by_arm[arm]:
            stats = mean_ci([float(row[metric]) for row in arm_runs])
            arm_report[metric] = stats
            csv_rows.append({"analysis": "arm_mean", "comparison": arm,
                             "metric": metric, **stats})
        report["by_arm"][arm] = arm_report
    for left, right in (("full", "hpcc"), ("full", "receiver"), ("receiver", "hpcc")):
        name = f"{left}_minus_{right}"
        comparison: Dict[str, object] = {}
        for metric in common_numeric:
            differences: List[float] = []
            percentages: List[float] = []
            per_seed: List[Dict[str, object]] = []
            for seed in range(1, 6):
                left_value = float(next(row for row in runs if row["seed"] == seed and row["arm"] == left)[metric])
                right_value = float(next(row for row in runs if row["seed"] == seed and row["arm"] == right)[metric])
                difference = left_value - right_value
                percent = 100.0 * difference / right_value if right_value != 0 else None
                differences.append(difference)
                if percent is not None:
                    percentages.append(percent)
                per_seed.append({
                    "seed": seed, left: left_value, right: right_value,
                    "difference": difference, "percent_vs_right": percent,
                })
            difference_stats = mean_ci(differences)
            percent_stats = mean_ci(percentages) if len(percentages) == 5 else None
            comparison[metric] = {
                "per_seed": per_seed, "difference": difference_stats,
                "percent_vs_right": percent_stats,
            }
            csv_rows.append({"analysis": "paired_difference", "comparison": name,
                             "metric": metric, **difference_stats})
            if percent_stats:
                csv_rows.append({"analysis": "paired_percent", "comparison": name,
                                 "metric": metric, **percent_stats})
        report["paired"][name] = comparison
    return report, csv_rows


def write_csv(path: Path, rows: Sequence[Mapping[str, object]], fields: Sequence[str]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("--simulator-sha", required=True)
    parser.add_argument("--expected-pg", type=int, default=4)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    results = args.results.resolve()
    output = (args.output_dir or results / "analysis").resolve()
    output.mkdir(parents=True, exist_ok=True)
    runs, timelines, admissions = analyze(results, args.simulator_sha, args.expected_pg)
    ci_report, ci_rows = confidence_intervals(runs)
    path_fields = [
        "output_dir", "driver_log", "summary_json", "summary_csv", "flow_file",
        "manifest", "flow_snapshot", "controller_csv", "config_path",
    ] + [f"{port}_scope" for port in PORTS]
    run_fields = list(runs[0])
    run_fields = [field for field in run_fields if field not in path_fields] + path_fields
    write_csv(output / "component_runs.csv", runs, run_fields)
    write_csv(output / "component_flow_timelines.csv", timelines, list(timelines[0]))
    ci_fields = (
        "analysis", "comparison", "metric", "n", "mean", "ci95_low",
        "ci95_high", "ci95_half_width", "t_critical",
    )
    write_csv(output / "component_ci.csv", ci_rows, ci_fields)
    report = {
        "schema": "matched-component-workload-v1", "simulator_sha": args.simulator_sha,
        "priority_group": args.expected_pg, "arms": list(ARMS),
        "pairing": "left-minus-right, matched by seed and exact flow SHA-256",
        "timeline_semantics": (
            "Each controller event binding owns [event time, next event time); the sole complete "
            "event closes the interval. Post-complete rows are counted and excluded."
        ),
        "admission": admissions, "runs": runs, "confidence_intervals": ci_report,
        "artifacts": {
            "runs_csv": str(output / "component_runs.csv"),
            "flow_timelines_csv": str(output / "component_flow_timelines.csv"),
            "ci_csv": str(output / "component_ci.csv"),
        },
    }
    (output / "component_report.json").write_text(json.dumps(report, indent=2, sort_keys=True))
    (output / "component_admission.json").write_text(json.dumps(
        {"all_passed": True, "runs": admissions}, indent=2, sort_keys=True
    ))
    print(f"validated {len(runs)} matched runs and {len(timelines)} full-GUARD timelines")
    print(output / "component_report.json")


if __name__ == "__main__":
    main()
