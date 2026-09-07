#!/usr/bin/env python3
"""Apply the frozen V28 transport-window-floor gates after mechanism admission."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, Mapping, Sequence, Tuple

try:
    from experiments.run_campaign import sha256_file, write_json
    from experiments.select_guard_v23_elephant_spillover import (
        SelectionError, read_object,
    )
except ModuleNotFoundError:
    from run_campaign import sha256_file, write_json
    from select_guard_v23_elephant_spillover import SelectionError, read_object


PERCENT_GATES: Tuple[Tuple[str, str, str], ...] = (
    ("guard_window_floor_minus_guard_k1", "gt_1MB_fct_us_mean",
     "require_candidate_minus_control_gt_1MB_mean_percent_ci95_high_at_most"),
    ("guard_window_floor_minus_guard_k1", "overall_fct_us_mean",
     "require_candidate_minus_control_overall_mean_percent_ci95_high_at_most"),
    ("guard_window_floor_minus_guard_k1", "le_8KB_fct_us_p95",
     "require_candidate_minus_control_le_8KB_p95_percent_ci95_high_at_most"),
    ("guard_window_floor_minus_guard_k1", "queue_bytes_mean",
     "require_candidate_minus_control_queue_mean_percent_ci95_high_at_most"),
    ("guard_window_floor_minus_guard_k1", "queue_bytes_p99",
     "require_candidate_minus_control_queue_p99_percent_ci95_high_at_most"),
    ("guard_window_floor_minus_homa", "gt_1MB_fct_us_mean",
     "require_candidate_minus_homa_gt_1MB_mean_percent_ci95_high_at_most"),
    ("guard_window_floor_minus_homa", "overall_fct_us_mean",
     "require_candidate_minus_homa_overall_mean_percent_ci95_high_at_most"),
    ("guard_window_floor_minus_homa", "overall_fct_us_p95",
     "require_candidate_minus_homa_overall_p95_percent_ci95_high_at_most"),
    ("guard_window_floor_minus_homa", "overall_fct_us_p99",
     "require_candidate_minus_homa_overall_p99_percent_ci95_high_at_most"),
    ("guard_window_floor_minus_homa", "le_8KB_fct_us_p95",
     "require_candidate_minus_homa_le_8KB_p95_percent_ci95_high_at_most"),
    ("guard_window_floor_minus_homa", "queue_bytes_mean",
     "require_candidate_minus_homa_queue_mean_percent_ci95_high_at_most"),
    ("guard_window_floor_minus_homa", "queue_bytes_p99",
     "require_candidate_minus_homa_queue_p99_percent_ci95_high_at_most"),
    ("guard_window_floor_minus_hpcc", "overall_fct_us_mean",
     "require_candidate_minus_hpcc_overall_mean_percent_ci95_high_at_most"),
    ("guard_window_floor_minus_hpcc", "overall_fct_us_p95",
     "require_candidate_minus_hpcc_overall_p95_percent_ci95_high_at_most"),
    ("guard_window_floor_minus_hpcc", "overall_fct_us_p99",
     "require_candidate_minus_hpcc_overall_p99_percent_ci95_high_at_most"),
    ("guard_window_floor_minus_hpcc", "gt_1MB_fct_us_mean",
     "require_candidate_minus_hpcc_gt_1MB_mean_percent_ci95_high_at_most"),
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
    if (spec.get("mechanism_profile") != "V28" or
            list(spec.get("seeds", [])) != [210, 211, 212, 213, 214] or
            set(dict(spec.get("arms", {}))) !=
                {"guard_k1", "guard_window_floor", "hpcc", "homa"}):
        raise SelectionError("campaign is not the frozen V28 four-arm matrix")
    sealed_spec_path = Path(str(preflight.get("spec_path", "")))
    if (not sealed_spec_path.is_file() or
            preflight.get("spec_sha256") != sha256_file(sealed_spec_path) or
            read_object(sealed_spec_path) != spec):
        raise SelectionError("campaign differs from the sealed V28 preflight")
    if (mechanism.get("phase") != "formal" or mechanism.get("passed") is not True or
            mechanism.get("performance_unsealed") is not True or
            int(mechanism.get("expected_runs", -1)) != 20 or
            int(mechanism.get("admitted_runs", -1)) != 20 or
            mechanism.get("preflight_sha256") != sha256_file(preflight_path)):
        raise SelectionError("V28 performance is still sealed by the mechanism gate")
    performance_by_workload = dict(report.get("performance", {}))
    if set(performance_by_workload) != {"AliStorage40"}:
        raise SelectionError("V28 report must contain only AliStorage40")
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
    jain_comparison = "guard_window_floor_minus_guard_k1"
    jain_metric = "overall_flow_goodput_jain"
    jain = _paired(performance, jain_comparison, jain_metric, "difference")
    jain_limit = float(selection[
        "require_candidate_minus_control_jain_difference_ci95_low_at_least"])
    gates[f"{jain_comparison}/{jain_metric}/ci95_low_at_least_{jain_limit}"] = (
        float(jain["ci95_low"]) >= jain_limit)
    evidence[f"{jain_comparison}/{jain_metric}"] = {
        "paired_difference": jain, "limit": jain_limit,
    }
    selected = all(gates.values())
    return {
        "schema_version": 1,
        "campaign": spec["name"],
        "development_only": True,
        "candidate_arm": "guard_window_floor",
        "control_arm": "guard_k1",
        "status": "select_v28_for_independent_holdout" if selected else "retain_k1",
        "selected_arm": "guard_window_floor" if selected else "guard_k1",
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
    print(f"V28 selection: {result['status']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, OSError, ValueError, SelectionError) as error:
        print(f"error: {error}")
        raise SystemExit(2)
