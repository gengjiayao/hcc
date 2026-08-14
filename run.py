#!/usr/bin/python3
from genericpath import exists
import subprocess
import os
import time
from xmlrpc.client import boolean
import numpy as np
import copy
import shutil
import random
from datetime import datetime
import sys
import os
import argparse
from datetime import date

# randomID
random.seed(datetime.now().timestamp())
MAX_RAND_RANGE = 1000000000

# config template
config_template = """TOPOLOGY_FILE config/{topo}.txt
FLOW_FILE {flow_file}

FLOW_INPUT_FILE mix/output/{id}/{id}_in.txt
FLOW_BW_OUTPUT_FILE mix/output/{id}/{id}_flow_bw.txt
NODE_BANDWIDTH_FILE mix/output/{id}/{id}_out_bw.txt
CNP_OUTPUT_FILE mix/output/{id}/{id}_out_cnp.txt
FCT_OUTPUT_FILE mix/output/{id}/{id}_out_fct.txt
PFC_OUTPUT_FILE mix/output/{id}/{id}_out_pfc.txt
GUARD_STATS_OUTPUT_FILE mix/output/{id}/{id}_out_guard_stats.txt
GUARD_LIFECYCLE_TRACE_OUTPUT_FILE {guard_lifecycle_output}
GUARD_CONTROLLER_TRACE_OUTPUT_FILE {guard_controller_output}
GUARD_GRANT_TRACE_OUTPUT_FILE {guard_grant_output}
QUEUE_STATS_OUTPUT_FILE mix/output/{id}/{id}_out_queue_stats.txt
QLEN_MON_FILE mix/output/{id}/{id}_out_qlen.txt
VOQ_MON_FILE mix/output/{id}/{id}_out_voq.txt
VOQ_MON_DETAIL_FILE mix/output/{id}/{id}_out_voq_per_dst.txt
UPLINK_MON_FILE mix/output/{id}/{id}_out_uplink.txt
CONN_MON_FILE mix/output/{id}/{id}_out_conn.txt
EST_ERROR_MON_FILE mix/output/{id}/{id}_out_est_error.txt

MONITOR_PROFILE {monitor_profile}
ANALYSIS_WARMUP_TIME {analysis_warmup_time}
PREFLIGHT_MAX_FLOWS {max_flows}
QLEN_MON_START {qlen_mon_start}
QLEN_MON_END {qlen_mon_end}
QLEN_MON_INTERVAL {qlen_monitoring_interval}
SW_MONITORING_INTERVAL {sw_monitoring_interval}

FLOWGEN_START_TIME {flowgen_start_time}
FLOWGEN_STOP_TIME {flowgen_stop_time}
BUFFER_SIZE {buffer_size}

CC_MODE {cc_mode}
LB_MODE {lb_mode}
ENABLE_PFC {enabled_pfc}
ENABLE_IRN {enabled_irn}

CONWEAVE_TX_EXPIRY_TIME {cwh_tx_expiry_time}
CONWEAVE_REPLY_TIMEOUT_EXTRA {cwh_extra_reply_deadline}
CONWEAVE_PATH_PAUSE_TIME {cwh_path_pause_time}
CONWEAVE_EXTRA_VOQ_FLUSH_TIME {cwh_extra_voq_flush_time}
CONWEAVE_DEFAULT_VOQ_WAITING_TIME {cwh_default_voq_waiting_time}

ALPHA_RESUME_INTERVAL 1
RATE_DECREASE_INTERVAL 4
CLAMP_TARGET_RATE 0
RP_TIMER 300 
FAST_RECOVERY_TIMES 1
EWMA_GAIN {ewma_gain}
RATE_AI {ai}Mb/s
RATE_HAI {hai}Mb/s
MIN_RATE 100Mb/s
DCTCP_RATE_AI {dctcp_ai}Mb/s

ERROR_RATE_PER_LINK {error_rate_per_link}
L2_CHUNK_SIZE 4000
L2_ACK_INTERVAL 1
L2_BACK_TO_ZERO 0

RATE_BOUND 1
HAS_WIN {has_win}
VAR_WIN {var_win}
FAST_REACT {fast_react}
MI_THRESH {mi}
INT_MULTI {int_multi}
GLOBAL_T 0
U_TARGET 0.95
GUARD_LAMBDA {guard_lambda}
GUARD_EWMA_BETA {guard_beta}
GUARD_RELEASE_GAMMA {guard_gamma}
GUARD_SELECTIVE_REGISTRATION {guard_selective_registration}
GUARD_PROACTIVE_RELEASE {guard_proactive_release}
GUARD_KEEP_LAST_HOP_INT {guard_keep_last_hop_int}
GUARD_SIZE_PRIORITY {guard_size_priority}
GUARD_WORK_CONSERVING {guard_work_conserving}
GUARD_REBALANCE_INTERVAL_US {guard_rebalance_interval_us}
GUARD_DEMAND_THRESHOLD {guard_demand_threshold}
GUARD_LIFECYCLE_TRACE {guard_lifecycle_trace}
GUARD_LIFECYCLE_TRACE_MAX_LINES {guard_lifecycle_max_lines}
GUARD_CONTROLLER_TRACE {guard_controller_trace}
GUARD_CONTROLLER_TRACE_MAX_LINES {guard_controller_max_lines}
GUARD_GRANT_TRACE {guard_grant_trace}
GUARD_GRANT_TRACE_MAX_LINES {guard_grant_max_lines}
HOMA_OVERCOMMIT {homa_overcommit}
HOMA_RESEND_TIMEOUT_US {homa_resend_timeout_us}
HOMA_UNSCHEDULED_LEVELS {homa_unscheduled_levels}
HOMA_UNSCHEDULED_CUTOFFS 5 {homa_unscheduled_cutoffs}
MULTI_RATE 0
SAMPLE_FEEDBACK 0

ENABLE_QCN 1
USE_DYNAMIC_PFC_THRESHOLD 1
PACKET_PAYLOAD_SIZE 1000


LINK_DOWN 0 0 0
KMAX_MAP {kmax_map}
KMIN_MAP {kmin_map}
PMAX_MAP {pmax_map}
LOAD {load}
RANDOM_SEED {seed}
"""


