#!/usr/bin/env python3
"""Preflight and serially run the frozen stage-1 GUARD parameter grid."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Mapping, Sequence

try:
    from experiments.run_campaign import CampaignError, sha256_file
    from experiments.run_guard_feature_ablation import (
        execute, load_preflight, preflight,
    )
except ModuleNotFoundError:  # Direct execution from experiments/.
    from run_campaign import CampaignError, sha256_file
    from run_guard_feature_ablation import execute, load_preflight, preflight


ETA_VALUES = (0.0, 0.1, 0.2)
RHO_VALUES = (0.5, 0.75, 1.0)
QUANTUM_VALUES = (16, 32, 64, 128)
TAIL_SAFE_RATIOS = (0.8, 0.9, 1.0)
HIGH_LOAD_LAMBDAS = (1.8, 2.0, 2.2, 2.4)
RECEIVER_CONCURRENCY_VALUES = (0, 1)
ELEPHANT_THRESHOLD_BDPS = (4.0, 6.0, 8.0, 12.0)
TAIL_BYPASS_BDPS = (1.0, 2.0, 4.0, 8.0)
ADAPTIVE_QUEUE_BUDGET_BDPS = (0.25, 0.5, 1.0)
ADAPTIVE_TARGET_MAX_BDPS = (4.0, 8.0, 12.0)


def read_spec(path: Path) -> Mapping[str, object]:
    with path.open(encoding="utf-8") as stream:
        spec = json.load(stream)
    if not isinstance(spec, dict) or spec.get("schema_version") != 1:
        raise CampaignError("parameter-search spec must be a schema-version-1 object")
    seeds = list(map(int, spec.get("seeds", [])))
    if len(seeds) != 5 or seeds != list(range(seeds[0], seeds[0] + 5)):
        raise CampaignError("parameter search requires five consecutive seeds")
    limits = dict(spec.get("limits", {}))
    if int(limits.get("max_flows", -1)) != 10_000:
        raise CampaignError("parameter search must retain the 10000-flow cap")
    defaults = dict(spec.get("defaults", {}))
    required_defaults = {
        "topo": "leaf_spine_8_100G_OS2",
        "hosts": 8,
        "oversubscription": 2,
        "simul_time": 0.01,
        "netload": 50,
        "priority_group": 3,
        "monitor_profile": "bulk",
        "guard_remaining_aware": 1,
        "guard_work_conserving": 0,
    }
    for field, expected in required_defaults.items():
        if defaults.get(field) != expected:
            raise CampaignError(f"{field}={defaults.get(field)}, expected {expected}")
    arms = dict(spec.get("arms", {}))
    kind = spec.get("search_kind", "receiver_grid")
    if kind == "receiver_grid":
        if int(defaults.get("guard_srpt_quantum_packets", -1)) != 64:
            raise CampaignError("receiver grid must freeze SRPT quantum at 64 packets")
        observed = set()
        for name, raw in arms.items():
            arm = dict(raw)
            if arm.get("cc") != "guard":
                raise CampaignError(f"{name} must select cc=guard")
            eta = float(arm.get("guard_min_share_fraction", -1))
            rho = float(arm.get("guard_remaining_exponent", -1))
            if eta not in ETA_VALUES or rho not in RHO_VALUES:
                raise CampaignError(f"{name} is outside the frozen eta/rho grid")
            observed.add((eta, rho))
        expected_grid = {(eta, rho) for eta in ETA_VALUES for rho in RHO_VALUES}
        if observed != expected_grid or len(arms) != len(expected_grid):
            raise CampaignError("arms must cover each point of the frozen 3x3 eta/rho grid once")
    elif kind == "srpt_quantum":
        if float(defaults.get("guard_min_share_fraction", -1)) != 0.0:
            raise CampaignError("quantum sweep must retain eta=0")
        if float(defaults.get("guard_remaining_exponent", -1)) != 1.0:
            raise CampaignError("quantum sweep must retain rho=1")
        observed = set()
        for name, raw in arms.items():
            arm = dict(raw)
            if arm.get("cc") != "guard":
                raise CampaignError(f"{name} must select cc=guard")
            quantum = int(arm.get("guard_srpt_quantum_packets", -1))
            if quantum not in QUANTUM_VALUES:
                raise CampaignError(f"{name} is outside the frozen SRPT-quantum grid")
            observed.add(quantum)
        if observed != set(QUANTUM_VALUES) or len(arms) != len(QUANTUM_VALUES):
            raise CampaignError("arms must cover each frozen SRPT quantum once")
    elif kind == "tail_gate":
        observed = set()
        for name, raw in arms.items():
            arm = dict(raw)
            if arm.get("cc") != "guard":
                raise CampaignError(f"{name} must select cc=guard")
            gate = int(arm.get("guard_tail_congestion_gate", -1))
            ratio = float(arm.get("guard_tail_safe_ratio", -1))
            samples = int(arm.get("guard_tail_safe_samples", -1))
            if samples != 2 or gate not in (0, 1):
                raise CampaignError(f"{name} is outside the frozen tail gate")
            if gate == 0:
                if ratio != 0.9:
                    raise CampaignError("ungated baseline must retain ratio 0.9")
                observed.add((0, ratio))
            elif ratio in TAIL_SAFE_RATIOS:
                observed.add((1, ratio))
            else:
                raise CampaignError(f"{name} has an unfrozen safe ratio")
        expected = {(0, 0.9)} | {(1, ratio) for ratio in TAIL_SAFE_RATIOS}
        if observed != expected or len(arms) != len(expected):
            raise CampaignError("arms must cover the frozen tail-gate baseline and ratios")
    elif kind == "lambda_high_load":
        observed = set()
        for name, raw in arms.items():
            arm = dict(raw)
            if arm.get("cc") != "guard":
                raise CampaignError(f"{name} must select cc=guard")
            value = float(arm.get("guard_lambda", -1))
            if value not in HIGH_LOAD_LAMBDAS:
                raise CampaignError(f"{name} has an unfrozen lambda")
            observed.add(value)
        if observed != set(HIGH_LOAD_LAMBDAS) or len(arms) != len(HIGH_LOAD_LAMBDAS):
            raise CampaignError("arms must cover the frozen high-load lambda grid")
    elif kind == "receiver_concurrency":
        observed = set()
        for name, raw in arms.items():
            arm = dict(raw)
            if arm.get("cc") != "guard":
                raise CampaignError(f"{name} must select cc=guard")
            value = int(arm.get("guard_receiver_concurrency", -1))
            if value not in RECEIVER_CONCURRENCY_VALUES:
                raise CampaignError(f"{name} has an unfrozen receiver concurrency")
            observed.add(value)
        if observed != set(RECEIVER_CONCURRENCY_VALUES) or len(arms) != 2:
            raise CampaignError("arms must cover unlimited and one-flow receiver service")
    elif kind == "elephant_concurrency":
        if float(defaults.get("guard_concurrency_min_bdps", -1)) != 8.0:
            raise CampaignError("elephant concurrency must freeze the threshold at 8 BDPs")
        observed = set()
        for name, raw in arms.items():
            arm = dict(raw)
            if arm.get("cc") != "guard":
                raise CampaignError(f"{name} must select cc=guard")
            value = int(arm.get("guard_receiver_concurrency", -1))
            if value not in RECEIVER_CONCURRENCY_VALUES:
                raise CampaignError(f"{name} has an unfrozen elephant concurrency")
            observed.add(value)
        if observed != set(RECEIVER_CONCURRENCY_VALUES) or len(arms) != 2:
            raise CampaignError("arms must cover unlimited and one-elephant service")
    elif kind == "elephant_threshold":
        observed = set()
        baseline_count = 0
        for name, raw in arms.items():
            arm = dict(raw)
            if arm.get("cc") != "guard":
                raise CampaignError(f"{name} must select cc=guard")
            concurrency = int(arm.get("guard_receiver_concurrency", -1))
            threshold = float(arm.get("guard_concurrency_min_bdps", -1))
            if concurrency == 0:
                baseline_count += 1
            elif concurrency == 1 and threshold in ELEPHANT_THRESHOLD_BDPS:
                observed.add(threshold)
            else:
                raise CampaignError(f"{name} is outside the frozen elephant threshold grid")
        if (baseline_count != 1 or observed != set(ELEPHANT_THRESHOLD_BDPS) or
                len(arms) != 1 + len(ELEPHANT_THRESHOLD_BDPS)):
            raise CampaignError("arms must cover the baseline and frozen elephant thresholds")
    elif kind == "tail_bypass_threshold":
        if int(defaults.get("guard_receiver_concurrency", -1)) != 1:
            raise CampaignError("tail threshold search must retain one-elephant service")
        if float(defaults.get("guard_concurrency_min_bdps", -1)) != 12.0:
            raise CampaignError("tail threshold search must retain the selected 12-BDP policy")
        observed = set()
        for name, raw in arms.items():
            arm = dict(raw)
            if arm.get("cc") != "guard":
                raise CampaignError(f"{name} must select cc=guard")
            if int(arm.get("guard_tail_bypass", -1)) != 1:
                raise CampaignError(f"{name} must keep tail bypass enabled")
            threshold = float(arm.get("guard_tail_bypass_bdps", -1))
            if threshold not in TAIL_BYPASS_BDPS:
                raise CampaignError(f"{name} is outside the frozen tail threshold grid")
            observed.add(threshold)
        if observed != set(TAIL_BYPASS_BDPS) or len(arms) != len(TAIL_BYPASS_BDPS):
            raise CampaignError("arms must cover each frozen tail-bypass threshold")
    elif kind == "adaptive_fabric_target":
        if int(defaults.get("guard_receiver_concurrency", -1)) != 1:
            raise CampaignError("adaptive target search must retain one-elephant service")
        if float(defaults.get("guard_concurrency_min_bdps", -1)) != 12.0:
            raise CampaignError("adaptive target search must retain the selected 12-BDP policy")
        if float(defaults.get("guard_target_floor", -1)) != 0.95:
            raise CampaignError("adaptive target search must retain target floor 0.95")
        observed = set()
        disabled = 0
        for name, raw in arms.items():
            arm = dict(raw)
            if arm.get("cc") != "guard":
                raise CampaignError(f"{name} must select cc=guard")
            enabled = int(arm.get("guard_adaptive_fabric_target", -1))
            budget = float(arm.get("guard_queue_budget_bdps", -1))
            if enabled == 0:
                disabled += 1
            elif enabled == 1 and budget in ADAPTIVE_QUEUE_BUDGET_BDPS:
                observed.add(budget)
            else:
                raise CampaignError(f"{name} is outside the frozen adaptive-target grid")
        if (disabled != 1 or observed != set(ADAPTIVE_QUEUE_BUDGET_BDPS) or
                len(arms) != 1 + len(ADAPTIVE_QUEUE_BUDGET_BDPS)):
            raise CampaignError("arms must cover disabled and each adaptive queue budget")
    elif kind == "adaptive_target_scope":
        if int(defaults.get("guard_receiver_concurrency", -1)) != 1:
            raise CampaignError("adaptive scope search must retain one-elephant service")
        if float(defaults.get("guard_concurrency_min_bdps", -1)) != 12.0:
            raise CampaignError("adaptive scope search must retain the selected 12-BDP policy")
        if float(defaults.get("guard_target_floor", -1)) != 0.95:
            raise CampaignError("adaptive scope search must retain target floor 0.95")
        if float(defaults.get("guard_queue_budget_bdps", -1)) != 0.5:
            raise CampaignError("adaptive scope search must retain the 0.5-BDP queue budget")
        observed = set()
        disabled = 0
        for name, raw in arms.items():
            arm = dict(raw)
            if arm.get("cc") != "guard":
                raise CampaignError(f"{name} must select cc=guard")
            enabled = int(arm.get("guard_adaptive_fabric_target", -1))
            max_bdps = float(arm.get("guard_adaptive_target_max_bdps", -1))
            if enabled == 0:
                disabled += 1
            elif enabled == 1 and max_bdps in ADAPTIVE_TARGET_MAX_BDPS:
                observed.add(max_bdps)
            else:
                raise CampaignError(f"{name} is outside the frozen adaptive-scope grid")
        if (disabled != 1 or observed != set(ADAPTIVE_TARGET_MAX_BDPS) or
                len(arms) != 1 + len(ADAPTIVE_TARGET_MAX_BDPS)):
            raise CampaignError("arms must cover disabled and each adaptive flow-size scope")
    else:
        raise CampaignError(
            "search_kind must be receiver_grid, srpt_quantum, tail_gate, "
            "lambda_high_load, receiver_concurrency, elephant_concurrency, "
            "elephant_threshold, tail_bypass_threshold, adaptive_fabric_target, "
            "or adaptive_target_scope")
    selection = dict(spec.get("selection", {}))
    if selection.get("baseline_arm") not in arms:
        raise CampaignError("selection baseline is not a grid arm")
    admission = dict(spec.get("admission", {}))
    if int(admission.get("seed", -1)) != seeds[0]:
        raise CampaignError("admission must use only the first frozen seed")
    workloads = list(spec.get("workloads", []))
    if len(workloads) != 1 or dict(workloads[0]).get("cdf") != "AliStorage2019":
        raise CampaignError("stage 1 must use only the frozen AliStorage workload")
    return spec


def load_admission(campaign_dir: Path) -> Mapping[str, object]:
    path = campaign_dir / "summary" / "admission.json"
    if not path.is_file():
        raise CampaignError("formal phase requires summary/admission.json")
    with path.open(encoding="utf-8") as stream:
        admission = json.load(stream)
    if admission.get("preflight_sha256") != sha256_file(campaign_dir / "preflight.json"):
        raise CampaignError("preflight changed after the admission decision")
    if admission.get("all_arms_passed") is not True:
        raise CampaignError("formal phase is blocked because seed admission failed")
    return admission


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--campaign-dir", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--phase", choices=("preflight", "admission", "formal"), required=True)
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    repo = args.repo.resolve()
    spec_path = args.spec.resolve()
    campaign_dir = args.campaign_dir.resolve()
    spec = read_spec(spec_path)
    if args.phase == "preflight":
        manifest = load_preflight(campaign_dir, spec_path) if args.resume else preflight(
            spec_path, spec, repo, campaign_dir)
        for workload in manifest["workloads"]:
            print(workload["name"], [trace["flow_count"] for trace in workload["traces"]])
        print("preflight complete; ns-3 was not launched")
        return 0
    manifest = load_preflight(campaign_dir, spec_path)
    if args.phase == "formal":
        load_admission(campaign_dir)
    max_runs = len(spec["arms"]) if args.phase == "admission" else None
    return execute(
        spec, manifest, repo, campaign_dir,
        args.resume or args.phase == "formal", None, None, max_runs,
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CampaignError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
