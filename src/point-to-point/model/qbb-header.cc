#include <stdint.h>
#include <iostream>
#include "qbb-header.h"
#include "ns3/buffer.h"
#include "ns3/address-utils.h"
#include "ns3/log.h"

NS_LOG_COMPONENT_DEFINE("qbbHeader");

namespace ns3 {

	NS_OBJECT_ENSURE_REGISTERED(qbbHeader);
	NS_OBJECT_ENSURE_REGISTERED(GuardGrantHeader);
	NS_OBJECT_ENSURE_REGISTERED(GuardGrantAckHeader);

	qbbHeader::qbbHeader(uint16_t pg)
		: m_pg(pg), sport(0), dport(0), flags(0), m_seq(0)
	{
	}

	qbbHeader::qbbHeader()
		: m_pg(0), sport(0), dport(0), flags(0), m_seq(0)
	{}

	qbbHeader::~qbbHeader()
	{}

	void qbbHeader::SetPG(uint16_t pg)
	{
		m_pg = pg;
	}

	void qbbHeader::SetSeq(uint32_t seq)
	{
		m_seq = seq;
	}

	void qbbHeader::SetSport(uint32_t _sport){
		sport = _sport;
	}
	void qbbHeader::SetDport(uint32_t _dport){
		dport = _dport;
	}

	void qbbHeader::SetTs(uint64_t ts){
		NS_ASSERT_MSG(IntHeader::mode == 1, "qbbHeader cannot SetTs when IntHeader::mode != 1");
		ih.ts = ts;
	}
	void qbbHeader::SetCnp(){
		flags |= 1 << FLAG_CNP;
	}
	void qbbHeader::SetGuardFabricBound(bool fabric_bound){
		if (fabric_bound) {
			flags |= 1 << FLAG_GUARD_FABRIC_BOUND;
		} else {
			flags &= ~(1 << FLAG_GUARD_FABRIC_BOUND);
		}
	}
	void qbbHeader::SetGuardCapReport(){
		flags |= 1 << FLAG_GUARD_CAP_REPORT;
	}
	void qbbHeader::SetIntHeader(const IntHeader &_ih){
		ih = _ih;
	}
	void qbbHeader::SetIrnNack(uint32_t seq){
		m_irn_nack = seq;
	}
	void qbbHeader::SetIrnNackSize(size_t sz){
		m_irn_nack_size = (uint16_t)sz;
	}

	uint16_t qbbHeader::GetPG() const
	{
		return m_pg;
	}

	uint32_t qbbHeader::GetSeq() const
	{
		return m_seq;
	}

	uint16_t qbbHeader::GetSport() const{
		return sport;
	}
	uint16_t qbbHeader::GetDport() const{
		return dport;
	}

	uint64_t qbbHeader::GetTs() const {
		NS_ASSERT_MSG(IntHeader::mode == 1, "qbbHeader cannot GetTs when IntHeader::mode != 1");
		return ih.ts;
	}
	uint8_t qbbHeader::GetCnp() const{
		return (flags >> FLAG_CNP) & 1;
	}
	bool qbbHeader::GetGuardFabricBound() const{
		return ((flags >> FLAG_GUARD_FABRIC_BOUND) & 1) != 0;
	}
	bool qbbHeader::GetGuardCapReport() const{
		return ((flags >> FLAG_GUARD_CAP_REPORT) & 1) != 0;
	}
	uint32_t qbbHeader::GetIrnNack() const{
		return m_irn_nack;
	}
	size_t qbbHeader::GetIrnNackSize() const{
		return (size_t) m_irn_nack_size;
	}

