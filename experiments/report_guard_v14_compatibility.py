#!/usr/bin/env python3
"""Produce a mechanism-only GUARD V14 compatibility admission report."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys
from typing import Dict, List, Mapping, Sequence

try:
    from experiments.run_campaign import CampaignError, sha256_file
    from experiments.run_guard_v14_compatibility import (
        FRESH_SCENARIOS, FRESH_SCOPE, _load_gate, compatibility_plan, read_spec,
        validate_sealed_replay, _lexical_absolute,
    )
    from experiments.analyze_guard_v14_compatibility import analyze
    from experiments.summarize_campaign import SummaryError
    from experiments.summarize_workload import atomic_json
except ModuleNotFoundError:  # Direct execution from experiments/.
    from run_campaign import CampaignError, sha256_file
    from run_guard_v14_compatibility import (
        FRESH_SCENARIOS, FRESH_SCOPE, _load_gate, compatibility_plan, read_spec,
        validate_sealed_replay, _lexical_absolute,
    )
    from analyze_guard_v14_compatibility import analyze
    from summarize_campaign import SummaryError
    from summarize_workload import atomic_json


RUN_FIELDS = (
    "ordinal", "scenario", "seed", "flow_count", "registered_flows",
    "completed_flows", "grant_frames", "audit_records", "mixed_pg_freezes",
    "progress_requests", "progress_transactions", "progress_commits",
    "draining_requests", "draining_completion_releases",
    "receiver_sent_rows_with_draining", "sender_srpt_selections",
    "sender_srpt_non_rr", "one_rtt_bypass_flows", "tail_bypass_flows",
)
SCENARIO_FIELDS = (
    "scenario", "seed_count", "flow_count_min", "flow_count_max",
    "registered_flows_min", "registered_flows_max", "audit_records_min",
    "audit_records_max", "mixed_pg_freezes_min", "progress_requests_min",
    "draining_requests_min", "receiver_sent_rows_with_draining_min",
    "sender_srpt_selections_min", "sender_srpt_non_rr_min",
    "one_rtt_bypass_flows_min", "tail_bypass_flows_min",
)


def _read_json(path: Path) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SummaryError(f"cannot read report input: {path}") from exc
    if not isinstance(value, dict):
        raise SummaryError(f"report input root must be an object: {path}")
    return value


def _run_row(run: Mapping[str, object]) -> Dict[str, object]:
    completion = dict(run["completion"])
    trace = dict(run["grant_closure"])
    audit = dict(run["transition_audit"])
    vector = dict(run["target_vector"])
    refresh = dict(run["refresh_draining"])
    signals = dict(run["feature_signals"])
    return {
        "ordinal": int(run["ordinal"]), "scenario": str(run["scenario"]),
        "seed": int(run["seed"]), "flow_count": int(run["flow_count"]),
        "registered_flows": int(run["registered_flows"]),
        "completed_flows": int(completion["finished"]),
        "grant_frames": int(trace["grant_frames"]),
        "audit_records": int(audit["records"]),
        "mixed_pg_freezes": int(vector["mixed_pg_freezes"]),
        "progress_requests": int(refresh["progress_requests"]),
        "progress_transactions": int(refresh["progress_transactions"]),
        "progress_commits": int(refresh["progress_commits"]),
        "draining_requests": int(refresh["draining_requests"]),
        "draining_completion_releases": int(refresh["completion_releases"]),
        "receiver_sent_rows_with_draining": int(
            trace["receiver_sent_rows_with_draining"]),
        "sender_srpt_selections": int(signals["sender_srpt_selections"]),
        "sender_srpt_non_rr": int(signals["sender_srpt_non_rr"]),
        "one_rtt_bypass_flows": int(signals["one_rtt_bypass_flows"]),
        "tail_bypass_flows": int(signals["tail_bypass_flows"]),
    }


def _scenario_row(name: str, rows: Sequence[Mapping[str, object]]) -> Dict[str, object]:
    if len(rows) != 3 or {int(row["seed"]) for row in rows} != {143, 144, 145}:
        raise SummaryError(f"scenario {name} lacks the three frozen seeds")

    def minimum(field: str) -> int:
        return min(int(row[field]) for row in rows)

    def maximum(field: str) -> int:
        return max(int(row[field]) for row in rows)

    return {
        "scenario": name, "seed_count": len(rows),
        "flow_count_min": minimum("flow_count"),
        "flow_count_max": maximum("flow_count"),
        "registered_flows_min": minimum("registered_flows"),
        "registered_flows_max": maximum("registered_flows"),
        "audit_records_min": minimum("audit_records"),
        "audit_records_max": maximum("audit_records"),
        "mixed_pg_freezes_min": minimum("mixed_pg_freezes"),
        "progress_requests_min": minimum("progress_requests"),
        "draining_requests_min": minimum("draining_requests"),
        "receiver_sent_rows_with_draining_min": minimum(
            "receiver_sent_rows_with_draining"),
        "sender_srpt_selections_min": minimum("sender_srpt_selections"),
        "sender_srpt_non_rr_min": minimum("sender_srpt_non_rr"),
        "one_rtt_bypass_flows_min": minimum("one_rtt_bypass_flows"),
        "tail_bypass_flows_min": minimum("tail_bypass_flows"),
    }


def validate_analysis(spec_path: Path, analysis_path: Path,
                      campaign_dir: Path) -> tuple[List[Dict[str, object]], List[Dict[str, object]], Mapping[str, object]]:
    spec = read_spec(spec_path)
    expected_analysis = campaign_dir.resolve() / "compatibility-admission.json"
    candidate_analysis = _lexical_absolute(Path(str(analysis_path)))
    if candidate_analysis != expected_analysis or expected_analysis.is_symlink():
        raise SummaryError("analysis must be the fixed campaign compatibility admission")
    analysis = _read_json(analysis_path)
    preflight_path = campaign_dir / "preflight.json"
    if preflight_path.is_symlink():
        raise SummaryError("preflight.json must not be a symlink")
    preflight = _read_json(preflight_path)
    try:
        gate = _load_gate(campaign_dir, preflight)
        _replay_path, _replay_analysis, replay_sha = validate_sealed_replay(
            campaign_dir, gate, preflight)
    except CampaignError as exc:
        raise SummaryError(str(exc)) from exc
    if (analysis.get("schema_version") != 14 or analysis.get("scope") != FRESH_SCOPE or
            analysis.get("status") != "admitted" or analysis.get("passed") is not True or
            analysis.get("mechanism_admission_passed") is not True or
            analysis.get("performance_emitted") is not False or
            analysis.get("run_count") != 24 or
            analysis.get("spec_sha256") != sha256_file(spec_path) or
            analysis.get("preflight_sha256") != sha256_file(campaign_dir / "preflight.json") or
            analysis.get("stage_gate_sha256") != sha256_file(
                campaign_dir / "stage-gate.json") or
            analysis.get("simulator_git_sha") != preflight.get("simulator_git_sha") or
            analysis.get("replay_admission_sha256") != replay_sha):
        raise SummaryError("analysis is not the exact admitted mechanism-only matrix")
    recomputed = analyze(spec_path, campaign_dir, FRESH_SCOPE)
    if analysis != recomputed:
        raise SummaryError("supplied analysis differs from fresh fail-closed revalidation")
    runs = list(analysis.get("runs", ()))
    if len(runs) != 24 or any(not isinstance(run, dict) for run in runs):
        raise SummaryError("analysis does not contain 24 run records")
    expected = compatibility_plan(spec)
    expected_ids = [(str(row["scenario"]), int(row["seed"])) for row in expected]
    observed_ids = [(str(row.get("scenario")), int(row.get("seed", -1))) for row in runs]
    if observed_ids != expected_ids or any(run.get("performance_emitted") is not False
                                           for run in runs):
        raise SummaryError("analysis run order/scope changed")
    run_rows = [_run_row(run) for run in runs]
    scenario_rows = [_scenario_row(name, [row for row in run_rows
                                          if row["scenario"] == name])
                     for name in FRESH_SCENARIOS]
    return run_rows, scenario_rows, analysis


def _write_csv(path: Path, fields: Sequence[str], rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def report(spec_path: Path, analysis_path: Path, campaign_dir: Path,
           output_dir: Path) -> Mapping[str, object]:
    run_rows, scenario_rows, analysis = validate_analysis(
        spec_path.resolve(), analysis_path.resolve(), campaign_dir.resolve())
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "mechanism-runs.csv", RUN_FIELDS, run_rows)
    _write_csv(output_dir / "mechanism-scenarios.csv", SCENARIO_FIELDS, scenario_rows)
    admission = {
        "schema_version": 14, "status": "admitted", "passed": True,
        "scope": "candidate_mechanism_compatibility_only",
        "run_count": len(run_rows), "scenario_count": len(scenario_rows),
        "seed_count_per_scenario": 3,
        "simulator_git_sha": analysis["simulator_git_sha"],
        "spec_sha256": sha256_file(spec_path),
        "analysis_sha256": sha256_file(analysis_path),
        "replay_admission_sha256": analysis["replay_admission_sha256"],
        "mechanism_runs_csv_sha256": sha256_file(output_dir / "mechanism-runs.csv"),
        "mechanism_scenarios_csv_sha256": sha256_file(
            output_dir / "mechanism-scenarios.csv"),
        "performance_emitted": False,
        "claim_boundary": (
            "All frozen V14 compatibility mechanisms passed. This report contains "
            "no FCT, queue, HPCC, Homa, or performance-direction result."),
        "scenarios": scenario_rows,
    }
    atomic_json(output_dir / "admission.json", admission)
    return admission


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("analysis", type=Path)
    parser.add_argument("--campaign-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        admission = report(args.spec, args.analysis, args.campaign_dir,
                           args.output_dir)
    except (SummaryError, CampaignError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"reported {admission['run_count']} mechanism-only compatibility runs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
