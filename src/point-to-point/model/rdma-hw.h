#ifndef RDMA_HW_H
#define RDMA_HW_H

#include <ns3/custom-header.h>
#include <ns3/node.h>
#include <ns3/rdma.h>
#include <ns3/selective-packet-queue.h>

#include <unordered_map>
#include <unordered_set>
#include <cstdio>
#include <cstdint>
#include <memory>
#include <map>
#include <string>
#include <vector>

#include "qbb-net-device.h"
#include "rdma-queue-pair.h"

namespace ns3 {

struct RdmaInterfaceMgr {
    Ptr<QbbNetDevice> dev;
    Ptr<RdmaQueuePairGroup> qpGrp;

    RdmaInterfaceMgr() : dev(NULL), qpGrp(NULL) {}
    RdmaInterfaceMgr(Ptr<QbbNetDevice> _dev) { dev = _dev; }
};

// One sink is shared by every receiver NIC in a simulation.  Admission and
// output are both capped by max_lines, which bounds the file and the aggregate
// amount of lifecycle state retained while flows are active.
struct GuardLifecycleTraceSink {
    FILE *file;
    uint64_t max_lines;
    uint64_t admitted;
    uint64_t written;

    GuardLifecycleTraceSink()
        : file(NULL), max_lines(0), admitted(0), written(0) {}
};

struct GuardControllerTraceSink {
    FILE *file;
    uint64_t max_lines;
    uint64_t attempted;
    uint64_t written;

    GuardControllerTraceSink() : file(NULL), max_lines(0), attempted(0), written(0) {}
};

struct GuardGrantTraceSink {
    FILE *file;
    uint64_t max_lines;
    uint64_t attempted;
    uint64_t written;

    GuardGrantTraceSink() : file(NULL), max_lines(0), attempted(0), written(0) {}
};

struct GuardTransitionPrefixWaiterBudgetInput {
    uint64_t flowSizeBytes;
    uint64_t exactGateBytes;
    uint64_t baseRttNs;
};

struct GuardTransitionPrefixIncumbentBudgetInput {
    uint64_t acknowledgedUpperBoundBps;
    uint64_t baseRttNs;
};

struct GuardTransitionPrefixWireBudget {
    uint64_t roundedPayloadBudgetBytes;
    uint64_t packetCount;
    uint64_t headerBytesPerPacket;
    uint64_t wireBytes;
    uint64_t incumbentCount;
    uint64_t occupancyBps;
    uint64_t receiverCapacityBps;
    uint64_t residualBps;
    uint64_t serializationNs;
    uint64_t maxRttNs;
    uint64_t delayNs;
};

class RdmaHw : public Object {
   public:
    static TypeId GetTypeId(void);
    RdmaHw();

    Ptr<Node> m_node;
    DataRate m_minRate;  //< Min sending rate
    uint32_t m_mtu;
    uint32_t m_cc_mode;
    double m_nack_interval;
    uint32_t m_chunk;
    uint32_t m_ack_interval;
    bool m_backto0;
    bool m_var_win, m_fast_react;
    bool m_rateBound;
    std::vector<RdmaInterfaceMgr> m_nic;  // list of running nic controlled by this RdmaHw
    std::unordered_map<uint64_t, Ptr<RdmaQueuePair>> m_qpMap;      // mapping from uint64_t to qp
    std::unordered_map<uint64_t, Ptr<RdmaRxQueuePair>> m_rxQpMap;  // mapping from uint64_t to rx qp
    std::unordered_map<uint32_t, std::vector<int>>
        m_rtTable;  // map from ip address (u32) to possible ECMP port (index of dev)

    // qp complete callback
    typedef Callback<void, Ptr<RdmaQueuePair>> QpCompleteCallback;
    QpCompleteCallback m_qpCompleteCallback;

    void SetNode(Ptr<Node> node);
    void Setup(QpCompleteCallback cb);  // setup shared data and callbacks with the QbbNetDevice

    /* Akashic Record of finished QP */
    std::unordered_set<uint64_t> akashic_Qp;    // instance for each src
    std::unordered_set<uint64_t> akashic_RxQp;  // instance for each dst
    static uint64_t nAllPkts;                   // number of total packets

    /* TxQpeueuPair */
    static uint64_t GetQpKey(uint32_t dip, uint16_t sport, uint16_t dport,
                             uint16_t pg);          // get the lookup key for m_qpMap
    Ptr<RdmaQueuePair> GetQp(uint64_t key);         // get the qp
    uint32_t GetNicIdxOfQp(Ptr<RdmaQueuePair> qp);  // get the NIC index of the qp
    void DeleteQueuePair(Ptr<RdmaQueuePair> qp);    // delete TxQP

    void AddQueuePair(uint64_t size, uint16_t pg, Ipv4Address _sip, Ipv4Address _dip,
                      uint16_t _sport, uint16_t _dport, uint32_t win, uint64_t baseRtt,
                      int32_t flow_id);  // add a nw qp (new send)
    void AddQueuePair(uint64_t size, uint16_t pg, Ipv4Address _sip, Ipv4Address _dip,
                      uint16_t _sport, uint16_t _dport, uint32_t win, uint64_t baseRtt) {
        this->AddQueuePair(size, pg, _sip, _dip, _sport, _dport, win, baseRtt, -1);
    }

    /* RxQueuePair */
    static uint64_t GetRxQpKey(uint32_t dip, uint16_t dport, uint16_t sport, uint16_t pg);
    Ptr<RdmaRxQueuePair> GetRxQp(uint32_t sip, uint32_t dip, uint16_t sport, uint16_t dport,
                                 uint16_t pg, bool create);  // get a rxQp
    uint32_t GetNicIdxOfRxQp(Ptr<RdmaRxQueuePair> q);        // get the NIC index of the rxQp
    void DeleteRxQp(uint32_t dip, uint16_t dport, uint16_t sport, uint16_t pg);  // delete RxQP

