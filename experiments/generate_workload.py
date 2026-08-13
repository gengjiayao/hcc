#!/usr/bin/env python3
"""Generate bounded, deterministic reviewer workloads for the ns-3 driver."""

import argparse
import hashlib
import json
import math
import os
import random
import sys
from collections import namedtuple


BASE_TIME_S = 2.0
MIN_DURATION_S = 0.010
HARD_MAX_FLOWS = 25000
Flow = namedtuple("Flow", "src dst pg size_bytes start_s")


def positive_int(value):
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def make_incast(hosts, destination, flow_bytes, pg, start_s, duration_s):
    trigger_s = start_s + duration_s * 0.35
    senders = [host for host in range(hosts) if host != destination]
    return [
        Flow(src, destination, pg, flow_bytes, trigger_s + index * 1e-9)
        for index, src in enumerate(senders)
    ]


def make_background(hosts, count, flow_bytes, pg, start_s, duration_s, seed):
    rng = random.Random(seed)
    flows = []
    for index in range(count):
        src = rng.randrange(hosts)
        dst = rng.randrange(hosts - 1)
        if dst >= src:
            dst += 1
        # Even spacing keeps offered traffic reproducible; the seed controls
        # only endpoint selection.
        fraction = (index + 1) / float(count + 1)
        flow_start = start_s + duration_s * (0.05 + 0.90 * fraction)
        flows.append(Flow(src, dst, pg, flow_bytes, flow_start))
    return flows


def make_ring_allreduce(hosts, tensor_bytes, pg, start_s, duration_s):
    # A ring executes N-1 reduce-scatter steps followed by N-1 all-gather
    # steps.  Each host sends one tensor chunk to its clockwise neighbor per
    # step.  Dependencies are represented by distinct, ordered start times.
    steps = 2 * (hosts - 1)
    chunk_bytes = int(math.ceil(tensor_bytes / float(hosts)))
    flows = []
    for step in range(steps):
        flow_start = start_s + duration_s * (step + 1) / float(steps + 1)
        for src in range(hosts):
            flows.append(Flow(src, (src + 1) % hosts, pg, chunk_bytes, flow_start))
    return flows


def make_all_to_all(hosts, flow_bytes, pg, start_s, duration_s):
    flows = []
    pair_count = hosts * (hosts - 1)
    pair_index = 0
    for src in range(hosts):
        for dst in range(hosts):
            if src == dst:
                continue
            fraction = (pair_index + 1) / float(pair_count + 1)
            flow_start = start_s + duration_s * (0.20 + 0.60 * fraction)
            flows.append(Flow(src, dst, pg, flow_bytes, flow_start))
            pair_index += 1
    return flows


def generate(args):
    if args.hosts < 2:
        raise ValueError("--hosts must be at least 2")
    if not math.isfinite(args.duration_ms) or args.duration_ms < MIN_DURATION_S * 1000:
        raise ValueError("--duration-ms must be at least 10")
    if not math.isfinite(args.base_time) or args.base_time < 0:
        raise ValueError("--base-time must be finite and non-negative")
    if not 0 <= args.priority_group <= 7:
        raise ValueError("--priority-group must be in [0, 7]")
    if not 0 <= args.incast_destination < args.hosts:
        raise ValueError("--incast-destination must name an existing host")
    if not 1 <= args.max_flows <= HARD_MAX_FLOWS:
        raise ValueError("--max-flows must be in [1, {}]".format(HARD_MAX_FLOWS))

    duration_s = args.duration_ms / 1000.0
    if args.workload == "incast":
        flows = make_incast(
            args.hosts, args.incast_destination, args.flow_bytes,
            args.priority_group, args.base_time, duration_s)
    elif args.workload == "hybrid":
        flows = make_incast(
            args.hosts, args.incast_destination, args.flow_bytes,
            args.priority_group, args.base_time, duration_s)
        flows += make_background(
            args.hosts, args.background_flows, args.background_flow_bytes,
            args.priority_group, args.base_time, duration_s, args.seed)
    elif args.workload == "ring-allreduce":
        flows = make_ring_allreduce(
            args.hosts, args.tensor_bytes, args.priority_group,
            args.base_time, duration_s)
    elif args.workload == "all-to-all":
        flows = make_all_to_all(
            args.hosts, args.flow_bytes, args.priority_group,
            args.base_time, duration_s)
    else:
        raise ValueError("unknown workload: {}".format(args.workload))

    flows.sort(key=lambda flow: (flow.start_s, flow.src, flow.dst))
    if len(flows) > args.max_flows:
        raise ValueError(
            "workload has {} flows, exceeding --max-flows {}".format(
                len(flows), args.max_flows))
    return flows


def write_flow_file(path, flows):
    temporary = "{}.tmp.{}".format(path, os.getpid())
    with open(temporary, "w", encoding="utf-8") as output:
        output.write("{}\n".format(len(flows)))
        for flow in flows:
            output.write("{} {} {} {} {:.9f}\n".format(*flow))
    os.replace(temporary, path)


