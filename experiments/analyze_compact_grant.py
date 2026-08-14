#!/usr/bin/env python3
"""Strict audit for the paired compact-GUARD-grant microbenchmark."""

import argparse
import csv
import hashlib
import json
from pathlib import Path


RECOVERY_FIELDS = (
    "recovery_nacks_generated",
    "recovery_nacks_received",
    "irn_nacks_generated",
    "irn_nacks_received",
    "irn_retransmit_packets",
    "irn_retransmit_bytes",
    "timeout_recoveries",
)
GRANT_SEMANTIC_FIELDS = (
    "event",
    "set_change",
    "host_node",
    "flow_id",
    "data_source_ip",
    "data_destination_ip",
    "active_flows",
    "line_rate_bps",
    "grant_rate_bps",
    "next_seq",
)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def one_file(directory, suffix):
    matches = sorted(directory.glob("*" + suffix))
    if len(matches) != 1:
        raise ValueError("expected one *{} in {}, found {}".format(
            suffix, directory, len(matches)))
    return matches[0]


def parse_grants(path):
    lines = path.read_text().splitlines()
    footer = lines[-1].split()
    if footer[:2] != ["#", "attempted"] or len(footer) != 7:
        raise ValueError("missing grant trace footer in {}".format(path))
    counters = {
        "attempted": int(footer[2]),
        "written": int(footer[4]),
        "truncated": int(footer[6]),
    }
    rows = list(csv.DictReader(line for line in lines if not line.startswith("#")))
    if len(rows) != counters["written"]:
        raise ValueError("grant row count disagrees with footer in {}".format(path))
    return rows, counters


def parse_total_stats(path):
    lines = path.read_text().splitlines()
    header = lines[0].split()[1:]
    total = next(line.split()[1:] for line in lines if line.startswith("total "))
    if len(header) != len(total):
        raise ValueError("guard total width mismatch in {}".format(path))
    return {key: int(value) for key, value in zip(header, total)}


def parse_health(path):
    lines = path.read_text().splitlines()
    drop_line = next(line.split() for line in lines if line.startswith("switch_drops "))
    return {
        "switch_ingress_drops": int(drop_line[2]),
        "switch_egress_drops": int(drop_line[4]),
        "switch_total_drops": int(drop_line[6]),
    }


def semantic_rows(rows):
    return [tuple(row[field] for field in GRANT_SEMANTIC_FIELDS) for row in rows]


def read_fct(path):
    return [line for line in path.read_text().splitlines() if line.strip()]


def run_view(directory, grants_path):
    input_path = one_file(directory, "_input_flow.txt")
    stats_path = one_file(directory, "_out_guard_stats.txt")
    fct_path = one_file(directory, "_out_fct.txt")
    pfc_path = one_file(directory, "_out_pfc.txt")
    rows, footer = parse_grants(grants_path)
    stats = parse_total_stats(stats_path)
    health = parse_health(stats_path)
    pfc_events = sum(1 for line in pfc_path.read_text().splitlines()
                     if line.strip() and not line.startswith("#"))
    return {
        "input_path": input_path,
        "input_sha256": sha256(input_path),
        "grants_path": grants_path,
        "grant_rows": rows,
        "grant_footer": footer,
        "stats": stats,
        "health": health,
        "fct_rows": read_fct(fct_path),
        "pfc_events": pfc_events,
    }