    int ReceiveUdp(Ptr<Packet> p, CustomHeader &ch);
    int ReceiveCnp(Ptr<Packet> p, CustomHeader &ch);
    int ReceiveAck(Ptr<Packet> p, CustomHeader &ch);  // handle both ACK and NACK
    int ReceiveRate(Ptr<Packet> p, CustomHeader &ch); // guard rate-grant packet
    int ReceiveGuardGrantAck(Ptr<Packet> p, CustomHeader &ch);
    int ReceiveGuardCapReport(Ptr<Packet> p, CustomHeader &ch);
    int ReceiveHomaSimpleCredit(Ptr<Packet> p, CustomHeader &ch); // homa-simple credit packet
    int Receive(Ptr<Packet> p,
                CustomHeader &
                    ch);  // callback function that the QbbNetDevice should use when receive
                          // packets. Only NIC can call this function. And do not call this upon PFC

    void CheckandSendQCN(Ptr<RdmaRxQueuePair> q);
    int ReceiverCheckSeq(uint32_t seq, Ptr<RdmaRxQueuePair> q, uint32_t size, bool &cnp);
    void AddHeader(Ptr<Packet> p, uint16_t protocolNumber);
    static uint16_t EtherToPpp(uint16_t protocol);

    void RecoverQueue(Ptr<RdmaQueuePair> qp);
    void QpComplete(Ptr<RdmaQueuePair> qp);
    void SetLinkDown(Ptr<QbbNetDevice> dev);

    // call this function after the NIC is setup
    void AddTableEntry(Ipv4Address &dstAddr, uint32_t intf_idx);
    void ClearTable();
    void RedistributeQp();

    Ptr<Packet> GetNxtPacket(Ptr<RdmaQueuePair> qp);  // get next packet to send, inc snd_nxt
    Ptr<Packet> GetNxtPacketHomaSimple(Ptr<RdmaQueuePair> qp);  // homa-simple-mode next-packet
    void PktSent(Ptr<RdmaQueuePair> qp, Ptr<Packet> pkt, Time interframeGap);
    void UpdateNextAvail(Ptr<RdmaQueuePair> qp, Time interframeGap, uint32_t pkt_size);
    void ChangeRate(Ptr<RdmaQueuePair> qp, DataRate new_rate);

    void HandleTimeout(Ptr<RdmaQueuePair> qp, Time rto);

    /* statistics */
    uint32_t cnp_by_ecn;
    uint32_t cnp_by_ooo;
    uint32_t cnp_total;
    // Constant-space, cumulative evidence for CC_MODE_DCQCN.  The legacy
    // 100us CNP monitor resets its buckets, while these counters do not.
    uint64_t m_dcqcnCnpGeneratedEcn;
    uint64_t m_dcqcnCnpGeneratedOoo;
    uint64_t m_dcqcnCnpReceived;
    uint64_t m_dcqcnAlphaUpdates;
    uint64_t m_dcqcnAlphaCnpUpdates;
    uint64_t m_dcqcnRateDecreaseEvents;
    uint64_t m_dcqcnActualRateDecreases;
    uint64_t m_dcqcnRateIncreaseEvents;
    uint64_t m_dcqcnActualRateIncreases;
    size_t getIrnBufferOverhead();  // get buffer overhead for IRN

    /******************************
     * Mellanox's version of DCQCN
     *****************************/
    double m_g;               // feedback weight
    double m_rateOnFirstCNP;  // the fraction of line rate to set on first CNP
    bool m_EcnClampTgtRate;
    double m_rpgTimeReset;
    double m_rateDecreaseInterval;
    uint32_t m_rpgThreshold;
    double m_alpha_resume_interval;
    DataRate m_rai;   //< Rate of additive increase
    DataRate m_rhai;  //< Rate of hyper-additive increase

    // the Mellanox's version of alpha update:
    // every fixed time slot, update alpha.
    void UpdateAlphaMlx(Ptr<RdmaQueuePair> q);
    void ScheduleUpdateAlphaMlx(Ptr<RdmaQueuePair> q);

    // Mellanox's version of CNP receive
    void cnp_received_mlx(Ptr<RdmaQueuePair> q);

    // Mellanox's version of rate decrease
    // It checks every m_rateDecreaseInterval if CNP arrived (m_decrease_cnp_arrived).
    // If so, decrease rate, and reset all rate increase related things
    void CheckRateDecreaseMlx(Ptr<RdmaQueuePair> q);
    void ScheduleDecreaseRateMlx(Ptr<RdmaQueuePair> q, uint32_t delta);

    // Mellanox's version of rate increase
    void RateIncEventTimerMlx(Ptr<RdmaQueuePair> q);
    void RateIncEventMlx(Ptr<RdmaQueuePair> q);
    void FastRecoveryMlx(Ptr<RdmaQueuePair> q);
    void ActiveIncreaseMlx(Ptr<RdmaQueuePair> q);
    void HyperIncreaseMlx(Ptr<RdmaQueuePair> q);

    // Implement Timeout according to IB Spec Vol. 1 C9-139.
    // For an HCA requester using Reliable Connection service, to detect missing responses,
    // every Send queue is required to implement a Transport Timer to time outstanding requests.
    Time m_waitAckTimeout;

