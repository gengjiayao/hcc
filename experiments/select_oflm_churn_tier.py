#!/usr/bin/env python3
"""Apply the frozen OFLM mechanism criteria before exposing performance metrics."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Dict, Mapping, Sequence


COMBINATIONS = ("00", "01", "10", "11")


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


def reduction(before: float, after: float) -> float:
    if before <= 0:
        raise SelectionError("reduction baseline must be positive")
    return 1.0 - after / before


def evaluate(design: Mapping[str, object], tier_name: str,
             summaries: Mapping[str, Mapping[str, object]]) -> Dict[str, object]:
    if set(summaries) != set(COMBINATIONS):
        raise SelectionError("exactly four 00/01/10/11 summaries are required")
    try:
        thresholds = design["mechanism_acceptance"]
        fixed = design["fixed_run"]
        tiers = design["tiers"]
    except KeyError as exc:
        raise SelectionError("ladder lacks required design sections") from exc
    if not isinstance(thresholds, dict) or not isinstance(fixed, dict) or not isinstance(tiers, list):
        raise SelectionError("ladder design sections have invalid types")
    matching_tiers = [tier for tier in tiers if tier.get("name") == tier_name]
    if len(matching_tiers) != 1:
        raise SelectionError(f"unknown or duplicate tier {tier_name!r}")
    tier = matching_tiers[0]

    flow_hashes = set()
    for combo in COMBINATIONS:
        summary = summaries[combo]
        if summary.get("status") != "validated_complete" or summary.get("workload") != "oflm-churn":
            raise SelectionError(f"{combo} is not a validated OFLM churn summary")
        configuration = summary.get("configuration", {})
        if (int(configuration.get("selective_registration", -1)) != int(combo[0]) or
                int(configuration.get("proactive_release", -1)) != int(combo[1])):
            raise SelectionError(f"{combo} summary has mismatched component switches")
        flow_hashes.add(str(summary.get("provenance", {}).get("flow_sha256", "")))
    if len(flow_hashes) != 1 or "" in flow_hashes:
        raise SelectionError("all four summaries must use one non-empty flow SHA")

    def mechanism(combo: str, field: str) -> float:
        return float(summaries[combo]["mechanism"][field])

    def validation(combo: str, field: str) -> float:
        return float(summaries[combo]["validation"][field])

    registration_reductions = {
        "00_vs_10": reduction(mechanism("00", "registrations"),
                              mechanism("10", "registrations")),
        "01_vs_11": reduction(mechanism("01", "registrations"),
                              mechanism("11", "registrations")),
    }
    active_area_reductions = {
        "00_vs_01": reduction(mechanism("00", "churn_active_set_area_ns"),
                              mechanism("01", "churn_active_set_area_ns")),
        "10_vs_11": reduction(mechanism("10", "churn_active_set_area_ns"),
                              mechanism("11", "churn_active_set_area_ns")),
    }
    grant_reductions = {
        "00_vs_10": reduction(mechanism("00", "grants_sent"),
                              mechanism("10", "grants_sent")),
        "01_vs_11": reduction(mechanism("01", "grants_sent"),
                              mechanism("11", "grants_sent")),
    }
    completed_fractions = {
        combo: validation(combo, "completed_flow_count") /
        validation(combo, "generated_flow_count")
        for combo in COMBINATIONS
    }
    criterion_values = {
        "completed_fraction_min": min(completed_fractions.values()),
        "switch_drops_max": max(validation(combo, "switch_drops") for combo in COMBINATIONS),
        "recovery_events_max": max(validation(combo, "recovery_events") for combo in COMBINATIONS),
        "registration_reduction_fraction_min": min(registration_reductions.values()),
        "proactive_release_fraction_min": min(
            mechanism(combo, "proactive_release_fraction") for combo in ("01", "11")),
        "churn_release_lead_median_ns_min": min(
            mechanism(combo, "churn_release_lead_median_ns") for combo in ("01", "11")),
        "churn_active_set_area_reduction_fraction_min": min(active_area_reductions.values()),
        "selective_total_grants_reduction_fraction_min": min(grant_reductions.values()),
    }
    criteria = {
        "completed_fraction_min": (
            criterion_values["completed_fraction_min"] >=
            float(thresholds["completed_fraction_min"])),
        "switch_drops_max": (
            criterion_values["switch_drops_max"] <= float(thresholds["switch_drops_max"])),
        "recovery_events_max": (
            criterion_values["recovery_events_max"] <= float(thresholds["recovery_events_max"])),
        "registration_reduction_fraction_min": (
            criterion_values["registration_reduction_fraction_min"] >=
            float(thresholds["registration_reduction_fraction_min"])),
        "proactive_release_fraction_min": (
            criterion_values["proactive_release_fraction_min"] >=
            float(thresholds["proactive_release_fraction_min"])),
        "churn_release_lead_median_ns_min": (
            criterion_values["churn_release_lead_median_ns_min"] >=
            float(thresholds["churn_release_lead_median_ns_min"])),
        "churn_active_set_area_reduction_fraction_min": (
            criterion_values["churn_active_set_area_reduction_fraction_min"] >=
            float(thresholds["churn_active_set_area_reduction_fraction_min"])),
        "selective_total_grants_reduction_fraction_min": (
            criterion_values["selective_total_grants_reduction_fraction_min"] >=
            float(thresholds["selective_total_grants_reduction_fraction_min"])),
        "artifact_limit": max(
            validation(combo, "artifact_bytes") for combo in COMBINATIONS
        ) <= float(fixed["artifact_limit_bytes"]),
    }
    selected = all(criteria.values())
    result: Dict[str, object] = {
        "schema_version": 1,
        "tier": tier,
        "flow_sha256": next(iter(flow_hashes)),
        "decision": "selected" if selected else "advance_to_next_tier",
        "criteria": criteria,
        "criterion_values": criterion_values,
        "registration_reductions": registration_reductions,
        "churn_active_set_area_reductions": active_area_reductions,
        "selective_total_grants_reductions": grant_reductions,
    }
    # Performance is deliberately withheld until the mechanism-only decision
    # has been made from the frozen criteria above.
    if selected:
        result["post_selection_performance"] = {
            combo: summaries[combo]["performance"] for combo in COMBINATIONS
        }
    return result


def parse_summary_args(values: Sequence[str]) -> Dict[str, Path]:
    paths: Dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise SelectionError("--summary must use COMBO=PATH")
        combo, raw_path = value.split("=", 1)
        if combo not in COMBINATIONS or combo in paths:
            raise SelectionError(f"invalid or duplicate summary combination {combo!r}")
        paths[combo] = Path(raw_path)
    if set(paths) != set(COMBINATIONS):
        raise SelectionError("one summary is required for each 00/01/10/11 combination")
    return paths


def atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Select the first valid frozen OFLM churn tier")
    parser.add_argument("--ladder", required=True, type=Path)
    parser.add_argument("--tier", required=True)
    parser.add_argument("--summary", action="append", default=[])
    parser.add_argument("--json-out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        paths = parse_summary_args(args.summary)
        summaries = {combo: read_json(path) for combo, path in paths.items()}
        result = evaluate(read_json(args.ladder), args.tier, summaries)
        result["provenance"] = {
            "ladder": str(args.ladder.resolve()),
            "ladder_sha256": sha256_file(args.ladder),
            "summaries": {combo: str(path.resolve()) for combo, path in paths.items()},
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
