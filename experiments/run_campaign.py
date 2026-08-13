#!/usr/bin/env python3
"""Expand and run a GUARD experiment campaign with hard resource limits.

The runner deliberately executes one simulation at a time.  Before launching
ns-3 it generates the exact traffic input in a temporary file, checks the flow
count, and only then installs that input under ``config/``.  Every attempted run
gets a self-contained JSON manifest, including content hashes and the simulator
Git revision.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import itertools
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Dict, Iterable, Iterator, List, Mapping, MutableMapping, Sequence, Tuple


DEFAULT_FLOW_LIMIT = 25_000
DEFAULT_RUN_BYTES = 100 * 1024 * 1024
DEFAULT_CAMPAIGN_BYTES = 5 * 1024 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 1_800


class CampaignError(RuntimeError):
    """A campaign definition or run failed a reproducibility check."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except FileNotFoundError:
                pass
    return total


def read_json(path: Path) -> Mapping[str, object]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise CampaignError("campaign root must be a JSON object")
    return value


def canonical_params(params: Mapping[str, object]) -> str:
    return json.dumps(params, sort_keys=True, separators=(",", ":"))


def run_key(stage: str, params: Mapping[str, object]) -> str:
    payload = stage + "\0" + canonical_params(params)
    suffix = hashlib.sha256(payload.encode()).hexdigest()[:12]
    return f"{stage}-{suffix}"


def expand_campaign(campaign: Mapping[str, object]) -> Iterator[Dict[str, object]]:
    defaults = campaign.get("defaults", {})
    stages = campaign.get("stages")
    if not isinstance(defaults, dict) or not isinstance(stages, list):
        raise CampaignError("campaign requires object 'defaults' and list 'stages'")

    seen: Dict[str, str] = {}
    for stage in stages:
        if not isinstance(stage, dict) or not isinstance(stage.get("name"), str):
            raise CampaignError("each stage requires a string name")
        name = stage["name"]
        fixed = stage.get("fixed", {})
        matrix = stage.get("matrix", {})
        metadata = dict(campaign.get("metadata", {}))
        metadata.update(stage.get("metadata", {}))
        if not isinstance(fixed, dict) or not isinstance(matrix, dict):
            raise CampaignError(f"stage {name}: fixed and matrix must be objects")
        keys = list(matrix)
        axes: List[Sequence[object]] = []
        for key in keys:
            values = matrix[key]
            if not isinstance(values, list) or not values:
                raise CampaignError(f"stage {name}: matrix axis {key} must be a non-empty list")
            axes.append(values)
        products: Iterable[Tuple[object, ...]] = itertools.product(*axes) if axes else [()]
        for values in products:
            params: Dict[str, object] = dict(defaults)
            params.update(fixed)
            params.update(dict(zip(keys, values)))
            identity = canonical_params(params)
            if identity in seen:
                # Reusing an identical run is intentional in sensitivity studies.
                continue
            seen[identity] = name
            yield {
                "stage": name,
                "params": params,
                "metadata": metadata,
                "run_key": run_key(name, params),
            }


def git_revision(repo: Path) -> Tuple[str, bool]:
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, text=True,
        stdout=subprocess.PIPE, check=True,
    ).stdout.strip() != ""
    return sha, dirty


def topology_hosts(repo: Path, topology: str) -> Tuple[int, int]:
    path = repo / "config" / f"{topology}.txt"
    try:
        node_count, switch_count, _links = map(int, path.read_text().splitlines()[0].split())
    except (FileNotFoundError, ValueError, IndexError) as exc:
        raise CampaignError(f"cannot parse topology header: {path}") from exc
    suffix = topology.rsplit("OS", 1)
    if len(suffix) != 2:
        raise CampaignError(f"topology name has no OS ratio: {topology}")
    try:
        oversubscription = int(suffix[1])
    except ValueError as exc:
        raise CampaignError(f"invalid topology OS ratio: {topology}") from exc
    return node_count - switch_count, oversubscription


