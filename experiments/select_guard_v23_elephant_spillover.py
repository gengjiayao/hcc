#!/usr/bin/env python3
"""Apply the frozen V23 cap-qualified spillover versus K=1 paired gates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Mapping, Sequence

try:
    from experiments.run_campaign import sha256_file, write_json
    from experiments.select_guard_v20_elephant_k import (
        JAIN_GATE, JAIN_METRIC, PERCENT_GATES,
    )
except ModuleNotFoundError:
    from run_campaign import sha256_file, write_json
    from select_guard_v20_elephant_k import (
        JAIN_GATE, JAIN_METRIC, PERCENT_GATES,
    )


class SelectionError(RuntimeError):
    """A V23 artifact or frozen selection contract is incomplete."""


def read_object(path: Path) -> Mapping[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SelectionError(f"JSON root must be an object: {path}")
    return value


def select(campaign: Path) -> Dict[str, object]:
    spec_path = campaign / "campaign.json"
    preflight_path = campaign / "preflight.json"
    mechanism_path = campaign / "summary" / "mechanism-formal.json"
    report_path = campaign / "summary" / "general_report.json"
    spec = read_object(spec_path)
    preflight = read_object(preflight_path)
    mechanism = read_object(mechanism_path)
    report = read_object(report_path)
    if (spec.get("mechanism_profile") != "V23" or
            list(spec.get("seeds", [])) != [185, 186, 187, 188, 189] or
            set(dict(spec.get("arms", {}))) != {"guard_k1", "guard_spillover"}):
        raise SelectionError("campaign is not the frozen V23 spillover matrix")
    sealed_spec_path = Path(str(preflight.get("spec_path", "")))
    if (not sealed_spec_path.is_file() or
            preflight.get("spec_sha256") != sha256_file(sealed_spec_path)):
        raise SelectionError("original campaign spec differs from sealed preflight")
    if read_object(sealed_spec_path) != spec:
        raise SelectionError("campaign copy is not semantically equal to sealed spec")
    if (mechanism.get("phase") != "formal" or mechanism.get("passed") is not True or
            mechanism.get("performance_unsealed") is not True or
            int(mechanism.get("expected_runs", -1)) != 10 or
            int(mechanism.get("admitted_runs", -1)) != 10 or
            mechanism.get("preflight_sha256") != sha256_file(preflight_path)):
        raise SelectionError("V23 performance is still sealed by the mechanism gate")
    performance = dict(report.get("performance", {}))
    if set(performance) != {"AliStorage50"}:
        raise SelectionError("V23 report must contain only AliStorage50")
    paired = dict(dict(performance["AliStorage50"])["paired"])
    comparison = dict(paired.get("guard_spillover_minus_guard_k1", {}))
    selection = dict(spec["selection"])
    gates: Dict[str, bool] = {}
    evidence: Dict[str, object] = {}
    for metric, field in PERCENT_GATES.items():
        stats = dict(comparison.get(metric, {})).get("percent_vs_right")
        if not isinstance(stats, dict) or int(stats.get("n", -1)) != 5:
            raise SelectionError(f"missing five-seed paired percent for {metric}")
        limit = float(selection[field])
        high = float(stats["ci95_high"])
        gates[f"{metric}_percent_ci95_high_at_most_{limit}"] = high <= limit
        evidence[metric] = {"paired_percent": stats, "limit": limit}
    jain = dict(comparison.get(JAIN_METRIC, {})).get("difference")
    if not isinstance(jain, dict) or int(jain.get("n", -1)) != 5:
        raise SelectionError("missing five-seed paired Jain difference")
    jain_limit = float(selection[JAIN_GATE])
    gates[f"{JAIN_METRIC}_difference_ci95_low_at_least_{jain_limit}"] = (
        float(jain["ci95_low"]) >= jain_limit)
    evidence[JAIN_METRIC] = {"paired_difference": jain, "limit": jain_limit}
    selected = all(gates.values())
    return {
        "schema_version": 1, "campaign": spec["name"],
        "development_only": True, "baseline_arm": "guard_k1",
        "candidate_arm": "guard_spillover",
        "status": "select_spillover_for_fresh_holdout" if selected else "retain_k1",
        "selected_arm": "guard_spillover" if selected else "guard_k1",
        "all_frozen_gates_passed": selected,
        "requires_fresh_three_arm_holdout_before_claim": True,
        "gates": gates, "evidence": evidence,
        "provenance": {
            "spec_sha256": str(preflight["spec_sha256"]),
            "preflight_sha256": sha256_file(preflight_path),
            "mechanism_sha256": sha256_file(mechanism_path),
            "performance_report_sha256": sha256_file(report_path),
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign", type=Path)
    args = parser.parse_args(argv)
    result = select(args.campaign.resolve())
    write_json(args.campaign.resolve() / "summary" / "selection.json", result)
    print(f"V23 selection: {result['status']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, OSError, ValueError, SelectionError) as error:
        print(f"error: {error}")
        raise SystemExit(2)
