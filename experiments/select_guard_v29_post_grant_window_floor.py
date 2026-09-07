#!/usr/bin/env python3
"""Apply the frozen V29 post-grant-window gates after mechanism admission."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, Mapping, Sequence

try:
    from experiments.run_campaign import sha256_file, write_json
    from experiments.select_guard_v23_elephant_spillover import (
        SelectionError, read_object,
    )
    from experiments.select_guard_v28_transport_window_floor import (
        PERCENT_GATES as V28_PERCENT_GATES,
    )
except ModuleNotFoundError:
    from run_campaign import sha256_file, write_json
    from select_guard_v23_elephant_spillover import SelectionError, read_object
    from select_guard_v28_transport_window_floor import (
        PERCENT_GATES as V28_PERCENT_GATES,
    )


PERCENT_GATES = tuple(
    (comparison.replace("guard_window_floor", "guard_post_grant_window"),
     metric, field)
    for comparison, metric, field in V28_PERCENT_GATES
)


def _paired(performance: Mapping[str, object], comparison: str,
            metric: str, statistic: str) -> Mapping[str, object]:
    value = dict(dict(performance["paired"])[comparison])[metric]
    stats = dict(value).get(statistic)
    if not isinstance(stats, dict) or int(stats.get("n", -1)) != 5:
        raise SelectionError(
            f"missing five-seed {statistic} for {comparison}/{metric}")
    return stats


def select(campaign: Path) -> Dict[str, object]:
    spec_path = campaign / "campaign.json"
    preflight_path = campaign / "preflight.json"
    mechanism_path = campaign / "summary" / "mechanism-formal.json"
    report_path = campaign / "summary" / "general_report.json"
    spec = read_object(spec_path)
    preflight = read_object(preflight_path)
    mechanism = read_object(mechanism_path)
    report = read_object(report_path)
    if (spec.get("mechanism_profile") != "V29" or
            list(spec.get("seeds", [])) != [215, 216, 217, 218, 219] or
            set(dict(spec.get("arms", {}))) !=
                {"guard_k1", "guard_post_grant_window", "hpcc", "homa"}):
        raise SelectionError("campaign is not the frozen V29 four-arm matrix")
    sealed_spec_path = Path(str(preflight.get("spec_path", "")))
    if (not sealed_spec_path.is_file() or
            preflight.get("spec_sha256") != sha256_file(sealed_spec_path) or
            read_object(sealed_spec_path) != spec):
        raise SelectionError("campaign differs from the sealed V29 preflight")
    if (mechanism.get("phase") != "formal" or mechanism.get("passed") is not True or
            mechanism.get("performance_unsealed") is not True or
            int(mechanism.get("expected_runs", -1)) != 20 or
            int(mechanism.get("admitted_runs", -1)) != 20 or
            mechanism.get("preflight_sha256") != sha256_file(preflight_path)):
        raise SelectionError("V29 performance is still sealed by the mechanism gate")
    performance_by_workload = dict(report.get("performance", {}))
    if set(performance_by_workload) != {"AliStorage40"}:
        raise SelectionError("V29 report must contain only AliStorage40")
    performance = dict(performance_by_workload["AliStorage40"])
    selection = dict(spec["selection"])
    gates: Dict[str, bool] = {}
    evidence: Dict[str, object] = {}
    for comparison, metric, field in PERCENT_GATES:
        stats = _paired(performance, comparison, metric, "percent_vs_right")
        limit = float(selection[field])
        name = f"{comparison}/{metric}/ci95_high_at_most_{limit}"
        gates[name] = float(stats["ci95_high"]) <= limit
        evidence[f"{comparison}/{metric}"] = {
            "paired_percent": stats, "limit": limit,
        }
    comparison = "guard_post_grant_window_minus_guard_k1"
    metric = "overall_flow_goodput_jain"
    jain = _paired(performance, comparison, metric, "difference")
    limit = float(selection[
        "require_candidate_minus_control_jain_difference_ci95_low_at_least"])
    gates[f"{comparison}/{metric}/ci95_low_at_least_{limit}"] = (
        float(jain["ci95_low"]) >= limit)
    evidence[f"{comparison}/{metric}"] = {
        "paired_difference": jain, "limit": limit,
    }
    selected = all(gates.values())
    return {
        "schema_version": 1,
        "campaign": spec["name"],
        "development_only": True,
        "candidate_arm": "guard_post_grant_window",
        "control_arm": "guard_k1",
        "status": "select_v29_for_independent_holdout" if selected else "retain_k1",
        "selected_arm": "guard_post_grant_window" if selected else "guard_k1",
        "all_frozen_gates_passed": selected,
        "requires_independent_holdout_before_claim": selected,
        "gates": gates,
        "evidence": evidence,
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
    print(f"V29 selection: {result['status']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, OSError, ValueError, SelectionError) as error:
        print(f"error: {error}")
        raise SystemExit(2)
