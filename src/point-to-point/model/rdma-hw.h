#ifndef RDMA_HW_H
#define RDMA_HW_H

#include <ns3/custom-header.h>
#include <ns3/node.h>
#include <ns3/rdma.h>
#include <ns3/selective-packet-queue.h>

#include <unordered_map>
#include <unordered_set>
#include <memory>
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
    bool m_guardKeepLastHopInt;
    std::unordered_set<RdmaRxQueuePair*> m_rate_flow_ctl_set;
    void SyncHwRate(Ptr<RdmaQueuePair> qp, DataRate target_cc_rate);
    void HandleRccRequest(Ptr<RdmaRxQueuePair> qp, Ptr<Packet> p, CustomHeader &ch);
    void HandleRccRemove(Ptr<RdmaRxQueuePair> qp, Ptr<Packet> p, CustomHeader &ch);
    void SendRateControlPacket(Ptr<RdmaRxQueuePair> qp, CustomHeader &ch, uint32_t rate);

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
        uint64_t msg_total_length;
        uint64_t bytes_received;
        uint64_t granted_offset_sent;    // cumulative bytes we've granted to sender
        uint64_t next_expected_offset;   // first byte not yet received contiguously
        Time     last_progress_time;     // last time next_expected_offset advanced
        uint64_t bdp;
        uint16_t pg;                     // sender QP's m_pg (used for control routing)
        Ptr<RdmaRxQueuePair> rx_qp;

        uint64_t bytes_remaining_to_grant() const {
            return msg_total_length > granted_offset_sent ? msg_total_length - granted_offset_sent : 0;
        }
        bool fully_granted() const { return granted_offset_sent >= msg_total_length; }

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

        RdmaHw* rdma_hw;
        bool is_scheduled;
        bool is_stall_scheduled;
        uint64_t pacing_interval;
        uint32_t overcommit_degree;
        Time stall_rto;
        HomaPriorityQueue active;
        std::unordered_map<RdmaRxQueuePair*, std::unique_ptr<HomaFlow>> flow_hash;
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
