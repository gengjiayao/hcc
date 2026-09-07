#!/usr/bin/env python3
"""Freeze and serially execute the mechanism-only GUARD V14 campaign.

This runner generates traffic, seals identities and commands, and enforces
hard process/storage limits.  Performance artifacts are forbidden inputs: the
runner never opens simulator result files and defers all completion and
mechanism validation to the analyzer.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import random
import shlex
import signal
import subprocess
import sys
import time
from typing import Dict, Iterable, List, Mapping, MutableMapping, Sequence, Tuple

try:
    from experiments.run_campaign import (
        CampaignError, campaign_storage, directory_size, git_revision,
        output_directories, sha256_file, write_json,
    )
except ModuleNotFoundError:  # Direct execution from experiments/.
    from run_campaign import (
        CampaignError, campaign_storage, directory_size, git_revision,
        output_directories, sha256_file, write_json,
    )


SPEC_NAME = "guard-v14-mechanism-compatibility"
SPEC_CANONICAL_SHA256 = "d465dfe182c00ff0f3041eaf3b31ec92fd4001ac9a66e8eabd6ce34009aab78c"
REPLAY_SCOPE = "replay"
FRESH_SCOPE = "compatibility"
FRESH_SCENARIOS_SHA256 = "521d51ab143eaceab53e128f9a94fb0607ce44adc6726ca2be65505888d1e187"
GLOBAL_GATES_SHA256 = "2363664575a41e9f81d319e36ee5bd845c6bdc2af0c470694424706910b3933c"
TERMINAL_FIELDS_SHA256 = "880f519926d4688f275c4b7b6c0a5f926c54e493aa825e3e82add630bfdfd51c"
PROFILE_KEYS = (
    "guard_lambda", "guard_beta", "guard_gamma",
    "guard_selective_registration", "guard_proactive_release",
    "guard_keep_last_hop_int", "guard_size_priority", "guard_sender_srpt",
    "guard_one_rtt_bypass", "guard_tail_bypass", "guard_tail_bypass_bdps",
    "guard_tail_congestion_gate", "guard_tail_safe_ratio",
    "guard_tail_safe_samples", "guard_adaptive_fabric_target",
    "guard_target_floor", "guard_queue_budget_bdps",
    "guard_adaptive_target_max_bdps", "guard_ack_interval_packets",
    "guard_fixed_window", "guard_remaining_aware", "guard_min_share_fraction",
    "guard_remaining_exponent", "guard_receiver_concurrency",
    "guard_concurrency_min_bdps", "guard_grant_refresh_bdps",
    "guard_membership_coalesce_ns", "guard_membership_coalesce_max_windows",
    "guard_initial_collection_quiet_ns",
    "guard_initial_collection_full_deadline", "guard_small_set_fastpath_limit",
    "guard_transition_prefix_barrier",
    "guard_transition_prefix_wire_watchdog",
    "guard_transition_prefix_fail_closed", "guard_mixed_pg_vector_fastpath",
    "guard_serialized_progress_refresh", "guard_serialized_draining",
    "guard_grant_reliability_rtts", "guard_srpt_quantum_packets",
    "guard_work_conserving", "guard_cap_aware_reclaim",
    "guard_cap_headroom", "guard_cap_min_share_fraction",
    "guard_rebalance_interval_us", "guard_demand_threshold",
    "guard_receiver_util_threshold",
)
FRESH_SCENARIOS = (
    "same_pg_n4", "same_pg_n15", "mixed_pg_n15",
    "same_sender_multiflow_srpt", "one_rtt_short_background",
    "tail_bypass_overlap", "proactive_drain_interleave",
    "remaining_refresh_dual_receiver_epoch",
)
REPLAY_HASHES = {
    2: "55cb59f42c008a37d0e24555fbdc33f1bec478e2816e2ba3f79eccdde9e971a2",
    4: "87a41f2df3f9a2e8d70498c0db29764ddc7fb53a96f38ae13022d6c627f2949b",
    8: "4d98144057c964d54299c5005883de6e13d01bb3491602b6a0b13d87e4b496f4",
    15: "430550e35b0d5c53c07ac1da5a408a6b31c24b71e257b9cdffcfbc252c67437f",
}


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _canonical_sha(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _lexical_absolute(path: Path) -> Path:
    """Normalize dots without following a possibly hostile final symlink."""
    return Path(os.path.abspath(os.path.normpath(str(path))))


def read_spec(path: Path) -> Mapping[str, object]:
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CampaignError(f"cannot read V14 compatibility spec: {path}") from exc
    if not isinstance(spec, dict) or spec.get("schema_version") != 14:
        raise CampaignError("V14 compatibility spec must be a schema-14 object")
    if _canonical_sha(spec) != SPEC_CANONICAL_SHA256:
        raise CampaignError("complete canonical V14 compatibility spec changed")
    if spec.get("name") != SPEC_NAME:
        raise CampaignError("frozen V14 compatibility name changed")
    protocol = dict(spec.get("protocol", {}))
    if (protocol.get("candidate_only") is not True or
            protocol.get("performance_metrics_permitted") is not False or
            protocol.get("minimum_simulator_commit") !=
                "3d266420608df72f92dddb8901a88e1c1c889ec2"):
        raise CampaignError("V14 protocol scope or minimum simulator changed")
    topology = dict(spec.get("topology", {}))
    expected_topology = {
        "name": "leaf_spine_16_100G_OS4", "hosts": 16,
        "receiver_capacity_bps": 100000000000, "base_time_s": 2.0,
        "simul_time_s": 0.02, "analysis_warmup_s": 0.0,
        "monitor_profile": "bulk", "pfc": 1, "irn": 0, "lb": "fecmp",
        "buffer": 9, "netload": 40, "bw_gbps": 100,
        "cdf": "AliStorage2019",
    }
    if topology != expected_topology:
        raise CampaignError("frozen V14 topology/run envelope changed")
    limits = dict(spec.get("resource_limits", {}))
    if limits != {
            "max_flows": 64, "grant_trace_max_lines": 8192,
            "lifecycle_trace_max_lines": 64,
            "transition_audit_max_records": 10000,
            "artifact_bytes_per_run": 16777216,
            "wall_time_seconds_per_run": 1800,
            "rss_kib_per_run": 8388608, "campaign_bytes": 536870912}:
        raise CampaignError("frozen V14 resource limits changed")
    if list(map(int, spec.get("fresh_seeds", ()))) != [143, 144, 145]:
        raise CampaignError("fresh compatibility seeds must remain 143--145")
    scenarios = list(spec.get("fresh_scenarios", ()))
    if (not all(isinstance(row, dict) for row in scenarios) or
            tuple(row.get("name") for row in scenarios) != FRESH_SCENARIOS):
        raise CampaignError("fresh scenario order or full-bundle overrides changed")
    expected_overrides = {
        name: ({"guard_gamma": 16.0} if name == "proactive_drain_interleave" else {})
        for name in FRESH_SCENARIOS
    }
    if any(row.get("feature_overrides") != expected_overrides[row["name"]]
           for row in scenarios):
        raise CampaignError("fresh scenario feature overrides changed")
    if (_canonical_sha(scenarios) != FRESH_SCENARIOS_SHA256 or
            _canonical_sha(spec.get("global_mechanism_gates")) != GLOBAL_GATES_SHA256 or
            _canonical_sha(spec.get("terminal_zero_fields")) != TERMINAL_FIELDS_SHA256):
        raise CampaignError("frozen V14 scenarios or admission gates changed")
    replay = dict(spec.get("v13_replay", {}))
    identities = list(replay.get("identities", ()))
    if (replay.get("seed") != 126 or
            [int(row.get("active_flows", -1)) for row in identities] != [2, 4, 8, 15] or
            {int(row["active_flows"]): row.get("flow_sha256") for row in identities}
                != REPLAY_HASHES):
        raise CampaignError("frozen V13 replay identities changed")
    for profile_name in ("final_guard", "v13_replay_profile"):
        profile = dict(spec.get(profile_name, {}))
        if set(profile) != set(PROFILE_KEYS):
            raise CampaignError(f"{profile_name} does not explicitly freeze every option")
    final = dict(spec["final_guard"])
    required_final = {
        "guard_proactive_release": 1, "guard_size_priority": 1,
        "guard_sender_srpt": 1, "guard_one_rtt_bypass": 1,
        "guard_tail_bypass": 1, "guard_tail_bypass_bdps": 8.0,
        "guard_fixed_window": 1, "guard_remaining_aware": 1,
        "guard_receiver_concurrency": 0, "guard_grant_refresh_bdps": 1.0,
        "guard_membership_coalesce_ns": 12480,
        "guard_membership_coalesce_max_windows": 5,
        "guard_initial_collection_quiet_ns": 16640,
        "guard_initial_collection_full_deadline": 0,
        "guard_small_set_fastpath_limit": 4,
        "guard_transition_prefix_barrier": 1,
        "guard_transition_prefix_wire_watchdog": 1,
        "guard_transition_prefix_fail_closed": 1,
        "guard_mixed_pg_vector_fastpath": 1,
        "guard_serialized_progress_refresh": 1,
        "guard_serialized_draining": 1,
        "guard_grant_reliability_rtts": 2.0,
        "guard_work_conserving": 0, "guard_cap_aware_reclaim": 0,
    }
    if any(final.get(key) != value for key, value in required_final.items()):
        raise CampaignError("complete fail-closed final GUARD bundle changed")
    replay_profile = dict(spec["v13_replay_profile"])
    if any(replay_profile.get(key) != 0 for key in (
            "guard_proactive_release", "guard_size_priority", "guard_sender_srpt",
            "guard_tail_bypass", "guard_remaining_aware",
            "guard_transition_prefix_fail_closed", "guard_mixed_pg_vector_fastpath",
            "guard_serialized_progress_refresh", "guard_serialized_draining")):
        raise CampaignError("V13 isolation replay profile changed")
    return spec


def _clean_revision(repo: Path, spec: Mapping[str, object]) -> str:
    sha, dirty = git_revision(repo)
    if dirty:
        raise CampaignError("refusing to use a dirty simulator worktree")
    minimum = str(dict(spec["protocol"])["minimum_simulator_commit"])
    if subprocess.run(
            ["git", "merge-base", "--is-ancestor", minimum, sha], cwd=repo,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        raise CampaignError(f"simulator {sha} does not contain V14 base {minimum}")
    probe = dict(spec["simulator_capability_probe"])
    source = repo / str(probe["source"])
    if not source.is_file():
        raise CampaignError(f"missing capability source: {source}")
    raw = source.read_text(encoding="utf-8", errors="strict")
    missing = [str(token) for token in probe["required_tokens"] if str(token) not in raw]
    if missing:
        raise CampaignError("V14 simulator capability absent: " + ", ".join(missing))
    return sha


Flow = Tuple[int, int, int, int, float]


def _jittered(base_s: float, jitter_us: float, rng: random.Random) -> float:
    return base_s + (rng.uniform(0.0, jitter_us) * 1e-6 if jitter_us else 0.0)


def _fresh_flows(spec: Mapping[str, object], scenario: Mapping[str, object],
                 seed: int) -> List[Flow]:
    base = float(dict(spec["topology"])["base_time_s"])
    p = dict(scenario["parameters"])
    rng = random.Random(seed)
    flows: List[Flow] = []

    def at(offset_us: float) -> float:
        return base + offset_us * 1e-6

    generator = str(scenario["generator"])
    if generator == "same_receiver":
        receiver = int(p["receiver"])
        groups = list(map(int, p["priority_groups"]))
        sources = (list(map(int, p["sender_sequence"]))
                   if "sender_sequence" in p else range(int(p["senders"])))
        for source in sources:
            flows.append((source, receiver, groups[source % len(groups)],
                          int(p["flow_bytes"]),
                          _jittered(at(float(p["start_us"])),
                                    float(p["jitter_us"]), rng)))
    elif generator == "mixed_size_same_receiver":
        sources = list(map(int, p["sender_sequence"]))
        sizes = list(map(int, p["sizes_bytes"]))
        if len(sources) != len(sizes):
            raise CampaignError("mixed-PG source/size vectors differ in length")
        for source, size in zip(sources, sizes):
            flows.append((source, int(p["receiver"]),
                          int(p["input_priority_group"]), size,
                          _jittered(at(float(p["start_us"])),
                                    float(p["jitter_us"]), rng)))
    elif generator == "same_sender_multiflow":
        for size in map(int, p["sizes_bytes"]):
            flows.append((int(p["sender"]), int(p["receiver"]),
                          int(p["priority_group"]), size,
                          _jittered(at(float(p["start_us"])),
                                    float(p["jitter_us"]), rng)))
    elif generator == "long_with_short_background":
        receiver = int(p["receiver"])
        for index in range(int(p["long_flows"])):
            flows.append((index, receiver, int(p["priority_group"]),
                          int(p["long_bytes"]),
                          _jittered(at(float(p["long_start_us"])),
                                    float(p["jitter_us"]), rng)))
        for index in range(int(p["short_flows"])):
            source = index % int(p["short_source_modulus"])
            flows.append((source, receiver, int(p["priority_group"]),
                          int(p["short_bytes"]),
                          _jittered(at(float(p["short_start_us"])),
                                    float(p["jitter_us"]), rng)))
    elif generator in ("tail_overlap", "staggered_join"):
        receiver = int(p["receiver"])
        first_sizes = (list(map(int, p["first_sizes_bytes"]))
                       if "first_sizes_bytes" in p else
                       [int(p["first_bytes"])] * int(p["first_flows"]))
        for index, first_size in enumerate(first_sizes):
            flows.append((index, receiver, int(p["priority_group"]),
                          first_size,
                          _jittered(at(float(p["first_start_us"])),
                                    float(p["jitter_us"]), rng)))
        for index in range(int(p["second_flows"])):
            source = ((index + len(first_sizes)) % 8
                      if generator == "staggered_join" else index)
            flows.append((source, receiver, int(p["priority_group"]),
                          int(p["second_bytes"]),
                          _jittered(at(float(p["second_start_us"])),
                                    float(p["jitter_us"]), rng)))
    elif generator == "dual_receiver_two_epoch":
        groups = list(map(int, p["priority_groups"]))
        for receiver_index, receiver in enumerate(map(int, p["receivers"])):
            pg = groups[receiver_index % len(groups)]
            for source in range(int(p["initial_flows_per_receiver"])):
                flows.append((source, receiver, pg, int(p["initial_bytes"]),
                              _jittered(at(float(p["initial_start_us"])),
                                        float(p["jitter_us"]), rng)))
            join_start = int(p["initial_flows_per_receiver"])
            for index in range(int(p["join_flows_per_receiver"])):
                flows.append((join_start + index, receiver, pg,
                              int(p["join_bytes"]),
                              _jittered(at(float(p["join_start_us"])),
                                        float(p["jitter_us"]), rng)))
            for source in range(int(p["second_epoch_flows_per_receiver"])):
                flows.append((source, receiver, pg,
                              int(p["second_epoch_bytes"]),
                              _jittered(at(float(p["second_epoch_start_us"])),
                                        float(p["jitter_us"]), rng)))
    else:
        raise CampaignError(f"unknown V14 scenario generator: {generator}")
    return sorted(flows, key=lambda row: (row[4], row[0], row[1]))


def _replay_flows(spec: Mapping[str, object], count: int) -> List[Flow]:
    replay = dict(spec["v13_replay"])
    base = float(dict(spec["topology"])["base_time_s"])
    rng = random.Random(int(replay["seed"]))
    flows = [
        (source, int(replay["receiver"]), int(replay["priority_group"]),
         int(replay["flow_bytes"]),
         _jittered(base + 0.002, float(replay["arrival_jitter_us"]), rng))
        for source in range(count)
    ]
    return sorted(flows, key=lambda row: (row[4], row[0], row[1]))


def _write_flow_file(path: Path, flows: Sequence[Flow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise CampaignError(f"refusing to overwrite frozen traffic: {path}")
    lines = [str(len(flows))]
    lines.extend(f"{src} {dst} {pg} {size} {start:.9f}"
                 for src, dst, pg, size, start in flows)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _no_fault_trace_bound(spec: Mapping[str, object],
                          scenario: Mapping[str, object], flows: Sequence[Flow]) -> Mapping[str, int]:
    """Conservative no-loss row envelope for the frozen directed topology.

    All registered fresh flows are cross-ToR and therefore use the exact
    104,000-byte BDP.  A logical transaction is budgeted for two full required
    generations (prepare and activate), each with four wire trace events per
    recipient.  A required ACK retired by completion substitutes
    ``ack_retired`` for ``ack_received`` and adds one local
    ``ack_retire_close`` row.  Exact tuple tombstones prevent flow reuse, so at
    most one such extra row is charged per registered flow.  Progress
    crossings, one release per registered flow, and five setup transactions
    per receiver epoch are each charged independently.
    Retry/drop activity is outside this envelope and rejects admission.
    """
    p = dict(scenario["parameters"])
    bdp_bytes = 104000
    registered_sizes = [size for _src, _dst, _pg, size, _start in flows
                        if size > bdp_bytes]
    progress_crossings = sum((size - 1) // bdp_bytes for size in registered_sizes)
    registered = len(registered_sizes)
    max_active = int(p["trace_bound_max_active_registered"])
    receiver_epochs = int(p["trace_bound_receiver_epochs"])
    if not 1 <= max_active <= registered or receiver_epochs <= 0:
        raise CampaignError(f"invalid trace-bound inputs for {scenario['name']}")
    logical_transactions = progress_crossings + registered + 5 * receiver_epochs
    lines = 8 * max_active * logical_transactions + registered
    cap = int(spec["resource_limits"]["grant_trace_max_lines"])
    if lines > 3 * cap // 4:
        raise CampaignError(
            f"{scenario['name']} no-fault trace bound {lines} exceeds 75% of {cap}")
    return {
        "bdp_bytes": bdp_bytes, "registered_flows": registered,
        "progress_crossings": progress_crossings, "max_active_registered": max_active,
        "receiver_epochs": receiver_epochs, "logical_transactions": logical_transactions,
        "retired_ack_local_closures": registered,
        "max_trace_lines": lines, "cap_lines": cap,
    }


def _validate_flow_file(path: Path, spec: Mapping[str, object]) -> int:
    topology = dict(spec["topology"])
    limits = dict(spec["resource_limits"])
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
        declared = int(lines[0])
    except (OSError, ValueError, IndexError) as exc:
        raise CampaignError(f"invalid flow input: {path}") from exc
    if declared != len(lines) - 1 or not 1 <= declared <= int(limits["max_flows"]):
        raise CampaignError(f"flow count is not within the frozen bound: {path}")
    for line_number, line in enumerate(lines[1:], 2):
        parts = line.split()
        if len(parts) != 5:
            raise CampaignError(f"malformed flow row {path}:{line_number}")
        try:
            src, dst, pg, size = map(int, parts[:4])
            start = float(parts[4])
        except ValueError as exc:
            raise CampaignError(f"non-numeric flow row {path}:{line_number}") from exc
        if (not 0 <= src < int(topology["hosts"]) or
                not 0 <= dst < int(topology["hosts"]) or src == dst or
                not 0 <= pg <= 7 or size <= 0 or
                not float(topology["base_time_s"]) <= start <=
                    float(topology["base_time_s"]) + float(topology["simul_time_s"])):
            raise CampaignError(f"out-of-domain flow row {path}:{line_number}")
    return declared


def replay_plan(spec: Mapping[str, object]) -> List[Dict[str, object]]:
    return [{"scope": REPLAY_SCOPE, "seed": 126, "scenario": str(row["name"]),
             "flow_count": int(row["active_flows"]), "profile": "v13_replay_profile"}
            for row in dict(spec["v13_replay"])["identities"]]


def compatibility_plan(spec: Mapping[str, object]) -> List[Dict[str, object]]:
    scenarios = list(spec["fresh_scenarios"])
    return [{"scope": FRESH_SCOPE, "seed": int(seed),
             "scenario": str(scenario["name"]), "flow_count": None,
             "profile": "final_guard"}
            for seed in spec["fresh_seeds"] for scenario in scenarios]


def _scenario(spec: Mapping[str, object], name: str) -> Mapping[str, object]:
    matches = [row for row in spec["fresh_scenarios"] if row["name"] == name]
    if len(matches) != 1:
        raise CampaignError(f"missing unique fresh scenario: {name}")
    return matches[0]


def run_command(repo: Path, spec: Mapping[str, object], identity: Mapping[str, object],
                traffic: Mapping[str, object]) -> List[str]:
    topology = dict(spec["topology"])
    limits = dict(spec["resource_limits"])
    profile = dict(spec[str(identity["profile"])])
    if identity["scope"] == FRESH_SCOPE:
        profile.update(dict(_scenario(spec, str(identity["scenario"]))[
            "feature_overrides"]))
    command = [
        "env", "PYENV_VERSION=2.7.18", sys.executable, str(repo / "run.py"),
        "--smoke",
        "--cc", "guard", "--lb", str(topology["lb"]),
        "--pfc", str(topology["pfc"]), "--irn", str(topology["irn"]),
        "--topo", str(topology["name"]),
        "--simul_time", str(topology["simul_time_s"]),
        "--netload", str(topology["netload"]), "--bw", str(topology["bw_gbps"]),
        "--cdf", str(topology["cdf"]), "--seed", str(identity["seed"]),
        "--flow_file", str(traffic["path"]),
        "--max_flows", str(limits["max_flows"]),
        "--analysis_warmup", str(topology["analysis_warmup_s"]),
        "--buffer", str(topology["buffer"]),
        "--monitor_profile", str(topology["monitor_profile"]),
    ]
    for key in PROFILE_KEYS:
        command.extend([f"--{key}", str(profile[key])])
    command.extend([
        "--guard_lifecycle_trace", "1", "--guard_lifecycle_max_lines",
        str(limits["lifecycle_trace_max_lines"]),
        "--guard_grant_trace", "1", "--guard_grant_max_lines",
        str(limits["grant_trace_max_lines"]),
    ])
    if identity["scope"] == FRESH_SCOPE:
        command.extend([
            "--guard_transition_audit", "1",
            "--guard_transition_audit_max_records",
            str(limits["transition_audit_max_records"]),
        ])
    return command


def _traffic_path(campaign_dir: Path, scope: str, scenario: str, seed: int) -> Path:
    return campaign_dir / "flows" / f"{scope}-{scenario}-s{seed}.txt"


def _find_traffic(preflight: Mapping[str, object], scope: str, scenario: str,
                  seed: int) -> Mapping[str, object]:
    matches = [row for row in preflight["traffic"]
               if row["scope"] == scope and row["scenario"] == scenario and
               int(row["seed"]) == seed]
    if len(matches) != 1:
        raise CampaignError(f"missing unique traffic {scope}/{scenario}/s{seed}")
    return matches[0]


def preflight(spec_path: Path, spec: Mapping[str, object], repo: Path,
              campaign_dir: Path) -> Mapping[str, object]:
    if campaign_dir.exists():
        raise CampaignError("campaign directory already exists")
    sha = _clean_revision(repo, spec)
    topology_path = repo / "config" / f"{spec['topology']['name']}.txt"
    if not topology_path.is_file():
        raise CampaignError(f"missing topology: {topology_path}")
    (campaign_dir / "flows").mkdir(parents=True)
    traffic_rows: List[Dict[str, object]] = []
    full_plan = replay_plan(spec) + compatibility_plan(spec)
    for identity in full_plan:
        scope = str(identity["scope"])
        scenario_name = str(identity["scenario"])
        seed = int(identity["seed"])
        flows = (_replay_flows(spec, int(identity["flow_count"]))
                 if scope == REPLAY_SCOPE else
                 _fresh_flows(spec, _scenario(spec, scenario_name), seed))
        path = _traffic_path(campaign_dir, scope, scenario_name, seed)
        _write_flow_file(path, flows)
        count = _validate_flow_file(path, spec)
        digest = sha256_file(path)
        if scope == REPLAY_SCOPE and digest != REPLAY_HASHES[count]:
            raise CampaignError(
                f"V13 replay N={count} hash changed: {digest} != {REPLAY_HASHES[count]}")
        row: Dict[str, object] = {
            "scope": scope, "scenario": scenario_name, "seed": seed,
            "flow_count": count, "path": str(path.resolve()),
            "sha256": digest, "bytes": path.stat().st_size,
            "performance_metrics_emitted": False,
        }
        if scope == FRESH_SCOPE:
            row["no_fault_grant_trace_bound"] = _no_fault_trace_bound(
                spec, _scenario(spec, scenario_name), flows)
        command = run_command(repo, spec, identity, row)
        row["cli"] = command
        row["cli_sha256"] = _canonical_sha(command)
        traffic_rows.append(row)
        identity["flow_count"] = count
    ordered = []
    for ordinal, identity in enumerate(full_plan, 1):
        ordered.append({"ordinal": ordinal, **identity})
    probe_path = repo / str(spec["simulator_capability_probe"]["source"])
    frozen: Mapping[str, object] = {
        "schema_version": 14, "created_at": _utc_now(),
        "repo": str(repo.resolve()), "simulator_git_sha": sha,
        "git_dirty": False, "spec_path": str(spec_path.resolve()),
        "spec_sha256": sha256_file(spec_path),
        "topology_path": str(topology_path.resolve()),
        "topology_sha256": sha256_file(topology_path),
        "capability_source_sha256": sha256_file(probe_path),
        "traffic": traffic_rows, "run_count": len(ordered),
        "replay_run_count": len(replay_plan(spec)),
        "compatibility_run_count": len(compatibility_plan(spec)),
        "run_order": ordered, "resource_limits": spec["resource_limits"],
        "performance_metrics_permitted": False,
    }
    write_json(campaign_dir / "campaign.json", spec)
    write_json(campaign_dir / "preflight.json", frozen)
    write_json(campaign_dir / "stage-gate.json", {
        "schema_version": 14, "preflight_sha256": sha256_file(
            campaign_dir / "preflight.json"),
        "replay_admission": None, "compatibility_execution_armed": False,
    })
    return frozen


def load_preflight(spec_path: Path, spec: Mapping[str, object], repo: Path,
                   campaign_dir: Path) -> Mapping[str, object]:
    path = campaign_dir / "preflight.json"
    if path.is_symlink() or not path.is_file():
        raise CampaignError("missing preflight.json")
    preflight = json.loads(path.read_text(encoding="utf-8"))
    if (preflight.get("schema_version") != 14 or preflight.get("git_dirty") or
            preflight.get("spec_sha256") != sha256_file(spec_path) or
            preflight.get("performance_metrics_permitted") is not False or
            preflight.get("resource_limits") != spec["resource_limits"]):
        raise CampaignError("preflight schema/spec/scope/limits changed")
    sha = _clean_revision(repo, spec)
    if sha != preflight.get("simulator_git_sha") or str(repo.resolve()) != preflight.get("repo"):
        raise CampaignError("simulator repository or SHA changed after preflight")
    topology_path = Path(str(preflight.get("topology_path", "")))
    probe_path = repo / str(spec["simulator_capability_probe"]["source"])
    if (not topology_path.is_file() or
            sha256_file(topology_path) != preflight.get("topology_sha256") or
            sha256_file(probe_path) != preflight.get("capability_source_sha256")):
        raise CampaignError("frozen topology or capability source changed")
    expected_plan = replay_plan(spec) + compatibility_plan(spec)
    expected_order = [{"ordinal": ordinal, **identity}
                      for ordinal, identity in enumerate(expected_plan, 1)]
    # flow_count is derived at preflight; bind it from the immutable traffic rows.
    for row in expected_order:
        traffic = _find_traffic(preflight, row["scope"], row["scenario"], row["seed"])
        row["flow_count"] = int(traffic["flow_count"])
    if (preflight.get("run_order") != expected_order or
            int(preflight.get("run_count", -1)) != len(expected_order)):
        raise CampaignError("frozen run order changed")
    if len(preflight.get("traffic", ())) != len(expected_order):
        raise CampaignError("frozen traffic set changed")
    for identity in expected_order:
        row = _find_traffic(preflight, identity["scope"], identity["scenario"],
                            int(identity["seed"]))
        flow_path = Path(str(row["path"]))
        if (not flow_path.is_file() or _validate_flow_file(flow_path, spec) !=
                int(row["flow_count"]) or sha256_file(flow_path) != row["sha256"]):
            raise CampaignError(f"frozen traffic changed: {flow_path}")
        if identity["scope"] == FRESH_SCOPE:
            scenario = _scenario(spec, str(identity["scenario"]))
            regenerated = _fresh_flows(spec, scenario, int(identity["seed"]))
            expected_bound = _no_fault_trace_bound(spec, scenario, regenerated)
            if row.get("no_fault_grant_trace_bound") != expected_bound:
                raise CampaignError(
                    f"frozen no-fault trace bound changed: {identity['scenario']}")
        expected_cli = run_command(repo, spec, identity, row)
        if row.get("cli") != expected_cli or row.get("cli_sha256") != _canonical_sha(expected_cli):
            raise CampaignError(f"frozen CLI changed: {identity['scenario']}")
    return preflight


def _load_gate(campaign_dir: Path, preflight: Mapping[str, object]) -> Mapping[str, object]:
    path = campaign_dir / "stage-gate.json"
    if path.is_symlink() or not path.is_file():
        raise CampaignError("missing stage-gate.json")
    gate = json.loads(path.read_text(encoding="utf-8"))
    if (gate.get("schema_version") != 14 or
            gate.get("preflight_sha256") != sha256_file(campaign_dir / "preflight.json")):
        raise CampaignError("stage gate is detached from immutable preflight")
    return gate


def validate_sealed_replay(campaign_dir: Path, gate: Mapping[str, object],
                           preflight: Mapping[str, object]) -> Tuple[Path, Mapping[str, object], str]:
    """Validate the one fixed replay admission before opening or hashing it."""
    admission = gate.get("replay_admission")
    if gate.get("compatibility_execution_armed") is not True or not isinstance(
            admission, dict):
        raise CampaignError("compatibility is blocked until replay admission is sealed")
    expected = campaign_dir.resolve() / "replay-admission.json"
    candidate_raw = admission.get("path")
    if not isinstance(candidate_raw, str) or not candidate_raw:
        raise CampaignError("sealed replay admission path is malformed")
    candidate = _lexical_absolute(Path(candidate_raw))
    if candidate != expected or expected.is_symlink():
        raise CampaignError("sealed replay admission must be the fixed campaign file")
    if not expected.is_file():
        raise CampaignError("sealed replay admission is missing")
    digest = sha256_file(expected)
    if digest != admission.get("sha256"):
        raise CampaignError("sealed replay admission changed")
    try:
        supplied = json.loads(expected.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CampaignError("sealed replay admission is not valid JSON") from exc
    runs = supplied.get("runs") if isinstance(supplied, dict) else None
    if (not isinstance(supplied, dict) or supplied.get("schema_version") != 14 or
            supplied.get("scope") != REPLAY_SCOPE or
            supplied.get("status") != "admitted" or supplied.get("passed") is not True or
            supplied.get("mechanism_admission_passed") is not True or
            supplied.get("performance_emitted") is not False or
            supplied.get("run_count") != 4 or
            supplied.get("spec_sha256") != preflight.get("spec_sha256") or
            supplied.get("preflight_sha256") != sha256_file(
                campaign_dir / "preflight.json") or
            supplied.get("simulator_git_sha") != preflight.get("simulator_git_sha") or
            supplied.get("stage_gate_sha256") != admission.get(
                "source_stage_gate_sha256") or
            not isinstance(runs, list) or len(runs) != 4 or
            any(not isinstance(row, dict) or row.get("scope") != REPLAY_SCOPE or
                row.get("performance_emitted") is not False for row in runs)):
        raise CampaignError("sealed replay admission provenance or result changed")
    return expected, supplied, digest


def _process_group_rss_kib(process_group: int) -> int:
    total_pages = 0
    page_kib = os.sysconf("SC_PAGE_SIZE") // 1024
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw_stat = (entry / "stat").read_text()
            stat = raw_stat[raw_stat.rfind(")") + 2:].split()
            if int(stat[2]) == process_group:
                total_pages += int((entry / "statm").read_text().split()[1])
        except (FileNotFoundError, PermissionError, IndexError, ValueError):
            continue
    return total_pages * page_kib


def _terminate(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def execute_limited(command: Sequence[str], repo: Path, log_path: Path,
                    baseline: Mapping[str, Path], campaign_dir: Path,
                    prior_outputs: Iterable[Path], limits: Mapping[str, object]) -> Tuple[int, str, int, int]:
    started = time.monotonic()
    reason = "completed"
    max_rss = 0
    with log_path.open("wb") as log:
        process = subprocess.Popen(command, cwd=repo, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        while process.poll() is None:
            elapsed = time.monotonic() - started
            current = output_directories(repo)
            new_outputs = [path for key, path in current.items() if key not in baseline]
            run_bytes = log_path.stat().st_size + sum(directory_size(path) for path in new_outputs)
            total_bytes = campaign_storage(campaign_dir, prior_outputs, []) + sum(
                directory_size(path) for path in new_outputs)
            max_rss = max(max_rss, _process_group_rss_kib(process.pid))
            if elapsed > int(limits["wall_time_seconds_per_run"]):
                reason = "wall-time-cap"
            elif max_rss > int(limits["rss_kib_per_run"]):
                reason = "rss-cap"
            elif run_bytes > int(limits["artifact_bytes_per_run"]):
                reason = "run-storage-cap"
            elif total_bytes > int(limits["campaign_bytes"]):
                reason = "campaign-storage-cap"
            if reason != "completed":
                _terminate(process)
                break
            time.sleep(1)
        returncode = process.wait()
    return returncode, reason, int(time.monotonic() - started), max_rss


def _prior_outputs(campaign_dir: Path) -> List[Path]:
    result = []
    for path in (campaign_dir / "runs").rglob("manifest.json"):
        row = json.loads(path.read_text(encoding="utf-8"))
        if row.get("output_dir"):
            result.append(Path(str(row["output_dir"])))
    return result


def execute_scope(spec_path: Path, spec: Mapping[str, object], repo: Path,
                  campaign_dir: Path, scope: str, resume: bool) -> int:
    preflight = load_preflight(spec_path, spec, repo, campaign_dir)
    gate = _load_gate(campaign_dir, preflight)
    if scope == REPLAY_SCOPE:
        if gate.get("replay_admission") is not None or gate.get("compatibility_execution_armed"):
            raise CampaignError("replay cannot run after it is sealed")
        plan = replay_plan(spec)
    elif scope == FRESH_SCOPE:
        validate_sealed_replay(campaign_dir, gate, preflight)
        plan = compatibility_plan(spec)
    else:
        raise CampaignError(f"unknown execution scope: {scope}")
    limits = dict(spec["resource_limits"])
    prior_outputs = _prior_outputs(campaign_dir)
    full_order = list(preflight["run_order"])
    for local_ordinal, identity in enumerate(plan, 1):
        traffic = _find_traffic(preflight, scope, str(identity["scenario"]),
                                int(identity["seed"]))
        identity = dict(identity)
        identity["flow_count"] = int(traffic["flow_count"])
        global_row = [row for row in full_order if all(
            row[key] == identity[key] for key in
            ("scope", "scenario", "seed", "flow_count", "profile"))]
        if len(global_row) != 1:
            raise CampaignError(f"identity is absent from frozen run order: {identity}")
        ordinal = int(global_row[0]["ordinal"])
        run_dir = (campaign_dir / "runs" / scope /
                   f"{ordinal:03d}-{identity['scenario']}-s{identity['seed']}")
        manifest_path = run_dir / "manifest.json"
        if manifest_path.exists():
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
            if resume and previous.get("status") == "completed":
                print(f"[{local_ordinal}/{len(plan)}] skip {identity['scenario']} seed={identity['seed']}")
                continue
            raise CampaignError(f"existing run is not resumable: {manifest_path}")
        run_dir.mkdir(parents=True, exist_ok=False)
        command = run_command(repo, spec, identity, traffic)
        if command != traffic["cli"] or _canonical_sha(command) != traffic["cli_sha256"]:
            raise CampaignError("run command differs from preflight seal")
        if _clean_revision(repo, spec) != preflight["simulator_git_sha"]:
            raise CampaignError("simulator changed during campaign")
        baseline = output_directories(repo)
        started = _utc_now()
        print(f"[{local_ordinal}/{len(plan)}] {identity['scenario']} seed={identity['seed']}")
        returncode, reason, elapsed, max_rss = execute_limited(
            command, repo, run_dir / "launcher.log", baseline, campaign_dir,
            prior_outputs, limits)
        after = output_directories(repo)
        new_outputs = [path for key, path in after.items() if key not in baseline]
        output = new_outputs[0] if len(new_outputs) == 1 else None
        if output is not None:
            prior_outputs.append(output)
        status = ("completed" if returncode == 0 and reason == "completed" and
                  output is not None else "failed")
        manifest: MutableMapping[str, object] = {
            "schema_version": 14, "ordinal": ordinal, "scope": scope,
            "scenario": identity["scenario"], "seed": identity["seed"],
            "profile": identity["profile"], "status": status,
            "returncode": returncode, "stop_reason": reason,
            "started_at": started, "finished_at": _utc_now(),
            "elapsed_seconds": elapsed, "max_rss_kib": max_rss,
            "repo": str(repo.resolve()),
            "simulator_git_sha": preflight["simulator_git_sha"], "git_dirty": False,
            "spec_path": str(spec_path.resolve()), "spec_sha256": sha256_file(spec_path),
            "preflight_sha256": sha256_file(campaign_dir / "preflight.json"),
            "stage_gate_sha256": sha256_file(campaign_dir / "stage-gate.json"),
            "flow_path": traffic["path"], "flow_sha256": traffic["sha256"],
            "flow_count": traffic["flow_count"], "cli": command,
            "cli_sha256": _canonical_sha(command), "cli_shell": shlex.join(command),
            "output_id": output.name if output else None,
            "output_dir": str(output.resolve()) if output else None,
            "output_bytes": directory_size(output) if output else 0,
            "launcher_log": str((run_dir / "launcher.log").resolve()),
            "resource_limits": limits, "completion_validation": "deferred_to_analyzer",
            "performance_metrics_emitted": False,
        }
        write_json(manifest_path, manifest)
        if campaign_storage(campaign_dir, prior_outputs, []) > int(limits["campaign_bytes"]):
            raise CampaignError("campaign storage cap exceeded")
        if status != "completed":
            print(f"run failed: {identity['scenario']} seed={identity['seed']}", file=sys.stderr)
            return 1
    return 0


def seal_replay(spec_path: Path, spec: Mapping[str, object], repo: Path,
                campaign_dir: Path, admission_path: Path) -> Mapping[str, object]:
    preflight = load_preflight(spec_path, spec, repo, campaign_dir)
    gate = _load_gate(campaign_dir, preflight)
    if gate.get("replay_admission") is not None or gate.get("compatibility_execution_armed"):
        raise CampaignError("replay is already sealed")
    expected = campaign_dir.resolve() / "replay-admission.json"
    if (_lexical_absolute(admission_path) != expected or expected.is_symlink() or
            not expected.is_file()):
        raise CampaignError(f"replay admission must be {expected}")
    try:
        from experiments.analyze_guard_v14_compatibility import analyze
    except ModuleNotFoundError:
        from analyze_guard_v14_compatibility import analyze
    supplied = json.loads(expected.read_text(encoding="utf-8"))
    recomputed = analyze(spec_path, campaign_dir, REPLAY_SCOPE)
    if supplied != recomputed:
        raise CampaignError("replay admission differs from fresh mechanism revalidation")
    if (supplied.get("status") != "admitted" or supplied.get("passed") is not True or
            supplied.get("performance_emitted") is not False or
            supplied.get("run_count") != 4 or
            supplied.get("preflight_sha256") != sha256_file(campaign_dir / "preflight.json")):
        raise CampaignError("replay analyzer did not admit this exact frozen replay")
    updated = dict(gate)
    updated["replay_admission"] = {
        "path": str(expected), "sha256": sha256_file(expected),
        "source_stage_gate_sha256": supplied["stage_gate_sha256"],
    }
    updated["compatibility_execution_armed"] = True
    updated["sealed_at"] = _utc_now()
    write_json(campaign_dir / "stage-gate.json", updated)
    return updated


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--campaign-dir", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--phase", required=True,
                        choices=("preflight", "replay", "seal-replay", "compatibility"))
    parser.add_argument("--replay-admission", type=Path)
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    spec_path = args.spec.resolve()
    campaign_dir = args.campaign_dir.resolve()
    repo = args.repo.resolve()
    try:
        spec = read_spec(spec_path)
        if args.phase == "preflight":
            preflight(spec_path, spec, repo, campaign_dir)
            print(f"sealed 28 candidate-only identities in {campaign_dir}")
            return 0
        if args.phase == "seal-replay":
            if args.replay_admission is None:
                raise CampaignError("--replay-admission is required for seal-replay")
            seal_replay(spec_path, spec, repo, campaign_dir,
                        args.replay_admission.resolve())
            print("sealed four V13 replay admissions; compatibility armed")
            return 0
        if args.replay_admission is not None:
            raise CampaignError("--replay-admission is valid only for seal-replay")
        return execute_scope(spec_path, spec, repo, campaign_dir,
                             args.phase, args.resume)
    except (CampaignError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