def validate_flow_file(path, hosts, start_s, stop_s, max_flows):
    total_bytes = 0
    first_start = None
    last_start = None
    with open(path, "r", encoding="utf-8") as source:
        header = source.readline().strip()
        try:
            declared_count = int(header)
        except ValueError as error:
            raise ValueError("invalid flow-count header: {!r}".format(header)) from error
        if not 0 <= declared_count <= max_flows:
            raise ValueError("declared flow count is outside [0, {}]".format(max_flows))

        observed_count = 0
        for line_number, line in enumerate(source, start=2):
            fields = line.split()
            if len(fields) != 5:
                raise ValueError("line {} must contain five fields".format(line_number))
            src, dst, pg, size_bytes = map(int, fields[:4])
            flow_start = float(fields[4])
            if not 0 <= src < hosts or not 0 <= dst < hosts or src == dst:
                raise ValueError("line {} has invalid endpoints {} -> {}".format(
                    line_number, src, dst))
            if pg < 0 or size_bytes <= 0:
                raise ValueError("line {} has invalid priority or byte count".format(line_number))
            if not start_s <= flow_start <= stop_s:
                raise ValueError("line {} starts outside the requested time window".format(
                    line_number))
            observed_count += 1
            total_bytes += size_bytes
            first_start = flow_start if first_start is None else min(first_start, flow_start)
            last_start = flow_start if last_start is None else max(last_start, flow_start)

    if observed_count != declared_count:
        raise ValueError("header declares {} flows but file contains {}".format(
            declared_count, observed_count))
    if observed_count == 0 or total_bytes <= 0:
        raise ValueError("workload must contain at least one positive-size flow")
    return {
        "flow_count": observed_count,
        "total_bytes": total_bytes,
        "first_start_s": first_start,
        "last_start_s": last_start,
    }


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate deterministic bounded ns-3 reviewer workloads")
    parser.add_argument(
        "--workload", required=True,
        choices=("incast", "hybrid", "ring-allreduce", "all-to-all"))
    parser.add_argument("--output", required=True, help="flow file to write")
    parser.add_argument("--manifest", help="manifest path (default: OUTPUT.manifest.json)")
    parser.add_argument("--force", action="store_true", help="replace existing outputs")
    parser.add_argument("--hosts", type=positive_int, default=16)
    parser.add_argument("--duration-ms", type=float, default=20.0)
    parser.add_argument("--base-time", type=float, default=BASE_TIME_S)
    parser.add_argument("--priority-group", type=int, default=3)
    parser.add_argument("--max-flows", type=positive_int, default=HARD_MAX_FLOWS)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--incast-destination", type=int, default=0)
    parser.add_argument("--flow-bytes", type=positive_int, default=512 * 1024)
    parser.add_argument("--background-flows", type=positive_int, default=512)
    parser.add_argument("--background-flow-bytes", type=positive_int, default=64 * 1024)
    parser.add_argument("--tensor-bytes", type=positive_int, default=4 * 1024 * 1024)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    output_path = os.path.abspath(args.output)
    manifest_path = os.path.abspath(args.manifest or (args.output + ".manifest.json"))
    if output_path == manifest_path:
        raise ValueError("flow and manifest paths must differ")
    for path in (output_path, manifest_path):
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        if os.path.exists(path) and not args.force:
            raise FileExistsError("refusing to replace {}; use --force".format(path))

    flows = generate(args)
    write_flow_file(output_path, flows)
    duration_s = args.duration_ms / 1000.0
    validation = validate_flow_file(
        output_path, args.hosts, args.base_time,
        args.base_time + duration_s, args.max_flows)
    expected_total_bytes = sum(flow.size_bytes for flow in flows)
    if (validation["flow_count"] != len(flows) or
            validation["total_bytes"] != expected_total_bytes):
        raise ValueError("read-back validation does not match generated workload")
    validation["sha256"] = sha256_file(output_path)
    validation["status"] = "passed"

    manifest = {
        "schema_version": 1,
        "workload": args.workload,
        "flow_file": os.path.basename(output_path),
        "parameters": {
            "hosts": args.hosts,
            "duration_ms": args.duration_ms,
            "base_time_s": args.base_time,
            "priority_group": args.priority_group,
            "max_flows": args.max_flows,
            "seed": args.seed,
            "incast_destination": args.incast_destination,
            "flow_bytes": args.flow_bytes,
            "background_flows": args.background_flows,
            "background_flow_bytes": args.background_flow_bytes,
            "tensor_bytes": args.tensor_bytes,
        },
        "validation": validation,
        "run_hint": {
            "simul_time_s": duration_s,
            "max_flows": args.max_flows,
        },
    }
    temporary_manifest = "{}.tmp.{}".format(manifest_path, os.getpid())
    with open(temporary_manifest, "w", encoding="utf-8") as output:
        json.dump(manifest, output, indent=2, sort_keys=True)
        output.write("\n")
    os.replace(temporary_manifest, manifest_path)

    print("wrote {} flows ({} bytes) to {}".format(
        validation["flow_count"], validation["total_bytes"], output_path))
    print("manifest: {}".format(manifest_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
