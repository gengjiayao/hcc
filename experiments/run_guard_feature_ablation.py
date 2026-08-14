#!/usr/bin/env python3
"""Freeze and serially run the GUARD endpoint-feature ablation."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import shlex
import sys
from typing import Dict, List, Mapping, MutableMapping, Sequence, Tuple

try:
    from experiments.run_campaign import (
        CampaignError,
        campaign_storage,
        completed_flow_count,
        directory_size,
        execute_limited,
        git_revision,
        output_directories,
        sha256_file,
        write_json,
    )
    from experiments.run_general_workloads import generate_trace
except ModuleNotFoundError:  # Direct execution from experiments/.
    from run_campaign import (
        CampaignError,
        campaign_storage,
        completed_flow_count,
        directory_size,
        execute_limited,
        git_revision,
        output_directories,
        sha256_file,
        write_json,
    )
    from run_general_workloads import generate_trace


LEGACY_ARMS = (
    "guard", "no_priority", "no_srpt", "no_reclaim", "core", "hpcc",
)
OPTIMIZED_ARMS = (
    "guard", "no_priority", "no_srpt", "no_reclaim", "no_one_rtt",
    "no_tail", "no_remaining", "no_ack_coalescing", "no_fixed_window",
)
EXPECTED_ARMS = LEGACY_ARMS
GUARD_OPTIONS = (
    "guard_lambda", "guard_beta", "guard_gamma",
    "guard_selective_registration", "guard_proactive_release",
    "guard_keep_last_hop_int", "guard_size_priority", "guard_sender_srpt",
    "guard_one_rtt_bypass", "guard_tail_bypass", "guard_tail_bypass_bdps",
    "guard_tail_congestion_gate", "guard_tail_safe_ratio", "guard_tail_safe_samples",
    "guard_ack_interval_packets", "guard_fixed_window", "guard_remaining_aware",
    "guard_min_share_fraction", "guard_remaining_exponent",
    "guard_receiver_concurrency",
    "guard_concurrency_min_bdps",
    "guard_grant_refresh_bdps",
    "guard_srpt_quantum_packets", "guard_work_conserving",
    "guard_cap_aware_reclaim", "guard_cap_headroom", "guard_cap_min_share_fraction",
    "guard_rebalance_interval_us", "guard_demand_threshold",
    "guard_receiver_util_threshold",
)


def read_spec(path: Path) -> Mapping[str, object]:
    with path.open(encoding="utf-8") as stream:
        spec = json.load(stream)
    if spec.get("schema_version") != 1:
        raise CampaignError("feature-ablation spec must use schema version 1")
    seeds = list(map(int, spec.get("seeds", [])))
    if len(seeds) != 5 or seeds != list(range(seeds[0], seeds[0] + 5)):
        raise CampaignError("feature ablation requires five consecutive seeds")
    arms = tuple(spec.get("arms", {}).keys())
    if arms not in (LEGACY_ARMS, OPTIMIZED_ARMS):
        raise CampaignError(
            f"feature ablation requires arms {LEGACY_ARMS} or {OPTIMIZED_ARMS}")
    defaults = dict(spec["defaults"])
    if int(defaults.get("priority_group", -1)) != 3:
        raise CampaignError("feature-ablation traces must use PG3")
    if defaults.get("monitor_profile") != "bulk":
        raise CampaignError("feature ablation requires bulk monitoring")
    if arms == LEGACY_ARMS:
        expected_switches = {
            "guard": (1, 1, 1),
            "no_priority": (0, 1, 1),
            "no_srpt": (1, 0, 1),
            "no_reclaim": (1, 1, 0),
            "core": (0, 0, 0),
        }
        for arm, expected in expected_switches.items():
            config = dict(spec["arms"][arm])
            actual = tuple(int(config[field]) for field in (
                "guard_size_priority", "guard_sender_srpt", "guard_work_conserving"
            ))
            if config.get("cc") != "guard" or actual != expected:
                raise CampaignError(f"{arm} does not match its frozen switch tuple")
        if dict(spec["arms"]["hpcc"]).get("cc") != "hpcc":
            raise CampaignError("hpcc arm must select cc=hpcc")
    else:
        frozen = (
            "guard_size_priority", "guard_sender_srpt", "guard_work_conserving",
            "guard_one_rtt_bypass", "guard_tail_bypass",
            "guard_tail_bypass_bdps", "guard_ack_interval_packets",
            "guard_fixed_window", "guard_remaining_aware",
            "guard_min_share_fraction", "guard_remaining_exponent",
            "guard_grant_refresh_bdps",
        )
        expected_override = {
            "guard": None,
            "no_priority": ("guard_size_priority", 0),
            "no_srpt": ("guard_sender_srpt", 0),
            "no_reclaim": ("guard_work_conserving", 0),
            "no_one_rtt": ("guard_one_rtt_bypass", 0),
            "no_tail": ("guard_tail_bypass", 0),
            "no_remaining": ("guard_remaining_aware", 0),
            "no_ack_coalescing": ("guard_ack_interval_packets", 1),
            "no_fixed_window": ("guard_fixed_window", 0),
        }
        baseline = {field: defaults[field] for field in frozen}
        for arm, override in expected_override.items():
            config = dict(defaults)
            config.update(dict(spec["arms"][arm]))
            if config.get("cc") != "guard":
                raise CampaignError(f"{arm} must select cc=guard")
            expected = dict(baseline)
            if override:
                expected[override[0]] = override[1]
            actual = {field: config[field] for field in frozen}
            if actual != expected:
                raise CampaignError(f"{arm} is not a one-factor optimized ablation")
    return spec


def preflight(
    spec_path: Path, spec: Mapping[str, object], repo: Path, campaign_dir: Path,
) -> Mapping[str, object]:
    target = campaign_dir / "preflight.json"
    if target.exists():
        raise CampaignError("preflight already exists; use --resume")
    flow_dir = campaign_dir / "flows"
    flow_dir.mkdir(parents=True, exist_ok=True)
    defaults = dict(spec["defaults"])
    max_flows = int(dict(spec["limits"])["max_flows"])
    rows: List[Dict[str, object]] = []
    for workload in spec["workloads"]:
        workload = dict(workload)
        traces: List[Dict[str, object]] = []
        for seed in spec["seeds"]:
            path = flow_dir / f"{workload['name']}-s{seed}.txt"
            trace = generate_trace(
                repo, str(workload["cdf"]), defaults, int(seed), path,
                int(defaults["priority_group"]),
            )
            if int(trace["flow_count"]) > max_flows:
                raise CampaignError(
                    f"{workload['name']}/s{seed} has {trace['flow_count']} flows; "
                    f"cap is {max_flows}"
                )
            trace["path"] = str(path.resolve())
            traces.append(trace)
        rows.append({
            "name": workload["name"], "cdf": workload["cdf"], "traces": traces,
        })
    sha, dirty = git_revision(repo)
    manifest = {
        "schema_version": 1,
        "campaign": spec["name"],
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "repo": str(repo),
        "git_sha": sha,
        "git_dirty": dirty,
        "spec_path": str(spec_path),
        "spec_sha256": sha256_file(spec_path),
        "workloads": rows,
    }
    write_json(campaign_dir / "campaign.json", spec)
    write_json(target, manifest)
    return manifest


def load_preflight(
    campaign_dir: Path, spec_path: Path,
) -> Mapping[str, object]:
    path = campaign_dir / "preflight.json"
    if not path.is_file():
        raise CampaignError("missing preflight.json")
    with path.open(encoding="utf-8") as stream:
        manifest = json.load(stream)
    if manifest.get("spec_sha256") != sha256_file(spec_path):
        raise CampaignError("campaign spec changed after preflight")
    for workload in manifest["workloads"]:
        for trace in workload["traces"]:
            path = Path(trace["path"])
            if not path.is_file() or sha256_file(path) != trace["sha256"]:
                raise CampaignError(f"frozen trace changed: {path}")
    return manifest


def run_command(
    repo: Path, spec: Mapping[str, object], workload: Mapping[str, object],
    trace: Mapping[str, object], arm_name: str,
) -> List[str]:
    defaults = dict(spec["defaults"])
    arm = dict(spec["arms"][arm_name])
    command = [
        "env", "PYENV_VERSION=2.7.18", sys.executable, str(repo / "run.py"),
        "--cc", str(arm["cc"]),
        "--lb", str(defaults["lb"]),
        "--pfc", str(defaults["pfc"]),
        "--irn", str(defaults["irn"]),
        "--topo", str(defaults["topo"]),
        "--simul_time", str(defaults["simul_time"]),
        "--netload", str(defaults["netload"]),
        "--bw", str(defaults["bw"]),
        "--cdf", str(workload["cdf"]),
        "--seed", str(trace["seed"]),
        "--flow_file", str(trace["path"]),
        "--max_flows", str(dict(spec["limits"])["max_flows"]),
        "--analysis_warmup", str(defaults["analysis_warmup"]),
        "--buffer", str(defaults["buffer"]),
        "--monitor_profile", str(defaults["monitor_profile"]),
    ]
    if arm["cc"] == "guard":
        controls = dict(defaults)
        controls.update(arm)
        for option in GUARD_OPTIONS:
            if option in controls:
                command.extend([f"--{option}", str(controls[option])])
    return command


def plans(
    spec: Mapping[str, object], manifest: Mapping[str, object],
    workload_filter: str | None, arm_filter: str | None,
) -> List[Tuple[Mapping[str, object], Mapping[str, object], str]]:
    rows = []
    for workload in manifest["workloads"]:
        if workload_filter and workload["name"] != workload_filter:
            continue
        for trace in workload["traces"]:
            for arm in spec["arms"]:
                if arm_filter and arm != arm_filter:
                    continue
                rows.append((workload, trace, arm))
    return rows


def existing_outputs(campaign_dir: Path) -> List[Path]:
    outputs = []
    for path in (campaign_dir / "runs").glob("*/*/*/manifest.json"):
        with path.open(encoding="utf-8") as stream:
            row = json.load(stream)
        if row.get("output_dir"):
            outputs.append(Path(row["output_dir"]))
    return outputs


def execute(
    spec: Mapping[str, object], manifest: Mapping[str, object], repo: Path,
    campaign_dir: Path, resume: bool, workload_filter: str | None,
    arm_filter: str | None, max_runs: int | None,
) -> int:
    if git_revision(repo)[1]:
        raise CampaignError("refusing to simulate from a dirty guard worktree")
    selected = plans(spec, manifest, workload_filter, arm_filter)
    if max_runs is not None:
        selected = selected[:max_runs]
    limits = dict(spec["limits"])
    prior_outputs = existing_outputs(campaign_dir)
    failures = 0
    for index, (workload, trace, arm) in enumerate(selected, 1):
        seed = int(trace["seed"])
        run_dir = campaign_dir / "runs" / str(workload["name"]) / f"seed{seed}" / arm
        manifest_path = run_dir / "manifest.json"
        if manifest_path.is_file() and resume:
            with manifest_path.open(encoding="utf-8") as stream:
                previous = json.load(stream)
            if previous.get("status") == "completed":
                print(f"[{index}/{len(selected)}] skip {workload['name']}/s{seed}/{arm}")
                continue
        elif manifest_path.exists():
            raise CampaignError(f"run exists; use --resume: {manifest_path}")
        run_dir.mkdir(parents=True, exist_ok=True)
        command = run_command(repo, spec, workload, trace, arm)
        baseline = output_directories(repo)
        started = dt.datetime.now(dt.timezone.utc).isoformat()
        print(f"[{index}/{len(selected)}] {workload['name']}/s{seed}/{arm}")
        returncode, reason, elapsed = execute_limited(
            command, repo, run_dir / "launcher.log", baseline, campaign_dir,
            prior_outputs, [], int(limits["run_timeout_seconds"]),
            int(limits["run_bytes"]), int(limits["campaign_bytes"]),
        )
        after = output_directories(repo)
        new_outputs = [path for key, path in after.items() if key not in baseline]
        output = new_outputs[0] if len(new_outputs) == 1 else None
        if output:
            prior_outputs.append(output)
        status = "completed" if returncode == 0 and reason == "completed" and output else "failed"
        failures += status != "completed"
        sha, dirty = git_revision(repo)
        row: MutableMapping[str, object] = {
            "schema_version": 1,
            "campaign": spec["name"],
            "workload": workload["name"],
            "cdf": workload["cdf"],
            "arm": arm,
            "seed": seed,
            "git_sha": sha,
            "git_dirty": dirty,
            "started_at": started,
            "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "status": status,
            "returncode": returncode,
            "stop_reason": reason,
            "elapsed_seconds": elapsed,
            "cli": command,
            "cli_shell": shlex.join(command),
            "traffic": trace,
            "output_id": output.name if output else None,
            "output_dir": str(output) if output else None,
            "output_bytes": directory_size(output) if output else 0,
            "completed_flow_count": completed_flow_count(output, output.name)
            if output else 0,
            "launcher_log": str((run_dir / "launcher.log").resolve()),
            "limits": limits,
        }
        write_json(manifest_path, row)
        total = campaign_storage(campaign_dir, prior_outputs, [])
        print(f"  status={status} output={row['output_id']} bytes={row['output_bytes']} total={total}")
        if total > int(limits["campaign_bytes"]):
            raise CampaignError("campaign storage limit exceeded")
    return 1 if failures else 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--campaign-dir", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--phase", choices=("preflight", "run"), required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--workload")
    parser.add_argument("--arm", choices=tuple(dict.fromkeys(LEGACY_ARMS + OPTIMIZED_ARMS)))
    parser.add_argument("--max-runs", type=int)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    repo = args.repo.resolve()
    spec_path = args.spec.resolve()
    campaign_dir = args.campaign_dir.resolve()
    spec = read_spec(spec_path)
    if args.phase == "preflight":
        manifest = load_preflight(campaign_dir, spec_path) if args.resume else preflight(
            spec_path, spec, repo, campaign_dir
        )
        for workload in manifest["workloads"]:
            print(workload["name"], [trace["flow_count"] for trace in workload["traces"]])
        print("preflight complete; ns-3 was not launched")
        return 0
    manifest = load_preflight(campaign_dir, spec_path)
    return execute(
        spec, manifest, repo, campaign_dir, args.resume,
        args.workload, args.arm, args.max_runs,
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CampaignError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
