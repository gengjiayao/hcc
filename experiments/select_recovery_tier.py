#!/usr/bin/env python3
"""Select the first recovery tier using only predeclared mechanism criteria."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Dict, Mapping, Sequence


ARMS = ("pfc", "irn")


class SelectionError(RuntimeError):
    pass


def read_json(path: Path) -> Mapping[str, object]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise SelectionError(f"JSON root must be an object: {path}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_arm_paths(values: Sequence[str]) -> Dict[str, Path]:
    paths: Dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise SelectionError("--summary must use ARM=PATH")
        arm, raw_path = value.split("=", 1)
        if arm not in ARMS or arm in paths:
            raise SelectionError(f"invalid or duplicate arm {arm!r}")
        paths[arm] = Path(raw_path)
    if set(paths) != set(ARMS):
        raise SelectionError("exactly pfc and irn summaries are required")
    return paths


def evaluate(ladder: Mapping[str, object], tier_name: str,
             summaries: Mapping[str, Mapping[str, object]]) -> Dict[str, object]:
    tiers = ladder.get("tiers")
    if not isinstance(tiers, list):
        raise SelectionError("ladder tiers must be a list")
    matching = [tier for tier in tiers if tier.get("name") == tier_name]
    if len(matching) != 1:
        raise SelectionError(f"expected one tier named {tier_name}")
    tier = matching[0]
    expected_flows = int(tier["expected_flows"])
    expected_error = float(tier["error_rate_per_link"])
    flow_hashes = set()
    for arm in ARMS:
        summary = summaries[arm]
        if summary.get("status") != "validated_complete" or summary.get("arm") != arm:
            raise SelectionError(f"invalid {arm} summary status or arm")
        flow_hashes.add(str(summary["provenance"]["flow_sha256"]))
        if int(summary["validation"]["generated_flow_count"]) != expected_flows:
            raise SelectionError(f"{arm} flow count differs from frozen tier")
        if float(summary["configuration"]["error_rate_per_link"]) != expected_error:
            raise SelectionError(f"{arm} error rate differs from frozen tier")
    if len(flow_hashes) != 1:
        raise SelectionError("PFC and IRN arms do not share an identical flow SHA")

    pfc_m = summaries["pfc"]["mechanism"]
    irn_m = summaries["irn"]["mechanism"]
    completed = min(
        int(summaries[arm]["validation"]["completed_flow_count"]) /
        int(summaries[arm]["validation"]["generated_flow_count"])
        for arm in ARMS)
    values = {
        "completed_fraction_min": completed,
        "pfc_matched_intervals": int(pfc_m["pfc_matched_intervals"]),
        "pfc_cumulative_pause_ns": int(pfc_m["pfc_cumulative_pause_ns"]),
        "pfc_unmatched_events": int(pfc_m["pfc_unmatched_events"]),
        "irn_nacks_generated": int(irn_m["irn_nacks_generated"]),
        "irn_nacks_received": int(irn_m["irn_nacks_received"]),
        "irn_retransmit_packets": int(irn_m["irn_retransmit_packets"]),
        "irn_retransmit_bytes": int(irn_m["irn_retransmit_bytes"]),
        "pfc_events_in_irn_arm": (
            int(irn_m["pfc_pause_count"]) + int(irn_m["pfc_resume_count"])),
        "irn_events_in_pfc_arm": (
            int(pfc_m["irn_nacks_generated"]) + int(pfc_m["irn_nacks_received"]) +
            int(pfc_m["irn_retransmit_packets"])),
    }
    thresholds = ladder["mechanism_acceptance"]
    checks = {
        "completed_fraction_min": values["completed_fraction_min"] >= float(thresholds["completed_fraction_min"]),
        "pfc_matched_intervals_min": values["pfc_matched_intervals"] >= int(thresholds["pfc_matched_intervals_min"]),
        "pfc_cumulative_pause_ns_min": values["pfc_cumulative_pause_ns"] >= int(thresholds["pfc_cumulative_pause_ns_min"]),
        "pfc_unmatched_events_max": values["pfc_unmatched_events"] <= int(thresholds["pfc_unmatched_events_max"]),
        "irn_nacks_generated_min": values["irn_nacks_generated"] >= int(thresholds["irn_nacks_generated_min"]),
        "irn_nacks_received_min": values["irn_nacks_received"] >= int(thresholds["irn_nacks_received_min"]),
        "irn_retransmit_packets_min": values["irn_retransmit_packets"] >= int(thresholds["irn_retransmit_packets_min"]),
        "irn_retransmit_bytes_min": values["irn_retransmit_bytes"] >= int(thresholds["irn_retransmit_bytes_min"]),
        "pfc_events_in_irn_arm_max": values["pfc_events_in_irn_arm"] <= int(thresholds["pfc_events_in_irn_arm_max"]),
        "irn_events_in_pfc_arm_max": values["irn_events_in_pfc_arm"] <= int(thresholds["irn_events_in_pfc_arm_max"]),
    }
    decision = "selected" if all(checks.values()) else "advance_to_next_tier"
    result = {
        "schema_version": 1, "decision": decision, "tier": tier,
        "flow_sha256": next(iter(flow_hashes)), "criterion_values": values,
        "criterion_checks": checks,
        "mechanism_by_arm": {arm: summaries[arm]["mechanism"] for arm in ARMS},
    }
    if decision == "selected":
        result["post_selection_performance"] = {
            arm: summaries[arm]["performance"] for arm in ARMS}
    return result


def atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ladder", required=True, type=Path)
    parser.add_argument("--tier", required=True)
    parser.add_argument("--summary", action="append", default=[])
    parser.add_argument("--json-out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        paths = parse_arm_paths(args.summary)
        result = evaluate(
            read_json(args.ladder), args.tier,
            {arm: read_json(path) for arm, path in paths.items()})
        result["provenance"] = {
            "ladder": str(args.ladder.resolve()),
            "ladder_sha256": sha256_file(args.ladder),
            "summaries": {arm: str(path.resolve()) for arm, path in paths.items()},
        }
        output = args.json_out.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(output, result)
    except (OSError, ValueError, KeyError, SelectionError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"{result['decision']}: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