    /***********************
     * Guard CC (rate grant)
     ***********************/
    double m_guardEwmaBeta;
    double m_guardReleaseGamma;
    bool m_guardSelectiveRegistration;
    bool m_guardProactiveRelease;
    bool m_guardKeepLastHopInt;
    bool m_guardSizePriority;
    bool m_guardSenderSrpt;
    bool m_guardOneRttBypass;
    bool m_guardTailBypass;
    double m_guardTailBypassBdps;
    bool m_guardTailCongestionGate;
    double m_guardTailSafeRatio;
    uint32_t m_guardTailSafeSamples;
    bool m_guardAdaptiveFabricTarget;
    double m_guardTargetFloor;
    double m_guardQueueBudgetBdps;
    double m_guardAdaptiveTargetMaxBdps;
    uint32_t m_guardAckIntervalPackets;
    bool m_guardFixedWindow;
    bool m_guardRemainingAware;
    double m_guardMinShareFraction;
    double m_guardRemainingExponent;
    uint32_t m_guardReceiverConcurrency;
    double m_guardConcurrencyMinBdps;
    double m_guardGrantRefreshBdps;
    Time m_guardMembershipCoalesceWindow;
    Time m_guardInitialCollectionQuietWindow;
    uint32_t m_guardMembershipCoalesceMaxWindows;
    bool m_guardInitialCollectionFullDeadline;
    double m_guardGrantReliabilityRtts;
    uint32_t m_guardSrptQuantumPackets;
    bool m_guardWorkConserving;
    bool m_guardCapAwareReclaim;
    double m_guardCapHeadroom;
    double m_guardCapMinShareFraction;
    Time m_guardRebalanceInterval;
    double m_guardDemandThreshold;
    double m_guardReceiverUtilThreshold;
    uint64_t m_guardRateGrantsSent;
    uint64_t m_guardRateGrantBytesSent;
    uint64_t m_guardRateGrantsReceived;
    // Calls into HPCC feedback processing in either HPCC-only or full-GUARD mode.
    uint64_t m_guardHpccFeedbackUpdates;
    // Feedback records that contain at least one INT hop, and updates that
    // consequently apply an HPCC-derived rate to the sender.
    uint64_t m_guardHpccValidFeedback;
    uint64_t m_guardHpccRateUpdatesApplied;
    uint64_t m_guardHpccFullComputations;
    uint64_t m_guardHpccFastComputations;
    uint64_t m_guardHpccActualRateChanges;
    uint64_t m_guardReactiveBindingUpdates;
    uint64_t m_guardGrantBindingUpdates;
    uint64_t m_guardTieBindingUpdates;
    uint64_t m_guardReactiveBindingRateChanges;
    uint64_t m_guardGrantBindingRateChanges;
    uint64_t m_guardTieBindingRateChanges;
    // Pacing-rate changes caused directly by a received receiver grant.
    uint64_t m_guardGrantEventRateChanges;
    uint64_t m_guardIntHopsBeforeStrip;
    uint64_t m_guardIntHopsAfterStrip;
    uint64_t m_guardIntRecordsStripped;
    uint64_t m_guardRegistrations;
    uint64_t m_guardSelectedRegistrations;
    uint64_t m_guardProactiveReleases;
    uint64_t m_guardCompletionReleases;
    uint64_t m_guardMaxActiveFlows;
    uint64_t m_guardRebalanceEvents;
    uint64_t m_guardAdaptiveGrantUpdates;
    uint64_t m_guardCapReportsSent;
    uint64_t m_guardCapReportBytesSent;
    uint64_t m_guardCapReportsReceived;
    uint64_t m_guardFabricBoundReportsReceived;
    uint64_t m_guardCapRebalanceEvents;
    uint64_t m_guardCapGrantUpdates;
    uint64_t m_guardCapMaxReclaimedBps;
    uint64_t m_guardRemainingRefreshEvents;
    uint64_t m_guardMembershipChangesDeferred;
    uint64_t m_guardMembershipBatches;
    uint64_t m_guardMembershipMaxBatch;
    uint64_t m_guardMembershipEmptyCancellations;
    uint64_t m_guardMembershipTimerReschedules;
    uint64_t m_guardReleaseThresholdDeferrals;
    uint64_t m_guardInitialCollectionStarts;
    uint64_t m_guardInitialCollectionFlushes;
    uint64_t m_guardInitialCollectionDeferredChanges;
    uint64_t m_guardInitialCollectionReschedules;
    uint64_t m_guardInitialCollectionCancellations;
    uint64_t m_guardInitialCollectionQuietFlushes;
    uint64_t m_guardInitialCollectionHardFlushes;
    uint64_t m_guardInitialCollectionWaitNs;
    uint64_t m_guardInitialCollectionMaxWaitNs;
    uint64_t m_guardReliabilityRefreshEvents;
    uint64_t m_guardReliabilityGrantUpdates;
    uint64_t m_guardGrantAcksSent;
    uint64_t m_guardGrantAckBytesSent;
    uint64_t m_guardGrantAcksReceived;
    uint64_t m_guardGrantAckBytesReceived;
    uint64_t m_guardGrantAcksStale;
    uint64_t m_guardStaleGrantsReceived;
    uint64_t m_guardAckRequiredBatches;
    uint64_t m_guardAckOptionalBatches;
    uint64_t m_guardAckRequiredGrantsSent;
    uint64_t m_guardAckOptionalGrantsSent;
    uint64_t m_guardGenerationZeroRejected;
    uint64_t m_guardGenerationMismatchRejected;
    uint64_t m_guardFullyAckedBatches;
    uint64_t m_guardFirstGrantGatedFlows;
    uint64_t m_guardFirstGrantGateReleases;
    uint64_t m_guardProgressEventsCoalesced;
    uint64_t m_guardFastpathTransactions;
    uint64_t m_guardFastpathPrepareBatches;
    uint64_t m_guardFastpathPrepareGrants;
    uint64_t m_guardFastpathPrepareAcks;
    uint64_t m_guardFastpathActivateBatches;
    uint64_t m_guardFastpathActivateGrants;
    uint64_t m_guardFastpathActivateAcks;
    uint64_t m_guardTransitionPrepareBatches;
    uint64_t m_guardTransitionPrepareGrants;
    uint64_t m_guardTransitionPrepareAcks;
    uint64_t m_guardTransitionActivateBatches;
    uint64_t m_guardTransitionActivateGrants;
    uint64_t m_guardTransitionActivateAcks;
    uint64_t m_guardFastpathOptionalReleaseGenerations;
    uint64_t m_guardFastpathOptionalReleaseGrants;
    uint64_t m_guardFastpathQueuedMembershipChanges;
    uint64_t m_guardFastpathHighFanInTransitions;
    uint64_t m_guardFastpathHighCollectionFlushes;
    uint64_t m_guardFastpathWaitersActivated;
    uint64_t m_guardFastpathPostTransitionFastGrants;
    uint64_t m_guardFastpathBarrierViolations;
    uint64_t m_guardFastpathEarlyUnlocks;
    uint64_t m_guardFastpathJoinQueueMax;
    uint64_t m_guardFastpathHighTransitionRegisteredN;
    uint64_t m_guardFastpathHighTransitionWaiters;
    uint64_t m_guardFastpathPrefixCloseNs;
    uint64_t m_guardFastpathTransitionPrepareStartNs;
    uint64_t m_guardFastpathCollectionFlushNs;
    uint64_t m_guardFastpathLastTransactionCloseNs;
    uint64_t m_guardFastPrepareWireGrantFrames;
    uint64_t m_guardFastActivateWireGrantFrames;
    uint64_t m_guardTransitionPrepareWireGrantFrames;
    uint64_t m_guardTransitionActivateWireGrantFrames;
    uint64_t m_guardReleaseWireGrantFrames;
    uint64_t m_guardFastPrepareWireAckFrames;
    uint64_t m_guardFastActivateWireAckFrames;
    uint64_t m_guardTransitionPrepareWireAckFrames;
    uint64_t m_guardTransitionActivateWireAckFrames;
    uint64_t m_guardFastpathUnattributedGrantFrames;
    uint64_t m_guardFastpathUnattributedAckFrames;
    uint64_t m_guardTransitionPrefixBarrierStarts;
    uint64_t m_guardTransitionPrefixBarrierReady;
    uint64_t m_guardTransitionPrefixBarrierTimeouts;
    uint64_t m_guardTransitionPrefixDegradedTransitions;
    uint64_t m_guardTransitionPrefixWaitersRequired;
    uint64_t m_guardTransitionPrefixWaitersReady;
    uint64_t m_guardTransitionPrefixTargetBytes;
    uint64_t m_guardTransitionPrefixReceivedBytes;
    uint64_t m_guardTransitionPrefixRemainingBytes;
    uint64_t m_guardTransitionPrefixWaitNs;
    uint64_t m_guardTransitionPrefixMaxWaitNs;
    uint64_t m_guardTransitionPrefixDeadlineNs;
    uint64_t m_guardTransitionPrefixStartNs;
    uint64_t m_guardTransitionPrefixReadyNs;
    uint64_t m_guardTransitionFallbackBatches;
    uint64_t m_guardTransitionFallbackMaxBatch;
    uint64_t m_guardTransitionFallbackClosedBatches;
    uint64_t m_guardTransitionFallbackOrderViolations;
    uint64_t m_guardTransitionPrefixBarrierViolations;
    uint64_t m_guardTransitionPrefixWatchdogRoundedPayloadBudgetBytes;
    uint64_t m_guardTransitionPrefixWatchdogPacketCount;
    uint64_t m_guardTransitionPrefixWatchdogHeaderBytesPerPacket;
    uint64_t m_guardTransitionPrefixWatchdogWireBytes;
    uint64_t m_guardTransitionPrefixWatchdogIncumbentCount;
    uint64_t m_guardTransitionPrefixWatchdogOccupancyBps;
    uint64_t m_guardTransitionPrefixWatchdogReceiverCapacityBps;
    uint64_t m_guardTransitionPrefixWatchdogResidualBps;
    uint64_t m_guardTransitionPrefixWatchdogSerializationNs;
    uint64_t m_guardTransitionPrefixWatchdogMaxRttNs;
    uint64_t m_guardTransitionPrefixWatchdogDelayNs;
    uint64_t m_guardTransitionPrefixWatchdogStartNs;
    uint64_t m_guardTransitionPrefixWatchdogDeadlineNs;
    uint64_t m_guardTransitionPrefixWatchdogBudgetRecords;
    bool m_guardTransitionPrefixWatchdogNonReconstructable;
    uint64_t m_guardConcurrencyLimitedAllocations;
    uint64_t m_guardConcurrencyMaxDeferredFlows;
    uint64_t m_guardOneRttBypassFlows;
    uint64_t m_guardOneRttBypassFeedbacks;
    uint64_t m_guardOneRttAcksSuppressed;
    uint64_t m_guardLongAcksSuppressed;
    uint64_t m_guardTailBypassFlows;
    uint64_t m_guardTailBypassFeedbacks;
    uint64_t m_guardTailGateDeferrals;
    uint64_t m_guardTailGateQualifiedFlows;
    uint64_t m_guardAdaptiveTargetUpdates;
    double m_guardAdaptiveTargetMinObserved;
    double m_guardAdaptiveTargetMaxQueueBdps;
    uint32_t m_guardUnderutilizedSamples;
    uint64_t m_recoveryNacksGenerated;
    uint64_t m_recoveryNacksReceived;
    uint64_t m_irnNacksGenerated;
    uint64_t m_irnNacksReceived;
    uint64_t m_irnRetransmitPackets;
    uint64_t m_irnRetransmitBytes;
    uint64_t m_timeoutRecoveries;
    // Constant-space Homa mode diagnostics. These distinguish native Homa
    // control activity from the repository's generic RDMA recovery counters.
    uint64_t m_homaDataPacketsSent;
    uint64_t m_homaDataBytesSent;
    uint64_t m_homaDataPacketsByPriority[8];
    uint64_t m_homaDataBytesByPriority[8];
    uint64_t m_homaRetransmitPacketsSent;
    uint64_t m_homaGrantsSent;
    uint64_t m_homaGrantsReceived;
    uint64_t m_homaResendsSent;
    uint64_t m_homaResendsReceived;
    uint64_t m_homaCompletionNoticesSent;
    uint64_t m_homaCompletionNoticesReceived;
    uint64_t m_homaMessagesTracked;
    uint64_t m_homaMessagesCompleted;
    uint64_t m_homaMaxPendingMessages;
    uint64_t m_homaDuplicateDataAfterCompletion;
    uint64_t m_homaCompletionNoticesReplayed;
    uint32_t m_homaOvercommitDegree;
    Time m_homaResendTimeout;
    uint32_t m_homaUnscheduledLevels;
    std::vector<uint64_t> m_homaUnscheduledCutoffs;
    void ConfigureHomaPriorities(uint32_t unscheduled_levels,
                                 const std::vector<uint64_t>& cutoffs);