def traffic_identity(repo: Path, params: Mapping[str, object]) -> Dict[str, object]:
    required = ("topo", "cdf", "netload", "simul_time", "bw", "seed")
    missing = [key for key in required if key not in params]
    if missing:
        raise CampaignError(f"traffic parameters missing: {', '.join(missing)}")
    hosts, oversubscription = topology_hosts(repo, str(params["topo"]))
    netload = int(params["netload"])
    if netload % oversubscription:
        raise CampaignError("netload must be divisible by topology oversubscription")
    hostload = netload / oversubscription
    duration = float(params["simul_time"])
    bandwidth = int(params["bw"])
    seed = int(params["seed"])
    cdf = str(params["cdf"])
    stem = (
        f"L_{hostload:.2f}_CDF_{cdf}_N_{hosts}_T_{int(duration * 1000)}ms_"
        f"B_{bandwidth}_S_{seed}_flow"
    )
    return {
        "hosts": hosts,
        "oversubscription": oversubscription,
        "hostload_percent": hostload,
        "duration_seconds": duration,
        "bandwidth_gbps": bandwidth,
        "seed": seed,
        "cdf": cdf,
        "path": repo / "config" / f"{stem}.txt",
    }


def parse_flow_count(path: Path) -> int:
    try:
        with path.open(encoding="utf-8") as stream:
            header = stream.readline().strip()
            count = int(header)
            actual = sum(1 for line in stream if line.strip())
    except (OSError, ValueError) as exc:
        raise CampaignError(f"cannot parse traffic input: {path}") from exc
    if count != actual:
        raise CampaignError(f"traffic header says {count} flows but file contains {actual}: {path}")
    return count


def preflight_traffic(
    repo: Path,
    params: Mapping[str, object],
    flow_limit: int,
    install: bool,
) -> Dict[str, object]:
    identity = traffic_identity(repo, params)
    cdf_path = repo / "traffic_gen" / f"{identity['cdf']}.txt"
    topo_path = repo / "config" / f"{params['topo']}.txt"
    if not cdf_path.is_file():
        raise CampaignError(f"CDF does not exist: {cdf_path}")

    handle, temp_name = tempfile.mkstemp(prefix="guard-flow-", suffix=".txt")
    os.close(handle)
    temp_path = Path(temp_name)
    command = [
        sys.executable,
        "traffic_gen/traffic_gen.py",
        "-c", str(cdf_path),
        "-n", str(identity["hosts"]),
        "-l", str(float(identity["hostload_percent"]) / 100.0),
        "-b", f"{identity['bandwidth_gbps']}G",
        "-t", str(identity["duration_seconds"]),
        "-s", str(identity["seed"]),
        "-o", str(temp_path),
    ]
    try:
        result = subprocess.run(command, cwd=repo, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=120)
        if result.returncode:
            raise CampaignError(f"traffic generator failed: {result.stdout[-2000:]}")
        count = parse_flow_count(temp_path)
        digest = sha256_file(temp_path)
        if count > flow_limit:
            raise CampaignError(f"flow preflight rejected {count} > {flow_limit}")
        final_path = identity["path"]
        existed_before = final_path.exists()
        if existed_before:
            existing_count = parse_flow_count(final_path)
            existing_digest = sha256_file(final_path)
            if (existing_count, existing_digest) != (count, digest):
                raise CampaignError(f"existing traffic input is not reproducible: {final_path}")
        elif install:
            shutil.copy2(temp_path, final_path)
        identity.update({
            "flow_count": count,
            "flow_sha256": digest,
            "cdf_path": str(cdf_path),
            "cdf_sha256": sha256_file(cdf_path),
            "topology_path": str(topo_path),
            "topology_sha256": sha256_file(topo_path),
            "path": str(final_path),
            "created": install and not existed_before,
        })
        return identity
    finally:
        temp_path.unlink(missing_ok=True)


def params_to_command(repo: Path, params: Mapping[str, object]) -> List[str]:
    aliases = {"duration": "simul_time"}
    args = [sys.executable, str(repo / "run.py")]
    for key, value in params.items():
        cli_key = aliases.get(key, key)
        if value is None:
            continue
        args.extend([f"--{cli_key}", str(int(value) if isinstance(value, bool) else value)])
    return args