# LB/CC mode matching
cc_modes = {
    "dcqcn": 1,
    "hpcc": 3,
    "timely": 7,
    "dctcp": 8,
    "homa-simple": 10,  # legacy simplified Homa
    "guard": 11,
    "homa": 12,         # Homa ns-3 mode (receiver grants + SRPT priorities)
    "guard-active-only": 13,  # receiver-rate-only GUARD component ablation
}

lb_modes = {
    "fecmp": 0,
    "drill": 2,
    "conga": 3,
    "letflow": 6,
    "conweave": 9,
}

topo2bdp = {
    "leaf_spine_8_100G_OS1": 104000,  # 2-tier -> all 100Gbps
    "leaf_spine_8_100G_OS2": 104000,  # 2-tier -> all 100Gbps
    "leaf_spine_8_100G_OS4": 104000,  # 2-tier -> all 100Gbps
    "leaf_spine_12_100G_OS4": 104000,  # 2-tier -> all 100Gbps
    "leaf_spine_16_100G_OS1": 104000,  # 2-tier -> all 100Gbps
    "leaf_spine_16_100G_OS4": 104000,  # 2-tier -> all 100Gbps
    "leaf_spine_128_100G_OS2": 104000,  # 2-tier -> all 100Gbps
    "leaf_spine_128_100G_OS1": 104000,  # 2-tier -> all 100Gbps
    "leaf_spine_64_100G_OS1": 104000,  # 2-tier -> all 100Gbps
    "fat_k8_100G_OS2": 156000,  # 3-tier -> all 100Gbps
}

FLOWGEN_DEFAULT_TIME = 2.0  # see /traffic_gen/traffic_gen.py::base_t
DEFAULT_ANALYSIS_WARMUP = 0.005
MIN_SMOKE_TIME = 0.005
MIN_FORMAL_TIME = 0.010
DEFAULT_GUARD_LIFECYCLE_MAX_LINES = 1024
HARD_GUARD_LIFECYCLE_MAX_LINES = 10000
DEFAULT_GUARD_CONTROLLER_MAX_LINES = 10000
HARD_GUARD_CONTROLLER_MAX_LINES = 300000
DEFAULT_GUARD_GRANT_MAX_LINES = 1000
HARD_GUARD_GRANT_MAX_LINES = 10000
HOMA_DATA_PRIORITY_LEVELS = 7
HOMA_PRIORITY_PROFILE_SAMPLES = 10000


def _homa_profile_from_sizes(sizes, bdp_bytes,
                             data_levels=HOMA_DATA_PRIORITY_LEVELS):
    """Allocate Homa DATA priorities by byte share, then equalize unscheduled bytes.

    Queue 0 is reserved for control traffic in this ns-3 model, leaving queues
    1..7 for DATA.  Homa allocates priorities in proportion to the workload's
    unscheduled byte fraction and partitions the unscheduled priorities so each
    carries approximately the same number of bytes.
    """
    if bdp_bytes <= 0:
        raise ValueError("Homa BDP must be positive")
    positive_sizes = sorted(int(size) for size in sizes if int(size) > 0)
    if not positive_sizes:
        raise ValueError("Homa priority profiling requires at least one positive flow")
    total_bytes = float(sum(positive_sizes))
    unscheduled_weights = [min(size, bdp_bytes) for size in positive_sizes]
    total_unscheduled = float(sum(unscheduled_weights))
    unscheduled_fraction = total_unscheduled / total_bytes
    unscheduled_levels = int(round(data_levels * unscheduled_fraction))
    unscheduled_levels = max(1, min(data_levels - 1, unscheduled_levels))

    cutoffs = []
    cumulative = 0.0
    next_boundary = 1
    for size, weight in zip(positive_sizes, unscheduled_weights):
        cumulative += weight
        while (next_boundary < unscheduled_levels and
               cumulative >= total_unscheduled * next_boundary / unscheduled_levels):
            cutoffs.append(size)
            next_boundary += 1
    while len(cutoffs) < unscheduled_levels - 1:
        cutoffs.append(positive_sizes[-1])
    return {
        "unscheduled_levels": unscheduled_levels,
        "scheduled_levels": data_levels - unscheduled_levels,
        "cutoffs": cutoffs,
        "unscheduled_fraction": unscheduled_fraction,
    }


def derive_homa_profile_from_flow_file(path, bdp_bytes):
    with open(path, "r") as traffic_file:
        declared = int(traffic_file.readline().strip())
        sizes = []
        for line in traffic_file:
            fields = line.split()
            if len(fields) >= 4:
                sizes.append(int(fields[3]))
    if declared != len(sizes):
        raise ValueError("flow count {} does not match {} records".format(
            declared, len(sizes)))
    return _homa_profile_from_sizes(sizes, bdp_bytes)


def derive_homa_profile_from_cdf(path, bdp_bytes,
                                 samples=HOMA_PRIORITY_PROFILE_SAMPLES):
    """Deterministically sample midpoint percentiles from traffic_gen's CDF."""
    with open(path, "r") as cdf_file:
        cdf = [tuple(map(float, line.split())) for line in cdf_file if line.strip()]
    if len(cdf) < 2 or cdf[0][1] != 0 or cdf[-1][1] != 100:
        raise ValueError("invalid Homa workload CDF: {}".format(path))
    sizes = []
    segment = 1
    for index in range(samples):
        percentile = (index + 0.5) * 100.0 / samples
        while percentile > cdf[segment][1]:
            segment += 1
        x0, y0 = cdf[segment - 1]
        x1, y1 = cdf[segment]
        size = x0 + (x1 - x0) * (percentile - y0) / (y1 - y0)
        sizes.append(max(1, int(round(size))))
    return _homa_profile_from_sizes(sizes, bdp_bytes)


def resolve_guard_components(guard_oflm, selective_registration, proactive_release):
    """Resolve the legacy combined OFLM switch without masking new ablations."""
    if guard_oflm == 0:
        return 0, 0
    return selective_registration, proactive_release


