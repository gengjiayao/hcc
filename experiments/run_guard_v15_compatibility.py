#!/usr/bin/env python3
"""Freeze and serially execute the mechanism-only GUARD V15 campaign."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
from typing import Mapping, Sequence

try:
    from experiments import run_guard_v14_compatibility as base
    from experiments.run_campaign import CampaignError, sha256_file, write_json
except ModuleNotFoundError:
    import run_guard_v14_compatibility as base
    from run_campaign import CampaignError, sha256_file, write_json


SPEC_CANONICAL_SHA256 = "a19545295849fc8156980a0302287899eca4b72e7ef1aae16911430e07211f5a"


def read_spec(path: Path) -> Mapping[str, object]:
    path = path.resolve()
    if path.is_symlink() or sha256_file(path) != SPEC_CANONICAL_SHA256:
        raise CampaignError("complete canonical V15 delta spec changed")
    try:
        delta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CampaignError("cannot read V15 compatibility spec") from exc
    if (not isinstance(delta, dict) or delta.get("schema_version") != 15 or
            delta.get("name") != "guard-v15-ack-clock-compatibility" or
            delta.get("candidate_only") is not True or
            delta.get("performance_metrics_permitted") is not False or
            delta.get("fresh_seeds") != [146, 147, 148]):
        raise CampaignError("V15 identity, seeds, or mechanism-only scope changed")
    repo = Path(__file__).resolve().parents[1]
    base_path = repo / str(delta.get("base_spec", ""))
    if (not base_path.is_file() or sha256_file(base_path) !=
            delta.get("base_spec_sha256")):
        raise CampaignError("V15 detached from the frozen V14 base spec")
    spec = copy.deepcopy(dict(base.read_spec(base_path)))
    spec["schema_version"] = 15
    spec["name"] = str(delta["name"])
    spec["purpose"] = (
        "Candidate-only admission for V15's explicit ACK-clocked timeout "
        "policy; V14 remains rejected.")
    spec["selection_policy"] = str(delta["selection_policy"])
    spec["fresh_seeds"] = list(delta["fresh_seeds"])
    protocol = dict(spec["protocol"])
    protocol["minimum_simulator_commit"] = str(delta["minimum_simulator_commit"])
    protocol["policy"] = "diagnostic_watchdog_with_required_ack_clocked_fallback_v1"
    protocol["required_order"] = (
        "preflight -> four frozen V13 replays -> machine admission and hash "
        "seal -> all 24 fresh V15 runs -> mechanism-only analysis")
    spec["protocol"] = protocol
    probe = copy.deepcopy(dict(spec["simulator_capability_probe"]))
    probe["required_tokens"] = list(probe["required_tokens"]) + [
        "GUARD_TRANSITION_PREFIX_ACK_CLOCK_FALLBACK",
        "guard_transition_prefix_ack_clock_fallback enabled 1",
    ]
    spec["simulator_capability_probe"] = probe
    overrides = dict(delta["profile_overrides"])
    for profile_name in ("final_guard", "v13_replay_profile"):
        profile = dict(spec[profile_name])
        profile.update(dict(overrides[profile_name]))
        spec[profile_name] = profile
        if set(profile) != set(base.profile_keys(spec)):
            raise CampaignError(f"V15 {profile_name} does not freeze every option")
    final = dict(spec["final_guard"])
    replay = dict(spec["v13_replay_profile"])
    if (final["guard_transition_prefix_fail_closed"] != 0 or
            final["guard_transition_prefix_ack_clock_fallback"] != 1 or
            replay["guard_transition_prefix_ack_clock_fallback"] != 0):
        raise CampaignError("V15 timeout profiles changed")
    gates = dict(spec["global_mechanism_gates"])
    for key in ("prefix_timeouts_max", "prefix_degraded_max",
                "fallback_batches_max"):
        gates.pop(key, None)
    gates.update(dict(delta["timeout_policy"]))
    spec["global_mechanism_gates"] = gates
    spec["scope_limits"] = list(delta["scope_limits"])
    return spec


def seal_replay(spec_path: Path, spec: Mapping[str, object], repo: Path,
                campaign_dir: Path, admission_path: Path) -> Mapping[str, object]:
    preflight = base.load_preflight(spec_path, spec, repo, campaign_dir)
    gate = base._load_gate(campaign_dir, preflight)
    if gate.get("replay_admission") is not None or gate.get(
            "compatibility_execution_armed"):
        raise CampaignError("replay is already sealed")
    expected = campaign_dir.resolve() / "replay-admission.json"
    if (base._lexical_absolute(admission_path) != expected or
            expected.is_symlink() or not expected.is_file()):
        raise CampaignError(f"replay admission must be {expected}")
    try:
        from experiments.analyze_guard_v15_compatibility import analyze
    except ModuleNotFoundError:
        from analyze_guard_v15_compatibility import analyze
    supplied = json.loads(expected.read_text(encoding="utf-8"))
    recomputed = analyze(spec_path, campaign_dir, base.REPLAY_SCOPE)
    if supplied != recomputed:
        raise CampaignError("V15 replay admission differs from fresh revalidation")
    if (supplied.get("schema_version") != 15 or
            supplied.get("status") != "admitted" or
            supplied.get("passed") is not True or
            supplied.get("performance_emitted") is not False or
            supplied.get("run_count") != 4 or
            supplied.get("preflight_sha256") != sha256_file(
                campaign_dir / "preflight.json")):
        raise CampaignError("V15 replay analyzer did not admit the frozen replay")
    updated = dict(gate)
    updated["replay_admission"] = {
        "path": str(expected), "sha256": sha256_file(expected),
        "source_stage_gate_sha256": supplied["stage_gate_sha256"],
    }
    updated["compatibility_execution_armed"] = True
    updated["sealed_at"] = base._utc_now()
    write_json(campaign_dir / "stage-gate.json", updated)
    return updated


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--campaign-dir", type=Path, required=True)
    parser.add_argument("--repo", type=Path,
                        default=Path(__file__).resolve().parents[1])
    parser.add_argument("--phase", required=True,
                        choices=("preflight", "replay", "seal-replay",
                                 "compatibility"))
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
            base.preflight(spec_path, spec, repo, campaign_dir)
            print(f"sealed 28 V15 candidate-only identities in {campaign_dir}")
            return 0
        if args.phase == "seal-replay":
            if args.replay_admission is None:
                raise CampaignError("--replay-admission is required")
            seal_replay(spec_path, spec, repo, campaign_dir,
                        args.replay_admission.resolve())
            print("sealed four V13 replay admissions; V15 compatibility armed")
            return 0
        if args.replay_admission is not None:
            raise CampaignError("--replay-admission is valid only for seal-replay")
        return base.execute_scope(spec_path, spec, repo, campaign_dir,
                                  args.phase, args.resume)
    except (CampaignError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