    enum GuardReleaseReason {
        GUARD_RELEASE_PROACTIVE,
        GUARD_RELEASE_COMPLETION,
    };

    struct GuardLifecycleState {
        int32_t flow_id;
        uint64_t size_bytes;
        uint32_t receiver_node;
        int64_t first_rx_ns;
        int64_t register_ns;
        int64_t release_ns;
        int64_t complete_ns;
        std::string release_reason;
        uint64_t remaining_bytes_at_release;
        int64_t active_before_register;
        int64_t active_after_register;
        int64_t active_before_release;
        int64_t active_after_release;
    };

    GuardLifecycleTraceSink *m_guardLifecycleTraceSink;
    GuardControllerTraceSink *m_guardControllerTraceSink;
    GuardGrantTraceSink *m_guardGrantTraceSink;
    std::unordered_map<RdmaRxQueuePair*, GuardLifecycleState> m_guardLifecycleStates;
    std::unordered_set<RdmaRxQueuePair*> m_rate_flow_ctl_set;
    EventId m_guardRebalanceEvent;
    EventId m_guardMembershipEvent;
    EventId m_guardReliabilityRefreshEvent;
    Time m_guardLastRebalanceTime;
    Time m_guardMembershipBatchStart;
    Time m_guardInitialCollectionStart;
    Time m_guardInitialCollectionDeadline;
    Time m_guardInitialCollectionTarget;
    uint64_t m_guardPendingMembershipChanges;
    uint64_t m_guardLastVectorActiveFlows;
    bool m_guardHasEmittedVectorThisEpoch;
    bool m_guardInitialCollectionPending;
    uint32_t m_guardGrantGeneration;
    uint64_t m_guardPendingGrantAcks;
    enum GuardFastpathPhase {
        GUARD_FASTPATH_IDLE = 0,
        GUARD_FASTPATH_PREPARE = 1,
        GUARD_FASTPATH_ACTIVATE = 2,
        GUARD_FASTPATH_PREFIX_BARRIER = 3,
    };
    enum GuardGrantPhaseTag {
        GUARD_GRANT_PHASE_NONE = 0,
        GUARD_GRANT_PHASE_FAST_PREPARE = 1,
        GUARD_GRANT_PHASE_FAST_ACTIVATE = 2,
        GUARD_GRANT_PHASE_TRANSITION_PREPARE = 3,
        GUARD_GRANT_PHASE_TRANSITION_ACTIVATE = 4,
        GUARD_GRANT_PHASE_RELEASE = 5,
    };
    uint32_t m_guardSmallSetFastpathLimit;
    bool m_guardTransitionPrefixBarrierEnabled;
    bool m_guardTransitionPrefixWireWatchdogEnabled;
    GuardFastpathPhase m_guardFastpathPhase;
    bool m_guardFastpathHighFanIn;
    bool m_guardFastpathHighInitialCollectionFlushed;
    bool m_guardFastpathHighInitialCommitted;
    bool m_guardFastpathCollectionReady;
    bool m_guardFastpathTransactionIsTransition;
    uint64_t m_guardFastpathTransaction;
    uint64_t m_guardFastpathMembershipRevision;
    uint64_t m_guardFastpathConsumedMembershipRevision;
    uint64_t m_guardFastpathReadyMembershipRevision;
    uint64_t m_guardFastpathEpochPrefixCloseNs;
    uint64_t m_guardFastpathEpochCollectionFlushNs;
    uint64_t m_guardFastpathEpochTransitionPrepareStartNs;
    uint64_t m_guardFastpathTransactionTargetN;
    uint32_t m_guardFastpathTransactionTargetMbps;
    std::unordered_set<RdmaRxQueuePair*> m_guardFastpathIncumbents;
    std::unordered_set<RdmaRxQueuePair*> m_guardFastpathWaiters;
    std::unordered_set<RdmaRxQueuePair*> m_guardFastpathTransactionWaiters;
    std::unordered_set<RdmaRxQueuePair*> m_guardFastpathReadyWaiters;
    EventId m_guardTransitionPrefixDeadlineEvent;
    bool m_guardTransitionPrefixBarrierWaiting;
    bool m_guardTransitionPrefixBarrierResolved;
    bool m_guardTransitionPrefixTimedOut;
    bool m_guardTransitionFallbackActive;
    bool m_guardTransitionPrefixWatchdogBudgetActive;
    Time m_guardTransitionPrefixBarrierStart;
    Time m_guardTransitionPrefixDeadline;
    std::vector<RdmaRxQueuePair*> m_guardTransitionActivationOrder;
    std::vector<Ptr<RdmaRxQueuePair> > m_guardTransitionActivationHolds;
    std::unordered_map<RdmaRxQueuePair*, uint64_t> m_guardTransitionPrefixTargets;
    std::unordered_set<RdmaRxQueuePair*> m_guardTransitionCurrentBatch;
    uint64_t m_guardTransitionActivationCursor;
    uint64_t m_guardTransitionActivationBatchIndex;
    uint64_t m_guardTransitionActivationBatchSize;
    int64_t m_guardTransitionLastRegisterNs;
    int32_t m_guardTransitionLastFlowId;
    void ConfigureGuardLifecycleTrace(GuardLifecycleTraceSink *sink);
    void ConfigureGuardControllerTrace(GuardControllerTraceSink *sink);
    void ConfigureGuardGrantTrace(GuardGrantTraceSink *sink);
    void TraceGuardControllerEvent(Ptr<RdmaQueuePair> qp, const char *event_type,
                                   DataRate hpcc_rate, const char *binding,
                                   bool rate_changed, bool fast_react, uint32_t nhop,
                                   uint32_t next_seq, double congestion_metric,
                                   double effective_target, double threshold_ratio);
    void FlushGuardLifecycleTrace();
    void SyncHwRate(Ptr<RdmaQueuePair> qp, DataRate target_cc_rate);
    void MaybeSendGuardCapReport(Ptr<RdmaQueuePair> qp);
    void HandleRccRequest(Ptr<RdmaRxQueuePair> qp, Ptr<Packet> p, CustomHeader &ch);
    bool HandleRccRemove(Ptr<RdmaRxQueuePair> qp, Ptr<Packet> p, CustomHeader &ch,
                         GuardReleaseReason reason, uint64_t remaining_bytes);
    void TraceGuardGrant(Ptr<RdmaRxQueuePair> qp, const char *event,
                         const char *set_change, uint64_t active_flows,
                         uint64_t line_rate_bps, uint64_t grant_rate_bps,
                         uint64_t next_seq, uint64_t serialized_bytes,
                         uint32_t generation, uint64_t pending_acks,
                         bool ack_required);
    void TraceGuardGrantReceive(Ptr<RdmaQueuePair> qp, Ptr<Packet> packet,
                                uint64_t grant_rate_bps, uint32_t generation,
                                const char *event, bool ack_required,
                                uint8_t phase_tag);
    void TraceGuardGrantAckReceive(Ptr<RdmaRxQueuePair> qp, CustomHeader &ch,
                                   Ptr<Packet> packet, const char *event);
    void TraceGuardGrantAckSend(Ptr<RdmaQueuePair> qp, Ptr<Packet> packet,
                                uint32_t generation, uint8_t phase_tag);
    Time GetGuardInitialCollectionQuietWindow() const;
    bool IsGuardMembershipDirty() const;
    void RedistributeGuardRates(const char *set_change, uint32_t generation = 0);
    void RequestGuardMembershipUpdate(const char *set_change);
    void FlushGuardMembershipUpdate();
    void ResetGuardReliabilityRefresh();
    void RefreshGuardGrantsForReliability();
    bool GuardSmallSetFastpathEnabled() const;
    const char *GetGuardFastpathPhaseName() const;
    const char *GetGuardGrantPhaseTagName(uint8_t phase_tag) const;
    uint8_t GetGuardFastpathWirePhaseTag(const char *set_change) const;
    void CountGuardFastpathGrantFrame(uint8_t phase_tag);
    void CountGuardFastpathAckFrame(uint8_t phase_tag);
    uint32_t NextGuardGrantGeneration();
    void RequestGuardFastpathMembershipUpdate(const char *set_change);
    void ScheduleGuardFastpathHighCollection(const char *set_change);
    void FlushGuardFastpathHighCollection();
    void StartGuardFastpathTransaction();
    void StartGuardFastpathPrepare();
    void StartGuardFastpathActivate();
    bool ValidateGuardTransitionQueueCohort();
    static bool ComputeGuardTransitionPrefixWireBudget(
        const std::vector<GuardTransitionPrefixWaiterBudgetInput> &waiters,
        const std::vector<GuardTransitionPrefixIncumbentBudgetInput> &incumbents,
        uint32_t mtu, uint32_t header_bytes_per_packet,
        uint64_t receiver_capacity_bps,
        GuardTransitionPrefixWireBudget *budget);
    static bool IsGuardTransitionPrefixWatchdogAggregateInconsistent(
        uint64_t budget_records, uint64_t contributing_hardware,
        bool non_reconstructable);
    void StartGuardTransitionPrefixBarrier();
    void CheckGuardTransitionPrefixBarrier();
    void HandleGuardTransitionPrefixDeadline();
    void SendNextGuardTransitionFallbackBatch();
    void FinishGuardFastpathGeneration();
    void FinishGuardFastpathTransaction();
    void SendGuardFastpathRequiredGeneration(
        const std::unordered_set<RdmaRxQueuePair*> &recipients,
        const char *set_change);
    void SendGuardFastpathRequiredGenerationOrdered(
        const std::vector<RdmaRxQueuePair*> &recipients,
        const char *set_change);
    void SendGuardFastpathOptionalRelease();
    void ConsumeGuardFastpathMembershipThrough(uint64_t revision);
    void ResetGuardFastpathEpoch();
    std::unordered_map<RdmaRxQueuePair*, uint64_t> ComputeGuardBaseTargets(
        uint64_t line_rate_bps) const;
    std::unordered_map<RdmaRxQueuePair*, uint64_t> ComputeGuardCapAwareTargets(
        uint64_t line_rate_bps) const;
    void ApplyGuardCapAwareRates();
    void ScheduleGuardRebalance();
    void RebalanceGuardRates();
    void SendRateControlPacket(Ptr<RdmaRxQueuePair> qp, uint32_t rate,
                               const char *set_change, uint32_t generation = 0,
                               bool ack_required = false);
    void SendGuardGrantAck(Ptr<RdmaQueuePair> qp, uint32_t generation,
                           uint8_t phase_tag);

