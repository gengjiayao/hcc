#include "ns3/log.h"
#include "ns3/header.h"
#include "homa-header.h"

NS_LOG_COMPONENT_DEFINE ("HomaHeader");

namespace ns3 {

NS_OBJECT_ENSURE_REGISTERED (HomaHeader);

HomaHeader::HomaHeader(): m_bdp(0), m_homa_requset(0), m_homa_unscheduled(0) {}

void HomaHeader::SetBdp(uint64_t bdp) {
    m_bdp = bdp;
}

uint64_t HomaHeader::GetBdp(void) const {
    return m_bdp;
}

void HomaHeader::SetHomaRequest(uint64_t req) {
    m_homa_requset = req;
}

uint64_t HomaHeader::GetHomaRequest(void) const {
    return m_homa_requset;
}

void HomaHeader::SetHomaUnscheduled(uint64_t us) {
    m_homa_unscheduled = us;
}

uint64_t HomaHeader::GetHomaUnscheduled(void) const {
    return m_homa_unscheduled;
}

TypeId HomaHeader::GetTypeId(void) {
  static TypeId tid = TypeId("ns3::HomaHeader").SetParent<Header>().AddConstructor<HomaHeader>();
  return tid;
}

TypeId HomaHeader::GetInstanceTypeId(void) const {
    return GetTypeId();
}

void HomaHeader::Print(std::ostream &os) const {
    os << "HomaHeader: bdp=" << m_bdp
       << " homa_request=" << m_homa_requset
       << " homa_unscheduled=" << m_homa_unscheduled;
}

uint32_t HomaHeader::GetSerializedSize(void) const {
    return GetHeaderSize();
}

uint32_t HomaHeader::GetHeaderSize(void) {
    return sizeof(m_bdp) + sizeof(m_homa_requset) + sizeof(m_homa_unscheduled);
}

void HomaHeader::Serialize(Buffer::Iterator start) const {
    Buffer::Iterator i = start;
    i.WriteHtonU64(m_bdp);
    i.WriteHtonU64(m_homa_requset);
    i.WriteHtonU64(m_homa_unscheduled);
}

uint32_t HomaHeader::Deserialize(Buffer::Iterator start) {
    Buffer::Iterator i = start;
    m_bdp = i.ReadNtohU64();
    m_homa_requset = i.ReadNtohU64();
    m_homa_unscheduled = i.ReadNtohU64();
    return GetSerializedSize();
}

} // namespace ns3