def validate_guard_lifecycle_options(enabled, cc, output, max_lines):
    """Reject lifecycle settings that could create misleading or unbounded output."""
    if not 1 <= max_lines <= HARD_GUARD_LIFECYCLE_MAX_LINES:
        raise ValueError(
            "--guard_lifecycle_max_lines must be in [1, {}]".format(
                HARD_GUARD_LIFECYCLE_MAX_LINES))
    if enabled and cc not in ("guard", "guard-active-only"):
        raise ValueError("--guard_lifecycle_trace requires a GUARD mode")
    if not enabled and output:
        raise ValueError(
            "--guard_lifecycle_output requires --guard_lifecycle_trace 1")


def validate_guard_controller_options(enabled, cc, output, max_lines):
    """Reject controller traces that are inapplicable or not strictly bounded."""
    if not 1 <= max_lines <= HARD_GUARD_CONTROLLER_MAX_LINES:
        raise ValueError(
            "--guard_controller_max_lines must be in [1, {}]".format(
                HARD_GUARD_CONTROLLER_MAX_LINES))
    if enabled and cc != "guard":
        raise ValueError("--guard_controller_trace requires full GUARD mode")
    if not enabled and output:
        raise ValueError(
            "--guard_controller_output requires --guard_controller_trace 1")


def resolve_ecn_thresholds(kmin_kb=None, kmax_kb=None, pmax=None):
    """Validate an explicit ECN threshold override as one indivisible tuple."""
    values = (kmin_kb, kmax_kb, pmax)
    if all(value is None for value in values):
        return 100, 400, 0.2
    if any(value is None for value in values):
        raise ValueError("ECN kmin, kmax, and pmax overrides must be supplied together")
    if not 0 <= kmin_kb < kmax_kb:
        raise ValueError("ECN thresholds must satisfy 0 <= kmin < kmax")
    if not 0 < pmax <= 1:
        raise ValueError("ECN pmax must be in (0, 1]")
    return kmin_kb, kmax_kb, pmax


def validate_guard_grant_options(enabled, cc, output, max_lines):
    """Keep the mechanism-only grant audit applicable and strictly bounded."""
    if not 1 <= max_lines <= HARD_GUARD_GRANT_MAX_LINES:
        raise ValueError(
            "--guard_grant_max_lines must be in [1, {}]".format(
                HARD_GUARD_GRANT_MAX_LINES))
    if enabled and cc not in ("guard", "guard-active-only"):
        raise ValueError("--guard_grant_trace requires a GUARD mode")
    if not enabled and output:
        raise ValueError("--guard_grant_output requires --guard_grant_trace 1")