   private:
    void TraceGuardRegistration(Ptr<RdmaRxQueuePair> qp, uint64_t flow_size,
                                uint64_t active_before, uint64_t active_after);
    void TraceGuardRelease(Ptr<RdmaRxQueuePair> qp, GuardReleaseReason reason,
                           uint64_t remaining_bytes, uint64_t active_before,
                           uint64_t active_after);
    void TraceGuardCompletion(Ptr<RdmaRxQueuePair> qp);
    void WriteGuardLifecycle(GuardLifecycleState const &state);
    void AppendGuardFastpathTraceFields(FILE *file, const char *event,
                                        const char *set_change,
                                        RdmaRxQueuePair *qp,
                                        uint8_t phase_tag,
                                        bool receiver_authoritative);

   public:

    /***********************
     * Homa Simple CC
     ***********************/
    enum HomaSimpleFlowState {
        HOMA_SIMPLE_FLOW_IDLE = 0,
        HOMA_SIMPLE_FLOW_ACTIVE = 1,
        HOMA_SIMPLE_FLOW_WAITING = 2
    };

    struct HomaSimpleFlow {
        HomaSimpleFlowState state;
        uint16_t pg;
        uint64_t bdp;
        uint64_t token_bucket;
        uint64_t requset_bytes;
        Ptr<RdmaRxQueuePair> rx_qp;

