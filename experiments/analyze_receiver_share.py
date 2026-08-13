#!/usr/bin/env python3
"""Strict mechanism audit for one bounded receiver-share simulator run.

The grant CSV is deliberately independent of the verbose controller trace.  A
``sent`` row records the rate encoded by the receiver, while a ``received`` row
is emitted only after the sender parses that packet and installs its grant
rate.  Thus the reported observation is packet-driven rather than reconstructed
from C/N.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Dict, List, Mapping, Sequence, Tuple

try:
    from experiments.summarize_campaign import SummaryError, parse_guard_stats
    from experiments.summarize_workload import (
        atomic_json, one_artifact, parse_config, parse_fct, parse_snapshot,
        read_json, resolve_snapshot, summarize,
    )
except ModuleNotFoundError:
    from summarize_campaign import SummaryError, parse_guard_stats
    from summarize_workload import (
        atomic_json, one_artifact, parse_config, parse_fct, parse_snapshot,
        read_json, resolve_snapshot, summarize,
    )


TRACE_FIELDS = (
    "time_ns", "event", "set_change", "host_node", "flow_id",
    "data_source_ip", "data_destination_ip", "active_flows",
    "line_rate_bps", "grant_rate_bps", "next_seq", "serialized_bytes",
)
ZERO_RECOVERY = (
    "recovery_nacks_generated", "recovery_nacks_received",
    "irn_nacks_generated", "irn_nacks_received", "irn_retransmit_packets",
    "irn_retransmit_bytes", "timeout_recoveries",
)


def parse_bounded_trace(path: Path) -> Tuple[List[Dict[str, object]], Dict[str, int]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) < 2:
        raise SummaryError(f"empty grant trace: {path}")
    if tuple(lines[0].split(",")) != TRACE_FIELDS:
        raise SummaryError(f"unexpected grant trace schema: {path}")
    footer = lines[-1].split()
    if len(footer) != 7 or footer[:2] != ["#", "attempted"] or footer[3] != "written" or footer[5] != "truncated":
        raise SummaryError(f"malformed grant trace footer: {path}")
    counts = {"attempted": int(footer[2]), "written": int(footer[4]), "truncated": int(footer[6])}
    rows: List[Dict[str, object]] = []
    reader = csv.DictReader(lines[:-1])
    for line_number, raw in enumerate(reader, 2):
        if raw["event"] not in ("sent", "received"):
            raise SummaryError(f"invalid grant event at {path}:{line_number}")
        if raw["set_change"] not in ("registration", "release", "none"):
            raise SummaryError(f"invalid set change at {path}:{line_number}")
        row: Dict[str, object] = {"event": raw["event"], "set_change": raw["set_change"]}
        for field in TRACE_FIELDS:
            if field in ("event", "set_change"):
                continue
            row[field] = int(raw[field])
        rows.append(row)
    if counts["written"] != len(rows) or counts["attempted"] != counts["written"] + counts["truncated"]:
        raise SummaryError("grant trace footer/count mismatch")
    if counts["truncated"]:
        raise SummaryError(f"grant trace truncated {counts['truncated']} rows")
    return rows, counts


def parse_lifecycle(path: Path) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {
            "flow_id", "size_bytes", "receiver_node", "register_ns", "release_ns",
            "complete_ns", "release_reason", "remaining_bytes_at_release",
        }
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise SummaryError(f"unexpected lifecycle schema: {path}")
        for raw in reader:
            row: Dict[str, object] = {"release_reason": raw["release_reason"]}
            for field in required - {"release_reason"}:
                row[field] = int(raw[field])
            rows.append(row)
    return rows


def active_set_metrics(rows: Sequence[Mapping[str, object]], receiver: int) -> Dict[str, float]:
    selected = [row for row in rows if int(row["receiver_node"]) == receiver]
    if not selected:
        raise SummaryError(f"no lifecycle rows for receiver {receiver}")
    events: List[Tuple[int, int]] = []
    for row in selected:
        if row["release_reason"] != "completion" or int(row["remaining_bytes_at_release"]):
            raise SummaryError("receiver-share flow was not released at completion")
        events.append((int(row["register_ns"]), 1))
        events.append((int(row["release_ns"]), -1))
    # Registers sort before releases at equal timestamps.
    events.sort(key=lambda event: (event[0], -event[1]))
    active = maximum = area = max_duration = 0
    previous = events[0][0]
    for time_ns, change in events:
        duration = time_ns - previous
        area += active * duration
        if active == maximum:
            max_duration += duration
        active += change
        maximum = max(maximum, active)
        previous = time_ns
    if active != 0:
        raise SummaryError("lifecycle active set does not return to zero")
    latest_register = max(int(row["register_ns"]) for row in selected)
    earliest_release = min(int(row["release_ns"]) for row in selected)
    stable_ns = max(0, earliest_release - latest_register)
    return {
        "target_lifecycle_rows": len(selected),
        "target_max_active_flows": maximum,
        "target_active_set_area_flow_ns": area,
        "target_max_active_stable_ns": stable_ns,
    }


def rate_metrics(
    rows: Sequence[Mapping[str, object]], receiver: int, target_ids: Sequence[int],
    expected_n: int,
) -> Dict[str, object]:
    sent = [row for row in rows if row["event"] == "sent"]
    received = [row for row in rows if row["event"] == "received"]
    sent_multiset = Counter((row["flow_id"], row["grant_rate_bps"]) for row in sent)
    received_multiset = Counter((row["flow_id"], row["grant_rate_bps"]) for row in received)
    if sent_multiset != received_multiset:
        raise SummaryError("sent and sender-applied grant event multisets differ")
    target_sent = [
        row for row in sent
        if int(row["host_node"]) == receiver and int(row["flow_id"]) in target_ids
    ]
    max_active_rows = [row for row in target_sent if int(row["active_flows"]) == expected_n]
    if {int(row["flow_id"]) for row in max_active_rows} != set(target_ids):
        raise SummaryError("not every target flow received a max-active C/N grant")
    observed_rates = [int(row["grant_rate_bps"]) for row in max_active_rows]
    line_rates = {int(row["line_rate_bps"]) for row in max_active_rows}
    if len(line_rates) != 1:
        raise SummaryError("target grant rows disagree on receiver line rate")
    line_rate = next(iter(line_rates))
    expected_exact = line_rate / expected_n
    errors = [abs(rate - expected_exact) for rate in observed_rates]
    received_by_flow: Dict[str, Dict[str, object]] = {}
    for flow_id in target_ids:
        flow_rows = [row for row in received if int(row["flow_id"]) == flow_id]
        received_by_flow[str(flow_id)] = {
            "packet_observations": len(flow_rows),
            "rates_bps": [int(row["grant_rate_bps"]) for row in flow_rows],
            "final_next_seq": max(int(row["next_seq"]) for row in flow_rows),
        }
    return {
        "line_rate_bps": line_rate,
        "c_over_n_exact_bps": expected_exact,
        "max_active_observed_grant_rate_bps_min": min(observed_rates),
        "max_active_observed_grant_rate_bps_max": max(observed_rates),
        "max_active_c_over_n_error_bps_max": max(errors),
        "target_grants_received_per_flow_mean": statistics.fmean(
            item["packet_observations"] for item in received_by_flow.values()),
        "target_grants_received_per_flow_min": min(
            item["packet_observations"] for item in received_by_flow.values()),
        "target_grants_received_per_flow_max": max(
            item["packet_observations"] for item in received_by_flow.values()),
        "sender_applied_grants_by_flow": received_by_flow,
    }


def common_active_window_metrics(
    trace: Sequence[Mapping[str, object]], flows: Sequence[Mapping[str, object]],
    receiver: int, target_ids: Sequence[int], expected_n: int,
) -> Dict[str, object]:
    if expected_n != 2:
        raise SummaryError("heterogeneous common-active audit currently requires two targets")
    sent = [
        row for row in trace if row["event"] == "sent"
        and int(row["host_node"]) == receiver and int(row["flow_id"]) in target_ids
    ]
    max_rows = [row for row in sent if int(row["active_flows"]) == expected_n]
    if {int(row["flow_id"]) for row in max_rows} != set(target_ids):
        raise SummaryError("common-active start lacks one C/N grant per target")
    start_times = {int(row["time_ns"]) for row in max_rows}
    if len(start_times) != 1:
        raise SummaryError("target C/N grants do not share one active-set transition time")
    start_ns = next(iter(start_times))
    start_seq = {int(row["flow_id"]): int(row["next_seq"]) for row in max_rows}
    release_times = sorted({
        int(row["time_ns"]) for row in sent
        if row["set_change"] == "release" and int(row["time_ns"]) > start_ns
    })
    if not release_times:
        raise SummaryError("heterogeneous target active set never falls below two")
    end_ns = release_times[0]
    release_rows = {
        int(row["flow_id"]): row for row in sent
        if int(row["time_ns"]) == end_ns and row["set_change"] == "release"
    }
    if len(release_rows) != 1:
        raise SummaryError("first heterogeneous release must leave exactly one target")
    sizes = {flow_id: int(flows[flow_id]["size"]) for flow_id in target_ids}
    sources = {flow_id: int(flows[flow_id]["src"]) for flow_id in target_ids}
    duration_ns = end_ns - start_ns
    delivered: Dict[int, int] = {}
    for flow_id in target_ids:
        end_seq = (
            int(release_rows[flow_id]["next_seq"])
            if flow_id in release_rows else sizes[flow_id]
        )
        delivered[flow_id] = end_seq - start_seq[flow_id]
        if not 0 < delivered[flow_id] <= sizes[flow_id]:
            raise SummaryError("invalid receiver progress in common-active window")
    by_source = {
        str(sources[flow_id]): {
            "flow_id": flow_id, "delivered_bytes": delivered[flow_id],
            "payload_goodput_gbps": delivered[flow_id] * 8 / duration_ns,
        }
        for flow_id in target_ids
    }
    line_rate = int(max_rows[0]["line_rate_bps"]) / 1e9
    aggregate = sum(float(row["payload_goodput_gbps"]) for row in by_source.values())
    return {
        "start_ns": start_ns, "end_ns": end_ns, "duration_ns": duration_ns,
        "per_source": by_source, "aggregate_payload_goodput_gbps": aggregate,
        "unused_payload_capacity_gbps": line_rate - aggregate,
        "receiver_line_rate_gbps": line_rate,
    }


def analyze(
    output_dir: Path, manifest_path: Path, lifecycle_path: Path, grant_path: Path,
    ladder_path: Path, scenario: str, expected_n: int,
) -> Dict[str, object]:
    base = summarize(output_dir, manifest_path)
    manifest = read_json(manifest_path)
    ladder = read_json(ladder_path)
    fixed = ladder["fixed_run"]
    params = manifest["parameters"]
    if base["workload"] != "receiver-share":
        raise SummaryError("manifest is not a receiver-share workload")
    if int(params["receiver_share_flows"]) != expected_n:
        raise SummaryError("manifest target-flow count differs from expected N")
    receiver = int(fixed["receiver"])
    snapshot_path = resolve_snapshot(output_dir, None)
    flows, _snapshot = parse_snapshot(snapshot_path, int(params["hosts"]), int(params["max_flows"]))
    target_ids = [
        flow_id for flow_id, flow in enumerate(flows) if int(flow["dst"]) == receiver
    ]
    if len(target_ids) != expected_n:
        raise SummaryError("snapshot target-flow count differs from expected N")

    config = parse_config(output_dir / "config.txt")
    expected_config = {
        "ENABLE_PFC": "1", "ENABLE_IRN": "0", "MONITOR_PROFILE": "bulk",
        "PREFLIGHT_MAX_FLOWS": str(fixed["flow_limit"]),
        "GUARD_SELECTIVE_REGISTRATION": "1", "GUARD_PROACTIVE_RELEASE": "0",
        "GUARD_SIZE_PRIORITY": "0", "GUARD_CONTROLLER_TRACE": "0",
        "GUARD_LIFECYCLE_TRACE": "1", "GUARD_GRANT_TRACE": "1",
        "GUARD_LIFECYCLE_TRACE_MAX_LINES": str(fixed["lifecycle_trace_max_lines"]),
        "GUARD_GRANT_TRACE_MAX_LINES": str(fixed["grant_trace_max_lines"]),
    }
    for key, value in expected_config.items():
        if config.get(key) != value:
            raise SummaryError(f"config {key}={config.get(key)!r}, expected {value!r}")
    if config.get("CC_MODE") not in ("11", "13"):
        raise SummaryError("receiver-share audit requires CC_MODE 11 or 13")

    stats = parse_guard_stats(one_artifact(output_dir, "_out_guard_stats.txt"))
    if int(stats["switch_drops_total"]) or any(int(stats[field]) for field in ZERO_RECOVERY):
        raise SummaryError("drop or recovery activity violates receiver-share admission")
    priorities = stats["pfc_priority"]
    pfc_pauses = sum(int(row["pause_count"]) for row in priorities.values())
    lifecycle = parse_lifecycle(lifecycle_path)
    if len(lifecycle) != int(stats["registrations"]):
        raise SummaryError("lifecycle rows do not equal registrations")
    active = active_set_metrics(lifecycle, receiver)
    if int(active["target_max_active_flows"]) != expected_n:
        raise SummaryError("target receiver did not reach expected active N")
    if active["target_max_active_stable_ns"] < int(
        (ladder["homogeneous"] if scenario == "homogeneous" else ladder["heterogeneous"])
        .get("stable_max_active_window_ns_min",
             ladder.get("heterogeneous", {}).get("acceptance", {}).get("stable_window_ns_min", 0))
    ):
        raise SummaryError("max-active interval is too short")

    trace, trace_counts = parse_bounded_trace(grant_path)
    sent = [row for row in trace if row["event"] == "sent"]
    received = [row for row in trace if row["event"] == "received"]
    sent_bytes = sum(int(row["serialized_bytes"]) for row in sent)
    serialized_sizes = sorted({int(row["serialized_bytes"]) for row in sent})
    controller = "guard" if config["CC_MODE"] == "11" else "guard-active-only"
    expected_bytes = int(
        ladder["homogeneous"]["simulated_serialized_grant_bytes_per_packet"][controller]
    )
    if serialized_sizes != [expected_bytes]:
        raise SummaryError(f"raw serialized grant sizes {serialized_sizes}, expected {[expected_bytes]}")
    if len(sent) != int(stats["grants_sent"]) or len(received) != int(stats["grants_received"]):
        raise SummaryError("grant trace counts differ from packet counters")
    if sent_bytes != int(stats["grant_bytes_sent"]):
        raise SummaryError("raw sent grant bytes differ from independent stats counter")
    rates = rate_metrics(trace, receiver, target_ids, expected_n)
    error_limit = int(ladder["homogeneous"]["grant_target_absolute_error_bps_max"])
    if float(rates["max_active_c_over_n_error_bps_max"]) > error_limit:
        raise SummaryError("observed max-active grant exceeds frozen C/N error bound")

    fct_rows = parse_fct(one_artifact(output_dir, "_out_fct.txt"))
    target_fct = [row for row in fct_rows if int(row["dst"]) == receiver]
    by_source = {
        str(int(row["src"])): {
            "duration_ns": int(row["duration_ns"]),
            "actual_goodput_gbps": int(row["size"]) * 8 / int(row["duration_ns"]),
        }
        for row in target_fct
    }
    common_active = None
    if scenario == "heterogeneous":
        common_active = common_active_window_metrics(
            trace, flows, receiver, target_ids, expected_n)
        acceptance = ladder["heterogeneous"]["acceptance"]
        share_gbps = float(rates["c_over_n_exact_bps"]) / 1e9
        restricted = float(common_active["per_source"]["0"]["payload_goodput_gbps"])
        aggregate = float(common_active["aggregate_payload_goodput_gbps"])
        line_rate = float(common_active["receiver_line_rate_gbps"])
        if restricted / share_gbps > float(
            acceptance["restricted_rate_fraction_of_share_max"]
        ):
            raise SummaryError("restricted target does not leave unused C/N share")
        if aggregate / line_rate > float(
            acceptance["aggregate_rate_fraction_of_line_max"]
        ):
            raise SummaryError("receiver common-active payload rate is not below the gate")
    packet_count = len(sent)
    ethernet_bytes_per_packet = int(
        ladder["homogeneous"]["ethernet_equivalent_bytes_per_packet"][controller]
    )
    result = {
        "schema_version": 1,
        "status": "validated_mechanism",
        "scenario": scenario,
        "expected_active_flows": expected_n,
        "controller": controller,
        "seed": int(config["RANDOM_SEED"]),
        "flow_sha256": base["provenance"]["flow_sha256"],
        "output_directory": str(output_dir.resolve()),
        "grant_observation_semantics": "packet-driven receiver send and sender parse/apply events",
        "completion": base["validation"],
        "active_set": active,
        "rate_audit": rates,
        "control_overhead": {
            "grants_sent": packet_count,
            "grants_received": len(received),
            "simulated_serialized_bytes": sent_bytes,
            "simulated_serialized_bytes_per_grant": sent_bytes / packet_count,
            "stats_grant_bytes_sent": int(stats["grant_bytes_sent"]),
            "ethernet_equivalent_bytes": packet_count * ethernet_bytes_per_packet,
            "ethernet_equivalent_bytes_per_grant": ethernet_bytes_per_packet,
            "ethernet_equivalent_definition": ladder["homogeneous"]["ethernet_equivalent_accounting"],
            "controller_size_explanation": ladder["homogeneous"]["controller_size_explanation"],
        },
        "health": {
            "switch_drops": int(stats["switch_drops_total"]),
            "recovery_events": sum(int(stats[field]) for field in ZERO_RECOVERY),
            "pfc_pause_events": pfc_pauses,
            "grant_trace": trace_counts,
        },
        "target_flow_actual_goodput_by_source": by_source,
        "heterogeneous_common_active_window": common_active,
        "workload_summary": base,
    }
    return result


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Strict receiver-share mechanism audit")
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--lifecycle", required=True, type=Path)
    parser.add_argument("--grants", required=True, type=Path)
    parser.add_argument("--ladder", type=Path, default=Path(__file__).with_name("receiver_share_ladder.json"))
    parser.add_argument("--scenario", choices=("homogeneous", "heterogeneous"), required=True)
    parser.add_argument("--expected-n", required=True, type=int)
    parser.add_argument("--json-out", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.expected_n < 1:
            raise SummaryError("expected N must be positive")
        result = analyze(
            args.output_dir.resolve(), args.manifest.resolve(), args.lifecycle.resolve(),
            args.grants.resolve(), args.ladder.resolve(), args.scenario, args.expected_n,
        )
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(args.json_out.resolve(), result)
    except (OSError, ValueError, KeyError, ZeroDivisionError, SummaryError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"validated receiver-share mechanism audit: {args.json_out.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