def main():
    # make directory if not exists
    isExist = os.path.exists(os.getcwd() + "/mix/output/")
    if not isExist:
        os.makedirs(os.getcwd() + "/mix/output/")
        print("The new directory is created - {}".format(os.getcwd() + "/mix/output/"))

    parser = argparse.ArgumentParser(description='run simulation')
    parser.add_argument('--cc', dest='cc', action='store',
                        choices=tuple(cc_modes), default='dcqcn',
                        help="congestion-control mode (default: dcqcn)")
    parser.add_argument('--lb', dest='lb', action='store',
                        default='fecmp', help="fecmp/pecmp/drill/conga (default: fecmp)")
    parser.add_argument('--pfc', dest='pfc', action='store',
                        type=int, default=1, help="enable PFC (default: 1)")
    parser.add_argument('--irn', dest='irn', action='store',
                        type=int, default=0, help="enable IRN (default: 0)")
    parser.add_argument('--simul_time', dest='simul_time', action='store',
                        default='0.1', help="traffic time to simulate (up to 3 seconds) (default: 0.1)")
    parser.add_argument('--smoke', action='store_true',
                        help="allow a 5ms diagnostic run and suppress formal FCT summaries")
    parser.add_argument('--analysis_warmup', type=float, default=DEFAULT_ANALYSIS_WARMUP,
                        help="warm-up excluded from formal FCT analysis in seconds (default: 0.005)")
    parser.add_argument('--max_flows', type=int, default=150000,
                        help="abort before ns-3 when traffic exceeds this flow count (default: 150000)")
    parser.add_argument('--flow_file', type=str,
                        help="use an existing flow file instead of generating random CDF traffic")
    parser.add_argument('--buffer', dest="buffer", action='store',
                        default='9', help="the switch buffer size (MB) (default: 9)")
    parser.add_argument('--netload', dest='netload', action='store', type=int,
                        default=40, help="Network load at NIC to generate traffic (default: 40.0)")
    parser.add_argument('--bw', dest="bw", action='store',
                        default='100', help="the NIC bandwidth (Gbps) (default: 100)")
    parser.add_argument('--topo', dest='topo', action='store',
                        default='leaf_spine_128_100G', help="the name of the topology file (default: leaf_spine_128_100G_OS2)")
    parser.add_argument('--cdf', dest='cdf', action='store',
                        default='AliStorage2019', help="the name of the cdf file (default: AliStorage2019)")
    parser.add_argument('--enforce_win', dest='enforce_win', action='store',
                        type=int, default=0, help="enforce to use window scheme (default: 0)")
    parser.add_argument('--sw_monitoring_interval', dest='sw_monitoring_interval', action='store',
                        type=int, default=10000, help="uplink/connection sampling interval in full mode (default: 10000ns)")
    parser.add_argument('--qlen_monitoring_interval', type=int, default=1000,
                        help="queue-length sampling interval in ns (default: 1000ns)")
    parser.add_argument('--monitor_profile', choices=('bulk', 'full'), default='bulk',
                        help="bulk keeps bounded summaries; full adds detailed time series (default: bulk)")
    parser.add_argument('--error_rate_per_link', type=float, default=0.0,
                        help="independent packet error probability per link in [0,1) (default: 0)")
    parser.add_argument('--ecn_kmin_kb', type=int,
                        help="override ECN Kmin in decimal KB; requires kmax and pmax")
    parser.add_argument('--ecn_kmax_kb', type=int,
                        help="override ECN Kmax in decimal KB; requires kmin and pmax")
    parser.add_argument('--ecn_pmax', type=float,
                        help="override ECN maximum marking probability; requires kmin/kmax")
    parser.add_argument('--guard_beta', type=float, default=0.125,
                        help="GUARD EWMA historical-sample weight in [0,1] (default: 0.125)")
    parser.add_argument('--guard_gamma', type=float, default=1.0,
                        help="GUARD proactive-release threshold multiplier >= 0 (default: 1.0)")
    parser.add_argument('--guard_lambda', type=float, default=1.0,
                        help="GUARD HPCC-target multiplier >= 1 (default: 1.0)")
    parser.add_argument('--guard_oflm', type=int, choices=(0, 1), default=None,
                        help="legacy alias; an explicit 0 disables both OFLM components")
    parser.add_argument('--guard_selective_registration', type=int, choices=(0, 1), default=1,
                        help="register only flows larger than one BDP (default: 1)")
    parser.add_argument('--guard_proactive_release', type=int, choices=(0, 1), default=1,
                        help="release registered flows before completion (default: 1)")
    parser.add_argument('--guard_keep_last_hop_int', type=int, choices=(0, 1), default=0,
                        help="retain last-hop INT in GUARD for ablation (default: 0)")
    parser.add_argument('--guard_size_priority', type=int, choices=(0, 1), default=1,
                        help="remap GUARD flows to size-based priority groups (default: 1)")
    parser.add_argument('--guard_work_conserving', type=int, choices=(0, 1), default=1,
                        help="reclaim persistently unused receiver shares (default: 1)")
    parser.add_argument('--guard_rebalance_interval_us', type=int, default=10,
                        help="GUARD receiver demand-sampling interval in us (default: 10)")
    parser.add_argument('--guard_demand_threshold', type=float, default=0.8,
                        help="arrival/grant ratio below which a share is reclaimable (default: 0.8)")
    parser.add_argument('--guard_lifecycle_trace', type=int, choices=(0, 1), default=0,
                        help="write a bounded per-flow GUARD lifecycle CSV (default: 0)")
    parser.add_argument('--guard_lifecycle_output', type=str,
                        help="lifecycle CSV path (default: the run output directory)")
    parser.add_argument('--guard_lifecycle_max_lines', type=int,
                        default=DEFAULT_GUARD_LIFECYCLE_MAX_LINES,
                        help="maximum lifecycle data rows (default: 1024; hard maximum: 10000)")
    parser.add_argument('--guard_controller_trace', type=int, choices=(0, 1), default=0,
                        help="write a bounded valid-update GUARD controller CSV (default: 0)")
    parser.add_argument('--guard_controller_output', type=str,
                        help="controller CSV path (default: the run output directory)")
    parser.add_argument('--guard_controller_max_lines', type=int,
                        default=DEFAULT_GUARD_CONTROLLER_MAX_LINES,
                        help="maximum controller rows (default: 10000; hard maximum: 300000)")
    parser.add_argument('--guard_grant_trace', type=int, choices=(0, 1), default=0,
                        help="write a bounded GUARD grant send/receive audit CSV (default: 0)")
    parser.add_argument('--guard_grant_output', type=str,
                        help="grant audit CSV path (default: the run output directory)")
    parser.add_argument('--guard_grant_max_lines', type=int,
                        default=DEFAULT_GUARD_GRANT_MAX_LINES,
                        help="maximum grant audit rows (default: 1000; hard maximum: 10000)")
    parser.add_argument('--homa_overcommit', type=int, choices=range(1, 7), default=None,
                        help="Homa scheduled messages per receiver; default uses every scheduled priority")
    parser.add_argument('--homa_resend_timeout_us', type=int, default=1000,
                        help="Homa receiver no-progress timeout in us (default: 1000)")
    parser.add_argument('--seed', type=int, default=1,
                        help="traffic-generator and ns-3 random seed (default: 1)")

    # #### CONWEAVE PARAMETERS ####
    # parser.add_argument('--cwh_extra_reply_deadline', dest='cwh_extra_reply_deadline', action='store',
    #                     type=int, default=4, help="extra-timeout, where reply_deadline = base-RTT + extra-timeout (default: 4us)")
    # parser.add_argument('--cwh_path_pause_time', dest='cwh_path_pause_time', action='store',
    #                     type=int, default=16, help="Time to pause the path with ECN feedback (default: 8us")
    # parser.add_argument('--cwh_extra_voq_flush_time', dest='cwh_extra_voq_flush_time', action='store',
    #                     type=int, default=16, help="Extra VOQ Flush Time (default: 8us for IRN)")
    # parser.add_argument('--cwh_default_voq_waiting_time', dest='cwh_default_voq_waiting_time', action='store',
    #                     type=int, default=400, help="Default VOQ Waiting Time (default: 400us)")
    # parser.add_argument('--cwh_tx_expiry_time', dest='cwh_tx_expiry_time', action='store',
    #                     type=int, default=1000, help="timeout value of ConWeave Tx for CLEAR signal (default: 1000us)")

    args = parser.parse_args()
    guard_selective_registration, guard_proactive_release = resolve_guard_components(
        args.guard_oflm, args.guard_selective_registration, args.guard_proactive_release)

    # make running ID of this config
    # need to check directory exists or not
    isExist = True
    config_ID = 0
    while (isExist):
        config_ID = str(random.randrange(MAX_RAND_RANGE))
        isExist = os.path.exists(os.getcwd() + "/mix/output/" + config_ID)

    # input parameters
    cc_mode = cc_modes[args.cc]
    lb_mode = lb_modes[args.lb]
    enabled_pfc = int(args.pfc)
    enabled_irn = int(args.irn)
    bw = int(args.bw)
    buffer = args.buffer
    topo = args.topo
    enforce_win = args.enforce_win
    cdf = args.cdf
    flowgen_start_time = FLOWGEN_DEFAULT_TIME  # default: 2.0
    simul_time = float(args.simul_time)
    flowgen_stop_time = flowgen_start_time + simul_time
    sw_monitoring_interval = int(args.sw_monitoring_interval)
    qlen_monitoring_interval = int(args.qlen_monitoring_interval)

    if not 0.0 <= args.guard_beta <= 1.0:
        raise Exception("CONFIG ERROR: --guard_beta must be in [0, 1].")
    if args.guard_gamma < 0.0:
        raise Exception("CONFIG ERROR: --guard_gamma must be non-negative.")
    if args.guard_lambda < 1.0:
        raise Exception("CONFIG ERROR: --guard_lambda must be at least 1.0.")
    if args.guard_rebalance_interval_us <= 0:
        raise Exception("CONFIG ERROR: --guard_rebalance_interval_us must be positive.")
    if not 0.0 < args.guard_demand_threshold < 1.0:
        raise Exception("CONFIG ERROR: --guard_demand_threshold must be in (0, 1).")
    if not 1 <= args.seed <= 2147483647:
        raise Exception("CONFIG ERROR: --seed must be in [1, 2147483647].")
    if args.homa_resend_timeout_us <= 0:
        raise Exception("CONFIG ERROR: --homa_resend_timeout_us must be positive.")
    if qlen_monitoring_interval <= 0:
        raise Exception("CONFIG ERROR: --qlen_monitoring_interval must be positive.")
    if not 0.0 <= args.error_rate_per_link < 1.0:
        raise Exception("CONFIG ERROR: --error_rate_per_link must be in [0, 1).")
    if args.analysis_warmup < 0:
        raise Exception("CONFIG ERROR: --analysis_warmup must be non-negative.")
    if args.max_flows <= 0:
        raise Exception("CONFIG ERROR: --max_flows must be positive.")
    try:
        validate_guard_lifecycle_options(
            args.guard_lifecycle_trace, args.cc, args.guard_lifecycle_output,
            args.guard_lifecycle_max_lines)
        validate_guard_controller_options(
            args.guard_controller_trace, args.cc, args.guard_controller_output,
            args.guard_controller_max_lines)
        ecn_kmin_kb, ecn_kmax_kb, ecn_pmax = resolve_ecn_thresholds(
            args.ecn_kmin_kb, args.ecn_kmax_kb, args.ecn_pmax)
        validate_guard_grant_options(
            args.guard_grant_trace, args.cc, args.guard_grant_output,
            args.guard_grant_max_lines)
    except ValueError as error:
        raise Exception("CONFIG ERROR: {}.".format(error))
    if simul_time < MIN_SMOKE_TIME:
        raise Exception("CONFIG ERROR: Runtime must be at least 5ms.")
    if not args.smoke and simul_time < MIN_FORMAL_TIME:
        raise Exception("CONFIG ERROR: Formal runs must be at least 10ms; use --smoke for diagnostics.")
    if not args.smoke and args.analysis_warmup >= simul_time:
        raise Exception("CONFIG ERROR: --analysis_warmup must be shorter than a formal run.")

    # get over-subscription ratio from topoogy name

    netload = args.netload
    oversub = int(topo.replace("\n", "").split("OS")[-1].replace(".txt", ""))
    assert (int(args.netload) % oversub == 0)
    hostload = int(args.netload) / oversub
    assert (hostload > 0)

    # Sanity checks
    if (args.cc == "timely" or args.cc == "hpcc") and args.lb == "conweave":
        raise Exception(
            "CONFIG ERROR : ConWeave currently does not support RTT-based protocols. Plz modify its logic accordingly.")
    if enabled_irn == 1 and enabled_pfc == 1 and cc_mode != 12:
        raise Exception(
            "CONFIG ERROR : If IRN is turn-on, then you should turn off PFC (for better perforamnce).")
    # cc_mode=12 (homa) intentionally combines --pfc 1 with --irn 1: PFC
    # stays on at the NIC for the control queue (pg=0), but the MMU disables
    # PFC pause for Homa's data queues (pg 1-7) so they run lossy; IRN
    # picks up dropped data packets until PR5 ships native RESEND.
    if enabled_irn == 0 and enabled_pfc == 0:
        raise Exception(
            "CONFIG ERROR : Either IRN or PFC should be true (at least one).")
    # sniff number of servers
    with open("config/{topo}.txt".format(topo=args.topo), 'r') as f_topo:
        line = f_topo.readline().split(" ")
        n_host = int(line[0]) - int(line[1])

    assert (hostload >= 0 and hostload < 100)
    custom_flow_source = None
    if args.flow_file:
        custom_flow_source = os.path.abspath(os.path.expanduser(args.flow_file))
        if not os.path.isfile(custom_flow_source):
            raise Exception("CONFIG ERROR: --flow_file is not a regular file: {}".format(
                custom_flow_source))
        try:
            with open(custom_flow_source, "r") as traffic_file:
                custom_declared_count = int(traffic_file.readline().strip())
        except (OSError, ValueError) as error:
            raise Exception("CONFIG ERROR: cannot read flow count from {}: {}".format(
                custom_flow_source, error))
        if custom_declared_count < 0:
            raise Exception("CONFIG ERROR: traffic declares a negative flow count: {}".format(
                custom_declared_count))
        if custom_declared_count > args.max_flows:
            raise Exception(
                "CONFIG ERROR: traffic has {} flows, exceeding --max_flows {}; ns-3 was not started.".format(
                    custom_declared_count, args.max_flows))

    # Each custom run consumes its private copy, so concurrent changes to the
    # source cannot alter the traffic after preflight.
    output_dir = os.path.join(os.getcwd(), "mix", "output", config_ID)

    if args.flow_file:
        assert not os.path.exists(output_dir)
        os.makedirs(output_dir)
        print("The new directory is created  - {}".format(output_dir + "/"))
        flow_path = os.path.join(output_dir, "{}_input_flow.txt".format(config_ID))
        shutil.copyfile(custom_flow_source, flow_path)
        flow_config_path = os.path.relpath(flow_path, os.getcwd())
        print("Custom traffic snapshot: {} -> {}".format(
            custom_flow_source, flow_config_path))
    else:
        flow = "L_{load:.2f}_CDF_{cdf}_N_{n_host}_T_{time}ms_B_{bw}_S_{seed}_flow".format(
            load=hostload, cdf=args.cdf, n_host=n_host,
            time=int(float(args.simul_time)*1000), bw=bw, seed=args.seed)

        # check the file exists
        if (exists(os.getcwd() + "/config/" + flow + ".txt")):
            print("Input traffic file with load:{load:.2f}, cdf:{cdf}, n_host:{n_host} already exists".format(
                load=hostload, cdf=cdf, n_host=n_host))
        else:  # make the input traffic file
            print("Generate a input traffic file...")
            print("python ./traffic_gen/traffic_gen.py -c {cdf} -n {n_host} -l {load} -b {bw} -t {time} -s {seed} -o {output}".format(
                cdf=os.getcwd() + "/../traffic_gen/" + args.cdf + ".txt",
                n_host=n_host,
                load=hostload / 100.0,
                bw=args.bw + "G",
                time=args.simul_time,
                seed=args.seed,
                output=os.getcwd() + "/config/" + flow + ".txt"))

            os.system("python ./traffic_gen/traffic_gen.py -c {cdf} -n {n_host} -l {load} -b {bw} -t {time} -s {seed} -o {output}".format(
                cdf=os.getcwd() + "/traffic_gen/" + args.cdf + ".txt",
                n_host=n_host,
                load=hostload / 100.0,
                bw=args.bw + "G",
                time=args.simul_time,
                seed=args.seed,
                output=os.getcwd() + "/config/" + flow + ".txt"))

        flow_path = os.getcwd() + "/config/" + flow + ".txt"
        flow_config_path = "config/{}.txt".format(flow)

    try:
        with open(flow_path, "r") as traffic_file:
            flow_count = int(traffic_file.readline().strip())
    except (OSError, ValueError) as error:
        raise Exception("CONFIG ERROR: cannot read flow count from {}: {}".format(
            flow_path, error))
    if flow_count < 0:
        raise Exception("CONFIG ERROR: traffic declares a negative flow count: {}".format(
            flow_count))
    if flow_count > args.max_flows:
        raise Exception(
            "CONFIG ERROR: traffic has {} flows, exceeding --max_flows {}; ns-3 was not started.".format(
                flow_count, args.max_flows))
    print("Preflight flow count: {}/{}".format(flow_count, args.max_flows))

    if not args.flow_file:
        assert not os.path.exists(output_dir)
        os.makedirs(output_dir)
        print("The new directory is created  - {}".format(output_dir + "/"))

    guard_lifecycle_output = args.guard_lifecycle_output
    if guard_lifecycle_output is None:
        guard_lifecycle_output = os.path.join(
            output_dir, "{}_out_guard_lifecycle.csv".format(config_ID))
    else:
        guard_lifecycle_output = os.path.abspath(
            os.path.expanduser(guard_lifecycle_output))
    if any(character.isspace() for character in guard_lifecycle_output):
        raise Exception("CONFIG ERROR: --guard_lifecycle_output cannot contain whitespace.")
    if args.guard_lifecycle_trace:
        output_parent = os.path.dirname(guard_lifecycle_output)
        if output_parent:
            os.makedirs(output_parent, exist_ok=True)
    guard_controller_output = args.guard_controller_output
    if guard_controller_output is None:
        guard_controller_output = os.path.join(
            output_dir, "{}_out_guard_controller.csv".format(config_ID))
    else:
        guard_controller_output = os.path.abspath(
            os.path.expanduser(guard_controller_output))
    if any(character.isspace() for character in guard_controller_output):
        raise Exception("CONFIG ERROR: --guard_controller_output cannot contain whitespace.")
    if args.guard_controller_trace:
        output_parent = os.path.dirname(guard_controller_output)
        if output_parent:
            os.makedirs(output_parent, exist_ok=True)
    guard_grant_output = args.guard_grant_output
    if guard_grant_output is None:
        guard_grant_output = os.path.join(
            output_dir, "{}_out_guard_grants.csv".format(config_ID))
    else:
        guard_grant_output = os.path.abspath(os.path.expanduser(guard_grant_output))
    if any(character.isspace() for character in guard_grant_output):
        raise Exception("CONFIG ERROR: --guard_grant_output cannot contain whitespace.")
    if args.guard_grant_trace:
        output_parent = os.path.dirname(guard_grant_output)
        if output_parent:
            os.makedirs(output_parent, exist_ok=True)

    # sanity check - bandwidth
    with open("config/{topo}.txt".format(topo=args.topo), 'r') as f_topo:
        first_line = f_topo.readline().split(" ")
        n_host = int(first_line[0]) - int(first_line[1])
        n_link = int(first_line[2])
        i = 0
        for line in f_topo.readlines()[1:]:
            i += 1
            if (i > n_link):
                break
            parsed = line.split(" ")
            if len(parsed) > 2 and (int(parsed[0]) < n_host or int(parsed[1]) < n_host):
                assert (int(parsed[2].replace("Gbps", "")) == int(bw))
    print("All NIC bandwidth is {bw}Gbps".format(bw=bw))

    ##################################################################
    ##########              ConWeave parameters             ##########
    ##################################################################
    if (lb_mode == 9):
        cwh_extra_reply_deadline = 4  # 4us, NOTE: this is "extra" term to base RTT
        cwh_path_pause_time = 16  # 8us (K_min) or 16us

        if "leaf_spine" in topo:  # 2-tier
            cwh_extra_voq_flush_time = 16
            cwh_default_voq_waiting_time = 200
            cwh_tx_expiry_time = 300  # 300us
        elif "fat" in topo and enabled_pfc == 0 and enabled_irn == 1:  # 3-tier, IRN
            cwh_extra_voq_flush_time = 16
            cwh_default_voq_waiting_time = 300
            cwh_tx_expiry_time = 1000  # 1ms
        elif "fat" in topo and enabled_pfc == 1 and enabled_irn == 0:  # 3-tier, Lossless
            cwh_extra_voq_flush_time = 64
            cwh_default_voq_waiting_time = 600
            cwh_tx_expiry_time = 1000  # 1ms
        else:
            raise Exception(
                "Unsupported ConWeave Parameter Setup")
    else:
        #### CONWEAVE PARAMETERS (DUMMY) ####
        cwh_extra_reply_deadline = 4
        cwh_path_pause_time = 16
        cwh_extra_voq_flush_time = 64
        cwh_default_voq_waiting_time = 400
        cwh_tx_expiry_time = 1000

    ##################################################################

    config_name = os.getcwd() + "/mix/output/" + config_ID + "/config.txt"
    print("Config filename:{}".format(config_name))

    # By default, DCQCN uses no window (rate-based).
    has_win = 0
    var_win = 0
    if (cc_mode == 3 or cc_mode == 8 or cc_mode == 11 or cc_mode == 13 or enforce_win == 1):
        has_win = 1
        var_win = 1
        if enforce_win == 1:
            print("### INFO: Enforced to use window scheme! ###")

    # record to history
    simulday = datetime.now().strftime("%m/%d/%y")
    with open("./mix/.history", "a") as history:
        history.write("{simulday},{config_ID},{cc_mode},{lb_mode},{cwh_tx_expiry_time},{cwh_extra_reply_deadline},{cwh_path_pause_time},{cwh_extra_voq_flush_time},{cwh_default_voq_waiting_time},{pfc},{irn},{has_win},{var_win},{topo},{bw},{cdf},{load},{time}\n".format(
            simulday=simulday,
            config_ID=config_ID,
            cc_mode=cc_mode,
            lb_mode=lb_mode,
            cwh_tx_expiry_time=cwh_tx_expiry_time,
            cwh_extra_reply_deadline=cwh_extra_reply_deadline,
            cwh_path_pause_time=cwh_path_pause_time,
            cwh_extra_voq_flush_time=cwh_extra_voq_flush_time,
            cwh_default_voq_waiting_time=cwh_default_voq_waiting_time,
            pfc=enabled_pfc,
            irn=enabled_irn,
            has_win=has_win,
            var_win=var_win,
            topo=topo,
            bw=bw,
            cdf=cdf,
            load=netload,
            time=args.simul_time,
        ))

    # 1 BDP calculation
    if topo2bdp.get(topo) == None:
        print("ERROR - topology is not registered in run.py!!", flush=True)
        return
    bdp = int(topo2bdp[topo])
    print("1BDP = {}".format(bdp))

    homa_profile = {
        "unscheduled_levels": 3,
        "scheduled_levels": 4,
        "cutoffs": [bdp // 4, bdp // 2],
        "unscheduled_fraction": 0.0,
    }
    if cc_mode == 12:
        try:
            if args.flow_file:
                homa_profile = derive_homa_profile_from_flow_file(flow_path, bdp)
                homa_profile_source = "flow-snapshot"
            else:
                cdf_path = os.path.join(os.getcwd(), "traffic_gen", args.cdf + ".txt")
                homa_profile = derive_homa_profile_from_cdf(cdf_path, bdp)
                homa_profile_source = "cdf"
        except (OSError, ValueError) as error:
            raise Exception("CONFIG ERROR: cannot derive Homa priority profile: {}.".format(error))
        print("Homa priority profile: source={} unscheduled_fraction={:.6f} "
              "unscheduled_levels={} scheduled_levels={} cutoffs={}".format(
                  homa_profile_source, homa_profile["unscheduled_fraction"],
                  homa_profile["unscheduled_levels"], homa_profile["scheduled_levels"],
                  homa_profile["cutoffs"]))
    homa_overcommit = (args.homa_overcommit if args.homa_overcommit is not None
                       else homa_profile["scheduled_levels"])
    if homa_overcommit > homa_profile["scheduled_levels"]:
        raise Exception(
            "CONFIG ERROR: --homa_overcommit {} exceeds {} scheduled priority levels.".format(
                homa_overcommit, homa_profile["scheduled_levels"]))
    homa_cutoffs = list(homa_profile["cutoffs"])
    homa_cutoffs.extend([0] * (5 - len(homa_cutoffs)))

    # ECN thresholds apply uniformly to every configured link speed.  The
    # defaults preserve all historical runs; explicit overrides support a
    # preregistered safety ladder and are recorded in config.txt.
    kmax_map = "6 %d %d %d %d %d %d %d %d %d %d %d %d" % (
        bw*200000000, ecn_kmax_kb, bw*500000000, ecn_kmax_kb,
        bw*1000000000, ecn_kmax_kb, bw*2*1000000000, ecn_kmax_kb,
        bw*2500000000, ecn_kmax_kb, bw*4*1000000000, ecn_kmax_kb)
    kmin_map = "6 %d %d %d %d %d %d %d %d %d %d %d %d" % (
        bw*200000000, ecn_kmin_kb, bw*500000000, ecn_kmin_kb,
        bw*1000000000, ecn_kmin_kb, bw*2*1000000000, ecn_kmin_kb,
        bw*2500000000, ecn_kmin_kb, bw*4*1000000000, ecn_kmin_kb)
    pmax_map = "6 %d %d %d %d %d %.2f %d %.2f %d %.2f %d %.2f" % (
        bw*200000000, ecn_pmax, bw*500000000, ecn_pmax,
        bw*1000000000, ecn_pmax, bw*2*1000000000, ecn_pmax,
        bw*2500000000, ecn_pmax, bw*4*1000000000, ecn_pmax)

    # queue monitoring
    qlen_mon_start = flowgen_start_time
    qlen_mon_end = flowgen_stop_time

    if 1:  # DCQCN
        ai = 10 * bw / 25
        hai = 25 * bw / 25
        dctcp_ai = 1000
        fast_react = 0
        mi = 0
        int_multi = 1
        ewma_gain = 0.00390625

        config = config_template.format(id=config_ID, topo=topo, flow_file=flow_config_path,
                                        qlen_mon_start=qlen_mon_start, qlen_mon_end=qlen_mon_end, flowgen_start_time=flowgen_start_time,
                                        flowgen_stop_time=flowgen_stop_time, sw_monitoring_interval=sw_monitoring_interval,
                                        qlen_monitoring_interval=qlen_monitoring_interval,
                                        monitor_profile=args.monitor_profile,
                                        analysis_warmup_time=args.analysis_warmup,
                                        max_flows=args.max_flows,
                                        load=netload, buffer_size=buffer, lb_mode=lb_mode, cwh_tx_expiry_time=cwh_tx_expiry_time,
                                        cwh_extra_reply_deadline=cwh_extra_reply_deadline, cwh_default_voq_waiting_time=cwh_default_voq_waiting_time,
                                        cwh_path_pause_time=cwh_path_pause_time, cwh_extra_voq_flush_time=cwh_extra_voq_flush_time,
                                        enabled_pfc=enabled_pfc, enabled_irn=enabled_irn,
                                        error_rate_per_link=args.error_rate_per_link,
                                        cc_mode=cc_mode,
                                        ai=ai, hai=hai, dctcp_ai=dctcp_ai,
                                        has_win=has_win, var_win=var_win,
                                        fast_react=fast_react, mi=mi, int_multi=int_multi, ewma_gain=ewma_gain,
                                        guard_beta=args.guard_beta, guard_gamma=args.guard_gamma,
                                        guard_lambda=args.guard_lambda,
                                        guard_selective_registration=guard_selective_registration,
                                        guard_proactive_release=guard_proactive_release,
                                        guard_keep_last_hop_int=args.guard_keep_last_hop_int,
                                        guard_size_priority=args.guard_size_priority,
                                        guard_work_conserving=args.guard_work_conserving,
                                        guard_rebalance_interval_us=args.guard_rebalance_interval_us,
                                        guard_demand_threshold=args.guard_demand_threshold,
                                        guard_lifecycle_trace=args.guard_lifecycle_trace,
                                        guard_lifecycle_output=guard_lifecycle_output,
                                        guard_lifecycle_max_lines=args.guard_lifecycle_max_lines,
                                        guard_controller_trace=args.guard_controller_trace,
                                        guard_controller_output=guard_controller_output,
                                        guard_controller_max_lines=args.guard_controller_max_lines,
                                        guard_grant_trace=args.guard_grant_trace,
                                        guard_grant_output=guard_grant_output,
                                        guard_grant_max_lines=args.guard_grant_max_lines,
                                        homa_overcommit=homa_overcommit,
                                        homa_resend_timeout_us=args.homa_resend_timeout_us,
                                        homa_unscheduled_levels=homa_profile["unscheduled_levels"],
                                        homa_unscheduled_cutoffs=" ".join(str(value) for value in homa_cutoffs),
                                        seed=args.seed,
                                        kmax_map=kmax_map, kmin_map=kmin_map, pmax_map=pmax_map)
    # else:
    #     print("unknown cc:{}".format(args.cc))

    with open(config_name, "w") as file:
        file.write(config)

    # run program
    print("Running simulation...")
    output_log = config_name.replace(".txt", ".log")
    run_command = "./waf --run 'scratch/network-load-balance {config_name}' > {output_log} 2>&1".format(
        config_name=config_name, output_log=output_log)
    with open("./mix/.history", "a") as history:
        history.write(run_command + "\n")
        history.write(
            "./waf --run 'scratch/network-load-balance' --command-template='gdb --args %s {config_name}'\n".format(
                config_name=config_name)
        )
        history.write("\n")

    print(run_command)
    with open(output_log, "w") as simulation_log:
        simulation = subprocess.run(
            ["./waf", "--run", "scratch/network-load-balance {}".format(config_name)],
            stdout=simulation_log, stderr=subprocess.STDOUT)
    if simulation.returncode != 0:
        raise RuntimeError(
            "ns-3 simulation failed with status {}; see {}".format(
                simulation.returncode, output_log))

    ####################################################
    #                 Analyze the output FCT           #
    ####################################################
    # NOTE: collect data except warm-up and cold-finish period
    fct_analysis_time_limit_begin = int(
        flowgen_start_time * 1e9) + int(args.analysis_warmup * 1e9)
    fct_analysistime_limit_end = int(
        flowgen_stop_time * 1e9) + int(0.05 * 1e9)  # extra term

    if args.smoke:
        print("Skipping formal FCT summary for --smoke run.")
        smoke_summary = os.path.join(
            os.getcwd(), "mix", "output", config_ID,
            "{}_out_fct_summary.txt".format(config_ID))
        with open(smoke_summary, "w") as summary_file:
            summary_file.write("SMOKE_ONLY no formal FCT statistics\n")
    else:
        print("Analyzing output FCT...")
        print("python3 fctAnalysis.py -id {config_ID} -dir {dir} -bdp {bdp} -sT {fct_analysis_time_limit_begin} -fT {fct_analysistime_limit_end} > /dev/null 2>&1".format(
            config_ID=config_ID, dir=os.getcwd(), bdp=bdp, fct_analysis_time_limit_begin=fct_analysis_time_limit_begin, fct_analysistime_limit_end=fct_analysistime_limit_end))
        os.system("python3 fctAnalysis.py -id {config_ID} -dir {dir} -bdp {bdp} -sT {fct_analysis_time_limit_begin} -fT {fct_analysistime_limit_end} > /dev/null 2>&1".format(
            config_ID=config_ID, dir=os.getcwd(), bdp=bdp, fct_analysis_time_limit_begin=fct_analysis_time_limit_begin, fct_analysistime_limit_end=fct_analysistime_limit_end))

    if lb_mode == 9: # ConWeave Logging
        ################################################################
        #             Analyze hardware resource of ConWeave            #
        ################################################################
        # NOTE: collect data except warm-up and cold-finish period
        queue_analysis_time_limit_begin = int(
            flowgen_start_time * 1e9) + int(args.analysis_warmup * 1e9)
        queue_analysistime_limit_end = int(flowgen_stop_time * 1e9)
        print("Analyzing output Queue...")
        print("python3 queueAnalysis.py -id {config_ID} -dir {dir} -sT {queue_analysis_time_limit_begin} -fT {queue_analysistime_limit_end} > /dev/null 2>&1".format(
            config_ID=config_ID, dir=os.getcwd(), queue_analysis_time_limit_begin=queue_analysis_time_limit_begin, queue_analysistime_limit_end=queue_analysistime_limit_end))
        os.system("python3 queueAnalysis.py -id {config_ID} -dir {dir} -sT {queue_analysis_time_limit_begin} -fT {queue_analysistime_limit_end} > /dev/null 2>&1".format(
            config_ID=config_ID, dir=os.getcwd(), queue_analysis_time_limit_begin=queue_analysis_time_limit_begin, queue_analysistime_limit_end=queue_analysistime_limit_end,
            monitoringInterval=sw_monitoring_interval))  # TODO: parameterize

    print("\n\n============== Done ============== ")


if __name__ == "__main__":
    main()