        bool operator < (const HomaSimpleFlow &other) const {
            if (pg != other.pg) {
                return pg < other.pg;
            }
            return requset_bytes > other.requset_bytes;  // SRPT
        }
    };

    class HomaSimplePriorityQueue {
    private:
        std::vector<HomaSimpleFlow*> heap;
        std::unordered_map<RdmaRxQueuePair*, int> map;

        inline RdmaRxQueuePair* _getKey(HomaSimpleFlow* flow) const {
            return PeekPointer(flow->rx_qp);
        }

        void _swap(int i, int j) {
            std::swap(heap[i], heap[j]);
            map[_getKey(heap[i])] = i;
            map[_getKey(heap[j])] = j;
        }

        void _shift_up(int i) {
            int parent = (i - 1) / 2;
            while (i > 0 && *heap[parent] < *heap[i]) {
                _swap(i, parent);
                i = parent;
                parent = (i - 1) / 2;
            }
        }

        void _shift_down(int i) {
            int left = 2 * i + 1;
            int right = 2 * i + 2;
            int target = i;
            if (left < (int)heap.size() && *heap[target] < *heap[left]) target = left;
            if (right < (int)heap.size() && *heap[target] < *heap[right]) target = right;
            if (target != i) {
                _swap(i, target);
                _shift_down(target);
            }
        }

