#!/usr/bin/env python3
"""Fail-closed, mechanism-only analysis for the GUARD V15 campaign."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Mapping, Sequence

try:
    from experiments.analyze_guard_v14_compatibility import analyze_loaded
    from experiments.run_campaign import CampaignError
    from experiments.run_guard_v14_compatibility import FRESH_SCOPE, REPLAY_SCOPE
    from experiments.run_guard_v15_compatibility import read_spec
    from experiments.summarize_campaign import SummaryError
    from experiments.summarize_workload import atomic_json
except ModuleNotFoundError:
    from analyze_guard_v14_compatibility import analyze_loaded
    from run_campaign import CampaignError
    from run_guard_v14_compatibility import FRESH_SCOPE, REPLAY_SCOPE
    from run_guard_v15_compatibility import read_spec
    from summarize_campaign import SummaryError
    from summarize_workload import atomic_json


def analyze(spec_path: Path, campaign_dir: Path,
            scope: str) -> Mapping[str, object]:
    spec_path = spec_path.resolve()
    return analyze_loaded(
        spec_path, campaign_dir.resolve(), scope, read_spec(spec_path))


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--campaign-dir", type=Path, required=True)
    parser.add_argument("--scope", choices=(REPLAY_SCOPE, FRESH_SCOPE),
                        required=True)
    parser.add_argument("--json-out", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = analyze(args.spec, args.campaign_dir, args.scope)
        atomic_json(args.json_out, result)
    except (SummaryError, CampaignError, OSError, ValueError, KeyError,
            TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"admitted {result['run_count']} {args.scope} mechanisms; performance sealed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