	TypeId
		qbbHeader::GetTypeId(void)
	{
		static TypeId tid = TypeId("ns3::qbbHeader")
			.SetParent<Header>()
			.AddConstructor<qbbHeader>()
			;
		return tid;
	}
	TypeId
		qbbHeader::GetInstanceTypeId(void) const
	{
		return GetTypeId();
	}
	void qbbHeader::Print(std::ostream &os) const
	{
		os << "qbb:" << "pg=" << m_pg << ",seq=" << m_seq;
	}
	uint32_t qbbHeader::GetSerializedSize(void)  const
	{
		return GetBaseSize() + IntHeader::GetStaticSize();
	}
	uint32_t qbbHeader::GetBaseSize() {
		qbbHeader tmp;
		return sizeof(tmp.sport) + sizeof(tmp.dport) + sizeof(tmp.flags) + sizeof(tmp.m_pg) + sizeof(tmp.m_seq) + sizeof(tmp.m_irn_nack) + sizeof(tmp.m_irn_nack_size);
	}
	void qbbHeader::Serialize(Buffer::Iterator start)  const
	{
		Buffer::Iterator i = start;
		i.WriteU16(sport);
		i.WriteU16(dport);
		i.WriteU16(flags);
		i.WriteU16(m_pg);
		i.WriteU32(m_seq);
		i.WriteU32(m_irn_nack);
		i.WriteU16(m_irn_nack_size);

		// write IntHeader
		ih.Serialize(i);
	}

	uint32_t qbbHeader::Deserialize(Buffer::Iterator start)
	{
		Buffer::Iterator i = start;
		sport = i.ReadU16();
		dport = i.ReadU16();
		flags = i.ReadU16();
		m_pg = i.ReadU16();
		m_seq = i.ReadU32();
		m_irn_nack = i.ReadU32();
		m_irn_nack_size = i.ReadU16();

		// read IntHeader
		ih.Deserialize(i);
		return GetSerializedSize();
	}

	GuardGrantHeader::GuardGrantHeader()
		: m_sport(0), m_dport(0), m_pg(0), m_rateMbps(0), m_generation(0),
		  m_ackRequired(0)
	{}

	GuardGrantHeader::~GuardGrantHeader()
	{}

	void GuardGrantHeader::SetSport(uint16_t sport) { m_sport = sport; }
	void GuardGrantHeader::SetDport(uint16_t dport) { m_dport = dport; }
	void GuardGrantHeader::SetPG(uint16_t pg) { m_pg = pg; }
	void GuardGrantHeader::SetRateMbps(uint32_t rateMbps) { m_rateMbps = rateMbps; }
	void GuardGrantHeader::SetGeneration(uint32_t generation) { m_generation = generation; }
	void GuardGrantHeader::SetAckRequired(bool ackRequired)
	{
		m_ackRequired = (m_ackRequired & 0xfe) | (ackRequired ? 1 : 0);
	}
	void GuardGrantHeader::SetPhaseTag(uint8_t phaseTag)
	{
		NS_ASSERT_MSG(phaseTag <= 7, "GUARD grant phase tag exceeds three bits");
		m_ackRequired = (m_ackRequired & 0x01) | (phaseTag << 1);
	}

	uint16_t GuardGrantHeader::GetSport() const { return m_sport; }
	uint16_t GuardGrantHeader::GetDport() const { return m_dport; }
	uint16_t GuardGrantHeader::GetPG() const { return m_pg; }
	uint32_t GuardGrantHeader::GetRateMbps() const { return m_rateMbps; }
	uint32_t GuardGrantHeader::GetGeneration() const { return m_generation; }
	bool GuardGrantHeader::GetAckRequired() const { return (m_ackRequired & 0x01) != 0; }
	uint8_t GuardGrantHeader::GetPhaseTag() const { return (m_ackRequired >> 1) & 0x07; }

	TypeId GuardGrantHeader::GetTypeId(void)
	{
		static TypeId tid = TypeId("ns3::GuardGrantHeader")
			.SetParent<Header>()
			.AddConstructor<GuardGrantHeader>();
		return tid;
	}

	TypeId GuardGrantHeader::GetInstanceTypeId(void) const
	{
		return GetTypeId();
	}

	void GuardGrantHeader::Print(std::ostream &os) const
	{
		os << "guard-grant:pg=" << m_pg << ",rate=" << m_rateMbps
		   << "Mbps,generation=" << m_generation
		   << ",ack_required=" << (GetAckRequired() ? 1 : 0)
		   << ",phase_tag=" << static_cast<uint32_t>(GetPhaseTag());
	}

