/* -*- Mode:C++; c-file-style:"gnu"; indent-tabs-mode:nil; -*- */
/*
 * Author: Youngmok Jung <tom418@kaist.ac.kr>
 */

#include "flow-stat-tag.h"

namespace ns3 {
FlowStatTag::FlowStatTag()
    : flow_stat(FLOW_NOTEND), initiatedTime(0.0), m_baseRttSeconds(0.0),
      m_hasBaseRtt(false), m_firstGrantGateBytes(0),
      m_hasFirstGrantGateBytes(false) {}

TypeId FlowStatTag::GetTypeId(void) {
    static TypeId tid = TypeId("ns3::FlowStatTag").SetParent<Tag>().AddConstructor<FlowStatTag>();
    return tid;
}
TypeId FlowStatTag::GetInstanceTypeId(void) const { return GetTypeId(); }

uint32_t FlowStatTag::GetSerializedSize(void) const {
    return sizeof(flow_stat) + sizeof(initiatedTime) + sizeof(uint8_t) +
           sizeof(m_baseRttSeconds) + sizeof(uint8_t) +
           sizeof(m_firstGrantGateBytes);
}

void FlowStatTag::Serialize(TagBuffer i) const {
    i.WriteU8(flow_stat);
    i.WriteDouble(initiatedTime);
    i.WriteU8(m_hasBaseRtt ? 1 : 0);
    i.WriteDouble(m_baseRttSeconds);
    i.WriteU8(m_hasFirstGrantGateBytes ? 1 : 0);
    i.WriteU64(m_firstGrantGateBytes);
}

void FlowStatTag::Deserialize(TagBuffer i) {
    uint8_t t = i.ReadU8();
    NS_ASSERT(t == FLOW_END || t == FLOW_NOTEND || t == FLOW_START || t == FLOW_START_AND_END);
    flow_stat = t;
    initiatedTime = i.ReadDouble();
    uint8_t hasBaseRtt = i.ReadU8();
    m_hasBaseRtt = (hasBaseRtt != 0);
    m_baseRttSeconds = i.ReadDouble();
    uint8_t hasFirstGrantGateBytes = i.ReadU8();
    m_hasFirstGrantGateBytes = (hasFirstGrantGateBytes != 0);
    m_firstGrantGateBytes = i.ReadU64();
}

void FlowStatTag::SetType(uint8_t ttl) {
    NS_ASSERT(ttl == FLOW_END || ttl == FLOW_NOTEND || ttl == FLOW_START ||
              ttl == FLOW_START_AND_END || ttl == FLOW_FIN);
    flow_stat = ttl;
}

uint8_t FlowStatTag::GetType() { return flow_stat; }

void FlowStatTag::Print(std::ostream& os) const {
    if (flow_stat != FLOW_NOTEND) {
        os << "Flow ended: " << ((flow_stat == FLOW_END) ? "Yes " : "No ");
    } else {
        os << "Flow Continuing: Yes";
    }
}

void FlowStatTag::setInitiatedTime(double t) { this->initiatedTime = t; }

double FlowStatTag::getInitiatedTime() { return this->initiatedTime; }

void FlowStatTag::SetBaseRttSeconds(double t) {
    m_hasBaseRtt = true;
    m_baseRttSeconds = t;
}

double FlowStatTag::GetBaseRttSeconds() const { return m_baseRttSeconds; }

bool FlowStatTag::HasBaseRtt() const { return m_hasBaseRtt; }

void FlowStatTag::SetFirstGrantGateBytes(uint64_t bytes) {
    m_hasFirstGrantGateBytes = true;
    m_firstGrantGateBytes = bytes;
}

uint64_t FlowStatTag::GetFirstGrantGateBytes() const {
    return m_firstGrantGateBytes;
}

bool FlowStatTag::HasFirstGrantGateBytes() const {
    return m_hasFirstGrantGateBytes;
}
}  // namespace ns3
