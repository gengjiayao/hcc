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
{guard_transition_audit_output_config}QUEUE_STATS_OUTPUT_FILE mix/output/{id}/{id}_out_queue_stats.txt
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
GUARD_INITIAL_WINDOW_PRIORITY {guard_initial_window_priority}
GUARD_TRANSPORT_WINDOW_FLOOR_RTT_NS {guard_transport_window_floor_rtt_ns}
GUARD_TRANSPORT_WINDOW_FLOOR_AFTER_FIRST_GRANT {guard_transport_window_floor_after_first_grant}
GUARD_TRANSPORT_WINDOW_WHOLE_FLOW_FIRST_GATE {guard_transport_window_whole_flow_first_gate}
GUARD_TRANSPORT_WINDOW_ACK_SLACK_PACKETS {guard_transport_window_ack_slack_packets}
GUARD_SENDER_SRPT {guard_sender_srpt}
GUARD_ONE_RTT_BYPASS {guard_one_rtt_bypass}
GUARD_TAIL_BYPASS {guard_tail_bypass}
GUARD_TAIL_BYPASS_BDPS {guard_tail_bypass_bdps}
GUARD_TAIL_CONGESTION_GATE {guard_tail_congestion_gate}
GUARD_TAIL_SAFE_RATIO {guard_tail_safe_ratio}
GUARD_TAIL_SAFE_SAMPLES {guard_tail_safe_samples}
GUARD_ADAPTIVE_FABRIC_TARGET {guard_adaptive_fabric_target}
GUARD_TARGET_FLOOR {guard_target_floor}
GUARD_QUEUE_BUDGET_BDPS {guard_queue_budget_bdps}
GUARD_ADAPTIVE_TARGET_MAX_BDPS {guard_adaptive_target_max_bdps}
GUARD_ACK_INTERVAL_PACKETS {guard_ack_interval_packets}
GUARD_FIXED_WINDOW {guard_fixed_window}
GUARD_REMAINING_AWARE {guard_remaining_aware}
GUARD_MIN_SHARE_FRACTION {guard_min_share_fraction}
GUARD_REMAINING_EXPONENT {guard_remaining_exponent}
GUARD_RECEIVER_CONCURRENCY {guard_receiver_concurrency}
GUARD_ADAPTIVE_ELEPHANT_CONCURRENCY {guard_adaptive_elephant_concurrency}
GUARD_SIZE_CLASS_ELEPHANT_CONCURRENCY {guard_size_class_elephant_concurrency}
GUARD_ELEPHANT_AGING_RTTS {guard_elephant_aging_rtts}
GUARD_ELEPHANT_CAP_SPILLOVER {guard_elephant_cap_spillover}
GUARD_CAP_TRIGGERED_REFRESH {guard_cap_triggered_refresh}
GUARD_CAP_REFRESH_MATERIAL_PERCENT {guard_cap_refresh_material_percent}
GUARD_ELEPHANT_SPILLOVER_ENTER_REPORTS {guard_elephant_spillover_enter_reports}
GUARD_ELEPHANT_SPILLOVER_EXIT_REPORTS {guard_elephant_spillover_exit_reports}
GUARD_ELEPHANT_FABRIC_TARGET {guard_elephant_fabric_target}
GUARD_ELEPHANT_FABRIC_TARGET_SCALE {guard_elephant_fabric_target_scale}
GUARD_ELEPHANT_RECEIVER_AUTHORITY {guard_elephant_receiver_authority}
GUARD_CONCURRENCY_MIN_BDPS {guard_concurrency_min_bdps}
GUARD_GRANT_REFRESH_BDPS {guard_grant_refresh_bdps}
GUARD_MEMBERSHIP_COALESCE_NS {guard_membership_coalesce_ns}
GUARD_MEMBERSHIP_COALESCE_MAX_WINDOWS {guard_membership_coalesce_max_windows}
GUARD_INITIAL_COLLECTION_QUIET_NS {guard_initial_collection_quiet_ns}
GUARD_INITIAL_COLLECTION_FULL_DEADLINE {guard_initial_collection_full_deadline}
GUARD_SMALL_SET_FASTPATH_LIMIT {guard_small_set_fastpath_limit}
GUARD_TRANSITION_PREFIX_BARRIER {guard_transition_prefix_barrier}
{guard_transition_prefix_fail_closed_config}{guard_transition_prefix_wire_watchdog_config}{guard_transition_prefix_ack_clock_fallback_config}GUARD_GRANT_RELIABILITY_RTTS {guard_grant_reliability_rtts}
{guard_mixed_pg_vector_fastpath_config}{guard_serialized_progress_refresh_config}{guard_serialized_draining_config}{guard_capacity_admission_deferral_config}GUARD_SRPT_QUANTUM_PACKETS {guard_srpt_quantum_packets}
GUARD_WORK_CONSERVING {guard_work_conserving}
GUARD_CAP_AWARE_RECLAIM {guard_cap_aware_reclaim}
GUARD_CAP_HEADROOM {guard_cap_headroom}
GUARD_CAP_MIN_SHARE_FRACTION {guard_cap_min_share_fraction}
GUARD_REBALANCE_INTERVAL_US {guard_rebalance_interval_us}
GUARD_DEMAND_THRESHOLD {guard_demand_threshold}
GUARD_RECEIVER_UTIL_THRESHOLD {guard_receiver_util_threshold}
GUARD_LIFECYCLE_TRACE {guard_lifecycle_trace}
GUARD_LIFECYCLE_TRACE_MAX_LINES {guard_lifecycle_max_lines}
GUARD_CONTROLLER_TRACE {guard_controller_trace}
GUARD_CONTROLLER_TRACE_MAX_LINES {guard_controller_max_lines}
GUARD_GRANT_TRACE {guard_grant_trace}
GUARD_GRANT_TRACE_MAX_LINES {guard_grant_max_lines}
{guard_transition_audit_config}HOMA_OVERCOMMIT {homa_overcommit}
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
    "leaf_spine_1024_100G_OS1": 104000,  # 2-tier -> all 100Gbps
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
DEFAULT_GUARD_TRANSITION_AUDIT_MAX_RECORDS = 10000
HARD_GUARD_TRANSITION_AUDIT_MAX_RECORDS = 10000
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


