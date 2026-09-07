#!/usr/bin/env python3
"""Apply the frozen V34 cap-triggered-refresh gates after admission."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, Sequence, Tuple

try:
    from experiments.run_campaign import sha256_file, write_json
    from experiments.select_guard_v23_elephant_spillover import (
        SelectionError, read_object,
    )
    from experiments.select_guard_v29_post_grant_window_floor import _paired
    from experiments.select_guard_v32_ack_slack_window import (
        PERCENT_GATES as V32_PERCENT_GATES,
    )
except ModuleNotFoundError:
    from run_campaign import sha256_file, write_json
    from select_guard_v23_elephant_spillover import SelectionError, read_object
    from select_guard_v29_post_grant_window_floor import _paired
    from select_guard_v32_ack_slack_window import PERCENT_GATES as V32_PERCENT_GATES


def _comparison(name: str) -> str:
    return name.replace(
        "guard_ack_slack_window_minus_guard_k1",
        "guard_cap_refresh_minus_guard_ack_slack_window",
    ).replace("guard_ack_slack_window_minus_", "guard_cap_refresh_minus_")


PERCENT_GATES: Tuple[Tuple[str, str, str], ...] = tuple(
    (_comparison(comparison), metric, field)
    for comparison, metric, field in V32_PERCENT_GATES
) + (
    ("guard_cap_refresh_minus_guard_ack_slack_window", "overall_fct_us_p95",
     "require_candidate_minus_control_overall_p95_percent_ci95_high_at_most"),
    ("guard_cap_refresh_minus_guard_ack_slack_window", "overall_fct_us_p99",
     "require_candidate_minus_control_overall_p99_percent_ci95_high_at_most"),
)


def select(campaign: Path) -> Dict[str, object]:
    spec_path = campaign / "campaign.json"
    preflight_path = campaign / "preflight.json"
    mechanism_path = campaign / "summary" / "mechanism-formal.json"
    report_path = campaign / "summary" / "general_report.json"
    spec = read_object(spec_path)
    preflight = read_object(preflight_path)
    mechanism = read_object(mechanism_path)
    report = read_object(report_path)
    if (spec.get("mechanism_profile") != "V34" or
            list(spec.get("seeds", [])) != [235, 236, 237, 238, 239] or
            set(dict(spec.get("arms", {}))) != {
                "guard_ack_slack_window", "guard_cap_refresh", "hpcc", "homa"}):
        raise SelectionError("campaign is not the frozen V34 four-arm matrix")
    sealed_spec_path = Path(str(preflight.get("spec_path", "")))
    if (not sealed_spec_path.is_file() or
            preflight.get("spec_sha256") != sha256_file(sealed_spec_path) or
            read_object(sealed_spec_path) != spec):
        raise SelectionError("campaign differs from the sealed V34 preflight")
    if (mechanism.get("phase") != "formal" or mechanism.get("passed") is not True or
            mechanism.get("performance_unsealed") is not True or
            int(mechanism.get("expected_runs", -1)) != 20 or
            int(mechanism.get("admitted_runs", -1)) != 20 or
            mechanism.get("preflight_sha256") != sha256_file(preflight_path)):
        raise SelectionError("V34 performance is still sealed by the mechanism gate")
    workloads = dict(report.get("performance", {}))
    if set(workloads) != {"AliStorage40"}:
        raise SelectionError("V34 report must contain only AliStorage40")
    performance = dict(workloads["AliStorage40"])
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
    for comparison, field in (
            ("guard_cap_refresh_minus_guard_ack_slack_window",
             "require_candidate_minus_control_jain_difference_ci95_low_at_least"),
            ("guard_cap_refresh_minus_homa",
             "require_candidate_minus_homa_jain_difference_ci95_low_at_least")):
        metric = "overall_flow_goodput_jain"
        stats = _paired(performance, comparison, metric, "difference")
        limit = float(selection[field])
        gates[f"{comparison}/{metric}/ci95_low_at_least_{limit}"] = (
            float(stats["ci95_low"]) >= limit)
        evidence[f"{comparison}/{metric}"] = {
            "paired_difference": stats, "limit": limit,
        }
    selected = all(gates.values())
    return {
        "schema_version": 1,
        "campaign": spec["name"],
        "development_only": True,
        "candidate_arm": "guard_cap_refresh",
        "control_arm": "guard_ack_slack_window",
        "status": ("select_v34_for_independent_holdout" if selected
                   else "retain_v32_ack_slack_window"),
        "selected_arm": ("guard_cap_refresh" if selected
                         else "guard_ack_slack_window"),
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
    print(f"V34 selection: {result['status']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, OSError, ValueError, SelectionError) as error:
        print(f"error: {error}")
        raise SystemExit(2)
