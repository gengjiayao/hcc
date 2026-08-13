#!/usr/bin/env python3
"""Validate and summarize a completed GUARD campaign.

Statistics are first computed independently per simulator seed.  Aggregate
rows then report Student-t 95% confidence intervals across those per-seed
values.  Paired comparisons require the exact same traffic-file SHA-256 for
both algorithms at every seed.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Dict, Iterable, Iterator, List, Mapping, MutableMapping, Sequence, Tuple

try:
    from experiments.run_campaign import read_json, sha256_file
except ModuleNotFoundError:  # Direct execution from experiments/.
    from run_campaign import read_json, sha256_file


T_975 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571,
    6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
    11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131,
    16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086,
    21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060,
    26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042,
}

CC_MODES = {"hpcc": 3, "guard": 11, "guard-active-only": 13}
PARAM_COLUMNS = (
    "cc", "topo", "cdf", "netload", "simul_time", "bw", "seed", "pfc", "irn",
    "guard_lambda", "guard_beta", "guard_gamma", "guard_selective_registration",
    "guard_proactive_release", "guard_oflm", "guard_keep_last_hop_int",
)
RUN_COLUMNS = (
    "run_key", "stage", "status", "valid", "rejection_reason", "output_id", "git_sha",
    *PARAM_COLUMNS, "flow_sha256", "flow_count", "completed_flow_count", "analyzed_flow_count",
    "config_sha256", "grants_sent", "grants_received", "hpcc_feedback_updates",
    "registrations", "selected_registrations", "proactive_releases",
    "completion_releases", "max_active_flows", "recovery_nacks_generated",
    "recovery_nacks_received",
    "irn_nacks_generated", "irn_nacks_received", "irn_retransmit_packets",
    "irn_retransmit_bytes", "timeout_recoveries", "switch_drops_ingress",
    "switch_drops_egress", "switch_drops_total", "pfc_pause_events", "pfc_resume_events",
    "pfc_matched_intervals", "pfc_cumulative_pause_ns", "pfc_max_pause_ns",
    "pfc_unmatched_pauses", "pfc_unmatched_resumes", "hpcc_valid_feedback",
    "hpcc_rate_updates_applied", "output_bytes",
)
METRIC_COLUMNS = (
    "row_type", "comparison", "stage", *tuple(column for column in PARAM_COLUMNS if column != "seed"),
    "seed", "metric", "category", "value", "n", "mean", "ci95_low", "ci95_high",
)


class SummaryError(RuntimeError):
    pass


def parse_config(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            parts = line.split()
            if len(parts) >= 2:
                values[parts[0]] = parts[1]
    return values


GUARD_TOTAL_FIELDS = (
    "grants_sent", "grants_received", "hpcc_feedback_updates", "registrations",
    "selected_registrations", "proactive_releases", "completion_releases", "max_active_flows",
    "recovery_nacks_generated", "recovery_nacks_received", "irn_nacks_generated",
    "irn_nacks_received", "irn_retransmit_packets", "irn_retransmit_bytes",
    "timeout_recoveries", "hpcc_valid_feedback", "hpcc_rate_updates_applied",
)
PFC_PRIORITY_FIELDS = (
    "pause_count", "resume_count", "matched_intervals", "cumulative_pause_ns",
    "max_pause_ns", "unmatched_pauses", "unmatched_resumes",
)


def parse_guard_stats(path: Path) -> Dict[str, object]:
    if not path.is_file():
        raise SummaryError(f"missing GUARD stats: {path}")
    total = None
    switch_drops = {"ingress": 0, "egress": 0, "total": 0}
    priorities: Dict[int, Dict[str, int]] = {}
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            parts = line.split()
            if parts and parts[0] == "total" and len(parts) in (5, 12, 16, 18):
                values = tuple(map(int, parts[1:]))
                if len(values) == 4:
                    total = dict(zip(
                        ("grants_sent", "grants_received", "hpcc_feedback_updates",
                         "max_active_flows"), values))
                elif len(values) == 11:
                    legacy_fields = (
                        "grants_sent", "grants_received", "hpcc_feedback_updates",
                        "max_active_flows", "recovery_nacks_generated",
                        "recovery_nacks_received", "irn_nacks_generated",
                        "irn_nacks_received", "irn_retransmit_packets",
                        "irn_retransmit_bytes", "timeout_recoveries",
                    )
                    total = dict(zip(legacy_fields, values))
                elif len(values) == 15:
                    total = dict(zip(GUARD_TOTAL_FIELDS, values))
                else:
                    total = dict(zip(GUARD_TOTAL_FIELDS, values))
            elif parts[:2] == ["switch_drops", "ingress"] and len(parts) == 7:
                switch_drops = {
                    "ingress": int(parts[2]), "egress": int(parts[4]), "total": int(parts[6])
                }
            elif parts and parts[0] == "pfc_priority" and len(parts) == 9:
                try:
                    qindex = int(parts[1])
                    priorities[qindex] = dict(zip(PFC_PRIORITY_FIELDS, map(int, parts[2:])))
                except ValueError:
                    # Column header starts with the same pfc_priority token.
                    continue
    if total is None:
        raise SummaryError(f"missing total row in GUARD stats: {path}")
    result: Dict[str, object] = {field: 0 for field in GUARD_TOTAL_FIELDS}
    result.update(total)
    result.update({f"switch_drops_{key}": value for key, value in switch_drops.items()})
    result["pfc_priority"] = priorities
    for field in PFC_PRIORITY_FIELDS:
        values = [priority[field] for priority in priorities.values()]
        result[f"pfc_{field}"] = max(values, default=0) if field == "max_pause_ns" else sum(values)
    return result


def parse_pfc(path: Path) -> Dict[str, object]:
    counts: Dict[str, object] = {
        "pfc_pause_events": 0, "pfc_resume_events": 0, "pfc_event_priority": {}
    }
    if not path.is_file():
        raise SummaryError(f"missing PFC trace: {path}")
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line_number, line in enumerate(stream, 1):
            parts = line.split()
            if not parts or parts[0].startswith("#"):
                continue
            if len(parts) == 5:  # Legacy: no priority or advertised pause time.
                event_index, priority = 4, None
            elif len(parts) == 7:
                event_index, priority = 5, int(parts[4])
            else:
                raise SummaryError(f"malformed PFC row {path}:{line_number}")
            if parts[event_index] not in ("0", "1"):
                raise SummaryError(f"invalid PFC event {path}:{line_number}")
            field = "pfc_pause_events" if parts[event_index] == "1" else "pfc_resume_events"
            counts[field] = int(counts[field]) + 1
            if priority is not None:
                priorities = counts["pfc_event_priority"]
                bucket = priorities.setdefault(priority, {"pause_count": 0, "resume_count": 0})
                bucket["pause_count" if parts[event_index] == "1" else "resume_count"] += 1
    return counts


def parse_fct(path: Path) -> List[Dict[str, float]]:
    rows: List[Dict[str, float]] = []
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line_number, line in enumerate(stream, 1):
            parts = line.split()
            if not parts:
                continue
            if len(parts) != 8:
                raise SummaryError(f"malformed FCT row {path}:{line_number}")
            try:
                size, start, duration, ideal = map(int, parts[4:8])
            except ValueError as exc:
                raise SummaryError(f"non-integer FCT row {path}:{line_number}") from exc
            if ideal <= 0 or duration < 0:
                raise SummaryError(f"invalid FCT values {path}:{line_number}")
            rows.append({
                "size": size,
                "start_ns": start,
                "finish_ns": start + duration,
                "absolute_us": duration / 1000.0,
                "slowdown": max(1.0, duration / ideal),
            })
    return rows


def percentile(values: Sequence[float], percent: float) -> float:
    if not values:
        raise SummaryError("cannot calculate a percentile of an empty sample")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percent / 100.0
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    fraction = rank - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def flow_category(size: int, bdp: int) -> str:
    if size <= 8 * 1024:
        return "le_8KB"
    if size <= bdp:
        return "8KB_to_1BDP"
    if size <= 1024 * 1024:
        return "1BDP_to_1MB"
    return "gt_1MB"


def fct_metrics(rows: Sequence[Mapping[str, float]], bdp: int) -> List[Tuple[str, str, float, int]]:
    grouped: Dict[str, List[Mapping[str, float]]] = {"all": list(rows)}
    for row in rows:
        grouped.setdefault(flow_category(int(row["size"]), bdp), []).append(row)
    result: List[Tuple[str, str, float, int]] = []
    for category, items in grouped.items():
        if not items:
            continue
        for field in ("slowdown", "absolute_us"):
            values = [float(item[field]) for item in items]
            result.append((f"fct_{field}_mean", category, statistics.fmean(values), len(values)))
            for label, pct in (("p50", 50), ("p95", 95), ("p99", 99)):
                result.append((f"fct_{field}_{label}", category, percentile(values, pct), len(values)))
            if len(values) >= 1000:
                result.append((f"fct_{field}_p999", category, percentile(values, 99.9), len(values)))
    if rows:
        starts = [float(row["start_ns"]) for row in rows]
        finishes = [float(row["finish_ns"]) for row in rows]
        span = max(finishes) - min(starts)
        if span > 0:
            bits = sum(float(row["size"]) for row in rows) * 8
            result.append(("aggregate_goodput_gbps", "all", bits / span, len(rows)))
    return result


def queue_metrics(path: Path) -> List[Tuple[str, str, float, int]]:
    if not path.is_file():
        return []
    values: List[float] = []
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line_number, line in enumerate(stream, 1):
            parts = line.split()
            if not parts:
                continue
            if len(parts) != 4:
                raise SummaryError(f"malformed queue row {path}:{line_number}")
            values.append(float(parts[3]))
    if not values:
        return [("queue_nonzero_sample_count", "all", 0.0, 0)]
    # The legacy trace omits zero queues; make that limitation explicit in names.
    return [
        ("queue_nonzero_bytes_mean", "all", statistics.fmean(values), len(values)),
        ("queue_nonzero_bytes_p95", "all", percentile(values, 95), len(values)),
        ("queue_nonzero_bytes_p99", "all", percentile(values, 99), len(values)),
        ("queue_nonzero_bytes_max", "all", max(values), len(values)),
        ("queue_nonzero_sample_count", "all", float(len(values)), len(values)),
    ]


def queue_summary_metrics(path: Path) -> List[Tuple[str, str, float, int]]:
    values: Dict[str, float] = {}
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line_number, line in enumerate(stream, 1):
            parts = line.split()
            if not parts:
                continue
            if len(parts) != 2:
                raise SummaryError(f"malformed queue summary {path}:{line_number}")
            try:
                values[parts[0]] = float(parts[1])
            except ValueError as exc:
                raise SummaryError(f"non-numeric queue summary {path}:{line_number}") from exc
    required = {"samples", "average_bytes", "p95_bytes", "p99_bytes", "max_bytes"}
    if set(values) != required:
        raise SummaryError(f"queue summary fields differ from {sorted(required)}: {path}")
    samples = int(values["samples"])
    return [
        ("queue_sample_count", "all", values["samples"], samples),
        ("queue_bytes_mean", "all", values["average_bytes"], samples),
        ("queue_bytes_p95", "all", values["p95_bytes"], samples),
        ("queue_bytes_p99", "all", values["p99_bytes"], samples),
        ("queue_bytes_max", "all", values["max_bytes"], samples),
    ]


def validate_mode(params: Mapping[str, object], config: Mapping[str, str], stats: Mapping[str, object]) -> List[str]:
    errors: List[str] = []
    cc = str(params.get("cc", ""))
    expected_mode = CC_MODES.get(cc)
    if expected_mode is not None and int(config.get("CC_MODE", -1)) != expected_mode:
        errors.append(f"CC_MODE is {config.get('CC_MODE')}, expected {expected_mode}")
    grants = int(stats["grants_sent"]) + int(stats["grants_received"])
    updates = int(stats["hpcc_feedback_updates"])
    valid_feedback = int(stats["hpcc_valid_feedback"])
    applied_updates = int(stats["hpcc_rate_updates_applied"])
    if cc == "guard" and (grants == 0 or updates == 0 or valid_feedback == 0 or applied_updates == 0):
        errors.append("full GUARD must have grants and nonzero valid-hop HPCC rate updates")
    elif cc == "guard-active-only" and (grants == 0 or updates != 0 or valid_feedback != 0 or applied_updates != 0):
        errors.append("receiver-only must have grants and zero HPCC feedback activity")
    elif cc == "hpcc" and (grants != 0 or updates == 0 or valid_feedback == 0 or applied_updates == 0):
        errors.append("HPCC-only must have zero grants and nonzero valid-hop HPCC rate updates")
    expected = {
        "RANDOM_SEED": params.get("seed"),
        "ENABLE_PFC": params.get("pfc"),
        "ENABLE_IRN": params.get("irn"),
        "GUARD_LAMBDA": params.get("guard_lambda"),
        "GUARD_EWMA_BETA": params.get("guard_beta"),
        "GUARD_RELEASE_GAMMA": params.get("guard_gamma"),
        "GUARD_SELECTIVE_REGISTRATION": params.get("guard_selective_registration"),
        "GUARD_PROACTIVE_RELEASE": params.get("guard_proactive_release"),
        "GUARD_OFLM": params.get("guard_oflm"),
        "GUARD_KEEP_LAST_HOP_INT": params.get("guard_keep_last_hop_int"),
    }
    for key, value in expected.items():
        if value is not None and key in config:
            try:
                if float(config[key]) != float(value):
                    errors.append(f"{key} is {config[key]}, expected {value}")
            except ValueError:
                errors.append(f"{key} is not numeric: {config[key]}")
    return errors


def params_for_csv(params: Mapping[str, object]) -> Dict[str, object]:
    return {column: params.get(column, "") for column in PARAM_COLUMNS}


def process_manifest(manifest_path: Path, bdp: int) -> Tuple[Dict[str, object], List[Dict[str, object]]]:
    manifest = read_json(manifest_path)
    params = dict(manifest.get("params", {}))
    run: Dict[str, object] = {
        "run_key": manifest.get("run_key", manifest_path.parent.name),
        "stage": manifest.get("stage", ""),
        "status": manifest.get("status", ""),
        "valid": False,
        "rejection_reason": "",
        "output_id": manifest.get("output_id", ""),
        "git_sha": manifest.get("git_sha", ""),
        **params_for_csv(params),
        "flow_sha256": dict(manifest.get("traffic", {})).get("flow_sha256", ""),
        "flow_count": dict(manifest.get("traffic", {})).get("flow_count", ""),
        "completed_flow_count": manifest.get("completed_flow_count", ""),
        "analyzed_flow_count": 0,
        "config_sha256": "",
        **{field: "" for field in GUARD_TOTAL_FIELDS},
        "switch_drops_ingress": "", "switch_drops_egress": "", "switch_drops_total": "",
        "pfc_pause_events": "", "pfc_resume_events": "", "pfc_matched_intervals": "",
        "pfc_cumulative_pause_ns": "", "pfc_max_pause_ns": "",
        "pfc_unmatched_pauses": "", "pfc_unmatched_resumes": "",
        "output_bytes": manifest.get("output_bytes", ""),
    }
    metric_rows: List[Dict[str, object]] = []
    if manifest.get("status") != "completed":
        run["rejection_reason"] = f"manifest status={manifest.get('status')}"
        return run, metric_rows
    output_dir = Path(str(manifest.get("output_dir", "")))
    output_id = str(manifest.get("output_id", ""))
    try:
        config_path = output_dir / "config.txt"
        config = parse_config(config_path)
        stats = parse_guard_stats(output_dir / f"{output_id}_out_guard_stats.txt")
        pfc = parse_pfc(output_dir / f"{output_id}_out_pfc.txt")
        flows = parse_fct(output_dir / f"{output_id}_out_fct.txt")
        errors = validate_mode(params, config, stats)
        raw_priorities = pfc.get("pfc_event_priority", {})
        summary_priorities = stats.get("pfc_priority", {})
        for priority in set(raw_priorities) | set(summary_priorities):
            raw_priority = raw_priorities.get(priority, {})
            summary_priority = summary_priorities.get(priority, {})
            for field in ("pause_count", "resume_count"):
                if int(raw_priority.get(field, 0)) != int(summary_priority.get(field, 0)):
                    errors.append(
                        f"PFC q{priority} {field} raw={raw_priority.get(field, 0)} "
                        f"summary={summary_priority.get(field, 0)}"
                    )
        expected_count = int(dict(manifest.get("traffic", {})).get("flow_count", -1))
        if len(flows) != expected_count:
            errors.append(f"completed flows {len(flows)} != generated flows {expected_count}")
        duration = float(params["simul_time"])
        selected = [
            row for row in flows
            if row["start_ns"] > 2.005e9 and row["finish_ns"] < (2.0 + duration + 0.05) * 1e9
        ]
        if not selected:
            errors.append("FCT analysis window contains no flows")
        run.update(stats)
        run.update(pfc)
        run["completed_flow_count"] = len(flows)
        run["analyzed_flow_count"] = len(selected)
        run["config_sha256"] = sha256_file(config_path)
        run["valid"] = not errors
        run["rejection_reason"] = "; ".join(errors)
        if not errors:
            raw_metrics = fct_metrics(selected, bdp)
            queue_summary = output_dir / f"{output_id}_out_queue_stats.txt"
            if queue_summary.is_file():
                raw_metrics.extend(queue_summary_metrics(queue_summary))
            else:
                raw_metrics.extend(queue_metrics(output_dir / f"{output_id}_out_qlen.txt"))
            raw_metrics.extend([
                ("grants_sent", "all", float(stats["grants_sent"]), 1),
                ("grants_received", "all", float(stats["grants_received"]), 1),
                ("hpcc_feedback_updates", "all", float(stats["hpcc_feedback_updates"]), 1),
                ("max_active_flows", "all", float(stats["max_active_flows"]), 1),
                ("pfc_pause_events", "all", float(pfc["pfc_pause_events"]), 1),
                ("pfc_resume_events", "all", float(pfc["pfc_resume_events"]), 1),
            ])
            for field in GUARD_TOTAL_FIELDS[3:7]:
                raw_metrics.append((field, "all", float(stats[field]), 1))
            for field in GUARD_TOTAL_FIELDS[8:]:
                raw_metrics.append((field, "all", float(stats[field]), 1))
            for field in ("switch_drops_ingress", "switch_drops_egress", "switch_drops_total"):
                raw_metrics.append((field, "all", float(stats[field]), 1))
            for priority, values in sorted(stats.get("pfc_priority", {}).items()):
                for field in PFC_PRIORITY_FIELDS:
                    raw_metrics.append((f"pfc_{field}", f"q{priority}", float(values[field]), 1))
            for metric, category, value, count in raw_metrics:
                metric_rows.append({
                    "row_type": "run", "comparison": "", "stage": run["stage"],
                    **{column: params.get(column, "") for column in PARAM_COLUMNS if column != "seed"},
                    "seed": params.get("seed", ""), "metric": metric, "category": category,
                    "value": value, "n": count, "mean": "", "ci95_low": "", "ci95_high": "",
                    "run_key": run["run_key"], "flow_sha256": run["flow_sha256"],
                })
    except (OSError, ValueError, KeyError, SummaryError) as exc:
        run["valid"] = False
        run["rejection_reason"] = str(exc)
    return run, metric_rows


def confidence_interval(values: Sequence[float]) -> Tuple[float, float, float]:
    if not values:
        raise SummaryError("empty confidence interval")
    mean = statistics.fmean(values)
    if len(values) == 1:
        return mean, math.nan, math.nan
    critical = T_975.get(len(values) - 1, 1.96)
    half = critical * statistics.stdev(values) / math.sqrt(len(values))
    return mean, mean - half, mean + half


def group_key(
    row: Mapping[str, object], excluded: Iterable[str] = (), keep_seed: bool = False
) -> Tuple[Tuple[str, object], ...]:
    ignored = set(excluded) | {"value", "n", "mean", "ci95_low", "ci95_high",
                               "row_type", "comparison", "run_key", "flow_sha256"}
    if not keep_seed:
        ignored.add("seed")
    return tuple(sorted((key, value) for key, value in row.items() if key not in ignored))


def aggregate_rows(run_metrics: Sequence[Mapping[str, object]]) -> List[Dict[str, object]]:
    groups: Dict[Tuple[Tuple[str, object], ...], List[Mapping[str, object]]] = {}
    for row in run_metrics:
        groups.setdefault(group_key(row), []).append(row)
    output: List[Dict[str, object]] = []
    for key, rows in groups.items():
        values = [float(row["value"]) for row in rows]
        mean, low, high = confidence_interval(values)
        base = dict(rows[0])
        base.update({
            "row_type": "aggregate", "comparison": "", "seed": "", "value": "",
            "n": len(values), "mean": mean, "ci95_low": low, "ci95_high": high,
        })
        output.append(base)
    return output


def paired_rows(
    run_metrics: Sequence[Mapping[str, object]], comparisons: Sequence[Mapping[str, object]]
) -> Tuple[List[Dict[str, object]], List[str]]:
    output: List[Dict[str, object]] = []
    errors: List[str] = []
    for comparison in comparisons:
        name = str(comparison["name"])
        stages = set(map(str, comparison.get("stages", [])))
        vary = str(comparison["vary"])
        a, b = comparison["a"], comparison["b"]
        where = dict(comparison.get("where", {}))
        eligible = [
            row for row in run_metrics
            if (not stages or str(row["stage"]) in stages)
            and all(row.get(key) == value for key, value in where.items())
        ]
        observed_variants = {row.get(vary) for row in eligible}
        # A bounded/partial campaign may omit an entire comparison arm (for
        # example, --max-runs stops before the last-hop-INT stage).  In that
        # case the comparison is not attempted.  Once both arms exist, every
        # seed/metric must still form a complete hash-matched pair below.
        if a not in observed_variants or b not in observed_variants:
            continue
        pairs: Dict[Tuple[Tuple[str, object], ...], Dict[object, Mapping[str, object]]] = {}
        for row in eligible:
            pairs.setdefault(
                group_key(row, excluded=(vary, "stage"), keep_seed=True), {}
            )[row.get(vary)] = row
        difference_groups: Dict[Tuple[Tuple[str, object], ...], List[float]] = {}
        representatives: Dict[Tuple[Tuple[str, object], ...], Mapping[str, object]] = {}
        for key, variants in pairs.items():
            if a not in variants and b not in variants:
                continue
            if a not in variants or b not in variants:
                errors.append(f"{name}: incomplete pair {key}")
                continue
            left, right = variants[a], variants[b]
            if left["flow_sha256"] != right["flow_sha256"]:
                errors.append(f"{name}: flow hash mismatch {key}")
                continue
            aggregate_key = group_key(left, excluded=(vary, "seed", "stage"))
            difference_groups.setdefault(aggregate_key, []).append(
                float(left["value"]) - float(right["value"])
            )
            representatives[aggregate_key] = left
        for key, values in difference_groups.items():
            mean, low, high = confidence_interval(values)
            base = dict(representatives[key])
            base.update({
                "row_type": "paired_difference", "comparison": name, "stage": "",
                vary: f"{a}-{b}", "seed": "", "value": "", "n": len(values),
                "mean": mean, "ci95_low": low, "ci95_high": high,
            })
            output.append(base)
    return output, errors


def write_csv(path: Path, fieldnames: Sequence[str], rows: Iterable[Mapping[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign_dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    campaign_dir = args.campaign_dir.resolve()
    output_dir = (args.output_dir or campaign_dir / "summary").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    campaign = read_json(campaign_dir / "campaign.json")
    bdp = int(dict(campaign.get("metadata", {})).get("bdp_bytes", 104000))
    manifests = sorted((campaign_dir / "runs").glob("*/manifest.json"))
    if not manifests:
        raise SummaryError(f"no manifests under {campaign_dir / 'runs'}")

    runs: List[Dict[str, object]] = []
    run_metrics: List[Dict[str, object]] = []
    for manifest in manifests:
        run, metrics = process_manifest(manifest, bdp)
        runs.append(run)
        run_metrics.extend(metrics)
    aggregates = aggregate_rows(run_metrics)
    pairs, pair_errors = paired_rows(run_metrics, campaign.get("comparisons", []))
    write_csv(output_dir / "runs.csv", RUN_COLUMNS, runs)
    write_csv(output_dir / "metrics.csv", METRIC_COLUMNS,
              [*run_metrics, *aggregates, *pairs])
    rejection_lines = [
        f"{run['run_key']}: {run['rejection_reason']}" for run in runs if not run["valid"]
    ] + pair_errors
    (output_dir / "rejections.txt").write_text(
        "\n".join(rejection_lines) + ("\n" if rejection_lines else ""), encoding="utf-8"
    )
    print(f"runs={len(runs)} valid={sum(bool(run['valid']) for run in runs)} "
          f"metrics={len(run_metrics)} paired={len(pairs)} rejections={len(rejection_lines)}")
    return 1 if rejection_lines else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SummaryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
