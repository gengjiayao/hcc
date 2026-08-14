#include "rdma-hw.h"

#include <ns3/ipv4-header.h>
#include <ns3/seq-ts-header.h>
#include <ns3/simulator.h>
#include <ns3/udp-header.h>

#include <climits>

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
            .AddAttribute("GuardWorkConserving",
                          "Reclaim persistently unused receiver grant shares",
                          BooleanValue(true), MakeBooleanAccessor(&RdmaHw::m_guardWorkConserving),
                          MakeBooleanChecker())
            .AddAttribute("GuardRebalanceInterval",
                          "Receiver demand-sampling interval for adaptive grants",
                          TimeValue(MicroSeconds(200)),
                          MakeTimeAccessor(&RdmaHw::m_guardRebalanceInterval),
                          MakeTimeChecker())
            .AddAttribute("GuardDemandThreshold",
                          "Arrival/grant ratio below which a share is reclaimable",
                          DoubleValue(0.75), MakeDoubleAccessor(&RdmaHw::m_guardDemandThreshold),
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
    m_guardLastRebalanceTime = Time(0);
    m_guardLifecycleTraceSink = NULL;
    m_guardControllerTraceSink = NULL;
    m_guardGrantTraceSink = NULL;
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
Ptr<RdmaQueuePair> RdmaHw::GetQp(uint64_t key) {
    auto it = m_qpMap.find(key);

    // lookup main memory
    if (it != m_qpMap.end()) {
        return it->second;
    }

    return NULL;
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
    qp->SetFlowId(flow_id);
    qp->SetTimeout(m_waitAckTimeout);

    if (m_irn) {
        qp->irn.m_enabled = m_irn;
        qp->irn.m_bdp = m_irn_bdp;
        qp->irn.m_rtoLow = m_irn_rtoLow;
        qp->irn.m_rtoHigh = m_irn_rtoHigh;
    }

    // add qp
    uint32_t nic_idx = GetNicIdxOfQp(qp);

    // For GUARD modes, borrow homa's idea of routing short
    // messages to higher-priority switch queues (lower pg = higher prio in
    // this codebase). All packets of one flow stay on the same pg, so the
    // QP-key / pause-check / RCC-grant routing all stay consistent.
    // Bucket boundaries match homa's unscheduled cutoffs.
    if ((m_cc_mode == 11 || m_cc_mode == 13) && m_guardSizePriority) {
        DataRate line_rate = m_nic[nic_idx].dev->GetDataRate();
        uint64_t bdp_bytes = baseRtt * line_rate.GetBitRate() / 8000000000lu;
        if (bdp_bytes == 0) bdp_bytes = 1;
        if (size < bdp_bytes / 4)      qp_pg = 1;
        else if (size < bdp_bytes / 2) qp_pg = 2;
        else if (size < bdp_bytes)     qp_pg = 3;
        else                           qp_pg = 4;
        qp->m_pg = qp_pg;
    }

    m_nic[nic_idx].qpGrp->AddQp(qp);
    uint64_t key = GetQpKey(dip.Get(), sport, dport, qp_pg);
    m_qpMap[key] = qp;

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

    // delete
    m_qpMap.erase(key);
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

    // delete
    m_rxQpMap.erase(key);
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
        FlowStatTag fst;
        if (p->PeekPacketTag(fst)) {
            uint8_t flow_tag = fst.GetType();
            bool flow_start = flow_tag == FlowStatTag::FLOW_START ||
                              flow_tag == FlowStatTag::FLOW_START_AND_END;
            // Derive BDP from this flow's baseRtt + receiver's line rate
            // instead of the hardcoded 104000 (which assumed leaf_spine 100G/
            // 8.32µs RTT). On other topologies (different RTT or rate) the
            // hardcoded value would mis-classify short vs. long flows.
            uint64_t bdp = 104000;  // fallback if tag has no baseRtt
            if (fst.HasBaseRtt()) {
                uint32_t nic_idx = GetNicIdxOfRxQp(rxQp);
                DataRate rate = m_nic[nic_idx].dev->GetDataRate();
                bdp = (uint64_t)(fst.GetBaseRttSeconds() * rate.GetBitRate() / 8.0);
                if (bdp == 0) bdp = 104000;  // safety
            }
            if (flow_start && (!m_guardSelectiveRegistration || flow_size > bdp)) {
                HandleRccRequest(rxQp, p, ch);
            }
            if (rxQp->m_base_rtt_sec == 0 && fst.HasBaseRtt()) {
                rxQp->m_base_rtt_sec = fst.GetBaseRttSeconds();
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
            if (HandleRccRemove(rxQp, p, ch, GUARD_RELEASE_COMPLETION, 0)) {
                m_guardCompletionReleases++;
            }
            TraceGuardCompletion(rxQp);
        } else if (m_guardProactiveRelease &&
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
                if (HandleRccRemove(rxQp, p, ch, GUARD_RELEASE_PROACTIVE, v_remain)) {
                    m_guardProactiveReleases++;
                }
                rxQp->m_proactive_released = true;
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
    Ptr<RdmaQueuePair> qp = GetQp(key);
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
    uint16_t pg = ch.ack.pg;
    uint16_t dport = ch.ack.dport;
    uint16_t sport = ch.ack.sport;
    uint64_t key = GetQpKey(ch.sip, dport, sport, pg);
    Ptr<RdmaQueuePair> qp = GetQp(key);

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

    uint32_t received_val = ch.ack.seq;

    // Use string constructor to avoid overflow
    std::string rate_str = std::to_string(received_val) + "Mbps";
    DataRate curRate(rate_str);

    qp->hp.m_grantRate = curRate;
    m_guardRateGrantsReceived++;
    TraceGuardGrantReceive(qp, p, curRate.GetBitRate());
    DataRate old_rate = qp->m_rate;
    SyncHwRate(qp, qp->hp.m_curRate);
    const char *binding = qp->hp.m_curRate < qp->hp.m_grantRate
                              ? "reactive"
                              : qp->hp.m_grantRate < qp->hp.m_curRate ? "grant" : "tie";
    TraceGuardControllerEvent(qp, "grant", qp->hp.m_curRate, binding,
                              qp->m_rate != old_rate, false, 0, qp->snd_nxt, -1.0, -1.0);

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
    Ptr<RdmaQueuePair> qp = GetQp(key);
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

    if (m_cc_mode == 3 || m_cc_mode == 11) {
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
    } else if (ch.l3Prot == 0xFB) {  // guard rate grant or homa-simple credit
        if (m_cc_mode == 11 || m_cc_mode == 13) {
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

    const char *binding = qp->hp.m_curRate < qp->hp.m_grantRate
                              ? "reactive"
                              : qp->hp.m_grantRate < qp->hp.m_curRate ? "grant" : "tie";
    TraceGuardControllerEvent(qp, "complete", qp->hp.m_curRate, binding, false, false,
                              0, qp->snd_nxt, -1.0, -1.0);

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
    ipHeader.SetTos(0);
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
        } else if (ch.l3Prot == 0xFB || ch.l3Prot == 0xFC || ch.l3Prot == 0xFD || ch.l3Prot == 0xFF || ch.l3Prot == 0xFA) {  // ACK, NACK, CNP, homa ctrl
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

    if (qp->hp.m_grantRate < final_rate) {
        final_rate = qp->hp.m_grantRate;
    }

    if (final_rate < m_minRate) final_rate = m_minRate;
    if (final_rate > qp->m_max_rate) final_rate = qp->m_max_rate;

    ChangeRate(qp, final_rate);
}

void RdmaHw::HandleRccRequest(Ptr<RdmaRxQueuePair> rx_qp, Ptr<Packet> p, CustomHeader &ch) {
    if (m_rate_flow_ctl_set.find(PeekPointer(rx_qp)) != m_rate_flow_ctl_set.end()) {
        std::cout << "Warning: duplicated RCC request for flow!" << std::endl;
        // exit(1);
        return;
    }
    uint64_t active_before = m_rate_flow_ctl_set.size();
    m_rate_flow_ctl_set.emplace(PeekPointer(rx_qp));
    m_guardRegistrations++;
    if (m_guardSelectiveRegistration) m_guardSelectedRegistrations++;
    m_guardMaxActiveFlows = std::max<uint64_t>(m_guardMaxActiveFlows,
                                               m_rate_flow_ctl_set.size());

    FlowIDNUMTag fit;
    uint64_t flow_size = p->PeekPacketTag(fit) ? fit.GetFlowSize() : 0;
    TraceGuardRegistration(rx_qp, flow_size, active_before, m_rate_flow_ctl_set.size());

    RedistributeGuardRates("registration");
    ScheduleGuardRebalance();
}

bool RdmaHw::HandleRccRemove(Ptr<RdmaRxQueuePair> rx_qp, Ptr<Packet> p, CustomHeader &ch,
                             GuardReleaseReason reason, uint64_t remaining_bytes) {
    if (m_rate_flow_ctl_set.find(PeekPointer(rx_qp)) == m_rate_flow_ctl_set.end()) {
        return false;
    }
    uint64_t active_before = m_rate_flow_ctl_set.size();
    m_rate_flow_ctl_set.erase(PeekPointer(rx_qp));
    TraceGuardRelease(rx_qp, reason, remaining_bytes, active_before,
                      m_rate_flow_ctl_set.size());

    // No grant needs to be sent after the last controlled flow leaves.  In
    // particular, do not compute C / N for N == 0.
    if (m_rate_flow_ctl_set.empty()) {
        if (m_guardRebalanceEvent.IsRunning()) Simulator::Cancel(m_guardRebalanceEvent);
        m_guardLastRebalanceTime = Time(0);
        return true;
    }
    RedistributeGuardRates("release");
    ScheduleGuardRebalance();
    return true;
}

void RdmaHw::RedistributeGuardRates(const char *set_change) {
    if (m_rate_flow_ctl_set.empty()) return;
    RdmaRxQueuePair *sample = *m_rate_flow_ctl_set.begin();
    uint32_t nic_idx = GetNicIdxOfRxQp(sample);
    uint64_t line_rate_bps = m_nic[nic_idx].dev->GetDataRate().GetBitRate();
    uint64_t equal_rate_bps = line_rate_bps / m_rate_flow_ctl_set.size();
    uint32_t rate_mbps = std::max<uint32_t>(1, equal_rate_bps / 1000000);
    for (auto *flow : m_rate_flow_ctl_set) {
        flow->m_guard_interval_bytes = 0;
        flow->m_guard_demand_samples = 0;
        flow->m_guard_below_threshold_samples = 0;
        flow->m_guard_demand_limited = false;
        flow->m_guard_grant_rate_bps = (uint64_t)rate_mbps * 1000000;
        SendRateControlPacket(flow, rate_mbps, set_change);
    }
    if (m_guardRebalanceEvent.IsRunning()) Simulator::Cancel(m_guardRebalanceEvent);
    m_guardLastRebalanceTime = Time(0);
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
    std::vector<std::pair<uint64_t, RdmaRxQueuePair*> > demands;
    demands.reserve(m_rate_flow_ctl_set.size());

    for (auto *flow : m_rate_flow_ctl_set) {
        uint64_t measured = (uint64_t)(
            (double)flow->m_guard_interval_bytes * 8.0 / elapsed.GetSeconds());
        flow->m_guard_interval_bytes = 0;
        flow->m_guard_measured_rate_bps = measured;
        flow->m_guard_demand_samples++;

        uint64_t grant = std::max<uint64_t>(flow->m_guard_grant_rate_bps, 1);
        if (m_rate_flow_ctl_set.size() == 1) {
            flow->m_guard_demand_limited = false;
            flow->m_guard_below_threshold_samples = 0;
        } else if (flow->m_guard_demand_limited) {
            if ((double)measured >= 0.95 * grant) {
                flow->m_guard_demand_limited = false;
                flow->m_guard_below_threshold_samples = 0;
            }
        } else {
            if ((double)measured < m_guardDemandThreshold * grant) {
                flow->m_guard_below_threshold_samples++;
            } else {
                flow->m_guard_below_threshold_samples = 0;
            }
            if (flow->m_guard_below_threshold_samples >= 3) {
                flow->m_guard_demand_limited = true;
                flow->m_guard_below_threshold_samples = 0;
            }
        }
        uint64_t demand = unlimited;
        if (flow->m_guard_demand_limited) {
            demand = std::min<uint64_t>(
                line_rate_bps, (uint64_t)(measured / m_guardDemandThreshold));
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
    if (update_vector) {
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

void RdmaHw::TraceGuardControllerEvent(Ptr<RdmaQueuePair> qp, const char *event_type,
                                       DataRate hpcc_rate, const char *binding,
                                       bool rate_changed, bool fast_react, uint32_t nhop,
                                       uint32_t next_seq, double congestion_metric,
                                       double threshold_ratio) {
    GuardControllerTraceSink *sink = m_guardControllerTraceSink;
    if (sink == NULL || sink->file == NULL) return;
    sink->attempted++;
    if (sink->written >= sink->max_lines) return;
    fprintf(sink->file,
            "%ld,%d,%u,%u,%s,%lu,%lu,%lu,%s,%u,%u,%u,%u,%.9f,%.9f,%.9f\n",
            Simulator::Now().GetNanoSeconds(), qp->m_flow_id, qp->sip.Get(), qp->dip.Get(),
            event_type, hpcc_rate.GetBitRate(), qp->hp.m_grantRate.GetBitRate(),
            qp->m_rate.GetBitRate(), binding, rate_changed ? 1 : 0, fast_react ? 1 : 0,
            nhop, next_seq, congestion_metric, m_targetUtil, threshold_ratio);
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

void RdmaHw::TraceGuardGrant(Ptr<RdmaRxQueuePair> qp, const char *event,
                             const char *set_change, uint64_t active_flows,
                             uint64_t line_rate_bps, uint64_t grant_rate_bps,
                             uint64_t next_seq, uint64_t serialized_bytes) {
    GuardGrantTraceSink *sink = m_guardGrantTraceSink;
    if (sink == NULL || sink->file == NULL) return;
    sink->attempted++;
    if (sink->written >= sink->max_lines) return;
    fprintf(sink->file, "%ld,%s,%s,%u,%d,%u,%u,%lu,%lu,%lu,%lu,%lu\n",
            Simulator::Now().GetNanoSeconds(), event, set_change, m_node->GetId(),
            qp->m_flow_id, qp->dip, qp->sip, active_flows, line_rate_bps,
            grant_rate_bps, next_seq, serialized_bytes);
    sink->written++;
}

void RdmaHw::TraceGuardGrantReceive(Ptr<RdmaQueuePair> qp, Ptr<Packet> packet,
                                    uint64_t grant_rate_bps) {
    GuardGrantTraceSink *sink = m_guardGrantTraceSink;
    if (sink == NULL || sink->file == NULL) return;
    sink->attempted++;
    if (sink->written >= sink->max_lines) return;
    uint64_t line_rate_bps = 0;
    uint32_t nic_idx = GetNicIdxOfQp(qp);
    if (nic_idx < m_nic.size() && m_nic[nic_idx].dev != NULL) {
        line_rate_bps = m_nic[nic_idx].dev->GetDataRate().GetBitRate();
    }
    fprintf(sink->file, "%ld,received,none,%u,%d,%u,%u,0,%lu,%lu,%lu,%u\n",
            Simulator::Now().GetNanoSeconds(), m_node->GetId(), qp->m_flow_id,
            qp->sip.Get(), qp->dip.Get(), line_rate_bps, grant_rate_bps,
            qp->snd_nxt, packet->GetSize());
    sink->written++;
}

void RdmaHw::SendRateControlPacket(Ptr<RdmaRxQueuePair> rx_qp,
                                   uint32_t rate_data, const char *set_change) {
    m_guardRateGrantsSent++;
    qbbHeader seqh;
    seqh.SetSeq(rate_data); // PS: send rate in Mbps, used field: seq
    seqh.SetPG(rx_qp->m_guard_pg);
    seqh.SetSport(rx_qp->sport);
    seqh.SetDport(rx_qp->dport);

    Ptr<Packet> newp = Create<Packet>(std::max(60 - 14 - 20 - (int)seqh.GetSerializedSize(), 0));
    newp->AddHeader(seqh);

    Ipv4Header head;  // Prepare IPv4 header
    head.SetDestination(Ipv4Address(rx_qp->dip));
    head.SetSource(Ipv4Address(rx_qp->sip));
    head.SetProtocol(0xFB);  // 0xFB rate grant (guard)
    head.SetTtl(64);
    head.SetPayloadSize(newp->GetSize());
    head.SetIdentification(rx_qp->m_ipid++);

    newp->AddHeader(head);
    AddHeader(newp, 0x800);  // Attach PPP header
    m_guardRateGrantBytesSent += newp->GetSize();
    TraceGuardGrant(rx_qp, "sent", set_change, m_rate_flow_ctl_set.size(),
                    m_nic[GetNicIdxOfRxQp(rx_qp)].dev->GetDataRate().GetBitRate(),
                    static_cast<uint64_t>(rate_data) * 1000000,
                    rx_qp->ReceiverNextExpectedSeq, newp->GetSize());

    // send
    uint32_t nic_idx = GetNicIdxOfRxQp(rx_qp);
    m_nic[nic_idx].dev->RdmaEnqueueHighPrioQ(newp);
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
    Ptr<RdmaQueuePair> qp = GetQp(key);
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
    Ptr<RdmaQueuePair> qp = GetQp(key);
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
    RdmaRxQueuePair* hkey = PeekPointer(rx_qp);
    auto it = flow_hash.find(hkey);
    if (it == flow_hash.end()) {
        // First DATA observed for this rx_qp.
        std::unique_ptr<HomaFlow> new_flow(new HomaFlow);
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
        SendCompletionNotice(p_flow);
        rdma_hw->m_homaMessagesCompleted++;
        active.erase(hkey);
        flow_hash.erase(hkey);
        if (flow_hash.empty()) is_stall_scheduled = false;
        return;
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
        }
        if (!flow->fully_granted()) {
            active.insert(flow);
        }
    }

    if (!active.empty()) {
        Simulator::Schedule(NanoSeconds(pacing_interval),
                            &RdmaHw::HomaScheduler::Schedule, this);
    } else {
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
    hfh.SetMessageId((uint64_t)flow->rx_qp->m_flow_id);
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
    hfh.SetMessageId((uint64_t)flow->rx_qp->m_flow_id);
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
    hfh.SetMessageId((uint64_t)flow->rx_qp->m_flow_id);

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
                double u = txRate / ih.hop[i].GetLineRate() +
                           (double)std::min(ih.hop[i].GetQlen(), qp->hp.hop[i].GetQlen()) *
                               qp->m_max_rate.GetBitRate() / ih.hop[i].GetLineRate() / qp->m_win;
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
            if (!m_multipleRate) {
                // for aggregate (single R)
                if (updated_any) {
                    if (dt > qp->m_baseRtt) dt = qp->m_baseRtt;
                    qp->hp.u = (qp->hp.u * (qp->m_baseRtt - dt) + U * dt) / double(qp->m_baseRtt);
                    max_c = qp->hp.u / m_targetUtil;

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
                        double c = qp->hp.hopState[i].u / m_targetUtil;
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
                    if (new_rate < qp->hp.m_grantRate) {
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
                                              observed_metric, threshold_ratio);
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