def terminate_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def output_directories(repo: Path) -> Dict[str, Path]:
    root = repo / "mix" / "output"
    root.mkdir(parents=True, exist_ok=True)
    return {path.name: path for path in root.iterdir() if path.is_dir()}


def execute_limited(
    command: Sequence[str],
    repo: Path,
    log_path: Path,
    baseline_outputs: Mapping[str, Path],
    campaign_dir: Path,
    prior_outputs: Iterable[Path],
    created_flows: Iterable[Path],
    timeout_seconds: int,
    run_byte_limit: int,
    campaign_byte_limit: int,
) -> Tuple[int, str, int]:
    started = time.monotonic()
    reason = "completed"
    with log_path.open("wb") as log:
        process = subprocess.Popen(command, cwd=repo, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        while process.poll() is None:
            elapsed = time.monotonic() - started
            current = output_directories(repo)
            new_outputs = [path for key, path in current.items() if key not in baseline_outputs]
            run_bytes = log_path.stat().st_size + sum(directory_size(path) for path in new_outputs)
            campaign_bytes = directory_size(campaign_dir) + sum(
                path.stat().st_size for path in set(created_flows) if path.exists()
            ) + sum(directory_size(path) for path in prior_outputs) + sum(
                directory_size(path) for path in new_outputs
            )
            if elapsed > timeout_seconds:
                reason = f"timeout>{timeout_seconds}s"
            elif run_bytes > run_byte_limit:
                reason = f"run-storage>{run_byte_limit}"
            elif campaign_bytes > campaign_byte_limit:
                reason = f"campaign-storage>{campaign_byte_limit}"
            if reason != "completed":
                terminate_group(process)
                break
            time.sleep(1)
        returncode = process.wait()
    return returncode, reason, int(time.monotonic() - started)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
    temporary.replace(path)


def completed_flow_count(output_dir: Path, output_id: str) -> int:
    path = output_dir / f"{output_id}_out_fct.txt"
    if not path.exists():
        return 0
    with path.open(encoding="utf-8", errors="replace") as stream:
        return sum(1 for line in stream if line.strip())


def campaign_storage(
    campaign_dir: Path, output_paths: Iterable[Path], created_flows: Iterable[Path]
) -> int:
    return (
        directory_size(campaign_dir)
        + sum(directory_size(path) for path in output_paths)
        + sum(path.stat().st_size for path in set(created_flows) if path.exists())
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign", type=Path)
    parser.add_argument("--repo", type=Path,
                        default=Path(__file__).resolve().parents[1])
    parser.add_argument("--campaign-dir", type=Path)
    parser.add_argument("--dry-run", action="store_true",
                        help="generate temporary traffic only; do not launch ns-3")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--max-runs", type=int)
    parser.add_argument("--flow-limit", type=int, default=DEFAULT_FLOW_LIMIT)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--run-byte-limit", type=int, default=DEFAULT_RUN_BYTES)
    parser.add_argument("--campaign-byte-limit", type=int, default=DEFAULT_CAMPAIGN_BYTES)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    repo = args.repo.resolve()
    campaign_path = args.campaign.resolve()
    campaign = read_json(campaign_path)
    name = str(campaign.get("name", campaign_path.stem))
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    campaign_dir = (args.campaign_dir or repo / "experiments" / "results" /
                    f"{name}-{timestamp}").resolve()
    campaign_dir.mkdir(parents=True, exist_ok=args.resume)
    write_json(campaign_dir / "campaign.json", campaign)
    sha, dirty = git_revision(repo)

    expanded = list(expand_campaign(campaign))
    if args.max_runs is not None:
        expanded = expanded[:args.max_runs]
    print(f"campaign={name} runs={len(expanded)} repo={repo} git={sha[:12]} dirty={dirty}")

    preflight_cache: Dict[str, Dict[str, object]] = {}
    created_flows: List[Path] = []
    campaign_outputs: List[Path] = []
    failures = 0
    for index, spec in enumerate(expanded, start=1):
        key = str(spec["run_key"])
        run_dir = campaign_dir / "runs" / key
        manifest_path = run_dir / "manifest.json"
        if args.resume and manifest_path.exists():
            previous = read_json(manifest_path)
            if previous.get("status") == "completed":
                print(f"[{index}/{len(expanded)}] resume skip {key}")
                output = previous.get("output_dir")
                if output:
                    campaign_outputs.append(Path(str(output)))
                continue

        params = spec["params"]
        traffic_key = canonical_params({
            name: params[name]
            for name in ("topo", "cdf", "netload", "simul_time", "bw", "seed")
        })
        print(f"[{index}/{len(expanded)}] preflight {key}")
        try:
            traffic = preflight_cache.get(traffic_key)
            if traffic is None:
                traffic = preflight_traffic(repo, params, args.flow_limit, not args.dry_run)
                preflight_cache[traffic_key] = traffic
                path = Path(str(traffic["path"]))
                if not args.dry_run and path.exists():
                    created_flows.append(path)
        except CampaignError as exc:
            failures += 1
            write_json(manifest_path, {
                "schema_version": 1, "campaign": name, "stage": spec["stage"],
                "run_key": key, "params": params, "metadata": spec["metadata"],
                "git_sha": sha, "git_dirty": dirty, "status": "preflight_rejected",
                "error": str(exc), "finished_at": utc_now(),
            })
            print(f"  rejected: {exc}", file=sys.stderr)
            continue
        if args.dry_run:
            print(f"  flow_count={traffic['flow_count']} sha256={str(traffic['flow_sha256'])[:12]}")
            continue

        command = params_to_command(repo, params)
        baseline = output_directories(repo)
        run_dir.mkdir(parents=True, exist_ok=True)
        started_at = utc_now()
        returncode, stop_reason, elapsed = execute_limited(
            command, repo, run_dir / "launcher.log", baseline, campaign_dir,
            campaign_outputs, created_flows, args.timeout, args.run_byte_limit,
            args.campaign_byte_limit,
        )
        after = output_directories(repo)
        new_outputs = [path for output_id, path in after.items() if output_id not in baseline]
        output_dir = new_outputs[0] if len(new_outputs) == 1 else None
        output_id = output_dir.name if output_dir else None
        if output_dir:
            campaign_outputs.append(output_dir)
        status = "completed" if returncode == 0 and stop_reason == "completed" and output_dir else "failed"
        if status != "completed":
            failures += 1
        manifest: MutableMapping[str, object] = {
            "schema_version": 1,
            "campaign": name,
            "stage": spec["stage"],
            "run_key": key,
            "params": params,
            "metadata": spec["metadata"],
            "git_sha": sha,
            "git_dirty": dirty,
            "cli": list(command),
            "cli_shell": shlex.join(command),
            "traffic": traffic,
            "started_at": started_at,
            "finished_at": utc_now(),
            "elapsed_seconds": elapsed,
            "returncode": returncode,
            "stop_reason": stop_reason,
            "status": status,
            "output_id": output_id,
            "output_dir": str(output_dir) if output_dir else None,
            "output_bytes": directory_size(output_dir) if output_dir else 0,
            "completed_flow_count": completed_flow_count(output_dir, output_id)
                if output_dir and output_id else 0,
            "launcher_log": str(run_dir / "launcher.log"),
            "launcher_log_sha256": sha256_file(run_dir / "launcher.log"),
            "limits": {
                "flow_count": args.flow_limit,
                "timeout_seconds": args.timeout,
                "run_bytes": args.run_byte_limit,
                "campaign_bytes": args.campaign_byte_limit,
            },
        }
        write_json(manifest_path, manifest)
        total_bytes = campaign_storage(campaign_dir, campaign_outputs, created_flows)
        print(f"  status={status} output={output_id} bytes={manifest['output_bytes']} "
              f"campaign_bytes={total_bytes}")
        if total_bytes > args.campaign_byte_limit:
            print("campaign storage hard limit reached", file=sys.stderr)
            return 2

    if args.dry_run:
        print("dry-run complete; no simulator was launched")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
