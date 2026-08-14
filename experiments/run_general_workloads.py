#!/usr/bin/env python3
"""Preflight and serially run the frozen general-workload comparison.

The runner has three deliberately separate phases.  ``preflight`` generates
all five traffic inputs without invoking ns-3.  ``admission`` runs only the
first frozen seed.  ``formal`` refuses to start until the summarizer has
written a passing admission decision, and then runs the remaining four seeds.
Every arm of a seed uses the same persistent flow file through
``run.py --flow_file``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import Dict, Iterable, List, Mapping, MutableMapping, Sequence, Tuple

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


BASE_TIME_SECONDS = 2.0


def read_spec(path: Path) -> Mapping[str, object]:
    with path.open(encoding="utf-8") as stream:
        spec = json.load(stream)
    if not isinstance(spec, dict) or spec.get("schema_version") != 1:
        raise CampaignError("general workload spec must be a schema-version-1 object")
    for field in ("name", "seeds", "limits", "defaults", "arms", "workloads"):
        if field not in spec:
            raise CampaignError(f"general workload spec is missing {field}")
    seeds = list(map(int, spec["seeds"]))
    if len(seeds) != 5 or seeds != list(range(seeds[0], seeds[0] + 5)) or seeds[0] < 1:
        raise CampaignError("formal general workload comparison requires five consecutive seeds")
    arm_names = set(spec["arms"])
    supported = ({"full", "hpcc", "receiver"}, {"guard", "hpcc", "homa"})
    if arm_names not in supported:
        raise CampaignError(
            "formal general workload comparison requires full/hpcc/receiver "
            "or guard/hpcc/homa"
        )
    limits = dict(spec["limits"])
    if int(limits["max_flows"]) != 10_000:
        raise CampaignError("frozen general workload flow cap must be 10000")
    defaults = dict(spec["defaults"])
    if int(defaults["priority_group"]) != 3:
        raise CampaignError("frozen general workload inputs must use PG3")
    if defaults.get("monitor_profile") != "bulk":
        raise CampaignError("formal general workload runs must use bulk monitoring")
    if arm_names == {"full", "hpcc", "receiver"}:
        for arm in ("full", "receiver"):
            if int(dict(spec["arms"])[arm].get("guard_size_priority", -1)) != 0:
                raise CampaignError(f"{arm} must explicitly disable size-priority remapping")
    else:
        guard = dict(dict(spec["arms"])["guard"])
        if guard.get("cc") != "guard" or dict(spec["arms"])["homa"].get("cc") != "homa":
            raise CampaignError("guard/hpcc/homa arm names must map to their matching cc modes")
        controls = dict(defaults)
        controls.update(guard)
        base_fields = (
            "guard_size_priority", "guard_sender_srpt",
            "guard_srpt_quantum_packets", "guard_work_conserving",
        )
        for field in base_fields:
            if field not in controls:
                raise CampaignError(f"guard arm must freeze {field}")
        optimized_fields = (
            "guard_one_rtt_bypass", "guard_tail_bypass",
            "guard_tail_bypass_bdps", "guard_ack_interval_packets",
            "guard_fixed_window", "guard_remaining_aware",
            "guard_min_share_fraction", "guard_remaining_exponent",
            "guard_grant_refresh_bdps",
        )
        present = [field for field in optimized_fields if field in controls]
        if present and len(present) != len(optimized_fields):
            missing = [field for field in optimized_fields if field not in controls]
            raise CampaignError(
                "optimized guard profile must freeze all new controls; missing "
                + ", ".join(missing)
            )
    return spec


def merged_profile(defaults: Mapping[str, object], override: Mapping[str, object] | None) -> Dict[str, object]:
    profile = dict(defaults)
    if override:
        profile.update(override)
    required = ("topo", "hosts", "oversubscription", "simul_time", "netload", "bw")
    missing = [field for field in required if field not in profile]
    if missing:
        raise CampaignError(f"traffic profile is missing {', '.join(missing)}")
    if int(profile["netload"]) % int(profile["oversubscription"]):
        raise CampaignError("netload must be divisible by oversubscription")
    return profile


def inspect_flow_file(path: Path, hosts: int, duration: float, expected_pg: int) -> Dict[str, object]:
    count = 0
    total_bytes = 0
    first_start = None
    last_start = None
    with path.open(encoding="utf-8", errors="strict") as stream:
        try:
            declared = int(stream.readline().strip())
        except ValueError as exc:
            raise CampaignError(f"invalid flow-count header: {path}") from exc
        for line_number, line in enumerate(stream, 2):
            parts = line.split()
            if len(parts) != 5:
                raise CampaignError(f"flow row must have five fields: {path}:{line_number}")
            try:
                src, dst, pg, size = map(int, parts[:4])
                start = float(parts[4])
            except ValueError as exc:
                raise CampaignError(f"non-numeric flow row: {path}:{line_number}") from exc
            if not (0 <= src < hosts and 0 <= dst < hosts and src != dst):
                raise CampaignError(f"invalid endpoints {src}->{dst}: {path}:{line_number}")
            if pg != expected_pg:
                raise CampaignError(
                    f"flow priority is PG{pg}, expected PG{expected_pg}: {path}:{line_number}"
                )
            if size <= 0:
                raise CampaignError(f"non-positive flow size: {path}:{line_number}")
            if not BASE_TIME_SECONDS <= start <= BASE_TIME_SECONDS + duration + 1e-9:
                raise CampaignError(f"flow start outside frozen interval: {path}:{line_number}")
            count += 1
            total_bytes += size
            first_start = start if first_start is None else min(first_start, start)
            last_start = start if last_start is None else max(last_start, start)
    if count != declared:
        raise CampaignError(f"flow header says {declared}, file contains {count}: {path}")
    return {
        "flow_count": count,
        "total_flow_bytes": total_bytes,
        "first_start_s": first_start,
        "last_start_s": last_start,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def generate_trace(
    repo: Path,
    cdf: str,
    profile: Mapping[str, object],
    seed: int,
    output: Path,
    expected_pg: int,
) -> Dict[str, object]:
    cdf_path = repo / "traffic_gen" / f"{cdf}.txt"
    topology_path = repo / "config" / f"{profile['topo']}.txt"
    if not cdf_path.is_file() or not topology_path.is_file():
        raise CampaignError(f"missing CDF or topology for {cdf}/{profile['topo']}")
    host_load = float(profile["netload"]) / float(profile["oversubscription"]) / 100.0
    command = [
        sys.executable,
        str(repo / "traffic_gen" / "traffic_gen.py"),
        "-c", str(cdf_path),
        "-n", str(profile["hosts"]),
        "-l", str(host_load),
        "-b", f"{profile['bw']}G",
        "-t", str(profile["simul_time"]),
        "-s", str(seed),
        "-o", str(output),
    ]
    result = subprocess.run(
        command, cwd=repo, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, timeout=120,
    )
    if result.returncode:
        raise CampaignError(f"traffic generator failed for {cdf}/seed{seed}: {result.stdout[-2000:]}")
    metadata = inspect_flow_file(
        output, int(profile["hosts"]), float(profile["simul_time"]), expected_pg
    )
    metadata.update({"seed": seed, "generator_cli": command})
    return metadata


def profile_passes(traces: Sequence[Mapping[str, object]], flow_limit: int) -> bool:
    return len(traces) == 5 and all(int(trace["flow_count"]) <= flow_limit for trace in traces)


def choose_profile(
    primary: Sequence[Mapping[str, object]],
    fallback: Sequence[Mapping[str, object]] | None,
    flow_limit: int,
) -> Tuple[str, str]:
    if profile_passes(primary, flow_limit):
        return "primary", "all primary traces satisfy the frozen flow cap"
    if fallback is not None and profile_passes(fallback, flow_limit):
        return "fallback", "primary exceeded the flow cap; all frozen fallback traces pass"
    if fallback is not None:
        return "excluded", "primary and the single predeclared fallback exceed the flow cap"
    return "excluded", "at least one primary trace exceeds the flow cap and no fallback is declared"


def preflight(spec_path: Path, spec: Mapping[str, object], repo: Path, campaign_dir: Path) -> Mapping[str, object]:
    if (campaign_dir / "preflight.json").exists():
        raise CampaignError("preflight already exists; use --resume to validate and reuse it")
    campaign_dir.mkdir(parents=True, exist_ok=True)
    flow_dir = campaign_dir / "flows"
    flow_dir.mkdir(parents=True, exist_ok=True)
    defaults = dict(spec["defaults"])
    seeds = list(map(int, spec["seeds"]))
    flow_limit = int(dict(spec["limits"])["max_flows"])
    expected_pg = int(defaults["priority_group"])
    workloads: List[Dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="guard-general-preflight-") as temporary:
        temp_root = Path(temporary)
        for workload in spec["workloads"]:
            workload = dict(workload)
            name, cdf = str(workload["name"]), str(workload["cdf"])
            attempts: Dict[str, Dict[str, object]] = {}
            generated: Dict[str, List[Tuple[Path, Dict[str, object]]]] = {}
            for attempt_name, override in (("primary", None), ("fallback", workload.get("fallback"))):
                if attempt_name == "fallback" and override is None:
                    continue
                profile = merged_profile(defaults, override if isinstance(override, dict) else None)
                attempt_rows: List[Tuple[Path, Dict[str, object]]] = []
                for seed in seeds:
                    path = temp_root / f"{name}-{attempt_name}-s{seed}.txt"
                    row = generate_trace(repo, cdf, profile, seed, path, expected_pg)
                    attempt_rows.append((path, row))
                generated[attempt_name] = attempt_rows
                attempts[attempt_name] = {
                    "profile": {key: profile[key] for key in (
                        "topo", "hosts", "oversubscription", "simul_time", "netload", "bw"
                    )},
                    "traces": [row for _path, row in attempt_rows],
                    "passed_flow_cap": profile_passes(
                        [row for _path, row in attempt_rows], flow_limit
                    ),
                }
            selected, reason = choose_profile(
                [row for _path, row in generated["primary"]],
                [row for _path, row in generated.get("fallback", [])]
                if "fallback" in generated else None,
                flow_limit,
            )
            selected_traces: List[Dict[str, object]] = []
            if selected != "excluded":
                for source, row in generated[selected]:
                    destination = flow_dir / f"{name}-s{row['seed']}.txt"
                    shutil.copy2(source, destination)
                    selected_row = dict(row)
                    selected_row["path"] = str(destination.resolve())
                    selected_traces.append(selected_row)
            workloads.append({
                "name": name,
                "cdf": cdf,
                "decision": "included" if selected != "excluded" else "excluded",
                "selected_profile": selected if selected != "excluded" else None,
                "reason": reason,
                "attempts": attempts,
                "selected_traces": selected_traces,
                "cdf_sha256": sha256_file(repo / "traffic_gen" / f"{cdf}.txt"),
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
        "flow_limit": flow_limit,
        "priority_group": expected_pg,
        "workloads": workloads,
    }
    write_json(campaign_dir / "campaign.json", spec)
    write_json(campaign_dir / "preflight.json", manifest)
    return manifest


def load_preflight(campaign_dir: Path, spec_path: Path) -> Mapping[str, object]:
    path = campaign_dir / "preflight.json"
    if not path.is_file():
        raise CampaignError("missing preflight.json; run the preflight phase first")
    with path.open(encoding="utf-8") as stream:
        manifest = json.load(stream)
    if manifest.get("spec_sha256") != sha256_file(spec_path):
        raise CampaignError("campaign spec changed after preflight")
    for workload in manifest["workloads"]:
        if workload["decision"] != "included":
            continue
        for trace in workload["selected_traces"]:
            path = Path(trace["path"])
            if not path.is_file() or sha256_file(path) != trace["sha256"]:
                raise CampaignError(f"selected flow snapshot changed: {path}")
    return manifest


def selected_workloads(preflight_manifest: Mapping[str, object]) -> Dict[str, Mapping[str, object]]:
    return {
        str(workload["name"]): workload
        for workload in preflight_manifest["workloads"]
        if workload["decision"] == "included"
    }


def run_command(
    repo: Path,
    spec: Mapping[str, object],
    workload: Mapping[str, object],
    trace: Mapping[str, object],
    arm_name: str,
    admission: bool,
) -> List[str]:
    defaults = dict(spec["defaults"])
    profile_name = str(workload["selected_profile"])
    profile = dict(workload["attempts"][profile_name]["profile"])
    arm = dict(spec["arms"][arm_name])
    control = dict(defaults)
    control.update(arm)
    # run.py itself is Python 3, while this ns-3 tree's waf launcher is pinned
    # to Python 2.7.  Export the pin through the driver so its child ./waf sees
    # the same interpreter used for the optimized build.
    command = [
        "env", "PYENV_VERSION=2.7.18", sys.executable, str(repo / "run.py"),
        "--cc", str(arm["cc"]),
        "--lb", str(defaults["lb"]),
        "--pfc", str(defaults["pfc"]),
        "--irn", str(defaults["irn"]),
        "--topo", str(profile["topo"]),
        "--simul_time", str(profile["simul_time"]),
        "--netload", str(profile["netload"]),
        "--bw", str(profile["bw"]),
        "--cdf", str(workload["cdf"]),
        "--seed", str(trace["seed"]),
        "--flow_file", str(trace["path"]),
        "--max_flows", str(dict(spec["limits"])["max_flows"]),
        "--analysis_warmup", str(defaults["analysis_warmup"]),
        "--buffer", str(defaults["buffer"]),
        "--monitor_profile", str(defaults["monitor_profile"]),
    ]
    guard_options = (
        "guard_lambda", "guard_beta", "guard_gamma",
        "guard_selective_registration", "guard_proactive_release",
        "guard_keep_last_hop_int", "guard_size_priority", "guard_sender_srpt",
        "guard_one_rtt_bypass", "guard_tail_bypass", "guard_tail_bypass_bdps",
        "guard_ack_interval_packets", "guard_fixed_window", "guard_remaining_aware",
        "guard_min_share_fraction", "guard_remaining_exponent",
        "guard_grant_refresh_bdps",
        "guard_srpt_quantum_packets", "guard_work_conserving",
        "guard_rebalance_interval_us", "guard_demand_threshold",
        "guard_receiver_util_threshold",
    )
    homa_options = ("homa_overcommit", "homa_resend_timeout_us")
    selected_options: Sequence[str] = ()
    if str(arm["cc"]) in ("guard", "guard-active-only"):
        selected_options = guard_options
    elif str(arm["cc"]) == "homa":
        selected_options = homa_options
    for option in selected_options:
        if option in control:
            command.extend([f"--{option}", str(control[option])])
    trace_arm = str(dict(spec["admission"]).get("controller_trace_arm", "full"))
    if admission and arm_name == trace_arm:
        command.extend([
            "--guard_controller_trace", "1",
            "--guard_controller_max_lines",
            str(dict(spec["admission"])["full_controller_max_lines"]),
        ])
    return command


def planned_runs(
    spec: Mapping[str, object],
    preflight_manifest: Mapping[str, object],
    phase: str,
    admission_decisions: Mapping[str, object] | None = None,
) -> List[Tuple[Mapping[str, object], Mapping[str, object], str]]:
    plans: List[Tuple[Mapping[str, object], Mapping[str, object], str]] = []
    for workload in selected_workloads(preflight_manifest).values():
        if phase == "formal":
            if admission_decisions is None:
                raise CampaignError("formal phase requires admission decisions")
            decision = dict(admission_decisions.get(str(workload["name"]), {}))
            if decision.get("passed") is not True:
                continue
        seeds = list(map(int, spec["seeds"]))
        allowed_seeds = {seeds[0]} if phase == "admission" else set(seeds[1:])
        for trace in workload["selected_traces"]:
            if int(trace["seed"]) not in allowed_seeds:
                continue
            for arm in spec["arms"]:
                plans.append((workload, trace, arm))
    return plans


def load_admission(campaign_dir: Path, preflight_manifest: Mapping[str, object]) -> Mapping[str, object]:
    path = campaign_dir / "summary" / "admission.json"
    if not path.is_file():
        raise CampaignError("formal phase requires summary/admission.json")
    with path.open(encoding="utf-8") as stream:
        admission = json.load(stream)
    if admission.get("preflight_sha256") != sha256_file(campaign_dir / "preflight.json"):
        raise CampaignError("preflight manifest changed after admission")
    decisions = dict(admission.get("workloads", {}))
    selected = set(selected_workloads(preflight_manifest))
    if set(decisions) != selected:
        raise CampaignError("admission decisions do not cover the selected workloads exactly")
    return decisions


def existing_outputs(campaign_dir: Path) -> List[Path]:
    paths: List[Path] = []
    for manifest_path in (campaign_dir / "runs").glob("*/*/*/manifest.json"):
        with manifest_path.open(encoding="utf-8") as stream:
            manifest = json.load(stream)
        if manifest.get("output_dir"):
            paths.append(Path(manifest["output_dir"]))
    return paths


def execute_phase(
    spec: Mapping[str, object],
    preflight_manifest: Mapping[str, object],
    repo: Path,
    campaign_dir: Path,
    phase: str,
    resume: bool,
    workload_filter: str | None,
    max_runs: int | None,
) -> int:
    if git_revision(repo)[1]:
        raise CampaignError("refusing to simulate from a dirty guard worktree")
    admissions = load_admission(campaign_dir, preflight_manifest) if phase == "formal" else None
    plans = planned_runs(spec, preflight_manifest, phase, admissions)
    if workload_filter:
        plans = [plan for plan in plans if plan[0]["name"] == workload_filter]
    if max_runs is not None:
        plans = plans[:max_runs]
    limits = dict(spec["limits"])
    prior_outputs = existing_outputs(campaign_dir)
    failures = 0
    for index, (workload, trace, arm) in enumerate(plans, 1):
        seed = int(trace["seed"])
        run_dir = campaign_dir / "runs" / str(workload["name"]) / f"seed{seed}" / arm
        manifest_path = run_dir / "manifest.json"
        if manifest_path.is_file() and resume:
            with manifest_path.open(encoding="utf-8") as stream:
                previous = json.load(stream)
            if previous.get("status") == "completed":
                print(f"[{index}/{len(plans)}] resume skip {workload['name']}/s{seed}/{arm}")
                continue
        elif manifest_path.exists():
            raise CampaignError(f"run manifest already exists; use --resume: {manifest_path}")
        run_dir.mkdir(parents=True, exist_ok=True)
        command = run_command(repo, spec, workload, trace, arm, phase == "admission")
        baseline = output_directories(repo)
        started = dt.datetime.now(dt.timezone.utc).isoformat()
        print(f"[{index}/{len(plans)}] {workload['name']}/s{seed}/{arm}")
        returncode, stop_reason, elapsed = execute_limited(
            command, repo, run_dir / "launcher.log", baseline, campaign_dir,
            prior_outputs, [],
            int(limits["run_timeout_seconds"]), int(limits["run_bytes"]),
            int(limits["campaign_bytes"]),
        )
        after = output_directories(repo)
        new_outputs = [path for key, path in after.items() if key not in baseline]
        output_dir = new_outputs[0] if len(new_outputs) == 1 else None
        output_id = output_dir.name if output_dir else None
        if output_dir:
            prior_outputs.append(output_dir)
        status = (
            "completed" if returncode == 0 and stop_reason == "completed" and output_dir
            else "failed"
        )
        if status != "completed":
            failures += 1
        sha, dirty = git_revision(repo)
        manifest: MutableMapping[str, object] = {
            "schema_version": 1,
            "campaign": spec["name"],
            "phase": phase,
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
            "stop_reason": stop_reason,
            "elapsed_seconds": elapsed,
            "cli": command,
            "cli_shell": shlex.join(command),
            "traffic": trace,
            "selected_profile": workload["selected_profile"],
            "profile": workload["attempts"][workload["selected_profile"]]["profile"],
            "output_id": output_id,
            "output_dir": str(output_dir) if output_dir else None,
            "output_bytes": directory_size(output_dir) if output_dir else 0,
            "completed_flow_count": completed_flow_count(output_dir, output_id)
            if output_dir and output_id else 0,
            "launcher_log": str((run_dir / "launcher.log").resolve()),
            "launcher_log_sha256": sha256_file(run_dir / "launcher.log"),
            "limits": limits,
        }
        write_json(manifest_path, manifest)
        total = campaign_storage(
            campaign_dir, prior_outputs, [],
        )
        print(f"  status={status} output={output_id} bytes={manifest['output_bytes']} total={total}")
        if total > int(limits["campaign_bytes"]):
            return 2
    return 1 if failures else 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--campaign-dir", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--phase", choices=("preflight", "admission", "formal"), required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--workload")
    parser.add_argument("--max-runs", type=int)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    repo = args.repo.resolve()
    spec_path = args.spec.resolve()
    campaign_dir = args.campaign_dir.resolve()
    spec = read_spec(spec_path)
    if args.phase == "preflight":
        if args.resume:
            manifest = load_preflight(campaign_dir, spec_path)
        else:
            manifest = preflight(spec_path, spec, repo, campaign_dir)
        for workload in manifest["workloads"]:
            counts = {
                name: [trace["flow_count"] for trace in attempt["traces"]]
                for name, attempt in workload["attempts"].items()
            }
            print(f"{workload['name']}: {workload['decision']} counts={counts}")
        print("preflight complete; ns-3 was not launched")
        return 0
    manifest = load_preflight(campaign_dir, spec_path)
    return execute_phase(
        spec, manifest, repo, campaign_dir, args.phase, args.resume,
        args.workload, args.max_runs,
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CampaignError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
