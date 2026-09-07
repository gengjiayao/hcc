#include "ns3/test.h"
#include "ns3/custom-header.h"
#include "ns3/drop-tail-queue.h"
#include "ns3/flow-stat-tag.h"
#include "ns3/ipv4-header.h"
#include "ns3/packet.h"
#include "ns3/simulator.h"
#include "ns3/point-to-point-net-device.h"
#include "ns3/point-to-point-channel.h"
#include "ns3/ppp-header.h"
#include "ns3/qbb-header.h"
#include "ns3/rdma-hw.h"

namespace ns3 {

class PointToPointTest : public TestCase
{
public:
  PointToPointTest ();

  virtual void DoRun (void);

private:
  void SendOnePacket (Ptr<PointToPointNetDevice> device);
};

PointToPointTest::PointToPointTest ()
  : TestCase ("PointToPoint")
{
}

void
PointToPointTest::SendOnePacket (Ptr<PointToPointNetDevice> device)
{
  Ptr<Packet> p = Create<Packet> ();
  device->Send (p, device->GetBroadcast (), 0x800);
}


void
PointToPointTest::DoRun (void)
{
  Ptr<Node> a = CreateObject<Node> ();
  Ptr<Node> b = CreateObject<Node> ();
  Ptr<PointToPointNetDevice> devA = CreateObject<PointToPointNetDevice> ();
  Ptr<PointToPointNetDevice> devB = CreateObject<PointToPointNetDevice> ();
  Ptr<PointToPointChannel> channel = CreateObject<PointToPointChannel> ();

  devA->Attach (channel);
  devA->SetAddress (Mac48Address::Allocate ());
  devA->SetQueue (CreateObject<DropTailQueue> ());
  devB->Attach (channel);
  devB->SetAddress (Mac48Address::Allocate ());
  devB->SetQueue (CreateObject<DropTailQueue> ());

  a->AddDevice (devA);
  b->AddDevice (devB);

  Simulator::Schedule (Seconds (1.0), &PointToPointTest::SendOnePacket, this, devA);

  Simulator::Run ();

  Simulator::Destroy ();
}

class GuardGrantHeaderTest : public TestCase
{
public:
  GuardGrantHeaderTest ();
  virtual void DoRun (void);
};

class GuardFirstGrantGateTagTest : public TestCase
{
public:
  GuardFirstGrantGateTagTest ();
  virtual void DoRun (void);
};

class GuardDeadlineSameTickTest : public TestCase
{
public:
  GuardDeadlineSameTickTest ();
  virtual void DoRun (void);

private:
  void Deadline (void);
  void Progress (void);
  void Decide (void);

