//yibo

#ifndef QBB_HEADER_H
#define QBB_HEADER_H

#include <stdint.h>
#include "ns3/header.h"
#include "ns3/buffer.h"
#include "ns3/int-header.h"

namespace ns3 {

/**
 * \ingroup Pause
 * \brief Header for the Congestion Notification Message
 *
 * This class has two fields: The five-tuple flow id and the quantized
 * congestion level. This can be serialized to or deserialzed from a byte
 * buffer.
 */
 
class qbbHeader : public Header
{
public:
 
  enum {
	  FLAG_CNP = 0,
	  FLAG_GUARD_FABRIC_BOUND = 1,
	  FLAG_GUARD_CAP_REPORT = 2
  };
  qbbHeader (uint16_t pg);
  qbbHeader ();
  virtual ~qbbHeader ();

//Setters
  /**
   * \param pg The PG
   */
  void SetPG (uint16_t pg);
  void SetSeq(uint32_t seq);
  void SetSport(uint32_t _sport);
  void SetDport(uint32_t _dport);
  void SetTs(uint64_t ts);
  void SetCnp();
  void SetGuardFabricBound(bool fabric_bound);
  void SetGuardCapReport();
  void SetIntHeader(const IntHeader &_ih);
  void SetIrnNack(uint32_t seq);
  void SetIrnNackSize(size_t sz);

//Getters
  /**
   * \return The pg
   */
  uint16_t GetPG () const;
  uint32_t GetSeq() const;
  uint16_t GetPort() const;
  uint16_t GetSport() const;
  uint16_t GetDport() const;
  uint64_t GetTs() const;
  uint8_t GetCnp() const;
  bool GetGuardFabricBound() const;
  bool GetGuardCapReport() const;
  uint32_t GetIrnNack() const;
  size_t GetIrnNackSize() const;

  static TypeId GetTypeId (void);
  virtual TypeId GetInstanceTypeId (void) const;
  virtual void Print (std::ostream &os) const;
  virtual uint32_t GetSerializedSize (void) const;
  virtual void Serialize (Buffer::Iterator start) const;
  virtual uint32_t Deserialize (Buffer::Iterator start);
  static uint32_t GetBaseSize(); // size without INT

private:
  uint16_t sport, dport;
  uint16_t flags;
  uint16_t m_pg;
  uint32_t m_seq; // the qbb sequence number.
  IntHeader ih;
  uint32_t m_irn_nack;
  uint16_t m_irn_nack_size;
  bool enable_irn;
};

/**
 * Compact receiver rate grant for GUARD.
 *
 * A rate grant needs only the flow ports, priority group, and rate.  Keeping
 * it separate from qbbHeader avoids serializing qbbHeader's unused INT area.
 */
class GuardGrantHeader : public Header
{
public:
  GuardGrantHeader ();
  virtual ~GuardGrantHeader ();

  void SetSport (uint16_t sport);
  void SetDport (uint16_t dport);
  void SetPG (uint16_t pg);
  void SetRateMbps (uint32_t rateMbps);

  uint16_t GetSport () const;
  uint16_t GetDport () const;
  uint16_t GetPG () const;
  uint32_t GetRateMbps () const;

  static TypeId GetTypeId (void);
  virtual TypeId GetInstanceTypeId (void) const;
  virtual void Print (std::ostream &os) const;
  virtual uint32_t GetSerializedSize (void) const;
  virtual void Serialize (Buffer::Iterator start) const;
  virtual uint32_t Deserialize (Buffer::Iterator start);

private:
  uint16_t m_sport;
  uint16_t m_dport;
  uint16_t m_pg;
  uint32_t m_rateMbps;
};

}; // namespace ns3

#endif /* QBB_HEADER */
