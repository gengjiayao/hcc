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
DEFAULT_OFLM_BDP_BYTES = 104000
DEFAULT_OFLM_CHURN_ROUNDS = 8
DEFAULT_OFLM_CHURN_INTERVAL_US = 25.0
Flow = namedtuple("Flow", "src dst pg size_bytes start_s")


def positive_int(value):
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def positive_float(value):
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be finite and positive")
    return parsed


def make_incast(hosts, destination, flow_bytes, pg, start_s, duration_s,
                seed=None, jitter_us=0.0, fanin=None, rounds=1,
                round_gap_us=0.0):
    trigger_s = start_s + duration_s * 0.35
    senders = [host for host in range(hosts) if host != destination]
    if fanin is not None:
        senders = senders[:fanin]
    rng = random.Random(seed)
    flows = []
    for round_index in range(rounds):
        round_start_s = trigger_s + round_index * round_gap_us * 1e-6
        for index, src in enumerate(senders):
            offset_s = (rng.uniform(0, jitter_us * 1e-6)
                        if jitter_us > 0 else index * 1e-9)
            flows.append(Flow(
                src, destination, pg, flow_bytes, round_start_s + offset_s))
    return flows


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


def make_oflm_churn(hosts, bdp_bytes, elephant_count, elephant_bytes,
                    rounds, arrival_interval_us, pg, start_s, duration_s,
                    seed=None, jitter_us=0.0):
    """Keep receiver elephants active while fixed-rate BDP-edge flows arrive."""
    if hosts < 16 or hosts % 2:
        raise ValueError("oflm-churn requires an even --hosts value of at least 16")
    hosts_per_tor = hosts // 2
    if elephant_count > hosts_per_tor:
        raise ValueError(
            "--oflm-elephant-flows cannot exceed the hosts on the source ToR ({})".format(
                hosts_per_tor))
    if bdp_bytes < 2:
        raise ValueError("--oflm-bdp-bytes must be at least 2")
    if elephant_bytes <= 2 * bdp_bytes:
        raise ValueError("--oflm-elephant-bytes must exceed twice --oflm-bdp-bytes")

    receiver = hosts - 1
    elephant_start = start_s + duration_s * 0.05
    flows = [
        Flow(src, receiver, pg, elephant_bytes, elephant_start)
        for src in range(elephant_count)
    ]

    # Each eight-arrival cycle contains four flows rejected by the strict
    # size>BDP policy and four admitted flows.  The order is seed-independent.
    sizes = (
        bdp_bytes - 1, bdp_bytes - 1,
        bdp_bytes, bdp_bytes,
        bdp_bytes + 1, bdp_bytes + 1,
        2 * bdp_bytes, 2 * bdp_bytes,
    )
    # Keep the default 5 ms analysis warm-up free of measured churn while the
    # elephants establish the receiver's persistent active set.
    churn_start = start_s + duration_s * 0.30
    interval_s = arrival_interval_us / 1_000_000.0
    rng = random.Random(seed)
    for index in range(rounds * len(sizes)):
        src = index % hosts_per_tor
        jitter_s = rng.uniform(0, jitter_us * 1e-6) if jitter_us > 0 else 0.0
        flows.append(Flow(
            src, receiver, pg, sizes[index % len(sizes)],
            churn_start + index * interval_s + jitter_s))
    if flows[-1].start_s > start_s + duration_s:
        raise ValueError(
            "oflm-churn arrivals exceed the duration; reduce rounds/interval or increase duration")
    return flows


