#include "rdma-hw.h"

#include <ns3/ipv4-header.h>
#include <ns3/seq-ts-header.h>
#include <ns3/simulator.h>
#include <ns3/udp-header.h>

#include <climits>
#include <cmath>
#include <tuple>

#include "cn-header.h"
#include "flow-stat-tag.h"
#include "ns3/boolean.h"
#include "ns3/data-rate.h"
#include "ns3/double.h"
#include "ns3/flow-id-num-tag.h"
#include "ns3/pointer.h"
#include "ns3/ppp-header.h"
#include "ns3/settings.h"
#include "ns3/switch-node.h"
#include "ns3/uinteger.h"
#include "homa-simple-header.h"
#include "homa-header.h"
#include "ppp-header.h"
#include "qbb-header.h"

namespace ns3 {

NS_LOG_COMPONENT_DEFINE("RdmaHw");

std::unordered_map<unsigned, unsigned> acc_timeout_count;
uint64_t RdmaHw::nAllPkts = 0;

GuardTransitionAuditRecord::GuardTransitionAuditRecord()
    : active(false),
      receiverNode(0),
      receiverNic(0),
      epoch(0),
      transaction(0),
      priorityGroupSetHash(0),
      targetVectorHash(0),
      targetVectorEntries(0),
      receiverCapacityBps(0),
      activeUpperBoundBps(0),
      drainingUpperBoundBps(0),
      activeDrainingUpperBoundBps(0),
      drainingWireUpperBoundBytes(0),
      prefixTargetBytes(0),
      prefixObservedBytes(0),
      prefixRetiredObservedBytes(0),
      startNs(0),
      deadlineNs(0),
      deadlinePolicy("none"),
      deadlineOutcome("none"),
      terminalClosure("none") {}

namespace {

void GuardAuditHashWord(uint64_t *hash, uint64_t word) {
    const uint64_t fnv_prime = 1099511628211ULL;
    for (uint32_t byte = 0; byte < 8; ++byte) {
        *hash ^= (word >> (byte * 8)) & 0xffULL;
        *hash *= fnv_prime;
    }
}

uint64_t GuardAuditHashWords(const std::vector<uint64_t> &words) {
    uint64_t hash = 14695981039346656037ULL;
    GuardAuditHashWord(&hash, words.size());
    for (uint64_t word : words) GuardAuditHashWord(&hash, word);
    return hash;
}

}  // namespace

TypeId RdmaHw::GetTypeId(void) {
    static TypeId tid =
        TypeId("ns3::RdmaHw")
            .SetParent<Object>()
            .AddAttribute("MinRate", "Minimum rate of a throttled flow",
                          DataRateValue(DataRate("100Mb/s")),
                          MakeDataRateAccessor(&RdmaHw::m_minRate), MakeDataRateChecker())
            .AddAttribute("Mtu", "Mtu.", UintegerValue(1000), MakeUintegerAccessor(&RdmaHw::m_mtu),
                          MakeUintegerChecker<uint32_t>())
            .AddAttribute("CcMode", "which mode of DCQCN is running", UintegerValue(0),
                          MakeUintegerAccessor(&RdmaHw::m_cc_mode), MakeUintegerChecker<uint32_t>())
            .AddAttribute("NACKGenerationInterval", "The NACK/CNP Generation interval",
                          DoubleValue(4.0), MakeDoubleAccessor(&RdmaHw::m_nack_interval),
                          MakeDoubleChecker<double>())
            .AddAttribute("L2ChunkSize", "Layer 2 chunk size. Disable chunk mode if equals to 0.",
                          UintegerValue(4000), MakeUintegerAccessor(&RdmaHw::m_chunk),
                          MakeUintegerChecker<uint32_t>())
            .AddAttribute("L2AckInterval", "Layer 2 Ack intervals. Disable ack if equals to 0.",
                          UintegerValue(1), MakeUintegerAccessor(&RdmaHw::m_ack_interval),
                          MakeUintegerChecker<uint32_t>())
            .AddAttribute("L2BackToZero", "Layer 2 go back to zero transmission.",
                          BooleanValue(false), MakeBooleanAccessor(&RdmaHw::m_backto0),
                          MakeBooleanChecker())
            .AddAttribute("EwmaGain",
                          "Control gain parameter which determines the level of rate decrease",
                          DoubleValue(1.0 / 16), MakeDoubleAccessor(&RdmaHw::m_g),
                          MakeDoubleChecker<double>())
            .AddAttribute("RateOnFirstCnp", "the fraction of rate on first CNP", DoubleValue(1.0),
                          MakeDoubleAccessor(&RdmaHw::m_rateOnFirstCNP),
                          MakeDoubleChecker<double>())
            .AddAttribute("ClampTargetRate", "Clamp target rate.", BooleanValue(false),
                          MakeBooleanAccessor(&RdmaHw::m_EcnClampTgtRate), MakeBooleanChecker())
            .AddAttribute("RPTimer", "The rate increase timer at RP in microseconds",
                          DoubleValue(300.0), MakeDoubleAccessor(&RdmaHw::m_rpgTimeReset),
                          MakeDoubleChecker<double>())
            .AddAttribute("RateDecreaseInterval", "The interval of rate decrease check",
                          DoubleValue(4.0), MakeDoubleAccessor(&RdmaHw::m_rateDecreaseInterval),
                          MakeDoubleChecker<double>())
            .AddAttribute("FastRecoveryTimes", "The rate increase timer at RP", UintegerValue(1),
                          MakeUintegerAccessor(&RdmaHw::m_rpgThreshold),
                          MakeUintegerChecker<uint32_t>())
            .AddAttribute("AlphaResumInterval", "The interval of resuming alpha", DoubleValue(1.0),
                          MakeDoubleAccessor(&RdmaHw::m_alpha_resume_interval),
                          MakeDoubleChecker<double>())
            .AddAttribute("RateAI", "Rate increment unit in AI period",
                          DataRateValue(DataRate("5Mb/s")), MakeDataRateAccessor(&RdmaHw::m_rai),
                          MakeDataRateChecker())
            .AddAttribute("RateHAI", "Rate increment unit in hyperactive AI period",
                          DataRateValue(DataRate("50Mb/s")), MakeDataRateAccessor(&RdmaHw::m_rhai),
                          MakeDataRateChecker())
            .AddAttribute("VarWin", "Use variable window size or not", BooleanValue(false),
                          MakeBooleanAccessor(&RdmaHw::m_var_win), MakeBooleanChecker())
            .AddAttribute("FastReact", "Fast React to congestion feedback", BooleanValue(true),
                          MakeBooleanAccessor(&RdmaHw::m_fast_react), MakeBooleanChecker())
            .AddAttribute("MiThresh", "Threshold of number of consecutive AI before MI",
                          UintegerValue(5), MakeUintegerAccessor(&RdmaHw::m_miThresh),
                          MakeUintegerChecker<uint32_t>())
            .AddAttribute("TargetUtil",
                          "The Target Utilization of the bottleneck bandwidth, by default 95%",
                          DoubleValue(0.95), MakeDoubleAccessor(&RdmaHw::m_targetUtil),
                          MakeDoubleChecker<double>())
            .AddAttribute(
                "UtilHigh",
                "The upper bound of Target Utilization of the bottleneck bandwidth, by default 98%",
                DoubleValue(0.98), MakeDoubleAccessor(&RdmaHw::m_utilHigh),
                MakeDoubleChecker<double>())
            .AddAttribute("RateBound", "Bound packet sending by rate, for test only",
                          BooleanValue(true), MakeBooleanAccessor(&RdmaHw::m_rateBound),
                          MakeBooleanChecker())
            .AddAttribute("MultiRate", "Maintain multiple rates in HPCC", BooleanValue(true),
                          MakeBooleanAccessor(&RdmaHw::m_multipleRate), MakeBooleanChecker())
            .AddAttribute("SampleFeedback", "Whether sample feedback or not", BooleanValue(false),
                          MakeBooleanAccessor(&RdmaHw::m_sampleFeedback), MakeBooleanChecker())
            .AddAttribute("GuardEwmaBeta",
                          "Historical-sample weight in GUARD's receive-rate EWMA",
                          DoubleValue(0.125), MakeDoubleAccessor(&RdmaHw::m_guardEwmaBeta),
                          MakeDoubleChecker<double>(0.0, 1.0))
            .AddAttribute("GuardReleaseGamma",
                          "Multiplier for GUARD's proactive-release in-flight threshold",
                          DoubleValue(1.0), MakeDoubleAccessor(&RdmaHw::m_guardReleaseGamma),
                          MakeDoubleChecker<double>(0.0))
            .AddAttribute("GuardSelectiveRegistration",
                          "Register only GUARD flows larger than one BDP",
                          BooleanValue(true),
                          MakeBooleanAccessor(&RdmaHw::m_guardSelectiveRegistration),
                          MakeBooleanChecker())
            .AddAttribute("GuardProactiveRelease",
                          "Release registered GUARD flows before completion",
                          BooleanValue(true), MakeBooleanAccessor(&RdmaHw::m_guardProactiveRelease),
                          MakeBooleanChecker())
            .AddAttribute("GuardKeepLastHopInt",
                          "Retain last-hop INT in GUARD ACKs for the double-control ablation",
                          BooleanValue(false), MakeBooleanAccessor(&RdmaHw::m_guardKeepLastHopInt),
                          MakeBooleanChecker())
            .AddAttribute("GuardSizePriority",
                          "Remap GUARD flows to size-based priority groups",
                          BooleanValue(true), MakeBooleanAccessor(&RdmaHw::m_guardSizePriority),
                          MakeBooleanChecker())
            .AddAttribute("GuardInitialWindowPriority",
                          "Serve only a GUARD flow's initial BDP at the Homa unscheduled "
                          "priority while retaining its immutable QP/PFC priority group",
                          BooleanValue(false),
                          MakeBooleanAccessor(&RdmaHw::m_guardInitialWindowPriority),
                          MakeBooleanChecker())
            .AddAttribute("GuardTransportWindowFloorRtt",
                          "Minimum RTT-equivalent GUARD transport window; path BDP "
                          "classification remains unchanged and zero disables the floor",
                          TimeValue(NanoSeconds(0)),
                          MakeTimeAccessor(&RdmaHw::m_guardTransportWindowFloorRtt),
                          MakeTimeChecker())
            .AddAttribute("GuardTransportWindowFloorAfterFirstGrant",
                          "Keep the exact path-BDP first-grant gate while applying the "
                          "GUARD transport-window floor after the first grant",
                          BooleanValue(false),
                          MakeBooleanAccessor(
                              &RdmaHw::m_guardTransportWindowFloorAfterFirstGrant),
                          MakeBooleanChecker())
            .AddAttribute("GuardTransportWindowWholeFlowFirstGate",
                          "Permit the topology window before the first grant only when "
                          "the complete flow fits in that bounded window",
                          BooleanValue(false),
                          MakeBooleanAccessor(
                              &RdmaHw::m_guardTransportWindowWholeFlowFirstGate),
                          MakeBooleanChecker())
            .AddAttribute("GuardTransportWindowAckSlackPackets",
                          "Cap the topology transport window at path BDP plus this "
                          "many MTU packets; zero disables the cap",
                          UintegerValue(0),
                          MakeUintegerAccessor(
                              &RdmaHw::m_guardTransportWindowAckSlackPackets),
                          MakeUintegerChecker<uint32_t>())
            .AddAttribute("GuardSenderSrpt",
                          "Select the shortest remaining ready GUARD flow at each sender NIC",
                          BooleanValue(true), MakeBooleanAccessor(&RdmaHw::m_guardSenderSrpt),
                          MakeBooleanChecker())
            .AddAttribute("GuardOneRttBypass",
                          "Keep GUARD flows no larger than one BDP at line rate because delayed "
                          "fabric feedback cannot prevent their first-RTT injection",
                          BooleanValue(true),
                          MakeBooleanAccessor(&RdmaHw::m_guardOneRttBypass),
                          MakeBooleanChecker())
            .AddAttribute("GuardTailBypass",
                          "Pace a flow's final acknowledged BDP only by its receiver cap",
                          BooleanValue(true), MakeBooleanAccessor(&RdmaHw::m_guardTailBypass),
                          MakeBooleanChecker())
            .AddAttribute("GuardTailBypassBdps",
                          "Acknowledged BDPs remaining when GUARD enters tail bypass",
                          DoubleValue(8.0), MakeDoubleAccessor(&RdmaHw::m_guardTailBypassBdps),
                          MakeDoubleChecker<double>(1.0, 16.0))
            .AddAttribute("GuardTailCongestionGate",
                          "Require low shared-fabric congestion before tail bypass",
                          BooleanValue(false),
                          MakeBooleanAccessor(&RdmaHw::m_guardTailCongestionGate),
                          MakeBooleanChecker())
            .AddAttribute("GuardTailSafeRatio",
                          "Maximum HPCC utilization-to-target ratio for a safe sample",
                          DoubleValue(0.9), MakeDoubleAccessor(&RdmaHw::m_guardTailSafeRatio),
                          MakeDoubleChecker<double>(0.5, 1.0))
            .AddAttribute("GuardTailSafeSamples",
                          "Consecutive safe fabric samples required for tail bypass",
                          UintegerValue(2), MakeUintegerAccessor(&RdmaHw::m_guardTailSafeSamples),
                          MakeUintegerChecker<uint32_t>(1, 8))
            .AddAttribute("GuardAdaptiveFabricTarget",
                          "Reduce GUARD's aggressive fabric target as queued BDPs grow",
                          BooleanValue(false),
                          MakeBooleanAccessor(&RdmaHw::m_guardAdaptiveFabricTarget),
                          MakeBooleanChecker())
            .AddAttribute("GuardTargetFloor",
                          "Minimum utilization target used by adaptive GUARD fabric control",
                          DoubleValue(0.95),
                          MakeDoubleAccessor(&RdmaHw::m_guardTargetFloor),
                          MakeDoubleChecker<double>(0.5, 1.0))
            .AddAttribute("GuardQueueBudgetBdps",
                          "Queued BDPs over which GUARD interpolates from its configured target "
                          "to the adaptive target floor",
                          DoubleValue(0.5),
                          MakeDoubleAccessor(&RdmaHw::m_guardQueueBudgetBdps),
                          MakeDoubleChecker<double>(0.01, 4.0))
            .AddAttribute("GuardAdaptiveTargetMaxBdps",
                          "Apply adaptive fabric targets only to flows no larger than this many "
                          "BDPs; zero applies to every flow",
                          DoubleValue(0.0),
                          MakeDoubleAccessor(&RdmaHw::m_guardAdaptiveTargetMaxBdps),
                          MakeDoubleChecker<double>(0.0, 64.0))
            .AddAttribute("GuardAckIntervalPackets",
                          "Packets between useful cumulative ACKs for registered GUARD flows",
                          UintegerValue(8),
                          MakeUintegerAccessor(&RdmaHw::m_guardAckIntervalPackets),
                          MakeUintegerChecker<uint32_t>(1))
            .AddAttribute("GuardFixedWindow",
                          "Use a fixed one-BDP safety window while GUARD's two rate caps pace data",
                          BooleanValue(true), MakeBooleanAccessor(&RdmaHw::m_guardFixedWindow),
                          MakeBooleanChecker())
            .AddAttribute("GuardRemainingAware",
                          "Bias receiver grants toward smaller remaining registered flows",
                          BooleanValue(true), MakeBooleanAccessor(&RdmaHw::m_guardRemainingAware),
                          MakeBooleanChecker())
            .AddAttribute("GuardMinShareFraction",
                          "Fraction of equal share guaranteed before remaining-size weighting",
                          DoubleValue(0.0),
                          MakeDoubleAccessor(&RdmaHw::m_guardMinShareFraction),
                          MakeDoubleChecker<double>(0.0, 1.0))
            .AddAttribute("GuardRemainingExponent",
                          "Exponent of inverse remaining bytes in GUARD receiver weighting",
                          DoubleValue(1.0),
                          MakeDoubleAccessor(&RdmaHw::m_guardRemainingExponent),
                          MakeDoubleChecker<double>(0.0, 2.0))
            .AddAttribute("GuardReceiverConcurrency",
                          "Maximum registered flows receiving more than the minimum rate; "
                          "zero serves all registered flows",
                          UintegerValue(0),
                          MakeUintegerAccessor(&RdmaHw::m_guardReceiverConcurrency),
                          MakeUintegerChecker<uint32_t>())
            .AddAttribute("GuardAdaptiveElephantConcurrency",
                          "Use a sublinear floor-sqrt elephant service width, capped by "
                          "GuardReceiverConcurrency",
                          BooleanValue(false),
                          MakeBooleanAccessor(&RdmaHw::m_guardAdaptiveElephantConcurrency),
                          MakeBooleanChecker())
            .AddAttribute("GuardSizeClassElephantConcurrency",
                          "Serve the two shortest elephants together only when their "
                          "remaining sizes are in the same dyadic size class",
                          BooleanValue(false),
                          MakeBooleanAccessor(&RdmaHw::m_guardSizeClassElephantConcurrency),
                          MakeBooleanChecker())
            .AddAttribute("GuardElephantAgingRtts",
                          "Deferred-elephant wait in base RTTs before one K=1 service "
                          "quantum; zero disables aging",
                          DoubleValue(0.0),
                          MakeDoubleAccessor(&RdmaHw::m_guardElephantAgingRtts),
                          MakeDoubleChecker<double>(0.0, 64.0))
            .AddAttribute("GuardConcurrencyMinBdps",
                          "Apply the receiver concurrency bound only above this many BDPs",
                          DoubleValue(8.0),
                          MakeDoubleAccessor(&RdmaHw::m_guardConcurrencyMinBdps),
                          MakeDoubleChecker<double>(0.0, 64.0))
            .AddAttribute("GuardGrantRefreshBdps",
                          "Receiver progress between remaining-aware grant refreshes in BDPs; "
                          "zero disables progress refresh",
                          DoubleValue(1.0),
                          MakeDoubleAccessor(&RdmaHw::m_guardGrantRefreshBdps),
                          MakeDoubleChecker<double>(0.0, 16.0))
            .AddAttribute("GuardMembershipCoalesceWindow",
                          "Bounded delay used to combine registered-set membership changes; "
                          "zero preserves immediate broadcasts",
                          TimeValue(NanoSeconds(0)),
                          MakeTimeAccessor(&RdmaHw::m_guardMembershipCoalesceWindow),
                          MakeTimeChecker())
            .AddAttribute("GuardInitialCollectionQuietWindow",
                          "Initial membership quiet window; zero inherits the normal "
                          "membership coalescing window",
                          TimeValue(NanoSeconds(0)),
                          MakeTimeAccessor(&RdmaHw::m_guardInitialCollectionQuietWindow),
                          MakeTimeChecker())
            .AddAttribute("GuardGrantReliabilityRtts",
                          "Cached-grant refresh interval in equal-share service rounds, where "
                          "one round is registered-flow count times maximum base RTT; zero "
                          "disables the timer",
                          DoubleValue(0.0),
                          MakeDoubleAccessor(&RdmaHw::m_guardGrantReliabilityRtts),
                          MakeDoubleChecker<double>(0.0, 64.0))
            .AddAttribute("GuardMembershipCoalesceMaxWindows",
                          "Hard membership-batch deadline in coalescing-window multiples",
                          UintegerValue(5),
                          MakeUintegerAccessor(&RdmaHw::m_guardMembershipCoalesceMaxWindows),
                          MakeUintegerChecker<uint32_t>(1, 16))
            .AddAttribute("GuardInitialCollectionFullDeadline",
                          "Wait for the full initial membership deadline instead of using "
                          "a sliding quiet window bounded by that deadline",
                          BooleanValue(true),
                          MakeBooleanAccessor(&RdmaHw::m_guardInitialCollectionFullDeadline),
                          MakeBooleanChecker())
            .AddAttribute("GuardSmallSetFastpathLimit",
                          "Maximum registered set using serialized two-phase joins; "
                          "zero disables the V11 fast path",
                          UintegerValue(0),
                          MakeUintegerAccessor(&RdmaHw::m_guardSmallSetFastpathLimit),
                          MakeUintegerChecker<uint32_t>(0, 64))
            .AddAttribute("GuardTransitionPrefixBarrier",
                          "Wait for exact first-window receiver progress before the "
                          "V12 high-fan-in transition activation; disabled by default",
                          BooleanValue(false),
                          MakeBooleanAccessor(
                              &RdmaHw::m_guardTransitionPrefixBarrierEnabled),
                          MakeBooleanChecker())
            .AddAttribute("GuardTransitionPrefixWireWatchdog",
                          "Budget the V12 prefix watchdog from frozen waiter wire bytes "
                          "and acknowledged incumbent occupancy; disabled by default",
                          BooleanValue(false),
                          MakeBooleanAccessor(
                              &RdmaHw::m_guardTransitionPrefixWireWatchdogEnabled),
                          MakeBooleanChecker())
            .AddAttribute("GuardTransitionPrefixFailClosed",
                          "Abort the simulation instead of activating a transition "
                          "through the prefix-deadline fallback path",
                          BooleanValue(false),
                          MakeBooleanAccessor(
                              &RdmaHw::m_guardTransitionPrefixFailClosed),
                          MakeBooleanChecker())
            .AddAttribute("GuardTransitionPrefixAckClockFallback",
                          "For the V15 mixed-PG bundle, treat the receiver-local "
                          "watchdog as a diagnostic trigger and activate frozen "
                          "waiters in required-ACK-clocked batches",
                          BooleanValue(false),
                          MakeBooleanAccessor(
                              &RdmaHw::m_guardTransitionPrefixAckClockFallback),
                          MakeBooleanChecker())
            .AddAttribute("GuardMixedPgVectorFastpath",
                          "Use one canonical, receiver-capacity-bounded target vector "
                          "across priority groups; disabled by default",
                          BooleanValue(false),
                          MakeBooleanAccessor(&RdmaHw::m_guardMixedPgVectorFastpath),
                          MakeBooleanChecker())
            .AddAttribute("GuardSerializedProgressRefresh",
                          "Route remaining-aware progress refreshes through the "
                          "serialized V14 vector coordinator; disabled by default",
                          BooleanValue(false),
                          MakeBooleanAccessor(&RdmaHw::m_guardSerializedProgressRefresh),
                          MakeBooleanChecker())
            .AddAttribute("GuardSerializedDraining",
                          "Reserve conservatively bounded proactive releases until "
                          "contiguous receiver completion; disabled by default",
                          BooleanValue(false),
                          MakeBooleanAccessor(&RdmaHw::m_guardSerializedDraining),
                          MakeBooleanChecker())
            .AddAttribute("GuardCapacityAdmissionDeferral",
                          "Delay a frozen waiter transaction while draining "
                          "reservations cannot fund every sender minimum rate",
                          BooleanValue(false),
                          MakeBooleanAccessor(
                              &RdmaHw::m_guardCapacityAdmissionDeferral),
                          MakeBooleanChecker())
            .AddAttribute("GuardSrptQuantumPackets",
                          "Maximum consecutive SRPT packets before one round-robin service",
                          UintegerValue(64),
                          MakeUintegerAccessor(&RdmaHw::m_guardSrptQuantumPackets),
                          MakeUintegerChecker<uint32_t>(1))
            .AddAttribute("GuardWorkConserving",
                          "Reclaim persistently unused receiver grant shares",
                          BooleanValue(true), MakeBooleanAccessor(&RdmaHw::m_guardWorkConserving),
                          MakeBooleanChecker())
            .AddAttribute("GuardCapAwareReclaim",
                          "Let sender cap reports drive receiver unused-share reclamation",
                          BooleanValue(false),
                          MakeBooleanAccessor(&RdmaHw::m_guardCapAwareReclaim),
                          MakeBooleanChecker())
            .AddAttribute("GuardElephantCapSpillover",
                          "Transfer only a confirmed fabric-limited K=1 elephant's "
                          "unused receiver share to the next elephant",
                          BooleanValue(false),
                          MakeBooleanAccessor(&RdmaHw::m_guardElephantCapSpillover),
                          MakeBooleanChecker())
            .AddAttribute("GuardCapTriggeredRefresh",
                          "Refresh the frozen K=1 receiver vector when a fresh fabric-cap "
                          "report changes eligibility or materially changes its cap",
                          BooleanValue(false),
                          MakeBooleanAccessor(&RdmaHw::m_guardCapTriggeredRefresh),
                          MakeBooleanChecker())
            .AddAttribute("GuardCapRefreshMaterialPercent",
                          "Minimum relative fabric-cap change that requests a "
                          "serialized vector refresh",
                          UintegerValue(5),
                          MakeUintegerAccessor(
                              &RdmaHw::m_guardCapRefreshMaterialPercent),
                          MakeUintegerChecker<uint32_t>(1, 20))
            .AddAttribute("GuardElephantSpilloverEnterReports",
                          "Consecutive fabric-bound reports required to enter spillover",
                          UintegerValue(3),
                          MakeUintegerAccessor(
                              &RdmaHw::m_guardElephantSpilloverEnterReports),
                          MakeUintegerChecker<uint32_t>(1, 8))
            .AddAttribute("GuardElephantSpilloverExitReports",
                          "Consecutive unbound reports required to leave spillover",
                          UintegerValue(1),
                          MakeUintegerAccessor(
                              &RdmaHw::m_guardElephantSpilloverExitReports),
                          MakeUintegerChecker<uint32_t>(1, 8))
            .AddAttribute("GuardElephantFabricTarget",
                          "Use a larger HPCC target only for greater-than-threshold elephants",
                          BooleanValue(false),
                          MakeBooleanAccessor(&RdmaHw::m_guardElephantFabricTarget),
                          MakeBooleanChecker())
            .AddAttribute("GuardElephantFabricTargetScale",
                          "Scale applied to the GUARD HPCC target for eligible elephants",
                          DoubleValue(1.0),
                          MakeDoubleAccessor(&RdmaHw::m_guardElephantFabricTargetScale),
                          MakeDoubleChecker<double>(1.0, 2.0))
            .AddAttribute("GuardElephantReceiverAuthority",
                          "Let a selected greater-than-threshold elephant follow its receiver cap",
                          BooleanValue(false),
                          MakeBooleanAccessor(&RdmaHw::m_guardElephantReceiverAuthority),
                          MakeBooleanChecker())
            .AddAttribute("GuardCapHeadroom",
                          "Headroom above a reported shared-fabric cap",
                          DoubleValue(1.1), MakeDoubleAccessor(&RdmaHw::m_guardCapHeadroom),
                          MakeDoubleChecker<double>(1.0, 2.0))
            .AddAttribute("GuardCapMinShareFraction",
                          "Safety floor as a fraction of equal receiver share",
                          DoubleValue(0.25),
                          MakeDoubleAccessor(&RdmaHw::m_guardCapMinShareFraction),
                          MakeDoubleChecker<double>(0.0, 1.0))
            .AddAttribute("GuardRebalanceInterval",
                          "Receiver demand-sampling interval for adaptive grants",
                          TimeValue(MicroSeconds(200)),
                          MakeTimeAccessor(&RdmaHw::m_guardRebalanceInterval),
                          MakeTimeChecker())
            .AddAttribute("GuardDemandThreshold",
                          "Arrival/grant ratio below which a share is reclaimable",
                          DoubleValue(0.75), MakeDoubleAccessor(&RdmaHw::m_guardDemandThreshold),
                          MakeDoubleChecker<double>(0.0, 1.0))
            .AddAttribute("GuardReceiverUtilThreshold",
                          "Aggregate receiver utilization gate for adaptive grants",
                          DoubleValue(0.75),
                          MakeDoubleAccessor(&RdmaHw::m_guardReceiverUtilThreshold),
                          MakeDoubleChecker<double>(0.0, 1.0))
            .AddAttribute("HomaOvercommitDegree",
                          "Maximum Homa messages granted concurrently at a receiver",
                          UintegerValue(4),
                          MakeUintegerAccessor(&RdmaHw::m_homaOvercommitDegree),
                          MakeUintegerChecker<uint32_t>(1, 6))
            .AddAttribute("HomaResendTimeout",
                          "Homa receiver timeout for a granted byte-range with no progress",
                          TimeValue(MilliSeconds(1)),
                          MakeTimeAccessor(&RdmaHw::m_homaResendTimeout),
                          MakeTimeChecker())
            .AddAttribute("TimelyAlpha", "Alpha of TIMELY", DoubleValue(0.875),
                          MakeDoubleAccessor(&RdmaHw::m_tmly_alpha), MakeDoubleChecker<double>())
            .AddAttribute("TimelyBeta", "Beta of TIMELY", DoubleValue(0.8),
                          MakeDoubleAccessor(&RdmaHw::m_tmly_beta), MakeDoubleChecker<double>())
            .AddAttribute("TimelyTLow", "TLow of TIMELY (ns)", UintegerValue(50000),
                          MakeUintegerAccessor(&RdmaHw::m_tmly_TLow),
                          MakeUintegerChecker<uint64_t>())
            .AddAttribute("TimelyTHigh", "THigh of TIMELY (ns)", UintegerValue(500000),
                          MakeUintegerAccessor(&RdmaHw::m_tmly_THigh),
                          MakeUintegerChecker<uint64_t>())
            .AddAttribute("TimelyMinRtt", "MinRtt of TIMELY (ns)", UintegerValue(20000),
                          MakeUintegerAccessor(&RdmaHw::m_tmly_minRtt),
                          MakeUintegerChecker<uint64_t>())
            .AddAttribute("DctcpRateAI", "DCTCP's Rate increment unit in AI period",
                          DataRateValue(DataRate("1000Mb/s")),
                          MakeDataRateAccessor(&RdmaHw::m_dctcp_rai), MakeDataRateChecker())
            .AddAttribute("IrnEnable", "Enable IRN", BooleanValue(false),
                          MakeBooleanAccessor(&RdmaHw::m_irn), MakeBooleanChecker())
            .AddAttribute("IrnRtoLow", "Low RTO for IRN", TimeValue(MicroSeconds(454)),
                          MakeTimeAccessor(&RdmaHw::m_irn_rtoLow), MakeTimeChecker())
            .AddAttribute("IrnRtoHigh", "High RTO for IRN", TimeValue(MicroSeconds(1350)),
                          MakeTimeAccessor(&RdmaHw::m_irn_rtoHigh), MakeTimeChecker())
            .AddAttribute("IrnBdp", "BDP Limit for IRN in Bytes", UintegerValue(100000),
                          MakeUintegerAccessor(&RdmaHw::m_irn_bdp), MakeUintegerChecker<uint32_t>())
            .AddAttribute("L2Timeout", "Sender's timer of waiting for the ack",
                          TimeValue(MilliSeconds(4)), MakeTimeAccessor(&RdmaHw::m_waitAckTimeout),
                          MakeTimeChecker());
    return tid;
}

RdmaHw::RdmaHw() : homa_simple_scheduler(this), homa_scheduler(this) {
    cnp_total = 0;
    cnp_by_ecn = 0;
    cnp_by_ooo = 0;
    m_dcqcnCnpGeneratedEcn = 0;
    m_dcqcnCnpGeneratedOoo = 0;
    m_dcqcnCnpReceived = 0;
    m_dcqcnAlphaUpdates = 0;
    m_dcqcnAlphaCnpUpdates = 0;
    m_dcqcnRateDecreaseEvents = 0;
    m_dcqcnActualRateDecreases = 0;
    m_dcqcnRateIncreaseEvents = 0;
    m_dcqcnActualRateIncreases = 0;
    m_guardRateGrantsSent = 0;
    m_guardRateGrantBytesSent = 0;
    m_guardRateGrantsReceived = 0;
    m_guardInitialWindowPriorityFlows = 0;
    m_guardInitialWindowPriorityPackets = 0;
    m_guardInitialWindowPriorityBytes = 0;
    m_guardInitialWindowPriorityTransitions = 0;
    m_guardTransportWindowRaisedFlows = 0;
    m_guardTransportWindowExtraBytes = 0;
    m_guardTransportWindowMaxBytes = 0;
    m_guardTransportWindowWholeFlowFirstGateFlows = 0;
    m_guardTransportWindowAckSlackLimitedFlows = 0;
    m_guardHpccFeedbackUpdates = 0;
    m_guardHpccValidFeedback = 0;
    m_guardHpccRateUpdatesApplied = 0;
    m_guardHpccFullComputations = 0;
    m_guardHpccFastComputations = 0;
    m_guardHpccActualRateChanges = 0;
    m_guardReactiveBindingUpdates = 0;
    m_guardGrantBindingUpdates = 0;
    m_guardTieBindingUpdates = 0;
    m_guardReactiveBindingRateChanges = 0;
    m_guardGrantBindingRateChanges = 0;
    m_guardTieBindingRateChanges = 0;
    m_guardGrantEventRateChanges = 0;
    m_guardIntHopsBeforeStrip = 0;
    m_guardIntHopsAfterStrip = 0;
    m_guardIntRecordsStripped = 0;
    m_guardRegistrations = 0;
    m_guardSelectedRegistrations = 0;
    m_guardProactiveReleases = 0;
    m_guardCompletionReleases = 0;
    m_guardMaxActiveFlows = 0;
    m_guardRebalanceEvents = 0;
    m_guardAdaptiveGrantUpdates = 0;
    m_guardCapReportsSent = 0;
    m_guardCapReportBytesSent = 0;
    m_guardCapReportsReceived = 0;
    m_guardFabricBoundReportsReceived = 0;
    m_guardCapRebalanceEvents = 0;
    m_guardCapGrantUpdates = 0;
    m_guardCapMaxReclaimedBps = 0;
    m_guardElephantSpilloverRefreshRequests = 0;
    m_guardCapTriggeredRefreshRequests = 0;
    m_guardElephantSpilloverVectors = 0;
    m_guardElephantSpilloverMaxBps = 0;
    m_guardElephantSpilloverAllocatorChecks = 0;
    m_guardElephantSpilloverNoPairChecks = 0;
    m_guardElephantSpilloverDonorInactiveChecks = 0;
    m_guardElephantSpilloverEligibleNonDonorChecks = 0;
    m_guardElephantSpilloverDonorStaleChecks = 0;
    m_guardElephantSpilloverCapAtOrAboveTargetChecks = 0;
    m_guardElephantFabricTargetUpdates = 0;
    m_guardElephantFabricTargetMaxEffective = 0.0;
    m_guardElephantReceiverAuthorityBindings = 0;
    m_guardElephantReceiverAuthorityRateChanges = 0;
    m_guardElephantReceiverAuthorityMaxReleasedBps = 0;
    m_guardRemainingRefreshEvents = 0;
    m_guardMembershipChangesDeferred = 0;
    m_guardMembershipBatches = 0;
    m_guardMembershipMaxBatch = 0;
    m_guardMembershipEmptyCancellations = 0;
    m_guardMembershipTimerReschedules = 0;
    m_guardReleaseThresholdDeferrals = 0;
    m_guardInitialCollectionStarts = 0;
    m_guardInitialCollectionFlushes = 0;
    m_guardInitialCollectionDeferredChanges = 0;
    m_guardInitialCollectionReschedules = 0;
    m_guardInitialCollectionCancellations = 0;
    m_guardInitialCollectionQuietFlushes = 0;
    m_guardInitialCollectionHardFlushes = 0;
    m_guardInitialCollectionWaitNs = 0;
    m_guardInitialCollectionMaxWaitNs = 0;
    m_guardReliabilityRefreshEvents = 0;
    m_guardReliabilityGrantUpdates = 0;
    m_guardGrantAcksSent = 0;
    m_guardGrantAckBytesSent = 0;
    m_guardGrantAcksReceived = 0;
    m_guardGrantAckBytesReceived = 0;
    m_guardGrantAcksStale = 0;
    m_guardRetiredAckClosures = 0;
    m_guardRetiredAcksReceived = 0;
    m_guardRetiredAckPeak = 0;
    m_guardRetiredAckOverflow = 0;
    m_guardStaleGrantsReceived = 0;
    m_guardAckRequiredBatches = 0;
    m_guardAckOptionalBatches = 0;
    m_guardAckRequiredGrantsSent = 0;
    m_guardAckOptionalGrantsSent = 0;
    m_guardGenerationZeroRejected = 0;
    m_guardGenerationMismatchRejected = 0;
    m_guardFullyAckedBatches = 0;
    m_guardFirstGrantGatedFlows = 0;
    m_guardFirstGrantGateReleases = 0;
    m_guardProgressEventsCoalesced = 0;
    m_guardFastpathTransactions = 0;
    m_guardFastpathPrepareBatches = 0;
    m_guardFastpathPrepareGrants = 0;
    m_guardFastpathPrepareAcks = 0;
    m_guardFastpathActivateBatches = 0;
    m_guardFastpathActivateGrants = 0;
    m_guardFastpathActivateAcks = 0;
    m_guardTransitionPrepareBatches = 0;
    m_guardTransitionPrepareGrants = 0;
    m_guardTransitionPrepareAcks = 0;
    m_guardTransitionActivateBatches = 0;
    m_guardTransitionActivateGrants = 0;
    m_guardTransitionActivateAcks = 0;
    m_guardFastpathOptionalReleaseGenerations = 0;
    m_guardFastpathOptionalReleaseGrants = 0;
    m_guardFastpathQueuedMembershipChanges = 0;
    m_guardFastpathHighFanInTransitions = 0;
    m_guardFastpathHighCollectionFlushes = 0;
    m_guardFastpathWaitersActivated = 0;
    m_guardFastpathPostTransitionFastGrants = 0;
    m_guardFastpathBarrierViolations = 0;
    m_guardFastpathEarlyUnlocks = 0;
    m_guardVectorFreezes = 0;
    m_guardVectorMixedPgFreezes = 0;
    m_guardVectorMaxEntries = 0;
    m_guardVectorMaxPriorityGroups = 0;
    m_guardVectorMixedPriorityGroupMask = 0;
    m_guardVectorAllPriorityGroupMask = 0;
    m_guardVectorPrepareDecreases = 0;
    m_guardVectorActivationWaiters = 0;
    m_guardVectorActivationIncreases = 0;
    m_guardVectorReleaseRequired = 0;
    m_guardVectorReleaseOptional = 0;
    m_guardVectorLastHash = 0;
    m_guardSerializedProgressRequests = 0;
    m_guardSerializedProgressTransactions = 0;
    m_guardSerializedProgressCommits = 0;
    m_guardSerializedProgressBusyDeferrals = 0;
    m_guardDrainingRequests = 0;
    m_guardDrainingPendingTransitions = 0;
    m_guardDrainingDirectTransitions = 0;
    m_guardDrainingBoundaryCommits = 0;
    m_guardDrainingCompletionReleases = 0;
    m_guardDrainingMaxRecords = 0;
    m_guardDrainingMaxReservedBps = 0;
    m_guardFastpathJoinQueueMax = 0;
    m_guardFastpathHighTransitionRegisteredN = 0;
    m_guardFastpathHighTransitionWaiters = 0;
    m_guardFastpathPrefixCloseNs = 0;
    m_guardFastpathTransitionPrepareStartNs = 0;
    m_guardFastpathCollectionFlushNs = 0;
    m_guardFastpathLastTransactionCloseNs = 0;
    m_guardFastPrepareWireGrantFrames = 0;
    m_guardFastActivateWireGrantFrames = 0;
    m_guardTransitionPrepareWireGrantFrames = 0;
    m_guardTransitionActivateWireGrantFrames = 0;
    m_guardReleaseWireGrantFrames = 0;
    m_guardFastPrepareWireAckFrames = 0;
    m_guardFastActivateWireAckFrames = 0;
    m_guardTransitionPrepareWireAckFrames = 0;
    m_guardTransitionActivateWireAckFrames = 0;
    m_guardFastpathUnattributedGrantFrames = 0;
    m_guardFastpathUnattributedAckFrames = 0;
    m_guardTransitionPrefixBarrierStarts = 0;
    m_guardTransitionPrefixBarrierReady = 0;
    m_guardTransitionPrefixBarrierTimeouts = 0;
    m_guardTransitionPrefixDegradedTransitions = 0;
    m_guardTransitionPrefixWaitersRequired = 0;
    m_guardTransitionPrefixWaitersReady = 0;
    m_guardTransitionPrefixTargetBytes = 0;
    m_guardTransitionPrefixReceivedBytes = 0;
    m_guardTransitionPrefixRemainingBytes = 0;
    m_guardTransitionPrefixWaitNs = 0;
    m_guardTransitionPrefixMaxWaitNs = 0;
    m_guardTransitionPrefixDeadlineNs = 0;
    m_guardTransitionPrefixStartNs = 0;
    m_guardTransitionPrefixReadyNs = 0;
    m_guardTransitionFallbackBatches = 0;
    m_guardTransitionFallbackMaxBatch = 0;
    m_guardTransitionFallbackClosedBatches = 0;
    m_guardTransitionFallbackOrderViolations = 0;
    m_guardTransitionPrefixBarrierViolations = 0;
    m_guardTransitionPrefixWatchdogRoundedPayloadBudgetBytes = 0;
    m_guardTransitionPrefixWatchdogPacketCount = 0;
    m_guardTransitionPrefixWatchdogHeaderBytesPerPacket = 0;
    m_guardTransitionPrefixWatchdogWireBytes = 0;
    m_guardTransitionPrefixWatchdogIncumbentCount = 0;
    m_guardTransitionPrefixWatchdogOccupancyBps = 0;
    m_guardTransitionPrefixWatchdogReceiverCapacityBps = 0;
    m_guardTransitionPrefixWatchdogResidualBps = 0;
    m_guardTransitionPrefixWatchdogSerializationNs = 0;
    m_guardTransitionPrefixWatchdogMaxRttNs = 0;
    m_guardTransitionPrefixWatchdogDelayNs = 0;
    m_guardTransitionPrefixWatchdogStartNs = 0;
    m_guardTransitionPrefixWatchdogDeadlineNs = 0;
    m_guardTransitionPrefixWatchdogBudgetRecords = 0;
    m_guardTransitionPrefixWatchdogNonReconstructable = false;
    m_guardConcurrencyLimitedAllocations = 0;
    m_guardConcurrencyMaxDeferredFlows = 0;
    m_guardAdaptiveConcurrencyPromotions = 0;
    m_guardAdaptiveConcurrencyMaxEffective = 0;
    m_guardSizeClassConcurrencyPromotions = 0;
    m_guardSizeClassConcurrencyMaxEffective = 0;
    m_guardElephantAgingRotations = 0;
    m_guardElephantAgingMaxWaitNs = 0;
    m_guardOneRttBypassFlows = 0;
    m_guardOneRttBypassFeedbacks = 0;
    m_guardOneRttAcksSuppressed = 0;
    m_guardLongAcksSuppressed = 0;
    m_guardTailBypassFlows = 0;
    m_guardTailBypassFeedbacks = 0;
    m_guardTailGateDeferrals = 0;
    m_guardTailGateQualifiedFlows = 0;
    m_guardAdaptiveTargetUpdates = 0;
    m_guardAdaptiveTargetMinObserved = std::numeric_limits<double>::max();
    m_guardAdaptiveTargetMaxQueueBdps = 0.0;
    m_guardUnderutilizedSamples = 0;
    m_guardLastRebalanceTime = Time(0);
    m_guardMembershipBatchStart = Time(0);
    m_guardInitialCollectionStart = Time(0);
    m_guardInitialCollectionDeadline = Time(0);
    m_guardInitialCollectionTarget = Time(0);
    m_guardPendingMembershipChanges = 0;
    m_guardLastVectorActiveFlows = 0;
    m_guardHasEmittedVectorThisEpoch = false;
    m_guardInitialCollectionPending = false;
    m_guardGrantGeneration = 0;
    m_guardPendingGrantAcks = 0;
    m_guardSmallSetFastpathLimit = 0;
    m_guardTransitionPrefixBarrierEnabled = false;
    m_guardTransitionPrefixWireWatchdogEnabled = false;
    m_guardTransitionPrefixFailClosed = false;
    m_guardTransitionPrefixAckClockFallback = false;
    m_guardMixedPgVectorFastpath = false;
    m_guardSerializedProgressRefresh = false;
    m_guardSerializedDraining = false;
    m_guardCapacityAdmissionDeferral = false;
    m_guardCapacityAdmissionBlocked = false;
    m_guardCapacityAdmissionDeferrals = 0;
    m_guardCapacityAdmissionResumes = 0;
    m_guardCapacityAdmissionMaxWaiters = 0;
    m_guardFastpathPhase = GUARD_FASTPATH_IDLE;
    m_guardFastpathHighFanIn = false;
    m_guardFastpathHighInitialCollectionFlushed = false;
    m_guardFastpathHighInitialCommitted = false;
    m_guardFastpathCollectionReady = false;
    m_guardFastpathTransactionIsTransition = false;
    m_guardFastpathReleaseRevectorActive = false;
    m_guardFastpathTransaction = 0;
    m_guardFastpathMembershipRevision = 0;
    m_guardFastpathConsumedMembershipRevision = 0;
    m_guardFastpathReadyMembershipRevision = 0;
    m_guardFastpathEpochPrefixCloseNs = 0;
    m_guardFastpathEpochCollectionFlushNs = 0;
    m_guardFastpathEpochTransitionPrepareStartNs = 0;
    m_guardFastpathTransactionTargetN = 0;
    m_guardFastpathTransactionTargetMbps = 0;
    m_guardFrozenVectorActive = false;
    m_guardFrozenReceiverNic = 0;
    m_guardFrozenReceiverCapacityBps = 0;
    m_guardFrozenActiveRecords = 0;
    m_guardFrozenDrainingRecords = 0;
    m_guardFrozenDrainingReservedBps = 0;
    m_guardFrozenAllocatableCapacityBps = 0;
    m_guardFrozenEncodedTargetBps = 0;
    m_guardFrozenMembershipRevision = 0;
    m_guardAllocationRevision = 0;
    m_guardProgressRevision = 0;
    m_guardConsumedProgressRevision = 0;
    m_guardFrozenAllocationRevision = 0;
    m_guardFrozenProgressRevision = 0;
    m_guardFrozenReasonMask = 0;
    m_guardProgressTransactionRevision = 0;
    m_guardProgressTransactionActive = false;
    m_guardProgressTransactionCapacityDirty = false;
    m_guardCapacityDirty = false;
    m_guardApplyingPendingDrains = false;
    m_guardTerminalResetDeferred = false;
    m_guardFrozenVectorHash = 0;
    m_guardTransitionPrefixBarrierWaiting = false;
    m_guardTransitionPrefixBarrierResolved = false;
    m_guardTransitionPrefixTimedOut = false;
    m_guardTransitionFallbackActive = false;
    m_guardTransitionPrefixWatchdogBudgetActive = false;
    m_guardTransitionPrefixBarrierStart = Time(0);
    m_guardTransitionPrefixDeadline = Time(0);
    m_guardTransitionActivationCursor = 0;
    m_guardTransitionActivationBatchIndex = 0;
    m_guardTransitionActivationBatchSize = 0;
    m_guardTransitionLastRegisterNs = -1;
    m_guardTransitionLastFlowId = -1;
    m_guardLifecycleTraceSink = NULL;
    m_guardControllerTraceSink = NULL;
    m_guardGrantTraceSink = NULL;
    m_guardTransitionAuditSink = NULL;
    m_guardTransitionAuditEpoch = 1;
    m_recoveryNacksGenerated = 0;
    m_recoveryNacksReceived = 0;
    m_irnNacksGenerated = 0;
    m_irnNacksReceived = 0;
    m_irnRetransmitPackets = 0;
    m_irnRetransmitBytes = 0;
    m_timeoutRecoveries = 0;
    m_homaDataPacketsSent = 0;
    m_homaDataBytesSent = 0;
    for (uint32_t priority = 0; priority < 8; priority++) {
        m_homaDataPacketsByPriority[priority] = 0;
        m_homaDataBytesByPriority[priority] = 0;
    }
    m_homaRetransmitPacketsSent = 0;
    m_homaGrantsSent = 0;
    m_homaGrantsReceived = 0;
    m_homaResendsSent = 0;
    m_homaResendsReceived = 0;
    m_homaCompletionNoticesSent = 0;
    m_homaCompletionNoticesReceived = 0;
    m_homaMessagesTracked = 0;
    m_homaMessagesCompleted = 0;
    m_homaMaxPendingMessages = 0;
    m_homaDuplicateDataAfterCompletion = 0;
    m_homaCompletionNoticesReplayed = 0;
    m_homaUnscheduledLevels = 3;
    m_homaUnscheduledCutoffs.push_back(26000);
    m_homaUnscheduledCutoffs.push_back(52000);
}

void RdmaHw::ConfigureHomaPriorities(uint32_t unscheduled_levels,
                                     const std::vector<uint64_t>& cutoffs) {
    NS_ASSERT_MSG(unscheduled_levels >= 1 && unscheduled_levels <= 6,
                  "Homa requires one to six unscheduled DATA priorities");
    NS_ASSERT_MSG(cutoffs.size() == unscheduled_levels - 1,
                  "Homa cutoff count must equal unscheduled priority count minus one");
    for (size_t index = 1; index < cutoffs.size(); index++) {
        NS_ASSERT_MSG(cutoffs[index] >= cutoffs[index - 1],
                      "Homa unscheduled cutoffs must be nondecreasing");
    }
    m_homaUnscheduledLevels = unscheduled_levels;
    m_homaUnscheduledCutoffs = cutoffs;
}

void RdmaHw::SetNode(Ptr<Node> node) { m_node = node; }
void RdmaHw::Setup(QpCompleteCallback cb) {
    for (uint32_t i = 0; i < m_nic.size(); i++) {
        Ptr<QbbNetDevice> dev = m_nic[i].dev;
        if (dev == NULL) continue;
        // share data with NIC
        dev->m_rdmaEQ->m_qpGrp = m_nic[i].qpGrp;
        // setup callback
        dev->m_rdmaReceiveCb = MakeCallback(&RdmaHw::Receive, this);
        dev->m_rdmaLinkDownCb = MakeCallback(&RdmaHw::SetLinkDown, this);
        dev->m_rdmaPktSent = MakeCallback(&RdmaHw::PktSent, this);
        // config NIC
        dev->m_rdmaEQ->m_mtu = m_mtu;
        if (m_cc_mode == 10) {  // homa-simple
            dev->m_rdmaEQ->m_rdmaGetNxtPkt = MakeCallback(&RdmaHw::GetNxtPacketHomaSimple, this);
        } else if (m_cc_mode == 12) {  // homa (PR1: line-rate sender, HomaHeader on every pkt)
            dev->m_rdmaEQ->m_rdmaGetNxtPkt = MakeCallback(&RdmaHw::GetNxtPacketHoma, this);
        } else {
            dev->m_rdmaEQ->m_rdmaGetNxtPkt = MakeCallback(&RdmaHw::GetNxtPacket, this);
        }
    }
    // setup qp complete callback
    m_qpCompleteCallback = cb;
}

uint32_t RdmaHw::GetNicIdxOfQp(Ptr<RdmaQueuePair> qp) {
    auto &v = m_rtTable[qp->dip.Get()];
    if (v.size() > 0) {
        return v[qp->GetHash() % v.size()];
    }
    NS_ASSERT_MSG(false, "We assume at least one NIC is alive");
    std::cout << "We assume at least one NIC is alive" << std::endl;
    exit(1);
}

uint64_t RdmaHw::GetQpKey(uint32_t dip, uint16_t sport, uint16_t dport,
                          uint16_t pg) {  // Sender perspective
    return ((uint64_t)dip << 32) | ((uint64_t)sport << 16) | (uint64_t)dport | (uint64_t)pg;
}

GuardQpInsertionDisposition RdmaHw::ClassifyGuardQpInsertion(
    const GuardQpIdentity *live, const GuardQpIdentity *tombstone,
    const GuardQpIdentity &candidate) {
    if (live != NULL) {
        return *live == candidate ? GUARD_QP_INSERT_LIVE_DUPLICATE
                                  : GUARD_QP_INSERT_LIVE_COLLISION;
    }
    if (tombstone != NULL) {
        return *tombstone == candidate ? GUARD_QP_INSERT_RETIRED_TUPLE
                                       : GUARD_QP_INSERT_TOMBSTONE_COLLISION;
    }
    return GUARD_QP_INSERT_OK;
}

GuardFrozenActiveProvenanceDisposition
RdmaHw::ClassifyGuardFrozenActiveProvenance(
    const GuardFrozenActiveProvenanceInput &input) {
    if (input.inCohort) return GUARD_FROZEN_ACTIVE_COHORT;

    bool later_dirty_membership =
        input.registrationMembershipRevision > input.frozenMembershipRevision &&
        input.liveMembershipRevision > input.frozenMembershipRevision &&
        input.registrationMembershipRevision <= input.liveMembershipRevision &&
        input.pendingMembershipChanges > 0 &&
        input.pendingMembershipChanges ==
            input.liveMembershipRevision - input.frozenMembershipRevision;
    bool zero_grant_authority =
        input.lastAckedGeneration == 0 &&
        input.lastAckedUpperBoundBps == 0 &&
        input.lastIssuedGeneration == 0 &&
        input.lastIssuedTargetBps == 0 &&
        input.grantGeneration == 0 && input.grantRateBps == 0 &&
        input.grantUpperBoundBps == 0 && !input.grantGenerationAcked;
    bool valid_first_grant_gate =
        input.registerNs >= 0 && input.flowSizeBytes > 0 &&
        input.hasFirstGrantGateBytes && input.firstGrantGateBytes > 0 &&
        std::isfinite(input.baseRttSec) && input.baseRttSec > 0.0;
    bool same_receiver_resource =
        input.receiverNic == input.frozenReceiverNic &&
        input.receiverCapacityBps != 0 &&
        input.receiverCapacityBps == input.frozenReceiverCapacityBps;
    if (input.inFastpathWaiters && !input.inIncumbents &&
        !input.inTransactionWaiters && later_dirty_membership &&
        zero_grant_authority && valid_first_grant_gate &&
        same_receiver_resource) {
        return GUARD_FROZEN_ACTIVE_DEFERRED_WAITER;
    }
    return GUARD_FROZEN_ACTIVE_INVALID;
}

Ptr<RdmaQueuePair> RdmaHw::GetQp(uint64_t key) {
    auto it = m_qpMap.find(key);

    // lookup main memory
    if (it != m_qpMap.end()) {
        return it->second;
    }

    return NULL;
}

Ptr<RdmaQueuePair> RdmaHw::GetQp(uint32_t sip, uint32_t dip, uint16_t sport,
                                 uint16_t dport, uint16_t pg) {
    uint64_t key = GetQpKey(dip, sport, dport, pg);
    GuardQpIdentity identity = {sip, dip, sport, dport, pg};
    auto live = m_qpIdentity.find(key);
    NS_ABORT_MSG_IF(live != m_qpIdentity.end() && live->second != identity,
                    "GUARD QP legacy-key collision on live lookup");
    auto dead = m_qpTombstoneIdentity.find(key);
    NS_ABORT_MSG_IF(dead != m_qpTombstoneIdentity.end() && dead->second != identity,
                    "GUARD QP legacy-key collision on tombstone lookup");
    return GetQp(key);
}

void print_rate(RdmaQueuePair *qp) {
    if (Settings::ip_to_node_id(qp->sip) == 0) {
        double a = ((double) qp->hp.m_curRate.GetBitRate()) / 1000000000.0;
        double b = ((double) qp->hp.m_grantRate.GetBitRate()) / 1000000000.0;
        double c = ((double) qp->m_rate.GetBitRate()) / 1000000000.0;
        std::cout << Settings::ip_to_node_id(qp->sip) << "\t" << Simulator::Now().GetNanoSeconds() - 2000000000 << "\t" << (a < b ? a : b) << "\t" << c << std::endl;
    }
    Time time("1000ns");
    Simulator::Schedule(NanoSeconds(time), &print_rate, qp);
}

void RdmaHw::AddQueuePair(uint64_t size, uint16_t pg, Ipv4Address sip, Ipv4Address dip,
                          uint16_t sport, uint16_t dport, uint32_t win, uint64_t baseRtt,
                          int32_t flow_id) {
    // For homa (cc_mode=12), the sender writes a per-packet udp.pg into
    // each DATA packet (unscheduled cutoff or grant slot). The QP's own m_pg
    // must therefore stay constant for QP-key lookups; we pin it to 0 and use
    // qbbHeader.pg=0 on control packets for the same reason.
    uint16_t qp_pg = (m_cc_mode == 12) ? 0 : pg;

    // create qp
    Ptr<RdmaQueuePair> qp = CreateObject<RdmaQueuePair>(qp_pg, sip, dip, sport, dport);
    qp->SetSize(size);
    qp->SetWin(win);
    qp->SetBaseRtt(baseRtt);
    qp->SetVarWin(m_var_win);
    if (m_cc_mode == CC_MODE_GUARD && m_guardFixedWindow) qp->SetVarWin(false);
    qp->SetFlowId(flow_id);
    qp->SetTimeout(m_waitAckTimeout);
    qp->m_guard_sender_srpt = (m_cc_mode == CC_MODE_GUARD && m_guardSenderSrpt);
    qp->m_guard_tail_bypass = false;
    qp->m_guard_srpt_quantum_packets = m_guardSrptQuantumPackets;

    if (m_irn) {
        qp->irn.m_enabled = m_irn;
        qp->irn.m_bdp = m_irn_bdp;
        qp->irn.m_rtoLow = m_irn_rtoLow;
        qp->irn.m_rtoHigh = m_irn_rtoHigh;
    }

    // add qp
    uint32_t nic_idx = GetNicIdxOfQp(qp);

    if (m_cc_mode == CC_MODE_GUARD && !m_guardTransportWindowFloorRtt.IsZero()) {
        NS_ABORT_MSG_IF(!m_guardFixedWindow ||
                            m_guardTransportWindowFloorRtt.IsNegative(),
                        "GUARD transport-window floor requires a fixed, nonnegative RTT");
        uint64_t floor_ns = m_guardTransportWindowFloorRtt.GetNanoSeconds();
        uint64_t line_bps = m_nic[nic_idx].dev->GetDataRate().GetBitRate();
        __uint128_t product = static_cast<__uint128_t>(floor_ns) * line_bps;
        __uint128_t floor_bytes =
            (product + 8000000000ULL - 1) / 8000000000ULL;
        NS_ABORT_MSG_IF(floor_bytes == 0 ||
                            floor_bytes > std::numeric_limits<uint32_t>::max(),
                        "GUARD transport-window floor overflows byte accounting");
        uint64_t requested_window = static_cast<uint64_t>(floor_bytes);
        if (m_guardTransportWindowAckSlackPackets > 0) {
            __uint128_t slack_limit = static_cast<__uint128_t>(qp->m_win) +
                static_cast<__uint128_t>(m_guardTransportWindowAckSlackPackets) * m_mtu;
            NS_ABORT_MSG_IF(slack_limit > std::numeric_limits<uint32_t>::max(),
                            "GUARD ACK-slack transport window overflows byte accounting");
            uint64_t limited_window = std::min<uint64_t>(
                requested_window, static_cast<uint64_t>(slack_limit));
            if (limited_window < requested_window && limited_window > qp->m_win) {
                m_guardTransportWindowAckSlackLimitedFlows++;
            }
            requested_window = limited_window;
        }
        qp->m_guard_transport_win = std::max<uint64_t>(qp->m_win, requested_window);
        m_guardTransportWindowMaxBytes = std::max<uint64_t>(
            m_guardTransportWindowMaxBytes, qp->m_guard_transport_win);
        if (qp->m_guard_transport_win > qp->m_win) {
            m_guardTransportWindowRaisedFlows++;
            m_guardTransportWindowExtraBytes +=
                qp->m_guard_transport_win - qp->m_win;
        }
    }
    if (m_cc_mode == CC_MODE_GUARD) {
        bool bounded_whole_flow =
            m_guardTransportWindowWholeFlowFirstGate &&
            size <= qp->GetGuardTransportWin();
        qp->m_guard_first_grant_win =
            (!m_guardTransportWindowFloorAfterFirstGrant || bounded_whole_flow) ?
                qp->GetGuardTransportWin() : qp->m_win;
        if (bounded_whole_flow && qp->GetGuardTransportWin() > qp->m_win) {
            m_guardTransportWindowWholeFlowFirstGateFlows++;
        }
    }

    if (m_cc_mode == CC_MODE_GUARD && m_guardOneRttBypass) {
        DataRate line_rate = m_nic[nic_idx].dev->GetDataRate();
        uint64_t bdp_bytes = baseRtt * line_rate.GetBitRate() / 8000000000lu;
        qp->m_guard_one_rtt_bypass = bdp_bytes > 0 && size <= bdp_bytes;
        if (qp->m_guard_one_rtt_bypass) m_guardOneRttBypassFlows++;
    }

    // For GUARD modes, borrow homa's idea of routing short
    // messages to higher-priority switch queues (lower pg = higher prio in
    // this codebase). All packets of one flow stay on the same pg, so the
    // QP-key / pause-check / RCC-grant routing all stay consistent.
    // Bucket boundaries match homa's unscheduled cutoffs.
    if ((m_cc_mode == 11 || m_cc_mode == 13) && m_guardSizePriority) {
        DataRate line_rate = m_nic[nic_idx].dev->GetDataRate();
        uint64_t bdp_bytes = baseRtt * line_rate.GetBitRate() / 8000000000lu;
        if (bdp_bytes == 0) bdp_bytes = 1;
        if (size < bdp_bytes / 4)           qp_pg = 1;
        else if (size < bdp_bytes / 2)      qp_pg = 2;
        else if (size < bdp_bytes)          qp_pg = 3;
        else if (size < 2 * bdp_bytes)      qp_pg = 4;
        else if (size < 4 * bdp_bytes)      qp_pg = 5;
        else if (size < 8 * bdp_bytes)      qp_pg = 6;
        else                                qp_pg = 7;
        qp->m_pg = qp_pg;
    }

    if (m_cc_mode == CC_MODE_GUARD && m_guardInitialWindowPriority) {
        NS_ABORT_MSG_IF(!m_guardSizePriority,
                        "GUARD initial-window priority requires size priority");
        if (size > qp->GetGuardFirstGrantWin() && qp->m_pg > 3) {
            m_guardInitialWindowPriorityFlows++;
        }
    }

    if ((m_cc_mode == 11 || m_cc_mode == 13) &&
        !m_guardMembershipCoalesceWindow.IsZero()) {
        DataRate line_rate = m_nic[nic_idx].dev->GetDataRate();
        uint64_t bdp_bytes = baseRtt * line_rate.GetBitRate() / 8000000000lu;
        bool receiver_will_register = !m_guardSelectiveRegistration ||
                                      (bdp_bytes > 0 && size > bdp_bytes);
        qp->m_guard_wait_first_grant = receiver_will_register;
        if (receiver_will_register) m_guardFirstGrantGatedFlows++;
    }

    uint64_t key = GetQpKey(dip.Get(), sport, dport, qp_pg);
    GuardQpIdentity identity = {sip.Get(), dip.Get(), sport, dport, qp_pg};
    auto live = m_qpIdentity.find(key);
    auto tombstone = m_qpTombstoneIdentity.find(key);
    GuardQpInsertionDisposition insertion = ClassifyGuardQpInsertion(
        live == m_qpIdentity.end() ? NULL : &live->second,
        tombstone == m_qpTombstoneIdentity.end() ? NULL : &tombstone->second,
        identity);
    NS_ABORT_MSG_IF(insertion == GUARD_QP_INSERT_LIVE_DUPLICATE,
                    "GUARD QP exact live tuple cannot be inserted twice");
    NS_ABORT_MSG_IF(insertion == GUARD_QP_INSERT_LIVE_COLLISION,
                    "GUARD QP legacy-key collision on live insertion");
    NS_ABORT_MSG_IF(insertion == GUARD_QP_INSERT_RETIRED_TUPLE,
                    "GUARD QP completed tuple cannot be reused");
    NS_ABORT_MSG_IF(insertion == GUARD_QP_INSERT_TOMBSTONE_COLLISION,
                    "GUARD QP legacy-key collision against tombstone");
    m_nic[nic_idx].qpGrp->AddQp(qp);
    m_qpMap[key] = qp;
    m_qpIdentity[key] = identity;

    // set init variables
    DataRate m_bps = m_nic[nic_idx].dev->GetDataRate();
    qp->m_rate = m_bps;
    qp->m_max_rate = m_bps;
    if (m_cc_mode == 1) {
        qp->mlx.m_targetRate = m_bps;
    } else if (m_cc_mode == 3 || m_cc_mode == 11 || m_cc_mode == 13) {
        qp->hp.m_curRate = m_bps;
        if (m_multipleRate) {
            for (uint32_t i = 0; i < IntHeader::maxHop; i++) qp->hp.hopState[i].Rc = m_bps;
        }
        // grantRate initialization (only used when the GUARD active layer enforces a cap)
        qp->hp.m_grantRate = m_bps;
    } else if (m_cc_mode == 7) {
        qp->tmly.m_curRate = m_bps;
    } else if (m_cc_mode == 10) {
        qp->homa_simple.m_curRate = m_bps;
        qp->homa_simple.is_request_package = true;
        uint64_t bdp_bytes = baseRtt * m_bps.GetBitRate() / 8000000000lu;
        // unscheduled credit (one credit = one packet)
        qp->homa_simple.m_credit_package = (std::min(bdp_bytes, size) + m_mtu - 1) / m_mtu;
        // bytes still need to grant
        qp->homa_simple.m_request_bytes = size > bdp_bytes ? size - bdp_bytes : 0;
        // unscheduled bytes (free initial burst)
        qp->homa_simple.m_unscheduled_bytes =
            size < bdp_bytes ? std::max(size, (uint64_t)m_mtu) : std::max(bdp_bytes, (uint64_t)m_mtu);
        qp->homa_simple.m_bdp = bdp_bytes;
    } else if (m_cc_mode == 12) {
        // Homa per-QP init. Sender may emit up to m_unscheduled_bytes
        // immediately; bytes beyond that need GRANTs from the receiver.
        uint64_t bdp_bytes = baseRtt * m_bps.GetBitRate() / 8000000000lu;
        qp->homa.m_bdp = bdp_bytes;
        qp->homa.m_unscheduled_bytes =
            size < bdp_bytes ? std::max(size, (uint64_t)m_mtu) : std::max(bdp_bytes, (uint64_t)m_mtu);
        qp->homa.m_granted_offset = qp->homa.m_unscheduled_bytes;
        // The run-level workload profile divides unscheduled bytes evenly
        // across queues 1..U.  All scheduled DATA uses U+1..7, so the two
        // classes never overlap and unscheduled bytes always preempt grants.
        qp->homa.m_unscheduled_priority = m_homaUnscheduledLevels;
        for (size_t index = 0; index < m_homaUnscheduledCutoffs.size(); index++) {
            if (size <= m_homaUnscheduledCutoffs[index]) {
                qp->homa.m_unscheduled_priority = (uint8_t)(index + 1);
                break;
            }
        }
        // Default scheduled priority until the first GRANT arrives — pick the
        // bottom of the scheduled range so it gets out of the way of unscheduled
        // bytes. The receiver will overwrite this via GRANT.priority.
        qp->homa.m_grant_priority = 7;
    }
    // print_rate(PeekPointer(qp));

    // Notify Nic
    m_nic[nic_idx].dev->NewQp(qp);
}

void RdmaHw::DeleteQueuePair(Ptr<RdmaQueuePair> qp) {
    // remove qp from the m_qpMap
    uint64_t key = GetQpKey(qp->dip.Get(), qp->sport, qp->dport, qp->m_pg);

    // record to Akashic record
    NS_ASSERT(akashic_Qp.find(key) == akashic_Qp.end());  // should not be already existing
    akashic_Qp.insert(key);
    GuardQpIdentity identity = {qp->sip.Get(), qp->dip.Get(), qp->sport,
                                qp->dport, qp->m_pg};
    auto live = m_qpIdentity.find(key);
    NS_ABORT_MSG_IF(live == m_qpIdentity.end() || live->second != identity,
                    "GUARD QP identity changed before deletion");
    auto dead = m_qpTombstoneIdentity.find(key);
    NS_ABORT_MSG_IF(dead != m_qpTombstoneIdentity.end() && dead->second != identity,
                    "GUARD QP tombstone collision on deletion");
    m_qpTombstoneIdentity[key] = identity;

    // delete
    m_qpMap.erase(key);
    m_qpIdentity.erase(key);
}

// DATA UDP's src = this key's dst (receiver's dst)
uint64_t RdmaHw::GetRxQpKey(uint32_t dip, uint16_t dport, uint16_t sport,
                            uint16_t pg) {  // Receiver perspective
    return ((uint64_t)dip << 32) | ((uint64_t)pg << 16) | ((uint64_t)sport << 16) |
           (uint64_t)dport;  // srcIP, srcPort
}

// src/dst are already flipped (this is calleld by UDP Data packet)
Ptr<RdmaRxQueuePair> RdmaHw::GetRxQp(uint32_t sip, uint32_t dip, uint16_t sport, uint16_t dport,
                                     uint16_t pg, bool create) {
    uint64_t rxKey = GetRxQpKey(dip, dport, sport, pg);
    GuardQpIdentity identity = {sip, dip, sport, dport, pg};
    auto live_identity = m_rxQpIdentity.find(rxKey);
    NS_ABORT_MSG_IF(live_identity != m_rxQpIdentity.end() &&
                        live_identity->second != identity,
                    "GUARD RxQP legacy-key collision on live lookup");
    auto dead_identity = m_rxQpTombstoneIdentity.find(rxKey);
    NS_ABORT_MSG_IF(dead_identity != m_rxQpTombstoneIdentity.end() &&
                        dead_identity->second != identity,
                    "GUARD RxQP legacy-key collision on tombstone lookup");
    if (dead_identity != m_rxQpTombstoneIdentity.end()) {
        // Tuple reuse is not supported: delayed DATA for a completed flow
        // must hit the akashic drop path, never resurrect receiver state.
        return NULL;
    }
    auto it = m_rxQpMap.find(rxKey);

    // main memory lookup
    if (it != m_rxQpMap.end()) return it->second;

    if (create) {
        // create new rx qp
        Ptr<RdmaRxQueuePair> q = CreateObject<RdmaRxQueuePair>();
        // init the qp
        q->sip = sip;
        q->dip = dip;
        q->sport = sport;
        q->dport = dport;
        q->m_ecn_source.qIndex = pg;
        q->m_flow_id = -1;     // unknown
        m_rxQpMap[rxKey] = q;  // store in map
        m_rxQpIdentity[rxKey] = identity;
        return q;
    }
    return NULL;
}
uint32_t RdmaHw::GetNicIdxOfRxQp(Ptr<RdmaRxQueuePair> q) {
    auto &v = m_rtTable[q->dip];
    if (v.size() > 0) {
        return v[q->GetHash() % v.size()];
    }
    NS_ASSERT_MSG(false, "We assume at least one NIC is alive");
    std::cout << "We assume at least one NIC is alive" << std::endl;
    exit(1);
}

// Receiver's perspective?
void RdmaHw::DeleteRxQp(uint32_t dip, uint16_t dport, uint16_t sport, uint16_t pg) {
    uint64_t key = GetRxQpKey(dip, dport, sport, pg);

    // record to Akashic record
    NS_ASSERT(akashic_RxQp.find(key) == akashic_RxQp.end());  // should not be already existing
    akashic_RxQp.insert(key);
    auto live = m_rxQpIdentity.find(key);
    NS_ABORT_MSG_IF(live == m_rxQpIdentity.end(),
                    "GUARD RxQP deletion lost its full identity");
    auto dead = m_rxQpTombstoneIdentity.find(key);
    NS_ABORT_MSG_IF(dead != m_rxQpTombstoneIdentity.end() &&
                        dead->second != live->second,
                    "GUARD RxQP tombstone collision on deletion");
    m_rxQpTombstoneIdentity[key] = live->second;

    // delete
    m_rxQpMap.erase(key);
    m_rxQpIdentity.erase(key);
}

int RdmaHw::ReceiveUdp(Ptr<Packet> p, CustomHeader &ch) {
    uint8_t ecnbits = ch.GetIpv4EcnBits();

    uint32_t payload_size = p->GetSize() - ch.GetSerializedSize();

    // find corresponding rx queue pair.
    // homa pins the rxQp key's pg to 0 so that per-packet udp.pg
    // variation (unscheduled cutoffs / overcommit slots) doesn't fan one
    // logical flow out across multiple rxQp entries.
    uint16_t rx_pg = (m_cc_mode == 12) ? 0 : ch.udp.pg;
    Ptr<RdmaRxQueuePair> rxQp =
        GetRxQp(ch.dip, ch.sip, ch.udp.dport, ch.udp.sport, rx_pg, true);
    if (rxQp == NULL) {
        uint64_t rxKey = GetRxQpKey(ch.sip, ch.udp.sport, ch.udp.dport, rx_pg);
        if (akashic_RxQp.find(rxKey) != akashic_RxQp.end()) {
            // printf("[GetRxQPUDP] Akashic access: %u(%d) -> %u(%d)\n", this->m_node->GetId(),
            // ch.udp.dport, ch.sip, ch.udp.sport);
            return 1;  // just drop
        } else {
            printf("ERROR: UDP NIC cannot find the flow\n");
            exit(1);
        }
    }

    if (ecnbits != 0) {
        rxQp->m_ecn_source.ecnbits |= ecnbits;
        rxQp->m_ecn_source.qfb++;
    }

    rxQp->m_ecn_source.total++;
    rxQp->m_milestone_rx = m_ack_interval;

    uint64_t flow_size = 0;
    FlowIDNUMTag fit;
    bool has_flow_tag = p->PeekPacketTag(fit);
    if (has_flow_tag) {
        if (rxQp->m_flow_id < 0) {
            rxQp->m_flow_id = fit.GetId();
        }
        flow_size = fit.GetFlowSize();
        rxQp->m_guard_flow_size = flow_size;
    }
    if (!rxQp->m_seen_first_pkt) {
        rxQp->m_first_pkt_time = Simulator::Now();
        rxQp->m_seen_first_pkt = true;
    }

    // Homa owns receive ordering and loss detection. Do not feed its DATA
    // through the repository's RC ACK/NACK path: doing so mixes Go-Back-N
    // recovery into the Homa baseline and creates NACKs from benign packet
    // reordering. A completion-only simulator notice closes the one-way QP.
    if (m_cc_mode == 12) {
        ReceiveHomaData(rxQp, p, ch);
        return 0;
    }

    bool cnp_check = false;
    int x = ReceiverCheckSeq(ch.udp.seq, rxQp, payload_size, cnp_check);
    if (m_guardTransitionPrefixBarrierEnabled &&
        m_guardTransitionPrefixBarrierWaiting) {
        CheckGuardTransitionPrefixBarrier();
    }

    // A <=1-BDP GUARD flow cannot act on closed-loop telemetry before its
    // bounded first-RTT burst is already in flight.  With lossless PFC, keep
    // only its cumulative completion ACK (and never suppress a NACK).  This
    // removes reverse-path per-packet ACK traffic without weakening recovery:
    // IRN retains its normal ACK stream, while any out-of-order packet still
    // produces x==2 below.
    if (m_cc_mode == CC_MODE_GUARD && !m_irn && x == 1 && has_flow_tag &&
        flow_size > 0 && rxQp->ReceiverNextExpectedSeq < flow_size) {
        FlowStatTag fst;
        uint64_t bdp = 104000;
        if (p->PeekPacketTag(fst) && fst.HasBaseRtt()) {
            uint32_t nic_idx = GetNicIdxOfRxQp(rxQp);
            DataRate rate = m_nic[nic_idx].dev->GetDataRate();
            bdp = (uint64_t)(fst.GetBaseRttSeconds() * rate.GetBitRate() / 8.0);
            if (bdp == 0) bdp = 104000;
        }
        if (m_guardOneRttBypass && flow_size <= bdp) {
            x = 5;
            m_guardOneRttAcksSuppressed++;
        } else if (m_guardAckIntervalPackets > 1 &&
                   rxQp->m_guard_last_ack_seq != 0 &&
                   rxQp->ReceiverNextExpectedSeq <
                       rxQp->m_guard_last_ack_seq +
                           (uint64_t)m_guardAckIntervalPackets * m_mtu) {
            x = 5;
            m_guardLongAcksSuppressed++;
        }
    }
    if (m_cc_mode == CC_MODE_GUARD && x == 1) {
        rxQp->m_guard_last_ack_seq = rxQp->ReceiverNextExpectedSeq;
    }

    // x==2 is a recovery NACK caused by an out-of-order packet. x==6 is
    // encoded with protocol 0xFD in IRN mode but semantically acknowledges
    // complete recovery, so do not count it as a recovery request.
    if (x == 2) {
        m_recoveryNacksGenerated++;
        if (m_irn) m_irnNacksGenerated++;
    }

    if (x == 1 || x == 2 || x == 6) {  // generate ACK or NACK
        qbbHeader seqh;
        seqh.SetSeq(rxQp->ReceiverNextExpectedSeq);
        // homa: data packets carry varying udp.pg (cutoff/grant slot),
        // but the sender QP key uses pg=0 (overridden in AddQueuePair). The
        // ACK must use pg=0 too so the sender can find the QP.
        seqh.SetPG(m_cc_mode == 12 ? (uint16_t)0 : ch.udp.pg);
        seqh.SetSport(ch.udp.dport);
        seqh.SetDport(ch.udp.sport);

        // guard: strip last-hop INT info (the receiver grant handles this link)
        if (m_cc_mode == 11) {
            m_guardIntHopsBeforeStrip += ch.udp.ih.nhop;
            if (!m_guardKeepLastHopInt && ch.udp.ih.nhop > 0) {
                int last_hop = --ch.udp.ih.nhop;
                memset(&ch.udp.ih.hop[last_hop], 0, sizeof(ch.udp.ih.hop[last_hop]));
                m_guardIntRecordsStripped++;
            }
            m_guardIntHopsAfterStrip += ch.udp.ih.nhop;
        }
        seqh.SetIntHeader(ch.udp.ih);

        if (m_irn) {
            if (x == 2) {
                seqh.SetIrnNack(ch.udp.seq);
                seqh.SetIrnNackSize(payload_size);
            } else {
                seqh.SetIrnNack(0);  // NACK without ackSyndrome (ACK) in loss recovery mode
                seqh.SetIrnNackSize(0);
            }
        }

        if (ecnbits || cnp_check) {  // NACK accompanies with CNP packet
            // XXX monitor CNP generation at sender
            cnp_total++;
            if (ecnbits) cnp_by_ecn++;
            if (cnp_check) cnp_by_ooo++;
            if (m_cc_mode == CC_MODE_DCQCN) {
                if (ecnbits) m_dcqcnCnpGeneratedEcn++;
                if (cnp_check) m_dcqcnCnpGeneratedOoo++;
            }
            seqh.SetCnp();
        }

        Ptr<Packet> newp =
            Create<Packet>(std::max(60 - 14 - 20 - (int)seqh.GetSerializedSize(), 0));
        newp->AddHeader(seqh);

        Ipv4Header head;  // Prepare IPv4 header
        head.SetDestination(Ipv4Address(ch.sip));
        head.SetSource(Ipv4Address(ch.dip));
        head.SetProtocol(x == 1 ? 0xFC : 0xFD);  // ack=0xFC nack=0xFD
        head.SetTtl(64);
        head.SetPayloadSize(newp->GetSize());
        head.SetIdentification(rxQp->m_ipid++);

        if (has_flow_tag) {
            newp->AddPacketTag(fit);
        }

        newp->AddHeader(head);
        AddHeader(newp, 0x800);  // Attach PPP header

        // send
        uint32_t nic_idx = GetNicIdxOfRxQp(rxQp);
        m_nic[nic_idx].dev->RdmaEnqueueHighPrioQ(newp);
        m_nic[nic_idx].dev->TriggerTransmit();
    }

    // GUARD and its active-only ablation: receiver-driven rate cap + proactive release.
    if (m_cc_mode == 11 || m_cc_mode == 13) {
        if (flow_size == 0) {
            std::cout << "ERROR: flow_size==0 in ReceiveUdp (guard)\n";
            exit(1);
        }

        rxQp->m_guard_pg = ch.udp.pg;
        uint64_t guard_bdp = 104000;
        FlowStatTag fst;
        if (p->PeekPacketTag(fst)) {
            uint8_t flow_tag = fst.GetType();
            bool flow_start = flow_tag == FlowStatTag::FLOW_START ||
                              flow_tag == FlowStatTag::FLOW_START_AND_END;
            // Derive BDP from this flow's baseRtt + receiver's line rate
            // instead of the hardcoded 104000 (which assumed leaf_spine 100G/
            // 8.32µs RTT). On other topologies (different RTT or rate) the
            // hardcoded value would mis-classify short vs. long flows.
            if (fst.HasBaseRtt()) {
                uint32_t nic_idx = GetNicIdxOfRxQp(rxQp);
                DataRate rate = m_nic[nic_idx].dev->GetDataRate();
                guard_bdp =
                    (uint64_t)(fst.GetBaseRttSeconds() * rate.GetBitRate() / 8.0);
                if (guard_bdp == 0) guard_bdp = 104000;  // safety
            }
            if (rxQp->m_base_rtt_sec == 0 && fst.HasBaseRtt()) {
                rxQp->m_base_rtt_sec = fst.GetBaseRttSeconds();
            }
            if (!rxQp->m_guard_has_first_grant_gate_bytes &&
                fst.HasFirstGrantGateBytes()) {
                rxQp->m_guard_first_grant_gate_bytes =
                    fst.GetFirstGrantGateBytes();
                rxQp->m_guard_has_first_grant_gate_bytes = true;
            }
            if (flow_start && (!m_guardSelectiveRegistration || flow_size > guard_bdp)) {
                HandleRccRequest(rxQp, p, ch);
            }
        } else {
            std::cout << "ERROR: no FlowStatTag in ReceiveUdp (guard)\n";
            exit(1);
        }

        uint32_t currentSeq = rxQp->ReceiverNextExpectedSeq;
        uint64_t v_remain = (flow_size > currentSeq) ? (flow_size - currentSeq) : 0;
        if (m_rate_flow_ctl_set.find(PeekPointer(rxQp)) != m_rate_flow_ctl_set.end()) {
            rxQp->m_guard_interval_bytes += payload_size;
        }

        // Completion is based on contiguous receiver progress rather than on
        // seeing FLOW_END: the nominal last packet may arrive out of order.
        // This is also the sole release path when OFLM is disabled.
        if (v_remain == 0) {
            auto tracked_drain = m_guardDrainingRecords.find(
                PeekPointer(rxQp));
            bool pending_drain = m_guardSerializedDraining &&
                tracked_drain != m_guardDrainingRecords.end() &&
                tracked_drain->second.state == GUARD_DRAIN_PENDING;
            bool completion_release =
                pending_drain
                    ? HandleRccRemove(rxQp, GUARD_RELEASE_COMPLETION, 0)
                    : tracked_drain != m_guardDrainingRecords.end()
                          ? ReleaseGuardDrainingOnCompletion(rxQp)
                          : HandleRccRemove(
                                rxQp, GUARD_RELEASE_COMPLETION, 0);
            if (completion_release) {
                m_guardCompletionReleases++;
            }
            TraceGuardCompletion(rxQp);
        } else if (m_guardProactiveRelease &&
                   (m_guardMembershipCoalesceWindow.IsZero() ||
                    rxQp->m_guard_grant_generation_acked) &&
                   m_rate_flow_ctl_set.find(PeekPointer(rxQp)) != m_rate_flow_ctl_set.end()) {
            Time now = Simulator::Now();
            if (rxQp->m_last_pkt_time.IsZero()) {
                rxQp->m_est_rate = 0;
            } else {
                double interval = (now - rxQp->m_last_pkt_time).GetSeconds();
                if (interval > 0) {
                    double inst_rate = (double)payload_size / interval; // Bytes/s
                    rxQp->m_est_rate = m_guardEwmaBeta * rxQp->m_est_rate +
                                       (1.0 - m_guardEwmaBeta) * inst_rate;
                }
            }
            rxQp->m_last_pkt_time = now;

            double v_th_double =
                rxQp->m_est_rate * rxQp->m_base_rtt_sec * m_guardReleaseGamma;
            uint64_t v_th = (uint64_t)v_th_double;

            if (v_remain < v_th && !rxQp->m_proactive_released) {
                bool released = m_guardSerializedDraining
                                    ? RequestGuardProactiveDrain(rxQp, v_remain)
                                    : HandleRccRemove(
                                          rxQp, GUARD_RELEASE_PROACTIVE,
                                          v_remain);
                if (released) {
                    m_guardProactiveReleases++;
                }
                rxQp->m_proactive_released = true;
            }
        }
        if (v_remain > 0 && m_guardRemainingAware && m_guardGrantRefreshBdps > 0 &&
            m_rate_flow_ctl_set.find(PeekPointer(rxQp)) != m_rate_flow_ctl_set.end()) {
            uint64_t refresh_bytes =
                std::max<uint64_t>(m_mtu,
                                   (uint64_t)(m_guardGrantRefreshBdps * guard_bdp));
            if (currentSeq >= rxQp->m_guard_last_schedule_seq + refresh_bytes) {
                m_guardRemainingRefreshEvents++;
                if (m_guardSerializedProgressRefresh) {
                    RequestGuardProgressRefresh(PeekPointer(rxQp), currentSeq);
                } else {
                    rxQp->m_guard_last_schedule_seq = currentSeq;
                    if (IsGuardMembershipDirty() ||
                        (m_guardMixedPgVectorFastpath &&
                         (m_guardFrozenVectorActive ||
                          m_guardFastpathPhase != GUARD_FASTPATH_IDLE))) {
                        m_guardProgressEventsCoalesced++;
                    } else {
                        RedistributeGuardRates("progress");
                    }
                }
            }
        }
    }

    // homa-simple (cc_mode 10): receiver-driven credit scheduling
    if (m_cc_mode == 10) {
        if (ch.udp.is_request_package) {
            ReceiveHomaSimpleRequest(rxQp, p, ch);
        } else {
            ReceiveHomaSimpleData(rxQp, p, ch);
        }
    }

    return 0;
}

int RdmaHw::ReceiveCnp(Ptr<Packet> p, CustomHeader &ch) {
    std::cerr << "ReceiveCnp is called. Exit this program." << std::endl;
    exit(1);
    // QCN on NIC
    // This is a Congestion signal
    // Then, extract data from the congestion packet.
    // We assume, without verify, the packet is destinated to me
    uint32_t qIndex = ch.cnp.qIndex;
    if (qIndex == 1) {  // DCTCP
        std::cout << "TCP--ignore\n";
        return 0;
    }
    NS_ASSERT(ch.cnp.fid == ch.udp.dport);
    uint16_t udpport = ch.cnp.fid;  // corresponds to the sport (CNP's dport)
    uint16_t sport = ch.udp.sport;  // corresponds to the dport (CNP's sport)
    uint8_t ecnbits = ch.cnp.ecnBits;
    uint16_t qfb = ch.cnp.qfb;
    uint16_t total = ch.cnp.total;

    uint32_t i;
    // get qp
    uint64_t key = GetQpKey(ch.sip, udpport, sport, qIndex);
    Ptr<RdmaQueuePair> qp = GetQp(ch.dip, ch.sip, udpport, sport, qIndex);
    if (qp == NULL) {
        // lookup akashic memory
        if (akashic_Qp.find(key) != akashic_Qp.end()) {
            // printf("[GetQPCNP] Akashic access: %u(%d) -> %u(%d)\n", this->m_node->GetId(),
            // udpport, ch.sip, sport);
            return 1;  // just drop
        } else {
            printf("ERROR: QCN NIC cannot find the flow\n");
            exit(1);
        }
    }
    // get nic
    uint32_t nic_idx = GetNicIdxOfQp(qp);
    Ptr<QbbNetDevice> dev = m_nic[nic_idx].dev;

    if (qp->m_rate == 0)  // lazy initialization
    {
        qp->m_rate = dev->GetDataRate();
        if (m_cc_mode == 1) {
            qp->mlx.m_targetRate = dev->GetDataRate();
        } else if (m_cc_mode == 3 || m_cc_mode == 11 || m_cc_mode == 13) {
            qp->hp.m_curRate = dev->GetDataRate();
            if (m_multipleRate) {
                for (uint32_t i = 0; i < IntHeader::maxHop; i++)
                    qp->hp.hopState[i].Rc = dev->GetDataRate();
            }
        } else if (m_cc_mode == 7) {
            qp->tmly.m_curRate = dev->GetDataRate();
        } else if (m_cc_mode == 10) {
            qp->homa_simple.m_curRate = dev->GetDataRate();
        }
    }
    return 0;
}

int RdmaHw::ReceiveRate(Ptr<Packet> p, CustomHeader &ch) {
    // find qp
    bool compact = ch.l3Prot == CustomHeader::GUARD_RATE_GRANT;
    uint16_t pg = compact ? ch.grant.pg : ch.ack.pg;
    uint16_t dport = compact ? ch.grant.dport : ch.ack.dport;
    uint16_t sport = compact ? ch.grant.sport : ch.ack.sport;
    uint64_t key = GetQpKey(ch.sip, dport, sport, pg);
    Ptr<RdmaQueuePair> qp = GetQp(ch.dip, ch.sip, dport, sport, pg);

    if (qp == NULL) {
        if (akashic_Qp.find(key) != akashic_Qp.end()) {
            // std::cout << "[ReceiveRate] Grant packet for completed flow, ignoring. "
            //           << "sip: " << Settings::ip_to_node_id(Ipv4Address(ch.sip)) << ", sport: " << sport
            //           << ", dip: " << Settings::ip_to_node_id(Ipv4Address(ch.dip)) << ", dport: " << dport << std::endl;
        } else {
            // std::cout << "[ReceiveRate] QP not found and not in Akashic record! "
            //           << "sip: " << Settings::ip_to_node_id(Ipv4Address(ch.sip)) << ", sport: " << sport
            //           << ", dip: " << Settings::ip_to_node_id(Ipv4Address(ch.dip)) << ", dport: " << dport
            //           << " - This might indicate a race condition." << std::endl;
        }
        return 0;
    }

    uint32_t generation = compact ? ch.grant.generation : 0;
    bool ack_required = compact && (ch.grant.ackRequired & 0x01) != 0;
    uint8_t phase_tag = compact ? (ch.grant.ackRequired >> 1) & 0x07 : 0;
    uint32_t received_val = compact ? ch.grant.rateMbps : ch.ack.seq;
    uint64_t received_rate_bps = static_cast<uint64_t>(received_val) * 1000000;
    if (compact && !m_guardMembershipCoalesceWindow.IsZero() && generation == 0) {
        m_guardStaleGrantsReceived++;
        m_guardGenerationZeroRejected++;
        TraceGuardGrantReceive(qp, p, 0, generation, "grant_generation_zero",
                               ack_required, phase_tag);
        return 0;
    }
    if (compact && generation > 0 && generation < qp->m_guard_last_grant_generation) {
        m_guardStaleGrantsReceived++;
        TraceGuardGrantReceive(qp, p, 0, generation, "grant_stale", ack_required,
                               phase_tag);
        return 0;
    }
    if (compact && generation > 0 && generation == qp->m_guard_last_grant_generation &&
        (received_rate_bps != qp->m_guard_last_generation_rate_bps ||
         ack_required != qp->m_guard_last_generation_ack_required ||
         phase_tag != qp->m_guard_last_generation_phase_tag)) {
        m_guardStaleGrantsReceived++;
        m_guardGenerationMismatchRejected++;
        TraceGuardGrantReceive(qp, p, 0, generation, "grant_generation_mismatch",
                               ack_required, phase_tag);
        return 0;
    }

    // Use string constructor to avoid overflow
    std::string rate_str = std::to_string(received_val) + "Mbps";
    DataRate curRate(rate_str);

    qp->hp.m_grantRate = curRate;
    bool released_first_grant_gate = false;
    if (qp->m_guard_wait_first_grant) {
        qp->m_guard_wait_first_grant = false;
        m_guardFirstGrantGateReleases++;
        released_first_grant_gate = true;
        if (GuardSmallSetFastpathEnabled() &&
            phase_tag != GUARD_GRANT_PHASE_FAST_ACTIVATE &&
            phase_tag != GUARD_GRANT_PHASE_TRANSITION_ACTIVATE) {
            m_guardFastpathEarlyUnlocks++;
        }
    }
    m_guardRateGrantsReceived++;
    TraceGuardGrantReceive(qp, p, curRate.GetBitRate(), generation, "received",
                           ack_required, phase_tag);
    if (compact && generation > 0) {
        // Commit the already validated receiver decision before composing the
        // pacing rate.  V26 authority must take effect on this grant rather
        // than waiting for an unrelated later HPCC sample.
        qp->m_guard_last_grant_generation = generation;
        qp->m_guard_last_generation_rate_bps = received_rate_bps;
        qp->m_guard_last_generation_ack_required = ack_required;
        qp->m_guard_last_generation_phase_tag = phase_tag;
    }
    DataRate old_rate = qp->m_rate;
    SyncHwRate(qp, qp->hp.m_curRate);
    if (compact && generation > 0) {
        if (ack_required) {
            if (GuardSmallSetFastpathEnabled()) {
                CountGuardFastpathAckFrame(phase_tag);
            }
            SendGuardGrantAck(qp, generation, phase_tag);
        }
    }
    const char *binding = (qp->m_guard_tail_bypass ||
                           UsesGuardElephantReceiverAuthority(qp))
                              ? "grant"
                              : qp->hp.m_curRate < qp->hp.m_grantRate
                                    ? "reactive"
                                    : qp->hp.m_grantRate < qp->hp.m_curRate ? "grant" : "tie";
    bool changed = qp->m_rate != old_rate;
    if (changed) m_guardGrantEventRateChanges++;
    TraceGuardControllerEvent(qp, "grant", qp->hp.m_curRate, binding,
                              changed, false, 0, qp->snd_nxt, -1.0,
                              m_targetUtil, -1.0);
    if (released_first_grant_gate) {
        uint32_t nic_idx = GetNicIdxOfQp(qp);
        m_nic[nic_idx].dev->TriggerTransmit();
    }

    return 0;
}

int RdmaHw::ReceiveGuardGrantAck(Ptr<Packet> p, CustomHeader &ch) {
    GuardQpIdentity wire_identity = {
        ch.dip, ch.sip, ch.grantAck.dport, ch.grantAck.sport,
        ch.grantAck.pg};
    uint32_t generation = ch.grantAck.generation;
    uint8_t phase_tag = ch.grantAck.phaseTag;
    m_guardGrantAckBytesReceived += p->GetSize();

    // A receiver QP can finish after its required ACK is sent but before that
    // ACK returns.  Authenticate that packet from the pointer-free retired
    // ledger before consulting live/tombstoned QP state.
    size_t retired_index = FindGuardRetiredAckRecord(wire_identity, generation);
    if (retired_index != m_guardRetiredAckRecords.size()) {
        const GuardRetiredAckRecord &retired =
            m_guardRetiredAckRecords[retired_index];
        if (!DoesGuardRetiredAckMatch(
                retired, wire_identity, generation, phase_tag)) {
            m_guardGrantAcksStale++;
            TraceGuardGrantAckReceive(NULL, ch, p, "ack_stale", &retired);
            return 0;
        }
        NS_ABORT_MSG_IF(
            retired.targetMbps == 0 || retired.transactionId == 0 ||
                retired.retiredNs >
                    static_cast<uint64_t>(Simulator::Now().GetNanoSeconds()),
            "GUARD V14 retired ACK record lost immutable provenance");
        m_guardGrantAcksReceived++;
        m_guardRetiredAcksReceived++;
        CountGuardAcceptedAck(retired.phaseTag);
        TraceGuardGrantAckReceive(NULL, ch, p, "ack_retired", &retired);
        m_guardRetiredAckRecords.erase(
            m_guardRetiredAckRecords.begin() + retired_index);
        return 0;
    }

    Ptr<RdmaRxQueuePair> rx_qp = GetRxQp(
        ch.dip, ch.sip, ch.grantAck.dport, ch.grantAck.sport,
        ch.grantAck.pg, false);
    if (rx_qp == NULL ||
        m_rate_flow_ctl_set.find(PeekPointer(rx_qp)) == m_rate_flow_ctl_set.end() ||
        generation == 0 || generation != rx_qp->m_guard_grant_generation ||
        rx_qp->m_guard_grant_generation_acked) {
        m_guardGrantAcksStale++;
        TraceGuardGrantAckReceive(rx_qp, ch, p, "ack_stale");
        return 0;
    }
    if (m_guardMixedPgVectorFastpath) {
        auto ledger = m_guardGenerationLedger.find(PeekPointer(rx_qp));
        GuardQpIdentity identity = {rx_qp->sip, rx_qp->dip, rx_qp->sport,
                                    rx_qp->dport, rx_qp->m_guard_pg};
        auto *frozen = GetGuardFrozenTarget(PeekPointer(rx_qp));
        NS_ABORT_MSG_IF(
            ledger == m_guardGenerationLedger.end() ||
                ledger->second.tombstone ||
                ledger->second.identity != identity ||
                ledger->second.generation != generation ||
                ledger->second.phaseTag != phase_tag ||
                !IsGuardLedgerActionCompatible(ledger->second.action,
                                               phase_tag) ||
                frozen == NULL || frozen->identity != identity ||
                frozen->targetMbps != ledger->second.targetMbps,
            "GUARD V14 ACK does not match the frozen generation ledger");
        rx_qp->m_guard_last_acked_generation = ledger->second.generation;
        rx_qp->m_guard_last_acked_upper_bound_bps =
            static_cast<uint64_t>(ledger->second.targetMbps) * 1000000ULL;
    } else {
        rx_qp->m_guard_last_acked_generation = generation;
        rx_qp->m_guard_last_acked_upper_bound_bps =
            rx_qp->m_guard_grant_rate_bps;
    }

    rx_qp->m_guard_grant_generation_acked = true;
    rx_qp->m_guard_grant_upper_bound_bps =
        rx_qp->m_guard_last_acked_upper_bound_bps;
    m_guardGrantAcksReceived++;
    CountGuardAcceptedAck(phase_tag);
    if (m_guardPendingGrantAcks > 0) m_guardPendingGrantAcks--;
    TraceGuardGrantAckReceive(rx_qp, ch, p, "ack_received");
    if (m_guardPendingGrantAcks == 0) {
        m_guardFullyAckedBatches++;
        if (m_guardReliabilityRefreshEvent.IsRunning()) {
            Simulator::Cancel(m_guardReliabilityRefreshEvent);
        }
        if (GuardSmallSetFastpathEnabled()) FinishGuardFastpathGeneration();
    }
    return 0;
}

int RdmaHw::ReceiveGuardCapReport(Ptr<Packet> /*p*/, CustomHeader &ch) {
    uint16_t pg = ch.ack.pg;
    Ptr<RdmaRxQueuePair> rx_qp = GetRxQp(
        ch.dip, ch.sip, ch.ack.dport, ch.ack.sport, pg, false);
    if (rx_qp == NULL ||
        m_rate_flow_ctl_set.find(PeekPointer(rx_qp)) == m_rate_flow_ctl_set.end()) {
        return 0;
    }

    uint64_t reported_rate_bps = static_cast<uint64_t>(ch.ack.seq) * 1000000lu;
    bool fabric_bound =
        ((ch.ack.flags >> qbbHeader::FLAG_GUARD_FABRIC_BOUND) & 1) != 0;
    Time now = Simulator::Now();
    if (m_guardElephantCapSpillover || m_guardCapTriggeredRefresh) {
        Time spillover_freshness = NanoSeconds(1);
        if (rx_qp->m_base_rtt_sec > 0) {
            spillover_freshness = Seconds(4.0 * rx_qp->m_base_rtt_sec);
        }
        bool spillover_fresh_previous =
            !rx_qp->m_guard_spillover_last_report_time.IsZero() &&
            now - rx_qp->m_guard_spillover_last_report_time <=
                spillover_freshness;
        bool below_grant = rx_qp->m_guard_grant_rate_bps > 0 &&
            static_cast<__uint128_t>(reported_rate_bps) * 100U <
                static_cast<__uint128_t>(rx_qp->m_guard_grant_rate_bps) * 95U;
        bool was_eligible = rx_qp->m_guard_spillover_active;
        uint64_t old_cap_bps = rx_qp->m_guard_spillover_reported_cap_bps;
        if (fabric_bound && below_grant) {
            if (spillover_fresh_previous &&
                rx_qp->m_guard_spillover_report_fabric_bound &&
                old_cap_bps > 0) {
                // The maximum observation in the consecutive streak is the
                // conservative donor cap: spillover never reclaims a rate that any
                // confirming sample says the shortest elephant could use.
                rx_qp->m_guard_spillover_reported_cap_bps =
                    std::max(old_cap_bps, reported_rate_bps);
                if (rx_qp->m_guard_spillover_fabric_reports <
                    std::numeric_limits<uint32_t>::max()) {
                    rx_qp->m_guard_spillover_fabric_reports++;
                }
            } else {
                rx_qp->m_guard_spillover_reported_cap_bps = reported_rate_bps;
                rx_qp->m_guard_spillover_fabric_reports = 1;
            }
            rx_qp->m_guard_spillover_unbound_reports = 0;
            rx_qp->m_guard_spillover_report_fabric_bound = true;
            if (rx_qp->m_guard_spillover_fabric_reports >=
                m_guardElephantSpilloverEnterReports) {
                rx_qp->m_guard_spillover_active = true;
            }
        } else {
            rx_qp->m_guard_spillover_reported_cap_bps = 0;
            rx_qp->m_guard_spillover_fabric_reports = 0;
            bool consecutive_unbound = spillover_fresh_previous &&
                !rx_qp->m_guard_spillover_report_fabric_bound;
            if (consecutive_unbound) {
                if (rx_qp->m_guard_spillover_unbound_reports <
                    std::numeric_limits<uint32_t>::max()) {
                    rx_qp->m_guard_spillover_unbound_reports++;
                }
            } else {
                rx_qp->m_guard_spillover_unbound_reports = 1;
            }
            rx_qp->m_guard_spillover_report_fabric_bound = false;
            if (spillover_fresh_previous &&
                rx_qp->m_guard_spillover_active &&
                rx_qp->m_guard_spillover_unbound_reports <
                    m_guardElephantSpilloverExitReports) {
                // One recovery sample may be caused by the conservative
                // receiver cap itself.  Keep the active latch, but raise the
                // donor cap estimate so V24 can only reclaim less capacity.
                rx_qp->m_guard_spillover_reported_cap_bps =
                    std::max(old_cap_bps, reported_rate_bps);
            } else {
                rx_qp->m_guard_spillover_active = false;
                rx_qp->m_guard_spillover_reported_cap_bps = 0;
            }
        }
        rx_qp->m_guard_spillover_last_report_time = now;
        bool is_eligible = rx_qp->m_guard_spillover_active &&
            rx_qp->m_guard_spillover_reported_cap_bps > 0;
        uint64_t new_cap_bps = rx_qp->m_guard_spillover_reported_cap_bps;
        uint64_t cap_delta = old_cap_bps > new_cap_bps
            ? old_cap_bps - new_cap_bps : new_cap_bps - old_cap_bps;
        __uint128_t scaled_delta =
            static_cast<__uint128_t>(old_cap_bps) *
            m_guardCapRefreshMaterialPercent;
        uint64_t relative_delta = static_cast<uint64_t>(
            (scaled_delta + 99U) / 100U);
        uint64_t material_delta = std::max<uint64_t>(
            100000000ULL, relative_delta);
        if (was_eligible != is_eligible ||
            (is_eligible && cap_delta >= material_delta)) {
            RequestGuardCapacityRefresh();
        }
    }

    Time freshness = MilliSeconds(1);
    if (rx_qp->m_base_rtt_sec > 0) {
        freshness = std::max(freshness, Seconds(32.0 * rx_qp->m_base_rtt_sec));
    }
    bool fresh_previous = !rx_qp->m_guard_last_cap_report_time.IsZero() &&
                          now - rx_qp->m_guard_last_cap_report_time <= freshness;
    rx_qp->m_guard_cap_report_samples = fresh_previous
                                             ? rx_qp->m_guard_cap_report_samples + 1
                                             : 1;
    bool below_grant = rx_qp->m_guard_grant_rate_bps > 0 &&
                       reported_rate_bps * 100 <
                           rx_qp->m_guard_grant_rate_bps * 95;
    if (fabric_bound && below_grant) {
        if ((rx_qp->m_guard_cap_limited ||
             (fresh_previous && rx_qp->m_guard_report_fabric_bound)) &&
            rx_qp->m_guard_reported_rate_bps > 0) {
            rx_qp->m_guard_reported_rate_bps = std::max(
                rx_qp->m_guard_reported_rate_bps, reported_rate_bps);
        } else {
            rx_qp->m_guard_reported_rate_bps = reported_rate_bps;
        }
        rx_qp->m_guard_fabric_bound_reports =
            fresh_previous && rx_qp->m_guard_report_fabric_bound
                ? rx_qp->m_guard_fabric_bound_reports + 1
                : 1;
        rx_qp->m_guard_unbound_reports = 0;
        if (rx_qp->m_guard_fabric_bound_reports >= 3) {
            rx_qp->m_guard_cap_limited = true;
        }
    } else {
        rx_qp->m_guard_fabric_bound_reports = 0;
        rx_qp->m_guard_unbound_reports =
            fresh_previous && !rx_qp->m_guard_report_fabric_bound
                ? rx_qp->m_guard_unbound_reports + 1
                : 1;
        // Once a fabric cap has been confirmed, an unbound report means the
        // reclaimed grant may now be the limiter.  Raise its demand estimate
        // from the observed grant instead of immediately restoring C/N; the
        // headroom factor then converges upward if fabric capacity returned.
        if (rx_qp->m_guard_cap_limited) {
            rx_qp->m_guard_reported_rate_bps = std::max(
                rx_qp->m_guard_reported_rate_bps, reported_rate_bps);
        } else {
            rx_qp->m_guard_reported_rate_bps = reported_rate_bps;
        }
    }
    rx_qp->m_guard_report_fabric_bound = fabric_bound;
    rx_qp->m_guard_last_cap_report_time = now;
    m_guardCapReportsReceived++;
    if (fabric_bound) m_guardFabricBoundReportsReceived++;
    if (m_guardCapAwareReclaim) ApplyGuardCapAwareRates();
    return 0;
}

int RdmaHw::ReceiveAck(Ptr<Packet> p, CustomHeader &ch) {
    uint16_t qIndex = ch.ack.pg;
    uint16_t port = ch.ack.dport;   // sport for this host
    uint16_t sport = ch.ack.sport;  // dport for this host (sport of ACK packet)
    uint32_t seq = ch.ack.seq;
    uint8_t cnp = (ch.ack.flags >> qbbHeader::FLAG_CNP) & 1;
    int i;
    uint64_t key = GetQpKey(ch.sip, port, sport, qIndex);
    Ptr<RdmaQueuePair> qp = GetQp(ch.dip, ch.sip, port, sport, qIndex);
    if (qp == NULL) {
        // lookup akashic memory
        if (akashic_Qp.find(key) != akashic_Qp.end()) {
            // printf("[GetQPACK] Akashic access: %u(%d) -> %u(%d)\n", this->m_node->GetId(), port,
            // ch.sip, sport);
            return 1;
        } else {
            printf("ERROR: Node: %u %s - NIC cannot find the flow\n", m_node->GetId(),
                   (ch.l3Prot == 0xFC ? "ACK" : "NACK"));
            exit(1);
        }
    }

    uint32_t nic_idx = GetNicIdxOfQp(qp);
    Ptr<QbbNetDevice> dev = m_nic[nic_idx].dev;

    if (ch.l3Prot == 0xFD && ch.ack.irnNackSize != 0) {
        m_recoveryNacksReceived++;
        if (qp->irn.m_enabled) m_irnNacksReceived++;
    } else if (ch.l3Prot == 0xFD && !qp->irn.m_enabled) {
        m_recoveryNacksReceived++;
    }

    if (m_ack_interval == 0)
        std::cout << "ERROR: shouldn't receive ack\n";
    else {
        if (!m_backto0) {
            qp->Acknowledge(seq);
        } else {
            uint32_t goback_seq = seq / m_chunk * m_chunk;
            qp->Acknowledge(goback_seq);
        }
        if (qp->irn.m_enabled) {
            // handle NACK
            NS_ASSERT(ch.l3Prot == 0xFD);

            // for bdp-fc calculation update m_irn_maxAck
            if (seq > qp->irn.m_highest_ack) qp->irn.m_highest_ack = seq;

            if (ch.ack.irnNackSize != 0) {
                // ch.ack.irnNack contains the seq triggered this NACK
                qp->irn.m_sack.sack(ch.ack.irnNack, ch.ack.irnNackSize);
            }

            uint32_t sack_seq, sack_len;
            if (qp->irn.m_sack.peekFrontBlock(&sack_seq, &sack_len)) {
                if (qp->snd_una == sack_seq) {
                    qp->snd_una += sack_len;
                }
            }

            qp->irn.m_sack.discardUpTo(qp->snd_una);

            if (qp->snd_nxt < qp->snd_una) {
                qp->snd_nxt = qp->snd_una;
            }
            // if (qp->irn.m_sack.IsEmpty())  { //
            if (qp->irn.m_recovery && qp->snd_una >= qp->irn.m_recovery_seq) {
                qp->irn.m_recovery = false;
            }
        } else {
            if (qp->snd_nxt < qp->snd_una) {
                qp->snd_nxt = qp->snd_una;
            }
        }
        if (qp->IsFinished()) {
            QpComplete(qp);
        }
    }

    /**
     * IB Spec Vol. 1 o9-85
     * The requester need not separately time each request launched into the
     * fabric, but instead simply begins the timer whenever it is expecting a response.
     * Once started, the timer is restarted each time an acknowledge
     * packet is received as long as there are outstanding expected responses.
     * The timer does not detect the loss of a particular expected acknowledge
     * packet, but rather simply detects the persistent absence of response
     * packets.
     * */
    if (!qp->IsFinished() && qp->GetOnTheFly() > 0) {
        if (qp->m_retransmit.IsRunning()) qp->m_retransmit.Cancel();
        qp->m_retransmit = Simulator::Schedule(qp->GetRto(m_mtu), &RdmaHw::HandleTimeout, this, qp,
                                               qp->GetRto(m_mtu));
    }

    if (m_irn) {
        if (ch.ack.irnNackSize != 0) {
            if (!qp->irn.m_recovery) {
                qp->irn.m_recovery_seq = qp->snd_nxt;
                RecoverQueue(qp);
                qp->irn.m_recovery = true;
            }
        } else {
            if (qp->irn.m_recovery) {
                qp->irn.m_recovery = false;
            }
        }

    } else if (ch.l3Prot == 0xFD)  // NACK
        RecoverQueue(qp);

    // handle cnp
    if (cnp) {
        if (m_cc_mode == 1) {  // mlx version
            m_dcqcnCnpReceived++;
            cnp_received_mlx(qp);
        }
    }

    if (m_cc_mode == 11 && qp->m_guard_tail_bypass) {
        m_guardTailBypassFeedbacks++;
    } else if (m_cc_mode == 3 || m_cc_mode == 11) {
        HandleAckHp(qp, p, ch);
    } else if (m_cc_mode == 7) {
        HandleAckTimely(qp, p, ch);
    } else if (m_cc_mode == 8) {
        HandleAckDctcp(qp, p, ch);
    }
    // ACK may advance the on-the-fly window, allowing more packets to send
    dev->TriggerTransmit();
    return 0;
}

size_t RdmaHw::getIrnBufferOverhead() {
    size_t overhead = 0;
    for (auto it = m_rxQpMap.begin(); it != m_rxQpMap.end(); it++) {
        overhead += it->second->m_irn_sack_.getSackBufferOverhead();
    }
    return overhead;
}

int RdmaHw::Receive(Ptr<Packet> p, CustomHeader &ch) {
    // #if (SLB_DEBUG == true)
    //     std::cout << "[RdmaHw::Receive] Node(" << m_node->GetId() << ")," << PARSE_FIVE_TUPLE(ch)
    //     << "l3Prot:" << ch.l3Prot << ",at" << Simulator::Now() << std::endl;
    // #endif
    if (ch.l3Prot == 0x11) {  // UDP
        return ReceiveUdp(p, ch);
    } else if (ch.l3Prot == 0xFF) {  // CNP
        return ReceiveCnp(p, ch);
    } else if (ch.l3Prot == 0xFD) {  // NACK
        return ReceiveAck(p, ch);
    } else if (ch.l3Prot == 0xFC) {  // ACK
        return ReceiveAck(p, ch);
    } else if (ch.l3Prot == CustomHeader::GUARD_RATE_GRANT) {
        if (m_cc_mode == 11 || m_cc_mode == 13) {
            return ReceiveRate(p, ch);
        }
        return 0;
    } else if (ch.l3Prot == CustomHeader::GUARD_RATE_GRANT_ACK) {
        if (m_cc_mode == 11 || m_cc_mode == 13) {
            return ReceiveGuardGrantAck(p, ch);
        }
        return 0;
    } else if (ch.l3Prot == 0xFB) {  // guard cap report or homa-simple credit
        if (m_cc_mode == 11 || m_cc_mode == 13) {
            if (m_cc_mode == 11 &&
                ((ch.ack.flags >> qbbHeader::FLAG_GUARD_CAP_REPORT) & 1) != 0) {
                return ReceiveGuardCapReport(p, ch);
            }
            return ReceiveRate(p, ch);
        } else if (m_cc_mode == 10) {
            return ReceiveHomaSimpleCredit(p, ch);
        }
        return 0;
    } else if (ch.l3Prot == 0xFA) {  // homa control (GRANT/RESEND/BUSY/ACK/...)
        if (m_cc_mode == 12) {
            return ReceiveHomaControl(p, ch);
        }
        return 0;
    }
    return 0;
}

/**
 * @brief Check sequence number when UDP DATA is received
 *
 * @return int
 * 0: should not reach here
 * 1: generate ACK
 * 2: still in loss recovery of IRN
 * 4: OoO, but skip to send NACK as it is already NACKed.
 * 6: NACK but functionality is ACK (indicating all packets are received)
 */
int RdmaHw::ReceiverCheckSeq(uint32_t seq, Ptr<RdmaRxQueuePair> q, uint32_t size, bool &cnp) {
    uint32_t expected = q->ReceiverNextExpectedSeq;
    if (seq == expected || (seq < expected && seq + size >= expected)) {
        if (m_irn) {
            if (q->m_milestone_rx < seq + size) q->m_milestone_rx = seq + size;
            q->ReceiverNextExpectedSeq += size - (expected - seq);
            {
                uint32_t sack_seq, sack_len;
                if (q->m_irn_sack_.peekFrontBlock(&sack_seq, &sack_len)) {
                    if (sack_seq <= q->ReceiverNextExpectedSeq)
                        q->ReceiverNextExpectedSeq +=
                            (sack_len - (q->ReceiverNextExpectedSeq - sack_seq));
                }
            }
            size_t progress = q->m_irn_sack_.discardUpTo(q->ReceiverNextExpectedSeq);
            if (q->m_irn_sack_.IsEmpty()) {
                return 6;  // This generates NACK, but actually functions as an ACK (indicates all
                           // packet has been received)
            } else {
                // should we put nack timer here
                return 2;  // Still in loss recovery mode of IRN
            }
            return 0;  // should not reach here
        }

        q->ReceiverNextExpectedSeq += size - (expected - seq);
        if (q->ReceiverNextExpectedSeq >= q->m_milestone_rx) {
            q->m_milestone_rx +=
                m_ack_interval;  // if ack_interval is small (e.g., 1), condition is meaningless
            return 1;            // Generate ACK
        } else if (q->ReceiverNextExpectedSeq % m_chunk == 0) {
            return 1;
        } else {
            return 5;
        }
    } else if (seq > expected) {
        // Generate NACK
        if (m_irn) {
            if (q->m_milestone_rx < seq + size) q->m_milestone_rx = seq + size;

            // if seq is already nacked, check for nacktimer
            if (q->m_irn_sack_.blockExists(seq, size) && Simulator::Now() < q->m_nackTimer) {
                return 4;  // don't need to send nack yet
            }
            q->m_nackTimer = Simulator::Now() + MicroSeconds(m_nack_interval);
            q->m_irn_sack_.sack(seq, size);  // set SACK
            NS_ASSERT(q->m_irn_sack_.discardUpTo(expected) ==
                      0);  // SACK blocks must be larger than expected
            cnp = true;    // XXX: out-of-order should accompany with CNP (?) TODO: Check on CX6
            return 2;      // generate SACK
        }
        if (Simulator::Now() >= q->m_nackTimer || q->m_lastNACK != expected) {  // new NACK
            q->m_nackTimer = Simulator::Now() + MicroSeconds(m_nack_interval);
            q->m_lastNACK = expected;
            if (m_backto0) {
                q->ReceiverNextExpectedSeq = q->ReceiverNextExpectedSeq / m_chunk * m_chunk;
            }
            cnp = true;  // XXX: out-of-order should accompany with CNP (?) TODO: Check on CX6
            return 2;
        } else {
            // skip to send NACK
            return 4;
        }
    } else {
        // Duplicate.
        if (m_irn) {
            // if (q->ReceiverNextExpectedSeq - 1 == q->m_milestone_rx) {
            // 	return 6; // This generates NACK, but actually functions as an ACK (indicates all
            // packet has been received)
            // }
            if (q->m_irn_sack_.IsEmpty()) {
                return 6;  // This generates NACK, but actually functions as an ACK (indicates all
                           // packet has been received)
            } else {
                // should we put nack timer here
                return 2;  // Still in loss recovery mode of IRN
            }
        }
        // Duplicate.
        return 1;  // According to IB Spec C9-110
                   /**
                    * IB Spec C9-110
                    * A responder shall respond to all duplicate requests in PSN order;
                    * i.e. the request with the (logically) earliest PSN shall be executed first. If,
                    * while responding to a new or duplicate request, a duplicate request is received
                    * with a logically earlier PSN, the responder shall cease responding
                    * to the original request and shall begin responding to the duplicate request
                    * with the logically earlier PSN.
                    */
    }
}

void RdmaHw::AddHeader(Ptr<Packet> p, uint16_t protocolNumber) {
    PppHeader ppp;
    ppp.SetProtocol(EtherToPpp(protocolNumber));
    p->AddHeader(ppp);
}

uint16_t RdmaHw::EtherToPpp(uint16_t proto) {
    switch (proto) {
        case 0x0800:
            return 0x0021;  // IPv4
        case 0x86DD:
            return 0x0057;  // IPv6
        default:
            NS_ASSERT_MSG(false, "PPP Protocol number not defined!");
    }
    return 0;
}

void RdmaHw::RecoverQueue(Ptr<RdmaQueuePair> qp) { qp->snd_nxt = qp->snd_una; }

void RdmaHw::QpComplete(Ptr<RdmaQueuePair> qp) {
    NS_ASSERT(!m_qpCompleteCallback.IsNull());
    if (m_cc_mode == 1) {
        Simulator::Cancel(qp->mlx.m_eventUpdateAlpha);
        Simulator::Cancel(qp->mlx.m_eventDecreaseRate);
        Simulator::Cancel(qp->mlx.m_rpTimer);
    }
    if (qp->m_retransmit.IsRunning()) qp->m_retransmit.Cancel();

    const char *binding = (qp->m_guard_tail_bypass ||
                           UsesGuardElephantReceiverAuthority(qp))
                              ? "grant"
                              : qp->hp.m_curRate < qp->hp.m_grantRate
                                    ? "reactive"
                                    : qp->hp.m_grantRate < qp->hp.m_curRate ? "grant" : "tie";
    TraceGuardControllerEvent(qp, "complete", qp->hp.m_curRate, binding, false, false,
                              0, qp->snd_nxt, -1.0, m_targetUtil, -1.0);

    // This callback will log info. It also calls deletetion the rxQp on the receiver
    m_qpCompleteCallback(qp);
    // delete TxQueuePair
    DeleteQueuePair(qp);
}

void RdmaHw::SetLinkDown(Ptr<QbbNetDevice> dev) {
    printf("RdmaHw: node:%u a link down\n", m_node->GetId());
}

void RdmaHw::AddTableEntry(Ipv4Address &dstAddr, uint32_t intf_idx) {
    uint32_t dip = dstAddr.Get();
    m_rtTable[dip].push_back(intf_idx);
}

void RdmaHw::ClearTable() { m_rtTable.clear(); }

void RdmaHw::RedistributeQp() {
    // clear old qpGrp
    for (uint32_t i = 0; i < m_nic.size(); i++) {
        if (m_nic[i].dev == NULL) continue;
        m_nic[i].qpGrp->Clear();
    }

    // redistribute qp
    for (auto &it : m_qpMap) {
        Ptr<RdmaQueuePair> qp = it.second;
        uint32_t nic_idx = GetNicIdxOfQp(qp);
        m_nic[nic_idx].qpGrp->AddQp(qp);
        // Notify Nic
        m_nic[nic_idx].dev->ReassignedQp(qp);
    }
}

Ptr<Packet> RdmaHw::GetNxtPacket(Ptr<RdmaQueuePair> qp) {
    // Once ACK progress proves that at most one BDP remains, another delayed
    // fabric-rate sample cannot prevent that bounded tail from entering the
    // network.  Keep the receiver cap, which still protects the destination,
    // but stop letting a stale shared-fabric estimate stretch completion.
    if (m_cc_mode == CC_MODE_GUARD && m_guardTailBypass &&
        !qp->m_guard_tail_bypass) {
        uint64_t bdp_bytes =
            qp->m_baseRtt * qp->m_max_rate.GetBitRate() / 8000000000lu;
        uint64_t acknowledged_remaining =
            qp->m_size > qp->snd_una ? qp->m_size - qp->snd_una : 0;
        uint64_t tail_bytes = (uint64_t)(m_guardTailBypassBdps * bdp_bytes);
        bool gate_ready = !m_guardTailCongestionGate ||
                          qp->m_guard_tail_safe_samples >= m_guardTailSafeSamples;
        if (bdp_bytes > 0 && qp->m_size > bdp_bytes &&
            acknowledged_remaining > 0 && acknowledged_remaining <= tail_bytes &&
            gate_ready) {
            qp->m_guard_tail_bypass = true;
            m_guardTailBypassFlows++;
            if (m_guardTailCongestionGate) m_guardTailGateQualifiedFlows++;
            SyncHwRate(qp, qp->hp.m_curRate);
        } else if (bdp_bytes > 0 && qp->m_size > bdp_bytes &&
                   acknowledged_remaining > 0 && acknowledged_remaining <= tail_bytes &&
                   !gate_ready && !qp->m_guard_tail_deferred) {
            qp->m_guard_tail_deferred = true;
            m_guardTailGateDeferrals++;
        }
    }
    uint32_t payload_size = qp->GetBytesLeft();
    if (m_mtu < payload_size) {  // possibly last packet
        payload_size = m_mtu;
    }
    uint32_t seq = (uint32_t)qp->snd_nxt;
    bool proceed_snd_nxt = true;
    // m_max_seq is the highest packet start offset previously transmitted.
    // txTotalPkts excludes the current packet here, avoiding a false positive
    // for the initial seq=0 packet.
    if (qp->irn.m_enabled && qp->stat.txTotalPkts > 0 && seq <= qp->irn.m_max_seq) {
        m_irnRetransmitPackets++;
        m_irnRetransmitBytes += payload_size;
    }
    qp->stat.txTotalPkts += 1;
    qp->stat.txTotalBytes += payload_size;

    Ptr<Packet> p = Create<Packet>(payload_size);
    // add SeqTsHeader
    SeqTsHeader seqTs;
    seqTs.SetSeq(seq);
    seqTs.SetPG(qp->m_pg);
    p->AddHeader(seqTs);
    // add udp header
    UdpHeader udpHeader;
    udpHeader.SetDestinationPort(qp->dport);
    udpHeader.SetSourcePort(qp->sport);
    p->AddHeader(udpHeader);
    // add ipv4 header
    Ipv4Header ipHeader;
    ipHeader.SetSource(qp->sip);
    ipHeader.SetDestination(qp->dip);
    ipHeader.SetProtocol(0x11);
    ipHeader.SetPayloadSize(p->GetSize());
    ipHeader.SetTtl(64);
    uint8_t ip_tos = 0;
    if (m_cc_mode == CC_MODE_GUARD && m_guardInitialWindowPriority) {
        uint8_t scheduling_pg = qp->m_pg;
        if (!qp->m_guard_initial_priority_closed &&
            seq < qp->GetGuardFirstGrantWin()) {
            scheduling_pg = std::min<uint8_t>(scheduling_pg, 3);
            if (scheduling_pg < qp->m_pg) {
                m_guardInitialWindowPriorityPackets++;
                m_guardInitialWindowPriorityBytes += payload_size;
            }
        } else if (!qp->m_guard_initial_priority_closed) {
            qp->m_guard_initial_priority_closed = true;
            if (qp->m_pg > 3) m_guardInitialWindowPriorityTransitions++;
        }
        ip_tos = CustomHeader::EncodeGuardScheduleClass(scheduling_pg);
    }
    ipHeader.SetTos(ip_tos);
    ipHeader.SetIdentification(qp->m_ipid);
    p->AddHeader(ipHeader);
    // add ppp header
    PppHeader ppp;
    ppp.SetProtocol(0x0021);  // EtherToPpp(0x800), see point-to-point-net-device.cc
    p->AddHeader(ppp);

    // attach Stat Tag
    uint8_t packet_pos = UINT8_MAX;
    {
        FlowIDNUMTag fint;
        if (!p->PeekPacketTag(fint)) {
            fint.SetId(qp->m_flow_id);
            fint.SetFlowSize(qp->m_size);
            p->AddPacketTag(fint);
        }
        FlowStatTag fst;
        uint64_t size = qp->m_size;
        if (!p->PeekPacketTag(fst)) {
            if (size <= m_mtu && qp->snd_nxt + payload_size >= qp->m_size) {
                fst.SetType(FlowStatTag::FLOW_START_AND_END);
            } else if (qp->snd_nxt + payload_size >= qp->m_size) {
                fst.SetType(FlowStatTag::FLOW_END);
            } else if (qp->snd_nxt == 0) {
                fst.SetType(FlowStatTag::FLOW_START);
            } else {
                fst.SetType(FlowStatTag::FLOW_NOTEND);
            }
            packet_pos = fst.GetType();
            fst.setInitiatedTime(Simulator::Now().GetSeconds());
            if (qp->m_baseRtt > 0) {
                fst.SetBaseRttSeconds(double(qp->m_baseRtt) / 1e9);
            }
            if (m_guardTransitionPrefixBarrierEnabled) {
                fst.SetFirstGrantGateBytes(qp->GetGuardFirstGrantWin());
            }
            p->AddPacketTag(fst);
        }
    }

    if (qp->irn.m_enabled) {
        if (qp->irn.m_max_seq < seq) qp->irn.m_max_seq = seq;
    }

    // // update state
    if (proceed_snd_nxt) qp->snd_nxt += payload_size;

    qp->m_ipid++;

    // return
    return p;
}

void RdmaHw::PktSent(Ptr<RdmaQueuePair> qp, Ptr<Packet> pkt, Time interframeGap) {
    qp->lastPktSize = pkt->GetSize();
    UpdateNextAvail(qp, interframeGap, pkt->GetSize());

    if (pkt) {
        CustomHeader ch(CustomHeader::L2_Header | CustomHeader::L3_Header |
                        CustomHeader::L4_Header);
        pkt->PeekHeader(ch);
#if (SLB_DEBUG == true)
        std::cout << "[RdmaHw::PktSent] Node(" << m_node->GetId() << ")," << PARSE_FIVE_TUPLE(ch)
                  << "l3Prot:" << ch.l3Prot << ",at" << Simulator::Now() << std::endl;
#endif
        RdmaHw::nAllPkts += 1;
        if (ch.l3Prot == 0x11 && m_cc_mode != 12) {  // UDP except Homa
            // Update Timer
            if (qp->m_retransmit.IsRunning()) qp->m_retransmit.Cancel();
            qp->m_retransmit = Simulator::Schedule(qp->GetRto(m_mtu), &RdmaHw::HandleTimeout, this,
                                                   qp, qp->GetRto(m_mtu));
        } else if (ch.l3Prot == CustomHeader::GUARD_RATE_GRANT ||
                   ch.l3Prot == CustomHeader::GUARD_RATE_GRANT_ACK ||
                   ch.l3Prot == 0xFB || ch.l3Prot == 0xFC || ch.l3Prot == 0xFD ||
                   ch.l3Prot == 0xFF || ch.l3Prot == 0xFA) {  // control packets
        } else if (ch.l3Prot == 0xFE) {                                            // PFC
        }
    }
}

void RdmaHw::HandleTimeout(Ptr<RdmaQueuePair> qp, Time rto) {
    // Assume Outstanding Packets are lost
    // std::cerr << "Timeout on qp=" << qp << std::endl;
    if (qp->IsFinished()) {
        return;
    }

    uint32_t nic_idx = GetNicIdxOfQp(qp);
    Ptr<QbbNetDevice> dev = m_nic[nic_idx].dev;

    // IRN: disable timeouts when PFC is enabled to prevent spurious retransmissions
    if (qp->irn.m_enabled && dev->IsQbbEnabled()) return;

    if (acc_timeout_count.find(qp->m_flow_id) == acc_timeout_count.end())
        acc_timeout_count[qp->m_flow_id] = 0;
    acc_timeout_count[qp->m_flow_id]++;
    m_timeoutRecoveries++;

    if (qp->irn.m_enabled) qp->irn.m_recovery = true;

    RecoverQueue(qp);
    dev->TriggerTransmit();
}

void RdmaHw::UpdateNextAvail(Ptr<RdmaQueuePair> qp, Time interframeGap, uint32_t pkt_size) {
    Time sendingTime;
    if (m_rateBound)
        sendingTime = interframeGap + Seconds(qp->m_rate.CalculateTxTime(pkt_size));
    else
        sendingTime = interframeGap + Seconds(qp->m_max_rate.CalculateTxTime(pkt_size));
    qp->m_nextAvail = Simulator::Now() + sendingTime;
}

void RdmaHw::ChangeRate(Ptr<RdmaQueuePair> qp, DataRate new_rate) {
#if 1
    Time sendingTime = Seconds(qp->m_rate.CalculateTxTime(qp->lastPktSize));
    Time new_sendintTime = Seconds(new_rate.CalculateTxTime(qp->lastPktSize));
    qp->m_nextAvail = qp->m_nextAvail + new_sendintTime - sendingTime;
    // update nic's next avail event
    uint32_t nic_idx = GetNicIdxOfQp(qp);
    m_nic[nic_idx].dev->UpdateNextAvail(qp->m_nextAvail);
#endif

    // change to new rate
    qp->m_rate = new_rate;
}

#define PRINT_LOG 0
/******************************
 * Mellanox's version of DCQCN
 *****************************/
void RdmaHw::UpdateAlphaMlx(Ptr<RdmaQueuePair> q) {
#if PRINT_LOG
// std::cout << Simulator::Now() << " alpha update:" << m_node->GetId() << ' ' << q->mlx.m_alpha <<
// ' ' << (int)q->mlx.m_alpha_cnp_arrived << '\n'; printf("%lu alpha update: %08x %08x %u %u
// %.6lf->", Simulator::Now().GetTimeStep(), q->sip.Get(), q->dip.Get(), q->sport, q->dport,
// q->mlx.m_alpha);
#endif
    m_dcqcnAlphaUpdates++;
    if (q->mlx.m_alpha_cnp_arrived) {                       // cnp -> increase
        m_dcqcnAlphaCnpUpdates++;
        q->mlx.m_alpha = (1 - m_g) * q->mlx.m_alpha + m_g;  // binary feedback
    } else {                                                // no cnp -> decrease
        q->mlx.m_alpha = (1 - m_g) * q->mlx.m_alpha;        // binary feedback
    }
#if PRINT_LOG
// printf("%.6lf\n", q->mlx.m_alpha);
#endif
    q->mlx.m_alpha_cnp_arrived = false;  // clear the CNP_arrived bit
    ScheduleUpdateAlphaMlx(q);
}
void RdmaHw::ScheduleUpdateAlphaMlx(Ptr<RdmaQueuePair> q) {
    q->mlx.m_eventUpdateAlpha = Simulator::Schedule(MicroSeconds(m_alpha_resume_interval),
                                                    &RdmaHw::UpdateAlphaMlx, this, q);
}

void RdmaHw::cnp_received_mlx(Ptr<RdmaQueuePair> q) {
    q->mlx.m_alpha_cnp_arrived = true;     // set CNP_arrived bit for alpha update
    q->mlx.m_decrease_cnp_arrived = true;  // set CNP_arrived bit for rate decrease
    if (q->mlx.m_first_cnp) {
        // init alpha
        q->mlx.m_alpha = 1;
        q->mlx.m_alpha_cnp_arrived = false;
        // schedule alpha update
        ScheduleUpdateAlphaMlx(q);
        // schedule rate decrease
        ScheduleDecreaseRateMlx(q, 1);  // add 1 ns to make sure rate decrease is after alpha update
        // set rate on first CNP
        q->mlx.m_targetRate = q->m_rate = m_rateOnFirstCNP * q->m_rate;
        q->mlx.m_first_cnp = false;
    }
}

void RdmaHw::CheckRateDecreaseMlx(Ptr<RdmaQueuePair> q) {
    ScheduleDecreaseRateMlx(q, 0);
    if (q->mlx.m_decrease_cnp_arrived) {
        m_dcqcnRateDecreaseEvents++;
#if PRINT_LOG
        printf("%lu rate dec: %08x %08x %u %u (%0.3lf %.3lf)->", Simulator::Now().GetTimeStep(),
               q->sip.Get(), q->dip.Get(), q->sport, q->dport,
               q->mlx.m_targetRate.GetBitRate() * 1e-9, q->m_rate.GetBitRate() * 1e-9);
#endif
        bool clamp = true;
        if (!m_EcnClampTgtRate) {
            if (q->mlx.m_rpTimeStage == 0) clamp = false;
        }
        if (clamp) {
            q->mlx.m_targetRate = q->m_rate;
        }
        DataRate old_rate = q->m_rate;
        q->m_rate = std::max(m_minRate, q->m_rate * (1 - q->mlx.m_alpha / 2));
        if (q->m_rate != old_rate) m_dcqcnActualRateDecreases++;
        // reset rate increase related things
        q->mlx.m_rpTimeStage = 0;
        q->mlx.m_decrease_cnp_arrived = false;
        Simulator::Cancel(q->mlx.m_rpTimer);
        q->mlx.m_rpTimer = Simulator::Schedule(MicroSeconds(m_rpgTimeReset),
                                               &RdmaHw::RateIncEventTimerMlx, this, q);
#if PRINT_LOG
        printf("(%.3lf %.3lf)\n", q->mlx.m_targetRate.GetBitRate() * 1e-9,
               q->m_rate.GetBitRate() * 1e-9);
#endif
    }
}
void RdmaHw::ScheduleDecreaseRateMlx(Ptr<RdmaQueuePair> q, uint32_t delta) {
    q->mlx.m_eventDecreaseRate =
        Simulator::Schedule(MicroSeconds(m_rateDecreaseInterval) + NanoSeconds(delta),
                            &RdmaHw::CheckRateDecreaseMlx, this, q);
}

void RdmaHw::RateIncEventTimerMlx(Ptr<RdmaQueuePair> q) {
    q->mlx.m_rpTimer =
        Simulator::Schedule(MicroSeconds(m_rpgTimeReset), &RdmaHw::RateIncEventTimerMlx, this, q);
    RateIncEventMlx(q);
    q->mlx.m_rpTimeStage++;
}
void RdmaHw::RateIncEventMlx(Ptr<RdmaQueuePair> q) {
    m_dcqcnRateIncreaseEvents++;
    DataRate old_rate = q->m_rate;
    // check which increase phase: fast recovery, active increase, hyper increase
    if (q->mlx.m_rpTimeStage < m_rpgThreshold) {  // fast recovery
        FastRecoveryMlx(q);
    } else if (q->mlx.m_rpTimeStage == m_rpgThreshold) {  // active increase
        ActiveIncreaseMlx(q);
    } else {  // hyper increase
        HyperIncreaseMlx(q);
    }
    if (q->m_rate != old_rate) m_dcqcnActualRateIncreases++;
}

void RdmaHw::FastRecoveryMlx(Ptr<RdmaQueuePair> q) {
#if PRINT_LOG
    printf("%lu fast recovery: %08x %08x %u %u (%0.3lf %.3lf)->", Simulator::Now().GetTimeStep(),
           q->sip.Get(), q->dip.Get(), q->sport, q->dport, q->mlx.m_targetRate.GetBitRate() * 1e-9,
           q->m_rate.GetBitRate() * 1e-9);
#endif
    q->m_rate = (q->m_rate / 2) + (q->mlx.m_targetRate / 2);
#if PRINT_LOG
    printf("(%.3lf %.3lf)\n", q->mlx.m_targetRate.GetBitRate() * 1e-9,
           q->m_rate.GetBitRate() * 1e-9);
#endif
}
void RdmaHw::ActiveIncreaseMlx(Ptr<RdmaQueuePair> q) {
#if PRINT_LOG
    printf("%lu active inc: %08x %08x %u %u (%0.3lf %.3lf)->", Simulator::Now().GetTimeStep(),
           q->sip.Get(), q->dip.Get(), q->sport, q->dport, q->mlx.m_targetRate.GetBitRate() * 1e-9,
           q->m_rate.GetBitRate() * 1e-9);
#endif
    // get NIC
    uint32_t nic_idx = GetNicIdxOfQp(q);
    Ptr<QbbNetDevice> dev = m_nic[nic_idx].dev;
    // increate rate
    q->mlx.m_targetRate += m_rai;
    if (q->mlx.m_targetRate > dev->GetDataRate()) q->mlx.m_targetRate = dev->GetDataRate();
    q->m_rate = (q->m_rate / 2) + (q->mlx.m_targetRate / 2);
#if PRINT_LOG
    printf("(%.3lf %.3lf)\n", q->mlx.m_targetRate.GetBitRate() * 1e-9,
           q->m_rate.GetBitRate() * 1e-9);
#endif
}
void RdmaHw::HyperIncreaseMlx(Ptr<RdmaQueuePair> q) {
#if PRINT_LOG
    printf("%lu hyper inc: %08x %08x %u %u (%0.3lf %.3lf)->", Simulator::Now().GetTimeStep(),
           q->sip.Get(), q->dip.Get(), q->sport, q->dport, q->mlx.m_targetRate.GetBitRate() * 1e-9,
           q->m_rate.GetBitRate() * 1e-9);
#endif
    // get NIC
    uint32_t nic_idx = GetNicIdxOfQp(q);
    Ptr<QbbNetDevice> dev = m_nic[nic_idx].dev;
    // increate rate
    q->mlx.m_targetRate += m_rhai;
    if (q->mlx.m_targetRate > dev->GetDataRate()) q->mlx.m_targetRate = dev->GetDataRate();
    q->m_rate = (q->m_rate / 2) + (q->mlx.m_targetRate / 2);
#if PRINT_LOG
    printf("(%.3lf %.3lf)\n", q->mlx.m_targetRate.GetBitRate() * 1e-9,
           q->m_rate.GetBitRate() * 1e-9);
#endif
}

/***********************
 * Rate CC
 ***********************/
void RdmaHw::SyncHwRate(Ptr<RdmaQueuePair> qp, DataRate target_cc_rate) {
    DataRate final_rate = target_cc_rate;
    bool elephant_receiver_authority =
        UsesGuardElephantReceiverAuthority(qp);

    if (m_cc_mode == CC_MODE_GUARD && qp->m_guard_tail_bypass) {
        final_rate = qp->hp.m_grantRate;
    } else if (m_cc_mode == CC_MODE_GUARD && elephant_receiver_authority) {
        final_rate = qp->hp.m_grantRate;
        m_guardElephantReceiverAuthorityBindings++;
        if (qp->hp.m_grantRate > target_cc_rate) {
            m_guardElephantReceiverAuthorityMaxReleasedBps = std::max(
                m_guardElephantReceiverAuthorityMaxReleasedBps,
                qp->hp.m_grantRate.GetBitRate() - target_cc_rate.GetBitRate());
        }
    } else if (qp->hp.m_grantRate < final_rate) {
        final_rate = qp->hp.m_grantRate;
    }

    if (final_rate < m_minRate) final_rate = m_minRate;
    if (final_rate > qp->m_max_rate) final_rate = qp->m_max_rate;

    DataRate old_rate = qp->m_rate;
    ChangeRate(qp, final_rate);
    if (elephant_receiver_authority && qp->m_rate != old_rate) {
        m_guardElephantReceiverAuthorityRateChanges++;
    }
    MaybeSendGuardCapReport(qp);
}

void RdmaHw::MaybeSendGuardCapReport(Ptr<RdmaQueuePair> qp) {
    if ((!m_guardCapAwareReclaim && !m_guardElephantCapSpillover &&
         !m_guardCapTriggeredRefresh) ||
        m_cc_mode != CC_MODE_GUARD || qp == NULL ||
        qp->IsFinishedConst()) {
        return;
    }
    uint64_t bdp_bytes =
        qp->m_baseRtt * qp->m_max_rate.GetBitRate() / 8000000000lu;
    if (m_guardSelectiveRegistration && bdp_bytes > 0 && qp->m_size <= bdp_bytes) {
        return;
    }
    Time now = Simulator::Now();
    Time interval = NanoSeconds(std::max<uint64_t>(1, qp->m_baseRtt));
    if (qp->m_guard_has_cap_report &&
        now - qp->m_guard_last_cap_report_time < interval) {
        return;
    }

    bool fabric_bound = !qp->m_guard_tail_bypass &&
                        qp->hp.m_curRate < qp->hp.m_grantRate;
    uint32_t rate_mbps = std::max<uint32_t>(
        1, static_cast<uint32_t>(qp->m_rate.GetBitRate() / 1000000));
    qbbHeader report;
    report.SetSeq(rate_mbps);
    report.SetPG(qp->m_pg);
    report.SetSport(qp->sport);
    report.SetDport(qp->dport);
    report.SetGuardCapReport();
    report.SetGuardFabricBound(fabric_bound);

    Ptr<Packet> packet = Create<Packet>(
        std::max(60 - 14 - 20 - static_cast<int>(report.GetSerializedSize()), 0));
    packet->AddHeader(report);
    Ipv4Header ip;
    ip.SetDestination(qp->dip);
    ip.SetSource(qp->sip);
    ip.SetProtocol(0xFB);
    ip.SetTtl(64);
    ip.SetPayloadSize(packet->GetSize());
    ip.SetIdentification(qp->m_ipid++);
    packet->AddHeader(ip);
    AddHeader(packet, 0x800);

    qp->m_guard_last_cap_report_time = now;
    qp->m_guard_last_cap_report_rate_bps = qp->m_rate.GetBitRate();
    qp->m_guard_has_cap_report = true;
    m_guardCapReportsSent++;
    m_guardCapReportBytesSent += packet->GetSize();
    uint32_t nic_idx = GetNicIdxOfQp(qp);
    m_nic[nic_idx].dev->RdmaEnqueueHighPrioQ(packet);
    m_nic[nic_idx].dev->TriggerTransmit();
}

void RdmaHw::HandleRccRequest(Ptr<RdmaRxQueuePair> rx_qp, Ptr<Packet> p, CustomHeader &ch) {
    if (m_rate_flow_ctl_set.find(PeekPointer(rx_qp)) != m_rate_flow_ctl_set.end()) {
        std::cout << "Warning: duplicated RCC request for flow!" << std::endl;
        // exit(1);
        return;
    }
    uint64_t active_before = m_rate_flow_ctl_set.size();
    m_rate_flow_ctl_set.emplace(PeekPointer(rx_qp));
    rx_qp->m_guard_last_schedule_seq = rx_qp->ReceiverNextExpectedSeq;
    rx_qp->m_guard_pending_progress_seq = rx_qp->ReceiverNextExpectedSeq;
    rx_qp->m_guard_grant_generation = 0;
    rx_qp->m_guard_grant_generation_acked = false;
    rx_qp->m_guard_grant_upper_bound_bps = 0;
    rx_qp->m_guard_last_acked_generation = 0;
    rx_qp->m_guard_last_acked_upper_bound_bps = 0;
    rx_qp->m_guard_last_issued_generation = 0;
    rx_qp->m_guard_last_issued_target_bps = 0;
    rx_qp->m_guard_register_ns = Simulator::Now().GetNanoSeconds();
    rx_qp->m_guard_registration_membership_revision = 0;
    if (GuardSmallSetFastpathEnabled()) {
        NS_ABORT_MSG_IF(
            m_guardFastpathMembershipRevision ==
                std::numeric_limits<uint64_t>::max(),
            "GUARD V14 registration membership provenance exhausted");
        // RequestGuardFastpathMembershipUpdate consumes this exact next
        // revision synchronously.  Latching it before the request lets a
        // nested vector freeze distinguish this flow from earlier waiters.
        rx_qp->m_guard_registration_membership_revision =
            m_guardFastpathMembershipRevision + 1;
    }
    m_guardRegistrations++;
    if (m_guardSelectiveRegistration) m_guardSelectedRegistrations++;
    m_guardMaxActiveFlows = std::max<uint64_t>(m_guardMaxActiveFlows,
                                               m_rate_flow_ctl_set.size());
    if (GuardSmallSetFastpathEnabled()) {
        m_guardFastpathWaiters.emplace(PeekPointer(rx_qp));
        m_guardFastpathJoinQueueMax = std::max<uint64_t>(
            m_guardFastpathJoinQueueMax, m_guardFastpathWaiters.size());
    }

    FlowIDNUMTag fit;
    uint64_t flow_size = p->PeekPacketTag(fit) ? fit.GetFlowSize() : 0;
    TraceGuardRegistration(rx_qp, flow_size, active_before, m_rate_flow_ctl_set.size());

    RequestGuardMembershipUpdate("registration");
    if (GuardSmallSetFastpathEnabled()) {
        NS_ABORT_MSG_IF(
            rx_qp->m_guard_registration_membership_revision == 0 ||
                rx_qp->m_guard_registration_membership_revision >
                    m_guardFastpathMembershipRevision,
            "GUARD V14 registration lost its membership provenance");
        if (m_guardMixedPgVectorFastpath && m_guardFrozenVectorActive &&
            m_guardFrozenTargetByFlow.find(PeekPointer(rx_qp)) ==
                m_guardFrozenTargetByFlow.end()) {
            uint32_t receiver_nic = GetNicIdxOfRxQp(rx_qp);
            uint64_t receiver_capacity_bps =
                m_nic[receiver_nic].dev->GetDataRate().GetBitRate();
            GuardFrozenActiveProvenanceInput provenance = {
                false,
                m_guardFastpathWaiters.find(PeekPointer(rx_qp)) !=
                    m_guardFastpathWaiters.end(),
                m_guardFastpathIncumbents.find(PeekPointer(rx_qp)) !=
                    m_guardFastpathIncumbents.end(),
                m_guardFastpathTransactionWaiters.find(PeekPointer(rx_qp)) !=
                    m_guardFastpathTransactionWaiters.end(),
                rx_qp->m_guard_registration_membership_revision,
                m_guardFastpathMembershipRevision,
                m_guardFrozenMembershipRevision,
                m_guardPendingMembershipChanges,
                receiver_nic,
                m_guardFrozenReceiverNic,
                receiver_capacity_bps,
                m_guardFrozenReceiverCapacityBps,
                rx_qp->m_guard_last_acked_generation,
                rx_qp->m_guard_last_acked_upper_bound_bps,
                rx_qp->m_guard_last_issued_generation,
                rx_qp->m_guard_last_issued_target_bps,
                rx_qp->m_guard_grant_generation,
                rx_qp->m_guard_grant_rate_bps,
                rx_qp->m_guard_grant_upper_bound_bps,
                rx_qp->m_guard_grant_generation_acked,
                rx_qp->m_guard_register_ns,
                rx_qp->m_guard_flow_size,
                rx_qp->m_guard_has_first_grant_gate_bytes,
                rx_qp->m_guard_first_grant_gate_bytes,
                rx_qp->m_base_rtt_sec};
            NS_ABORT_MSG_IF(
                ClassifyGuardFrozenActiveProvenance(provenance) !=
                    GUARD_FROZEN_ACTIVE_DEFERRED_WAITER,
                "GUARD V14 live registration violates frozen cohort provenance");
        }
    }
    ScheduleGuardRebalance();
}

bool RdmaHw::RequestGuardProactiveDrain(Ptr<RdmaRxQueuePair> rx_qp,
                                        uint64_t remaining_bytes) {
    NS_ABORT_MSG_IF(!m_guardSerializedDraining ||
                        m_rate_flow_ctl_set.find(PeekPointer(rx_qp)) ==
                            m_rate_flow_ctl_set.end(),
                    "GUARD V14 proactive drain requires an ACTIVE flow");
    if (m_guardDrainingRecords.find(PeekPointer(rx_qp)) !=
        m_guardDrainingRecords.end()) {
        return false;
    }
    uint64_t effective_min_mbps = m_minRate.GetBitRate() / 1000000ULL +
        (m_minRate.GetBitRate() % 1000000ULL != 0 ? 1 : 0);
    NS_ABORT_MSG_IF(
        effective_min_mbps >
            std::numeric_limits<uint64_t>::max() / 1000000ULL,
        "GUARD V14 effective minimum rate overflow");
    uint64_t effective_min_bps = effective_min_mbps * 1000000ULL;
    uint64_t reserved_bps = ComputeGuardDrainingReservationBps(
        rx_qp->m_guard_last_acked_upper_bound_bps,
        rx_qp->m_guard_last_issued_target_bps, effective_min_bps);
    bool transaction_busy = m_guardFastpathPhase != GUARD_FASTPATH_IDLE ||
                            m_guardPendingGrantAcks != 0 ||
                            m_guardFrozenVectorActive ||
                            m_guardProgressTransactionActive;
    GuardQpIdentity identity = {rx_qp->sip, rx_qp->dip, rx_qp->sport,
                                rx_qp->dport, rx_qp->m_guard_pg};
    uint32_t receiver_nic = GetNicIdxOfRxQp(rx_qp);
    AssertGuardDrainReplacementFits(PeekPointer(rx_qp), receiver_nic,
                                    reserved_bps);
    m_guardDrainingRecords.emplace(
        PeekPointer(rx_qp),
        GuardDrainingRecord{
            identity, rx_qp, receiver_nic, rx_qp->m_guard_pg,
            rx_qp->m_guard_last_acked_upper_bound_bps,
            rx_qp->m_guard_last_issued_target_bps, reserved_bps,
            rx_qp->m_guard_last_issued_generation, remaining_bytes,
            static_cast<uint64_t>(Simulator::Now().GetNanoSeconds()),
            transaction_busy ? GUARD_DRAIN_PENDING : GUARD_DRAINING});
    m_guardDrainingRequests++;
    if (transaction_busy) {
        m_guardDrainingPendingTransitions++;
    } else {
        m_guardDrainingDirectTransitions++;
    }
    m_guardDrainingMaxRecords = std::max<uint64_t>(
        m_guardDrainingMaxRecords, m_guardDrainingRecords.size());
    __uint128_t draining_sum = 0;
    for (const auto &item : m_guardDrainingRecords) {
        NS_ABORT_MSG_IF(item.second.receiverNic != receiver_nic,
                        "GUARD V14 draining records span receiver NICs");
        draining_sum += item.second.reservedBps;
    }
    NS_ABORT_MSG_IF(
        draining_sum > std::numeric_limits<uint64_t>::max(),
        "GUARD V14 pending draining reservation sum overflow");
    m_guardDrainingMaxReservedBps = std::max(
        m_guardDrainingMaxReservedBps,
        static_cast<uint64_t>(draining_sum));
    if (transaction_busy) return true;
    return HandleRccRemove(rx_qp, GUARD_RELEASE_PROACTIVE, remaining_bytes);
}

bool RdmaHw::ReleaseGuardDrainingOnCompletion(
    Ptr<RdmaRxQueuePair> rx_qp) {
    auto found = m_guardDrainingRecords.find(PeekPointer(rx_qp));
    if (found == m_guardDrainingRecords.end()) return false;
    NS_ABORT_MSG_IF(
        !CanReleaseGuardDraining(rx_qp->m_guard_flow_size,
                                 rx_qp->ReceiverNextExpectedSeq),
        "GUARD V14 draining reservation released before contiguous completion");
    if (found->second.state == GUARD_DRAIN_PENDING) {
        // The live transaction still owns this ACTIVE flow.  Preserve the
        // record through its boundary so the ACTIVE potential is atomically
        // replaced by D before the already-proven completion releases D.
        return false;
    }
    NS_ABORT_MSG_IF(found->second.state != GUARD_DRAINING,
                    "GUARD V14 completion found an invalid drain state");
    m_guardDrainingRecords.erase(found);
    m_guardDrainingCompletionReleases++;
    if (m_guardCapacityAdmissionBlocked) {
        NS_ABORT_MSG_IF(!m_guardCapacityAdmissionDeferral ||
                            m_guardFastpathPhase != GUARD_FASTPATH_IDLE ||
                            m_guardPendingGrantAcks != 0 ||
                            m_guardFrozenVectorActive,
                        "GUARD V18 capacity resume crossed serialized state");
        StartGuardFastpathTransaction();
        // Either the newly freed reservation admitted the immutable cohort,
        // or more draining capacity must still complete.  In both cases the
        // resumed membership vector already owns the capacity change.
        return true;
    }
    if (m_rate_flow_ctl_set.empty()) {
        // More than one D can complete at the same simulation time after the
        // last ACTIVE flow has left.  The first completion must preserve the
        // remaining reservation and its epoch; the shared settlement path
        // resets only after the final D has been released.
        SettleGuardActiveEmptyState();
        return true;
    }
    NS_ABORT_MSG_IF(
        m_guardProgressRevision == std::numeric_limits<uint64_t>::max(),
        "GUARD V14 draining-release progress revision exhausted");
    m_guardProgressRevision++;
    m_guardCapacityDirty = true;
    bool busy = m_guardProgressTransactionActive ||
                m_guardFastpathPhase != GUARD_FASTPATH_IDLE ||
                m_guardPendingGrantAcks != 0 || m_guardFrozenVectorActive ||
                IsGuardMembershipDirty() || m_guardFastpathCollectionReady;
    if (busy) {
        m_guardProgressEventsCoalesced++;
    } else {
        StartGuardProgressTransaction();
    }
    return true;
}

bool RdmaHw::HandleRccRemove(Ptr<RdmaRxQueuePair> rx_qp,
                             GuardReleaseReason reason,
                             uint64_t remaining_bytes) {
    if (m_rate_flow_ctl_set.find(PeekPointer(rx_qp)) == m_rate_flow_ctl_set.end()) {
        return false;
    }
    uint64_t active_before = m_rate_flow_ctl_set.size();
    bool closes_required_ack =
        rx_qp->m_guard_grant_generation == m_guardGrantGeneration &&
        !rx_qp->m_guard_grant_generation_acked && m_guardPendingGrantAcks > 0;
    if (closes_required_ack && m_guardMixedPgVectorFastpath) {
        GuardQpIdentity identity = {
            rx_qp->sip, rx_qp->dip, rx_qp->sport, rx_qp->dport,
            rx_qp->m_guard_pg};
        auto ledger = m_guardGenerationLedger.find(PeekPointer(rx_qp));
        auto frozen = GetGuardFrozenTarget(PeekPointer(rx_qp));
        uint8_t expected_phase = GetGuardFastpathWirePhaseTag("none");
        NS_ABORT_MSG_IF(
            !m_guardFrozenVectorActive ||
                ledger == m_guardGenerationLedger.end() ||
                ledger->second.tombstone ||
                ledger->second.identity != identity ||
                ledger->second.generation !=
                    rx_qp->m_guard_grant_generation ||
                ledger->second.phaseTag != expected_phase ||
                !IsGuardLedgerActionCompatible(
                    ledger->second.action, ledger->second.phaseTag) ||
                frozen == NULL || frozen->tombstone ||
                frozen->identity != identity ||
                frozen->targetMbps != ledger->second.targetMbps,
            "GUARD V14 removal cannot authenticate its required ACK");
        // Materialize immutable wire provenance before either the pending
        // barrier or the live frozen ledger is changed.
        RetireGuardRequiredAck(rx_qp, ledger->second);
        m_guardPendingGrantAcks--;
        TraceGuardGrant(
            rx_qp, "ack_retire_close", "none", active_before,
            m_nic[GetNicIdxOfRxQp(rx_qp)].dev->GetDataRate().GetBitRate(),
            static_cast<uint64_t>(ledger->second.targetMbps) * 1000000ULL,
            rx_qp->ReceiverNextExpectedSeq, 0, ledger->second.generation,
            m_guardPendingGrantAcks, true, ledger->second.phaseTag);
    } else if (closes_required_ack) {
        m_guardPendingGrantAcks--;
    }
    rx_qp->m_guard_elephant_deferred_since_ns = -1;
    m_rate_flow_ctl_set.erase(PeekPointer(rx_qp));
    m_guardProgressDirtyFlows.erase(PeekPointer(rx_qp));
    m_guardProgressTransactionFlows.erase(PeekPointer(rx_qp));
    if (GuardSmallSetFastpathEnabled()) {
        m_guardFastpathIncumbents.erase(PeekPointer(rx_qp));
        m_guardFastpathWaiters.erase(PeekPointer(rx_qp));
        m_guardFastpathTransactionWaiters.erase(PeekPointer(rx_qp));
        if (m_guardMixedPgVectorFastpath && m_guardFrozenVectorActive) {
            auto frozen = m_guardFrozenTargetByFlow.find(PeekPointer(rx_qp));
            if (frozen != m_guardFrozenTargetByFlow.end()) {
                NS_ABORT_MSG_IF(frozen->second >= m_guardFrozenTargetVector.size(),
                                "GUARD V14 removal found an invalid frozen index");
                m_guardFrozenTargetVector[frozen->second].tombstone = true;
            }
            auto ledger = m_guardGenerationLedger.find(PeekPointer(rx_qp));
            if (ledger != m_guardGenerationLedger.end()) {
                ledger->second.tombstone = true;
            }
        }
    }
    if (m_guardTransitionPrefixBarrierEnabled) {
        RetireGuardTransitionAuditPrefix(PeekPointer(rx_qp));
        m_guardFastpathReadyWaiters.erase(PeekPointer(rx_qp));
        auto order_it = std::find(m_guardTransitionActivationOrder.begin(),
                                  m_guardTransitionActivationOrder.end(),
                                  PeekPointer(rx_qp));
        if (order_it != m_guardTransitionActivationOrder.end()) {
            uint64_t index = static_cast<uint64_t>(
                order_it - m_guardTransitionActivationOrder.begin());
            if (index < m_guardTransitionActivationCursor) {
                m_guardTransitionActivationCursor--;
            }
            m_guardTransitionActivationOrder.erase(order_it);
            m_guardTransitionActivationHolds.erase(
                m_guardTransitionActivationHolds.begin() + index);
        }
        m_guardTransitionPrefixTargets.erase(PeekPointer(rx_qp));
        m_guardTransitionCurrentBatch.erase(PeekPointer(rx_qp));
    }
    if (m_guardTransitionPrefixBarrierEnabled &&
        m_guardTransitionPrefixBarrierWaiting) {
        bool previous_defer = m_guardTerminalResetDeferred;
        m_guardTerminalResetDeferred = true;
        CheckGuardTransitionPrefixBarrier();
        m_guardTerminalResetDeferred = previous_defer;
    }
    TraceGuardRelease(rx_qp, reason, remaining_bytes, active_before,
                      m_rate_flow_ctl_set.size());
    if (reason == GUARD_RELEASE_COMPLETION) {
        auto drain = m_guardDrainingRecords.find(PeekPointer(rx_qp));
        if (drain != m_guardDrainingRecords.end() &&
            drain->second.state != GUARD_DRAIN_PENDING) {
            m_guardDrainingRecords.erase(drain);
        }
    }
    if (m_guardApplyingPendingDrains) return true;

    if (m_rate_flow_ctl_set.empty() && m_guardSerializedDraining &&
        m_guardFastpathPhase != GUARD_FASTPATH_IDLE) {
        NS_ABORT_MSG_IF(
            m_guardPendingGrantAcks != 0,
            "GUARD V14 ACTIVE-empty transaction cannot close pending ACKs");
        NS_ABORT_MSG_IF(
            m_guardFastpathPhase != GUARD_FASTPATH_PREPARE &&
                m_guardFastpathPhase != GUARD_FASTPATH_ACTIVATE,
            "GUARD V14 ACTIVE-empty transaction has no closable generation");
        bool previous_defer = m_guardTerminalResetDeferred;
        m_guardTerminalResetDeferred = true;
        FinishGuardFastpathGeneration();
        m_guardTerminalResetDeferred = previous_defer;
        NS_ABORT_MSG_IF(
            m_guardFastpathPhase != GUARD_FASTPATH_IDLE ||
                m_guardFrozenVectorActive,
            "GUARD V14 ACTIVE-empty flow set failed to close its transaction");
    }

    // No grant needs to be sent after the last controlled flow leaves.  In
    // particular, do not compute C / N for N == 0.
    if (m_rate_flow_ctl_set.empty()) {
        SettleGuardActiveEmptyState();
        return true;
    }
    RequestGuardMembershipUpdate("release");
    ScheduleGuardRebalance();
    if (GuardSmallSetFastpathEnabled() && m_guardPendingGrantAcks == 0 &&
        m_guardFastpathPhase != GUARD_FASTPATH_IDLE) {
        FinishGuardFastpathGeneration();
    }
    return true;
}

Time RdmaHw::GetGuardInitialCollectionQuietWindow() const {
    return m_guardInitialCollectionQuietWindow.IsZero()
               ? m_guardMembershipCoalesceWindow
               : m_guardInitialCollectionQuietWindow;
}

bool RdmaHw::GuardSmallSetFastpathEnabled() const {
    return m_guardSmallSetFastpathLimit > 0;
}

const char *RdmaHw::GetGuardFastpathPhaseName() const {
    if (m_guardFastpathPhase == GUARD_FASTPATH_PREPARE) {
        return m_guardFastpathTransactionIsTransition
                   ? "transition_prepare"
                   : "fast_prepare";
    }
    if (m_guardFastpathPhase == GUARD_FASTPATH_ACTIVATE) {
        return m_guardFastpathTransactionIsTransition
                   ? "transition_activate"
                   : "fast_activate";
    }
    if (m_guardFastpathPhase == GUARD_FASTPATH_PREFIX_BARRIER) {
        return "transition_prefix";
    }
    return "idle";
}

const char *RdmaHw::GetGuardGrantPhaseTagName(uint8_t phase_tag) const {
    switch (phase_tag) {
        case GUARD_GRANT_PHASE_FAST_PREPARE:
            return "fast_prepare";
        case GUARD_GRANT_PHASE_FAST_ACTIVATE:
            return "fast_activate";
        case GUARD_GRANT_PHASE_TRANSITION_PREPARE:
            return "transition_prepare";
        case GUARD_GRANT_PHASE_TRANSITION_ACTIVATE:
            return "transition_activate";
        case GUARD_GRANT_PHASE_RELEASE:
            return "release";
        default:
            return "none";
    }
}

uint8_t RdmaHw::GetGuardFastpathWirePhaseTag(const char *set_change) const {
    if (!GuardSmallSetFastpathEnabled()) return GUARD_GRANT_PHASE_NONE;
    if (m_guardFastpathPhase == GUARD_FASTPATH_PREPARE) {
        return m_guardFastpathTransactionIsTransition
                   ? GUARD_GRANT_PHASE_TRANSITION_PREPARE
                   : GUARD_GRANT_PHASE_FAST_PREPARE;
    }
    if (m_guardFastpathPhase == GUARD_FASTPATH_ACTIVATE) {
        return m_guardFastpathTransactionIsTransition
                   ? GUARD_GRANT_PHASE_TRANSITION_ACTIVATE
                   : GUARD_GRANT_PHASE_FAST_ACTIVATE;
    }
    return std::string(set_change) == "release"
               ? GUARD_GRANT_PHASE_RELEASE
               : GUARD_GRANT_PHASE_NONE;
}

void RdmaHw::CountGuardFastpathGrantFrame(uint8_t phase_tag) {
    switch (phase_tag) {
        case GUARD_GRANT_PHASE_FAST_PREPARE:
            m_guardFastPrepareWireGrantFrames++;
            break;
        case GUARD_GRANT_PHASE_FAST_ACTIVATE:
            m_guardFastActivateWireGrantFrames++;
            break;
        case GUARD_GRANT_PHASE_TRANSITION_PREPARE:
            m_guardTransitionPrepareWireGrantFrames++;
            break;
        case GUARD_GRANT_PHASE_TRANSITION_ACTIVATE:
            m_guardTransitionActivateWireGrantFrames++;
            break;
        case GUARD_GRANT_PHASE_RELEASE:
            m_guardReleaseWireGrantFrames++;
            break;
        default:
            m_guardFastpathUnattributedGrantFrames++;
            break;
    }
}

void RdmaHw::CountGuardFastpathAckFrame(uint8_t phase_tag) {
    switch (phase_tag) {
        case GUARD_GRANT_PHASE_FAST_PREPARE:
            m_guardFastPrepareWireAckFrames++;
            break;
        case GUARD_GRANT_PHASE_FAST_ACTIVATE:
            m_guardFastActivateWireAckFrames++;
            break;
        case GUARD_GRANT_PHASE_TRANSITION_PREPARE:
            m_guardTransitionPrepareWireAckFrames++;
            break;
        case GUARD_GRANT_PHASE_TRANSITION_ACTIVATE:
            m_guardTransitionActivateWireAckFrames++;
            break;
        default:
            m_guardFastpathUnattributedAckFrames++;
            break;
    }
}

void RdmaHw::CountGuardAcceptedAck(uint8_t phase_tag) {
    if (!GuardSmallSetFastpathEnabled()) return;
    switch (phase_tag) {
        case GUARD_GRANT_PHASE_FAST_PREPARE:
            m_guardFastpathPrepareAcks++;
            break;
        case GUARD_GRANT_PHASE_FAST_ACTIVATE:
            m_guardFastpathActivateAcks++;
            break;
        case GUARD_GRANT_PHASE_TRANSITION_PREPARE:
            m_guardTransitionPrepareAcks++;
            break;
        case GUARD_GRANT_PHASE_TRANSITION_ACTIVATE:
            m_guardTransitionActivateAcks++;
            break;
        default:
            NS_ABORT_MSG("GUARD required ACK has no exact fast-path phase");
    }
}

uint32_t RdmaHw::NextGuardGrantGeneration() {
    NS_ABORT_MSG_IF(
        m_guardGrantGeneration == std::numeric_limits<uint32_t>::max(),
        "GUARD grant generation exhausted");
    return ++m_guardGrantGeneration;
}

void RdmaHw::ResetGuardFastpathEpoch() {
    NS_ABORT_MSG_IF(m_guardSerializedDraining &&
                        !m_guardDrainingRecords.empty(),
                    "GUARD V14 cannot reset with draining reservations");
    if (m_guardTransitionAuditRecord.active) {
        if (m_guardTransitionAuditRecord.deadlineOutcome == "pending") {
            ResolveGuardTransitionAudit("epoch_reset_unresolved");
        }
        FinalizeGuardTransitionAudit("epoch_reset");
    }
    if (m_guardTransitionAuditSink != NULL &&
        m_guardTransitionAuditSink->file != NULL) {
        NS_ABORT_MSG_IF(
            m_guardTransitionAuditEpoch == std::numeric_limits<uint64_t>::max(),
            "GUARD V14 transition audit epoch exhausted");
        m_guardTransitionAuditEpoch++;
    }
    if (m_guardMembershipEvent.IsRunning()) Simulator::Cancel(m_guardMembershipEvent);
    if (m_guardReliabilityRefreshEvent.IsRunning()) {
        Simulator::Cancel(m_guardReliabilityRefreshEvent);
    }
    if (m_guardTransitionPrefixDeadlineEvent.IsRunning()) {
        Simulator::Cancel(m_guardTransitionPrefixDeadlineEvent);
    }
    m_guardFastpathPhase = GUARD_FASTPATH_IDLE;
    m_guardFastpathHighFanIn = false;
    m_guardFastpathHighInitialCollectionFlushed = false;
    m_guardFastpathHighInitialCommitted = false;
    m_guardFastpathCollectionReady = false;
    m_guardFastpathTransactionIsTransition = false;
    m_guardFastpathReleaseRevectorActive = false;
    m_guardFastpathMembershipRevision = 0;
    m_guardFastpathConsumedMembershipRevision = 0;
    m_guardFastpathReadyMembershipRevision = 0;
    m_guardFastpathEpochPrefixCloseNs = 0;
    m_guardFastpathEpochCollectionFlushNs = 0;
    m_guardFastpathEpochTransitionPrepareStartNs = 0;
    m_guardFastpathTransactionTargetN = 0;
    m_guardFastpathTransactionTargetMbps = 0;
    m_guardFrozenVectorActive = false;
    m_guardFrozenReceiverNic = 0;
    m_guardFrozenReceiverCapacityBps = 0;
    m_guardFrozenActiveRecords = 0;
    m_guardFrozenDrainingRecords = 0;
    m_guardFrozenDrainingReservedBps = 0;
    m_guardFrozenAllocatableCapacityBps = 0;
    m_guardFrozenEncodedTargetBps = 0;
    m_guardFrozenMembershipRevision = 0;
    // Allocation revision is a run-lifetime trace identity.  Membership and
    // progress revisions are epoch-local, but reusing an allocation revision
    // would make two frozen tuples indistinguishable in one bounded trace.
    m_guardProgressRevision = 0;
    m_guardConsumedProgressRevision = 0;
    m_guardFrozenAllocationRevision = 0;
    m_guardFrozenProgressRevision = 0;
    m_guardFrozenReasonMask = 0;
    m_guardProgressTransactionRevision = 0;
    m_guardProgressTransactionActive = false;
    m_guardProgressTransactionCapacityDirty = false;
    m_guardCapacityDirty = false;
    m_guardApplyingPendingDrains = false;
    m_guardTerminalResetDeferred = false;
    m_guardCapacityAdmissionBlocked = false;
    m_guardProgressDirtyFlows.clear();
    m_guardProgressTransactionFlows.clear();
    m_guardFrozenVectorHash = 0;
    m_guardFrozenTargetVector.clear();
    m_guardFrozenTargetHolds.clear();
    m_guardFrozenTargetByFlow.clear();
    m_guardGenerationLedger.clear();
    m_guardFastpathIncumbents.clear();
    m_guardFastpathWaiters.clear();
    m_guardFastpathTransactionWaiters.clear();
    m_guardFastpathReadyWaiters.clear();
    m_guardTransitionPrefixBarrierWaiting = false;
    m_guardTransitionPrefixBarrierResolved = false;
    m_guardTransitionPrefixTimedOut = false;
    m_guardTransitionFallbackActive = false;
    m_guardTransitionPrefixWatchdogBudgetActive = false;
    m_guardTransitionPrefixBarrierStart = Time(0);
    m_guardTransitionPrefixDeadline = Time(0);
    m_guardTransitionActivationOrder.clear();
    m_guardTransitionActivationHolds.clear();
    m_guardTransitionPrefixTargets.clear();
    m_guardTransitionCurrentBatch.clear();
    m_guardTransitionActivationCursor = 0;
    m_guardTransitionActivationBatchIndex = 0;
    m_guardTransitionActivationBatchSize = 0;
    m_guardTransitionLastRegisterNs = -1;
    m_guardTransitionLastFlowId = -1;
    m_guardPendingMembershipChanges = 0;
    m_guardPendingGrantAcks = 0;
    m_guardMembershipBatchStart = Time(0);
    m_guardInitialCollectionStart = Time(0);
    m_guardInitialCollectionDeadline = Time(0);
    m_guardInitialCollectionTarget = Time(0);
    m_guardInitialCollectionPending = false;
    m_guardHasEmittedVectorThisEpoch = false;
    m_guardLastVectorActiveFlows = 0;
}

void RdmaHw::ConsumeGuardFastpathMembershipThrough(uint64_t revision) {
    NS_ABORT_MSG_IF(
        revision < m_guardFastpathConsumedMembershipRevision ||
            revision > m_guardFastpathMembershipRevision,
        "GUARD fast-path consumed an invalid membership revision");
    m_guardFastpathConsumedMembershipRevision = revision;
    m_guardPendingMembershipChanges =
        m_guardFastpathMembershipRevision - revision;
}

void RdmaHw::RequestGuardFastpathMembershipUpdate(const char *set_change) {
    NS_ABORT_MSG_IF(!GuardSmallSetFastpathEnabled(),
                    "GUARD small-set request without an enabled fast path");
    m_guardMembershipChangesDeferred++;
    NS_ABORT_MSG_IF(
        m_guardFastpathMembershipRevision ==
            std::numeric_limits<uint64_t>::max(),
        "GUARD fast-path membership revision exhausted");
    m_guardFastpathMembershipRevision++;
    m_guardPendingMembershipChanges =
        m_guardFastpathMembershipRevision -
        m_guardFastpathConsumedMembershipRevision;
    bool registration = std::string(set_change) == "registration";

    if (!m_guardFastpathHighFanIn &&
        m_rate_flow_ctl_set.size() > m_guardSmallSetFastpathLimit) {
        m_guardFastpathHighFanIn = true;
        m_guardFastpathHighFanInTransitions++;
        m_guardFastpathHighTransitionRegisteredN = m_rate_flow_ctl_set.size();
        m_guardFastpathHighTransitionWaiters = m_guardFastpathWaiters.size();
    }

    if (m_guardFastpathHighFanIn) {
        if (m_guardFastpathCollectionReady) {
            m_guardFastpathQueuedMembershipChanges++;
            m_guardFastpathJoinQueueMax = std::max<uint64_t>(
                m_guardFastpathJoinQueueMax, m_guardFastpathWaiters.size());
            return;
        }
        ScheduleGuardFastpathHighCollection(set_change);
        return;
    }

    if (m_guardFastpathPhase != GUARD_FASTPATH_IDLE ||
        m_guardPendingGrantAcks != 0) {
        m_guardFastpathQueuedMembershipChanges++;
        m_guardFastpathJoinQueueMax = std::max<uint64_t>(
            m_guardFastpathJoinQueueMax, m_guardFastpathWaiters.size());
        return;
    }
    if (registration) {
        StartGuardFastpathTransaction();
    } else {
        ConsumeGuardFastpathMembershipThrough(
            m_guardFastpathMembershipRevision);
        SendGuardFastpathOptionalRelease();
    }
}

void RdmaHw::RequestGuardProgressRefresh(RdmaRxQueuePair *flow,
                                         uint64_t progress_seq) {
    NS_ABORT_MSG_IF(!m_guardSerializedProgressRefresh ||
                        !m_guardMixedPgVectorFastpath,
                    "GUARD V14 progress refresh escaped its vector coordinator");
    if (m_rate_flow_ctl_set.find(flow) == m_rate_flow_ctl_set.end() ||
        progress_seq <= flow->m_guard_pending_progress_seq) {
        return;
    }
    flow->m_guard_pending_progress_seq = progress_seq;
    bool first_dirty = m_guardProgressDirtyFlows.emplace(flow).second;
    if (first_dirty) {
        NS_ABORT_MSG_IF(
            m_guardProgressRevision == std::numeric_limits<uint64_t>::max(),
            "GUARD V14 progress revision exhausted");
        m_guardProgressRevision++;
        m_guardSerializedProgressRequests++;
    }
    bool busy = m_guardProgressTransactionActive ||
                m_guardFastpathPhase != GUARD_FASTPATH_IDLE ||
                m_guardPendingGrantAcks != 0 || m_guardFrozenVectorActive ||
                IsGuardMembershipDirty() || m_guardFastpathCollectionReady;
    if (busy) {
        m_guardProgressEventsCoalesced++;
        m_guardSerializedProgressBusyDeferrals++;
        return;
    }
    StartGuardProgressTransaction();
}

void RdmaHw::RequestGuardCapacityRefresh() {
    NS_ABORT_MSG_IF((!m_guardElephantCapSpillover &&
                     !m_guardCapTriggeredRefresh) ||
                        !m_guardSerializedProgressRefresh ||
                        !m_guardMixedPgVectorFastpath,
                    "GUARD elephant spillover escaped its vector coordinator");
    if (m_rate_flow_ctl_set.empty() || m_guardCapacityDirty) return;
    NS_ABORT_MSG_IF(
        m_guardProgressRevision == std::numeric_limits<uint64_t>::max(),
        "GUARD elephant spillover progress revision exhausted");
    m_guardProgressRevision++;
    m_guardCapacityDirty = true;
    if (m_guardCapTriggeredRefresh) {
        m_guardCapTriggeredRefreshRequests++;
    } else {
        m_guardElephantSpilloverRefreshRequests++;
    }
    bool busy = m_guardProgressTransactionActive ||
                m_guardFastpathPhase != GUARD_FASTPATH_IDLE ||
                m_guardPendingGrantAcks != 0 || m_guardFrozenVectorActive ||
                IsGuardMembershipDirty() || m_guardFastpathCollectionReady;
    if (busy) {
        m_guardProgressEventsCoalesced++;
        m_guardSerializedProgressBusyDeferrals++;
        return;
    }
    StartGuardProgressTransaction();
}

void RdmaHw::StartGuardProgressTransaction() {
    NS_ABORT_MSG_IF(!m_guardSerializedProgressRefresh ||
                        m_guardProgressTransactionActive ||
                        (m_guardProgressDirtyFlows.empty() &&
                         !m_guardCapacityDirty) ||
                        m_guardFastpathPhase != GUARD_FASTPATH_IDLE ||
                        m_guardPendingGrantAcks != 0 || m_guardFrozenVectorActive,
                    "GUARD V14 progress transaction crossed serialized state");
    m_guardProgressTransactionFlows.swap(m_guardProgressDirtyFlows);
    m_guardProgressTransactionCapacityDirty = m_guardCapacityDirty;
    m_guardCapacityDirty = false;
    m_guardProgressTransactionRevision = m_guardProgressRevision;
    m_guardProgressTransactionActive = true;
    m_guardSerializedProgressTransactions++;
    SendGuardFastpathOptionalRelease();
}

void RdmaHw::CloseGuardProgressTransaction() {
    if (!m_guardProgressTransactionActive) return;
    NS_ABORT_MSG_IF(!m_guardFrozenVectorActive ||
                        m_guardPendingGrantAcks != 0,
                    "GUARD V14 progress committed before its vector barrier closed");
    for (auto *flow : m_guardProgressTransactionFlows) {
        if (m_rate_flow_ctl_set.find(flow) == m_rate_flow_ctl_set.end()) continue;
        auto *target = GetGuardFrozenTarget(flow);
        NS_ABORT_MSG_IF(target == NULL || target->tombstone ||
                            target->progressRevision !=
                                m_guardProgressTransactionRevision,
                        "GUARD V14 progress commit lost its frozen record");
        flow->m_guard_last_schedule_seq = std::max(
            flow->m_guard_last_schedule_seq, target->frozenProgressSeq);
        if (flow->m_guard_pending_progress_seq <=
            flow->m_guard_last_schedule_seq) {
            m_guardProgressDirtyFlows.erase(flow);
        }
    }
    m_guardConsumedProgressRevision = std::max(
        m_guardConsumedProgressRevision, m_guardProgressTransactionRevision);
    m_guardProgressTransactionFlows.clear();
    m_guardProgressTransactionRevision = 0;
    m_guardProgressTransactionActive = false;
    m_guardProgressTransactionCapacityDirty = false;
    m_guardSerializedProgressCommits++;
}

uint64_t RdmaHw::CommitGuardPendingDrains() {
    std::vector<RdmaRxQueuePair*> pending;
    for (auto &item : m_guardDrainingRecords) {
        if (item.second.state == GUARD_DRAIN_PENDING)
            pending.push_back(item.first);
    }
    if (pending.empty()) return 0;
    m_guardApplyingPendingDrains = true;
    uint64_t committed = 0;
    uint64_t proactive_removals = 0;
    for (auto *flow : pending) {
        auto found = m_guardDrainingRecords.find(flow);
        if (found == m_guardDrainingRecords.end() ||
            found->second.state != GUARD_DRAIN_PENDING) {
            continue;
        }
        bool active = m_rate_flow_ctl_set.find(flow) !=
                      m_rate_flow_ctl_set.end();
        bool completed = CanReleaseGuardDraining(
            found->second.hold->m_guard_flow_size,
            found->second.hold->ReceiverNextExpectedSeq);
        if (!active) {
            NS_ABORT_MSG_IF(
                !completed,
                "GUARD V14 pending drain lost ACTIVE before contiguous completion");
            found->second.state = GUARD_DRAINING;
            m_guardDrainingRecords.erase(found);
            m_guardDrainingCompletionReleases++;
            committed++;
            continue;
        }
        uint64_t effective_min_mbps =
            m_minRate.GetBitRate() / 1000000ULL +
            (m_minRate.GetBitRate() % 1000000ULL != 0 ? 1 : 0);
        NS_ABORT_MSG_IF(
            effective_min_mbps >
                std::numeric_limits<uint64_t>::max() / 1000000ULL,
            "GUARD V14 effective minimum rate overflow");
        GuardDrainingRecord &record = found->second;
        record.lastAckedUpperBoundBps =
            flow->m_guard_last_acked_upper_bound_bps;
        record.lastIssuedTargetBps = flow->m_guard_last_issued_target_bps;
        record.generation = flow->m_guard_last_issued_generation;
        record.reservedBps = ComputeGuardDrainingReservationBps(
            record.lastAckedUpperBoundBps, record.lastIssuedTargetBps,
            effective_min_mbps * 1000000ULL);
        AssertGuardDrainReplacementFits(flow, record.receiverNic,
                                        record.reservedBps);
        found->second.state = GUARD_DRAINING;
        m_guardDrainingMaxReservedBps = std::max(
            m_guardDrainingMaxReservedBps,
            GetGuardDrainingReservedBps(record.receiverNic));
        Ptr<RdmaRxQueuePair> hold = found->second.hold;
        bool removed = HandleRccRemove(
            hold, GUARD_RELEASE_PROACTIVE,
            found->second.remainingBytesAtRelease);
        if (removed) {
            committed++;
            proactive_removals++;
            if (CanReleaseGuardDraining(hold->m_guard_flow_size,
                                        hold->ReceiverNextExpectedSeq)) {
                auto completed_record = m_guardDrainingRecords.find(flow);
                NS_ABORT_MSG_IF(
                    completed_record == m_guardDrainingRecords.end() ||
                        completed_record->second.state != GUARD_DRAINING,
                    "GUARD V14 boundary completion lost its draining record");
                m_guardDrainingRecords.erase(completed_record);
                m_guardDrainingCompletionReleases++;
                m_guardCompletionReleases++;
            }
        }
    }
    m_guardApplyingPendingDrains = false;
    m_guardDrainingBoundaryCommits += committed;
    if (proactive_removals > 0 && !m_rate_flow_ctl_set.empty()) {
        NS_ABORT_MSG_IF(
            proactive_removals > std::numeric_limits<uint64_t>::max() -
                                     m_guardFastpathMembershipRevision,
            "GUARD V14 pending-drain membership revision exhausted");
        m_guardMembershipChangesDeferred += proactive_removals;
        m_guardFastpathMembershipRevision += proactive_removals;
        m_guardPendingMembershipChanges =
            m_guardFastpathMembershipRevision -
            m_guardFastpathConsumedMembershipRevision;
    }
    return committed;
}

void RdmaHw::SettleGuardActiveEmptyState() {
    NS_ABORT_MSG_IF(!m_rate_flow_ctl_set.empty(),
                    "GUARD active-empty cleanup retained an ACTIVE flow");
    if (m_guardMembershipEvent.IsRunning()) {
        Simulator::Cancel(m_guardMembershipEvent);
        m_guardMembershipEmptyCancellations++;
        if (m_guardInitialCollectionPending) {
            m_guardInitialCollectionCancellations++;
        }
    }
    m_guardPendingMembershipChanges = 0;
    m_guardMembershipBatchStart = Time(0);
    m_guardInitialCollectionStart = Time(0);
    m_guardInitialCollectionDeadline = Time(0);
    m_guardInitialCollectionTarget = Time(0);
    m_guardLastVectorActiveFlows = 0;
    m_guardHasEmittedVectorThisEpoch = false;
    m_guardInitialCollectionPending = false;
    if (m_guardRebalanceEvent.IsRunning()) Simulator::Cancel(m_guardRebalanceEvent);
    if (m_guardReliabilityRefreshEvent.IsRunning()) {
        Simulator::Cancel(m_guardReliabilityRefreshEvent);
    }
    NS_ABORT_MSG_IF(
        m_guardSerializedDraining &&
            (m_guardFastpathPhase != GUARD_FASTPATH_IDLE ||
             m_guardPendingGrantAcks != 0 || m_guardFrozenVectorActive),
        "GUARD V14 ACTIVE-empty state crossed a live transaction");
    m_guardPendingGrantAcks = 0;
    if (GuardSmallSetFastpathEnabled() && m_guardSerializedDraining) {
        m_guardFastpathConsumedMembershipRevision =
            m_guardFastpathMembershipRevision;
        m_guardConsumedProgressRevision = m_guardProgressRevision;
        m_guardProgressDirtyFlows.clear();
        m_guardProgressTransactionFlows.clear();
        m_guardProgressTransactionRevision = 0;
        m_guardProgressTransactionActive = false;
        m_guardProgressTransactionCapacityDirty = false;
        m_guardCapacityDirty = false;
        if (m_guardDrainingRecords.empty()) {
            NS_ABORT_MSG_IF(!CanResetGuardCoordinator(),
                            "GUARD V14 terminal cleanup retained coordinator state");
            ResetGuardFastpathEpoch();
        }
    } else if (GuardSmallSetFastpathEnabled()) {
        ResetGuardFastpathEpoch();
    }
    m_guardLastRebalanceTime = Time(0);
}

bool RdmaHw::CanResetGuardCoordinator() const {
    bool prefix_clean = !m_guardTransitionPrefixBarrierWaiting &&
                        !m_guardTransitionFallbackActive &&
                        !m_guardTransitionPrefixWatchdogBudgetActive &&
                        m_guardTransitionActivationOrder.empty() &&
                        m_guardTransitionActivationHolds.empty() &&
                        m_guardTransitionPrefixTargets.empty() &&
                        m_guardTransitionCurrentBatch.empty();
    bool vector_clean = !m_guardFrozenVectorActive &&
                        m_guardFrozenTargetVector.empty() &&
                        m_guardFrozenTargetHolds.empty() &&
                        m_guardFrozenTargetByFlow.empty() &&
                        m_guardGenerationLedger.empty();
    bool revisions_clean =
        m_guardPendingMembershipChanges == 0 &&
        m_guardFastpathConsumedMembershipRevision ==
            m_guardFastpathMembershipRevision &&
        m_guardConsumedProgressRevision == m_guardProgressRevision;
    return m_rate_flow_ctl_set.empty() && m_guardDrainingRecords.empty() &&
           m_guardFastpathIncumbents.empty() &&
           m_guardFastpathWaiters.empty() &&
           m_guardFastpathTransactionWaiters.empty() &&
           m_guardFastpathReadyWaiters.empty() && vector_clean &&
           prefix_clean && revisions_clean &&
           m_guardFastpathPhase == GUARD_FASTPATH_IDLE &&
           m_guardPendingGrantAcks == 0 &&
           !m_guardProgressTransactionActive &&
           !m_guardProgressTransactionCapacityDirty &&
           !m_guardCapacityAdmissionBlocked &&
           !m_guardTerminalResetDeferred &&
           m_guardProgressDirtyFlows.empty() &&
           m_guardProgressTransactionFlows.empty() && !m_guardCapacityDirty;
}

void RdmaHw::ScheduleGuardFastpathHighCollection(const char *set_change) {
    NS_ABORT_MSG_IF(m_guardPendingMembershipChanges == 0,
                    "GUARD fast-path scheduled a clean membership state");
    Time now = Simulator::Now();
    bool initial = !m_guardFastpathHighInitialCollectionFlushed;
    Time window = initial ? GetGuardInitialCollectionQuietWindow()
                          : m_guardMembershipCoalesceWindow;
    NS_ABORT_MSG_IF(window.IsZero(),
                    "GUARD fast-path high-fan-in collection needs a window");
    if (initial && !m_guardInitialCollectionPending) {
        int64_t window_ns = window.GetNanoSeconds();
        uint64_t max_time_value = static_cast<uint64_t>(
            std::numeric_limits<int64_t>::max());
        NS_ABORT_MSG_IF(
            window_ns <= 0 || m_guardMembershipCoalesceMaxWindows == 0 ||
                static_cast<uint64_t>(window_ns) >
                    max_time_value / m_guardMembershipCoalesceMaxWindows,
            "GUARD fast-path initial collection deadline overflow");
        Time delay = NanoSeconds(static_cast<int64_t>(
            static_cast<uint64_t>(window_ns) *
            m_guardMembershipCoalesceMaxWindows));
        NS_ABORT_MSG_IF(
            now.GetTimeStep() < 0 || delay.GetTimeStep() <= 0 ||
                now.GetTimeStep() > std::numeric_limits<int64_t>::max() -
                                        delay.GetTimeStep(),
            "GUARD fast-path initial collection absolute deadline overflow");
        m_guardInitialCollectionStart = now;
        m_guardInitialCollectionDeadline = now + delay;
        m_guardInitialCollectionTarget = m_guardInitialCollectionFullDeadline
                                             ? m_guardInitialCollectionDeadline
                                             : now + window;
        m_guardInitialCollectionPending = true;
        m_guardInitialCollectionStarts++;
    } else if (initial) {
        m_guardInitialCollectionDeferredChanges++;
        if (!m_guardInitialCollectionFullDeadline) {
            NS_ABORT_MSG_IF(now > m_guardInitialCollectionDeadline,
                            "GUARD fast-path sliding collection passed deadline");
            Time remaining = m_guardInitialCollectionDeadline - now;
            m_guardInitialCollectionTarget = remaining <= window
                                                 ? m_guardInitialCollectionDeadline
                                                 : now + window;
            m_guardInitialCollectionReschedules++;
        }
    }

    if (!initial) {
        bool release = std::string(set_change) == "release";
        if (release && !m_guardMembershipEvent.IsRunning() &&
            m_guardLastVectorActiveFlows > 0 &&
            m_guardFastpathIncumbents.size() > m_guardLastVectorActiveFlows / 2) {
            m_guardReleaseThresholdDeferrals++;
            return;
        }
        if (m_guardMembershipBatchStart.IsZero()) m_guardMembershipBatchStart = now;
        Time deadline = m_guardMembershipBatchStart + NanoSeconds(
            m_guardMembershipCoalesceWindow.GetNanoSeconds() *
            m_guardMembershipCoalesceMaxWindows);
        m_guardInitialCollectionTarget = std::min(now + window, deadline);
    }

    if (m_guardMembershipEvent.IsRunning()) {
        Simulator::Cancel(m_guardMembershipEvent);
        m_guardMembershipTimerReschedules++;
    }
    Time target = m_guardInitialCollectionTarget;
    m_guardMembershipEvent = Simulator::Schedule(
        std::max(Time(0), target - now),
        &RdmaHw::FlushGuardFastpathHighCollection, this);
}

void RdmaHw::FlushGuardFastpathHighCollection() {
    NS_ABORT_MSG_IF(!m_guardFastpathHighFanIn,
                    "GUARD fast-path collection fired outside high-fan-in mode");
    if (m_guardPendingMembershipChanges == 0) return;
    bool initial = !m_guardFastpathHighInitialCollectionFlushed;
    if (initial) {
        Time expected = m_guardInitialCollectionFullDeadline
                            ? m_guardInitialCollectionDeadline
                            : m_guardInitialCollectionTarget;
        NS_ABORT_MSG_IF(Simulator::Now() != expected,
                        "GUARD fast-path initial collection target mismatch");
        uint64_t wait_ns = static_cast<uint64_t>(
            (Simulator::Now() - m_guardInitialCollectionStart).GetNanoSeconds());
        m_guardInitialCollectionFlushes++;
        if (expected == m_guardInitialCollectionDeadline) {
            m_guardInitialCollectionHardFlushes++;
        } else {
            m_guardInitialCollectionQuietFlushes++;
        }
        m_guardInitialCollectionWaitNs += wait_ns;
        m_guardInitialCollectionMaxWaitNs = std::max(
            m_guardInitialCollectionMaxWaitNs, wait_ns);
        m_guardInitialCollectionPending = false;
        m_guardFastpathHighInitialCollectionFlushed = true;
    }
    m_guardFastpathHighCollectionFlushes++;
    if (initial) {
        m_guardFastpathEpochCollectionFlushNs = static_cast<uint64_t>(
            Simulator::Now().GetNanoSeconds());
    }
    m_guardFastpathReadyWaiters.clear();
    for (auto *flow : m_guardFastpathWaiters) {
        if (m_guardFastpathTransactionWaiters.find(flow) ==
            m_guardFastpathTransactionWaiters.end()) {
            m_guardFastpathReadyWaiters.emplace(flow);
        }
    }
    m_guardFastpathReadyMembershipRevision =
        m_guardFastpathMembershipRevision;
    m_guardMembershipBatchStart = Time(0);
    m_guardFastpathCollectionReady = true;
    if (m_guardFastpathPhase == GUARD_FASTPATH_IDLE &&
        m_guardPendingGrantAcks == 0) {
        if (!m_guardFastpathReadyWaiters.empty()) {
            StartGuardFastpathTransaction();
        } else {
            m_guardFastpathCollectionReady = false;
            ConsumeGuardFastpathMembershipThrough(
                m_guardFastpathReadyMembershipRevision);
            SendGuardFastpathOptionalRelease();
        }
    }
}

void RdmaHw::FreezeGuardFastpathTargetVector(uint64_t membership_revision) {
    NS_ABORT_MSG_IF(!m_guardMixedPgVectorFastpath ||
                        m_guardFrozenVectorActive,
                    "GUARD V14 vector freeze crossed transaction state");
    std::vector<RdmaRxQueuePair*> cohort;
    std::unordered_set<RdmaRxQueuePair*> cohort_set;
    cohort.reserve(m_guardFastpathIncumbents.size() +
                   m_guardFastpathTransactionWaiters.size());
    for (auto *flow : m_guardFastpathIncumbents) {
        if (m_rate_flow_ctl_set.find(flow) != m_rate_flow_ctl_set.end()) {
            NS_ABORT_MSG_IF(!cohort_set.emplace(flow).second,
                            "GUARD V14 frozen cohort contains duplicate roles");
            cohort.push_back(flow);
        }
    }
    for (auto *flow : m_guardFastpathTransactionWaiters) {
        if (m_rate_flow_ctl_set.find(flow) != m_rate_flow_ctl_set.end()) {
            NS_ABORT_MSG_IF(!cohort_set.emplace(flow).second,
                            "GUARD V14 frozen cohort contains duplicate roles");
            cohort.push_back(flow);
        }
    }
    NS_ABORT_MSG_IF(cohort.empty(), "GUARD V14 cannot freeze an empty vector");
    uint32_t receiver_nic = GetNicIdxOfRxQp(cohort.front());
    uint64_t capacity_bps =
        m_nic[receiver_nic].dev->GetDataRate().GetBitRate();
    NS_ABORT_MSG_IF(capacity_bps == 0,
                    "GUARD V14 vector receiver has zero capacity");
    // The frozen cohort and the live ACTIVE set are intentionally distinct.
    // A busy collection can leave a later registration in the global set;
    // prove that every such record is a gated, zero-authority deferred waiter
    // instead of silently admitting it to this immutable vector.
    for (auto *flow : m_rate_flow_ctl_set) {
        uint32_t flow_nic = GetNicIdxOfRxQp(flow);
        uint64_t flow_capacity_bps =
            m_nic[flow_nic].dev->GetDataRate().GetBitRate();
        GuardFrozenActiveProvenanceInput provenance = {
            cohort_set.find(flow) != cohort_set.end(),
            m_guardFastpathWaiters.find(flow) != m_guardFastpathWaiters.end(),
            m_guardFastpathIncumbents.find(flow) !=
                m_guardFastpathIncumbents.end(),
            m_guardFastpathTransactionWaiters.find(flow) !=
                m_guardFastpathTransactionWaiters.end(),
            flow->m_guard_registration_membership_revision,
            m_guardFastpathMembershipRevision,
            membership_revision,
            m_guardPendingMembershipChanges,
            flow_nic,
            receiver_nic,
            flow_capacity_bps,
            capacity_bps,
            flow->m_guard_last_acked_generation,
            flow->m_guard_last_acked_upper_bound_bps,
            flow->m_guard_last_issued_generation,
            flow->m_guard_last_issued_target_bps,
            flow->m_guard_grant_generation,
            flow->m_guard_grant_rate_bps,
            flow->m_guard_grant_upper_bound_bps,
            flow->m_guard_grant_generation_acked,
            flow->m_guard_register_ns,
            flow->m_guard_flow_size,
            flow->m_guard_has_first_grant_gate_bytes,
            flow->m_guard_first_grant_gate_bytes,
            flow->m_base_rtt_sec};
        NS_ABORT_MSG_IF(
            ClassifyGuardFrozenActiveProvenance(provenance) ==
                GUARD_FROZEN_ACTIVE_INVALID,
            "GUARD V14 live ACTIVE record violates frozen cohort provenance");
    }
    uint64_t draining_reserved_bps =
        GetGuardDrainingReservedBps(receiver_nic);
    NS_ABORT_MSG_IF(draining_reserved_bps > capacity_bps,
                    "GUARD V14 draining reservation exceeds receiver capacity");
    uint64_t allocatable_bps = capacity_bps - draining_reserved_bps;
    uint64_t effective_min_mbps = m_minRate.GetBitRate() / 1000000ULL +
        (m_minRate.GetBitRate() % 1000000ULL != 0 ? 1 : 0);
    uint64_t effective_min_bps = effective_min_mbps * 1000000ULL;
    __uint128_t waiter_floor = static_cast<__uint128_t>(
        m_guardFastpathTransactionWaiters.size()) * effective_min_bps;
    NS_ABORT_MSG_IF(
        waiter_floor > allocatable_bps,
        "GUARD V14 draining capacity cannot admit the frozen waiter floor");
    std::vector<GuardVectorTargetInput> inputs;
    inputs.reserve(cohort.size());
    for (auto *flow : cohort) {
        uint32_t nic = GetNicIdxOfRxQp(flow);
        uint64_t flow_capacity = m_nic[nic].dev->GetDataRate().GetBitRate();
        NS_ABORT_MSG_IF(nic != receiver_nic || flow_capacity != capacity_bps,
                        "GUARD V14 vector spans receiver NICs or capacities");
        uint64_t remaining =
            flow->m_guard_flow_size > flow->ReceiverNextExpectedSeq
                ? flow->m_guard_flow_size - flow->ReceiverNextExpectedSeq
                : 0;
        uint8_t role = m_guardFastpathTransactionWaiters.find(flow) !=
                               m_guardFastpathTransactionWaiters.end()
                           ? GUARD_VECTOR_WAITER
                           : GUARD_VECTOR_INCUMBENT;
        GuardQpIdentity identity = {flow->sip, flow->dip, flow->sport,
                                    flow->dport, flow->m_guard_pg};
        inputs.push_back({identity, role, remaining,
                          flow->ReceiverNextExpectedSeq, 0, flow});
    }
    NS_ABORT_MSG_IF(
        !ComputeGuardFrozenRequestedTargets(capacity_bps, allocatable_bps,
                                             &inputs),
        "GUARD V14 frozen remaining-aware allocation is infeasible");
    NS_ABORT_MSG_IF(
        m_guardAllocationRevision == std::numeric_limits<uint64_t>::max(),
        "GUARD V14 allocation revision exhausted");
    uint64_t allocation_revision = ++m_guardAllocationRevision;
    uint64_t frozen_progress_revision = m_guardProgressTransactionActive
                                            ? m_guardProgressTransactionRevision
                                            : m_guardProgressRevision;
    uint64_t reason_mask = 0;
    if (!m_guardProgressTransactionActive) reason_mask |= 1ULL;
    if (m_guardProgressTransactionActive &&
        !m_guardProgressTransactionFlows.empty())
        reason_mask |= 2ULL;
    if (m_guardProgressTransactionActive &&
        m_guardProgressTransactionCapacityDirty)
        reason_mask |= 4ULL;
    for (const auto &item : m_guardDrainingRecords) {
        if (item.second.state == GUARD_DRAIN_PENDING) {
            reason_mask |= 8ULL;
            break;
        }
    }
    NS_ABORT_MSG_IF(reason_mask == 0,
                    "GUARD V14 frozen vector lost its scheduling reason");
    NS_ABORT_MSG_IF(
        !EncodeGuardTargetVector(inputs, receiver_nic, capacity_bps,
                                 draining_reserved_bps, allocatable_bps,
                                 m_minRate.GetBitRate(), membership_revision,
                                 allocation_revision, frozen_progress_revision,
                                 reason_mask,
                                 &m_guardFrozenTargetVector,
                                 &m_guardFrozenVectorHash),
        "GUARD V14 target vector cannot be encoded within receiver capacity");
    m_guardFrozenTargetByFlow.clear();
    m_guardFrozenTargetHolds.clear();
    for (size_t index = 0; index < m_guardFrozenTargetVector.size(); ++index) {
        RdmaRxQueuePair *flow = m_guardFrozenTargetVector[index].flow;
        NS_ABORT_MSG_IF(flow == NULL ||
                            !m_guardFrozenTargetByFlow.emplace(flow, index).second,
                        "GUARD V14 vector contains a duplicate live recipient");
        m_guardFrozenTargetHolds.push_back(Ptr<RdmaRxQueuePair>(flow));
    }
    m_guardFrozenReceiverNic = receiver_nic;
    m_guardFrozenReceiverCapacityBps = capacity_bps;
    uint64_t frozen_draining_records = 0;
    for (const auto &item : m_guardDrainingRecords) {
        if (item.second.state != GUARD_DRAINING) continue;
        NS_ABORT_MSG_IF(item.second.receiverNic != receiver_nic,
                        "GUARD V14 frozen drain count spans receiver resources");
        frozen_draining_records++;
    }
    __uint128_t encoded_target_bps = 0;
    for (const auto &target : m_guardFrozenTargetVector) {
        encoded_target_bps +=
            static_cast<uint64_t>(target.targetMbps) * 1000000ULL;
    }
    NS_ABORT_MSG_IF(encoded_target_bps > allocatable_bps,
                    "GUARD V14 frozen encoded vector exceeds allocatable capacity");
    m_guardFrozenActiveRecords = cohort.size();
    m_guardFrozenDrainingRecords = frozen_draining_records;
    m_guardFrozenDrainingReservedBps = draining_reserved_bps;
    m_guardFrozenAllocatableCapacityBps = allocatable_bps;
    m_guardFrozenEncodedTargetBps =
        static_cast<uint64_t>(encoded_target_bps);
    m_guardFrozenMembershipRevision = membership_revision;
    m_guardFrozenAllocationRevision = allocation_revision;
    m_guardFrozenProgressRevision = frozen_progress_revision;
    m_guardFrozenReasonMask = reason_mask;
    m_guardFrozenVectorActive = true;
    m_guardVectorFreezes++;
    m_guardVectorMaxEntries = std::max<uint64_t>(
        m_guardVectorMaxEntries, m_guardFrozenTargetVector.size());
    std::unordered_set<uint16_t> priority_groups;
    for (const auto &target : m_guardFrozenTargetVector)
        priority_groups.emplace(target.identity.pg);
    uint64_t priority_group_mask = 0;
    for (uint16_t priority_group : priority_groups) {
        NS_ABORT_MSG_IF(priority_group >= 64,
                        "GUARD V16 priority group cannot be represented in audit mask");
        priority_group_mask |= 1ULL << priority_group;
    }
    m_guardVectorMaxPriorityGroups = std::max<uint64_t>(
        m_guardVectorMaxPriorityGroups, priority_groups.size());
    m_guardVectorAllPriorityGroupMask |= priority_group_mask;
    if (priority_groups.size() > 1) {
        m_guardVectorMixedPgFreezes++;
        m_guardVectorMixedPriorityGroupMask |= priority_group_mask;
    }
    m_guardVectorLastHash = m_guardFrozenVectorHash;
}

const GuardFrozenTargetRecord *RdmaHw::GetGuardFrozenTarget(
    RdmaRxQueuePair *flow) const {
    auto found = m_guardFrozenTargetByFlow.find(flow);
    if (found == m_guardFrozenTargetByFlow.end()) return NULL;
    NS_ABORT_MSG_IF(found->second >= m_guardFrozenTargetVector.size(),
                    "GUARD V14 frozen-vector index escaped its bounds");
    return &m_guardFrozenTargetVector[found->second];
}

uint64_t RdmaHw::GetGuardPotentialUpperBoundBps(RdmaRxQueuePair *flow) const {
    return std::max(flow->m_guard_last_acked_upper_bound_bps,
                    flow->m_guard_last_issued_target_bps);
}

void RdmaHw::AssertGuardFrozenPotentialFits() {
    NS_ABORT_MSG_IF(!m_guardFrozenVectorActive ||
                        m_guardFrozenReceiverCapacityBps == 0,
                    "GUARD V14 potential check lost its frozen resource");
    __uint128_t potential_bps = m_guardFrozenDrainingReservedBps;
    for (auto *flow : m_rate_flow_ctl_set) {
        NS_ABORT_MSG_IF(GetNicIdxOfRxQp(flow) != m_guardFrozenReceiverNic,
                        "GUARD V14 potential check spans receiver NICs");
        potential_bps += GetGuardPotentialUpperBoundBps(flow);
    }
    NS_ABORT_MSG_IF(
        potential_bps > m_guardFrozenReceiverCapacityBps,
        "GUARD V14 offered potential exceeds frozen receiver capacity");
}

void RdmaHw::AssertGuardDrainReplacementFits(RdmaRxQueuePair *flow,
                                             uint32_t receiver_nic,
                                             uint64_t reserved_bps) {
    NS_ABORT_MSG_IF(flow == NULL || reserved_bps == 0 ||
                        m_rate_flow_ctl_set.find(flow) ==
                            m_rate_flow_ctl_set.end(),
                    "GUARD V14 drain replacement requires one active flow");
    uint64_t capacity_bps =
        m_nic[receiver_nic].dev->GetDataRate().GetBitRate();
    __uint128_t replacement_bps =
        GetGuardDrainingReservedBps(receiver_nic);
    for (auto *active : m_rate_flow_ctl_set) {
        NS_ABORT_MSG_IF(GetNicIdxOfRxQp(active) != receiver_nic,
                        "GUARD V14 drain replacement spans receiver NICs");
        if (active != flow)
            replacement_bps += GetGuardPotentialUpperBoundBps(active);
    }
    replacement_bps += reserved_bps;
    NS_ABORT_MSG_IF(
        replacement_bps > capacity_bps,
        "GUARD V14 drain replacement exceeds receiver capacity");
}

uint64_t RdmaHw::GetGuardDrainingReservedBps(uint32_t receiver_nic) const {
    __uint128_t reserved = 0;
    for (const auto &item : m_guardDrainingRecords) {
        const GuardDrainingRecord &record = item.second;
        if (record.state != GUARD_DRAINING) continue;
        NS_ABORT_MSG_IF(record.receiverNic != receiver_nic,
                        "GUARD V14 draining record spans receiver NICs");
        reserved += record.reservedBps;
        NS_ABORT_MSG_IF(
            reserved > std::numeric_limits<uint64_t>::max(),
            "GUARD V14 draining reservation sum overflow");
    }
    return static_cast<uint64_t>(reserved);
}

bool RdmaHw::ValidateGuardGrantTraceSnapshot(
    uint64_t frozen_capacity_bps, uint64_t frozen_draining_bps,
    uint64_t frozen_allocatable_bps, uint64_t frozen_encoded_target_bps,
    uint64_t grant_rate_bps, uint64_t live_draining_bps,
    bool capacity_recompute_pending) {
    return frozen_capacity_bps > 0 &&
           frozen_draining_bps <= frozen_capacity_bps &&
           frozen_allocatable_bps ==
               frozen_capacity_bps - frozen_draining_bps &&
           frozen_encoded_target_bps <= frozen_allocatable_bps &&
           grant_rate_bps <= frozen_allocatable_bps &&
           live_draining_bps <= frozen_draining_bps &&
           (live_draining_bps == frozen_draining_bps ||
            capacity_recompute_pending);
}

uint64_t RdmaHw::ComputeGuardDrainingReservationBps(
    uint64_t last_acked_bps, uint64_t last_issued_bps,
    uint64_t effective_min_bps) {
    return std::max(effective_min_bps,
                    std::max(last_acked_bps, last_issued_bps));
}

bool RdmaHw::CanGuardAllocationFloorFit(
    uint64_t capacity_bps, uint64_t draining_bps,
    uint64_t active_records, uint64_t effective_min_bps) {
    if (capacity_bps == 0 || active_records == 0 || effective_min_bps == 0 ||
        draining_bps > capacity_bps) {
        return false;
    }
    __uint128_t required = static_cast<__uint128_t>(active_records) *
                           effective_min_bps;
    return required <= capacity_bps - draining_bps;
}

bool RdmaHw::CanReleaseGuardDraining(uint64_t flow_size_bytes,
                                     uint64_t next_expected_seq) {
    return flow_size_bytes > 0 && next_expected_seq >= flow_size_bytes;
}

bool RdmaHw::ComputeGuardElephantFabricTarget(
    double base_target, bool enabled, uint64_t flow_size_bytes,
    uint64_t path_bdp_bytes, double threshold_bdps, double scale,
    double *effective_target, bool *eligible) {
    if (effective_target == NULL || eligible == NULL ||
        !std::isfinite(base_target) || base_target <= 0.0 ||
        !std::isfinite(threshold_bdps) || threshold_bdps < 0.0 ||
        !std::isfinite(scale) || scale < 1.0 || scale > 2.0 ||
        (enabled && path_bdp_bytes == 0)) {
        return false;
    }
    *eligible = enabled &&
        static_cast<long double>(flow_size_bytes) >
            static_cast<long double>(threshold_bdps) *
                static_cast<long double>(path_bdp_bytes);
    *effective_target = *eligible ? base_target * scale : base_target;
    return std::isfinite(*effective_target) && *effective_target > 0.0;
}

bool RdmaHw::IsGuardElephantReceiverAuthorityEligible(
    bool enabled, bool has_receiver_grant, bool tail_bypass,
    uint64_t flow_size_bytes, uint64_t path_bdp_bytes,
    double threshold_bdps, uint64_t grant_rate_bps,
    uint64_t minimum_rate_bps) {
    if (!enabled || !has_receiver_grant || tail_bypass ||
        path_bdp_bytes == 0 || minimum_rate_bps == 0 ||
        !std::isfinite(threshold_bdps) || threshold_bdps < 0.0) {
        return false;
    }
    return grant_rate_bps > minimum_rate_bps &&
        static_cast<long double>(flow_size_bytes) >
            static_cast<long double>(threshold_bdps) *
                static_cast<long double>(path_bdp_bytes);
}

bool RdmaHw::UsesGuardElephantReceiverAuthority(Ptr<RdmaQueuePair> qp) const {
    return qp != NULL && IsGuardElephantReceiverAuthorityEligible(
        m_guardElephantReceiverAuthority,
        !qp->m_guard_wait_first_grant && qp->m_guard_last_grant_generation > 0,
        qp->m_guard_tail_bypass, qp->m_size, qp->m_win,
        m_guardConcurrencyMinBdps, qp->hp.m_grantRate.GetBitRate(),
        m_minRate.GetBitRate());
}

uint32_t RdmaHw::ComputeGuardEffectiveElephantConcurrency(
    uint32_t configured_max, size_t candidate_count, bool adaptive) {
    if (configured_max == 0 || candidate_count == 0 || !adaptive) {
        return configured_max;
    }
    uint32_t effective = 1;
    while (effective < configured_max) {
        uint64_t next = static_cast<uint64_t>(effective) + 1;
        if (next > candidate_count / next) break;
        effective++;
    }
    return effective;
}

uint32_t RdmaHw::ComputeGuardSizeClassElephantConcurrency(
    uint32_t configured_max, size_t candidate_count,
    uint64_t shortest_remaining, uint64_t second_remaining, bool enabled) {
    if (!enabled) return configured_max;
    if (configured_max < 2 || candidate_count < 2 || shortest_remaining == 0 ||
        second_remaining < shortest_remaining) {
        return 1;
    }
    // Dyadic size classes preserve K=1 SRPT when the next elephant is much
    // larger.  Subtraction avoids overflowing 2 * shortest_remaining.
    return second_remaining - shortest_remaining <= shortest_remaining ? 2 : 1;
}

bool RdmaHw::ComputeGuardFrozenRequestedTargets(
    uint64_t receiver_capacity_bps, uint64_t allocatable_bps,
    std::vector<GuardVectorTargetInput> *inputs) {
    if (inputs == NULL || inputs->empty() || receiver_capacity_bps == 0 ||
        allocatable_bps == 0 || allocatable_bps > receiver_capacity_bps) {
        return false;
    }
    uint64_t effective_min_mbps = m_minRate.GetBitRate() / 1000000ULL +
        (m_minRate.GetBitRate() % 1000000ULL != 0 ? 1 : 0);
    uint64_t effective_min_bps = effective_min_mbps * 1000000ULL;
    __uint128_t floor_budget =
        static_cast<__uint128_t>(effective_min_bps) * inputs->size();
    if (floor_budget > allocatable_bps) return false;
    uint64_t equal_share = allocatable_bps / inputs->size();
    if (!m_guardRemainingAware) {
        for (auto &input : *inputs) input.requestedBps = equal_share;
        return true;
    }

    uint64_t configured_floor = static_cast<uint64_t>(
        static_cast<long double>(equal_share) * m_guardMinShareFraction);
    uint64_t floor_share = std::max(effective_min_bps, configured_floor);
    __uint128_t total_floor =
        static_cast<__uint128_t>(floor_share) * inputs->size();
    if (total_floor > allocatable_bps) return false;
    uint64_t residual = allocatable_bps - static_cast<uint64_t>(total_floor);

    // The pre-V14 allocator already bounded simultaneous elephant service,
    // but canonical frozen vectors initially omitted that policy.  Preserve
    // every shorter registered flow in the residual allocation and admit at
    // most K elephants, ordered deterministically by remaining bytes and the
    // full flow identity.  Deferred elephants retain the same sender-enforced
    // floor, so the resulting vector remains bounded by C-D.
    std::vector<bool> receives_residual(inputs->size(), true);
    std::vector<size_t> elephants;
    if (m_guardReceiverConcurrency > 0) {
        for (size_t index = 0; index < inputs->size(); ++index) {
            RdmaRxQueuePair *flow = (*inputs)[index].flow;
            if (flow == NULL || flow->m_guard_flow_size == 0) return false;
            uint64_t bdp_bytes = 104000;
            if (flow->m_base_rtt_sec > 0.0) {
                long double computed =
                    static_cast<long double>(flow->m_base_rtt_sec) *
                    static_cast<long double>(receiver_capacity_bps) / 8.0L;
                if (!(computed > 0.0L) ||
                    computed > static_cast<long double>(
                        std::numeric_limits<uint64_t>::max())) {
                    return false;
                }
                bdp_bytes = static_cast<uint64_t>(computed);
                if (bdp_bytes == 0) return false;
            }
            long double threshold_bytes =
                static_cast<long double>(m_guardConcurrencyMinBdps) *
                static_cast<long double>(bdp_bytes);
            if (static_cast<long double>(flow->m_guard_flow_size) >
                threshold_bytes) {
                elephants.push_back(index);
            }
        }
    }
    std::sort(elephants.begin(), elephants.end(),
              [inputs](size_t left_index, size_t right_index) {
                  const GuardVectorTargetInput &left = (*inputs)[left_index];
                  const GuardVectorTargetInput &right = (*inputs)[right_index];
                  if (left.remainingBytes != right.remainingBytes) {
                      return left.remainingBytes < right.remainingBytes;
                  }
                  const GuardQpIdentity &a = left.identity;
                  const GuardQpIdentity &b = right.identity;
                  return std::tie(a.sip, a.dip, a.sport, a.dport, a.pg) <
                         std::tie(b.sip, b.dip, b.sport, b.dport, b.pg);
              });
    uint32_t effective_concurrency = ComputeGuardEffectiveElephantConcurrency(
        m_guardReceiverConcurrency, elephants.size(),
        m_guardAdaptiveElephantConcurrency);
    if (m_guardSizeClassElephantConcurrency) {
        uint64_t first = elephants.size() > 0
            ? (*inputs)[elephants[0]].remainingBytes : 0;
        uint64_t second = elephants.size() > 1
            ? (*inputs)[elephants[1]].remainingBytes : 0;
        effective_concurrency = ComputeGuardSizeClassElephantConcurrency(
            m_guardReceiverConcurrency, elephants.size(), first, second, true);
    }
    std::vector<bool> is_elephant(inputs->size(), false);
    for (size_t index : elephants) is_elephant[index] = true;
    for (size_t index = 0; index < inputs->size(); ++index) {
        RdmaRxQueuePair *flow = (*inputs)[index].flow;
        if (flow != NULL && (!is_elephant[index] || m_guardElephantAgingRtts == 0.0)) {
            flow->m_guard_elephant_deferred_since_ns = -1;
        }
    }
    if (m_guardAdaptiveElephantConcurrency && effective_concurrency > 1) {
        m_guardAdaptiveConcurrencyPromotions++;
        m_guardAdaptiveConcurrencyMaxEffective = std::max<uint64_t>(
            m_guardAdaptiveConcurrencyMaxEffective, effective_concurrency);
    }
    if (m_guardSizeClassElephantConcurrency && effective_concurrency > 1) {
        m_guardSizeClassConcurrencyPromotions++;
        m_guardSizeClassConcurrencyMaxEffective = std::max<uint64_t>(
            m_guardSizeClassConcurrencyMaxEffective, effective_concurrency);
    }
    if (elephants.size() > effective_concurrency && effective_concurrency > 0) {
        if (m_guardElephantAgingRtts > 0.0 && effective_concurrency == 1) {
            int64_t now_ns = Simulator::Now().GetNanoSeconds();
            if (now_ns < 0) return false;
            size_t aged_rank = elephants.size();
            int64_t oldest_since_ns = std::numeric_limits<int64_t>::max();
            for (size_t rank = 1; rank < elephants.size(); ++rank) {
                RdmaRxQueuePair *flow = (*inputs)[elephants[rank]].flow;
                if (flow->m_guard_elephant_deferred_since_ns < 0) {
                    flow->m_guard_elephant_deferred_since_ns = now_ns;
                }
                long double rtt_ns = flow->m_base_rtt_sec > 0.0
                    ? static_cast<long double>(flow->m_base_rtt_sec) * 1.0e9L
                    : 8320.0L;
                long double threshold = rtt_ns * m_guardElephantAgingRtts;
                if (!(threshold > 0.0L) ||
                    threshold > static_cast<long double>(
                        std::numeric_limits<int64_t>::max())) {
                    return false;
                }
                if (flow->m_guard_elephant_deferred_since_ns > now_ns) return false;
                int64_t waited_ns = now_ns - flow->m_guard_elephant_deferred_since_ns;
                if (waited_ns >= static_cast<int64_t>(std::ceil(threshold)) &&
                    flow->m_guard_elephant_deferred_since_ns < oldest_since_ns) {
                    aged_rank = rank;
                    oldest_since_ns = flow->m_guard_elephant_deferred_since_ns;
                }
            }
            if (aged_rank < elephants.size()) {
                int64_t waited_ns = now_ns - oldest_since_ns;
                std::swap(elephants[0], elephants[aged_rank]);
                m_guardElephantAgingRotations++;
                m_guardElephantAgingMaxWaitNs = std::max<uint64_t>(
                    m_guardElephantAgingMaxWaitNs,
                    static_cast<uint64_t>(waited_ns));
            }
            for (size_t rank = 0; rank < elephants.size(); ++rank) {
                RdmaRxQueuePair *flow = (*inputs)[elephants[rank]].flow;
                if (rank < effective_concurrency) {
                    flow->m_guard_elephant_deferred_since_ns = -1;
                } else if (flow->m_guard_elephant_deferred_since_ns < 0) {
                    flow->m_guard_elephant_deferred_since_ns = now_ns;
                }
            }
        }
        for (size_t index : elephants) receives_residual[index] = false;
        for (size_t rank = 0; rank < effective_concurrency; ++rank) {
            receives_residual[elephants[rank]] = true;
        }
        m_guardConcurrencyLimitedAllocations++;
        m_guardConcurrencyMaxDeferredFlows = std::max<uint64_t>(
            m_guardConcurrencyMaxDeferredFlows,
            elephants.size() - effective_concurrency);
    } else {
        for (size_t index : elephants)
            (*inputs)[index].flow->m_guard_elephant_deferred_since_ns = -1;
    }

    std::vector<long double> weights;
    weights.assign(inputs->size(), 0.0L);
    long double weight_sum = 0.0L;
    uint64_t minimum_remaining = std::numeric_limits<uint64_t>::max();
    for (size_t index = 0; index < inputs->size(); ++index) {
        if (!receives_residual[index]) continue;
        const GuardVectorTargetInput &input = (*inputs)[index];
        minimum_remaining = std::min(
            minimum_remaining, std::max<uint64_t>(input.remainingBytes, m_mtu));
    }
    if (minimum_remaining == std::numeric_limits<uint64_t>::max()) return false;
    for (size_t index = 0; index < inputs->size(); ++index) {
        if (!receives_residual[index]) continue;
        const GuardVectorTargetInput &input = (*inputs)[index];
        uint64_t remaining = std::max<uint64_t>(input.remainingBytes, m_mtu);
        long double weight = std::pow(
            static_cast<long double>(minimum_remaining) / remaining,
            m_guardRemainingExponent);
        weights[index] = weight;
        weight_sum += weight;
    }
    if (!(weight_sum > 0.0L)) return false;
    for (size_t index = 0; index < inputs->size(); ++index) {
        (*inputs)[index].requestedBps = floor_share;
        if (receives_residual[index]) {
            (*inputs)[index].requestedBps +=
                static_cast<uint64_t>(static_cast<long double>(residual) *
                                      weights[index] / weight_sum);
        }
    }
    if (m_guardElephantCapSpillover && effective_concurrency == 1) {
        m_guardElephantSpilloverAllocatorChecks++;
        if (elephants.size() < 2) {
            m_guardElephantSpilloverNoPairChecks++;
            return true;
        }
        size_t donor_index = elephants[0];
        size_t recipient_index = elephants[1];
        RdmaRxQueuePair *donor = (*inputs)[donor_index].flow;
        if (donor == NULL) return false;
        Time freshness = donor->m_base_rtt_sec > 0
            ? Seconds(4.0 * donor->m_base_rtt_sec) : NanoSeconds(1);
        bool fresh = !donor->m_guard_spillover_last_report_time.IsZero() &&
            Simulator::Now() - donor->m_guard_spillover_last_report_time <=
                freshness;
        bool eligible = fresh && donor->m_guard_spillover_active &&
            donor->m_guard_spillover_reported_cap_bps > 0;
        if (!eligible) {
            m_guardElephantSpilloverDonorInactiveChecks++;
            if (donor->m_guard_spillover_active && !fresh) {
                m_guardElephantSpilloverDonorStaleChecks++;
            }
            for (size_t rank = 1; rank < elephants.size(); ++rank) {
                RdmaRxQueuePair *other = (*inputs)[elephants[rank]].flow;
                if (other == NULL) return false;
                Time other_freshness = other->m_base_rtt_sec > 0
                    ? Seconds(4.0 * other->m_base_rtt_sec) : NanoSeconds(1);
                bool other_fresh =
                    !other->m_guard_spillover_last_report_time.IsZero() &&
                    Simulator::Now() - other->m_guard_spillover_last_report_time <=
                        other_freshness;
                if (other_fresh && other->m_guard_spillover_active &&
                    other->m_guard_spillover_reported_cap_bps > 0) {
                    m_guardElephantSpilloverEligibleNonDonorChecks++;
                    break;
                }
            }
        }
        if (eligible) {
            __uint128_t headroom_numerator =
                static_cast<__uint128_t>(
                    donor->m_guard_spillover_reported_cap_bps) * 11U + 9U;
            uint64_t conservative_cap_bps = static_cast<uint64_t>(
                headroom_numerator / 10U);
            uint64_t donor_target_bps = (*inputs)[donor_index].requestedBps;
            uint64_t donor_new_bps = std::max(floor_share,
                                              conservative_cap_bps);
            if (donor_new_bps < donor_target_bps) {
                uint64_t spillover_bps = donor_target_bps - donor_new_bps;
                NS_ABORT_MSG_IF(
                    (*inputs)[recipient_index].requestedBps >
                        std::numeric_limits<uint64_t>::max() - spillover_bps,
                    "GUARD elephant spillover target overflow");
                (*inputs)[donor_index].requestedBps = donor_new_bps;
                (*inputs)[recipient_index].requestedBps += spillover_bps;
                m_guardElephantSpilloverVectors++;
                m_guardElephantSpilloverMaxBps = std::max(
                    m_guardElephantSpilloverMaxBps, spillover_bps);
            } else {
                m_guardElephantSpilloverCapAtOrAboveTargetChecks++;
            }
        }
    }
    return true;
}

bool RdmaHw::IsGuardLedgerActionCompatible(uint8_t action,
                                           uint8_t phase_tag) {
    bool prepare = phase_tag == GUARD_GRANT_PHASE_FAST_PREPARE ||
                   phase_tag == GUARD_GRANT_PHASE_TRANSITION_PREPARE;
    bool activate = phase_tag == GUARD_GRANT_PHASE_FAST_ACTIVATE ||
                    phase_tag == GUARD_GRANT_PHASE_TRANSITION_ACTIVATE;
    if (action == GUARD_VECTOR_PREPARE_DECREASE ||
        action == GUARD_VECTOR_RELEASE_DECREASE) {
        return prepare;
    }
    if (action == GUARD_VECTOR_ACTIVATE_WAITER ||
        action == GUARD_VECTOR_ACTIVATE_INCREASE ||
        action == GUARD_VECTOR_RELEASE_INCREASE) {
        return activate;
    }
    return false;
}

bool RdmaHw::DoesGuardRetiredAckMatch(
    const GuardRetiredAckRecord &record,
    const GuardQpIdentity &wire_identity, uint32_t generation,
    uint8_t phase_tag) {
    return record.identity == wire_identity &&
           record.generation == generation &&
           record.phaseTag == phase_tag &&
           IsGuardLedgerActionCompatible(record.action, phase_tag);
}

size_t RdmaHw::FindGuardRetiredAckRecord(
    const GuardQpIdentity &identity, uint32_t generation) const {
    for (size_t index = 0; index < m_guardRetiredAckRecords.size(); ++index) {
        const GuardRetiredAckRecord &record = m_guardRetiredAckRecords[index];
        if (record.identity == identity && record.generation == generation) {
            return index;
        }
    }
    return m_guardRetiredAckRecords.size();
}

void RdmaHw::RetireGuardRequiredAck(
    Ptr<RdmaRxQueuePair> qp,
    const GuardGenerationLedgerEntry &ledger) {
    static const size_t kGuardRetiredAckRecordCap = 4096;
    NS_ABORT_MSG_IF(qp == NULL,
                    "GUARD V14 cannot retire an ACK without its receiver QP");
    NS_ABORT_MSG_IF(
        FindGuardRetiredAckRecord(ledger.identity, ledger.generation) !=
            m_guardRetiredAckRecords.size(),
        "GUARD V14 duplicate retired ACK identity and generation");
    if (m_guardRetiredAckRecords.size() >= kGuardRetiredAckRecordCap) {
        m_guardRetiredAckOverflow++;
        NS_ABORT_MSG("GUARD V14 retired ACK ledger exhausted");
    }
    GuardRetiredAckRecord record = {
        ledger.identity, ledger.generation, ledger.phaseTag, ledger.action,
        ledger.targetMbps, qp->m_flow_id, m_guardFastpathTransaction,
        static_cast<uint64_t>(Simulator::Now().GetNanoSeconds())};
    m_guardRetiredAckRecords.emplace_back(record);
    m_guardRetiredAckClosures++;
    m_guardRetiredAckPeak = std::max<uint64_t>(
        m_guardRetiredAckPeak, m_guardRetiredAckRecords.size());
}

bool RdmaHw::GuardFastpathTransactionFloorFits(
    uint64_t *live_waiters) {
    const std::unordered_set<RdmaRxQueuePair*> &candidate_waiters =
        m_guardFastpathCollectionReady ? m_guardFastpathReadyWaiters
                                       : m_guardFastpathWaiters;
    std::unordered_set<RdmaRxQueuePair*> cohort;
    for (auto *flow : m_guardFastpathIncumbents) {
        if (m_rate_flow_ctl_set.find(flow) != m_rate_flow_ctl_set.end())
            cohort.emplace(flow);
    }
    uint64_t waiter_count = 0;
    for (auto *flow : candidate_waiters) {
        if (m_rate_flow_ctl_set.find(flow) != m_rate_flow_ctl_set.end() &&
            cohort.emplace(flow).second) {
            waiter_count++;
        }
    }
    if (live_waiters != NULL) *live_waiters = waiter_count;
    if (cohort.empty()) return true;
    RdmaRxQueuePair *sample = *cohort.begin();
    uint32_t receiver_nic = GetNicIdxOfRxQp(sample);
    uint64_t capacity_bps =
        m_nic[receiver_nic].dev->GetDataRate().GetBitRate();
    uint64_t draining_bps = GetGuardDrainingReservedBps(receiver_nic);
    NS_ABORT_MSG_IF(draining_bps > capacity_bps,
                    "GUARD V18 draining reservation exceeds receiver capacity");
    for (auto *flow : cohort) {
        NS_ABORT_MSG_IF(GetNicIdxOfRxQp(flow) != receiver_nic,
                        "GUARD V18 floor admission spans receiver NICs");
    }
    uint64_t minimum_mbps = m_minRate.GetBitRate() / 1000000ULL +
        (m_minRate.GetBitRate() % 1000000ULL != 0 ? 1 : 0);
    return CanGuardAllocationFloorFit(
        capacity_bps, draining_bps, cohort.size(),
        minimum_mbps * 1000000ULL);
}

void RdmaHw::StartGuardFastpathTransaction() {
    NS_ABORT_MSG_IF(m_guardFastpathPhase != GUARD_FASTPATH_IDLE ||
                        m_guardPendingGrantAcks != 0,
                    "GUARD fast-path transaction crossed an ACK barrier");
    if (m_guardCapacityAdmissionDeferral) {
        uint64_t live_waiters = 0;
        if (!GuardFastpathTransactionFloorFits(&live_waiters)) {
            NS_ABORT_MSG_IF(m_guardDrainingRecords.empty(),
                            "GUARD V18 floor shortage lacks a draining release");
            if (!m_guardCapacityAdmissionBlocked) {
                m_guardCapacityAdmissionBlocked = true;
                m_guardCapacityAdmissionDeferrals++;
            }
            m_guardCapacityAdmissionMaxWaiters = std::max(
                m_guardCapacityAdmissionMaxWaiters, live_waiters);
            return;
        }
        if (m_guardCapacityAdmissionBlocked) {
            m_guardCapacityAdmissionBlocked = false;
            m_guardCapacityAdmissionResumes++;
        }
    }
    m_guardFastpathTransactionWaiters.clear();
    m_guardFastpathTransactionIsTransition =
        m_guardFastpathCollectionReady && m_guardFastpathHighFanIn &&
        !m_guardFastpathHighInitialCommitted;
    uint64_t snapshot_revision = m_guardFastpathMembershipRevision;
    if (m_guardFastpathCollectionReady) {
        snapshot_revision = m_guardFastpathReadyMembershipRevision;
        m_guardFastpathTransactionWaiters.swap(m_guardFastpathReadyWaiters);
        m_guardFastpathCollectionReady = false;
    } else {
        m_guardFastpathTransactionWaiters = m_guardFastpathWaiters;
    }
    ConsumeGuardFastpathMembershipThrough(snapshot_revision);
    if (m_guardFastpathTransactionWaiters.empty()) {
        SendGuardFastpathOptionalRelease();
        return;
    }
    for (auto it = m_guardFastpathTransactionWaiters.begin();
         it != m_guardFastpathTransactionWaiters.end();) {
        if (m_rate_flow_ctl_set.find(*it) == m_rate_flow_ctl_set.end()) {
            it = m_guardFastpathTransactionWaiters.erase(it);
        } else {
            ++it;
        }
    }
    if (m_guardFastpathTransactionWaiters.empty()) {
        FinishGuardFastpathTransaction();
        return;
    }
    if (m_guardTransitionAuditSink != NULL &&
        m_guardTransitionAuditSink->file != NULL) {
        NS_ABORT_MSG_IF(
            m_guardFastpathTransaction == std::numeric_limits<uint64_t>::max(),
            "GUARD V14 transition audit transaction exhausted");
    }
    m_guardFastpathTransaction++;
    m_guardFastpathTransactions++;
    m_guardFastpathTransactionTargetN =
        m_guardFastpathIncumbents.size() +
        m_guardFastpathTransactionWaiters.size();
    NS_ABORT_MSG_IF(m_guardFastpathTransactionTargetN == 0,
                    "GUARD fast-path transaction has no target cohort");
    RdmaRxQueuePair *sample = !m_guardFastpathIncumbents.empty()
                                  ? *m_guardFastpathIncumbents.begin()
                                  : *m_guardFastpathTransactionWaiters.begin();
    uint64_t line_rate_bps =
        m_nic[GetNicIdxOfRxQp(sample)].dev->GetDataRate().GetBitRate();
    m_guardFastpathTransactionTargetMbps = std::max<uint32_t>(
        1, line_rate_bps / m_guardFastpathTransactionTargetN / 1000000);
    if (m_guardMixedPgVectorFastpath) {
        FreezeGuardFastpathTargetVector(snapshot_revision);
    }
    if (m_guardFastpathTransactionIsTransition) {
        m_guardFastpathHighTransitionRegisteredN =
            m_guardFastpathTransactionTargetN;
        m_guardFastpathHighTransitionWaiters =
            m_guardFastpathTransactionWaiters.size();
        if (m_guardTransitionPrefixBarrierEnabled) {
            NS_ABORT_MSG_IF(
                m_guardTransitionPrefixDeadlineEvent.IsRunning() ||
                    m_guardTransitionPrefixBarrierWaiting ||
                    m_guardTransitionFallbackActive,
                "GUARD V12 transition inherited live barrier state");
            m_guardTransitionPrefixBarrierResolved = false;
            m_guardTransitionPrefixTimedOut = false;
            m_guardTransitionActivationOrder.clear();
            m_guardTransitionActivationHolds.clear();
            m_guardTransitionPrefixTargets.clear();
            m_guardTransitionCurrentBatch.clear();
            m_guardTransitionActivationCursor = 0;
            m_guardTransitionActivationBatchIndex = 0;
            m_guardTransitionActivationBatchSize = 0;
            m_guardTransitionLastRegisterNs = -1;
            m_guardTransitionLastFlowId = -1;
            NS_ABORT_MSG_IF(!ValidateGuardTransitionQueueCohort(),
                            "GUARD V12 transition has no live queue cohort");
        }
    }
    StartGuardFastpathPrepare();
}

bool RdmaHw::ValidateGuardTransitionQueueCohort() {
    NS_ABORT_MSG_IF(!m_guardTransitionPrefixBarrierEnabled ||
                        !m_guardFastpathTransactionIsTransition,
                    "GUARD V12 queue validation ran outside a transition");
    bool found = false;
    uint32_t expected_nic = 0;
    uint16_t expected_pg = 0;
    uint64_t expected_capacity_bps = 0;
    auto validate = [&](RdmaRxQueuePair *flow) {
        if (m_rate_flow_ctl_set.find(flow) == m_rate_flow_ctl_set.end()) return;
        uint32_t nic = GetNicIdxOfRxQp(flow);
        uint64_t capacity_bps = m_nic[nic].dev->GetDataRate().GetBitRate();
        NS_ABORT_MSG_IF(capacity_bps == 0,
                        "GUARD V12 transition found zero receiver capacity");
        if (!found) {
            found = true;
            expected_nic = nic;
            expected_pg = flow->m_guard_pg;
            expected_capacity_bps = capacity_bps;
            return;
        }
        bool priority_group_mismatch =
            !m_guardMixedPgVectorFastpath && flow->m_guard_pg != expected_pg;
        if (nic != expected_nic || priority_group_mismatch ||
            capacity_bps != expected_capacity_bps) {
            m_guardTransitionPrefixBarrierViolations++;
            m_guardFastpathBarrierViolations++;
            NS_ABORT_MSG("GUARD transition spans receiver capacities or a disallowed priority group");
        }
    };
    for (auto *flow : m_guardFastpathIncumbents) validate(flow);
    for (auto *flow : m_guardFastpathTransactionWaiters) validate(flow);
    return found;
}

void RdmaHw::StartGuardFastpathPrepare() {
    std::unordered_set<RdmaRxQueuePair*> recipients;
    for (auto *flow : m_guardFastpathIncumbents) {
        NS_ABORT_MSG_IF(flow->m_guard_grant_upper_bound_bps == 0,
                        "GUARD fast-path incumbent lacks an acknowledged cap");
        uint64_t target_bps =
            static_cast<uint64_t>(m_guardFastpathTransactionTargetMbps) *
            1000000ULL;
        if (m_guardMixedPgVectorFastpath) {
            const GuardFrozenTargetRecord *target =
                GetGuardFrozenTarget(flow);
            NS_ABORT_MSG_IF(target == NULL ||
                                target->role != GUARD_VECTOR_INCUMBENT,
                            "GUARD V14 prepare lost an incumbent target");
            target_bps = static_cast<uint64_t>(target->targetMbps) *
                         1000000ULL;
        }
        bool requires_decrease = m_guardMixedPgVectorFastpath
                                     ? GetGuardPotentialUpperBoundBps(flow) > target_bps
                                     : flow->m_guard_grant_upper_bound_bps > target_bps;
        if (requires_decrease) {
            recipients.emplace(flow);
        }
    }
    bool transition = m_guardFastpathTransactionIsTransition;
    if (transition) {
        if (m_guardFastpathEpochPrefixCloseNs == 0) {
            m_guardFastpathEpochPrefixCloseNs =
                m_guardFastpathLastTransactionCloseNs;
        }
        if (m_guardFastpathEpochTransitionPrepareStartNs == 0) {
            m_guardFastpathEpochTransitionPrepareStartNs = static_cast<uint64_t>(
                Simulator::Now().GetNanoSeconds());
        }
        if (m_guardFastpathPrefixCloseNs == 0) {
            m_guardFastpathPrefixCloseNs = m_guardFastpathEpochPrefixCloseNs;
        }
        if (m_guardFastpathCollectionFlushNs == 0) {
            m_guardFastpathCollectionFlushNs =
                m_guardFastpathEpochCollectionFlushNs;
        }
        if (m_guardFastpathTransitionPrepareStartNs == 0) {
            m_guardFastpathTransitionPrepareStartNs =
                m_guardFastpathEpochTransitionPrepareStartNs;
        }
        if (m_guardFastpathEpochCollectionFlushNs == 0 ||
            m_guardFastpathEpochPrefixCloseNs == 0 ||
            m_guardFastpathEpochTransitionPrepareStartNs <
                m_guardFastpathEpochPrefixCloseNs ||
            m_guardFastpathEpochTransitionPrepareStartNs <
                m_guardFastpathEpochCollectionFlushNs) {
            m_guardFastpathBarrierViolations++;
            NS_ABORT_MSG("GUARD transition prepare crossed collection/prefix barrier");
        }
    }
    if (recipients.empty()) {
        StartGuardFastpathActivate();
        return;
    }
    if (m_guardMixedPgVectorFastpath)
        m_guardVectorPrepareDecreases += recipients.size();
    m_guardFastpathPhase = GUARD_FASTPATH_PREPARE;
    if (transition) {
        m_guardTransitionPrepareBatches++;
        m_guardTransitionPrepareGrants += recipients.size();
    } else {
        m_guardFastpathPrepareBatches++;
        m_guardFastpathPrepareGrants += recipients.size();
    }
    SendGuardFastpathRequiredGeneration(
        recipients, transition ? "transition_prepare" : "fast_prepare");
}

void RdmaHw::StartGuardFastpathActivate() {
    bool transition = m_guardFastpathTransactionIsTransition;
    if (transition && m_guardTransitionPrefixBarrierEnabled &&
        !m_guardTransitionPrefixBarrierResolved) {
        StartGuardTransitionPrefixBarrier();
        return;
    }
    if (transition && m_guardTransitionPrefixBarrierEnabled &&
        m_guardTransitionFallbackActive) {
        SendNextGuardTransitionFallbackBatch();
        return;
    }
    if (transition && m_guardTransitionPrefixBarrierEnabled) {
        std::vector<RdmaRxQueuePair*> recipients;
        if (m_guardMixedPgVectorFastpath) {
            for (const auto &target : m_guardFrozenTargetVector) {
                RdmaRxQueuePair *flow = target.flow;
                if (m_rate_flow_ctl_set.find(flow) ==
                    m_rate_flow_ctl_set.end()) {
                    continue;
                }
                uint64_t target_bps =
                    static_cast<uint64_t>(target.targetMbps) * 1000000ULL;
                if (target.role == GUARD_VECTOR_WAITER ||
                    GetGuardPotentialUpperBoundBps(flow) < target_bps) {
                    recipients.push_back(flow);
                }
            }
        } else {
            for (auto *flow : m_guardTransitionActivationOrder) {
                if (m_rate_flow_ctl_set.find(flow) != m_rate_flow_ctl_set.end() &&
                    m_guardFastpathTransactionWaiters.find(flow) !=
                        m_guardFastpathTransactionWaiters.end()) {
                    recipients.push_back(flow);
                }
            }
        }
        if (recipients.empty()) {
            FinishGuardFastpathTransaction();
            return;
        }
        m_guardFastpathPhase = GUARD_FASTPATH_ACTIVATE;
        m_guardTransitionCurrentBatch.clear();
        m_guardTransitionCurrentBatch.insert(recipients.begin(),
                                             recipients.end());
        m_guardTransitionActivationBatchIndex = 1;
        m_guardTransitionActivationBatchSize = recipients.size();
        m_guardTransitionActivateBatches++;
        m_guardTransitionActivateGrants += recipients.size();
        SendGuardFastpathRequiredGenerationOrdered(
            recipients, "transition_activate");
        return;
    }
    std::unordered_set<RdmaRxQueuePair*> recipients;
    for (auto *flow : m_guardFastpathTransactionWaiters) {
        if (m_rate_flow_ctl_set.find(flow) != m_rate_flow_ctl_set.end()) {
            recipients.emplace(flow);
        }
    }
    if (m_guardMixedPgVectorFastpath) {
        for (auto *flow : m_guardFastpathIncumbents) {
            if (m_rate_flow_ctl_set.find(flow) == m_rate_flow_ctl_set.end())
                continue;
            const GuardFrozenTargetRecord *target = GetGuardFrozenTarget(flow);
            NS_ABORT_MSG_IF(target == NULL,
                            "GUARD V14 activation lost an incumbent target");
            uint64_t target_bps =
                static_cast<uint64_t>(target->targetMbps) * 1000000ULL;
            if (GetGuardPotentialUpperBoundBps(flow) < target_bps)
                recipients.emplace(flow);
        }
    }
    if (recipients.empty()) {
        FinishGuardFastpathTransaction();
        return;
    }
    m_guardFastpathPhase = GUARD_FASTPATH_ACTIVATE;
    if (transition) {
        m_guardTransitionActivateBatches++;
        m_guardTransitionActivateGrants += recipients.size();
    } else {
        m_guardFastpathActivateBatches++;
        m_guardFastpathActivateGrants += recipients.size();
        if (m_guardFastpathHighInitialCommitted) {
            m_guardFastpathPostTransitionFastGrants += recipients.size();
        }
    }
    SendGuardFastpathRequiredGeneration(
        recipients, transition ? "transition_activate" : "fast_activate");
}

bool RdmaHw::ComputeGuardTransitionPrefixWireBudget(
    const std::vector<GuardTransitionPrefixWaiterBudgetInput> &waiters,
    const std::vector<GuardTransitionPrefixIncumbentBudgetInput> &incumbents,
    const std::vector<GuardTransitionPrefixDrainingBudgetInput> &draining,
    uint32_t mtu, uint32_t header_bytes_per_packet,
    uint64_t receiver_capacity_bps,
    GuardTransitionPrefixWireBudget *budget) {
    if (budget == NULL || waiters.empty() || mtu == 0 ||
        header_bytes_per_packet == 0 || receiver_capacity_bps == 0 ||
        incumbents.size() > std::numeric_limits<uint64_t>::max() ||
        draining.size() > std::numeric_limits<uint64_t>::max()) {
        return false;
    }
    const __uint128_t wide_max = ~static_cast<__uint128_t>(0);
    const __uint128_t u64_max =
        static_cast<__uint128_t>(std::numeric_limits<uint64_t>::max());
    __uint128_t rounded_payload_budget_bytes = 0;
    __uint128_t packet_count = 0;
    __uint128_t wire_bytes = 0;
    uint64_t max_rtt_ns = 0;
    for (const auto &waiter : waiters) {
        if (waiter.flowSizeBytes == 0 || waiter.exactGateBytes == 0 ||
            waiter.baseRttNs == 0) {
            return false;
        }
        uint64_t target = std::min(
            waiter.flowSizeBytes, waiter.exactGateBytes);
        uint64_t packets = target / mtu;
        if (target % mtu != 0) {
            if (packets == std::numeric_limits<uint64_t>::max()) return false;
            packets++;
        }
        if (packets == 0 ||
            static_cast<__uint128_t>(packets) > wide_max / mtu) {
            return false;
        }
        __uint128_t rounded_payload =
            static_cast<__uint128_t>(packets) * mtu;
        __uint128_t payload_sent_upper = std::min(
            static_cast<__uint128_t>(waiter.flowSizeBytes), rounded_payload);
        if (static_cast<__uint128_t>(packets) >
            wide_max / header_bytes_per_packet) {
            return false;
        }
        __uint128_t headers =
            static_cast<__uint128_t>(packets) * header_bytes_per_packet;
        if (payload_sent_upper > wide_max - headers) return false;
        __uint128_t flow_wire_bytes = payload_sent_upper + headers;
        if (rounded_payload_budget_bytes > wide_max - payload_sent_upper ||
            packet_count > wide_max - packets ||
            wire_bytes > wide_max - flow_wire_bytes) {
            return false;
        }
        rounded_payload_budget_bytes += payload_sent_upper;
        packet_count += packets;
        wire_bytes += flow_wire_bytes;
        max_rtt_ns = std::max(max_rtt_ns, waiter.baseRttNs);
    }

    __uint128_t occupancy_bps = 0;
    for (const auto &incumbent : incumbents) {
        if (incumbent.acknowledgedUpperBoundBps == 0 ||
            incumbent.baseRttNs == 0 ||
            occupancy_bps > wide_max - incumbent.acknowledgedUpperBoundBps) {
            return false;
        }
        occupancy_bps += incumbent.acknowledgedUpperBoundBps;
        max_rtt_ns = std::max(max_rtt_ns, incumbent.baseRttNs);
    }
    __uint128_t draining_bps = 0;
    for (const auto &record : draining) {
        if (record.reservedUpperBoundBps == 0 || record.baseRttNs == 0 ||
            occupancy_bps > wide_max - record.reservedUpperBoundBps ||
            draining_bps > wide_max - record.reservedUpperBoundBps) {
            return false;
        }
        occupancy_bps += record.reservedUpperBoundBps;
        draining_bps += record.reservedUpperBoundBps;
        max_rtt_ns = std::max(max_rtt_ns, record.baseRttNs);
    }
    if ((!incumbents.empty() && occupancy_bps == 0) ||
        occupancy_bps >= receiver_capacity_bps ||
        rounded_payload_budget_bytes > u64_max || packet_count > u64_max ||
        wire_bytes > u64_max || occupancy_bps > u64_max ||
        draining_bps > u64_max) {
        return false;
    }
    uint64_t occupancy = static_cast<uint64_t>(occupancy_bps);
    uint64_t residual_bps = receiver_capacity_bps - occupancy;
    if (residual_bps == 0 || wire_bytes > wide_max / 8000000000ULL) {
        return false;
    }
    __uint128_t serialization_numerator = wire_bytes * 8000000000ULL;
    __uint128_t serialization_ns_wide =
        serialization_numerator / residual_bps;
    if (serialization_numerator % residual_bps != 0) {
        if (serialization_ns_wide == wide_max) return false;
        serialization_ns_wide++;
    }
    if (serialization_ns_wide > u64_max ||
        max_rtt_ns > std::numeric_limits<uint64_t>::max() -
                         static_cast<uint64_t>(serialization_ns_wide)) {
        return false;
    }
    uint64_t delay_ns = max_rtt_ns +
                        static_cast<uint64_t>(serialization_ns_wide);
    if (delay_ns == 0 ||
        delay_ns > static_cast<uint64_t>(std::numeric_limits<int64_t>::max())) {
        return false;
    }

    budget->roundedPayloadBudgetBytes =
        static_cast<uint64_t>(rounded_payload_budget_bytes);
    budget->packetCount = static_cast<uint64_t>(packet_count);
    budget->headerBytesPerPacket = header_bytes_per_packet;
    budget->wireBytes = static_cast<uint64_t>(wire_bytes);
    budget->incumbentCount = static_cast<uint64_t>(incumbents.size());
    budget->drainingCount = static_cast<uint64_t>(draining.size());
    budget->drainingReservedBps = static_cast<uint64_t>(draining_bps);
    budget->occupancyBps = occupancy;
    budget->receiverCapacityBps = receiver_capacity_bps;
    budget->residualBps = residual_bps;
    budget->serializationNs = static_cast<uint64_t>(serialization_ns_wide);
    budget->maxRttNs = max_rtt_ns;
    budget->delayNs = delay_ns;
    return true;
}

bool RdmaHw::IsGuardTransitionPrefixWatchdogAggregateInconsistent(
    uint64_t budget_records, uint64_t contributing_hardware,
    bool non_reconstructable) {
    return non_reconstructable || budget_records > 1 ||
           contributing_hardware > 1;
}

bool RdmaHw::EncodeGuardTargetVector(
    const std::vector<GuardVectorTargetInput> &inputs,
    uint32_t receiver_nic, uint64_t receiver_capacity_bps,
    uint64_t draining_reserved_bps, uint64_t allocatable_capacity_bps,
    uint64_t minimum_wire_rate_bps, uint64_t membership_revision,
    uint64_t allocation_revision, uint64_t progress_revision,
    uint64_t reason_mask,
    std::vector<GuardFrozenTargetRecord> *records,
    uint64_t *vector_hash) {
    if (records == NULL || vector_hash == NULL || inputs.empty() ||
        receiver_capacity_bps < 1000000ULL ||
        draining_reserved_bps > receiver_capacity_bps ||
        allocatable_capacity_bps !=
            receiver_capacity_bps - draining_reserved_bps ||
        minimum_wire_rate_bps == 0 ||
        minimum_wire_rate_bps > allocatable_capacity_bps || reason_mask == 0) {
        return false;
    }
    std::vector<GuardVectorTargetInput> canonical = inputs;
    std::sort(canonical.begin(), canonical.end(),
              [](const GuardVectorTargetInput &left,
                 const GuardVectorTargetInput &right) {
                  const GuardQpIdentity &a = left.identity;
                  const GuardQpIdentity &b = right.identity;
                  return std::tie(a.sip, a.dip, a.sport, a.dport, a.pg,
                                  left.role, left.remainingBytes) <
                         std::tie(b.sip, b.dip, b.sport, b.dport, b.pg,
                                  right.role, right.remainingBytes);
              });
    records->clear();
    records->reserve(canonical.size());
    __uint128_t encoded_sum = 0;
    GuardQpIdentity previous = {};
    bool have_previous = false;
    for (const auto &input : canonical) {
        if (input.role != GUARD_VECTOR_INCUMBENT &&
            input.role != GUARD_VECTOR_WAITER) {
            return false;
        }
        if (have_previous && input.identity == previous) return false;
        previous = input.identity;
        have_previous = true;
        uint64_t bounded = std::min(input.requestedBps,
                                    allocatable_capacity_bps);
        uint64_t mbps = bounded / 1000000ULL;
        uint64_t minimum_mbps =
            minimum_wire_rate_bps / 1000000ULL +
            (minimum_wire_rate_bps % 1000000ULL != 0 ? 1 : 0);
        mbps = std::max(mbps, minimum_mbps);
        if (mbps > std::numeric_limits<uint32_t>::max()) return false;
        encoded_sum += mbps * 1000000ULL;
        if (encoded_sum > allocatable_capacity_bps) return false;
        records->push_back({input.identity, input.role, input.remainingBytes,
                            input.frozenProgressSeq, allocation_revision,
                            progress_revision, static_cast<uint32_t>(mbps),
                            input.flow, false});
    }

    // Versioned, pointer-free hash.  Include the frozen receiver resource and
    // membership revision before each canonical full-identity record.
    std::vector<uint64_t> words;
    words.reserve(10 + records->size() * 11);
    words.push_back(14);
    words.push_back(receiver_nic);
    words.push_back(receiver_capacity_bps);
    words.push_back(draining_reserved_bps);
    words.push_back(allocatable_capacity_bps);
    words.push_back(membership_revision);
    words.push_back(allocation_revision);
    words.push_back(progress_revision);
    words.push_back(reason_mask);
    words.push_back(records->size());
    for (const auto &record : *records) {
        words.push_back(record.identity.sip);
        words.push_back(record.identity.dip);
        words.push_back(record.identity.sport);
        words.push_back(record.identity.dport);
        words.push_back(record.identity.pg);
        words.push_back(record.role);
        words.push_back(record.remainingBytes);
        words.push_back(record.frozenProgressSeq);
        words.push_back(record.allocationRevision);
        words.push_back(record.progressRevision);
        words.push_back(record.targetMbps);
    }
    *vector_hash = GuardAuditHashWords(words);
    return true;
}

void RdmaHw::BeginGuardTransitionAudit(
    uint32_t receiver_nic, uint16_t receiver_pg,
    uint64_t receiver_capacity_bps,
    const GuardTransitionPrefixWireBudget *watchdog_budget) {
    GuardTransitionAuditSink *sink = m_guardTransitionAuditSink;
    if (sink == NULL || sink->file == NULL) return;
    NS_ABORT_MSG_IF(m_guardTransitionAuditRecord.active,
                    "GUARD V14 transition audit overlapped records");
    NS_ABORT_MSG_IF(sink->records == std::numeric_limits<uint64_t>::max(),
                    "GUARD V14 transition audit record counter overflow");

    GuardTransitionAuditRecord record;
    record.active = true;
    record.receiverNode = m_node->GetId();
    record.receiverNic = receiver_nic;
    record.epoch = m_guardTransitionAuditEpoch;
    record.transaction = m_guardFastpathTransaction;
    record.receiverCapacityBps = receiver_capacity_bps;
    record.startNs = static_cast<uint64_t>(
        m_guardTransitionPrefixBarrierStart.GetNanoSeconds());
    record.deadlineNs = static_cast<uint64_t>(
        m_guardTransitionPrefixDeadline.GetNanoSeconds());
    record.deadlinePolicy = m_guardTransitionPrefixWireWatchdogEnabled
                                ? "wire_residual_watchdog"
                                : "legacy_remaining_payload";
    record.deadlineOutcome = "pending";
    record.terminalClosure = "pending";

    std::vector<uint16_t> priority_groups;
    std::vector<std::tuple<int32_t, uint64_t, uint64_t, uint64_t> > targets;
    priority_groups.reserve(m_guardFastpathIncumbents.size() +
                            m_guardTransitionActivationOrder.size());
    targets.reserve(m_guardFastpathIncumbents.size() +
                    m_guardTransitionActivationOrder.size());

    __uint128_t active_upper_bps = 0;
    for (auto *flow : m_guardFastpathIncumbents) {
        if (m_rate_flow_ctl_set.find(flow) == m_rate_flow_ctl_set.end()) continue;
        active_upper_bps += flow->m_guard_last_acked_upper_bound_bps;
        priority_groups.push_back(flow->m_guard_pg);
        targets.push_back(std::make_tuple(
            flow->m_flow_id, 0,
            static_cast<uint64_t>(m_guardFastpathTransactionTargetMbps) *
                1000000ULL,
            0));
    }
    __uint128_t reserved_draining_upper_bps = 0;
    for (const auto &item : m_guardDrainingRecords) {
        const GuardDrainingRecord &draining = item.second;
        if (draining.state != GUARD_DRAINING) continue;
        NS_ABORT_MSG_IF(draining.receiverNic != receiver_nic ||
                            draining.reservedBps == 0,
                        "GUARD V14 audit found an invalid draining reservation");
        active_upper_bps += draining.reservedBps;
        reserved_draining_upper_bps += draining.reservedBps;
        priority_groups.push_back(draining.priorityGroup);
    }
    __uint128_t draining_upper_bps = 0;
    __uint128_t draining_wire_bytes = 0;
    __uint128_t prefix_target_bytes = 0;
    uint32_t header_bytes = CustomHeader::GetStaticWholeHeaderSize();
    for (auto *flow : m_guardTransitionActivationOrder) {
        if (m_rate_flow_ctl_set.find(flow) == m_rate_flow_ctl_set.end()) continue;
        auto target_it = m_guardTransitionPrefixTargets.find(flow);
        NS_ABORT_MSG_IF(target_it == m_guardTransitionPrefixTargets.end(),
                        "GUARD V14 audit lost a frozen prefix target");
        uint64_t target = target_it->second;
        uint64_t packets = target / m_mtu + (target % m_mtu != 0 ? 1 : 0);
        __uint128_t rounded_payload = static_cast<__uint128_t>(packets) * m_mtu;
        __uint128_t payload_upper = std::min(
            static_cast<__uint128_t>(flow->m_guard_flow_size), rounded_payload);
        draining_wire_bytes += payload_upper +
                               static_cast<__uint128_t>(packets) * header_bytes;
        draining_upper_bps += receiver_capacity_bps;
        prefix_target_bytes += target;
        priority_groups.push_back(flow->m_guard_pg);
        targets.push_back(std::make_tuple(
            flow->m_flow_id, 1,
            static_cast<uint64_t>(m_guardFastpathTransactionTargetMbps) *
                1000000ULL,
            target));
    }
    const __uint128_t u64_max = static_cast<__uint128_t>(
        std::numeric_limits<uint64_t>::max());
    NS_ABORT_MSG_IF(active_upper_bps > u64_max ||
                        reserved_draining_upper_bps > u64_max ||
                        draining_upper_bps > u64_max ||
                        active_upper_bps + draining_upper_bps > u64_max ||
                        draining_wire_bytes > u64_max ||
                        prefix_target_bytes > u64_max,
                    "GUARD V14 transition audit aggregate overflow");
    record.activeUpperBoundBps = static_cast<uint64_t>(active_upper_bps);
    record.drainingUpperBoundBps = static_cast<uint64_t>(draining_upper_bps);
    record.activeDrainingUpperBoundBps = static_cast<uint64_t>(
        active_upper_bps + draining_upper_bps);
    record.drainingWireUpperBoundBytes = static_cast<uint64_t>(
        draining_wire_bytes);
    record.prefixTargetBytes = static_cast<uint64_t>(prefix_target_bytes);
    if (watchdog_budget != NULL) {
        NS_ABORT_MSG_IF(
            record.activeUpperBoundBps != watchdog_budget->occupancyBps ||
                static_cast<uint64_t>(reserved_draining_upper_bps) !=
                    watchdog_budget->drainingReservedBps ||
                record.drainingWireUpperBoundBytes != watchdog_budget->wireBytes,
            "GUARD V14 audit/watchdog source budget mismatch");
    }

    std::sort(priority_groups.begin(), priority_groups.end());
    priority_groups.erase(
        std::unique(priority_groups.begin(), priority_groups.end()),
        priority_groups.end());
    NS_ABORT_MSG_IF(priority_groups.empty() ||
                        (!m_guardMixedPgVectorFastpath &&
                         (priority_groups.size() != 1 ||
                          priority_groups.front() != receiver_pg)),
                    "GUARD V14 audit priority-group source mismatch");
    std::vector<uint64_t> pg_words;
    pg_words.reserve(priority_groups.size());
    for (uint16_t pg : priority_groups) pg_words.push_back(pg);
    record.priorityGroupSetHash = GuardAuditHashWords(pg_words);

    std::sort(targets.begin(), targets.end());
    std::vector<uint64_t> target_words;
    target_words.reserve(targets.size() * 4);
    for (const auto &entry : targets) {
        target_words.push_back(static_cast<uint32_t>(std::get<0>(entry)));
        target_words.push_back(std::get<1>(entry));
        target_words.push_back(std::get<2>(entry));
        target_words.push_back(std::get<3>(entry));
    }
    record.targetVectorEntries = targets.size();
    record.targetVectorHash = GuardAuditHashWords(target_words);
    if (m_guardMixedPgVectorFastpath) {
        NS_ABORT_MSG_IF(!m_guardFrozenVectorActive ||
                            m_guardFrozenReceiverNic != receiver_nic ||
                            m_guardFrozenReceiverCapacityBps !=
                                receiver_capacity_bps ||
                            m_guardFrozenVectorHash == 0,
                        "GUARD V14 audit lost its frozen vector source");
        record.targetVectorEntries = m_guardFrozenTargetVector.size();
        record.targetVectorHash = m_guardFrozenVectorHash;
    }
    m_guardTransitionAuditRecord = record;
    sink->records++;
}

void RdmaHw::RetireGuardTransitionAuditPrefix(RdmaRxQueuePair *flow) {
    if (!m_guardTransitionAuditRecord.active) return;
    auto target_it = m_guardTransitionPrefixTargets.find(flow);
    if (target_it == m_guardTransitionPrefixTargets.end()) return;
    uint64_t observed = std::min<uint64_t>(
        target_it->second, flow->ReceiverNextExpectedSeq);
    NS_ABORT_MSG_IF(
        m_guardTransitionAuditRecord.prefixRetiredObservedBytes >
            std::numeric_limits<uint64_t>::max() - observed,
        "GUARD V14 retired prefix evidence overflow");
    m_guardTransitionAuditRecord.prefixRetiredObservedBytes += observed;
}

void RdmaHw::ResolveGuardTransitionAudit(const char *outcome) {
    if (!m_guardTransitionAuditRecord.active) return;
    uint64_t observed = m_guardTransitionAuditRecord.prefixRetiredObservedBytes;
    for (auto *flow : m_guardTransitionActivationOrder) {
        if (m_rate_flow_ctl_set.find(flow) == m_rate_flow_ctl_set.end()) continue;
        auto target_it = m_guardTransitionPrefixTargets.find(flow);
        NS_ABORT_MSG_IF(target_it == m_guardTransitionPrefixTargets.end(),
                        "GUARD V14 audit resolution lost a prefix target");
        uint64_t flow_observed = std::min<uint64_t>(
            target_it->second, flow->ReceiverNextExpectedSeq);
        NS_ABORT_MSG_IF(observed > std::numeric_limits<uint64_t>::max() -
                                      flow_observed,
                        "GUARD V14 observed prefix evidence overflow");
        observed += flow_observed;
    }
    NS_ABORT_MSG_IF(observed > m_guardTransitionAuditRecord.prefixTargetBytes,
                    "GUARD V14 observed prefix exceeds frozen target");
    m_guardTransitionAuditRecord.prefixObservedBytes = observed;
    m_guardTransitionAuditRecord.deadlineOutcome = outcome;
}

void RdmaHw::FinalizeGuardTransitionAudit(const char *terminal_closure) {
    if (!m_guardTransitionAuditRecord.active) return;
    GuardTransitionAuditSink *sink = m_guardTransitionAuditSink;
    NS_ABORT_MSG_IF(sink == NULL || sink->file == NULL,
                    "GUARD V14 active transition audit lost its sink");
    NS_ABORT_MSG_IF(sink->attempted == std::numeric_limits<uint64_t>::max(),
                    "GUARD V14 transition audit attempted counter overflow");
    if (m_guardTransitionAuditRecord.deadlineOutcome == "pending") {
        ResolveGuardTransitionAudit("unresolved");
    }
    m_guardTransitionAuditRecord.terminalClosure = terminal_closure;
    sink->attempted++;
    if (sink->written < sink->max_records) {
        const GuardTransitionAuditRecord &record = m_guardTransitionAuditRecord;
        fprintf(
            sink->file,
            "%u,%u,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%s,%s,%s\n",
            record.receiverNode, record.receiverNic, record.epoch,
            record.transaction, record.priorityGroupSetHash,
            record.targetVectorHash, record.targetVectorEntries,
            record.receiverCapacityBps, record.activeUpperBoundBps,
            record.drainingUpperBoundBps,
            record.activeDrainingUpperBoundBps,
            record.drainingWireUpperBoundBytes, record.prefixTargetBytes,
            record.prefixObservedBytes, record.startNs, record.deadlineNs,
            record.deadlinePolicy.c_str(), record.deadlineOutcome.c_str(),
            record.terminalClosure.c_str());
        sink->written++;
        fflush(sink->file);
    }
    m_guardTransitionAuditRecord = GuardTransitionAuditRecord();
}

void RdmaHw::FlushGuardTransitionAudit() {
    if (!m_guardTransitionAuditRecord.active) return;
    if (m_guardTransitionAuditRecord.deadlineOutcome == "pending") {
        ResolveGuardTransitionAudit("simulation_stop_unresolved");
    }
    FinalizeGuardTransitionAudit("simulation_stop");
}

void RdmaHw::StartGuardTransitionPrefixBarrier() {
    NS_ABORT_MSG_IF(!m_guardTransitionPrefixBarrierEnabled ||
                        !m_guardFastpathTransactionIsTransition ||
                        (m_guardFastpathPhase != GUARD_FASTPATH_IDLE &&
                         m_guardFastpathPhase != GUARD_FASTPATH_PREPARE) ||
                        m_guardPendingGrantAcks != 0 ||
                        m_guardTransitionPrefixBarrierWaiting ||
                        m_guardTransitionPrefixBarrierResolved ||
                        m_guardTransitionPrefixDeadlineEvent.IsRunning(),
                    "GUARD V12 prefix barrier started outside a closed prepare phase");
    if (!ValidateGuardTransitionQueueCohort()) {
        // Prepare can outlive every member of its frozen cohort while a late
        // join keeps the controller's global active set nonempty.  Close only
        // this empty snapshot; the membership revision queues the late join
        // for a subsequent transaction.
        m_guardTransitionPrefixBarrierResolved = true;
        FinishGuardFastpathTransaction();
        return;
    }
    m_guardFastpathPhase = GUARD_FASTPATH_PREFIX_BARRIER;

    m_guardTransitionActivationOrder.clear();
    for (auto *flow : m_guardFastpathTransactionWaiters) {
        if (m_rate_flow_ctl_set.find(flow) != m_rate_flow_ctl_set.end()) {
            m_guardTransitionActivationOrder.push_back(flow);
        }
    }
    std::sort(m_guardTransitionActivationOrder.begin(),
              m_guardTransitionActivationOrder.end(),
              [](RdmaRxQueuePair *left, RdmaRxQueuePair *right) {
                  if (left->m_guard_register_ns != right->m_guard_register_ns) {
                      return left->m_guard_register_ns < right->m_guard_register_ns;
                  }
                  return left->m_flow_id < right->m_flow_id;
              });
    m_guardTransitionActivationHolds.clear();
    for (auto *flow : m_guardTransitionActivationOrder) {
        m_guardTransitionActivationHolds.push_back(
            Ptr<RdmaRxQueuePair>(flow));
    }
    if (m_guardTransitionActivationOrder.empty()) {
        m_guardTransitionPrefixBarrierResolved = true;
        FinishGuardFastpathTransaction();
        return;
    }

    RdmaRxQueuePair *sample = m_guardTransitionActivationOrder.front();
    uint32_t receiver_nic = GetNicIdxOfRxQp(sample);
    uint16_t receiver_pg = sample->m_guard_pg;
    uint64_t receiver_capacity_bps =
        m_nic[receiver_nic].dev->GetDataRate().GetBitRate();
    NS_ABORT_MSG_IF(receiver_capacity_bps == 0,
                    "GUARD V12 prefix barrier found zero receiver capacity");
    uint64_t max_rtt_ns = 0;
    uint64_t remaining_bytes = 0;
    m_guardTransitionPrefixTargets.clear();
    for (auto *flow : m_guardTransitionActivationOrder) {
        NS_ABORT_MSG_IF(flow->m_guard_register_ns < 0 ||
                            flow->m_guard_flow_size == 0 ||
                            !flow->m_guard_has_first_grant_gate_bytes ||
                            flow->m_guard_first_grant_gate_bytes == 0 ||
                            !std::isfinite(flow->m_base_rtt_sec) ||
                            flow->m_base_rtt_sec <= 0.0,
                        "GUARD V12 prefix barrier lacks receiver-verifiable flow metadata");
        uint32_t flow_nic = GetNicIdxOfRxQp(flow);
        uint64_t capacity_bps = m_nic[flow_nic].dev->GetDataRate().GetBitRate();
        NS_ABORT_MSG_IF(flow_nic != receiver_nic ||
                            capacity_bps != receiver_capacity_bps ||
                            (!m_guardMixedPgVectorFastpath &&
                             flow->m_guard_pg != receiver_pg),
                        "GUARD transition spans receiver capacities or a disallowed priority group");
        long double rtt_ns_value =
            std::ceil(static_cast<long double>(flow->m_base_rtt_sec) * 1.0e9L);
        NS_ABORT_MSG_IF(
            rtt_ns_value <= 0.0L ||
                rtt_ns_value >
                    static_cast<long double>(std::numeric_limits<int64_t>::max()),
            "GUARD V12 base RTT does not fit the simulator time domain");
        uint64_t rtt_ns = static_cast<uint64_t>(rtt_ns_value);
        max_rtt_ns = std::max(max_rtt_ns, rtt_ns);
        uint64_t target = std::min<uint64_t>(
            flow->m_guard_flow_size, flow->m_guard_first_grant_gate_bytes);
        m_guardTransitionPrefixTargets.emplace(flow, target);
        uint64_t received = std::min<uint64_t>(
            target, flow->ReceiverNextExpectedSeq);
        NS_ABORT_MSG_IF(
            remaining_bytes > std::numeric_limits<uint64_t>::max() -
                                  (target - received),
            "GUARD V12 prefix remaining-byte sum overflow");
        remaining_bytes += target - received;
    }

    uint64_t delay_ns = 0;
    GuardTransitionPrefixWireBudget watchdog_budget = {};
    if (m_guardTransitionPrefixWireWatchdogEnabled) {
        std::vector<GuardTransitionPrefixWaiterBudgetInput> waiter_inputs;
        waiter_inputs.reserve(m_guardTransitionActivationOrder.size());
        for (auto *flow : m_guardTransitionActivationOrder) {
            long double rtt_ns_value = std::ceil(
                static_cast<long double>(flow->m_base_rtt_sec) * 1.0e9L);
            NS_ABORT_MSG_IF(
                rtt_ns_value <= 0.0L ||
                    rtt_ns_value > static_cast<long double>(
                        std::numeric_limits<uint64_t>::max()),
                "GUARD V13 waiter base RTT does not fit uint64 nanoseconds");
            waiter_inputs.push_back({
                flow->m_guard_flow_size,
                flow->m_guard_first_grant_gate_bytes,
                static_cast<uint64_t>(rtt_ns_value)});
        }
        std::vector<GuardTransitionPrefixIncumbentBudgetInput>
            incumbent_inputs;
        incumbent_inputs.reserve(m_guardFastpathIncumbents.size());
        for (auto *flow : m_guardFastpathIncumbents) {
            NS_ABORT_MSG_IF(
                m_rate_flow_ctl_set.find(flow) == m_rate_flow_ctl_set.end() ||
                    flow->m_guard_last_acked_upper_bound_bps == 0 ||
                    !std::isfinite(flow->m_base_rtt_sec) ||
                    flow->m_base_rtt_sec <= 0.0,
                "GUARD V13 incumbent lacks an acknowledged upper bound or base RTT");
            long double rtt_ns_value = std::ceil(
                static_cast<long double>(flow->m_base_rtt_sec) * 1.0e9L);
            NS_ABORT_MSG_IF(
                rtt_ns_value <= 0.0L ||
                    rtt_ns_value > static_cast<long double>(
                        std::numeric_limits<uint64_t>::max()),
                "GUARD V13 incumbent base RTT does not fit uint64 nanoseconds");
            incumbent_inputs.push_back({
                flow->m_guard_last_acked_upper_bound_bps,
                static_cast<uint64_t>(rtt_ns_value)});
        }
        std::vector<GuardTransitionPrefixDrainingBudgetInput> draining_inputs;
        for (const auto &item : m_guardDrainingRecords) {
            const GuardDrainingRecord &record = item.second;
            if (record.state != GUARD_DRAINING) continue;
            NS_ABORT_MSG_IF(
                record.receiverNic != receiver_nic || record.reservedBps == 0 ||
                    !std::isfinite(record.hold->m_base_rtt_sec) ||
                    record.hold->m_base_rtt_sec <= 0.0,
                "GUARD V14 watchdog found an invalid draining reservation");
            long double rtt_ns_value = std::ceil(
                static_cast<long double>(record.hold->m_base_rtt_sec) * 1.0e9L);
            NS_ABORT_MSG_IF(
                rtt_ns_value <= 0.0L ||
                    rtt_ns_value > static_cast<long double>(
                        std::numeric_limits<uint64_t>::max()),
                "GUARD V14 draining base RTT does not fit uint64 nanoseconds");
            draining_inputs.push_back({
                record.reservedBps, static_cast<uint64_t>(rtt_ns_value)});
        }
        NS_ABORT_MSG_IF(
            !ComputeGuardTransitionPrefixWireBudget(
                waiter_inputs, incumbent_inputs, draining_inputs, m_mtu,
                CustomHeader::GetStaticWholeHeaderSize(),
                receiver_capacity_bps, &watchdog_budget),
            "GUARD V13 prefix wire watchdog budget is invalid or overflowed");
        delay_ns = watchdog_budget.delayNs;
    } else {
        __uint128_t serialization_numerator =
            static_cast<__uint128_t>(remaining_bytes) * 8000000000ULL;
        __uint128_t serialization_ns_wide =
            (serialization_numerator + receiver_capacity_bps - 1) /
            receiver_capacity_bps;
        NS_ABORT_MSG_IF(
            serialization_ns_wide >
                static_cast<__uint128_t>(std::numeric_limits<int64_t>::max()) ||
                max_rtt_ns >
                    static_cast<uint64_t>(std::numeric_limits<int64_t>::max()) -
                        static_cast<uint64_t>(serialization_ns_wide),
            "GUARD V12 prefix deadline delay overflow");
        delay_ns = max_rtt_ns +
                   static_cast<uint64_t>(serialization_ns_wide);
    }
    int64_t now_ns = Simulator::Now().GetNanoSeconds();
    NS_ABORT_MSG_IF(
        now_ns < 0 || delay_ns == 0 ||
            delay_ns > static_cast<uint64_t>(std::numeric_limits<int64_t>::max()) ||
            now_ns > std::numeric_limits<int64_t>::max() -
                         static_cast<int64_t>(delay_ns),
        "GUARD V12 prefix absolute deadline overflow");

    m_guardTransitionPrefixBarrierStart = Simulator::Now();
    m_guardTransitionPrefixDeadline =
        NanoSeconds(now_ns + static_cast<int64_t>(delay_ns));
    if (m_guardTransitionPrefixWireWatchdogEnabled) {
        NS_ABORT_MSG_IF(
            m_guardTransitionPrefixWatchdogBudgetRecords ==
                std::numeric_limits<uint64_t>::max(),
            "GUARD V13 prefix watchdog budget-record counter overflow");
        m_guardTransitionPrefixWatchdogBudgetRecords++;
        if (m_guardTransitionPrefixWatchdogBudgetRecords > 1) {
            m_guardTransitionPrefixWatchdogNonReconstructable = true;
        }
        m_guardTransitionPrefixWatchdogRoundedPayloadBudgetBytes =
            watchdog_budget.roundedPayloadBudgetBytes;
        m_guardTransitionPrefixWatchdogPacketCount = watchdog_budget.packetCount;
        m_guardTransitionPrefixWatchdogHeaderBytesPerPacket =
            watchdog_budget.headerBytesPerPacket;
        m_guardTransitionPrefixWatchdogWireBytes = watchdog_budget.wireBytes;
        m_guardTransitionPrefixWatchdogIncumbentCount =
            watchdog_budget.incumbentCount;
        m_guardTransitionPrefixWatchdogOccupancyBps =
            watchdog_budget.occupancyBps;
        m_guardTransitionPrefixWatchdogReceiverCapacityBps =
            watchdog_budget.receiverCapacityBps;
        m_guardTransitionPrefixWatchdogResidualBps =
            watchdog_budget.residualBps;
        m_guardTransitionPrefixWatchdogSerializationNs =
            watchdog_budget.serializationNs;
        m_guardTransitionPrefixWatchdogMaxRttNs = watchdog_budget.maxRttNs;
        m_guardTransitionPrefixWatchdogDelayNs = watchdog_budget.delayNs;
        m_guardTransitionPrefixWatchdogStartNs =
            static_cast<uint64_t>(now_ns);
        m_guardTransitionPrefixWatchdogDeadlineNs = static_cast<uint64_t>(
            m_guardTransitionPrefixDeadline.GetNanoSeconds());
        m_guardTransitionPrefixWatchdogBudgetActive = true;
    }
    BeginGuardTransitionAudit(
        receiver_nic, receiver_pg, receiver_capacity_bps,
        m_guardTransitionPrefixWireWatchdogEnabled ? &watchdog_budget : NULL);
    m_guardTransitionPrefixBarrierWaiting = true;
    m_guardTransitionPrefixBarrierStarts++;
    m_guardTransitionPrefixWaitersRequired +=
        m_guardTransitionActivationOrder.size();
    m_guardTransitionPrefixStartNs = std::max<uint64_t>(
        m_guardTransitionPrefixStartNs,
        static_cast<uint64_t>(m_guardTransitionPrefixBarrierStart.GetNanoSeconds()));
    m_guardTransitionPrefixDeadlineNs = std::max<uint64_t>(
        m_guardTransitionPrefixDeadlineNs,
        static_cast<uint64_t>(m_guardTransitionPrefixDeadline.GetNanoSeconds()));
    CheckGuardTransitionPrefixBarrier();
    if (m_guardTransitionPrefixBarrierWaiting) {
        m_guardTransitionPrefixDeadlineEvent = Simulator::Schedule(
            m_guardTransitionPrefixDeadline - Simulator::Now(),
            &RdmaHw::HandleGuardTransitionPrefixDeadline, this);
    }
}

void RdmaHw::CheckGuardTransitionPrefixBarrier() {
    if (!m_guardTransitionPrefixBarrierWaiting) return;
    uint64_t target_bytes = 0;
    uint64_t received_bytes = 0;
    uint64_t ready_waiters = 0;
    bool ready = true;
    for (auto *flow : m_guardTransitionActivationOrder) {
        if (m_rate_flow_ctl_set.find(flow) == m_rate_flow_ctl_set.end() ||
            m_guardFastpathTransactionWaiters.find(flow) ==
                m_guardFastpathTransactionWaiters.end()) {
            continue;
        }
        auto target_it = m_guardTransitionPrefixTargets.find(flow);
        if (target_it == m_guardTransitionPrefixTargets.end()) {
            m_guardTransitionPrefixBarrierViolations++;
            m_guardFastpathBarrierViolations++;
            NS_ABORT_MSG("GUARD V12 active waiter lost its frozen prefix target");
        }
        uint64_t target = target_it->second;
        uint64_t received = std::min<uint64_t>(
            target, flow->ReceiverNextExpectedSeq);
        NS_ABORT_MSG_IF(
            target_bytes > std::numeric_limits<uint64_t>::max() - target ||
                received_bytes >
                    std::numeric_limits<uint64_t>::max() - received,
            "GUARD V12 prefix evidence sum overflow");
        target_bytes += target;
        received_bytes += received;
        if (received < target) {
            ready = false;
        } else {
            ready_waiters++;
        }
    }
    if (!ready && Simulator::Now() < m_guardTransitionPrefixDeadline) return;

    uint64_t remaining_bytes = target_bytes - received_bytes;
    uint64_t wait_ns = static_cast<uint64_t>(
        (Simulator::Now() - m_guardTransitionPrefixBarrierStart).GetNanoSeconds());
    m_guardTransitionPrefixTargetBytes += target_bytes;
    m_guardTransitionPrefixReceivedBytes += received_bytes;
    m_guardTransitionPrefixRemainingBytes += remaining_bytes;
    m_guardTransitionPrefixWaitersReady += ready_waiters;
    m_guardTransitionPrefixWaitNs += wait_ns;
    m_guardTransitionPrefixMaxWaitNs = std::max(
        m_guardTransitionPrefixMaxWaitNs, wait_ns);
    m_guardTransitionPrefixBarrierWaiting = false;
    m_guardTransitionPrefixBarrierResolved = true;
    m_guardTransitionPrefixWatchdogBudgetActive = false;
    if (m_guardTransitionPrefixDeadlineEvent.IsRunning()) {
        Simulator::Cancel(m_guardTransitionPrefixDeadlineEvent);
    }

    if (ready) {
        ResolveGuardTransitionAudit("ready");
        m_guardTransitionPrefixBarrierReady++;
        m_guardTransitionPrefixTimedOut = false;
        m_guardTransitionPrefixReadyNs = std::max<uint64_t>(
            m_guardTransitionPrefixReadyNs,
            static_cast<uint64_t>(Simulator::Now().GetNanoSeconds()));
        StartGuardFastpathActivate();
        return;
    }
    NS_ABORT_MSG_IF(Simulator::Now() < m_guardTransitionPrefixDeadline,
                    "GUARD V12 prefix fallback preceded its deadline");
    m_guardTransitionPrefixBarrierTimeouts++;
    m_guardTransitionPrefixDegradedTransitions++;
    m_guardTransitionPrefixTimedOut = true;
    ResolveGuardTransitionAudit(
        m_guardTransitionPrefixFailClosed
            ? "timeout_fail_closed"
            : "timeout_fallback");
    if (m_guardTransitionPrefixFailClosed) {
        FinalizeGuardTransitionAudit("fail_closed_abort");
        NS_ABORT_MSG(
            "GUARD V14 fail-closed transition prefix deadline expired");
    }
    NS_ABORT_MSG_IF(
        m_guardMixedPgVectorFastpath &&
            !m_guardTransitionPrefixAckClockFallback,
        "GUARD mixed-PG prefix timeout lacks an explicitly enabled "
        "ACK-clocked fallback policy");
    m_guardTransitionFallbackActive = true;
    m_guardTransitionActivationCursor = 0;
    m_guardTransitionActivationBatchIndex = 0;
    NS_ABORT_MSG_IF(m_guardSmallSetFastpathLimit == 0,
                    "GUARD V12 fallback has no microbatch bound");
    SendNextGuardTransitionFallbackBatch();
}

void RdmaHw::HandleGuardTransitionPrefixDeadline() {
    NS_ABORT_MSG_IF(!m_guardTransitionPrefixBarrierWaiting ||
                        Simulator::Now() != m_guardTransitionPrefixDeadline,
                    "GUARD V12 prefix liveness timer fired out of state");
    // Requeue the decision at the same timestamp.  Packet deliveries that
    // were already scheduled for the deadline then advance their receiver
    // watermarks before the liveness fallback is selected.  The check is
    // idempotent if one of those deliveries resolves the barrier first.
    Simulator::ScheduleNow(&RdmaHw::CheckGuardTransitionPrefixBarrier, this);
}

void RdmaHw::SendNextGuardTransitionFallbackBatch() {
    NS_ABORT_MSG_IF(!m_guardTransitionFallbackActive ||
                        !m_guardFastpathTransactionIsTransition ||
                        m_guardFastpathPhase == GUARD_FASTPATH_PREPARE ||
                        m_guardPendingGrantAcks != 0,
                    "GUARD V12 fallback crossed a generation barrier");
    std::vector<RdmaRxQueuePair*> recipients;
    while (m_guardTransitionActivationCursor <
               m_guardTransitionActivationOrder.size() &&
           recipients.size() < m_guardSmallSetFastpathLimit) {
        RdmaRxQueuePair *flow = m_guardTransitionActivationOrder[
            m_guardTransitionActivationCursor++];
        if (m_rate_flow_ctl_set.find(flow) == m_rate_flow_ctl_set.end() ||
            m_guardFastpathTransactionWaiters.find(flow) ==
                m_guardFastpathTransactionWaiters.end()) {
            continue;
        }
        bool out_of_order =
            m_guardTransitionLastRegisterNs >= 0 &&
            (flow->m_guard_register_ns < m_guardTransitionLastRegisterNs ||
             (flow->m_guard_register_ns == m_guardTransitionLastRegisterNs &&
              flow->m_flow_id <= m_guardTransitionLastFlowId));
        if (out_of_order) {
            m_guardTransitionFallbackOrderViolations++;
            m_guardFastpathBarrierViolations++;
            NS_ABORT_MSG("GUARD V12 fallback activation order regressed");
        }
        m_guardTransitionLastRegisterNs = flow->m_guard_register_ns;
        m_guardTransitionLastFlowId = flow->m_flow_id;
        recipients.push_back(flow);
    }
    if (recipients.empty()) {
        NS_ABORT_MSG_IF(
            m_guardTransitionActivationCursor <
                m_guardTransitionActivationOrder.size(),
            "GUARD V12 fallback stalled before its terminal cursor");
        m_guardTransitionFallbackActive = false;
        FinishGuardFastpathTransaction();
        return;
    }
    m_guardFastpathPhase = GUARD_FASTPATH_ACTIVATE;
    m_guardTransitionCurrentBatch.clear();
    m_guardTransitionCurrentBatch.insert(recipients.begin(),
                                         recipients.end());
    m_guardTransitionActivationBatchIndex++;
    m_guardTransitionActivationBatchSize = recipients.size();
    m_guardTransitionActivateBatches++;
    m_guardTransitionActivateGrants += recipients.size();
    m_guardTransitionFallbackBatches++;
    m_guardTransitionFallbackMaxBatch = std::max<uint64_t>(
        m_guardTransitionFallbackMaxBatch, recipients.size());
    SendGuardFastpathRequiredGenerationOrdered(
        recipients, "transition_activate");
}

void RdmaHw::SendGuardFastpathRequiredGeneration(
    const std::unordered_set<RdmaRxQueuePair*> &recipients,
    const char *set_change) {
    NS_ABORT_MSG_IF(recipients.empty(),
                    "GUARD fast-path emitted an empty required generation");
    uint32_t generation = NextGuardGrantGeneration();
    m_guardPendingGrantAcks = recipients.size();
    m_guardAckRequiredBatches++;
    if (m_guardMixedPgVectorFastpath) m_guardGenerationLedger.clear();
    for (auto *flow : recipients) {
        bool waiter = m_guardFastpathWaiters.find(flow) !=
                      m_guardFastpathWaiters.end();
        bool allowed_vector_increase = false;
        if (m_guardMixedPgVectorFastpath &&
            m_guardFastpathPhase == GUARD_FASTPATH_ACTIVATE && !waiter) {
            const GuardFrozenTargetRecord *target = GetGuardFrozenTarget(flow);
            allowed_vector_increase =
                target != NULL && target->role == GUARD_VECTOR_INCUMBENT &&
                GetGuardPotentialUpperBoundBps(flow) <
                    static_cast<uint64_t>(target->targetMbps) * 1000000ULL;
        }
        if ((m_guardFastpathPhase == GUARD_FASTPATH_PREPARE && waiter) ||
            (m_guardFastpathPhase == GUARD_FASTPATH_ACTIVATE && !waiter &&
             !allowed_vector_increase)) {
            m_guardFastpathBarrierViolations++;
            NS_ABORT_MSG("GUARD fast-path recipient role violates phase barrier");
        }
        uint32_t target_mbps = m_guardFastpathTransactionTargetMbps;
        if (m_guardMixedPgVectorFastpath) {
            const GuardFrozenTargetRecord *target = GetGuardFrozenTarget(flow);
            NS_ABORT_MSG_IF(target == NULL,
                            "GUARD V14 required generation lost its target");
            target_mbps = target->targetMbps;
            uint8_t action = m_guardFastpathPhase == GUARD_FASTPATH_PREPARE
                                 ? m_guardFastpathReleaseRevectorActive
                                       ? GUARD_VECTOR_RELEASE_DECREASE
                                       : GUARD_VECTOR_PREPARE_DECREASE
                                 : m_guardFastpathReleaseRevectorActive
                                       ? GUARD_VECTOR_RELEASE_INCREASE
                                       : target->role == GUARD_VECTOR_WAITER
                                             ? GUARD_VECTOR_ACTIVATE_WAITER
                                             : GUARD_VECTOR_ACTIVATE_INCREASE;
            m_guardGenerationLedger.emplace(
                flow, GuardGenerationLedgerEntry{
                          target->identity, generation,
                          GetGuardFastpathWirePhaseTag(set_change), action,
                          target_mbps, false});
            if (action == GUARD_VECTOR_ACTIVATE_WAITER)
                m_guardVectorActivationWaiters++;
            else if (action == GUARD_VECTOR_ACTIVATE_INCREASE ||
                     action == GUARD_VECTOR_RELEASE_INCREASE)
                m_guardVectorActivationIncreases++;
        }
        flow->m_guard_grant_generation = generation;
        flow->m_guard_grant_generation_acked = false;
        flow->m_guard_last_issued_generation = generation;
        flow->m_guard_last_issued_target_bps =
            static_cast<uint64_t>(target_mbps) * 1000000ULL;
        flow->m_guard_grant_rate_bps =
            static_cast<uint64_t>(target_mbps) * 1000000ULL;
        if (m_guardMixedPgVectorFastpath) AssertGuardFrozenPotentialFits();
        SendRateControlPacket(flow, target_mbps,
                              set_change, generation, true);
    }
    if (m_guardMixedPgVectorFastpath) {
        __uint128_t potential_sum = 0;
        for (auto *flow : m_guardFastpathIncumbents) {
            if (m_rate_flow_ctl_set.find(flow) != m_rate_flow_ctl_set.end())
                potential_sum += GetGuardPotentialUpperBoundBps(flow);
        }
        if (m_guardFastpathPhase == GUARD_FASTPATH_ACTIVATE) {
            for (auto *flow : m_guardFastpathTransactionWaiters) {
                if (m_rate_flow_ctl_set.find(flow) != m_rate_flow_ctl_set.end())
                    potential_sum += GetGuardPotentialUpperBoundBps(flow);
            }
        }
        NS_ABORT_MSG_IF(
            potential_sum + m_guardFrozenDrainingReservedBps >
                m_guardFrozenReceiverCapacityBps,
                        "GUARD V14 issued vector exceeds receiver capacity");
    }
    ResetGuardReliabilityRefresh();
}

void RdmaHw::SendGuardFastpathRequiredGenerationOrdered(
    const std::vector<RdmaRxQueuePair*> &recipients,
    const char *set_change) {
    NS_ABORT_MSG_IF(recipients.empty(),
                    "GUARD fast-path emitted an empty ordered generation");
    uint32_t generation = NextGuardGrantGeneration();
    m_guardPendingGrantAcks = recipients.size();
    m_guardAckRequiredBatches++;
    if (m_guardMixedPgVectorFastpath) m_guardGenerationLedger.clear();
    for (auto *flow : recipients) {
        bool waiter = m_guardFastpathWaiters.find(flow) !=
                      m_guardFastpathWaiters.end();
        const GuardFrozenTargetRecord *target =
            m_guardMixedPgVectorFastpath ? GetGuardFrozenTarget(flow) : NULL;
        bool valid_vector_recipient =
            m_guardMixedPgVectorFastpath && target != NULL &&
            ((target->role == GUARD_VECTOR_WAITER && waiter &&
              m_guardFastpathTransactionWaiters.find(flow) !=
                  m_guardFastpathTransactionWaiters.end()) ||
             (target->role == GUARD_VECTOR_INCUMBENT && !waiter &&
              GetGuardPotentialUpperBoundBps(flow) <
                  static_cast<uint64_t>(target->targetMbps) * 1000000ULL));
        if (m_guardFastpathPhase != GUARD_FASTPATH_ACTIVATE ||
            (!valid_vector_recipient &&
             (!waiter || m_guardFastpathTransactionWaiters.find(flow) ==
                             m_guardFastpathTransactionWaiters.end()))) {
            m_guardTransitionPrefixBarrierViolations++;
            m_guardFastpathBarrierViolations++;
            NS_ABORT_MSG("GUARD V12 ordered recipient violates activation barrier");
        }
        uint32_t target_mbps = m_guardFastpathTransactionTargetMbps;
        if (m_guardMixedPgVectorFastpath) {
            NS_ABORT_MSG_IF(target == NULL,
                            "GUARD V14 ordered activation lost its target");
            target_mbps = target->targetMbps;
            uint8_t action = target->role == GUARD_VECTOR_WAITER
                                 ? GUARD_VECTOR_ACTIVATE_WAITER
                                 : GUARD_VECTOR_ACTIVATE_INCREASE;
            m_guardGenerationLedger.emplace(
                flow, GuardGenerationLedgerEntry{
                          target->identity, generation,
                          GetGuardFastpathWirePhaseTag(set_change), action,
                          target_mbps, false});
            if (action == GUARD_VECTOR_ACTIVATE_WAITER)
                m_guardVectorActivationWaiters++;
            else
                m_guardVectorActivationIncreases++;
        }
        flow->m_guard_grant_generation = generation;
        flow->m_guard_grant_generation_acked = false;
        flow->m_guard_last_issued_generation = generation;
        flow->m_guard_last_issued_target_bps =
            static_cast<uint64_t>(target_mbps) * 1000000ULL;
        flow->m_guard_grant_rate_bps =
            static_cast<uint64_t>(target_mbps) * 1000000ULL;
        if (m_guardMixedPgVectorFastpath) AssertGuardFrozenPotentialFits();
        SendRateControlPacket(flow, target_mbps,
                              set_change, generation, true);
    }
    if (m_guardMixedPgVectorFastpath) {
        __uint128_t potential_sum = 0;
        for (auto *flow : m_guardFastpathIncumbents) {
            if (m_rate_flow_ctl_set.find(flow) != m_rate_flow_ctl_set.end())
                potential_sum += GetGuardPotentialUpperBoundBps(flow);
        }
        for (auto *flow : m_guardFastpathTransactionWaiters) {
            if (m_rate_flow_ctl_set.find(flow) != m_rate_flow_ctl_set.end())
                potential_sum += GetGuardPotentialUpperBoundBps(flow);
        }
        NS_ABORT_MSG_IF(
            potential_sum + m_guardFrozenDrainingReservedBps >
                m_guardFrozenReceiverCapacityBps,
                        "GUARD V14 activation vector exceeds receiver capacity");
    }
    ResetGuardReliabilityRefresh();
}

void RdmaHw::SendGuardFastpathOptionalRelease() {
    if (m_guardFastpathIncumbents.empty()) return;
    NS_ABORT_MSG_IF(m_guardFastpathPhase != GUARD_FASTPATH_IDLE ||
                        m_guardPendingGrantAcks != 0,
                    "GUARD optional release crossed an ACK barrier");
    if (m_guardMixedPgVectorFastpath) {
        NS_ABORT_MSG_IF(m_guardFrozenVectorActive,
                        "GUARD V14 release inherited a frozen vector");
        m_guardGenerationLedger.clear();
        FreezeGuardFastpathTargetVector(
            m_guardFastpathConsumedMembershipRevision);
        bool requires_barrier = false;
        for (const auto &target : m_guardFrozenTargetVector) {
            if (m_rate_flow_ctl_set.find(target.flow) ==
                m_rate_flow_ctl_set.end()) {
                continue;
            }
            uint64_t target_bps =
                static_cast<uint64_t>(target.targetMbps) * 1000000ULL;
            if (GetGuardPotentialUpperBoundBps(target.flow) > target_bps) {
                requires_barrier = true;
                break;
            }
        }
        if (requires_barrier) {
            m_guardVectorReleaseRequired++;
            m_guardFastpathReleaseRevectorActive = true;
            m_guardFastpathTransaction++;
            m_guardFastpathTransactions++;
            m_guardFastpathTransactionTargetN =
                m_guardFrozenTargetVector.size();
            m_guardFastpathTransactionTargetMbps = 0;
            StartGuardFastpathPrepare();
            return;
        }
    }
    RdmaRxQueuePair *sample = *m_guardFastpathIncumbents.begin();
    uint64_t line_rate_bps =
        m_nic[GetNicIdxOfRxQp(sample)].dev->GetDataRate().GetBitRate();
    uint32_t rate_mbps = std::max<uint32_t>(
        1, line_rate_bps / m_guardFastpathIncumbents.size() / 1000000);
    uint64_t rate_bps = static_cast<uint64_t>(rate_mbps) * 1000000;
    for (auto *flow : m_guardFastpathIncumbents) {
        uint64_t flow_rate_bps = rate_bps;
        if (m_guardMixedPgVectorFastpath) {
            const GuardFrozenTargetRecord *target = GetGuardFrozenTarget(flow);
            NS_ABORT_MSG_IF(target == NULL,
                            "GUARD V14 optional release lost its target");
            flow_rate_bps =
                static_cast<uint64_t>(target->targetMbps) * 1000000ULL;
        }
        NS_ABORT_MSG_IF(flow_rate_bps < flow->m_guard_grant_upper_bound_bps,
                        "GUARD release-only generation decreased an incumbent");
    }
    uint32_t generation = NextGuardGrantGeneration();
    if (m_guardMixedPgVectorFastpath) m_guardVectorReleaseOptional++;
    m_guardPendingGrantAcks = 0;
    m_guardAckOptionalBatches++;
    m_guardFastpathOptionalReleaseGenerations++;
    m_guardFastpathOptionalReleaseGrants += m_guardFastpathIncumbents.size();
    for (auto *flow : m_guardFastpathIncumbents) {
        uint32_t flow_rate_mbps = rate_mbps;
        if (m_guardMixedPgVectorFastpath) {
            flow_rate_mbps = GetGuardFrozenTarget(flow)->targetMbps;
        }
        uint64_t flow_rate_bps =
            static_cast<uint64_t>(flow_rate_mbps) * 1000000ULL;
        flow->m_guard_grant_generation = generation;
        flow->m_guard_grant_generation_acked = true;
        flow->m_guard_last_issued_generation = generation;
        flow->m_guard_last_issued_target_bps = flow_rate_bps;
        flow->m_guard_last_acked_generation = generation;
        flow->m_guard_last_acked_upper_bound_bps = flow_rate_bps;
        flow->m_guard_grant_rate_bps = flow_rate_bps;
        flow->m_guard_grant_upper_bound_bps = std::max(
            flow->m_guard_grant_upper_bound_bps, flow_rate_bps);
        if (m_guardMixedPgVectorFastpath) AssertGuardFrozenPotentialFits();
        SendRateControlPacket(flow, flow_rate_mbps, "release", generation, false);
    }
    if (m_guardProgressTransactionActive) CloseGuardProgressTransaction();
    if (m_guardMixedPgVectorFastpath) {
        m_guardFrozenVectorActive = false;
        m_guardFrozenReceiverNic = 0;
        m_guardFrozenReceiverCapacityBps = 0;
        m_guardFrozenActiveRecords = 0;
        m_guardFrozenDrainingRecords = 0;
        m_guardFrozenDrainingReservedBps = 0;
        m_guardFrozenAllocatableCapacityBps = 0;
        m_guardFrozenEncodedTargetBps = 0;
        m_guardFrozenMembershipRevision = 0;
        m_guardFrozenAllocationRevision = 0;
        m_guardFrozenProgressRevision = 0;
        m_guardFrozenReasonMask = 0;
        m_guardFrozenTargetVector.clear();
        m_guardFrozenTargetHolds.clear();
        m_guardFrozenTargetByFlow.clear();
        m_guardFrozenVectorHash = 0;
    }
    m_guardLastVectorActiveFlows = m_guardFastpathIncumbents.size();
    m_guardHasEmittedVectorThisEpoch = true;
    if (m_guardFastpathHighFanIn && !m_guardFastpathWaiters.empty() &&
        !m_guardMembershipEvent.IsRunning()) {
        ScheduleGuardFastpathHighCollection("registration");
    } else if (m_guardSerializedProgressRefresh &&
               !m_guardProgressTransactionActive &&
               (!m_guardProgressDirtyFlows.empty() || m_guardCapacityDirty) &&
               m_guardPendingMembershipChanges == 0) {
        StartGuardProgressTransaction();
    }
}

void RdmaHw::FinishGuardFastpathGeneration() {
    NS_ABORT_MSG_IF(m_guardPendingGrantAcks != 0,
                    "GUARD fast-path phase closed with pending ACKs");
    if (m_guardFastpathPhase == GUARD_FASTPATH_PREPARE) {
        StartGuardFastpathActivate();
        return;
    }
    if (m_guardFastpathPhase == GUARD_FASTPATH_ACTIVATE) {
        if (m_guardFastpathTransactionIsTransition &&
            m_guardTransitionPrefixBarrierEnabled &&
            m_guardTransitionFallbackActive) {
            m_guardTransitionCurrentBatch.clear();
            m_guardTransitionFallbackClosedBatches++;
            SendNextGuardTransitionFallbackBatch();
            return;
        }
        if (m_guardFastpathTransactionIsTransition &&
            m_guardTransitionPrefixBarrierEnabled) {
            m_guardTransitionCurrentBatch.clear();
        }
        FinishGuardFastpathTransaction();
    }
}

void RdmaHw::FinishGuardFastpathTransaction() {
    bool transition = m_guardFastpathTransactionIsTransition;
    bool release_revector = m_guardFastpathReleaseRevectorActive;
    if (transition && m_guardTransitionPrefixBarrierEnabled) {
        NS_ABORT_MSG_IF(m_guardTransitionPrefixBarrierWaiting ||
                            m_guardTransitionPrefixDeadlineEvent.IsRunning() ||
                            m_guardPendingGrantAcks != 0,
                        "GUARD V12 transaction closed across a live barrier");
        NS_ABORT_MSG_IF(!m_guardTransitionCurrentBatch.empty(),
                        "GUARD V12 transaction closed with an active batch");
        FinalizeGuardTransitionAudit(
            m_guardTransitionPrefixTimedOut
                ? "fallback_activation_ack_closed"
                : "activation_ack_closed");
    }
    for (auto *flow : m_guardFastpathTransactionWaiters) {
        if (m_rate_flow_ctl_set.find(flow) == m_rate_flow_ctl_set.end()) continue;
        m_guardFastpathWaiters.erase(flow);
        m_guardFastpathIncumbents.emplace(flow);
        m_guardFastpathWaitersActivated++;
    }
    m_guardFastpathTransactionWaiters.clear();
    m_guardFastpathTransactionTargetN = 0;
    m_guardFastpathTransactionTargetMbps = 0;
    m_guardFastpathPhase = GUARD_FASTPATH_IDLE;
    m_guardFastpathTransactionIsTransition = false;
    m_guardFastpathReleaseRevectorActive = false;
    m_guardFastpathLastTransactionCloseNs = static_cast<uint64_t>(
        Simulator::Now().GetNanoSeconds());
    m_guardLastVectorActiveFlows = m_guardFastpathIncumbents.size();
    m_guardHasEmittedVectorThisEpoch = true;
    if (transition) m_guardFastpathHighInitialCommitted = true;
    if (m_guardProgressTransactionActive) CloseGuardProgressTransaction();
    if (m_guardMixedPgVectorFastpath) {
        m_guardFrozenVectorActive = false;
        m_guardFrozenReceiverNic = 0;
        m_guardFrozenReceiverCapacityBps = 0;
        m_guardFrozenActiveRecords = 0;
        m_guardFrozenDrainingRecords = 0;
        m_guardFrozenDrainingReservedBps = 0;
        m_guardFrozenAllocatableCapacityBps = 0;
        m_guardFrozenEncodedTargetBps = 0;
        m_guardFrozenMembershipRevision = 0;
        m_guardFrozenAllocationRevision = 0;
        m_guardFrozenProgressRevision = 0;
        m_guardFrozenReasonMask = 0;
        m_guardFrozenVectorHash = 0;
        m_guardFrozenTargetVector.clear();
        m_guardFrozenTargetHolds.clear();
        m_guardFrozenTargetByFlow.clear();
        m_guardGenerationLedger.clear();
    }
    if (m_guardSerializedDraining) CommitGuardPendingDrains();
    if (transition && m_guardTransitionPrefixBarrierEnabled) {
        m_guardTransitionPrefixBarrierResolved = false;
        m_guardTransitionPrefixTimedOut = false;
        m_guardTransitionFallbackActive = false;
        m_guardTransitionPrefixWatchdogBudgetActive = false;
        m_guardTransitionPrefixBarrierStart = Time(0);
        m_guardTransitionPrefixDeadline = Time(0);
        m_guardTransitionActivationOrder.clear();
        m_guardTransitionActivationHolds.clear();
        m_guardTransitionPrefixTargets.clear();
        m_guardTransitionCurrentBatch.clear();
        m_guardTransitionActivationCursor = 0;
        m_guardTransitionActivationBatchIndex = 0;
        m_guardTransitionActivationBatchSize = 0;
        m_guardTransitionLastRegisterNs = -1;
        m_guardTransitionLastFlowId = -1;
    }
    if (m_guardSerializedDraining && m_rate_flow_ctl_set.empty() &&
        m_guardDrainingRecords.empty()) {
        if (!m_guardTerminalResetDeferred) SettleGuardActiveEmptyState();
        return;
    }

    if (release_revector && m_guardPendingMembershipChanges == 0) {
        if (m_guardSerializedProgressRefresh &&
            !m_rate_flow_ctl_set.empty() &&
            (!m_guardProgressDirtyFlows.empty() || m_guardCapacityDirty)) {
            StartGuardProgressTransaction();
        }
        return;
    }
    if (m_guardFastpathHighFanIn) {
        if (m_guardFastpathCollectionReady) {
            if (!m_guardFastpathReadyWaiters.empty()) {
                StartGuardFastpathTransaction();
            } else {
                m_guardFastpathCollectionReady = false;
                ConsumeGuardFastpathMembershipThrough(
                    m_guardFastpathReadyMembershipRevision);
                SendGuardFastpathOptionalRelease();
            }
            return;
        }
        if (!m_guardFastpathWaiters.empty() &&
            !m_guardMembershipEvent.IsRunning()) {
            NS_ABORT_MSG_IF(
                m_guardPendingMembershipChanges == 0,
                "GUARD fast-path waiter lost its dirty membership revision");
            ScheduleGuardFastpathHighCollection("registration");
        } else if (m_guardPendingMembershipChanges > 0 &&
                   !m_guardMembershipEvent.IsRunning()) {
            ScheduleGuardFastpathHighCollection("release");
        } else if (m_guardSerializedProgressRefresh &&
                   !m_rate_flow_ctl_set.empty() &&
                   (!m_guardProgressDirtyFlows.empty() ||
                    m_guardCapacityDirty)) {
            StartGuardProgressTransaction();
        }
        return;
    }
    if (!m_guardFastpathWaiters.empty()) {
        StartGuardFastpathTransaction();
    } else if (m_guardPendingMembershipChanges > 0) {
        ConsumeGuardFastpathMembershipThrough(
            m_guardFastpathMembershipRevision);
        SendGuardFastpathOptionalRelease();
    } else if (m_guardSerializedProgressRefresh &&
               !m_rate_flow_ctl_set.empty() &&
               (!m_guardProgressDirtyFlows.empty() || m_guardCapacityDirty)) {
        StartGuardProgressTransaction();
    }
}

void RdmaHw::RequestGuardMembershipUpdate(const char *set_change) {
    if (m_guardMembershipCoalesceWindow.IsZero()) {
        RedistributeGuardRates(set_change);
        return;
    }
    if (GuardSmallSetFastpathEnabled()) {
        RequestGuardFastpathMembershipUpdate(set_change);
        return;
    }
    m_guardMembershipChangesDeferred++;
    m_guardPendingMembershipChanges++;
    Time now = Simulator::Now();
    if (!m_guardHasEmittedVectorThisEpoch) {
        if (!m_guardInitialCollectionPending) {
            NS_ABORT_MSG_IF(
                m_guardMembershipEvent.IsRunning(),
                "GUARD initial collection found an unexpected membership timer");
            Time initial_window = GetGuardInitialCollectionQuietWindow();
            int64_t window_ns = initial_window.GetNanoSeconds();
            uint64_t max_time_value = static_cast<uint64_t>(
                std::numeric_limits<int64_t>::max());
            NS_ABORT_MSG_IF(
                window_ns <= 0 || m_guardMembershipCoalesceMaxWindows == 0 ||
                    static_cast<uint64_t>(window_ns) >
                        max_time_value /
                            m_guardMembershipCoalesceMaxWindows,
                "GUARD initial collection deadline overflow");
            uint64_t delay_ns = static_cast<uint64_t>(window_ns) *
                                m_guardMembershipCoalesceMaxWindows;
            Time delay = NanoSeconds(static_cast<int64_t>(delay_ns));
            int64_t now_steps = now.GetTimeStep();
            int64_t delay_steps = delay.GetTimeStep();
            NS_ABORT_MSG_IF(
                now_steps < 0 || delay_steps <= 0 ||
                    now_steps > std::numeric_limits<int64_t>::max() - delay_steps,
                "GUARD initial collection absolute deadline overflow");
            m_guardInitialCollectionStart = now;
            m_guardInitialCollectionDeadline = now + delay;
            m_guardInitialCollectionTarget = m_guardInitialCollectionFullDeadline
                                                  ? m_guardInitialCollectionDeadline
                                                  : now + initial_window;
            m_guardMembershipBatchStart = now;
            m_guardInitialCollectionPending = true;
            m_guardInitialCollectionStarts++;
            m_guardMembershipEvent = Simulator::Schedule(
                m_guardInitialCollectionTarget - now,
                &RdmaHw::FlushGuardMembershipUpdate, this);
        } else {
            m_guardInitialCollectionDeferredChanges++;
            NS_ABORT_MSG_IF(
                !m_guardMembershipEvent.IsRunning(),
                "GUARD initial collection lost its membership timer");
            if (!m_guardInitialCollectionFullDeadline) {
                Time initial_window = GetGuardInitialCollectionQuietWindow();
                NS_ABORT_MSG_IF(
                    now > m_guardInitialCollectionDeadline,
                    "GUARD sliding initial collection passed its hard deadline");
                Time remaining = m_guardInitialCollectionDeadline - now;
                Time target = remaining <= initial_window
                                  ? m_guardInitialCollectionDeadline
                                  : now + initial_window;
                NS_ABORT_MSG_IF(
                    target < now || target > m_guardInitialCollectionDeadline,
                    "GUARD sliding initial collection target is out of bounds");
                Simulator::Cancel(m_guardMembershipEvent);
                m_guardMembershipTimerReschedules++;
                m_guardInitialCollectionReschedules++;
                m_guardInitialCollectionTarget = target;
                m_guardMembershipEvent = Simulator::Schedule(
                    target - now, &RdmaHw::FlushGuardMembershipUpdate, this);
            }
        }
        // Full-deadline mode retains V9's one fixed timer.  Sliding mode
        // restarts one quiet window without ever crossing that same deadline.
        return;
    }
    bool release = std::string(set_change) == "release";
    if (release && !m_guardMembershipEvent.IsRunning() &&
        m_guardLastVectorActiveFlows > 0 &&
        m_rate_flow_ctl_set.size() > m_guardLastVectorActiveFlows / 2) {
        // Retaining the previous vector after departures cannot oversubscribe
        // the receiver.  Wait until the registered set has at least halved;
        // the resulting geometric sequence bounds pure-release grant traffic.
        m_guardReleaseThresholdDeferrals++;
        return;
    }
    if (m_guardMembershipBatchStart.IsZero()) m_guardMembershipBatchStart = now;
    Time deadline = m_guardMembershipBatchStart + NanoSeconds(
        m_guardMembershipCoalesceWindow.GetNanoSeconds() *
        m_guardMembershipCoalesceMaxWindows);
    Time target = std::min(now + m_guardMembershipCoalesceWindow, deadline);
    if (m_guardMembershipEvent.IsRunning()) {
        Simulator::Cancel(m_guardMembershipEvent);
        m_guardMembershipTimerReschedules++;
    }
    m_guardMembershipEvent = Simulator::Schedule(
        std::max(Time(0), target - now), &RdmaHw::FlushGuardMembershipUpdate, this);
}

bool RdmaHw::IsGuardMembershipDirty() const {
    return !m_guardMembershipCoalesceWindow.IsZero() &&
           (m_guardPendingMembershipChanges > 0 || m_guardMembershipEvent.IsRunning());
}

void RdmaHw::FlushGuardMembershipUpdate() {
    uint64_t batch = m_guardPendingMembershipChanges;
    m_guardPendingMembershipChanges = 0;
    m_guardMembershipBatchStart = Time(0);
    // A cancelled or stale timer must never manufacture a new generation.
    if (batch == 0) return;
    bool initial_collection =
        m_guardInitialCollectionPending && !m_guardHasEmittedVectorThisEpoch;
    if (initial_collection) {
        Time expected = m_guardInitialCollectionFullDeadline
                            ? m_guardInitialCollectionDeadline
                            : m_guardInitialCollectionTarget;
        NS_ABORT_MSG_IF(
            Simulator::Now() != expected,
            "GUARD initial collection did not flush at its scheduled target");
        NS_ABORT_MSG_IF(
            expected > m_guardInitialCollectionDeadline ||
                (!m_guardInitialCollectionFullDeadline &&
                 expected < m_guardInitialCollectionStart +
                                GetGuardInitialCollectionQuietWindow()),
            "GUARD initial collection flush target violates its mode bounds");
        uint64_t wait_ns = static_cast<uint64_t>(
            (Simulator::Now() - m_guardInitialCollectionStart).GetNanoSeconds());
        m_guardInitialCollectionFlushes++;
        if (expected == m_guardInitialCollectionDeadline) {
            m_guardInitialCollectionHardFlushes++;
        } else {
            m_guardInitialCollectionQuietFlushes++;
        }
        NS_ABORT_MSG_IF(
            m_guardInitialCollectionQuietFlushes +
                    m_guardInitialCollectionHardFlushes !=
                m_guardInitialCollectionFlushes,
            "GUARD initial collection flush-reason counters diverged");
        m_guardInitialCollectionWaitNs += wait_ns;
        m_guardInitialCollectionMaxWaitNs = std::max(
            m_guardInitialCollectionMaxWaitNs, wait_ns);
        m_guardInitialCollectionPending = false;
        m_guardInitialCollectionStart = Time(0);
        m_guardInitialCollectionDeadline = Time(0);
        m_guardInitialCollectionTarget = Time(0);
    }
    if (m_rate_flow_ctl_set.empty()) {
        m_guardMembershipEmptyCancellations++;
        m_guardHasEmittedVectorThisEpoch = false;
        return;
    }
    m_guardMembershipBatches++;
    m_guardMembershipMaxBatch = std::max(m_guardMembershipMaxBatch, batch);
    if (m_guardGrantGeneration == std::numeric_limits<uint32_t>::max()) {
        std::cerr << "GUARD grant generation exhausted" << std::endl;
        exit(1);
    }
    m_guardGrantGeneration++;
    RedistributeGuardRates("membership_batch", m_guardGrantGeneration);
    m_guardLastVectorActiveFlows = m_rate_flow_ctl_set.size();
    m_guardHasEmittedVectorThisEpoch = true;
    ResetGuardReliabilityRefresh();
}

void RdmaHw::ResetGuardReliabilityRefresh() {
    if (m_guardReliabilityRefreshEvent.IsRunning()) {
        Simulator::Cancel(m_guardReliabilityRefreshEvent);
    }
    if (m_guardGrantReliabilityRtts <= 0 || m_rate_flow_ctl_set.empty()) return;
    double max_rtt_seconds = 0.0;
    for (auto *flow : m_rate_flow_ctl_set) {
        max_rtt_seconds = std::max(max_rtt_seconds, flow->m_base_rtt_sec);
    }
    if (m_guardPendingGrantAcks == 0) return;
    if (max_rtt_seconds <= 0) max_rtt_seconds = 8.32e-6;
    Time interval = Seconds(m_guardGrantReliabilityRtts * max_rtt_seconds *
                            m_rate_flow_ctl_set.size());
    if (interval <= m_guardMembershipCoalesceWindow) {
        interval = m_guardMembershipCoalesceWindow + NanoSeconds(1);
    }
    m_guardReliabilityRefreshEvent = Simulator::Schedule(
        interval, &RdmaHw::RefreshGuardGrantsForReliability, this);
}

void RdmaHw::RefreshGuardGrantsForReliability() {
    if (m_rate_flow_ctl_set.empty()) return;
    if (m_guardMembershipEvent.IsRunning()) {
        ResetGuardReliabilityRefresh();
        return;
    }
    if (m_guardPendingGrantAcks == 0) return;
    m_guardReliabilityRefreshEvents++;
    for (auto *flow : m_rate_flow_ctl_set) {
        if (flow->m_guard_grant_generation != m_guardGrantGeneration ||
            flow->m_guard_grant_generation_acked) {
            continue;
        }
        uint32_t rate_mbps = 0;
        if (m_guardMixedPgVectorFastpath) {
            auto ledger = m_guardGenerationLedger.find(flow);
            NS_ABORT_MSG_IF(
                ledger == m_guardGenerationLedger.end() ||
                    ledger->second.tombstone ||
                    ledger->second.generation != m_guardGrantGeneration,
                "GUARD V14 required retry lost its frozen generation ledger");
            rate_mbps = ledger->second.targetMbps;
        } else if (flow->m_guard_grant_rate_bps > 0) {
            rate_mbps = std::max<uint32_t>(
                1, flow->m_guard_grant_rate_bps / 1000000);
        }
        if (rate_mbps == 0) continue;
        if (m_guardMixedPgVectorFastpath) AssertGuardFrozenPotentialFits();
        SendRateControlPacket(flow, rate_mbps, "reliability_refresh",
                              m_guardGrantGeneration, true);
        m_guardReliabilityGrantUpdates++;
    }
    ResetGuardReliabilityRefresh();
}

void RdmaHw::RedistributeGuardRates(const char *set_change, uint32_t generation) {
    if (m_rate_flow_ctl_set.empty()) return;
    if (generation == 0 && IsGuardMembershipDirty()) {
        m_guardProgressEventsCoalesced++;
        return;
    }
    RdmaRxQueuePair *sample = *m_rate_flow_ctl_set.begin();
    uint32_t nic_idx = GetNicIdxOfRxQp(sample);
    uint64_t line_rate_bps = m_nic[nic_idx].dev->GetDataRate().GetBitRate();
    std::unordered_map<RdmaRxQueuePair*, uint64_t> targets =
        m_guardCapAwareReclaim
            ? ComputeGuardCapAwareTargets(line_rate_bps)
            : ComputeGuardBaseTargets(line_rate_bps);
    std::vector<uint64_t> concurrency_remaining;
    if (m_guardReceiverConcurrency > 0) {
        for (auto *flow : m_rate_flow_ctl_set) {
            uint64_t bdp_bytes = flow->m_base_rtt_sec > 0
                                     ? (uint64_t)(flow->m_base_rtt_sec * line_rate_bps / 8.0)
                                     : 104000;
            if (flow->m_guard_flow_size > m_guardConcurrencyMinBdps * bdp_bytes) {
                concurrency_remaining.push_back(
                    flow->m_guard_flow_size > flow->ReceiverNextExpectedSeq
                        ? flow->m_guard_flow_size - flow->ReceiverNextExpectedSeq : 1);
            }
        }
    }
    std::sort(concurrency_remaining.begin(), concurrency_remaining.end());
    size_t concurrency_candidates = concurrency_remaining.size();
    uint32_t effective_concurrency = ComputeGuardEffectiveElephantConcurrency(
        m_guardReceiverConcurrency, concurrency_candidates,
        m_guardAdaptiveElephantConcurrency);
    if (m_guardSizeClassElephantConcurrency) {
        effective_concurrency = ComputeGuardSizeClassElephantConcurrency(
            m_guardReceiverConcurrency, concurrency_candidates,
            concurrency_candidates > 0 ? concurrency_remaining[0] : 0,
            concurrency_candidates > 1 ? concurrency_remaining[1] : 0, true);
    }
    if (m_guardAdaptiveElephantConcurrency && effective_concurrency > 1) {
        m_guardAdaptiveConcurrencyPromotions++;
        m_guardAdaptiveConcurrencyMaxEffective = std::max<uint64_t>(
            m_guardAdaptiveConcurrencyMaxEffective, effective_concurrency);
    }
    if (m_guardSizeClassElephantConcurrency && effective_concurrency > 1) {
        m_guardSizeClassConcurrencyPromotions++;
        m_guardSizeClassConcurrencyMaxEffective = std::max<uint64_t>(
            m_guardSizeClassConcurrencyMaxEffective, effective_concurrency);
    }
    if (concurrency_candidates > effective_concurrency) {
        m_guardConcurrencyLimitedAllocations++;
        m_guardConcurrencyMaxDeferredFlows = std::max<uint64_t>(
            m_guardConcurrencyMaxDeferredFlows,
            concurrency_candidates - effective_concurrency);
    }
    std::unordered_map<RdmaRxQueuePair*, uint32_t> encoded_rates;
    bool ack_required_generation = false;
    for (auto *flow : m_rate_flow_ctl_set) {
        uint32_t rate_mbps = std::max<uint32_t>(1, targets[flow] / 1000000);
        encoded_rates[flow] = rate_mbps;
        uint64_t rate_bps = static_cast<uint64_t>(rate_mbps) * 1000000;
        if (generation > 0 &&
            (flow->m_guard_grant_generation == 0 ||
             flow->m_guard_grant_upper_bound_bps == 0 ||
             rate_bps < flow->m_guard_grant_upper_bound_bps)) {
            ack_required_generation = true;
        }
    }
    if (generation > 0) {
        m_guardPendingGrantAcks = ack_required_generation
                                     ? m_rate_flow_ctl_set.size()
                                     : 0;
        if (ack_required_generation) {
            m_guardAckRequiredBatches++;
        } else {
            m_guardAckOptionalBatches++;
        }
        for (auto *flow : m_rate_flow_ctl_set) {
            flow->m_guard_grant_generation = generation;
            flow->m_guard_grant_generation_acked = !ack_required_generation;
            flow->m_guard_last_issued_generation = generation;
            flow->m_guard_last_issued_target_bps =
                static_cast<uint64_t>(encoded_rates[flow]) * 1000000ULL;
            if (!ack_required_generation) {
                uint64_t rate_bps =
                    static_cast<uint64_t>(encoded_rates[flow]) * 1000000;
                flow->m_guard_grant_upper_bound_bps = std::max(
                    flow->m_guard_grant_upper_bound_bps, rate_bps);
                flow->m_guard_last_acked_generation = generation;
                flow->m_guard_last_acked_upper_bound_bps =
                    flow->m_guard_grant_upper_bound_bps;
            }
        }
    }
    for (auto *flow : m_rate_flow_ctl_set) {
        flow->m_guard_interval_bytes = 0;
        flow->m_guard_demand_samples = 0;
        flow->m_guard_below_threshold_samples = 0;
        flow->m_guard_demand_limited = false;
        uint32_t rate_mbps = encoded_rates[flow];
        flow->m_guard_grant_rate_bps = (uint64_t)rate_mbps * 1000000;
        SendRateControlPacket(flow, rate_mbps, set_change, generation,
                              ack_required_generation);
    }
    m_guardUnderutilizedSamples = 0;
    if (m_guardRebalanceEvent.IsRunning()) Simulator::Cancel(m_guardRebalanceEvent);
    m_guardLastRebalanceTime = Time(0);
}

std::unordered_map<RdmaRxQueuePair*, uint64_t> RdmaHw::ComputeGuardBaseTargets(
    uint64_t line_rate_bps) const {
    std::unordered_map<RdmaRxQueuePair*, uint64_t> targets;
    size_t count = m_rate_flow_ctl_set.size();
    if (count == 0) return targets;
    uint64_t equal_share = line_rate_bps / count;
    std::vector<RdmaRxQueuePair*> concurrency_candidates;
    if (m_guardReceiverConcurrency > 0) {
        for (auto *flow : m_rate_flow_ctl_set) {
            uint64_t bdp_bytes = flow->m_base_rtt_sec > 0
                                     ? (uint64_t)(flow->m_base_rtt_sec * line_rate_bps / 8.0)
                                     : 104000;
            if (flow->m_guard_flow_size > m_guardConcurrencyMinBdps * bdp_bytes)
                concurrency_candidates.push_back(flow);
        }
    }
    std::sort(concurrency_candidates.begin(), concurrency_candidates.end(),
              [](RdmaRxQueuePair *left, RdmaRxQueuePair *right) {
                  uint64_t left_remaining =
                      left->m_guard_flow_size > left->ReceiverNextExpectedSeq
                          ? left->m_guard_flow_size - left->ReceiverNextExpectedSeq : 0;
                  uint64_t right_remaining =
                      right->m_guard_flow_size > right->ReceiverNextExpectedSeq
                          ? right->m_guard_flow_size - right->ReceiverNextExpectedSeq : 0;
                  if (left_remaining != right_remaining)
                      return left_remaining < right_remaining;
                  return left->m_flow_id < right->m_flow_id;
              });
    uint32_t effective_concurrency = ComputeGuardEffectiveElephantConcurrency(
        m_guardReceiverConcurrency, concurrency_candidates.size(),
        m_guardAdaptiveElephantConcurrency);
    if (m_guardSizeClassElephantConcurrency) {
        uint64_t first = concurrency_candidates.size() > 0
            ? std::max<uint64_t>(1,
                  concurrency_candidates[0]->m_guard_flow_size >
                          concurrency_candidates[0]->ReceiverNextExpectedSeq
                      ? concurrency_candidates[0]->m_guard_flow_size -
                            concurrency_candidates[0]->ReceiverNextExpectedSeq
                      : 1) : 0;
        uint64_t second = concurrency_candidates.size() > 1
            ? std::max<uint64_t>(1,
                  concurrency_candidates[1]->m_guard_flow_size >
                          concurrency_candidates[1]->ReceiverNextExpectedSeq
                      ? concurrency_candidates[1]->m_guard_flow_size -
                            concurrency_candidates[1]->ReceiverNextExpectedSeq
                      : 1) : 0;
        effective_concurrency = ComputeGuardSizeClassElephantConcurrency(
            m_guardReceiverConcurrency, concurrency_candidates.size(), first, second, true);
    }
    size_t service_count = std::min<size_t>(concurrency_candidates.size(),
                                             effective_concurrency);
    std::unordered_set<RdmaRxQueuePair*> service_set;
    if (m_guardReceiverConcurrency > 0 &&
        service_count < concurrency_candidates.size()) {
        for (auto *flow : m_rate_flow_ctl_set) {
            if (std::find(concurrency_candidates.begin(), concurrency_candidates.end(), flow) ==
                concurrency_candidates.end()) {
                service_set.insert(flow);
            }
        }
        service_set.insert(concurrency_candidates.begin(),
                           concurrency_candidates.begin() + service_count);

        // Preserve a bounded starvation floor for deferred elephants, then
        // share the remaining receiver capacity among all shorter flows and
        // only the K shortest elephants.
        size_t deferred_count = concurrency_candidates.size() - service_count;
        uint64_t minimum_rate = std::min<uint64_t>(
            m_minRate.GetBitRate(), line_rate_bps / count);
        uint64_t residual = line_rate_bps - minimum_rate * deferred_count;
        double weight_sum = 0.0;
        uint64_t min_remaining = std::numeric_limits<uint64_t>::max();
        for (auto *flow : service_set) {
            uint64_t remaining = flow->m_guard_flow_size > flow->ReceiverNextExpectedSeq
                                     ? flow->m_guard_flow_size - flow->ReceiverNextExpectedSeq
                                     : 1;
            min_remaining = std::min(min_remaining,
                                     std::max<uint64_t>(remaining, m_mtu));
        }
        std::unordered_map<RdmaRxQueuePair*, double> weights;
        for (auto *flow : service_set) {
            uint64_t remaining = flow->m_guard_flow_size > flow->ReceiverNextExpectedSeq
                                     ? flow->m_guard_flow_size - flow->ReceiverNextExpectedSeq
                                     : 1;
            remaining = std::max<uint64_t>(remaining, m_mtu);
            double weight = (!m_guardRemainingAware || m_guardRemainingExponent == 0.0)
                                ? 1.0
                                : std::pow((double)min_remaining / remaining,
                                           m_guardRemainingExponent);
            weights[flow] = weight;
            weight_sum += weight;
        }
        for (auto *flow : m_rate_flow_ctl_set) {
            targets[flow] = service_set.find(flow) == service_set.end() ? minimum_rate : 0;
            if (service_set.find(flow) != service_set.end()) {
                targets[flow] = (uint64_t)(
                    residual * weights[flow] / std::max(weight_sum, 1e-12));
            }
        }
        return targets;
    }
    if (!m_guardRemainingAware || count == 1 || m_guardRemainingExponent == 0.0) {
        for (auto *flow : m_rate_flow_ctl_set) targets[flow] = equal_share;
        return targets;
    }

    uint64_t min_remaining = std::numeric_limits<uint64_t>::max();
    for (auto *flow : m_rate_flow_ctl_set) {
        uint64_t remaining = flow->m_guard_flow_size > flow->ReceiverNextExpectedSeq
                                 ? flow->m_guard_flow_size - flow->ReceiverNextExpectedSeq
                                 : 1;
        min_remaining = std::min(min_remaining, std::max<uint64_t>(remaining, m_mtu));
    }
    std::unordered_map<RdmaRxQueuePair*, double> weights;
    double weight_sum = 0.0;
    for (auto *flow : m_rate_flow_ctl_set) {
        uint64_t remaining = flow->m_guard_flow_size > flow->ReceiverNextExpectedSeq
                                 ? flow->m_guard_flow_size - flow->ReceiverNextExpectedSeq
                                 : 1;
        remaining = std::max<uint64_t>(remaining, m_mtu);
        double weight = std::pow((double)min_remaining / remaining,
                                 m_guardRemainingExponent);
        weights[flow] = weight;
        weight_sum += weight;
    }
    uint64_t floor_share = (uint64_t)(m_guardMinShareFraction * equal_share);
    uint64_t residual = line_rate_bps - floor_share * count;
    for (auto *flow : m_rate_flow_ctl_set) {
        targets[flow] = floor_share +
            (uint64_t)(residual * weights[flow] / std::max(weight_sum, 1e-12));
    }
    return targets;
}

std::unordered_map<RdmaRxQueuePair*, uint64_t> RdmaHw::ComputeGuardCapAwareTargets(
    uint64_t line_rate_bps) const {
    std::unordered_map<RdmaRxQueuePair*, uint64_t> base =
        ComputeGuardBaseTargets(line_rate_bps);
    std::unordered_map<RdmaRxQueuePair*, uint64_t> targets;
    if (base.empty()) return targets;

    const uint64_t unlimited = std::numeric_limits<uint64_t>::max();
    std::unordered_map<RdmaRxQueuePair*, uint64_t> demands;
    size_t capped = 0;
    Time now = Simulator::Now();
    for (auto *flow : m_rate_flow_ctl_set) {
        Time freshness = MilliSeconds(1);
        if (flow->m_base_rtt_sec > 0) {
            freshness = std::max(freshness, Seconds(32.0 * flow->m_base_rtt_sec));
        }
        bool fresh = !flow->m_guard_last_cap_report_time.IsZero() &&
                     now - flow->m_guard_last_cap_report_time <= freshness;
        if (!fresh || flow->m_guard_cap_report_samples < 3 ||
            (!flow->m_guard_cap_limited && flow->m_guard_unbound_reports < 3)) {
            return base;
        }
        if (fresh && flow->m_guard_cap_limited &&
            flow->m_guard_reported_rate_bps > 0) {
            long double demand = m_guardCapHeadroom *
                                 (long double)flow->m_guard_reported_rate_bps;
            uint64_t safety_floor = (uint64_t)(
                m_guardCapMinShareFraction *
                ((long double)line_rate_bps / m_rate_flow_ctl_set.size()));
            demands[flow] = std::max<uint64_t>(
                std::max<uint64_t>(1000000, safety_floor),
                std::min<uint64_t>(line_rate_bps, (uint64_t)demand));
            capped++;
        } else {
            demands[flow] = unlimited;
        }
    }

    // Reclamation needs both a constrained donor and a recipient.  If either
    // side is absent, restore the ordinary remaining-aware allocation.
    if (capped == 0 || capped == m_rate_flow_ctl_set.size()) return base;

    // Reclamation and remaining-size priority must not reinforce each other:
    // after one flow donates capacity, inverse-remaining weights would give
    // the recipient still more share and can starve the donor.  Use equal
    // weights within the demand-capped water fill; the ordinary scheduler is
    // restored as soon as the mixed-cap condition disappears.
    std::unordered_map<RdmaRxQueuePair*, uint64_t> weights;
    for (auto *flow : m_rate_flow_ctl_set) weights[flow] = 1;
    std::unordered_set<RdmaRxQueuePair*> unassigned = m_rate_flow_ctl_set;
    uint64_t remaining = line_rate_bps;
    while (!unassigned.empty()) {
        long double weight_sum = 0.0;
        for (auto *flow : unassigned) weight_sum += weights[flow];

        std::vector<RdmaRxQueuePair*> newly_capped;
        for (auto *flow : unassigned) {
            uint64_t tentative = (uint64_t)(
                (long double)remaining * weights[flow] /
                std::max<long double>(weight_sum, 1.0));
            if (demands[flow] != unlimited && demands[flow] < tentative) {
                targets[flow] = demands[flow];
                newly_capped.push_back(flow);
            }
        }
        if (newly_capped.empty()) {
            for (auto *flow : unassigned) {
                targets[flow] = (uint64_t)(
                    (long double)remaining * weights[flow] /
                    std::max<long double>(weight_sum, 1.0));
            }
            break;
        }
        for (auto *flow : newly_capped) {
            remaining = remaining > targets[flow] ? remaining - targets[flow] : 0;
            unassigned.erase(flow);
        }
    }
    return targets;
}

void RdmaHw::ApplyGuardCapAwareRates() {
    if (!m_guardCapAwareReclaim || m_rate_flow_ctl_set.size() < 2) return;
    if (IsGuardMembershipDirty()) return;
    RdmaRxQueuePair *sample = *m_rate_flow_ctl_set.begin();
    uint32_t nic_idx = GetNicIdxOfRxQp(sample);
    uint64_t line_rate_bps = m_nic[nic_idx].dev->GetDataRate().GetBitRate();
    std::unordered_map<RdmaRxQueuePair*, uint64_t> targets =
        ComputeGuardCapAwareTargets(line_rate_bps);

    std::vector<std::pair<RdmaRxQueuePair*, uint32_t> > updates;
    uint64_t reclaimed_bps = 0;
    for (auto *flow : m_rate_flow_ctl_set) {
        uint64_t target = std::max<uint64_t>(1000000, targets[flow]);
        uint64_t current = flow->m_guard_grant_rate_bps;
        uint64_t difference = target > current ? target - current : current - target;
        uint64_t threshold = std::max<uint64_t>(100000000, current / 20);
        if (difference < threshold) continue;
        uint32_t rate_mbps = std::max<uint32_t>(1, target / 1000000);
        uint64_t encoded = (uint64_t)rate_mbps * 1000000;
        if (encoded > current) reclaimed_bps += encoded - current;
        updates.push_back(std::make_pair(flow, rate_mbps));
    }
    if (updates.empty()) return;

    m_guardCapRebalanceEvents++;
    m_guardCapMaxReclaimedBps = std::max(m_guardCapMaxReclaimedBps, reclaimed_bps);
    for (auto const &update : updates) {
        update.first->m_guard_grant_rate_bps = (uint64_t)update.second * 1000000;
        SendRateControlPacket(update.first, update.second, "cap_report");
        m_guardCapGrantUpdates++;
    }
}

void RdmaHw::ScheduleGuardRebalance() {
    if (!m_guardWorkConserving || m_rate_flow_ctl_set.size() < 2 ||
        m_guardRebalanceEvent.IsRunning()) {
        return;
    }
    m_guardLastRebalanceTime = Simulator::Now();
    m_guardRebalanceEvent = Simulator::Schedule(
        m_guardRebalanceInterval, &RdmaHw::RebalanceGuardRates, this);
}

void RdmaHw::RebalanceGuardRates() {
    if (!m_guardWorkConserving || m_rate_flow_ctl_set.size() < 2) {
        m_guardLastRebalanceTime = Time(0);
        return;
    }
    if (IsGuardMembershipDirty()) {
        m_guardProgressEventsCoalesced++;
        m_guardRebalanceEvent = Simulator::Schedule(
            m_guardRebalanceInterval, &RdmaHw::RebalanceGuardRates, this);
        return;
    }
    Time now = Simulator::Now();
    Time elapsed = now - m_guardLastRebalanceTime;
    m_guardLastRebalanceTime = now;
    if (elapsed.IsZero()) {
        m_guardRebalanceEvent = Simulator::Schedule(
            m_guardRebalanceInterval, &RdmaHw::RebalanceGuardRates, this);
        return;
    }

    RdmaRxQueuePair *sample = *m_rate_flow_ctl_set.begin();
    uint32_t nic_idx = GetNicIdxOfRxQp(sample);
    uint64_t line_rate_bps = m_nic[nic_idx].dev->GetDataRate().GetBitRate();
    const uint64_t unlimited = std::numeric_limits<uint64_t>::max();
    uint64_t aggregate_measured = 0;
    for (auto *flow : m_rate_flow_ctl_set) {
        uint64_t measured = (uint64_t)(
            (double)flow->m_guard_interval_bytes * 8.0 / elapsed.GetSeconds());
        flow->m_guard_interval_bytes = 0;
        flow->m_guard_measured_rate_bps = measured;
        flow->m_guard_demand_samples++;
        aggregate_measured += measured;
    }
    if ((double)aggregate_measured < m_guardReceiverUtilThreshold * line_rate_bps) {
        m_guardUnderutilizedSamples++;
    } else {
        m_guardUnderutilizedSamples = 0;
    }
    bool receiver_underutilized = m_guardUnderutilizedSamples >= 3;

    std::vector<std::pair<uint64_t, RdmaRxQueuePair*> > demands;
    demands.reserve(m_rate_flow_ctl_set.size());
    bool has_unlimited_demand = false;
    for (auto *flow : m_rate_flow_ctl_set) {
        uint64_t measured = flow->m_guard_measured_rate_bps;

        uint64_t grant = std::max<uint64_t>(flow->m_guard_grant_rate_bps, 1);
        if (m_guardUnderutilizedSamples > 0 &&
            (double)measured < m_guardDemandThreshold * grant) {
            flow->m_guard_below_threshold_samples++;
        } else {
            flow->m_guard_below_threshold_samples = 0;
        }
        if (!receiver_underutilized) {
            flow->m_guard_demand_limited = false;
        } else if (flow->m_guard_demand_limited) {
            if ((double)measured >= 0.95 * grant) {
                flow->m_guard_demand_limited = false;
                flow->m_guard_below_threshold_samples = 0;
            }
        } else if (flow->m_guard_below_threshold_samples >= 3) {
            flow->m_guard_demand_limited = true;
            flow->m_guard_below_threshold_samples = 0;
        }
        uint64_t demand = unlimited;
        if (flow->m_guard_demand_limited) {
            demand = std::min<uint64_t>(
                line_rate_bps, (uint64_t)(measured / m_guardDemandThreshold));
        } else {
            has_unlimited_demand = true;
        }
        demands.push_back(std::make_pair(demand, flow));
    }

    std::sort(demands.begin(), demands.end(),
              [](const std::pair<uint64_t, RdmaRxQueuePair*> &left,
                 const std::pair<uint64_t, RdmaRxQueuePair*> &right) {
                  return left.first < right.first;
              });
    uint64_t remaining = line_rate_bps;
    size_t unassigned = demands.size();
    std::unordered_map<RdmaRxQueuePair*, uint64_t> targets;
    for (auto const &entry : demands) {
        uint64_t fair_share = unassigned == 0 ? 0 : remaining / unassigned;
        if (entry.first != unlimited && entry.first < fair_share) {
            targets[entry.second] = entry.first;
            remaining -= entry.first;
            unassigned--;
        } else {
            break;
        }
    }
    uint64_t residual_share = unassigned == 0 ? 0 : remaining / unassigned;
    for (auto const &entry : demands) {
        if (targets.find(entry.second) == targets.end()) {
            targets[entry.second] = residual_share;
        }
    }

    m_guardRebalanceEvents++;
    bool update_vector = false;
    for (auto *flow : m_rate_flow_ctl_set) {
        uint64_t target = std::max<uint64_t>(1000000, targets[flow]);
        uint64_t current = flow->m_guard_grant_rate_bps;
        uint64_t difference = target > current ? target - current : current - target;
        uint64_t threshold = std::max<uint64_t>(100000000, current / 20);
        if (difference >= threshold) update_vector = true;
    }
    if (update_vector && has_unlimited_demand) {
        for (auto *flow : m_rate_flow_ctl_set) {
            uint64_t target = std::max<uint64_t>(1000000, targets[flow]);
            uint32_t rate_mbps = std::max<uint32_t>(1, target / 1000000);
            flow->m_guard_grant_rate_bps = (uint64_t)rate_mbps * 1000000;
            SendRateControlPacket(flow, rate_mbps, "demand");
            m_guardAdaptiveGrantUpdates++;
        }
    }

    m_guardRebalanceEvent = Simulator::Schedule(
        m_guardRebalanceInterval, &RdmaHw::RebalanceGuardRates, this);
}

void RdmaHw::ConfigureGuardLifecycleTrace(GuardLifecycleTraceSink *sink) {
    m_guardLifecycleTraceSink = sink;
}

void RdmaHw::ConfigureGuardControllerTrace(GuardControllerTraceSink *sink) {
    m_guardControllerTraceSink = sink;
}

void RdmaHw::ConfigureGuardGrantTrace(GuardGrantTraceSink *sink) {
    m_guardGrantTraceSink = sink;
}

void RdmaHw::ConfigureGuardTransitionAudit(GuardTransitionAuditSink *sink) {
    NS_ABORT_MSG_IF(m_guardTransitionAuditRecord.active,
                    "GUARD V14 transition audit sink changed mid-record");
    m_guardTransitionAuditSink = sink;
}

void RdmaHw::TraceGuardControllerEvent(Ptr<RdmaQueuePair> qp, const char *event_type,
                                       DataRate hpcc_rate, const char *binding,
                                       bool rate_changed, bool fast_react, uint32_t nhop,
                                       uint32_t next_seq, double congestion_metric,
                                       double effective_target, double threshold_ratio) {
    GuardControllerTraceSink *sink = m_guardControllerTraceSink;
    if (sink == NULL || sink->file == NULL) return;
    sink->attempted++;
    if (sink->written >= sink->max_lines) return;
    fprintf(sink->file,
            "%ld,%d,%u,%u,%s,%lu,%lu,%lu,%s,%u,%u,%u,%u,%.9f,%.9f,%.9f\n",
            Simulator::Now().GetNanoSeconds(), qp->m_flow_id, qp->sip.Get(), qp->dip.Get(),
            event_type, hpcc_rate.GetBitRate(), qp->hp.m_grantRate.GetBitRate(),
            qp->m_rate.GetBitRate(), binding, rate_changed ? 1 : 0, fast_react ? 1 : 0,
            nhop, next_seq, congestion_metric, effective_target, threshold_ratio);
    sink->written++;
}

void RdmaHw::TraceGuardRegistration(Ptr<RdmaRxQueuePair> rx_qp, uint64_t flow_size,
                                     uint64_t active_before, uint64_t active_after) {
    GuardLifecycleTraceSink *sink = m_guardLifecycleTraceSink;
    if (sink == NULL || sink->file == NULL || sink->admitted >= sink->max_lines) return;

    GuardLifecycleState state;
    state.flow_id = rx_qp->m_flow_id;
    state.size_bytes = flow_size;
    state.receiver_node = m_node->GetId();
    state.first_rx_ns = rx_qp->m_first_pkt_time.GetNanoSeconds();
    state.register_ns = Simulator::Now().GetNanoSeconds();
    state.release_ns = -1;
    state.complete_ns = -1;
    state.release_reason = "not_released";
    state.remaining_bytes_at_release = 0;
    state.active_before_register = active_before;
    state.active_after_register = active_after;
    state.active_before_release = -1;
    state.active_after_release = -1;
    m_guardLifecycleStates.emplace(PeekPointer(rx_qp), state);
    sink->admitted++;
}

void RdmaHw::TraceGuardRelease(Ptr<RdmaRxQueuePair> rx_qp, GuardReleaseReason reason,
                                uint64_t remaining_bytes, uint64_t active_before,
                                uint64_t active_after) {
    auto it = m_guardLifecycleStates.find(PeekPointer(rx_qp));
    if (it == m_guardLifecycleStates.end()) return;

    GuardLifecycleState &state = it->second;
    state.release_ns = Simulator::Now().GetNanoSeconds();
    state.release_reason = reason == GUARD_RELEASE_PROACTIVE ? "proactive" : "completion";
    state.remaining_bytes_at_release = remaining_bytes;
    state.active_before_release = active_before;
    state.active_after_release = active_after;
}

void RdmaHw::TraceGuardCompletion(Ptr<RdmaRxQueuePair> rx_qp) {
    auto it = m_guardLifecycleStates.find(PeekPointer(rx_qp));
    if (it == m_guardLifecycleStates.end()) return;

    it->second.complete_ns = Simulator::Now().GetNanoSeconds();
    WriteGuardLifecycle(it->second);
    m_guardLifecycleStates.erase(it);
}

void RdmaHw::WriteGuardLifecycle(GuardLifecycleState const &state) {
    GuardLifecycleTraceSink *sink = m_guardLifecycleTraceSink;
    if (sink == NULL || sink->file == NULL || sink->written >= sink->max_lines) return;
    fprintf(sink->file,
            "%d,%lu,%u,%ld,%ld,%ld,%ld,%s,%lu,%ld,%ld,%ld,%ld\n",
            state.flow_id, state.size_bytes, state.receiver_node, state.first_rx_ns,
            state.register_ns, state.release_ns, state.complete_ns,
            state.release_reason.c_str(), state.remaining_bytes_at_release,
            state.active_before_register, state.active_after_register,
            state.active_before_release, state.active_after_release);
    sink->written++;
}

void RdmaHw::FlushGuardLifecycleTrace() {
    for (auto const &entry : m_guardLifecycleStates) {
        GuardLifecycleState state = entry.second;
        if (state.release_ns < 0) {
            RdmaRxQueuePair const *rx_qp = entry.first;
            state.remaining_bytes_at_release =
                state.size_bytes > rx_qp->ReceiverNextExpectedSeq
                    ? state.size_bytes - rx_qp->ReceiverNextExpectedSeq
                    : 0;
        }
        WriteGuardLifecycle(state);
    }
    m_guardLifecycleStates.clear();
}

void RdmaHw::AppendGuardFastpathTraceFields(
    FILE *file, const char *event, const char *set_change,
    RdmaRxQueuePair *qp, uint8_t phase_tag, bool receiver_authoritative,
    uint64_t grant_rate_bps, const GuardRetiredAckRecord *retired) {
    if (!GuardSmallSetFastpathEnabled()) return;
    const char *phase = GetGuardGrantPhaseTagName(phase_tag);
    const char *role = "none";
    if (phase_tag == GUARD_GRANT_PHASE_FAST_PREPARE ||
        phase_tag == GUARD_GRANT_PHASE_TRANSITION_PREPARE ||
        phase_tag == GUARD_GRANT_PHASE_RELEASE) {
        role = "incumbent";
    } else if (phase_tag == GUARD_GRANT_PHASE_FAST_ACTIVATE ||
               phase_tag == GUARD_GRANT_PHASE_TRANSITION_ACTIVATE) {
        role = "waiter";
    }
    uint64_t transaction_id = 0;
    uint64_t membership_target_n = 0;
    if (retired != NULL) {
        transaction_id = retired->transactionId;
    } else if (receiver_authoritative &&
               phase_tag != GUARD_GRANT_PHASE_NONE) {
        if (phase_tag == GUARD_GRANT_PHASE_RELEASE) {
            membership_target_n = m_guardFastpathIncumbents.size();
        } else {
            transaction_id = m_guardFastpathTransaction;
            membership_target_n = m_guardFastpathTransactionTargetN;
        }
    }
    fprintf(file, ",%lu,%s,%lu,%s", transaction_id, phase,
            membership_target_n, role);
    if (m_guardTransitionPrefixBarrierEnabled) {
        uint64_t prefix_target_bytes = 0;
        uint64_t prefix_observed_bytes = 0;
        const char *drain_outcome = "none";
        uint64_t activation_batch_index = 0;
        uint64_t activation_batch_size = 0;
        int64_t register_ns = -1;
        if (receiver_authoritative && qp != NULL &&
            phase_tag == GUARD_GRANT_PHASE_TRANSITION_ACTIVATE) {
            auto target_it = m_guardTransitionPrefixTargets.find(qp);
            if (target_it != m_guardTransitionPrefixTargets.end()) {
                prefix_target_bytes = target_it->second;
                prefix_observed_bytes = std::min<uint64_t>(
                    prefix_target_bytes, qp->ReceiverNextExpectedSeq);
            }
            drain_outcome = m_guardTransitionPrefixTimedOut
                                ? "timeout"
                                : m_guardTransitionPrefixBarrierResolved
                                      ? "ready"
                                      : "none";
            activation_batch_index = m_guardTransitionActivationBatchIndex;
            activation_batch_size = m_guardTransitionActivationBatchSize;
            register_ns = qp->m_guard_register_ns;
        }
        fprintf(file, ",%lu,%lu,%s,%lu,%lu,%ld",
                prefix_target_bytes, prefix_observed_bytes, drain_outcome,
                activation_batch_index, activation_batch_size, register_ns);
    }
    if (m_guardSerializedProgressRefresh && m_guardSerializedDraining) {
        bool snapshot_authoritative =
            receiver_authoritative && retired == NULL;
        bool snapshot_valid =
            receiver_authoritative && m_guardFrozenVectorActive &&
            retired == NULL;
        uint64_t live_active_records = 0;
        uint64_t live_draining_records = 0;
        __uint128_t live_draining_wide = 0;
        bool capacity_recompute_pending = false;
        if (snapshot_authoritative) {
            live_active_records = m_rate_flow_ctl_set.size();
            for (const auto &item : m_guardDrainingRecords) {
                if (item.second.state == GUARD_DRAINING) {
                    live_draining_wide += item.second.reservedBps;
                    live_draining_records++;
                }
            }
            capacity_recompute_pending =
                m_guardCapacityDirty ||
                m_guardProgressTransactionCapacityDirty;
        }
        NS_ABORT_MSG_IF(
            live_draining_wide > std::numeric_limits<uint64_t>::max(),
            "GUARD V14 trace live draining reservation sum overflow");
        uint64_t live_draining_bps =
            static_cast<uint64_t>(live_draining_wide);
        if (snapshot_valid) {
            NS_ABORT_MSG_IF(
                !ValidateGuardGrantTraceSnapshot(
                    m_guardFrozenReceiverCapacityBps,
                    m_guardFrozenDrainingReservedBps,
                    m_guardFrozenAllocatableCapacityBps,
                    m_guardFrozenEncodedTargetBps,
                    std::string(event) == "sent" ? grant_rate_bps : 0,
                    live_draining_bps, capacity_recompute_pending),
                "GUARD V14 grant trace snapshot violates frozen/live capacity provenance");
        }
        fprintf(file,
                ",%lu,%lu,%lu,%u,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%u,%lu",
                snapshot_valid ? m_guardFrozenAllocationRevision : 0,
                snapshot_valid ? m_guardFrozenProgressRevision : 0,
                snapshot_valid ? m_guardFrozenMembershipRevision : 0,
                snapshot_valid ? 1 : 0,
                snapshot_valid ? m_guardFrozenReceiverCapacityBps : 0,
                snapshot_valid ? m_guardFrozenActiveRecords : 0,
                snapshot_valid ? m_guardFrozenDrainingRecords : 0,
                snapshot_valid ? m_guardFrozenDrainingReservedBps : 0,
                snapshot_valid ? m_guardFrozenAllocatableCapacityBps : 0,
                snapshot_valid ? m_guardFrozenEncodedTargetBps : 0,
                snapshot_authoritative ? live_active_records : 0,
                snapshot_authoritative ? live_draining_records : 0,
                snapshot_authoritative ? live_draining_bps : 0,
                snapshot_authoritative && capacity_recompute_pending ? 1 : 0,
                snapshot_valid ? m_guardFrozenReasonMask : 0);
    }
}

void RdmaHw::TraceGuardGrant(Ptr<RdmaRxQueuePair> qp, const char *event,
                             const char *set_change, uint64_t active_flows,
                             uint64_t line_rate_bps, uint64_t grant_rate_bps,
                             uint64_t next_seq, uint64_t serialized_bytes,
                             uint32_t generation, uint64_t pending_acks,
                             bool ack_required, uint8_t phase_tag) {
    GuardGrantTraceSink *sink = m_guardGrantTraceSink;
    if (sink == NULL || sink->file == NULL) return;
    sink->attempted++;
    if (sink->written >= sink->max_lines) return;
    fprintf(sink->file, "%ld,%s,%s,%u,%d,%u,%u,%lu,%lu,%lu,%lu,%lu,%u,%lu,%u",
            Simulator::Now().GetNanoSeconds(), event, set_change, m_node->GetId(),
            qp->m_flow_id, qp->dip, qp->sip, active_flows, line_rate_bps,
            grant_rate_bps, next_seq, serialized_bytes, generation, pending_acks,
            ack_required ? 1 : 0);
    AppendGuardFastpathTraceFields(
        sink->file, event, set_change, PeekPointer(qp),
        phase_tag, true, grant_rate_bps);
    fprintf(sink->file, "\n");
    sink->written++;
}

void RdmaHw::TraceGuardGrantReceive(Ptr<RdmaQueuePair> qp, Ptr<Packet> packet,
                                    uint64_t grant_rate_bps, uint32_t generation,
                                    const char *event, bool ack_required,
                                    uint8_t phase_tag) {
    GuardGrantTraceSink *sink = m_guardGrantTraceSink;
    if (sink == NULL || sink->file == NULL) return;
    sink->attempted++;
    if (sink->written >= sink->max_lines) return;
    uint64_t line_rate_bps = 0;
    uint32_t nic_idx = GetNicIdxOfQp(qp);
    if (nic_idx < m_nic.size() && m_nic[nic_idx].dev != NULL) {
        line_rate_bps = m_nic[nic_idx].dev->GetDataRate().GetBitRate();
    }
    fprintf(sink->file, "%ld,%s,none,%u,%d,%u,%u,0,%lu,%lu,%lu,%u,%u,0,%u",
            Simulator::Now().GetNanoSeconds(), event, m_node->GetId(), qp->m_flow_id,
            qp->sip.Get(), qp->dip.Get(), line_rate_bps, grant_rate_bps,
            qp->snd_nxt, packet->GetSize(), generation, ack_required ? 1 : 0);
    AppendGuardFastpathTraceFields(sink->file, event, "none", NULL,
                                   phase_tag, false, grant_rate_bps);
    fprintf(sink->file, "\n");
    sink->written++;
}

void RdmaHw::TraceGuardGrantAckReceive(Ptr<RdmaRxQueuePair> qp, CustomHeader &ch,
                                       Ptr<Packet> packet, const char *event,
                                       const GuardRetiredAckRecord *retired) {
    GuardGrantTraceSink *sink = m_guardGrantTraceSink;
    if (sink == NULL || sink->file == NULL) return;
    sink->attempted++;
    if (sink->written >= sink->max_lines) return;
    int32_t flow_id = qp == NULL
                          ? retired == NULL ? -1 : retired->flowId
                          : qp->m_flow_id;
    uint64_t line_rate_bps = 0;
    uint64_t next_seq = 0;
    if (qp != NULL) {
        uint32_t nic_idx = GetNicIdxOfRxQp(qp);
        if (nic_idx < m_nic.size() && m_nic[nic_idx].dev != NULL) {
            line_rate_bps = m_nic[nic_idx].dev->GetDataRate().GetBitRate();
        }
        next_seq = qp->ReceiverNextExpectedSeq;
    }
    uint64_t retired_target_bps =
        retired == NULL
            ? 0
            : static_cast<uint64_t>(retired->targetMbps) * 1000000ULL;
    fprintf(sink->file, "%ld,%s,none,%u,%d,%u,%u,%lu,%lu,%lu,%lu,%u,%u,%lu,1",
            Simulator::Now().GetNanoSeconds(), event, m_node->GetId(), flow_id,
            ch.sip, ch.dip, m_rate_flow_ctl_set.size(), line_rate_bps,
            retired_target_bps, next_seq,
            packet->GetSize(), ch.grantAck.generation, m_guardPendingGrantAcks);
    bool authoritative =
        (std::string(event) == "ack_received" && qp != NULL) ||
        (std::string(event) == "ack_retired" && retired != NULL);
    uint8_t phase_tag = ch.grantAck.phaseTag;
    AppendGuardFastpathTraceFields(sink->file, event, "none",
                                   PeekPointer(qp), phase_tag,
                                   authoritative, retired_target_bps, retired);
    fprintf(sink->file, "\n");
    sink->written++;
}

void RdmaHw::TraceGuardGrantAckSend(Ptr<RdmaQueuePair> qp, Ptr<Packet> packet,
                                    uint32_t generation, uint8_t phase_tag) {
    GuardGrantTraceSink *sink = m_guardGrantTraceSink;
    if (sink == NULL || sink->file == NULL) return;
    sink->attempted++;
    if (sink->written >= sink->max_lines) return;
    uint64_t line_rate_bps = 0;
    uint32_t nic_idx = GetNicIdxOfQp(qp);
    if (nic_idx < m_nic.size() && m_nic[nic_idx].dev != NULL) {
        line_rate_bps = m_nic[nic_idx].dev->GetDataRate().GetBitRate();
    }
    fprintf(sink->file, "%ld,ack_sent,none,%u,%d,%u,%u,0,%lu,0,%lu,%u,%u,0,1",
            Simulator::Now().GetNanoSeconds(), m_node->GetId(), qp->m_flow_id,
            qp->sip.Get(), qp->dip.Get(), line_rate_bps, qp->snd_nxt,
            packet->GetSize(), generation);
    AppendGuardFastpathTraceFields(sink->file, "ack_sent", "none", NULL,
                                   phase_tag, false, 0);
    fprintf(sink->file, "\n");
    sink->written++;
}

void RdmaHw::SendRateControlPacket(Ptr<RdmaRxQueuePair> rx_qp,
                                   uint32_t rate_data, const char *set_change,
                                   uint32_t generation, bool ack_required) {
    m_guardRateGrantsSent++;
    if (generation > 0) {
        if (ack_required) {
            m_guardAckRequiredGrantsSent++;
        } else {
            m_guardAckOptionalGrantsSent++;
        }
    }
    uint8_t phase_tag = GetGuardFastpathWirePhaseTag(set_change);
    if (GuardSmallSetFastpathEnabled()) {
        CountGuardFastpathGrantFrame(phase_tag);
    }
    GuardGrantHeader grant;
    grant.SetRateMbps(rate_data);
    grant.SetPG(rx_qp->m_guard_pg);
    grant.SetSport(rx_qp->sport);
    grant.SetDport(rx_qp->dport);
    grant.SetGeneration(generation);
    grant.SetAckRequired(ack_required);
    grant.SetPhaseTag(phase_tag);

    Ptr<Packet> newp = Create<Packet>(std::max(60 - 14 - 20 - (int)grant.GetSerializedSize(), 0));
    newp->AddHeader(grant);

    Ipv4Header head;  // Prepare IPv4 header
    head.SetDestination(Ipv4Address(rx_qp->dip));
    head.SetSource(Ipv4Address(rx_qp->sip));
    head.SetProtocol(CustomHeader::GUARD_RATE_GRANT);
    head.SetTtl(64);
    head.SetPayloadSize(newp->GetSize());
    head.SetIdentification(rx_qp->m_ipid++);

    newp->AddHeader(head);
    AddHeader(newp, 0x800);  // Attach PPP header
    m_guardRateGrantBytesSent += newp->GetSize();
    TraceGuardGrant(rx_qp, "sent", set_change, m_rate_flow_ctl_set.size(),
                    m_nic[GetNicIdxOfRxQp(rx_qp)].dev->GetDataRate().GetBitRate(),
                    static_cast<uint64_t>(rate_data) * 1000000,
                    rx_qp->ReceiverNextExpectedSeq, newp->GetSize(), generation,
                    m_guardPendingGrantAcks, ack_required, phase_tag);

    // send
    uint32_t nic_idx = GetNicIdxOfRxQp(rx_qp);
    m_nic[nic_idx].dev->RdmaEnqueueHighPrioQ(newp);
    m_nic[nic_idx].dev->TriggerTransmit();
}

void RdmaHw::SendGuardGrantAck(Ptr<RdmaQueuePair> qp, uint32_t generation,
                               uint8_t phase_tag) {
    GuardGrantAckHeader ack;
    ack.SetSport(qp->sport);
    ack.SetDport(qp->dport);
    ack.SetPG(qp->m_pg);
    ack.SetGeneration(generation);
    ack.SetPhaseTag(phase_tag);

    Ptr<Packet> packet = Create<Packet>(
        std::max(60 - 14 - 20 - (int)ack.GetSerializedSize(), 0));
    packet->AddHeader(ack);

    Ipv4Header head;
    head.SetDestination(qp->dip);
    head.SetSource(qp->sip);
    head.SetProtocol(CustomHeader::GUARD_RATE_GRANT_ACK);
    head.SetTtl(64);
    head.SetPayloadSize(packet->GetSize());
    head.SetIdentification(qp->m_ipid++);
    packet->AddHeader(head);
    AddHeader(packet, 0x800);

    m_guardGrantAcksSent++;
    m_guardGrantAckBytesSent += packet->GetSize();
    TraceGuardGrantAckSend(qp, packet, generation, phase_tag);
    uint32_t nic_idx = GetNicIdxOfQp(qp);
    m_nic[nic_idx].dev->RdmaEnqueueHighPrioQ(packet);
    m_nic[nic_idx].dev->TriggerTransmit();
}

/***********************
 * Homa Simple CC
 ***********************/
RdmaHw::HomaSimpleScheduler::HomaSimpleScheduler(RdmaHw* hw): rdma_hw(hw) {
    is_scheduled = false;
    pacing_interval = 0;
}

RdmaHw::HomaSimpleScheduler::~HomaSimpleScheduler() {}

void RdmaHw::HomaSimpleScheduler::SetPacingInterval(Ptr<Packet> p) {
    // pacing interval ≈ time to put one MTU on the wire at line rate
    uint64_t interval_bytes = rdma_hw->m_mtu;
    uint32_t nic_idx = 0;
    if (!active_flow.empty()) {
        nic_idx = rdma_hw->GetNicIdxOfRxQp(active_flow.top()->rx_qp);
    } else if (!flow_hash.empty()) {
        nic_idx = rdma_hw->GetNicIdxOfRxQp(flow_hash.begin()->second->rx_qp);
    }
    Ptr<QbbNetDevice> dev = rdma_hw->m_nic[nic_idx].dev;
    DataRate qp_rate = dev->GetDataRate();
    this->pacing_interval = (uint64_t)(1e9 * 8 * interval_bytes / qp_rate.GetBitRate());
}

void RdmaHw::HomaSimpleScheduler::SendHomaSimpleCreditPackage(HomaSimpleFlow &flow) {
    qbbHeader seqh;
    seqh.SetSeq(flow.rx_qp->ReceiverNextExpectedSeq);
    seqh.SetPG(flow.pg);
    seqh.SetSport(flow.rx_qp->sport);
    seqh.SetDport(flow.rx_qp->dport);
    seqh.SetCnp();

    Ptr<Packet> newp = Create<Packet>(std::max(60 - 14 - 20 - (int)seqh.GetSerializedSize(), 0));
    newp->AddHeader(seqh);

    Ipv4Header head;
    head.SetDestination(Ipv4Address(flow.rx_qp->dip));
    head.SetSource(Ipv4Address(flow.rx_qp->sip));
    head.SetProtocol(0xFB);  // homa-simple credit (decoded by cc_mode)
    head.SetTtl(64);
    head.SetPayloadSize(newp->GetSize());
    head.SetIdentification(flow.rx_qp->m_ipid++);

    newp->AddHeader(head);
    rdma_hw->AddHeader(newp, 0x800);

    uint32_t nic_idx = rdma_hw->GetNicIdxOfRxQp(flow.rx_qp);
    rdma_hw->m_nic[nic_idx].dev->RdmaEnqueueHighPrioQ(newp);
    rdma_hw->m_nic[nic_idx].dev->TriggerTransmit();
}

void RdmaHw::HomaSimpleScheduler::UpdateFlowState(HomaSimpleFlow* flow) {
    flow->token_bucket += rdma_hw->m_mtu;
    if (flow->requset_bytes >= rdma_hw->m_mtu) {
        flow->requset_bytes -= rdma_hw->m_mtu;
    } else {
        flow->requset_bytes = 0;
    }

    if (flow->requset_bytes > 0 && flow->token_bucket < flow->bdp) {
        flow->state = HOMA_SIMPLE_FLOW_ACTIVE;
        active_flow.insert(flow);
    } else if (flow->requset_bytes > 0 && flow->token_bucket >= flow->bdp) {
        flow->state = HOMA_SIMPLE_FLOW_WAITING;
        wait_flow.insert(flow);
    } else if (flow->requset_bytes == 0) {
        flow_hash.erase(PeekPointer(flow->rx_qp));
    }
}

void RdmaHw::HomaSimpleScheduler::ScheduleHomaSimple() {
    if (flow_hash.size() == 0) {
        is_scheduled = false;
        return;
    }
    if (!active_flow.empty()) {
        HomaSimpleFlow* priority_flow = active_flow.pop();
        SendHomaSimpleCreditPackage(*priority_flow);
        UpdateFlowState(priority_flow);
        Simulator::Schedule(NanoSeconds(this->pacing_interval),
                            &RdmaHw::HomaSimpleScheduler::ScheduleHomaSimple, this);
    } else {
        is_scheduled = false;
    }
}

void RdmaHw::HomaSimpleScheduler::AddHomaSimpleFlow(HomaSimpleFlow &flow_template, Ptr<Packet> p, CustomHeader &ch) {
    std::unique_ptr<HomaSimpleFlow> new_flow_ptr(new HomaSimpleFlow(flow_template));
    HomaSimpleFlow* p_flow = new_flow_ptr.get();

    if (p_flow->token_bucket >= p_flow->bdp) {
        p_flow->state = HOMA_SIMPLE_FLOW_WAITING;
        wait_flow.insert(p_flow);
    } else {
        p_flow->state = HOMA_SIMPLE_FLOW_ACTIVE;
        active_flow.insert(p_flow);
    }

    flow_hash[PeekPointer(p_flow->rx_qp)] = std::move(new_flow_ptr);

    // only kick off the pacing loop if we now have an active flow.
    // a pure-waiting flow will be promoted to active by ReceiveHomaSimpleData,
    // which schedules then.
    if (!is_scheduled && !active_flow.empty()) {
        is_scheduled = true;
        SetPacingInterval(p);
        ScheduleHomaSimple();
    }
}

void RdmaHw::ReceiveHomaSimpleRequest(Ptr<RdmaRxQueuePair> qp, Ptr<Packet> p, CustomHeader &ch) {
    HomaSimpleFlow homa_flow;
    homa_flow.state = HOMA_SIMPLE_FLOW_IDLE;
    homa_flow.rx_qp = qp;
    homa_flow.requset_bytes = ch.udp.homa_simple_requset;
    homa_flow.token_bucket = ch.udp.homa_simple_unscheduled;
    homa_flow.pg = ch.udp.pg;
    homa_flow.bdp = ch.udp.homa_simple_bdp;
    homa_simple_scheduler.AddHomaSimpleFlow(homa_flow, p, ch);
}

void RdmaHw::ReceiveHomaSimpleData(Ptr<RdmaRxQueuePair> qp, Ptr<Packet> p, CustomHeader &ch) {
    auto it = homa_simple_scheduler.flow_hash.find(PeekPointer(qp));
    if (it == homa_simple_scheduler.flow_hash.end()) return;

    HomaSimpleFlow* p_flow = it->second.get();
    if (p_flow->token_bucket >= homa_simple_scheduler.rdma_hw->m_mtu) {
        p_flow->token_bucket -= homa_simple_scheduler.rdma_hw->m_mtu;
    } else {
        p_flow->token_bucket = 0;
    }

    if (p_flow->state == HOMA_SIMPLE_FLOW_WAITING) {
        if (p_flow->token_bucket < p_flow->bdp) {
            homa_simple_scheduler.wait_flow.erase(p_flow);
            p_flow->state = HOMA_SIMPLE_FLOW_ACTIVE;
            homa_simple_scheduler.active_flow.insert(p_flow);
            if (!homa_simple_scheduler.is_scheduled) {
                homa_simple_scheduler.is_scheduled = true;
                homa_simple_scheduler.SetPacingInterval(p);
                Simulator::Schedule(NanoSeconds(0),
                                    &RdmaHw::HomaSimpleScheduler::ScheduleHomaSimple, &this->homa_simple_scheduler);
            }
        }
    }
}

int RdmaHw::ReceiveHomaSimpleCredit(Ptr<Packet> p, CustomHeader &ch) {
    uint16_t qIndex = ch.ack.pg;
    uint16_t port = ch.ack.dport;
    uint16_t sport = ch.ack.sport;
    uint64_t key = GetQpKey(ch.sip, port, sport, qIndex);
    Ptr<RdmaQueuePair> qp = GetQp(ch.dip, ch.sip, port, sport, qIndex);
    if (qp == NULL) {
        // Race-tolerant: receiver scheduler may emit a credit just as the
        // sender QP is being torn down. Drop silently like ReceiveAck.
        return 1;
    }
    qp->homa_simple.m_credit_package++;
    uint32_t nic_idx = GetNicIdxOfQp(qp);
    Ptr<QbbNetDevice> dev = m_nic[nic_idx].dev;
    dev->TriggerTransmit();
    return 0;
}

Ptr<Packet> RdmaHw::GetNxtPacketHomaSimple(Ptr<RdmaQueuePair> qp) {
    uint32_t payload_size = qp->GetBytesLeft();
    if (m_mtu < payload_size) payload_size = m_mtu;
    uint32_t seq = (uint32_t)qp->snd_nxt;
    qp->stat.txTotalPkts += 1;
    qp->stat.txTotalBytes += payload_size;

    Ptr<Packet> p = Create<Packet>(payload_size);
    if (qp->homa_simple.is_request_package) {
        HomaSimpleHeader homaHeader;
        homaHeader.SetHomaRequest(qp->homa_simple.m_request_bytes);
        homaHeader.SetHomaUnscheduled(qp->homa_simple.m_unscheduled_bytes);
        homaHeader.SetBdp(qp->homa_simple.m_bdp);
        p->AddHeader(homaHeader);
    }
    qp->homa_simple.m_credit_package--;

    SeqTsHeader seqTs;
    seqTs.SetSeq(seq);
    seqTs.SetPG(qp->m_pg);
    seqTs.SetIsRequest(qp->homa_simple.is_request_package ? 1 : 0);

    if (qp->homa_simple.is_request_package) qp->homa_simple.is_request_package = false;

    p->AddHeader(seqTs);

    UdpHeader udpHeader;
    udpHeader.SetDestinationPort(qp->dport);
    udpHeader.SetSourcePort(qp->sport);
    p->AddHeader(udpHeader);

    Ipv4Header ipHeader;
    ipHeader.SetSource(qp->sip);
    ipHeader.SetDestination(qp->dip);
    ipHeader.SetProtocol(0x11);
    ipHeader.SetPayloadSize(p->GetSize());
    ipHeader.SetTtl(64);
    ipHeader.SetTos(0);
    ipHeader.SetIdentification(qp->m_ipid);
    p->AddHeader(ipHeader);

    PppHeader ppp;
    ppp.SetProtocol(0x0021);
    p->AddHeader(ppp);

    // attach Stat tags
    {
        FlowIDNUMTag fint;
        if (!p->PeekPacketTag(fint)) {
            fint.SetId(qp->m_flow_id);
            fint.SetFlowSize(qp->m_size);
            p->AddPacketTag(fint);
        }
        FlowStatTag fst;
        uint64_t size = qp->m_size;
        if (!p->PeekPacketTag(fst)) {
            if (size <= m_mtu && qp->snd_nxt + payload_size >= qp->m_size) {
                fst.SetType(FlowStatTag::FLOW_START_AND_END);
            } else if (qp->snd_nxt + payload_size >= qp->m_size) {
                fst.SetType(FlowStatTag::FLOW_END);
            } else if (qp->snd_nxt == 0) {
                fst.SetType(FlowStatTag::FLOW_START);
            } else {
                fst.SetType(FlowStatTag::FLOW_NOTEND);
            }
            fst.setInitiatedTime(Simulator::Now().GetSeconds());
            p->AddPacketTag(fst);
        }
    }

    if (qp->irn.m_enabled) {
        if (qp->irn.m_max_seq < seq) qp->irn.m_max_seq = seq;
    }
    qp->snd_nxt += payload_size;
    qp->m_ipid++;
    return p;
}

/***********************
 * Homa CC (cc_mode 12)
 *
 * PR1: minimal sender that emits one DATA packet at a time at line rate
 * with a HomaHeader{type=DATA} attached. No credit gating, no
 * receiver scheduling, no loss recovery. PR2+ adds those.
 ***********************/
Ptr<Packet> RdmaHw::GetNxtPacketHoma(Ptr<RdmaQueuePair> qp) {
    // PR5: prefer a queued retransmit (RESEND request) over forward progress.
    bool is_retransmit = !qp->homa.m_retransmit_queue.empty();
    uint64_t pkt_offset;
    uint32_t payload_size;
    if (is_retransmit) {
        auto front = qp->homa.m_retransmit_queue.front();
        qp->homa.m_retransmit_queue.pop_front();
        pkt_offset = front.first;
        payload_size = std::min(front.second, m_mtu);
    } else {
        pkt_offset = qp->snd_nxt;
        payload_size = qp->GetBytesLeft();
        if (m_mtu < payload_size) payload_size = m_mtu;
    }
    uint32_t seq = (uint32_t)pkt_offset;
    qp->stat.txTotalPkts += 1;
    qp->stat.txTotalBytes += payload_size;
    m_homaDataPacketsSent++;
    m_homaDataBytesSent += payload_size;
    if (is_retransmit) m_homaRetransmitPacketsSent++;

    Ptr<Packet> p = Create<Packet>(payload_size);

    // Per-packet priority: unscheduled bytes use this QP's static cutoff
    // priority (set at AddQueuePair from m_size); scheduled bytes use the
    // priority slot the receiver assigned via the latest GRANT. Retransmits
    // pick whichever range the offset falls in.
    uint8_t pkt_priority;
    if (pkt_offset < qp->homa.m_unscheduled_bytes) {
        pkt_priority = qp->homa.m_unscheduled_priority;
    } else {
        pkt_priority = qp->homa.m_grant_priority;
    }
    NS_ASSERT_MSG(pkt_priority < 8, "Homa DATA priority is outside the switch queue range");
    m_homaDataPacketsByPriority[pkt_priority]++;
    m_homaDataBytesByPriority[pkt_priority] += payload_size;

    HomaHeader hfh;
    hfh.SetType(HomaHeader::DATA);
    hfh.SetMessageId((uint64_t)qp->m_flow_id);
    hfh.SetMsgTotalLength(qp->m_size);
    hfh.SetPktOffset(pkt_offset);
    hfh.SetPktLength(payload_size);
    hfh.SetUnscheduledBytes(qp->homa.m_unscheduled_bytes);
    hfh.SetPriority(pkt_priority);
    p->AddHeader(hfh);

    SeqTsHeader seqTs;
    seqTs.SetSeq(seq);
    seqTs.SetPG((uint16_t)pkt_priority);  // pg drives switch qIndex per-packet
    p->AddHeader(seqTs);

    UdpHeader udpHeader;
    udpHeader.SetDestinationPort(qp->dport);
    udpHeader.SetSourcePort(qp->sport);
    p->AddHeader(udpHeader);

    Ipv4Header ipHeader;
    ipHeader.SetSource(qp->sip);
    ipHeader.SetDestination(qp->dip);
    ipHeader.SetProtocol(0x11);
    ipHeader.SetPayloadSize(p->GetSize());
    ipHeader.SetTtl(64);
    ipHeader.SetTos(0);
    ipHeader.SetIdentification(qp->m_ipid);
    p->AddHeader(ipHeader);

    PppHeader ppp;
    ppp.SetProtocol(0x0021);
    p->AddHeader(ppp);

    // attach Stat tags (same shape as the other GetNxtPacket* paths)
    {
        FlowIDNUMTag fint;
        if (!p->PeekPacketTag(fint)) {
            fint.SetId(qp->m_flow_id);
            fint.SetFlowSize(qp->m_size);
            p->AddPacketTag(fint);
        }
        FlowStatTag fst;
        uint64_t size = qp->m_size;
        if (!p->PeekPacketTag(fst)) {
            if (size <= m_mtu && qp->snd_nxt + payload_size >= qp->m_size) {
                fst.SetType(FlowStatTag::FLOW_START_AND_END);
            } else if (qp->snd_nxt + payload_size >= qp->m_size) {
                fst.SetType(FlowStatTag::FLOW_END);
            } else if (qp->snd_nxt == 0) {
                fst.SetType(FlowStatTag::FLOW_START);
            } else {
                fst.SetType(FlowStatTag::FLOW_NOTEND);
            }
            fst.setInitiatedTime(Simulator::Now().GetSeconds());
            p->AddPacketTag(fst);
        }
    }

    if (qp->irn.m_enabled) {
        if (qp->irn.m_max_seq < seq) qp->irn.m_max_seq = seq;
    }
    if (!is_retransmit) qp->snd_nxt += payload_size;
    qp->m_ipid++;
    return p;
}

void RdmaHw::ReceiveHomaData(Ptr<RdmaRxQueuePair> qp, Ptr<Packet> p, CustomHeader &ch) {
    homa_scheduler.OnDataArrival(qp, p, ch);
}

int RdmaHw::ReceiveHomaControl(Ptr<Packet> /*p*/, CustomHeader &ch) {
    // 0xFA control packet. Dispatch by HomaHeader::type.
    uint16_t qIndex = ch.ack.pg;
    uint16_t port = ch.ack.dport;
    uint16_t sport = ch.ack.sport;
    uint64_t key = GetQpKey(ch.sip, port, sport, qIndex);
    Ptr<RdmaQueuePair> qp = GetQp(ch.dip, ch.sip, port, sport, qIndex);
    if (qp == NULL) {
        // Race-tolerant like ReceiveAck / ReceiveHomaSimpleCredit.
        return 1;
    }

    switch (ch.udp.homa_type) {
        case HomaHeader::GRANT: {
            // Advance the sender's authorized offset and remember the priority
            // slot the receiver wants future scheduled packets to use.
            if (ch.udp.homa_granted_offset > qp->homa.m_granted_offset) {
                qp->homa.m_granted_offset = ch.udp.homa_granted_offset;
            }
            qp->homa.m_grant_priority = ch.udp.homa_grant_priority;
            m_homaGrantsReceived++;
            uint32_t nic_idx = GetNicIdxOfQp(qp);
            m_nic[nic_idx].dev->TriggerTransmit();
            return 0;
        }
        case HomaHeader::RESEND: {
            // Receiver detected a hole; queue the requested range for
            // retransmission. GetNxtPacketHoma will preempt forward
            // progress to drain the queue.
            qp->homa.m_retransmit_queue.push_back(
                std::make_pair(ch.udp.homa_resend_offset,
                               (uint32_t)ch.udp.homa_resend_length));
            m_homaResendsReceived++;
            uint32_t nic_idx = GetNicIdxOfQp(qp);
            m_nic[nic_idx].dev->TriggerTransmit();
            return 0;
        }
        case HomaHeader::ACK: {
            // The traffic generator models one-way messages, whereas Homa's
            // normal RPC response implicitly acknowledges the request. This
            // completion-only control notice is simulator plumbing: it does
            // not provide per-packet reliability or drive sender progress.
            qp->Acknowledge(qp->m_size);
            m_homaCompletionNoticesReceived++;
            if (qp->IsFinished()) QpComplete(qp);
            return 0;
        }
        // PR5+ will add NEED_ACK / ACK / BUSY / UNKNOWN handling.
        default:
            return 0;
    }
}

/***********************
 * Homa CC — Scheduler
 ***********************/
RdmaHw::HomaScheduler::HomaScheduler(RdmaHw* hw)
    : rdma_hw(hw),
      is_scheduled(false),
      is_stall_scheduled(false),
      pacing_interval(0) {}

RdmaHw::HomaScheduler::~HomaScheduler() {}

void RdmaHw::HomaScheduler::SetPacingInterval() {
    // 1 MTU on the wire at line rate. Picks any active flow's NIC for rate.
    uint32_t nic_idx = 0;
    if (!flow_hash.empty()) {
        nic_idx = rdma_hw->GetNicIdxOfRxQp(flow_hash.begin()->second->rx_qp);
    }
    Ptr<QbbNetDevice> dev = rdma_hw->m_nic[nic_idx].dev;
    DataRate qp_rate = dev->GetDataRate();
    pacing_interval = (uint64_t)(1e9 * 8 * rdma_hw->m_mtu / qp_rate.GetBitRate());
}

void RdmaHw::HomaScheduler::OnDataArrival(Ptr<RdmaRxQueuePair> rx_qp,
                                              Ptr<Packet> /*p*/, CustomHeader &ch) {
    uint64_t message_id = ch.udp.homa_message_id;
    if (completed_message_ids.count(message_id)) {
        // A RESEND may already be in flight when the original DATA completes.
        // Do not recreate receive/grant state for that late duplicate. Replay
        // the completion notice so a lost first notice cannot strand the
        // sender-side QP.
        HomaFlow completed;
        completed.message_id = message_id;
        completed.msg_total_length = ch.udp.homa_msg_total_length;
        completed.pg = 0;
        completed.rx_qp = rx_qp;
        SendCompletionNotice(&completed);
        rdma_hw->m_homaDuplicateDataAfterCompletion++;
        rdma_hw->m_homaCompletionNoticesReplayed++;
        return;
    }
    RdmaRxQueuePair* hkey = PeekPointer(rx_qp);
    auto it = flow_hash.find(hkey);
    if (it == flow_hash.end()) {
        // First DATA observed for this rx_qp.
        std::unique_ptr<HomaFlow> new_flow(new HomaFlow);
        new_flow->message_id          = message_id;
        new_flow->msg_total_length    = ch.udp.homa_msg_total_length;
        new_flow->bytes_received      = 0;
        new_flow->granted_offset_sent = ch.udp.homa_unscheduled_bytes;
        new_flow->bdp                 = ch.udp.homa_unscheduled_bytes;
        new_flow->next_expected_offset = 0;
        new_flow->last_progress_time  = Simulator::Now();
        // cc_mode=12 pins QP-key pg to 0 (sender) and rxQp-key pg to 0
        // (receiver) so per-packet udp.pg variation can't break either lookup.
        // Control packets (GRANT/RESEND/...) sent back must use pg=0 too.
        new_flow->pg                  = 0;
        new_flow->rx_qp               = rx_qp;

        HomaFlow* p_flow = new_flow.get();
        flow_hash[hkey] = std::move(new_flow);
        it = flow_hash.find(hkey);
        rdma_hw->m_homaMessagesTracked++;

        // Messages wholly covered by the unscheduled allowance need no
        // grants, but retain their receive state until all DATA arrives so a
        // late hole can still be detected.
        if (!p_flow->fully_granted()) {
            active.insert(p_flow);
            rdma_hw->m_homaMaxPendingMessages = std::max(
                rdma_hw->m_homaMaxPendingMessages, (uint64_t)active.size());
        }

        if (!is_scheduled) {
            is_scheduled = true;
            SetPacingInterval();
            Simulator::Schedule(NanoSeconds(pacing_interval),
                                &RdmaHw::HomaScheduler::Schedule, this);
        }
        if (!is_stall_scheduled) {
            is_stall_scheduled = true;
            Simulator::Schedule(rdma_hw->m_homaResendTimeout,
                                &RdmaHw::HomaScheduler::StallCheck, this);
        }
    }

    // Record DATA as a byte interval, merging overlaps and adjacency. This is
    // required because Homa explicitly permits per-packet reordering.
    HomaFlow* p_flow = it->second.get();
    uint64_t start = ch.udp.homa_pkt_offset;
    uint64_t end = std::min(start + ch.udp.homa_pkt_length, p_flow->msg_total_length);
    if (end > start) {
        auto range = p_flow->received_ranges.lower_bound(start);
        if (range != p_flow->received_ranges.begin()) {
            auto previous = range;
            --previous;
            if (previous->second >= start) range = previous;
        }
        while (range != p_flow->received_ranges.end() && range->first <= end) {
            if (range->second < start) {
                ++range;
                continue;
            }
            start = std::min(start, range->first);
            end = std::max(end, range->second);
            p_flow->bytes_received -= range->second - range->first;
            range = p_flow->received_ranges.erase(range);
        }
        p_flow->received_ranges[start] = end;
        p_flow->bytes_received += end - start;

        uint64_t old_expected = p_flow->next_expected_offset;
        auto first = p_flow->received_ranges.begin();
        if (first != p_flow->received_ranges.end() && first->first == 0) {
            p_flow->next_expected_offset = first->second;
        }
        rx_qp->ReceiverNextExpectedSeq = p_flow->next_expected_offset;
        if (p_flow->next_expected_offset > old_expected) {
            p_flow->last_progress_time = Simulator::Now();
        }
    }

    if (p_flow->fully_received()) {
        completed_message_ids.insert(p_flow->message_id);
        SendCompletionNotice(p_flow);
        rdma_hw->m_homaMessagesCompleted++;
        active.erase(hkey);
        flow_hash.erase(hkey);
        if (flow_hash.empty()) is_stall_scheduled = false;
        return;
    }

    // Schedule() intentionally sleeps when every selected message already
    // has one BDP of granted-but-unreceived data. A new DATA arrival reduces
    // that outstanding window and is therefore the event that should wake
    // the grant pacer; polling every serialization interval would create an
    // empty event loop under persistent fabric congestion.
    if (!active.empty() && !is_scheduled) {
        is_scheduled = true;
        SetPacingInterval();
        Simulator::Schedule(NanoSeconds(pacing_interval),
                            &RdmaHw::HomaScheduler::Schedule, this);
    }
}

void RdmaHw::HomaScheduler::Schedule() {
    if (active.empty()) {
        is_scheduled = false;
        return;
    }

    // Pop up to overcommit_degree flows in SRPT order, grant each by 1 MTU.
    std::vector<HomaFlow*> tick;
    uint32_t N = rdma_hw->m_homaOvercommitDegree;
    while (tick.size() < N && !active.empty()) {
        tick.push_back(active.pop());
    }

    bool sent_grant = false;
    for (size_t k = 0; k < tick.size(); k++) {
        HomaFlow* flow = tick[k];
        // Do not authorize an entire stalled message. Homa keeps roughly one
        // RTT of granted-but-not-yet-received bytes outstanding for each
        // selected sender; overcommitment exists specifically so another
        // selected sender can fill the receiver downlink when one is blocked.
        uint64_t outstanding = flow->granted_offset_sent > flow->bytes_received
                                   ? flow->granted_offset_sent - flow->bytes_received
                                   : 0;
        uint64_t grant_bytes = 0;
        if (outstanding < flow->bdp) {
            grant_bytes = std::min<uint64_t>(rdma_hw->m_mtu, flow->bdp - outstanding);
        }
        uint64_t new_offset = std::min(flow->granted_offset_sent + grant_bytes,
                                       flow->msg_total_length);
        if (new_offset > flow->granted_offset_sent) {
            flow->granted_offset_sent = new_offset;
            // With fewer than all scheduled slots active, assign the lowest
            // available priorities. This leaves higher scheduled priorities
            // vacant so a newly arriving shorter message can preempt without
            // waiting for already-queued packets (Homa paper, Section 3.6).
            uint8_t slot_pri = (uint8_t)(7 - (tick.size() - 1 - k));
            SendGrant(flow, slot_pri);
            sent_grant = true;
        }
        if (!flow->fully_granted()) {
            active.insert(flow);
        }
    }

    if (!active.empty() && sent_grant) {
        Simulator::Schedule(NanoSeconds(pacing_interval),
                            &RdmaHw::HomaScheduler::Schedule, this);
    } else {
        // If no grant was possible, all selected flows already have a BDP in
        // flight. OnDataArrival restarts the pacer once receiver progress
        // creates grant headroom.
        is_scheduled = false;
    }
}

void RdmaHw::HomaScheduler::SendGrant(HomaFlow* flow, uint8_t grant_priority) {
    qbbHeader qbbh;
    qbbh.SetSeq(flow->rx_qp->ReceiverNextExpectedSeq);
    qbbh.SetPG(flow->pg);  // pg=0 for cc_mode=12 (matches sender QP key)
    qbbh.SetSport(flow->rx_qp->sport);
    qbbh.SetDport(flow->rx_qp->dport);
    qbbh.SetCnp();

    HomaHeader hfh;
    hfh.SetType(HomaHeader::GRANT);
    hfh.SetMessageId(flow->message_id);
    hfh.SetGrantedOffset(flow->granted_offset_sent);
    // Sender will use this priority for subsequent scheduled DATA packets.
    hfh.SetGrantPriority(grant_priority);

    Ptr<Packet> newp = Create<Packet>(
        std::max(60 - 14 - 20 - (int)qbbh.GetSerializedSize() - (int)HomaHeader::GetHeaderSize(), 0));
    newp->AddHeader(hfh);
    newp->AddHeader(qbbh);

    Ipv4Header head;
    head.SetDestination(Ipv4Address(flow->rx_qp->dip));
    head.SetSource(Ipv4Address(flow->rx_qp->sip));
    head.SetProtocol(0xFA);  // homa control
    head.SetTtl(64);
    head.SetPayloadSize(newp->GetSize());
    head.SetIdentification(flow->rx_qp->m_ipid++);

    newp->AddHeader(head);
    rdma_hw->AddHeader(newp, 0x800);

    uint32_t nic_idx = rdma_hw->GetNicIdxOfRxQp(flow->rx_qp);
    rdma_hw->m_nic[nic_idx].dev->RdmaEnqueueHighPrioQ(newp);
    rdma_hw->m_homaGrantsSent++;
    rdma_hw->m_nic[nic_idx].dev->TriggerTransmit();
}

void RdmaHw::HomaScheduler::StallCheck() {
    Time now = Simulator::Now();
    for (auto& kv : flow_hash) {
        HomaFlow* flow = kv.second.get();
        // Skip flows that have nothing missing to RESEND for.
        if (flow->next_expected_offset >= flow->msg_total_length) continue;
        if (flow->next_expected_offset >= flow->granted_offset_sent) continue;
        // Stalled means: no contiguous progress for the configured Homa
        // receiver timeout
        // even though the receiver has authorized more bytes than have arrived.
        if ((now - flow->last_progress_time) < rdma_hw->m_homaResendTimeout) continue;
        uint64_t resend_off = flow->next_expected_offset;
        uint64_t resend_len = std::min(flow->granted_offset_sent - resend_off,
                                       (uint64_t)rdma_hw->m_mtu);
        SendResend(flow, resend_off, resend_len);
        // Reset the timer to avoid spamming the sender every tick.
        flow->last_progress_time = now;
    }
    if (!flow_hash.empty()) {
        Simulator::Schedule(rdma_hw->m_homaResendTimeout,
                            &RdmaHw::HomaScheduler::StallCheck, this);
    } else {
        is_stall_scheduled = false;
    }
}

void RdmaHw::HomaScheduler::SendResend(HomaFlow* flow, uint64_t offset, uint64_t length) {
    qbbHeader qbbh;
    qbbh.SetSeq(flow->rx_qp->ReceiverNextExpectedSeq);
    qbbh.SetPG(flow->pg);
    qbbh.SetSport(flow->rx_qp->sport);
    qbbh.SetDport(flow->rx_qp->dport);
    qbbh.SetCnp();

    HomaHeader hfh;
    hfh.SetType(HomaHeader::RESEND);
    hfh.SetMessageId(flow->message_id);
    hfh.SetResendOffset(offset);
    hfh.SetResendLength(length);

    Ptr<Packet> newp = Create<Packet>(
        std::max(60 - 14 - 20 - (int)qbbh.GetSerializedSize() - (int)HomaHeader::GetHeaderSize(), 0));
    newp->AddHeader(hfh);
    newp->AddHeader(qbbh);

    Ipv4Header head;
    head.SetDestination(Ipv4Address(flow->rx_qp->dip));
    head.SetSource(Ipv4Address(flow->rx_qp->sip));
    head.SetProtocol(0xFA);
    head.SetTtl(64);
    head.SetPayloadSize(newp->GetSize());
    head.SetIdentification(flow->rx_qp->m_ipid++);

    newp->AddHeader(head);
    rdma_hw->AddHeader(newp, 0x800);

    uint32_t nic_idx = rdma_hw->GetNicIdxOfRxQp(flow->rx_qp);
    rdma_hw->m_nic[nic_idx].dev->RdmaEnqueueHighPrioQ(newp);
    rdma_hw->m_homaResendsSent++;
    rdma_hw->m_nic[nic_idx].dev->TriggerTransmit();
}

void RdmaHw::HomaScheduler::SendCompletionNotice(HomaFlow* flow) {
    qbbHeader qbbh;
    qbbh.SetSeq(flow->msg_total_length);
    qbbh.SetPG(flow->pg);
    qbbh.SetSport(flow->rx_qp->sport);
    qbbh.SetDport(flow->rx_qp->dport);
    qbbh.SetCnp();

    HomaHeader hfh;
    hfh.SetType(HomaHeader::ACK);
    hfh.SetMessageId(flow->message_id);

    Ptr<Packet> newp = Create<Packet>(
        std::max(60 - 14 - 20 - (int)qbbh.GetSerializedSize() -
                     (int)HomaHeader::GetHeaderSize(), 0));
    newp->AddHeader(hfh);
    newp->AddHeader(qbbh);

    Ipv4Header head;
    head.SetDestination(Ipv4Address(flow->rx_qp->dip));
    head.SetSource(Ipv4Address(flow->rx_qp->sip));
    head.SetProtocol(0xFA);
    head.SetTtl(64);
    head.SetPayloadSize(newp->GetSize());
    head.SetIdentification(flow->rx_qp->m_ipid++);
    newp->AddHeader(head);
    rdma_hw->AddHeader(newp, 0x800);

    uint32_t nic_idx = rdma_hw->GetNicIdxOfRxQp(flow->rx_qp);
    rdma_hw->m_nic[nic_idx].dev->RdmaEnqueueHighPrioQ(newp);
    rdma_hw->m_homaCompletionNoticesSent++;
    rdma_hw->m_nic[nic_idx].dev->TriggerTransmit();
}

/***********************
 * High Precision CC
 ***********************/
void RdmaHw::HandleAckHp(Ptr<RdmaQueuePair> qp, Ptr<Packet> p, CustomHeader &ch) {
    if (m_cc_mode == CC_MODE_GUARD && qp->m_guard_one_rtt_bypass) {
        // A flow no larger than one BDP cannot reduce the bytes it injected
        // before the first feedback RTT.  Applying that delayed sample only
        // throttles its tail, so GUARD leaves this bounded burst at line rate.
        m_guardOneRttBypassFeedbacks++;
        return;
    }
    if (m_cc_mode == CC_MODE_GUARD && m_guardTailCongestionGate &&
        ch.ack.ih.nhop == 0) {
        // After receiver-hop removal, no remaining INT record means there is
        // no shared-fabric hop whose queue the bypass could aggravate.
        qp->m_guard_tail_safe_samples = m_guardTailSafeSamples;
    }
    uint32_t ack_seq = ch.ack.seq;
    // update rate
    if (ack_seq > qp->hp.m_lastUpdateSeq) {  // if full RTT feedback is ready, do full update
        UpdateRateHp(qp, p, ch, false);
    } else {  // do fast react
        FastReactHp(qp, p, ch);
    }
}

void RdmaHw::UpdateRateHp(Ptr<RdmaQueuePair> qp, Ptr<Packet> p, CustomHeader &ch, bool fast_react) {
    if (m_cc_mode == 3 || m_cc_mode == 11) {
        m_guardHpccFeedbackUpdates++;
        if (ch.ack.ih.nhop > 0) m_guardHpccValidFeedback++;
    }
    uint32_t next_seq = qp->snd_nxt;
    bool print = !fast_react || true;
    if (qp->hp.m_lastUpdateSeq == 0) {  // first RTT
        qp->hp.m_lastUpdateSeq = next_seq;
        // store INT
        IntHeader &ih = ch.ack.ih;
        NS_ASSERT(ih.nhop <= IntHeader::maxHop);
        for (uint32_t i = 0; i < ih.nhop; i++) qp->hp.hop[i] = ih.hop[i];
#if PRINT_LOG
        if (print) {
            printf("%lu %s %08x %08x %u %u [%u,%u,%u]", Simulator::Now().GetTimeStep(),
                   fast_react ? "fast" : "update", qp->sip.Get(), qp->dip.Get(), qp->sport,
                   qp->dport, qp->hp.m_lastUpdateSeq, ch.ack.seq, next_seq);
            for (uint32_t i = 0; i < ih.nhop; i++)
                printf(" %u %lu %lu", ih.hop[i].GetQlen(), ih.hop[i].GetBytes(),
                       ih.hop[i].GetTime());
            printf("\n");
        }
#endif
    } else {
        // check packet INT
        IntHeader &ih = ch.ack.ih;
        if (ih.nhop <= IntHeader::maxHop) {
            double max_c = 0;
            double max_queue_bdps = 0;
            bool inStable = false;
#if PRINT_LOG
            if (print)
                printf("%lu %s %08x %08x %u %u [%u,%u,%u]", Simulator::Now().GetTimeStep(),
                       fast_react ? "fast" : "update", qp->sip.Get(), qp->dip.Get(), qp->sport,
                       qp->dport, qp->hp.m_lastUpdateSeq, ch.ack.seq, next_seq);
#endif
            // check each hop
            double U = 0;
            uint64_t dt = 0;
            bool updated[IntHeader::maxHop] = {false}, updated_any = false;
            NS_ASSERT(ih.nhop <= IntHeader::maxHop);
            for (uint32_t i = 0; i < ih.nhop; i++) {
                if (m_sampleFeedback) {
                    if (ih.hop[i].GetQlen() == 0 and fast_react) continue;
                }
                updated[i] = updated_any = true;
#if PRINT_LOG
                if (print)
                    printf(" %u(%u) %lu(%lu) %lu(%lu)", ih.hop[i].GetQlen(),
                           qp->hp.hop[i].GetQlen(), ih.hop[i].GetBytes(), qp->hp.hop[i].GetBytes(),
                           ih.hop[i].GetTime(), qp->hp.hop[i].GetTime());
#endif
                uint64_t tau = ih.hop[i].GetTimeDelta(qp->hp.hop[i]);
                ;
                double duration = tau * 1e-9;
                double txRate = (ih.hop[i].GetBytesDelta(qp->hp.hop[i])) * 8 / duration;
                double queue_bdps =
                    (double)std::min(ih.hop[i].GetQlen(), qp->hp.hop[i].GetQlen()) *
                    qp->m_max_rate.GetBitRate() / ih.hop[i].GetLineRate() / qp->m_win;
                max_queue_bdps = std::max(max_queue_bdps, queue_bdps);
                double u = txRate / ih.hop[i].GetLineRate() + queue_bdps;
#if PRINT_LOG
                if (print) printf(" %.3lf %.3lf", txRate, u);
#endif
                if (!m_multipleRate) {
                    // for aggregate (single R)
                    if (u > U) {
                        U = u;
                        dt = tau;
                    }
                } else {
                    // for per hop (per hop R)
                    if (tau > qp->m_baseRtt) tau = qp->m_baseRtt;
                    qp->hp.hopState[i].u =
                        (qp->hp.hopState[i].u * (qp->m_baseRtt - tau) + u * tau) /
                        double(qp->m_baseRtt);
                }
                qp->hp.hop[i] = ih.hop[i];
            }

            DataRate new_rate;
            int32_t new_incStage;
            DataRate new_rate_per_hop[IntHeader::maxHop];
            int32_t new_incStage_per_hop[IntHeader::maxHop];
            double effective_target = m_targetUtil;
            const bool adaptive_size_eligible =
                m_guardAdaptiveTargetMaxBdps == 0.0 ||
                qp->m_size <= m_guardAdaptiveTargetMaxBdps * qp->m_win;
            if (m_cc_mode == CC_MODE_GUARD && m_guardAdaptiveFabricTarget &&
                adaptive_size_eligible && updated_any) {
                const double floor = std::min(m_guardTargetFloor, m_targetUtil);
                const double queued_fraction =
                    std::min(1.0, max_queue_bdps / m_guardQueueBudgetBdps);
                effective_target =
                    floor + (m_targetUtil - floor) * (1.0 - queued_fraction);
                m_guardAdaptiveTargetMaxQueueBdps =
                    std::max(m_guardAdaptiveTargetMaxQueueBdps, max_queue_bdps);
                m_guardAdaptiveTargetMinObserved =
                    std::min(m_guardAdaptiveTargetMinObserved, effective_target);
                if (effective_target + 1e-12 < m_targetUtil)
                    m_guardAdaptiveTargetUpdates++;
            }
            if (m_cc_mode == CC_MODE_GUARD && m_guardElephantFabricTarget) {
                NS_ABORT_MSG_IF(m_guardAdaptiveFabricTarget,
                                "GUARD elephant target requires a fixed base target");
                bool elephant_target_eligible = false;
                NS_ABORT_MSG_IF(
                    !ComputeGuardElephantFabricTarget(
                        effective_target, true, qp->m_size, qp->m_win,
                        m_guardConcurrencyMinBdps,
                        m_guardElephantFabricTargetScale,
                        &effective_target, &elephant_target_eligible),
                    "GUARD elephant fabric target is invalid");
                if (elephant_target_eligible && updated_any) {
                    m_guardElephantFabricTargetUpdates++;
                    m_guardElephantFabricTargetMaxEffective = std::max(
                        m_guardElephantFabricTargetMaxEffective,
                        effective_target);
                }
            }
            if (!m_multipleRate) {
                // for aggregate (single R)
                if (updated_any) {
                    if (dt > qp->m_baseRtt) dt = qp->m_baseRtt;
                    qp->hp.u = (qp->hp.u * (qp->m_baseRtt - dt) + U * dt) / double(qp->m_baseRtt);
                    max_c = qp->hp.u / effective_target;

                    if (max_c >= 1 || qp->hp.m_incStage >= m_miThresh) {
                        new_rate = qp->hp.m_curRate / max_c + m_rai;
                        new_incStage = 0;
                    } else {
                        new_rate = qp->hp.m_curRate + m_rai;
                        new_incStage = qp->hp.m_incStage + 1;
                    }
                    if (new_rate < m_minRate) new_rate = m_minRate;
                    if (new_rate > qp->m_max_rate) new_rate = qp->m_max_rate;
#if PRINT_LOG
                    if (print) printf(" u=%.6lf U=%.3lf dt=%u max_c=%.3lf", qp->hp.u, U, dt, max_c);
#endif
#if PRINT_LOG
                    if (print)
                        printf(" rate:%.3lf->%.3lf\n", qp->hp.m_curRate.GetBitRate() * 1e-9,
                               new_rate.GetBitRate() * 1e-9);
#endif
                }
            } else {
                // for per hop (per hop R)
                new_rate = qp->m_max_rate;
                for (uint32_t i = 0; i < ih.nhop; i++) {
                    if (updated[i]) {
                        double c = qp->hp.hopState[i].u / effective_target;
                        max_c = std::max(max_c, c);
                        if (c >= 1 || qp->hp.hopState[i].incStage >= m_miThresh) {
                            new_rate_per_hop[i] = qp->hp.hopState[i].Rc / c + m_rai;
                            new_incStage_per_hop[i] = 0;
                        } else {
                            new_rate_per_hop[i] = qp->hp.hopState[i].Rc + m_rai;
                            new_incStage_per_hop[i] = qp->hp.hopState[i].incStage + 1;
                        }
                        // bound rate
                        if (new_rate_per_hop[i] < m_minRate) new_rate_per_hop[i] = m_minRate;
                        if (new_rate_per_hop[i] > qp->m_max_rate)
                            new_rate_per_hop[i] = qp->m_max_rate;
                        // find min new_rate
                        if (new_rate_per_hop[i] < new_rate) new_rate = new_rate_per_hop[i];
#if PRINT_LOG
                        if (print) printf(" [%u]u=%.6lf c=%.3lf", i, qp->hp.hopState[i].u, c);
#endif
#if PRINT_LOG
                        if (print)
                            printf(" %.3lf->%.3lf", qp->hp.hopState[i].Rc.GetBitRate() * 1e-9,
                                   new_rate.GetBitRate() * 1e-9);
#endif
                    } else {
                        if (qp->hp.hopState[i].Rc < new_rate) new_rate = qp->hp.hopState[i].Rc;
                    }
                }
#if PRINT_LOG
                printf("\n");
#endif
            }

            if (updated_any) {
                if (m_cc_mode == CC_MODE_GUARD && m_guardTailCongestionGate &&
                    !fast_react) {
                    if (max_c <= m_guardTailSafeRatio) {
                        qp->m_guard_tail_safe_samples = std::min<uint32_t>(
                            m_guardTailSafeSamples, qp->m_guard_tail_safe_samples + 1);
                    } else {
                        qp->m_guard_tail_safe_samples = 0;
                    }
                }
                m_guardHpccRateUpdatesApplied++;
                if (fast_react) {
                    m_guardHpccFastComputations++;
                } else {
                    m_guardHpccFullComputations++;
                }
                if (!fast_react) {
                    qp->hp.m_curRate = new_rate;
                    qp->hp.m_incStage = new_incStage;

                    if (m_multipleRate) {
                        for (uint32_t i = 0; i < ih.nhop; i++) {
                            if (updated[i]) {
                                qp->hp.hopState[i].Rc = new_rate_per_hop[i];
                                qp->hp.hopState[i].incStage = new_incStage_per_hop[i];
                            }
                        }
                    }
                }
                if (m_cc_mode == 11) {
                    DataRate old_rate = qp->m_rate;
                    const char *binding = "tie";
                    if (UsesGuardElephantReceiverAuthority(qp)) {
                        m_guardGrantBindingUpdates++;
                        binding = "grant";
                    } else if (new_rate < qp->hp.m_grantRate) {
                        m_guardReactiveBindingUpdates++;
                        binding = "reactive";
                    } else if (qp->hp.m_grantRate < new_rate) {
                        m_guardGrantBindingUpdates++;
                        binding = "grant";
                    } else {
                        m_guardTieBindingUpdates++;
                    }
                    SyncHwRate(qp, new_rate);  // guard: cap by grant rate
                    bool changed = qp->m_rate != old_rate;
                    if (changed) {
                        m_guardHpccActualRateChanges++;
                        if (binding[0] == 'r') m_guardReactiveBindingRateChanges++;
                        if (binding[0] == 'g') m_guardGrantBindingRateChanges++;
                        if (binding[0] == 't') m_guardTieBindingRateChanges++;
                    }
                    double observed_metric = m_multipleRate ? -1.0 : qp->hp.u;
                    double threshold_ratio = m_multipleRate ? -1.0 : max_c;
                    TraceGuardControllerEvent(qp, "hpcc", new_rate, binding, changed,
                                              fast_react, ih.nhop, next_seq,
                                              observed_metric, effective_target,
                                              threshold_ratio);
                } else {
                    DataRate old_rate = qp->m_rate;
                    ChangeRate(qp, new_rate);  // vanilla HPCC
                    if (qp->m_rate != old_rate) m_guardHpccActualRateChanges++;
                }
            }
        }
        if (!fast_react) {
            if (next_seq > qp->hp.m_lastUpdateSeq)
                qp->hp.m_lastUpdateSeq = next_seq;  //+ rand() % 2 * m_mtu;
        }
    }
}

void RdmaHw::FastReactHp(Ptr<RdmaQueuePair> qp, Ptr<Packet> p, CustomHeader &ch) {
    if (m_fast_react) UpdateRateHp(qp, p, ch, true);
}

/**********************
 * TIMELY
 *********************/
void RdmaHw::HandleAckTimely(Ptr<RdmaQueuePair> qp, Ptr<Packet> p, CustomHeader &ch) {
    uint32_t ack_seq = ch.ack.seq;
    // update rate
    if (ack_seq > qp->tmly.m_lastUpdateSeq) {  // if full RTT feedback is ready, do full update
        UpdateRateTimely(qp, p, ch, false);
    } else {  // do fast react
        FastReactTimely(qp, p, ch);
    }
}
void RdmaHw::UpdateRateTimely(Ptr<RdmaQueuePair> qp, Ptr<Packet> p, CustomHeader &ch, bool us) {
    uint32_t next_seq = qp->snd_nxt;
    uint64_t rtt = Simulator::Now().GetTimeStep() - ch.ack.ih.ts;
    bool print = !us;
    if (qp->tmly.m_lastUpdateSeq != 0) {  // not first RTT
        int64_t new_rtt_diff = (int64_t)rtt - (int64_t)qp->tmly.lastRtt;
        double rtt_diff = (1 - m_tmly_alpha) * qp->tmly.rttDiff + m_tmly_alpha * new_rtt_diff;
        double gradient = rtt_diff / m_tmly_minRtt;
        bool inc = false;
        double c = 0;
#if PRINT_LOG
        if (print)
            printf("%lu node:%u rtt:%lu rttDiff:%.0lf gradient:%.3lf rate:%.3lf",
                   Simulator::Now().GetTimeStep(), m_node->GetId(), rtt, rtt_diff, gradient,
                   qp->tmly.m_curRate.GetBitRate() * 1e-9);
#endif
        if (rtt < m_tmly_TLow) {
            inc = true;
        } else if (rtt > m_tmly_THigh) {
            c = 1 - m_tmly_beta * (1 - (double)m_tmly_THigh / rtt);
            inc = false;
        } else if (gradient <= 0) {
            inc = true;
        } else {
            c = 1 - m_tmly_beta * gradient;
            if (c < 0) c = 0;
            inc = false;
        }
        if (inc) {
            if (qp->tmly.m_incStage < 5) {
                qp->m_rate = qp->tmly.m_curRate + m_rai;
            } else {
                qp->m_rate = qp->tmly.m_curRate + m_rhai;
            }
            if (qp->m_rate > qp->m_max_rate) qp->m_rate = qp->m_max_rate;
            if (!us) {
                qp->tmly.m_curRate = qp->m_rate;
                qp->tmly.m_incStage++;
                qp->tmly.rttDiff = rtt_diff;
            }
        } else {
            qp->m_rate = std::max(m_minRate, qp->tmly.m_curRate * c);
            if (!us) {
                qp->tmly.m_curRate = qp->m_rate;
                qp->tmly.m_incStage = 0;
                qp->tmly.rttDiff = rtt_diff;
            }
        }
#if PRINT_LOG
        if (print) {
            printf(" %c %.3lf\n", inc ? '^' : 'v', qp->m_rate.GetBitRate() * 1e-9);
        }
#endif
    }
    if (!us && next_seq > qp->tmly.m_lastUpdateSeq) {
        qp->tmly.m_lastUpdateSeq = next_seq;
        // update
        qp->tmly.lastRtt = rtt;
    }
}
void RdmaHw::FastReactTimely(Ptr<RdmaQueuePair> qp, Ptr<Packet> p, CustomHeader &ch) {}

/**********************
 * DCTCP
 *********************/
void RdmaHw::HandleAckDctcp(Ptr<RdmaQueuePair> qp, Ptr<Packet> p, CustomHeader &ch) {
    uint32_t ack_seq = ch.ack.seq;
    uint8_t cnp = (ch.ack.flags >> qbbHeader::FLAG_CNP) & 1;
    bool new_batch = false;

    // update alpha
    qp->dctcp.m_ecnCnt += (cnp > 0);
    if (ack_seq > qp->dctcp.m_lastUpdateSeq) {  // if full RTT feedback is ready, do alpha update
#if PRINT_LOG
        printf("%lu %s %08x %08x %u %u [%u,%u,%u] %.3lf->", Simulator::Now().GetTimeStep(), "alpha",
               qp->sip.Get(), qp->dip.Get(), qp->sport, qp->dport, qp->dctcp.m_lastUpdateSeq,
               ch.ack.seq, qp->snd_nxt, qp->dctcp.m_alpha);
#endif
        new_batch = true;
        if (qp->dctcp.m_lastUpdateSeq == 0) {  // first RTT
            qp->dctcp.m_lastUpdateSeq = qp->snd_nxt;
            qp->dctcp.m_batchSizeOfAlpha = qp->snd_nxt / m_mtu + 1;
        } else {
            double frac = std::min(1.0, double(qp->dctcp.m_ecnCnt) / qp->dctcp.m_batchSizeOfAlpha);
            qp->dctcp.m_alpha = (1 - m_g) * qp->dctcp.m_alpha + m_g * frac;
            qp->dctcp.m_lastUpdateSeq = qp->snd_nxt;
            qp->dctcp.m_ecnCnt = 0;
            qp->dctcp.m_batchSizeOfAlpha = (qp->snd_nxt - ack_seq) / m_mtu + 1;
#if PRINT_LOG
            printf("%.3lf F:%.3lf", qp->dctcp.m_alpha, frac);
#endif
        }
#if PRINT_LOG
        printf("\n");
#endif
    }

    // check cwr exit
    if (qp->dctcp.m_caState == 1) {
        if (ack_seq > qp->dctcp.m_highSeq) qp->dctcp.m_caState = 0;
    }

    // check if need to reduce rate: ECN and not in CWR
    if (cnp && qp->dctcp.m_caState == 0) {
#if PRINT_LOG
        printf("%lu %s %08x %08x %u %u %.3lf->", Simulator::Now().GetTimeStep(), "rate",
               qp->sip.Get(), qp->dip.Get(), qp->sport, qp->dport, qp->m_rate.GetBitRate() * 1e-9);
#endif
        qp->m_rate = std::max(m_minRate, qp->m_rate * (1 - qp->dctcp.m_alpha / 2));
#if PRINT_LOG
        printf("%.3lf\n", qp->m_rate.GetBitRate() * 1e-9);
#endif
        qp->dctcp.m_caState = 1;
        qp->dctcp.m_highSeq = qp->snd_nxt;
    }

    // additive inc
    if (qp->dctcp.m_caState == 0 && new_batch)
        qp->m_rate = std::min(qp->m_max_rate, qp->m_rate + m_dctcp_rai);
}

}  // namespace ns3
