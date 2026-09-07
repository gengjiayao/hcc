#!/usr/bin/env python3
"""Generate a two-tier, full-mesh leaf-spine topology for the simulator."""

import argparse
from pathlib import Path


def positive_int(value):
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hosts", type=positive_int, required=True)
    parser.add_argument("--hosts-per-tor", type=positive_int, default=16)
    parser.add_argument("--spines", type=positive_int, default=16)
    parser.add_argument("--rate-gbps", type=positive_int, default=100)
    parser.add_argument("--delay-ns", type=positive_int, default=1000)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    if args.hosts % args.hosts_per_tor:
        raise ValueError("hosts must be divisible by hosts-per-tor")

    tor_count = args.hosts // args.hosts_per_tor
    tor_base = args.hosts
    spine_base = tor_base + tor_count
    switch_count = tor_count + args.spines
    node_count = args.hosts + switch_count
    link_count = args.hosts + tor_count * args.spines
    rate = "{}Gbps".format(args.rate_gbps)
    delay = "{}ns".format(args.delay_ns)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as output:
        output.write("{} {} {}\n".format(node_count, switch_count, link_count))
        output.write("{}\n".format(
            " ".join(str(node) for node in range(tor_base, node_count))))

        for host in range(args.hosts):
            tor = tor_base + host // args.hosts_per_tor
            output.write("{} {} {} {} 0\n".format(host, tor, rate, delay))

        for tor_offset in range(tor_count):
            tor = tor_base + tor_offset
            for spine_offset in range(args.spines):
                spine = spine_base + spine_offset
                output.write("{} {} {} {} 0\n".format(tor, spine, rate, delay))


if __name__ == "__main__":
    main()