def generate(args):
    duration_s = args.duration_ms / 1000.0
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
    if args.incast_fanin is not None and not 1 <= args.incast_fanin < args.hosts:
        raise ValueError("--incast-fanin must be in [1, hosts - 1]")
    if not math.isfinite(args.incast_jitter_us) or args.incast_jitter_us < 0:
        raise ValueError("--incast-jitter-us must be finite and non-negative")
    if not math.isfinite(args.incast_round_gap_us) or args.incast_round_gap_us < 0:
        raise ValueError("--incast-round-gap-us must be finite and non-negative")
    if not math.isfinite(args.oflm_churn_jitter_us) or args.oflm_churn_jitter_us < 0:
        raise ValueError("--oflm-churn-jitter-us must be finite and non-negative")
    if args.oflm_churn_jitter_us >= args.oflm_churn_interval_us:
        raise ValueError("--oflm-churn-jitter-us must be below the arrival interval")
    incast_span_s = ((args.incast_rounds - 1) * args.incast_round_gap_us +
                     args.incast_jitter_us) * 1e-6
    if incast_span_s > duration_s * 0.65:
        raise ValueError("incast rounds and jitter exceed the workload duration")
    if not 1 <= args.max_flows <= HARD_MAX_FLOWS:
        raise ValueError("--max-flows must be in [1, {}]".format(HARD_MAX_FLOWS))

    if args.workload == "incast":
        flows = make_incast(
            args.hosts, args.incast_destination, args.flow_bytes,
            args.priority_group, args.base_time, duration_s,
            args.seed, args.incast_jitter_us, args.incast_fanin,
            args.incast_rounds, args.incast_round_gap_us)
    elif args.workload == "hybrid":
        flows = make_incast(
            args.hosts, args.incast_destination, args.flow_bytes,
            args.priority_group, args.base_time, duration_s,
            args.seed, args.incast_jitter_us, args.incast_fanin,
            args.incast_rounds, args.incast_round_gap_us)
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
    elif args.workload == "oflm-churn":
        flows = make_oflm_churn(
            args.hosts, args.oflm_bdp_bytes, args.oflm_elephant_flows,
            args.oflm_elephant_bytes, args.oflm_churn_rounds,
            args.oflm_churn_interval_us, args.priority_group,
            args.base_time, duration_s, args.seed, args.oflm_churn_jitter_us)
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
        choices=("incast", "hybrid", "ring-allreduce", "all-to-all", "oflm-churn"))
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
    parser.add_argument(
        "--incast-fanin", type=positive_int,
        help="limit incast to the lowest-numbered eligible senders")
    parser.add_argument("--incast-jitter-us", type=float, default=0.0,
                        help="seeded uniform incast start jitter (default: 0us)")
    parser.add_argument("--incast-rounds", type=positive_int, default=1)
    parser.add_argument("--incast-round-gap-us", type=float, default=0.0)
    parser.add_argument("--flow-bytes", type=positive_int, default=512 * 1024)
    parser.add_argument("--background-flows", type=positive_int, default=512)
    parser.add_argument("--background-flow-bytes", type=positive_int, default=64 * 1024)
    parser.add_argument("--tensor-bytes", type=positive_int, default=4 * 1024 * 1024)
    parser.add_argument("--oflm-bdp-bytes", type=positive_int,
                        default=DEFAULT_OFLM_BDP_BYTES)
    parser.add_argument("--oflm-elephant-flows", type=positive_int, default=8)
    parser.add_argument("--oflm-elephant-bytes", type=positive_int,
                        default=16 * 1024 * 1024)
    parser.add_argument("--oflm-churn-rounds", type=positive_int,
                        default=DEFAULT_OFLM_CHURN_ROUNDS)
    parser.add_argument("--oflm-churn-interval-us", type=positive_float,
                        default=DEFAULT_OFLM_CHURN_INTERVAL_US)
    parser.add_argument("--oflm-churn-jitter-us", type=float, default=0.0,
                        help="seeded uniform jitter below each churn interval")
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
            "incast_fanin": args.incast_fanin,
            "incast_jitter_us": args.incast_jitter_us,
            "incast_rounds": args.incast_rounds,
            "incast_round_gap_us": args.incast_round_gap_us,
            "flow_bytes": args.flow_bytes,
            "background_flows": args.background_flows,
            "background_flow_bytes": args.background_flow_bytes,
            "tensor_bytes": args.tensor_bytes,
            "oflm_bdp_bytes": args.oflm_bdp_bytes,
            "oflm_elephant_flows": args.oflm_elephant_flows,
            "oflm_elephant_bytes": args.oflm_elephant_bytes,
            "oflm_churn_rounds": args.oflm_churn_rounds,
            "oflm_churn_interval_us": args.oflm_churn_interval_us,
            "oflm_churn_jitter_us": args.oflm_churn_jitter_us,
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
