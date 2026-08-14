#!/usr/bin/env python3
"""Generate a bounded trace in which both GUARD caps change one target flow."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path


FLOW_BYTES = 4 * 1024 * 1024
BASE_TIME_S = 2.0035
FABRIC_DELAY_S = 100e-6
JITTER_S = 0.5e-6


def build(seed: int):
    rng = random.Random(seed)
    # Phase 1 forms a four-flow receiver fan-in. The target flow 0->8 first
    # receives a 25-Gbps receiver-downlink cap.
    receiver_phase = [(0, 8), (9, 8), (10, 8), (11, 8)]
    # Phase 2 adds seven cross-ToR flows to the same source-leaf uplink. With
    # the target, eight flows then share the fabric link.
    fabric_phase = [(src, src + 8) for src in range(1, 8)]
    rows = []
    for src, dst in receiver_phase:
        rows.append((src, dst, 4, FLOW_BYTES, BASE_TIME_S + rng.random() * JITTER_S))
    for src, dst in fabric_phase:
        rows.append((src, dst, 4, FLOW_BYTES,
                     BASE_TIME_S + FABRIC_DELAY_S + rng.random() * JITTER_S))
    return rows


def write_trace(seed: int, output: Path) -> dict:
    rows = build(seed)
    text = str(len(rows)) + "\n" + "".join(
        f"{src} {dst} {pg} {size} {start:.9f}\n"
        for src, dst, pg, size, start in rows
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    manifest = {
        "schema_version": 1,
        "purpose": "Exercise receiver-grant then shared-fabric pacing changes on flow 0->8.",
        "seed": seed,
        "flow_count": len(rows),
        "flow_bytes": FLOW_BYTES,
        "priority_group": 4,
        "receiver_phase_flows": 4,
        "fabric_phase_flows_including_target": 8,
        "fabric_phase_delay_us": FABRIC_DELAY_S * 1e6,
        "jitter_us": JITTER_S * 1e6,
        "target": {"source": 0, "destination": 8, "file_row": 1},
        "sha256": digest,
    }
    output.with_suffix(output.suffix + ".manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.seed <= 0:
        parser.error("--seed must be positive")
    write_trace(args.seed, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