    public:
        HomaSimplePriorityQueue() {}

        bool empty() const { return heap.empty(); }
        int size() const { return (int)heap.size(); }

        bool find(RdmaRxQueuePair* key) const { return map.count(key); }

        void insert(HomaSimpleFlow* flow) {
            RdmaRxQueuePair* key = _getKey(flow);
            heap.push_back(flow);
            map[key] = heap.size() - 1;
            _shift_up(heap.size() - 1);
        }

        HomaSimpleFlow* pop() {
            HomaSimpleFlow* flowToReturn = heap[0];
            RdmaRxQueuePair* key = _getKey(flowToReturn);
            _swap(0, heap.size() - 1);
            heap.pop_back();
            map.erase(key);
            if (!empty()) _shift_down(0);
            return flowToReturn;
        }

        const HomaSimpleFlow* top() const { return heap[0]; }
    };

    class HomaSimpleScheduler {
    public:
        HomaSimpleScheduler(RdmaHw* hw);
        ~HomaSimpleScheduler();
        void SetPacingInterval(Ptr<Packet> p);
        void AddHomaSimpleFlow(HomaSimpleFlow &flow, Ptr<Packet> p, CustomHeader &ch);
        void ScheduleHomaSimple();
        void SendHomaSimpleCreditPackage(HomaSimpleFlow &flow);
        void UpdateFlowState(HomaSimpleFlow* flow);

        RdmaHw* rdma_hw;
        bool is_scheduled;
        uint64_t pacing_interval;
        HomaSimplePriorityQueue active_flow;
        std::unordered_set<HomaSimpleFlow*> wait_flow;
        std::unordered_map<RdmaRxQueuePair*, std::unique_ptr<HomaSimpleFlow>> flow_hash;
    };

    HomaSimpleScheduler homa_simple_scheduler;
    void ReceiveHomaSimpleRequest(Ptr<RdmaRxQueuePair> qp, Ptr<Packet> p, CustomHeader &ch);
    void ReceiveHomaSimpleData(Ptr<RdmaRxQueuePair> qp, Ptr<Packet> p, CustomHeader &ch);

    /***********************
     * Homa CC (cc_mode 12)
     *
     * Receiver-driven scheduling with overcommit. PR2 introduces:
     * - HomaFlow / HomaPriorityQueue: SRPT min-heap keyed on
     *   bytes_remaining_to_grant (smallest at top).
     * - HomaScheduler: timer-driven; every pacing_interval pops up
     *   to overcommit_degree flows, advances each by 1 MTU of grants,
     *   sends a GRANT (0xFA control packet), re-inserts if not fully
     *   granted.
     * Sender (GetNxtPacketHoma) is gated by max(unscheduled_bytes,
     * granted_offset). 0xFA control packets are dispatched through
     * ReceiveHomaControl.
     *
     * PR3+ will introduce per-packet priority routing (8 queue slots);
     * PR4 makes cc_mode=12 PFC-free; PR5 adds RESEND / NEED_ACK / ACK.
     ***********************/
    Ptr<Packet> GetNxtPacketHoma(Ptr<RdmaQueuePair> qp);  // homa sender path
    void ReceiveHomaData(Ptr<RdmaRxQueuePair> qp, Ptr<Packet> p, CustomHeader &ch);
    int ReceiveHomaControl(Ptr<Packet> p, CustomHeader &ch);

    struct HomaFlow {
        uint64_t message_id;
        uint64_t msg_total_length;
        uint64_t bytes_received;
        uint64_t granted_offset_sent;    // cumulative bytes we've granted to sender
        uint64_t next_expected_offset;   // first byte not yet received contiguously
        Time     last_progress_time;     // last time next_expected_offset advanced
        // Disjoint [start, end) DATA ranges. Homa permits packet reordering, so
        // a single contiguous pointer is insufficient for native RESEND.
        std::map<uint64_t, uint64_t> received_ranges;
        uint64_t bdp;
        uint16_t pg;                     // sender QP's m_pg (used for control routing)
        Ptr<RdmaRxQueuePair> rx_qp;

