#include "ns3/test.h"
#include "ns3/custom-header.h"
#include "ns3/drop-tail-queue.h"
#include "ns3/ipv4-header.h"
#include "ns3/packet.h"
#include "ns3/simulator.h"
#include "ns3/point-to-point-net-device.h"
#include "ns3/point-to-point-channel.h"
#include "ns3/ppp-header.h"
#include "ns3/qbb-header.h"

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

  NS_TEST_ASSERT_MSG_EQ (grant.GetSerializedSize (), 10,
                         "compact grant must serialize to ten bytes");

  Ptr<Packet> packet = Create<Packet> (16);
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
  NS_TEST_ASSERT_MSG_EQ (parsed.GetSerializedSize (), 44,
                         "parsed L2, IPv4, and grant headers must total 44 bytes");
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
}

static PointToPointTestSuite g_pointToPointTestSuite;

} // namespace ns3
