#!/usr/bin/env python3
"""Generate and serially run the frozen GUARD/HPCC/Homa collective matrix."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Dict, List, Mapping, MutableMapping, Sequence, Tuple

try:
    from experiments.run_campaign import (
        CampaignError, campaign_storage, completed_flow_count, directory_size,
        execute_limited, git_revision, output_directories, sha256_file, write_json,
    )
except ModuleNotFoundError:
    from run_campaign import (
        CampaignError, campaign_storage, completed_flow_count, directory_size,
        execute_limited, git_revision, output_directories, sha256_file, write_json,
    )


ARMS = ("guard", "hpcc", "homa")
SEEDS = (1, 2, 3, 4, 5)


def read_spec(path: Path) -> Mapping[str, object]:
    spec = json.loads(path.read_text(encoding="utf-8"))
    if spec.get("schema_version") != 1 or tuple(spec.get("seeds", ())) != SEEDS:
        raise CampaignError("collective spec must be schema v1 with seeds 1..5")
    if tuple(spec.get("arms", {}).keys()) != ARMS:
        raise CampaignError("collective arms must be ordered guard, hpcc, homa")
    if set(spec.get("workloads", {})) != {"all_to_all", "ring_open_loop"}:
        raise CampaignError("collective spec must freeze all_to_all and ring_open_loop")
    limits = dict(spec["limits"])
    if int(limits["max_flows"]) != 1000 or int(limits["run_bytes"]) != 100 * 1024 * 1024:
        raise CampaignError("collective safety limits changed")
    return spec


def _flow_count(path: Path) -> int:
    with path.open(encoding="utf-8", errors="strict") as stream:
        try:
            declared = int(stream.readline().strip())
        except ValueError as exc:
            raise CampaignError(f"invalid flow header: {path}") from exc
        observed = sum(1 for line in stream if line.strip())
    if observed != declared:
        raise CampaignError(f"flow header/count mismatch: {path}")
    return observed


def preflight(spec_path: Path, spec: Mapping[str, object], repo: Path, results: Path) -> None:
    preflight_path = results / "preflight.json"
    if preflight_path.exists():
        raise CampaignError("preflight already exists; use the recorded traces")
    flows_dir = results / "flows"
    flows_dir.mkdir(parents=True, exist_ok=True)
    defaults = dict(spec["defaults"])
    records: List[Dict[str, object]] = []
    for workload_name, workload_value in spec["workloads"].items():
        workload = dict(workload_value)
        for seed in SEEDS:
            flow = flows_dir / f"{workload_name}-s{seed}.txt"
            manifest = Path(str(flow) + ".manifest.json")
            command = [
                sys.executable, str(repo / "experiments" / "generate_workload.py"),
                "--workload", str(workload["generator"]),
                "--output", str(flow), "--manifest", str(manifest),
                "--hosts", str(workload["hosts"]),
                "--duration-ms", str(float(defaults["simul_time"]) * 1000),
                "--priority-group", str(defaults["priority_group"]),
                "--max-flows", str(spec["limits"]["max_flows"]),
                "--seed", str(seed),
            ]
            if workload_name == "all_to_all":
                command.extend([
                    "--flow-bytes", str(workload["flow_bytes"]),
                    "--all-to-all-jitter-us", str(workload["jitter_us"]),
                ])
            else:
                command.extend([
                    "--tensor-bytes", str(workload["tensor_bytes"]),
                    "--ring-jitter-us", str(workload["jitter_us"]),
                ])
            completed = subprocess.run(
                command, cwd=repo, text=True, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, timeout=120,
            )
            if completed.returncode:
                raise CampaignError(f"workload generation failed: {completed.stdout[-2000:]}")
            count = _flow_count(flow)
            if count != int(workload["expected_flows"]):
                raise CampaignError(f"{workload_name}/s{seed}: expected {workload['expected_flows']} flows, got {count}")
            manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
            flow_sha = sha256_file(flow)
            if manifest_data["validation"]["sha256"] != flow_sha:
                raise CampaignError(f"manifest hash mismatch: {flow}")
            records.append({
                "workload": workload_name, "seed": seed,
                "flow_file": str(flow.resolve()), "manifest": str(manifest.resolve()),
                "flow_sha256": flow_sha, "flow_count": count,
                "flow_bytes_on_disk": flow.stat().st_size,
                "manifest_sha256": sha256_file(manifest), "generator_cli": command,
            })
    for workload_name in spec["workloads"]:
        hashes = {row["flow_sha256"] for row in records if row["workload"] == workload_name}
        if len(hashes) != 5:
            raise CampaignError(f"{workload_name}: five seeds must yield five distinct traces")
    sha, dirty = git_revision(repo)
    if dirty:
        raise CampaignError("refusing to freeze provenance from a dirty guard worktree")
    write_json(results / "campaign.json", spec)
    write_json(preflight_path, {
        "schema_version": 1, "campaign": spec["name"],
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "git_sha": sha, "git_dirty": dirty,
        "spec_path": str(spec_path.resolve()), "spec_sha256": sha256_file(spec_path),
        "traces": records,
    })
    print(f"froze {len(records)} traces in {results}")


def load_preflight(spec_path: Path, results: Path) -> Mapping[str, object]:
    path = results / "preflight.json"
    if not path.is_file():
        raise CampaignError("missing preflight.json")
    preflight_data = json.loads(path.read_text(encoding="utf-8"))
    if preflight_data.get("spec_sha256") != sha256_file(spec_path):
        raise CampaignError("collective spec changed after preflight")
    for trace in preflight_data["traces"]:
        flow, manifest = Path(trace["flow_file"]), Path(trace["manifest"])
        if sha256_file(flow) != trace["flow_sha256"] or sha256_file(manifest) != trace["manifest_sha256"]:
            raise CampaignError(f"frozen trace changed: {flow}")
    return preflight_data


def run_command(
    repo: Path, spec: Mapping[str, object], trace: Mapping[str, object], arm_name: str,
) -> List[str]:
    defaults = dict(spec["defaults"])
    arm = dict(spec["arms"][arm_name])
    control = dict(defaults)
    control.update(arm)
    command = [
        "env", "PYENV_VERSION=2.7.18", sys.executable, str(repo / "run.py"),
        "--cc", str(arm["cc"]), "--lb", str(defaults["lb"]),
        "--pfc", str(defaults["pfc"]), "--irn", str(defaults["irn"]),
        "--topo", str(defaults["topo"]), "--simul_time", str(defaults["simul_time"]),
        "--netload", str(defaults["netload"]), "--bw", str(defaults["bw"]),
        "--cdf", "AliStorage2019", "--seed", str(trace["seed"]),
        "--flow_file", str(trace["flow_file"]),
        "--max_flows", str(spec["limits"]["max_flows"]),
        "--analysis_warmup", str(defaults["analysis_warmup"]),
        "--buffer", str(defaults["buffer"]),
        "--monitor_profile", str(defaults["monitor_profile"]),
        "--qlen_monitoring_interval", "1000",
        "--guard_lifecycle_trace", "0", "--guard_controller_trace", "0",
    ]
    guard_options = (
        "guard_lambda", "guard_beta", "guard_gamma", "guard_selective_registration",
        "guard_proactive_release", "guard_keep_last_hop_int", "guard_size_priority",
        "guard_sender_srpt", "guard_srpt_quantum_packets", "guard_work_conserving",
        "guard_rebalance_interval_us", "guard_demand_threshold",
        "guard_receiver_util_threshold",
    )
    homa_options = ("homa_overcommit", "homa_resend_timeout_us")
    options: Sequence[str] = ()
    if arm_name == "guard":
        options = guard_options
    elif arm_name == "homa":
        options = homa_options
    for option in options:
        if option in control:
            command.extend([f"--{option}", str(control[option])])
    return command


def traces_for_phase(preflight_data: Mapping[str, object], phase: str) -> List[Mapping[str, object]]:
    seeds = {1} if phase == "admission" else {2, 3, 4, 5}
    return [row for row in preflight_data["traces"] if int(row["seed"]) in seeds]


def require_admission(results: Path) -> None:
    path = results / "analysis" / "admission-seed1.json"
    if not path.is_file():
        raise CampaignError("formal phase requires analysis/admission-seed1.json")
    admission = json.loads(path.read_text(encoding="utf-8"))
    if admission.get("all_passed") is not True:
        raise CampaignError("seed-1 admission did not pass")
    if admission.get("preflight_sha256") != sha256_file(results / "preflight.json"):
        raise CampaignError("seed-1 admission is not bound to this preflight")


def existing_outputs(results: Path) -> List[Path]:
    output: List[Path] = []
    for path in (results / "runs").glob("*/*/*/manifest.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("output_dir"):
            output.append(Path(data["output_dir"]))
    return output


def execute_phase(
    spec: Mapping[str, object], preflight_data: Mapping[str, object], repo: Path,
    results: Path, phase: str, resume: bool,
) -> int:
    sha, dirty = git_revision(repo)
    if dirty:
        raise CampaignError("refusing to simulate from a dirty guard worktree")
    if phase == "formal":
        require_admission(results)
    limits = dict(spec["limits"])
    prior_outputs = existing_outputs(results)
    plans: List[Tuple[Mapping[str, object], str]] = []
    for trace in traces_for_phase(preflight_data, phase):
        for arm in ARMS:
            plans.append((trace, arm))
    failures = 0
    for index, (trace, arm) in enumerate(plans, 1):
        workload, seed = str(trace["workload"]), int(trace["seed"])
        run_dir = results / "runs" / workload / f"seed{seed}" / arm
        manifest_path = run_dir / "manifest.json"
        if manifest_path.exists() and resume:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
            if previous.get("status") == "completed":
                print(f"[{index}/{len(plans)}] resume skip {workload}/s{seed}/{arm}")
                continue
        elif manifest_path.exists():
            raise CampaignError(f"run manifest exists; pass --resume: {manifest_path}")
        run_dir.mkdir(parents=True, exist_ok=True)
        command = run_command(repo, spec, trace, arm)
        baseline = output_directories(repo)
        print(f"[{index}/{len(plans)}] {workload}/s{seed}/{arm}", flush=True)
        started = dt.datetime.now(dt.timezone.utc).isoformat()
        returncode, stop_reason, elapsed = execute_limited(
            command, repo, run_dir / "launcher.log", baseline, results,
            prior_outputs, [], int(limits["run_timeout_seconds"]),
            int(limits["run_bytes"]), int(limits["campaign_bytes"]),
        )
        after = output_directories(repo)
        new_outputs = [path for key, path in after.items() if key not in baseline]
        output_dir = new_outputs[0] if len(new_outputs) == 1 else None
        output_id = output_dir.name if output_dir else None
        if output_dir:
            prior_outputs.append(output_dir)
        status = "completed" if returncode == 0 and stop_reason == "completed" and output_dir else "failed"
        summary_json = run_dir / "summary.json"
        summary_csv = run_dir / "summary.csv"
        summary_error = ""
        if status == "completed":
            summary_command = [
                sys.executable, str(repo / "experiments" / "summarize_workload.py"),
                str(output_dir), "--manifest", str(trace["manifest"]),
                "--json-out", str(summary_json), "--csv-out", str(summary_csv),
            ]
            summary = subprocess.run(
                summary_command, cwd=repo, text=True, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, timeout=120,
            )
            if summary.returncode:
                status, summary_error = "failed", summary.stdout[-4000:]
        if status != "completed":
            failures += 1
        record: MutableMapping[str, object] = {
            "schema_version": 1, "campaign": spec["name"], "phase": phase,
            "workload": workload, "arm": arm, "seed": seed,
            "git_sha": sha, "git_dirty": dirty, "started_at": started,
            "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "status": status, "returncode": returncode, "stop_reason": stop_reason,
            "elapsed_seconds": elapsed, "cli": command, "cli_shell": shlex.join(command),
            "traffic": trace, "output_id": output_id,
            "output_dir": str(output_dir) if output_dir else None,
            "output_bytes": directory_size(output_dir) if output_dir else 0,
            "completed_flow_count": completed_flow_count(output_dir, output_id)
            if output_dir and output_id else 0,
            "summary_json": str(summary_json.resolve()) if summary_json.exists() else None,
            "summary_csv": str(summary_csv.resolve()) if summary_csv.exists() else None,
            "summary_error": summary_error,
            "launcher_log": str((run_dir / "launcher.log").resolve()), "limits": limits,
        }
        write_json(manifest_path, record)
        total = campaign_storage(results, prior_outputs, [])
        print(f"  status={status} output={output_id} bytes={record['output_bytes']} total={total}")
        if total > int(limits["campaign_bytes"]):
            raise CampaignError("campaign storage limit exceeded")
    return 1 if failures else 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("preflight", "admission", "formal"))
    parser.add_argument("--spec", type=Path, default=Path("experiments/campaigns/guard_homa_collectives.json"))
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    repo = Path(__file__).resolve().parents[1]
    spec_path, results = args.spec.resolve(), args.results.resolve()
    spec = read_spec(spec_path)
    if args.phase == "preflight":
        preflight(spec_path, spec, repo, results)
        return 0
    preflight_data = load_preflight(spec_path, results)
    return execute_phase(spec, preflight_data, repo, results, args.phase, args.resume)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CampaignError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(2)