def validate(spec, legacy, compact):
    expected = spec["expected"]
    expected_hash = spec["flow_sha256"]
    if legacy["input_sha256"] != expected_hash or compact["input_sha256"] != expected_hash:
        raise ValueError("flow hash differs from the frozen input")

    if semantic_rows(legacy["grant_rows"]) != semantic_rows(compact["grant_rows"]):
        raise ValueError("compact encoding changed the grant event sequence")
    if legacy["fct_rows"] != compact["fct_rows"]:
        raise ValueError("compact encoding changed FCT rows")
    if len(compact["fct_rows"]) != expected["completed_flows"]:
        raise ValueError("flow completion count failed")

    for label, view, packet_bytes in (
            ("legacy", legacy, expected["legacy_serialized_bytes_per_grant"]),
            ("compact", compact, expected["compact_serialized_bytes_per_grant"])):
        footer = view["grant_footer"]
        if footer["truncated"] != expected["grant_trace_truncated"]:
            raise ValueError("{} grant trace truncated".format(label))
        if footer["attempted"] != footer["written"]:
            raise ValueError("{} grant trace dropped rows".format(label))
        sizes = {int(row["serialized_bytes"]) for row in view["grant_rows"]}
        if sizes != {packet_bytes}:
            raise ValueError("{} grant size mismatch: {}".format(label, sizes))
        stats = view["stats"]
        if stats["grants_sent"] != expected["grant_count"]:
            raise ValueError("{} grant count mismatch".format(label))
        if stats["grants_received"] != stats["grants_sent"]:
            raise ValueError("{} lost a grant".format(label))
        if stats["grant_bytes_sent"] != stats["grants_sent"] * packet_bytes:
            raise ValueError("{} byte counter disagrees with trace".format(label))
        if sum(stats[field] for field in RECOVERY_FIELDS) != expected["recovery_events"]:
            raise ValueError("{} recovery gate failed".format(label))
        if view["health"]["switch_total_drops"] != expected["switch_drops"]:
            raise ValueError("{} switch-drop gate failed".format(label))
        if view["pfc_events"] != expected["pfc_events"]:
            raise ValueError("{} PFC gate failed".format(label))

    for field in legacy["stats"]:
        if field == "grant_bytes_sent":
            continue
        if legacy["stats"][field] != compact["stats"][field]:
            raise ValueError("non-byte guard counter changed: {}".format(field))

    legacy_receives = [row for row in legacy["grant_rows"] if row["event"] == "received"]
    compact_receives = [row for row in compact["grant_rows"] if row["event"] == "received"]
    arrival_advance_ns = [
        int(old["time_ns"]) - int(new["time_ns"])
        for old, new in zip(legacy_receives, compact_receives)
    ]
    legacy_bytes = legacy["stats"]["grant_bytes_sent"]
    compact_bytes = compact["stats"]["grant_bytes_sent"]
    return {
        "status": "passed",
        "legacy_commit": spec["legacy_commit"],
        "compact_run_commit": spec["compact_run_commit"],
        "flow_sha256": expected_hash,
        "completed_flows": len(compact["fct_rows"]),
        "grant_count": compact["stats"]["grants_sent"],
        "legacy_serialized_bytes_per_grant": expected["legacy_serialized_bytes_per_grant"],
        "compact_serialized_bytes_per_grant": expected["compact_serialized_bytes_per_grant"],
        "legacy_grant_bytes": legacy_bytes,
        "compact_grant_bytes": compact_bytes,
        "serialized_byte_reduction_percent": 100.0 * (legacy_bytes - compact_bytes) / legacy_bytes,
        "semantic_grant_events_equal": True,
        "fct_rows_equal": True,
        "non_byte_guard_counters_equal": True,
        "receiver_arrival_advance_ns": arrival_advance_ns,
        "switch_drops": compact["health"]["switch_total_drops"],
        "recovery_events": sum(compact["stats"][field] for field in RECOVERY_FIELDS),
        "pfc_events": compact["pfc_events"],
        "grant_trace_truncated": compact["grant_footer"]["truncated"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--legacy-run", type=Path, required=True)
    parser.add_argument("--compact-run", type=Path, required=True)
    parser.add_argument("--legacy-grants", type=Path, required=True)
    parser.add_argument("--compact-grants", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    spec = json.loads(args.spec.read_text())
    result = validate(
        spec,
        run_view(args.legacy_run, args.legacy_grants),
        run_view(args.compact_run, args.compact_grants),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