	uint32_t GuardGrantHeader::GetSerializedSize(void) const
	{
		return sizeof(m_sport) + sizeof(m_dport) + sizeof(m_pg) + sizeof(m_rateMbps)
		       + sizeof(m_generation) + sizeof(m_ackRequired);
	}

	void GuardGrantHeader::Serialize(Buffer::Iterator start) const
	{
		Buffer::Iterator i = start;
		i.WriteU16(m_sport);
		i.WriteU16(m_dport);
		i.WriteU16(m_pg);
		i.WriteU32(m_rateMbps);
		i.WriteU32(m_generation);
		i.WriteU8(m_ackRequired);
	}

	uint32_t GuardGrantHeader::Deserialize(Buffer::Iterator start)
	{
		Buffer::Iterator i = start;
		m_sport = i.ReadU16();
		m_dport = i.ReadU16();
		m_pg = i.ReadU16();
		m_rateMbps = i.ReadU32();
		m_generation = i.ReadU32();
		m_ackRequired = i.ReadU8();
		return GetSerializedSize();
	}

	GuardGrantAckHeader::GuardGrantAckHeader()
		: m_sport(0), m_dport(0), m_pg(0), m_generation(0), m_phaseTag(0)
	{}

	GuardGrantAckHeader::~GuardGrantAckHeader()
	{}

	void GuardGrantAckHeader::SetSport(uint16_t sport) { m_sport = sport; }
	void GuardGrantAckHeader::SetDport(uint16_t dport) { m_dport = dport; }
	void GuardGrantAckHeader::SetPG(uint16_t pg) { m_pg = pg; }
	void GuardGrantAckHeader::SetGeneration(uint32_t generation) { m_generation = generation; }
	void GuardGrantAckHeader::SetPhaseTag(uint8_t phaseTag)
	{
		NS_ASSERT_MSG(phaseTag <= 7, "GUARD grant ACK phase tag exceeds three bits");
		m_phaseTag = phaseTag;
	}

	uint16_t GuardGrantAckHeader::GetSport() const { return m_sport; }
	uint16_t GuardGrantAckHeader::GetDport() const { return m_dport; }
	uint16_t GuardGrantAckHeader::GetPG() const { return m_pg; }
	uint32_t GuardGrantAckHeader::GetGeneration() const { return m_generation; }
	uint8_t GuardGrantAckHeader::GetPhaseTag() const { return m_phaseTag; }

	TypeId GuardGrantAckHeader::GetTypeId(void)
	{
		static TypeId tid = TypeId("ns3::GuardGrantAckHeader")
			.SetParent<Header>()
			.AddConstructor<GuardGrantAckHeader>();
		return tid;
	}

	TypeId GuardGrantAckHeader::GetInstanceTypeId(void) const
	{
		return GetTypeId();
	}

	void GuardGrantAckHeader::Print(std::ostream &os) const
	{
		os << "guard-grant-ack:pg=" << m_pg << ",generation=" << m_generation
		   << ",phase_tag=" << static_cast<uint32_t>(m_phaseTag);
	}

	uint32_t GuardGrantAckHeader::GetSerializedSize(void) const
	{
		return sizeof(m_sport) + sizeof(m_dport) + sizeof(m_pg) + sizeof(m_generation)
		       + sizeof(m_phaseTag);
	}

	void GuardGrantAckHeader::Serialize(Buffer::Iterator start) const
	{
		Buffer::Iterator i = start;
		i.WriteU16(m_sport);
		i.WriteU16(m_dport);
		i.WriteU16(m_pg);
		i.WriteU32(m_generation);
		i.WriteU8(m_phaseTag);
	}

	uint32_t GuardGrantAckHeader::Deserialize(Buffer::Iterator start)
	{
		Buffer::Iterator i = start;
		m_sport = i.ReadU16();
		m_dport = i.ReadU16();
		m_pg = i.ReadU16();
		m_generation = i.ReadU32();
		m_phaseTag = i.ReadU8();
		return GetSerializedSize();
	}
}; // namespace ns3
