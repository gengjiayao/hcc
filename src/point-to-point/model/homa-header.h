#ifndef HOMA_HEADER_H
#define HOMA_HEADER_H

#include "ns3/header.h"

namespace ns3 {

class HomaHeader : public Header {
public:
    HomaHeader ();

    void SetBdp (uint64_t bdp);
    uint64_t GetBdp () const;

    void SetHomaRequest (uint64_t req);
    uint64_t GetHomaRequest () const;

    void SetHomaUnscheduled (uint64_t us);
    uint64_t GetHomaUnscheduled () const;

    static TypeId GetTypeId (void);
    virtual TypeId GetInstanceTypeId (void) const;
    virtual void Print (std::ostream &os) const;
    virtual uint32_t GetSerializedSize (void) const;
    static uint32_t GetHeaderSize(void);
private:
    virtual void Serialize (Buffer::Iterator start) const;
    virtual uint32_t Deserialize (Buffer::Iterator start);

    uint64_t m_bdp;
    uint64_t m_homa_requset;
    uint64_t m_homa_unscheduled;
};

} // namespace ns3

#endif