#!/usr/bin/env python3
"""Apply the frozen five-seed recovery mechanism replication gate."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Dict, Mapping, Sequence


class GateError(RuntimeError):
    pass


def read_json(path: Path) -> Mapping[str, object]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise GateError(f"JSON root must be an object: {path}")
    return value


def parse_seed_paths(values: Sequence[str]) -> Dict[int, Path]:
    paths: Dict[int, Path] = {}
    for value in values:
        if "=" not in value:
            raise GateError("--summary must use SEED=PATH")
        raw_seed, raw_path = value.split("=", 1)
        seed = int(raw_seed)
        if seed <= 0 or seed in paths:
            raise GateError(f"invalid or duplicate seed {seed}")
        paths[seed] = Path(raw_path)
    if len(paths) != 5:
        raise GateError("cross-seed recovery gate requires exactly five seeds")
    return paths


def evaluate(ladder: Mapping[str, object], tier_name: str, phase: str,
             summaries: Mapping[int, Mapping[str, object]]) -> Dict[str, object]:
    tiers = ladder.get("tiers")
    matching = [tier for tier in tiers if tier.get("name") == tier_name]
    if len(matching) != 1:
        raise GateError(f"expected one tier named {tier_name}")
    tier = matching[0]
    expected_flows = int(tier["expected_flows"])
    expected_error = float(tier["error_rate_per_link"])
    seed_checks: Dict[str, object] = {}
    hashes: Dict[str, str] = {}
    for seed in sorted(summaries):
        summary = summaries[seed]
        if summary.get("status") != "validated_complete" or summary.get("arm") != phase:
            raise GateError(f"seed {seed} is not a validated {phase} summary")
        if int(summary["validation"]["generated_flow_count"]) != expected_flows:
            raise GateError(f"seed {seed} flow count differs from frozen tier")
        if float(summary["configuration"]["error_rate_per_link"]) != expected_error:
            raise GateError(f"seed {seed} error rate differs from frozen tier")
        generated = int(summary["validation"]["generated_flow_count"])
        completed = int(summary["validation"]["completed_flow_count"])
        mechanism = summary["mechanism"]
        checks = {"all_flows_completed": completed == generated}
        if phase == "irn":
            checks.update({
                "irn_nacks_generated": int(mechanism["irn_nacks_generated"]) > 0,
                "irn_nacks_received": int(mechanism["irn_nacks_received"]) > 0,
                "irn_retransmit_packets": int(mechanism["irn_retransmit_packets"]) > 0,
                "irn_retransmit_bytes": int(mechanism["irn_retransmit_bytes"]) > 0,
                "zero_timeout_recoveries": int(mechanism["timeout_recoveries"]) == 0,
            })
        else:
            checks.update({
                "pfc_matched_intervals": int(mechanism["pfc_matched_intervals"]) > 0,
                "pfc_cumulative_pause_ns": int(mechanism["pfc_cumulative_pause_ns"]) > 0,
                "zero_unmatched_pfc_events": int(mechanism["pfc_unmatched_events"]) == 0,
            })
        seed_checks[str(seed)] = checks
        hashes[str(seed)] = str(summary["provenance"]["flow_sha256"])
    passed = all(all(checks.values()) for checks in seed_checks.values())
    return {
        "schema_version": 1, "tier": tier, "phase": phase,
        "decision": (
            "run_pfc" if phase == "irn" and passed else
            "selected" if phase == "pfc" and passed else
            "advance_to_next_tier"),
        "passed": passed, "flow_sha256_by_seed": hashes,
        "seed_checks": seed_checks,
    }


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
    parser.add_argument("--phase", required=True, choices=("irn", "pfc"))
    parser.add_argument("--summary", action="append", default=[])
    parser.add_argument("--json-out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        paths = parse_seed_paths(args.summary)
        result = evaluate(
            read_json(args.ladder), args.tier, args.phase,
            {seed: read_json(path) for seed, path in paths.items()})
        result["provenance"] = {
            "ladder": str(args.ladder.resolve()),
            "summaries": {str(seed): str(path.resolve()) for seed, path in paths.items()},
        }
        output = args.json_out.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(output, result)
    except (OSError, ValueError, KeyError, TypeError, GateError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"{result['decision']}: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
