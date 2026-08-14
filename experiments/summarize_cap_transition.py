#!/usr/bin/env python3
"""Validate and export the five-seed same-flow GUARD cap-transition trace."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

try:
    from experiments.summarize_campaign import SummaryError, parse_guard_stats
except ModuleNotFoundError:
    from summarize_campaign import SummaryError, parse_guard_stats


EXPECTED_CONFIG = {
    "CC_MODE": "11",
    "MONITOR_PROFILE": "bulk",
    "ENABLE_PFC": "1",
    "ENABLE_IRN": "0",
    "ERROR_RATE_PER_LINK": "0.0",
    "FAST_REACT": "0",
    "GUARD_LAMBDA": "1.0",
    "GUARD_SELECTIVE_REGISTRATION": "1",
    "GUARD_PROACTIVE_RELEASE": "0",
    "GUARD_KEEP_LAST_HOP_INT": "0",
    "GUARD_SIZE_PRIORITY": "0",
    "GUARD_SENDER_SRPT": "0",
    "GUARD_WORK_CONSERVING": "0",
    "GUARD_CONTROLLER_TRACE": "1",
    "GUARD_CONTROLLER_TRACE_MAX_LINES": "10000",
}
ZERO_STATS = (
    "recovery_nacks_generated",
    "recovery_nacks_received",
    "irn_nacks_generated",
    "irn_nacks_received",
    "irn_retransmit_packets",
    "irn_retransmit_bytes",
    "timeout_recoveries",
    "switch_ingress_drops",
    "switch_egress_drops",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_config(path: Path) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) >= 2:
            result[fields[0]] = fields[1]
    return result


def parse_flow(path: Path) -> List[Tuple[int, int, int, int, float]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or int(lines[0]) != 11 or len(lines) != 12:
        raise SummaryError(f"cap-transition trace must contain 11 flows: {path}")
    rows = []
    for line in lines[1:]:
        src, dst, pg, size, start = line.split()
        rows.append((int(src), int(dst), int(pg), int(size), float(start)))
    receiver = {(0, 8), (9, 8), (10, 8), (11, 8)}
    fabric = {(src, src + 8) for src in range(1, 8)}
    if {(src, dst) for src, dst, _pg, _size, _start in rows[:4]} != receiver:
        raise SummaryError("receiver phase geometry mismatch")
    if {(src, dst) for src, dst, _pg, _size, _start in rows[4:]} != fabric:
        raise SummaryError("fabric phase geometry mismatch")
    if {pg for _src, _dst, pg, _size, _start in rows} != {4}:
        raise SummaryError("cap-transition trace must use PG4")
    if {size for _src, _dst, _pg, size, _start in rows} != {4 * 1024 * 1024}:
        raise SummaryError("cap-transition trace must use 4 MiB flows")
    if min(row[4] for row in rows[4:]) <= max(row[4] for row in rows[:4]):
        raise SummaryError("fabric phase must begin after receiver phase")
    return rows


def parse_controller(path: Path) -> Tuple[List[Dict[str, str]], Dict[str, int]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    footers = [line for line in lines if line.startswith("# attempted ")]
    if len(footers) != 1:
        raise SummaryError(f"controller trace needs one footer: {path}")
    match = re.fullmatch(r"# attempted (\d+) written (\d+) truncated (\d+)", footers[0])
    if match is None:
        raise SummaryError(f"malformed controller footer: {footers[0]}")
    footer = dict(zip(("attempted", "written", "truncated"), map(int, match.groups())))
    data_lines = [line for line in lines if not line.startswith("#")]
    rows = list(csv.DictReader(data_lines))
    if footer["written"] != len(rows) or footer["attempted"] != footer["written"]:
        raise SummaryError("controller trace is incomplete")
    if footer["truncated"] != 0:
        raise SummaryError("controller trace was truncated")
    return rows, footer


def parse_fct_count(path: Path) -> int:
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def pfc_is_empty(path: Path) -> bool:
    rows = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return len(rows) <= 1


def analyze_one(seed: int, output: Path, flow: Path, manifest_path: Path,
                simulator_sha: str) -> Tuple[Dict[str, object], List[Dict[str, object]]]:
    output_id = output.name
    parse_flow(flow)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    flow_sha = sha256_file(flow)
    if manifest["sha256"] != flow_sha or manifest["seed"] != seed:
        raise SummaryError(f"seed {seed} flow manifest mismatch")
    snapshot = output / f"{output_id}_input_flow.txt"
    if sha256_file(snapshot) != flow_sha:
        raise SummaryError(f"seed {seed} simulator snapshot mismatch")
    config = parse_config(output / "config.txt")
    for key, expected in EXPECTED_CONFIG.items():
        if config.get(key) != expected:
            raise SummaryError(f"seed {seed} config {key}={config.get(key)!r}, expected {expected!r}")
    if config.get("RANDOM_SEED") != str(seed):
        raise SummaryError(f"seed {seed} RANDOM_SEED mismatch")

    controller, footer = parse_controller(output / f"{output_id}_out_guard_controller.csv")
    stats = parse_guard_stats(output / f"{output_id}_out_guard_stats.txt")
    total = stats["total"]
    nonzero = {field: int(total.get(field, 0)) for field in ZERO_STATS if int(total.get(field, 0))}
    if nonzero:
        raise SummaryError(f"seed {seed} has loss/recovery activity: {nonzero}")
    if parse_fct_count(output / f"{output_id}_out_fct.txt") != 11:
        raise SummaryError(f"seed {seed} did not complete all 11 flows")
    if not pfc_is_empty(output / f"{output_id}_out_pfc.txt"):
        raise SummaryError(f"seed {seed} unexpectedly triggered PFC")

    per_flow: List[Dict[str, object]] = []
    for flow_id in range(11):
        events = [row for row in controller if int(row["flow_id"]) == flow_id]
        grant_changes = [row for row in events if row["event_type"] == "grant" and row["rate_changed"] == "1"]
        fabric_changes = [row for row in events if row["event_type"] == "hpcc"
                          and row["binding"] == "reactive" and row["rate_changed"] == "1"]
        per_flow.append({
            "seed": seed,
            "flow_id": flow_id,
            "source": manifest["target"]["source"] if flow_id == 0 else "",
            "destination": manifest["target"]["destination"] if flow_id == 0 else "",
            "is_target": int(flow_id == 0),
            "grant_event_rate_changes": len(grant_changes),
            "shared_fabric_event_rate_changes": len(fabric_changes),
            "first_grant_change_ns": int(grant_changes[0]["time_ns"]) if grant_changes else "",
            "first_shared_fabric_change_ns": int(fabric_changes[0]["time_ns"]) if fabric_changes else "",
        })
    target = per_flow[0]
    if int(target["grant_event_rate_changes"]) < 1:
        raise SummaryError(f"seed {seed} target lacks receiver-grant pacing change")
    if int(target["shared_fabric_event_rate_changes"]) < 1:
        raise SummaryError(f"seed {seed} target lacks shared-fabric pacing change")
    if int(target["first_grant_change_ns"]) >= int(target["first_shared_fabric_change_ns"]):
        raise SummaryError(f"seed {seed} cap-transition order is reversed")

    seed_row: Dict[str, object] = {
        "seed": seed,
        "output_id": output_id,
        "simulator_sha": simulator_sha,
        "flow_sha256": flow_sha,
        "completed_flows": 11,
        "controller_attempted": footer["attempted"],
        "controller_written": footer["written"],
        "controller_truncated": footer["truncated"],
        "grants_sent": int(total["grants_sent"]),
        "grants_received": int(total["grants_received"]),
        "grant_event_rate_changes": int(total["grant_event_rate_changes"]),
        "hpcc_actual_rate_changes": int(total["hpcc_actual_rate_changes"]),
        "target_grant_event_rate_changes": target["grant_event_rate_changes"],
        "target_shared_fabric_event_rate_changes": target["shared_fabric_event_rate_changes"],
        "target_first_grant_change_ns": target["first_grant_change_ns"],
        "target_first_shared_fabric_change_ns": target["first_shared_fabric_change_ns"],
        "pfc_events": 0,
        "loss_recovery_events": 0,
        "admitted": 1,
    }
    return seed_row, per_flow


def write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    if not rows:
        raise SummaryError(f"refusing to write empty CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--guard-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--run", action="append", required=True,
                        help="seed=output-id; provide exactly seeds 1 through 5")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--simulator-sha", required=True)
    args = parser.parse_args()
    run_map: Dict[int, str] = {}
    for item in args.run:
        seed_text, output_id = item.split("=", 1)
        run_map[int(seed_text)] = output_id
    if set(run_map) != set(range(1, 6)):
        parser.error("--run must specify seeds 1 through 5 exactly once")
    seed_rows: List[Dict[str, object]] = []
    flow_rows: List[Dict[str, object]] = []
    for seed in range(1, 6):
        flow = args.results / "flows" / f"cap-transition-s{seed}.txt"
        seed_row, rows = analyze_one(
            seed,
            args.guard_root / "mix" / "output" / run_map[seed],
            flow,
            flow.with_suffix(".txt.manifest.json"),
            args.simulator_sha,
        )
        seed_rows.append(seed_row)
        flow_rows.extend(rows)
    args.output.mkdir(parents=True, exist_ok=True)
    write_csv(args.output / "cap_transition_per_seed.csv", seed_rows)
    write_csv(args.output / "cap_transition_per_flow.csv", flow_rows)
    admission = {
        "schema_version": 1,
        "status": "admitted",
        "claim": "The same target flow records an actual receiver-grant pacing change before actual shared-fabric-cap pacing changes.",
        "seeds": 5,
        "all_seeds_admitted": True,
        "simulator_sha": args.simulator_sha,
        "target_flow_id": 0,
        "per_seed_csv_sha256": sha256_file(args.output / "cap_transition_per_seed.csv"),
        "per_flow_csv_sha256": sha256_file(args.output / "cap_transition_per_flow.csv"),
        "run_ids": {str(row["seed"]): row["output_id"] for row in seed_rows},
        "flow_sha256": {str(row["seed"]): row["flow_sha256"] for row in seed_rows},
    }
    (args.output / "cap_transition_admission.json").write_text(
        json.dumps(admission, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