def validate_guard_transition_audit_options(enabled, cc, output, max_records,
                                            prefix_barrier):
    """Keep per-transition evidence applicable, explicit, and bounded."""
    if not 1 <= max_records <= HARD_GUARD_TRANSITION_AUDIT_MAX_RECORDS:
        raise ValueError(
            "--guard_transition_audit_max_records must be in [1, {}]".format(
                HARD_GUARD_TRANSITION_AUDIT_MAX_RECORDS))
    if enabled and cc != "guard":
        raise ValueError("--guard_transition_audit requires full GUARD mode")
    if enabled and not prefix_barrier:
        raise ValueError(
            "--guard_transition_audit requires --guard_transition_prefix_barrier 1")
    if not enabled and output:
        raise ValueError(
            "--guard_transition_audit_output requires --guard_transition_audit 1")


def validate_guard_transition_audit_output_path(enabled, output,
                                                reserved_outputs):
    """Require a dedicated audit artifact instead of aliasing another file."""
    if not enabled:
        return
    normalized_output = os.path.realpath(os.path.abspath(output))
    for label, reserved in reserved_outputs.items():
        if normalized_output == os.path.realpath(os.path.abspath(reserved)):
            raise ValueError(
                "--guard_transition_audit_output collides with {}".format(label))


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
    parser.add_argument('--guard_lambda', type=float, default=1.8,
                        help="GUARD HPCC-target multiplier >= 1 (default: 1.8)")
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
    parser.add_argument('--guard_initial_window_priority', type=int, choices=(0, 1), default=0,
                        help="serve only GUARD's initial BDP at unscheduled priority (default: 0)")
    parser.add_argument('--guard_transport_window_floor_rtt_ns', type=int, default=0,
                        help="minimum RTT-equivalent GUARD transport window in ns (default: 0)")
    parser.add_argument('--guard_transport_window_floor_after_first_grant',
                        type=int, choices=(0, 1), default=0,
                        help="apply the GUARD window floor only after its first grant (default: 0)")
    parser.add_argument('--guard_transport_window_whole_flow_first_gate',
                        type=int, choices=(0, 1), default=0,
                        help="use the floor before grant only for whole flows that fit (default: 0)")
    parser.add_argument('--guard_transport_window_ack_slack_packets',
                        type=int, default=0,
                        help="cap the floor at path BDP plus this many MTU packets (default: 0)")
    parser.add_argument('--guard_sender_srpt', type=int, choices=(0, 1), default=1,
                        help="select shortest remaining ready GUARD flow at sender (default: 1)")
    parser.add_argument('--guard_one_rtt_bypass', type=int, choices=(0, 1), default=1,
                        help="keep <=1-BDP GUARD flows at line rate (default: 1)")
    parser.add_argument('--guard_tail_bypass', type=int, choices=(0, 1), default=1,
                        help="pace the final acknowledged BDP only by the receiver cap (default: 1)")
    parser.add_argument('--guard_tail_bypass_bdps', type=float, default=8.0,
                        help="acknowledged BDPs remaining at tail bypass, in [1,16] (default: 8)")
    parser.add_argument('--guard_tail_congestion_gate', type=int, choices=(0, 1), default=0,
                        help="require safe fabric samples before tail bypass (default: 0)")
    parser.add_argument('--guard_tail_safe_ratio', type=float, default=0.9,
                        help="safe HPCC utilization/target ratio in [0.5,1] (default: 0.9)")
    parser.add_argument('--guard_tail_safe_samples', type=int, default=2,
                        help="consecutive safe samples before tail bypass in [1,8] (default: 2)")
    parser.add_argument('--guard_adaptive_fabric_target', type=int, choices=(0, 1), default=0,
                        help="lower GUARD's fabric target as queued BDPs grow (default: 0)")
    parser.add_argument('--guard_target_floor', type=float, default=0.95,
                        help="minimum adaptive fabric target in [0.5,1] (default: 0.95)")
    parser.add_argument('--guard_queue_budget_bdps', type=float, default=0.5,
                        help="queued BDPs for full target rollback in [0.01,4] (default: 0.5)")
    parser.add_argument('--guard_adaptive_target_max_bdps', type=float, default=0.0,
                        help="largest flow using adaptive targets in BDPs; 0 means all")
    parser.add_argument('--guard_ack_interval_packets', type=int, default=8,
                        help="cumulative ACK interval for registered GUARD flows (default: 8)")
    parser.add_argument('--guard_fixed_window', type=int, choices=(0, 1), default=1,
                        help="use a fixed BDP safety window under GUARD rate pacing (default: 1)")
    parser.add_argument('--guard_remaining_aware', type=int, choices=(0, 1), default=1,
                        help="weight receiver grants by remaining flow size (default: 1)")
    parser.add_argument('--guard_min_share_fraction', type=float, default=0.0,
                        help="guaranteed fraction of equal receiver share in [0,1] (default: 0)")
    parser.add_argument('--guard_remaining_exponent', type=float, default=1.0,
                        help="inverse-remaining-size exponent in [0,2] (default: 1)")
    parser.add_argument('--guard_receiver_concurrency', type=int, default=0,
                        help="registered flows served above MinRate; 0 serves all (default: 0)")
    parser.add_argument('--guard_adaptive_elephant_concurrency', type=int,
                        choices=(0, 1), default=0,
                        help="use floor-sqrt elephant width capped by receiver concurrency")
    parser.add_argument('--guard_size_class_elephant_concurrency', type=int,
                        choices=(0, 1), default=0,
                        help="use K=2 only for the same dyadic remaining-size class")
    parser.add_argument('--guard_elephant_aging_rtts', type=float, default=0.0,
                        help="deferred-elephant wait before one K=1 service quantum; 0 disables")
    parser.add_argument('--guard_elephant_cap_spillover', type=int,
                        choices=(0, 1), default=0,
                        help="spill only a confirmed fabric-limited K=1 elephant share")
    parser.add_argument('--guard_cap_triggered_refresh', type=int,
                        choices=(0, 1), default=0,
                        help="refresh frozen K=1 targets on material fabric-cap changes")
    parser.add_argument('--guard_cap_refresh_material_percent', type=int,
                        default=5,
                        help="relative cap change required for a serialized refresh [1,20]")
    parser.add_argument('--guard_elephant_spillover_enter_reports', type=int,
                        default=3,
                        help="fabric-bound reports required to enter spillover [1,8]")
    parser.add_argument('--guard_elephant_spillover_exit_reports', type=int,
                        default=1,
                        help="unbound reports required to exit spillover [1,8]")
    parser.add_argument('--guard_elephant_fabric_target', type=int,
                        choices=(0, 1), default=0,
                        help="scale the HPCC target only for configured elephants")
    parser.add_argument('--guard_elephant_fabric_target_scale', type=float,
                        default=1.0,
                        help="eligible-elephant HPCC target scale [1,2]")
    parser.add_argument('--guard_elephant_receiver_authority', type=int,
                        choices=(0, 1), default=0,
                        help="let a selected >threshold elephant follow its receiver cap")
    parser.add_argument('--guard_concurrency_min_bdps', type=float, default=8.0,
                        help="apply receiver concurrency bound only above this many BDPs (default: 8)")
    parser.add_argument('--guard_grant_refresh_bdps', type=float, default=1.0,
                        help="receiver progress between grant refreshes in BDPs; 0 disables (default: 1)")
    parser.add_argument('--guard_membership_coalesce_ns', type=int, default=0,
                        help="bounded registered-set quiet period in ns [0,65536] (default: 0)")
    parser.add_argument('--guard_initial_collection_quiet_ns', type=int, default=0,
                        help="initial collection quiet period; 0 inherits membership quiet [0, 65536]")
    parser.add_argument('--guard_membership_coalesce_max_windows', type=int, default=5,
                        help="hard batch deadline in coalescing-window multiples [1,16] (default: 5)")
    parser.add_argument('--guard_initial_collection_full_deadline', type=int,
                        choices=(0, 1), default=1,
                        help="wait to the full initial deadline; 0 uses bounded sliding quiet (default: 1)")
    parser.add_argument('--guard_small_set_fastpath_limit', type=int, default=0,
                        help="V11 serialized small-set limit; 0 disables, 4 enables (default: 0)")
    parser.add_argument('--guard_transition_prefix_barrier', type=int,
                        choices=(0, 1), default=0,
                        help="V12 exact-prefix transition barrier (default: 0)")
    parser.add_argument('--guard_transition_prefix_wire_watchdog', type=int,
                        choices=(0, 1), default=0,
                        help="V13 wire/residual-capacity prefix watchdog (default: 0)")
    parser.add_argument('--guard_transition_prefix_fail_closed', type=int,
                        choices=(0, 1), default=0,
                        help="V14 abort-on-prefix-deadline policy (default: 0)")
    parser.add_argument('--guard_transition_prefix_ack_clock_fallback', type=int,
                        choices=(0, 1), default=0,
                        help="V15 required-ACK-clocked mixed-PG timeout fallback (default: 0)")
    parser.add_argument('--guard_mixed_pg_vector_fastpath', type=int,
                        choices=(0, 1), default=0,
                        help="V14 canonical vector across receiver priority groups (default: 0)")
    parser.add_argument('--guard_serialized_progress_refresh', type=int,
                        choices=(0, 1), default=0,
                        help="V14 serialized remaining-aware progress refresh (default: 0)")
    parser.add_argument('--guard_serialized_draining', type=int,
                        choices=(0, 1), default=0,
                        help="V14 capacity reservation for proactive draining (default: 0)")
    parser.add_argument('--guard_capacity_admission_deferral', type=int,
                        choices=(0, 1), default=0,
                        help="V18 wait for draining capacity before admitting a frozen cohort (default: 0)")
    parser.add_argument('--guard_grant_reliability_rtts', type=float, default=0.0,
                        help="cached grant refresh in equal-share service rounds [0,64] (default: 0)")
    parser.add_argument('--guard_srpt_quantum_packets', type=int, default=64,
                        help="consecutive SRPT packet bound before RR service (default: 64)")
    parser.add_argument('--guard_work_conserving', type=int, choices=(0, 1), default=0,
                        help="enable experimental unused-share reclamation (default: 0)")
    parser.add_argument('--guard_cap_aware_reclaim', type=int, choices=(0, 1), default=0,
                        help="reclaim shares using bounded sender cap reports (default: 0)")
    parser.add_argument('--guard_cap_headroom', type=float, default=1.1,
                        help="headroom above reported fabric caps in [1,2] (default: 1.1)")
    parser.add_argument('--guard_cap_min_share_fraction', type=float, default=0.25,
                        help="donor safety floor as equal-share fraction in [0,1] (default: 0.25)")
    parser.add_argument('--guard_rebalance_interval_us', type=int, default=200,
                        help="GUARD receiver demand-sampling interval in us (default: 200)")
    parser.add_argument('--guard_demand_threshold', type=float, default=0.75,
                        help="arrival/grant ratio below which a share is reclaimable (default: 0.75)")
    parser.add_argument('--guard_receiver_util_threshold', type=float, default=0.75,
                        help="aggregate receiver utilization gate for share reclamation (default: 0.75)")
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
    parser.add_argument('--guard_transition_audit', type=int, choices=(0, 1), default=0,
                        help="write bounded per-transition GUARD audit records (default: 0)")
    parser.add_argument('--guard_transition_audit_output', type=str,
                        help="transition audit CSV path (default: the run output directory)")
    parser.add_argument('--guard_transition_audit_max_records', type=int,
                        default=DEFAULT_GUARD_TRANSITION_AUDIT_MAX_RECORDS,
                        help="maximum transition records (default/hard maximum: 10000)")
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
    if not 1.0 <= args.guard_cap_headroom <= 2.0:
        raise Exception("CONFIG ERROR: --guard_cap_headroom must be in [1, 2].")
    if not 0.0 <= args.guard_cap_min_share_fraction <= 1.0:
        raise Exception("CONFIG ERROR: --guard_cap_min_share_fraction must be in [0, 1].")
    if args.guard_srpt_quantum_packets <= 0:
        raise Exception("CONFIG ERROR: --guard_srpt_quantum_packets must be positive.")
    if args.guard_ack_interval_packets <= 0:
        raise Exception("CONFIG ERROR: --guard_ack_interval_packets must be positive.")
    if not 1.0 <= args.guard_tail_bypass_bdps <= 16.0:
        raise Exception("CONFIG ERROR: --guard_tail_bypass_bdps must be in [1, 16].")
    if not 0.5 <= args.guard_tail_safe_ratio <= 1.0:
        raise Exception("CONFIG ERROR: --guard_tail_safe_ratio must be in [0.5, 1].")
    if not 1 <= args.guard_tail_safe_samples <= 8:
        raise Exception("CONFIG ERROR: --guard_tail_safe_samples must be in [1, 8].")
    if not 0.5 <= args.guard_target_floor <= 1.0:
        raise Exception("CONFIG ERROR: --guard_target_floor must be in [0.5, 1].")
    if not 0.01 <= args.guard_queue_budget_bdps <= 4.0:
        raise Exception("CONFIG ERROR: --guard_queue_budget_bdps must be in [0.01, 4].")
    if not 0.0 <= args.guard_adaptive_target_max_bdps <= 64.0:
        raise Exception("CONFIG ERROR: --guard_adaptive_target_max_bdps must be in [0, 64].")
    if not 0.0 <= args.guard_min_share_fraction <= 1.0:
        raise Exception("CONFIG ERROR: --guard_min_share_fraction must be in [0, 1].")
    if not 0.0 <= args.guard_remaining_exponent <= 2.0:
        raise Exception("CONFIG ERROR: --guard_remaining_exponent must be in [0, 2].")
    if args.guard_receiver_concurrency < 0:
        raise Exception("CONFIG ERROR: --guard_receiver_concurrency must be non-negative.")
    if args.guard_adaptive_elephant_concurrency and args.guard_receiver_concurrency < 2:
        raise Exception(
            "CONFIG ERROR: adaptive elephant concurrency requires receiver concurrency >= 2.")
    if args.guard_size_class_elephant_concurrency:
        if args.guard_receiver_concurrency != 2:
            raise Exception(
                "CONFIG ERROR: size-class elephant concurrency requires receiver concurrency 2.")
        if args.guard_adaptive_elephant_concurrency:
            raise Exception(
                "CONFIG ERROR: adaptive and size-class elephant concurrency are exclusive.")
    if not 0.0 <= args.guard_elephant_aging_rtts <= 64.0:
        raise Exception("CONFIG ERROR: --guard_elephant_aging_rtts must be in [0, 64].")
    if args.guard_elephant_aging_rtts > 0.0:
        if args.guard_receiver_concurrency != 1:
            raise Exception("CONFIG ERROR: elephant aging requires receiver concurrency 1.")
        if (args.guard_adaptive_elephant_concurrency or
                args.guard_size_class_elephant_concurrency):
            raise Exception("CONFIG ERROR: elephant aging and adaptive concurrency are exclusive.")
    if args.guard_elephant_cap_spillover:
        if args.guard_receiver_concurrency != 1:
            raise Exception("CONFIG ERROR: elephant cap spillover requires receiver concurrency 1.")
        if (args.guard_adaptive_elephant_concurrency or
                args.guard_size_class_elephant_concurrency or
                args.guard_elephant_aging_rtts > 0.0):
            raise Exception(
                "CONFIG ERROR: elephant cap spillover is exclusive with adaptive concurrency and aging.")
    if args.guard_cap_triggered_refresh:
        if (args.cc != "guard" or args.guard_receiver_concurrency != 1 or
                args.guard_adaptive_elephant_concurrency or
                args.guard_size_class_elephant_concurrency or
                args.guard_elephant_aging_rtts > 0.0 or
                args.guard_elephant_cap_spillover or
                not args.guard_remaining_aware or
                not args.guard_mixed_pg_vector_fastpath or
                not args.guard_serialized_progress_refresh or
                not args.guard_serialized_draining or
                not args.guard_capacity_admission_deferral):
            raise Exception(
                "CONFIG ERROR: cap-triggered refresh requires final K=1 GUARD "
                "and other elephant adaptations disabled.")
    if not 1 <= args.guard_cap_refresh_material_percent <= 20:
        raise Exception(
            "CONFIG ERROR: --guard_cap_refresh_material_percent must be in [1, 20].")
    if (not args.guard_cap_triggered_refresh and
            args.guard_cap_refresh_material_percent != 5):
        raise Exception(
            "CONFIG ERROR: a non-default cap-refresh threshold requires "
            "--guard_cap_triggered_refresh 1.")
    if not 1 <= args.guard_elephant_spillover_enter_reports <= 8:
        raise Exception(
            "CONFIG ERROR: --guard_elephant_spillover_enter_reports must be in [1, 8].")
    if not 1 <= args.guard_elephant_spillover_exit_reports <= 8:
        raise Exception(
            "CONFIG ERROR: --guard_elephant_spillover_exit_reports must be in [1, 8].")
    if not 1.0 <= args.guard_elephant_fabric_target_scale <= 2.0:
        raise Exception(
            "CONFIG ERROR: --guard_elephant_fabric_target_scale must be in [1, 2].")
    if args.guard_elephant_fabric_target:
        if (args.cc != "guard" or args.guard_adaptive_fabric_target or
                args.guard_elephant_cap_spillover or
                args.guard_cap_triggered_refresh or
                args.guard_adaptive_elephant_concurrency or
                args.guard_size_class_elephant_concurrency or
                args.guard_elephant_aging_rtts > 0.0 or
                args.guard_receiver_concurrency != 1 or
                not args.guard_remaining_aware or
                not args.guard_mixed_pg_vector_fastpath or
                not args.guard_serialized_progress_refresh or
                not args.guard_serialized_draining or
                not args.guard_capacity_admission_deferral):
            raise Exception(
                "CONFIG ERROR: elephant fabric target requires K=1 final GUARD, "
                "the V18/V14 bundle, and other fabric/elephant adaptations disabled.")
    if args.guard_elephant_receiver_authority:
        if (args.cc != "guard" or args.guard_adaptive_fabric_target or
                args.guard_elephant_fabric_target or
                args.guard_elephant_cap_spillover or
                args.guard_cap_triggered_refresh or
                args.guard_adaptive_elephant_concurrency or
                args.guard_size_class_elephant_concurrency or
                args.guard_elephant_aging_rtts > 0.0 or
                args.guard_receiver_concurrency != 1 or
                not args.guard_remaining_aware or
                not args.guard_mixed_pg_vector_fastpath or
                not args.guard_serialized_progress_refresh or
                not args.guard_serialized_draining or
                not args.guard_capacity_admission_deferral):
            raise Exception(
                "CONFIG ERROR: elephant receiver authority requires K=1 final GUARD, "
                "the V18/V14 bundle, and other fabric/elephant adaptations disabled.")
    if not 0.0 <= args.guard_concurrency_min_bdps <= 64.0:
        raise Exception("CONFIG ERROR: --guard_concurrency_min_bdps must be in [0, 64].")
    if not 0.0 <= args.guard_grant_refresh_bdps <= 16.0:
        raise Exception("CONFIG ERROR: --guard_grant_refresh_bdps must be in [0, 16].")
    if not 0 <= args.guard_membership_coalesce_ns <= 65536:
        raise Exception("CONFIG ERROR: --guard_membership_coalesce_ns must be in [0, 65536].")
    if not 0 <= args.guard_initial_collection_quiet_ns <= 65536:
        raise Exception("CONFIG ERROR: --guard_initial_collection_quiet_ns must be in [0, 65536].")
    if not 1 <= args.guard_membership_coalesce_max_windows <= 16:
        raise Exception("CONFIG ERROR: --guard_membership_coalesce_max_windows must be in [1, 16].")
    if args.guard_small_set_fastpath_limit not in (0, 4):
        raise Exception("CONFIG ERROR: --guard_small_set_fastpath_limit must be 0 or 4.")
    if not 0.0 <= args.guard_grant_reliability_rtts <= 64.0:
        raise Exception("CONFIG ERROR: --guard_grant_reliability_rtts must be in [0, 64].")
    if args.guard_membership_coalesce_ns > 0:
        if args.guard_fixed_window != 1:
            raise Exception("CONFIG ERROR: membership coalescing requires --guard_fixed_window 1.")
        if args.guard_grant_reliability_rtts < 1.0:
            raise Exception("CONFIG ERROR: membership coalescing requires grant refresh >= 1 RTT.")
        if (args.guard_proactive_release == 1 and
                not args.guard_serialized_progress_refresh):
            raise Exception(
                "CONFIG ERROR: membership coalescing permits proactive release "
                "only through the V14 serialized draining coordinator.")
        if ((args.guard_grant_refresh_bdps > 0 and
             not args.guard_serialized_progress_refresh) or
                args.guard_work_conserving or args.guard_cap_aware_reclaim):
            raise Exception(
                "CONFIG ERROR: membership coalescing requires serialized V14 refresh "
                "or zero grant refresh, and forbids work-conserving/cap-aware reclaim.")
    if args.guard_small_set_fastpath_limit:
        if (args.guard_membership_coalesce_ns != 12480 or
                args.guard_initial_collection_quiet_ns != 16640 or
                args.guard_membership_coalesce_max_windows != 5 or
                args.guard_initial_collection_full_deadline != 0):
            raise Exception(
                "CONFIG ERROR: V11 small-set fast path requires membership quiet 12480 ns, "
                "initial quiet 16640 ns, five windows, and sliding initial mode.")
        frozen_concurrency = (
            args.guard_receiver_concurrency > 0 and
            args.guard_mixed_pg_vector_fastpath and
            args.guard_serialized_progress_refresh and
            args.guard_serialized_draining and
            args.guard_capacity_admission_deferral)
        if ((args.guard_remaining_aware != 0 and
             not args.guard_serialized_progress_refresh) or
                (args.guard_receiver_concurrency != 0 and
                 not frozen_concurrency)):
            raise Exception(
                "CONFIG ERROR: V11 small-set fast path permits remaining-aware grants "
                "and bounded receiver concurrency only through the complete "
                "V18 canonical-vector bundle.")
    if args.guard_transition_prefix_barrier and (
            args.guard_small_set_fastpath_limit != 4 or
            args.guard_fixed_window != 1):
        raise Exception(
            "CONFIG ERROR: V12 transition prefix barrier requires the V11 safe "
            "activation with limit 4 and --guard_fixed_window 1.")
    if (args.guard_transition_prefix_wire_watchdog and
            not args.guard_transition_prefix_barrier):
        raise Exception(
            "CONFIG ERROR: V13 prefix wire watchdog requires "
            "--guard_transition_prefix_barrier 1.")
    if (args.guard_transition_prefix_fail_closed and
            not args.guard_transition_prefix_wire_watchdog):
        raise Exception(
            "CONFIG ERROR: V14 fail-closed prefix deadlines require "
            "--guard_transition_prefix_wire_watchdog 1.")
    if args.guard_transition_prefix_fail_closed and args.cc != "guard":
        raise Exception(
            "CONFIG ERROR: V14 fail-closed prefix deadlines require full GUARD mode.")
    if args.guard_transition_prefix_ack_clock_fallback and (
            not args.guard_transition_prefix_wire_watchdog or
            args.guard_transition_prefix_fail_closed or args.cc != "guard"):
        raise Exception(
            "CONFIG ERROR: V15 ACK-clocked prefix fallback requires full GUARD, "
            "the wire watchdog, and fail-closed mode disabled.")
    if (args.guard_mixed_pg_vector_fastpath and
            (args.cc != "guard" or
             not (args.guard_transition_prefix_fail_closed or
                  args.guard_transition_prefix_ack_clock_fallback))):
        raise Exception(
            "CONFIG ERROR: V14 mixed-PG vector fast path requires full GUARD "
            "and either the V14 fail-closed policy or the V15 ACK-clocked fallback.")
    if args.guard_serialized_progress_refresh != args.guard_serialized_draining:
        raise Exception(
            "CONFIG ERROR: V14 refresh/draining must be enabled as one fail-closed bundle.")
    if args.guard_serialized_progress_refresh and (
            not args.guard_mixed_pg_vector_fastpath or
            args.guard_remaining_aware != 1 or
            args.guard_grant_refresh_bdps <= 0 or
            args.guard_proactive_release != 1 or args.cc != "guard"):
        raise Exception(
            "CONFIG ERROR: V14 refresh/draining requires full GUARD, mixed-PG vector, "
            "remaining-aware refresh, and proactive release.")
    if args.guard_capacity_admission_deferral and (
            not args.guard_serialized_draining or
            not args.guard_mixed_pg_vector_fastpath or
            args.guard_small_set_fastpath_limit != 4 or args.cc != "guard"):
        raise Exception(
            "CONFIG ERROR: V18 capacity admission deferral requires full GUARD, "
            "serialized draining, and mixed-vector limit 4.")
    if args.guard_elephant_aging_rtts > 0.0 and (
            args.cc != "guard" or args.guard_remaining_aware != 1 or
            not args.guard_mixed_pg_vector_fastpath or
            not args.guard_serialized_progress_refresh or
            not args.guard_serialized_draining or
            not args.guard_capacity_admission_deferral):
        raise Exception(
            "CONFIG ERROR: elephant aging requires the complete V18 "
            "canonical-vector bundle.")
    if args.guard_elephant_cap_spillover and (
            args.cc != "guard" or args.guard_remaining_aware != 1 or
            not args.guard_mixed_pg_vector_fastpath or
            not args.guard_serialized_progress_refresh or
            not args.guard_serialized_draining or
            not args.guard_capacity_admission_deferral or
            args.guard_cap_aware_reclaim or args.guard_work_conserving):
        raise Exception(
            "CONFIG ERROR: elephant cap spillover requires fixed K=1, the complete V18 "
            "canonical-vector bundle, and legacy reclaim disabled.")
    if args.guard_initial_window_priority and (
            args.cc != "guard" or args.guard_size_priority != 1):
        raise Exception(
            "CONFIG ERROR: --guard_initial_window_priority requires GUARD size priority.")
    if args.guard_transport_window_floor_rtt_ns < 0:
        raise Exception(
            "CONFIG ERROR: --guard_transport_window_floor_rtt_ns must be nonnegative.")
    if args.guard_transport_window_floor_rtt_ns and (
            args.cc != "guard" or not args.guard_fixed_window or
            not args.guard_transition_prefix_barrier):
        raise Exception(
            "CONFIG ERROR: the GUARD transport-window floor requires GUARD, a fixed "
            "window, and the exact transition-prefix barrier.")
    if (args.guard_transport_window_floor_after_first_grant and
            not args.guard_transport_window_floor_rtt_ns):
        raise Exception(
            "CONFIG ERROR: --guard_transport_window_floor_after_first_grant "
            "requires a positive transport-window floor.")
    if (args.guard_transport_window_whole_flow_first_gate and
            (not args.guard_transport_window_floor_after_first_grant or
             not args.guard_transport_window_floor_rtt_ns)):
        raise Exception(
            "CONFIG ERROR: --guard_transport_window_whole_flow_first_gate "
            "requires a positive post-grant transport-window floor.")
    if args.guard_transport_window_ack_slack_packets < 0:
        raise Exception(
            "CONFIG ERROR: --guard_transport_window_ack_slack_packets must be nonnegative.")
    if (args.guard_transport_window_ack_slack_packets and
            (not args.guard_transport_window_whole_flow_first_gate or
             not args.guard_transport_window_floor_after_first_grant or
             not args.guard_transport_window_floor_rtt_ns)):
        raise Exception(
            "CONFIG ERROR: --guard_transport_window_ack_slack_packets requires "
            "the positive bounded whole-flow post-grant window.")
    if not 0.0 < args.guard_demand_threshold < 1.0:
        raise Exception("CONFIG ERROR: --guard_demand_threshold must be in (0, 1).")
    if not 0.0 < args.guard_receiver_util_threshold < 1.0:
        raise Exception("CONFIG ERROR: --guard_receiver_util_threshold must be in (0, 1).")
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
        validate_guard_transition_audit_options(
            args.guard_transition_audit, args.cc,
            args.guard_transition_audit_output,
            args.guard_transition_audit_max_records,
            args.guard_transition_prefix_barrier)
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
    guard_transition_audit_output = args.guard_transition_audit_output
    if guard_transition_audit_output is None:
        guard_transition_audit_output = os.path.join(
            output_dir, "{}_out_guard_transition_audit.csv".format(config_ID))
    else:
        guard_transition_audit_output = os.path.abspath(
            os.path.expanduser(guard_transition_audit_output))
    if any(character.isspace() for character in guard_transition_audit_output):
        raise Exception(
            "CONFIG ERROR: --guard_transition_audit_output cannot contain whitespace.")
    if args.guard_transition_audit:
        reserved_outputs = {
            "traffic input": flow_path,
            "topology input": os.path.join(
                os.getcwd(), "config", "{}.txt".format(args.topo)),
            "config": os.path.join(output_dir, "config.txt"),
            "simulation log": os.path.join(output_dir, "config.log"),
            "lifecycle trace": guard_lifecycle_output,
            "controller trace": guard_controller_output,
            "grant trace": guard_grant_output,
        }
        if custom_flow_source is not None:
            reserved_outputs["custom traffic source"] = custom_flow_source
        for suffix in (
                "in.txt", "flow_bw.txt", "out_bw.txt", "out_cnp.txt",
                "out_fct.txt", "out_pfc.txt", "out_guard_stats.txt",
                "out_queue_stats.txt", "out_qlen.txt", "out_voq.txt",
                "out_voq_per_dst.txt", "out_uplink.txt", "out_conn.txt",
                "out_est_error.txt"):
            reserved_outputs[suffix] = os.path.join(
                output_dir, "{}_{}".format(config_ID, suffix))
        try:
            validate_guard_transition_audit_output_path(
                True, guard_transition_audit_output, reserved_outputs)
        except ValueError as error:
            raise Exception("CONFIG ERROR: {}.".format(error))
        output_parent = os.path.dirname(guard_transition_audit_output)
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
        print("ERROR - topology is not registered in run.py!!")
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
                                        guard_initial_window_priority=args.guard_initial_window_priority,
                                        guard_transport_window_floor_rtt_ns=
                                            args.guard_transport_window_floor_rtt_ns,
                                        guard_transport_window_floor_after_first_grant=
                                            args.guard_transport_window_floor_after_first_grant,
                                        guard_transport_window_whole_flow_first_gate=
                                            args.guard_transport_window_whole_flow_first_gate,
                                        guard_transport_window_ack_slack_packets=
                                            args.guard_transport_window_ack_slack_packets,
                                        guard_sender_srpt=args.guard_sender_srpt,
                                        guard_one_rtt_bypass=args.guard_one_rtt_bypass,
                                        guard_tail_bypass=args.guard_tail_bypass,
                                        guard_tail_bypass_bdps=args.guard_tail_bypass_bdps,
                                        guard_tail_congestion_gate=args.guard_tail_congestion_gate,
                                        guard_tail_safe_ratio=args.guard_tail_safe_ratio,
                                        guard_tail_safe_samples=args.guard_tail_safe_samples,
                                        guard_adaptive_fabric_target=args.guard_adaptive_fabric_target,
                                        guard_target_floor=args.guard_target_floor,
                                        guard_queue_budget_bdps=args.guard_queue_budget_bdps,
                                        guard_adaptive_target_max_bdps=args.guard_adaptive_target_max_bdps,
                                        guard_ack_interval_packets=args.guard_ack_interval_packets,
                                        guard_fixed_window=args.guard_fixed_window,
                                        guard_remaining_aware=args.guard_remaining_aware,
                                        guard_min_share_fraction=args.guard_min_share_fraction,
                                        guard_remaining_exponent=args.guard_remaining_exponent,
                                        guard_receiver_concurrency=args.guard_receiver_concurrency,
                                        guard_adaptive_elephant_concurrency=args.guard_adaptive_elephant_concurrency,
                                        guard_size_class_elephant_concurrency=
                                            args.guard_size_class_elephant_concurrency,
                                        guard_elephant_aging_rtts=args.guard_elephant_aging_rtts,
                                        guard_elephant_cap_spillover=args.guard_elephant_cap_spillover,
                                        guard_cap_triggered_refresh=
                                            args.guard_cap_triggered_refresh,
                                        guard_cap_refresh_material_percent=
                                            args.guard_cap_refresh_material_percent,
                                        guard_elephant_spillover_enter_reports=args.guard_elephant_spillover_enter_reports,
                                        guard_elephant_spillover_exit_reports=args.guard_elephant_spillover_exit_reports,
                                        guard_elephant_fabric_target=args.guard_elephant_fabric_target,
                                        guard_elephant_fabric_target_scale=args.guard_elephant_fabric_target_scale,
                                        guard_elephant_receiver_authority=args.guard_elephant_receiver_authority,
                                        guard_concurrency_min_bdps=args.guard_concurrency_min_bdps,
                                        guard_grant_refresh_bdps=args.guard_grant_refresh_bdps,
                                        guard_membership_coalesce_ns=args.guard_membership_coalesce_ns,
                                        guard_membership_coalesce_max_windows=args.guard_membership_coalesce_max_windows,
                                        guard_initial_collection_quiet_ns=args.guard_initial_collection_quiet_ns,
                                        guard_initial_collection_full_deadline=args.guard_initial_collection_full_deadline,
                                        guard_small_set_fastpath_limit=args.guard_small_set_fastpath_limit,
                                        guard_transition_prefix_barrier=args.guard_transition_prefix_barrier,
                                        guard_transition_prefix_wire_watchdog_config=(
                                            "GUARD_TRANSITION_PREFIX_WIRE_WATCHDOG 1\n"
                                            if args.guard_transition_prefix_wire_watchdog else ""),
                                        guard_transition_prefix_fail_closed_config=(
                                            "GUARD_TRANSITION_PREFIX_FAIL_CLOSED 1\n"
                                            if args.guard_transition_prefix_fail_closed else ""),
                                        guard_transition_prefix_ack_clock_fallback_config=(
                                            "GUARD_TRANSITION_PREFIX_ACK_CLOCK_FALLBACK 1\n"
                                            if args.guard_transition_prefix_ack_clock_fallback else ""),
                                        guard_mixed_pg_vector_fastpath_config=(
                                            "GUARD_MIXED_PG_VECTOR_FASTPATH 1\n"
                                            if args.guard_mixed_pg_vector_fastpath else ""),
                                        guard_serialized_progress_refresh_config=(
                                            "GUARD_SERIALIZED_PROGRESS_REFRESH 1\n"
                                            if args.guard_serialized_progress_refresh else ""),
                                        guard_serialized_draining_config=(
                                            "GUARD_SERIALIZED_DRAINING 1\n"
                                            if args.guard_serialized_draining else ""),
                                        guard_capacity_admission_deferral_config=(
                                            "GUARD_CAPACITY_ADMISSION_DEFERRAL 1\n"
                                            if args.guard_capacity_admission_deferral else ""),
                                        guard_grant_reliability_rtts=args.guard_grant_reliability_rtts,
                                        guard_srpt_quantum_packets=args.guard_srpt_quantum_packets,
                                        guard_work_conserving=args.guard_work_conserving,
                                        guard_cap_aware_reclaim=args.guard_cap_aware_reclaim,
                                        guard_cap_headroom=args.guard_cap_headroom,
                                        guard_cap_min_share_fraction=args.guard_cap_min_share_fraction,
                                        guard_rebalance_interval_us=args.guard_rebalance_interval_us,
                                        guard_demand_threshold=args.guard_demand_threshold,
                                        guard_receiver_util_threshold=args.guard_receiver_util_threshold,
                                        guard_lifecycle_trace=args.guard_lifecycle_trace,
                                        guard_lifecycle_output=guard_lifecycle_output,
                                        guard_lifecycle_max_lines=args.guard_lifecycle_max_lines,
                                        guard_controller_trace=args.guard_controller_trace,
                                        guard_controller_output=guard_controller_output,
                                        guard_controller_max_lines=args.guard_controller_max_lines,
                                        guard_grant_trace=args.guard_grant_trace,
                                        guard_grant_output=guard_grant_output,
                                        guard_grant_max_lines=args.guard_grant_max_lines,
                                        guard_transition_audit_output_config=(
                                            "GUARD_TRANSITION_AUDIT_OUTPUT_FILE {}\n".format(
                                                guard_transition_audit_output)
                                            if args.guard_transition_audit else ""),
                                        guard_transition_audit_config=(
                                            "GUARD_TRANSITION_AUDIT 1\n"
                                            "GUARD_TRANSITION_AUDIT_MAX_RECORDS {}\n".format(
                                                args.guard_transition_audit_max_records)
                                            if args.guard_transition_audit else ""),
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