  bool m_ready;
  bool m_timedOut;
};

class GuardTransitionPrefixWireBudgetTest : public TestCase
{
public:
  GuardTransitionPrefixWireBudgetTest ();
  virtual void DoRun (void);
};

GuardTransitionPrefixWireBudgetTest::GuardTransitionPrefixWireBudgetTest ()
  : TestCase ("GUARD V13 transition-prefix wire watchdog budget")
{
}

void
GuardTransitionPrefixWireBudgetTest::DoRun (void)
{
  uint32_t savedIntMode = IntHeader::mode;
  IntHeader::mode = 0; // CC11/full GUARD carries the complete INT header.
  uint32_t dataHeaderBytes = CustomHeader::GetStaticWholeHeaderSize ();
  NS_TEST_ASSERT_MSG_EQ (dataHeaderBytes, 90,
                         "current CC11 DATA header must be exactly 90 bytes");
  std::vector<GuardTransitionPrefixWaiterBudgetInput> n8Waiters;
  for (uint32_t i = 0; i < 7; ++i)
    {
      n8Waiters.push_back ({900000, 104000, 8320});
    }
  std::vector<GuardTransitionPrefixIncumbentBudgetInput> n8Incumbents = {
    {12500000000ULL, 8320}
  };
  GuardTransitionPrefixWireBudget budget = {};
  bool valid = RdmaHw::ComputeGuardTransitionPrefixWireBudget (
    n8Waiters, n8Incumbents, 1000, dataHeaderBytes,
    100000000000ULL, &budget);
  NS_TEST_ASSERT_MSG_EQ (valid, true, "frozen N=8 budget must be valid");
  NS_TEST_ASSERT_MSG_EQ (budget.roundedPayloadBudgetBytes, 728000,
                         "N=8 rounded payload budget must cover seven exact gates");
  NS_TEST_ASSERT_MSG_EQ (budget.packetCount, 728,
                         "N=8 packet count must use exact MTU ceilings");
  NS_TEST_ASSERT_MSG_EQ (budget.headerBytesPerPacket, 90,
                         "CC11 data header must contribute 90 bytes per packet");
  NS_TEST_ASSERT_MSG_EQ (budget.wireBytes, 793520,
                         "N=8 watchdog must budget complete data frames");
  NS_TEST_ASSERT_MSG_EQ (budget.incumbentCount, 1,
                         "N=8 frozen cohort must retain its incumbent");
  NS_TEST_ASSERT_MSG_EQ (budget.occupancyBps, 12500000000ULL,
                         "watchdog occupancy must use the acknowledged upper bound");
  NS_TEST_ASSERT_MSG_EQ (budget.receiverCapacityBps, 100000000000ULL,
                         "watchdog must retain receiver line rate");
  NS_TEST_ASSERT_MSG_EQ (budget.residualBps, 87500000000ULL,
                         "watchdog serialization must use residual capacity");
  NS_TEST_ASSERT_MSG_EQ (budget.serializationNs, 72551,
                         "N=8 wire serialization must round upward");
  NS_TEST_ASSERT_MSG_EQ (budget.maxRttNs, 8320,
                         "watchdog must use the cohort's exact maximum RTT");
  NS_TEST_ASSERT_MSG_EQ (budget.delayNs, 80871,
                         "N=8 watchdog delay must match the frozen derivation");

  std::vector<GuardTransitionPrefixWaiterBudgetInput> boundaries = {
    {2000, 1000, 8000},
    {1500, 1500, 8000}
  };
  valid = RdmaHw::ComputeGuardTransitionPrefixWireBudget (
    boundaries, {}, 1000, dataHeaderBytes, 100000000000ULL, &budget);
  NS_TEST_ASSERT_MSG_EQ (valid, true, "MTU-boundary budget must be valid");
  NS_TEST_ASSERT_MSG_EQ (budget.roundedPayloadBudgetBytes, 2500,
                         "partial last packets must not invent rounded payload bytes");
  NS_TEST_ASSERT_MSG_EQ (budget.packetCount, 3,
                         "exact and partial MTU targets must count 1+2 packets");
  NS_TEST_ASSERT_MSG_EQ (budget.wireBytes, 2770,
                         "each partial or full packet must carry one header");
  NS_TEST_ASSERT_MSG_EQ (budget.occupancyBps, 0,
                         "an empty incumbent cohort must have zero occupancy");
  NS_TEST_ASSERT_MSG_EQ (budget.residualBps, 100000000000ULL,
                         "zero occupancy leaves the complete receiver capacity");

  std::vector<GuardTransitionPrefixWaiterBudgetInput> oneWaiter = {
    {1000, 1000, 8000}
  };
  std::vector<GuardTransitionPrefixIncumbentBudgetInput> incumbents = {
    {10000000000ULL, 9000},
    {15000000000ULL, 7000}
  };
  valid = RdmaHw::ComputeGuardTransitionPrefixWireBudget (
    oneWaiter, incumbents, 1000, dataHeaderBytes,
    100000000000ULL, &budget);
  NS_TEST_ASSERT_MSG_EQ (valid, true, "multiple incumbent budget must be valid");
  NS_TEST_ASSERT_MSG_EQ (budget.occupancyBps, 25000000000ULL,
                         "all actual acknowledged incumbent bounds must be summed");
  NS_TEST_ASSERT_MSG_EQ (budget.residualBps, 75000000000ULL,
                         "multiple incumbents must reduce residual capacity");
  NS_TEST_ASSERT_MSG_EQ (budget.maxRttNs, 9000,
                         "maximum RTT must include frozen incumbents");
  NS_TEST_ASSERT_MSG_EQ (budget.serializationNs, 117,
                         "residual serialization must round upward");
  NS_TEST_ASSERT_MSG_EQ (budget.delayNs, 9117,
                         "delay must add exact cohort RTT and serialization");

  valid = RdmaHw::ComputeGuardTransitionPrefixWireBudget (
    oneWaiter, {{100000000000ULL, 8000}}, 1000, dataHeaderBytes,
    100000000000ULL, &budget);
  NS_TEST_ASSERT_MSG_EQ (valid, false,
                         "zero residual capacity must fail closed");
  valid = RdmaHw::ComputeGuardTransitionPrefixWireBudget (
    {{18446744073709551615ULL, 18446744073709551615ULL, 8000}}, {},
    2, dataHeaderBytes, 100000000000ULL, &budget);
  NS_TEST_ASSERT_MSG_EQ (valid, false,
                         "wire-byte overflow must fail closed");
  valid = RdmaHw::ComputeGuardTransitionPrefixWireBudget (
    oneWaiter,
    {{18446744073709551615ULL, 8000}, {1, 8000}},
    1000, dataHeaderBytes, 18446744073709551615ULL, &budget);
  NS_TEST_ASSERT_MSG_EQ (valid, false,
                         "incumbent occupancy overflow must fail closed");
  valid = RdmaHw::ComputeGuardTransitionPrefixWireBudget (
    {{2000000000ULL, 2000000000ULL, 8000}}, {},
    1000, dataHeaderBytes, 1, &budget);
  NS_TEST_ASSERT_MSG_EQ (valid, false,
                         "delay outside signed simulator time must fail closed");

  NS_TEST_ASSERT_MSG_EQ (
    RdmaHw::IsGuardTransitionPrefixWatchdogAggregateInconsistent (1, 1, false),
    false,
    "one budget from one receiver must remain reconstructable");
  NS_TEST_ASSERT_MSG_EQ (
    RdmaHw::IsGuardTransitionPrefixWatchdogAggregateInconsistent (2, 1, true),
    true,
    "two transitions on one receiver must reject reconstruction");
  NS_TEST_ASSERT_MSG_EQ (
    RdmaHw::IsGuardTransitionPrefixWatchdogAggregateInconsistent (2, 2, false),
    true,
    "one transition on each of two receivers must reject mixed reconstruction");
  IntHeader::mode = savedIntMode;
}

GuardDeadlineSameTickTest::GuardDeadlineSameTickTest ()
  : TestCase ("GUARD deadline gives same-tick receiver progress priority"),
    m_ready (false),
    m_timedOut (false)
{
}

void
GuardDeadlineSameTickTest::Deadline (void)
{
  // Mirrors HandleGuardTransitionPrefixDeadline: the final decision gets a
  // fresh UID at the current timestamp, behind already queued DATA/removal.
  Simulator::ScheduleNow (&GuardDeadlineSameTickTest::Decide, this);
}

void
GuardDeadlineSameTickTest::Progress (void)
{
  m_ready = true;
}

void
GuardDeadlineSameTickTest::Decide (void)
{
  m_timedOut = !m_ready;
}

void
GuardDeadlineSameTickTest::DoRun (void)
{
  // Deliberately give the deadline the earlier UID at the same timestamp.
  Simulator::Schedule (NanoSeconds (10), &GuardDeadlineSameTickTest::Deadline, this);
  Simulator::Schedule (NanoSeconds (10), &GuardDeadlineSameTickTest::Progress, this);
  Simulator::Run ();
  NS_TEST_ASSERT_MSG_EQ (m_ready, true,
                         "same-tick receiver progress must be processed");
  NS_TEST_ASSERT_MSG_EQ (m_timedOut, false,
                         "same-tick receiver progress must beat fallback");
  Simulator::Destroy ();
}

GuardFirstGrantGateTagTest::GuardFirstGrantGateTagTest ()
  : TestCase ("GUARD exact first-grant gate packet tag")
{
}

void
GuardFirstGrantGateTagTest::DoRun (void)
{
  const uint64_t pairWindow = 104123;
  const uint64_t globalWindow = 208257;
  const uint64_t wireBytes = 1000;

  for (uint32_t globalT = 0; globalT <= 1; ++globalT)
    {
      uint64_t exactWindow = globalT == 1 ? globalWindow : pairWindow;
      FlowStatTag tag;
      tag.SetType (FlowStatTag::FLOW_START);
      tag.SetBaseRttSeconds (0.00000832);
      tag.SetFirstGrantGateBytes (exactWindow);

      Ptr<Packet> packet = Create<Packet> (wireBytes);
      packet->AddPacketTag (tag);
      NS_TEST_ASSERT_MSG_EQ (packet->GetSize (), wireBytes,
                             "packet tags must not change wire bytes");

      FlowStatTag decoded;
      NS_TEST_ASSERT_MSG_EQ (packet->PeekPacketTag (decoded), true,
                             "receiver must recover the flow-stat tag");
      NS_TEST_ASSERT_MSG_EQ (decoded.HasFirstGrantGateBytes (), true,
                             "gate watermark must be explicitly present");
      NS_TEST_ASSERT_MSG_EQ (decoded.GetFirstGrantGateBytes (), exactWindow,
                             "GLOBAL_T pair/global windows must round-trip exactly");
      NS_TEST_ASSERT_MSG_EQ (decoded.GetFirstGrantGateBytes () % wireBytes,
                             exactWindow % wireBytes,
                             "non-MTU-aligned gate bytes must not be rounded");
    }
}

GuardGrantHeaderTest::GuardGrantHeaderTest ()
  : TestCase ("GUARD compact rate-grant header")
{
}

void
GuardGrantHeaderTest::DoRun (void)
{
  GuardGrantHeader grant;
  grant.SetSport (1234);
  grant.SetDport (4321);
  grant.SetPG (4);
  grant.SetRateMbps (25000);
  grant.SetGeneration (17);
  grant.SetAckRequired (true);

  NS_TEST_ASSERT_MSG_EQ (grant.GetSerializedSize (), 15,
						 "selectively acknowledged compact grant must serialize to fifteen bytes");

  Ptr<Packet> packet = Create<Packet> (11);
  packet->AddHeader (grant);

  Ipv4Header ip;
  ip.SetSource (Ipv4Address ("10.0.0.1"));
  ip.SetDestination (Ipv4Address ("10.0.0.2"));
  ip.SetProtocol (CustomHeader::GUARD_RATE_GRANT);
  ip.SetPayloadSize (packet->GetSize ());
  ip.SetTtl (64);
  packet->AddHeader (ip);

  PppHeader ppp;
  ppp.SetProtocol (0x0021);
  packet->AddHeader (ppp);

  NS_TEST_ASSERT_MSG_EQ (packet->GetSize (), 60,
                         "compact grant must fit in the simulator's minimum frame");

  CustomHeader parsed (CustomHeader::L2_Header | CustomHeader::L3_Header |
                       CustomHeader::L4_Header);
  packet->PeekHeader (parsed);
  NS_TEST_ASSERT_MSG_EQ (parsed.l3Prot, CustomHeader::GUARD_RATE_GRANT,
                         "custom parser must identify a compact grant");
  NS_TEST_ASSERT_MSG_EQ (parsed.grant.sport, 1234,
                         "custom parser must preserve source port");
  NS_TEST_ASSERT_MSG_EQ (parsed.grant.dport, 4321,
                         "custom parser must preserve destination port");
  NS_TEST_ASSERT_MSG_EQ (parsed.grant.pg, 4,
                         "custom parser must preserve priority group");
  NS_TEST_ASSERT_MSG_EQ (parsed.grant.rateMbps, 25000,
                         "custom parser must preserve the rate");
  NS_TEST_ASSERT_MSG_EQ (parsed.grant.generation, 17,
                         "custom parser must preserve the generation");
  NS_TEST_ASSERT_MSG_EQ (parsed.grant.ackRequired, 1,
						 "custom parser must preserve the acknowledgement requirement");
  NS_TEST_ASSERT_MSG_EQ (parsed.GetSerializedSize (), 49,
						 "parsed L2, IPv4, and grant headers must total 49 bytes");

  GuardGrantHeader phasedGrant;
  phasedGrant.SetAckRequired (true);
  phasedGrant.SetPhaseTag (3);
  NS_TEST_ASSERT_MSG_EQ (phasedGrant.GetAckRequired (), true,
                         "V11 phase bits must not change the ACK-required bit");
  NS_TEST_ASSERT_MSG_EQ (phasedGrant.GetPhaseTag (), 3,
                         "V11 grant must expose its three-bit phase tag");
  phasedGrant.SetAckRequired (false);
  NS_TEST_ASSERT_MSG_EQ (phasedGrant.GetAckRequired (), false,
                         "clearing ACK-required must clear only bit zero");
  NS_TEST_ASSERT_MSG_EQ (phasedGrant.GetPhaseTag (), 3,
                         "clearing ACK-required must preserve the phase tag");
  NS_TEST_ASSERT_MSG_EQ (phasedGrant.GetSerializedSize (), 15,
                         "phase attribution must not expand the compact grant");
  phasedGrant.SetAckRequired (true);
  Ptr<Packet> phasedPacket = Create<Packet> ();
  phasedPacket->AddHeader (phasedGrant);
  GuardGrantHeader decodedPhasedGrant;
  phasedPacket->RemoveHeader (decodedPhasedGrant);
  NS_TEST_ASSERT_MSG_EQ (decodedPhasedGrant.GetAckRequired (), true,
                         "serialized phase bits must preserve ACK-required");
  NS_TEST_ASSERT_MSG_EQ (decodedPhasedGrant.GetPhaseTag (), 3,
                         "serialized compact grant must preserve its phase tag");

  GuardGrantAckHeader ack;
  ack.SetSport (4321);
  ack.SetDport (1234);
  ack.SetPG (4);
  ack.SetGeneration (17);
  NS_TEST_ASSERT_MSG_EQ (ack.GetSerializedSize (), 10,
                         "compact grant acknowledgement must serialize to ten bytes");

  Ptr<Packet> ackPacket = Create<Packet> (16);
  ackPacket->AddHeader (ack);
  ip.SetProtocol (CustomHeader::GUARD_RATE_GRANT_ACK);
  ip.SetPayloadSize (ackPacket->GetSize ());
  ackPacket->AddHeader (ip);
  ackPacket->AddHeader (ppp);
  NS_TEST_ASSERT_MSG_EQ (ackPacket->GetSize (), 60,
                         "compact grant acknowledgement must fit in the minimum frame");

  CustomHeader parsedAck (CustomHeader::L2_Header | CustomHeader::L3_Header |
                          CustomHeader::L4_Header);
  ackPacket->PeekHeader (parsedAck);
  NS_TEST_ASSERT_MSG_EQ (parsedAck.l3Prot, CustomHeader::GUARD_RATE_GRANT_ACK,
                         "custom parser must identify a grant acknowledgement");
  NS_TEST_ASSERT_MSG_EQ (parsedAck.grant.sport, 4321,
                         "grant acknowledgement must preserve source port");
  NS_TEST_ASSERT_MSG_EQ (parsedAck.grant.dport, 1234,
                         "grant acknowledgement must preserve destination port");
  NS_TEST_ASSERT_MSG_EQ (parsedAck.grant.pg, 4,
                         "grant acknowledgement must preserve priority group");
  NS_TEST_ASSERT_MSG_EQ (parsedAck.grant.generation, 17,
                         "grant acknowledgement must preserve generation");
  NS_TEST_ASSERT_MSG_EQ (parsedAck.GetSerializedSize (), 44,
                         "parsed L2, IPv4, and acknowledgement headers must total 44 bytes");
}
//-----------------------------------------------------------------------------
class PointToPointTestSuite : public TestSuite
{
public:
  PointToPointTestSuite ();
};

PointToPointTestSuite::PointToPointTestSuite ()
  : TestSuite ("devices-point-to-point", UNIT)
{
  AddTestCase (new PointToPointTest);
  AddTestCase (new GuardGrantHeaderTest);
  AddTestCase (new GuardFirstGrantGateTagTest);
  AddTestCase (new GuardDeadlineSameTickTest);
  AddTestCase (new GuardTransitionPrefixWireBudgetTest);
}

static PointToPointTestSuite g_pointToPointTestSuite;

} // namespace ns3