        uint64_t bytes_remaining_to_grant() const {
            return msg_total_length > granted_offset_sent ? msg_total_length - granted_offset_sent : 0;
        }
        bool fully_granted() const { return granted_offset_sent >= msg_total_length; }
        bool fully_received() const { return next_expected_offset >= msg_total_length; }

        // SRPT min-heap: smaller bytes_remaining_to_grant = "greater" so it bubbles up
        bool operator < (const HomaFlow &other) const {
            return bytes_remaining_to_grant() > other.bytes_remaining_to_grant();
        }
    };

    class HomaPriorityQueue {
    private:
        std::vector<HomaFlow*> heap;
        std::unordered_map<RdmaRxQueuePair*, int> map;

        inline RdmaRxQueuePair* _getKey(HomaFlow* flow) const {
            return PeekPointer(flow->rx_qp);
        }

        void _swap(int i, int j) {
            std::swap(heap[i], heap[j]);
            map[_getKey(heap[i])] = i;
            map[_getKey(heap[j])] = j;
        }

        void _shift_up(int i) {
            int parent = (i - 1) / 2;
            while (i > 0 && *heap[parent] < *heap[i]) {
                _swap(i, parent);
                i = parent;
                parent = (i - 1) / 2;
            }
        }

        void _shift_down(int i) {
            int left = 2 * i + 1;
            int right = 2 * i + 2;
            int target = i;
            if (left < (int)heap.size() && *heap[target] < *heap[left]) target = left;
            if (right < (int)heap.size() && *heap[target] < *heap[right]) target = right;
            if (target != i) {
                _swap(i, target);
                _shift_down(target);
            }
        }

    public:
        HomaPriorityQueue() {}
        bool empty() const { return heap.empty(); }
        int size() const { return (int)heap.size(); }
        bool find(RdmaRxQueuePair* key) const { return map.count(key); }

        void erase(RdmaRxQueuePair* key) {
            auto it = map.find(key);
            if (it == map.end()) return;
            int index = it->second;
            _swap(index, heap.size() - 1);
            heap.pop_back();
            map.erase(key);
            if (index < (int)heap.size()) {
                RdmaRxQueuePair* moved_key = _getKey(heap[index]);
                _shift_up(index);
                auto moved = map.find(moved_key);
                if (moved != map.end()) _shift_down(moved->second);
            }
        }

        void insert(HomaFlow* flow) {
            RdmaRxQueuePair* key = _getKey(flow);
            heap.push_back(flow);
            map[key] = heap.size() - 1;
            _shift_up(heap.size() - 1);
        }

        HomaFlow* pop() {
            HomaFlow* flowToReturn = heap[0];
            RdmaRxQueuePair* key = _getKey(flowToReturn);
            _swap(0, heap.size() - 1);
            heap.pop_back();
            map.erase(key);
            if (!empty()) _shift_down(0);
            return flowToReturn;
        }

        const HomaFlow* top() const { return heap[0]; }
    };

    class HomaScheduler {
    public:
        HomaScheduler(RdmaHw* hw);
        ~HomaScheduler();
        void SetPacingInterval();
        void OnDataArrival(Ptr<RdmaRxQueuePair> rx_qp, Ptr<Packet> p, CustomHeader &ch);
        void Schedule();
        void SendGrant(HomaFlow* flow, uint8_t grant_priority);
        void StallCheck();
        void SendResend(HomaFlow* flow, uint64_t offset, uint64_t length);
        void SendCompletionNotice(HomaFlow* flow);

        RdmaHw* rdma_hw;
        bool is_scheduled;
        bool is_stall_scheduled;
        uint64_t pacing_interval;
        HomaPriorityQueue active;
        std::unordered_map<RdmaRxQueuePair*, std::unique_ptr<HomaFlow>> flow_hash;
        // A completed message may still have DATA retransmissions in flight.
        // Retain only its ID so those packets cannot create a ghost scheduler
        // entry; formal runs cap this set by their bounded input flow count.
        std::unordered_set<uint64_t> completed_message_ids;
    };

    HomaScheduler homa_scheduler;

    /***********************
     * High Precision CC
     ***********************/
    double m_targetUtil;
    double m_utilHigh;
    uint32_t m_miThresh;
    bool m_multipleRate;
    bool m_sampleFeedback;  // only react to feedback every RTT, or qlen > 0
    void HandleAckHp(Ptr<RdmaQueuePair> qp, Ptr<Packet> p, CustomHeader &ch);
    void UpdateRateHp(Ptr<RdmaQueuePair> qp, Ptr<Packet> p, CustomHeader &ch, bool fast_react);
    void UpdateRateHpTest(Ptr<RdmaQueuePair> qp, Ptr<Packet> p, CustomHeader &ch, bool fast_react);
    void FastReactHp(Ptr<RdmaQueuePair> qp, Ptr<Packet> p, CustomHeader &ch);

    /**********************
     * TIMELY
     *********************/
    double m_tmly_alpha, m_tmly_beta;
    uint64_t m_tmly_TLow, m_tmly_THigh, m_tmly_minRtt;
    void HandleAckTimely(Ptr<RdmaQueuePair> qp, Ptr<Packet> p, CustomHeader &ch);
    void UpdateRateTimely(Ptr<RdmaQueuePair> qp, Ptr<Packet> p, CustomHeader &ch, bool us);
    void FastReactTimely(Ptr<RdmaQueuePair> qp, Ptr<Packet> p, CustomHeader &ch);

    /**********************
     * DCTCP
     *********************/
    DataRate m_dctcp_rai;
    void HandleAckDctcp(Ptr<RdmaQueuePair> qp, Ptr<Packet> p, CustomHeader &ch);

    /**********************
     * IRN
     *********************/
    bool m_irn;
    Time m_irn_rtoLow;
    Time m_irn_rtoHigh;
    uint32_t m_irn_bdp;
};

} /* namespace ns3 */

#endif /* RDMA_HW_H */
