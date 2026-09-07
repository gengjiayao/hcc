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
#include "ns3/seq-ts-header.h"
#include "ns3/udp-header.h"

#include <algorithm>

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

class GuardInitialWindowPriorityEncodingTest : public TestCase
{
public:
  GuardInitialWindowPriorityEncodingTest ();
  virtual void DoRun (void);
};

class GuardTransportWindowFloorTest : public TestCase
{
public:
  GuardTransportWindowFloorTest ();
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

class GuardTransitionAuditSinkTest : public TestCase
{
public:
  GuardTransitionAuditSinkTest ();
  virtual void DoRun (void);

private:
  void Emit (Ptr<RdmaHw> hw, GuardTransitionAuditSink *sink,
             uint64_t epoch, uint64_t transaction);
};

class GuardMixedPgVectorTest : public TestCase
{
public:
  GuardMixedPgVectorTest ()
    : TestCase ("GUARD V14 canonical mixed-PG target vector") {}
  virtual void DoRun (void);
};

class GuardElephantAgingTest : public TestCase
{
public:
  GuardElephantAgingTest ()
    : TestCase ("GUARD V22 bounded elephant aging") {}
  virtual void DoRun (void);

private:
  void EvaluateAfterWait (void);

  Ptr<RdmaHw> m_allocator;
  Ptr<RdmaRxQueuePair> m_longOne;
  Ptr<RdmaRxQueuePair> m_longTwo;
  Ptr<RdmaRxQueuePair> m_longThree;
  std::vector<GuardVectorTargetInput> m_inputs;
};

class GuardElephantCapSpilloverTest : public TestCase
{
public:
  GuardElephantCapSpilloverTest ()
    : TestCase ("GUARD V23 cap-qualified elephant spillover") {}
  virtual void DoRun (void);

private:
  void Evaluate (void);

  Ptr<RdmaHw> m_allocator;
  Ptr<RdmaRxQueuePair> m_donor;
  std::vector<GuardVectorTargetInput> m_inputs;
};

class GuardElephantFabricTargetTest : public TestCase
{
public:
  GuardElephantFabricTargetTest ()
    : TestCase ("GUARD V25 class-scoped elephant fabric target") {}
  virtual void DoRun (void);
};

class GuardElephantReceiverAuthorityTest : public TestCase
{
public:
  GuardElephantReceiverAuthorityTest ()
    : TestCase ("GUARD V26 selected-elephant receiver authority") {}
  virtual void DoRun (void);
};

class GuardTerminalDrainCompletionTest : public TestCase
{
public:
  GuardTerminalDrainCompletionTest ()
    : TestCase ("GUARD V14 terminal draining completion serialization"),
      m_firstObserved (false),
      m_secondObserved (false) {}
  virtual void DoRun (void);

private:
  void CompleteFirst (void);
  void CompleteSecond (void);

  Ptr<RdmaHw> m_allocator;
  Ptr<RdmaRxQueuePair> m_first;
  Ptr<RdmaRxQueuePair> m_second;
  bool m_firstObserved;
  bool m_secondObserved;
};

class GuardActiveEmptyTransactionTest : public TestCase
{
public:
  GuardActiveEmptyTransactionTest ()
    : TestCase ("GUARD V14 last ACTIVE flow closes its live transaction") {}
  virtual void DoRun (void);

private:
  void RunPhase (uint32_t phase);
};

class GuardGrantTraceProvenanceTest : public TestCase
{
public:
  GuardGrantTraceProvenanceTest ()
    : TestCase ("GUARD V14 frozen-live grant trace provenance") {}
  virtual void DoRun (void);
};

class GuardFrozenCohortProvenanceTest : public TestCase
{
public:
  GuardFrozenCohortProvenanceTest ()
    : TestCase ("GUARD V14 frozen cohort and deferred waiter provenance") {}
  virtual void DoRun (void);
};

class GuardRetiredAckIdentityTest : public TestCase
{
public:
  GuardRetiredAckIdentityTest ()
    : TestCase ("GUARD V14 exact retired ACK wire identity") {}
  virtual void DoRun (void);
};

class GuardRetiredAckReceiveTest : public TestCase
{
public:
  GuardRetiredAckReceiveTest ()
    : TestCase ("GUARD V14 retired ACK receive state machine") {}
  virtual void DoRun (void);

private:
  void RunExactCase (bool keepRxQp);
};

void
GuardRetiredAckIdentityTest::DoRun (void)
{
  GuardQpIdentity identity = {0x0a000001, 0x0a000002, 4000, 5000, 4};
  GuardRetiredAckRecord record = {
    identity, 17, 3, GUARD_VECTOR_PREPARE_DECREASE, 25000, 91, 8, 42000};
  NS_TEST_ASSERT_MSG_EQ (
    RdmaHw::DoesGuardRetiredAckMatch (record, identity, 17, 3), true,
    "an exact tuple, generation, phase, and action must close");

  for (uint32_t field = 0; field < 5; ++field)
    {
      GuardQpIdentity mismatch = identity;
      if (field == 0) mismatch.sip++;
      if (field == 1) mismatch.dip++;
      if (field == 2) mismatch.sport++;
      if (field == 3) mismatch.dport++;
      if (field == 4) mismatch.pg++;
      NS_TEST_ASSERT_MSG_EQ (
        RdmaHw::DoesGuardRetiredAckMatch (record, mismatch, 17, 3), false,
        "every full-tuple field must participate in retired ACK identity");
    }
  NS_TEST_ASSERT_MSG_EQ (
    RdmaHw::DoesGuardRetiredAckMatch (record, identity, 18, 3), false,
    "a different run-monotonic generation must remain stale");
  NS_TEST_ASSERT_MSG_EQ (
    RdmaHw::DoesGuardRetiredAckMatch (record, identity, 17, 1), false,
    "fast and transition prepare phases must not alias");

  GuardRetiredAckRecord incompatible = record;
  incompatible.action = GUARD_VECTOR_ACTIVATE_WAITER;
  NS_TEST_ASSERT_MSG_EQ (
    RdmaHw::DoesGuardRetiredAckMatch (incompatible, identity, 17, 3), false,
    "a phase-incompatible frozen action must remain stale");

  GuardQpIdentity legacyCollision = identity;
  legacyCollision.sip++;
  legacyCollision.pg++;
  NS_TEST_ASSERT_MSG_EQ (
    RdmaHw::DoesGuardRetiredAckMatch (record, legacyCollision, 17, 3), false,
    "a legacy-key collision must not alias the complete wire tuple");
}

void
GuardRetiredAckReceiveTest::RunExactCase (bool keepRxQp)
{
  Ptr<RdmaHw> hw = CreateObject<RdmaHw> ();
  GuardQpIdentity identity = {0x0a00000f, 0x0a000001, 5000, 4000, 4};
  Ptr<RdmaRxQueuePair> rx = hw->GetRxQp (
    identity.sip, identity.dip, identity.sport, identity.dport,
    identity.pg, true);
  NS_TEST_ASSERT_MSG_NE (rx, 0, "test RxQP creation must succeed");
  if (!keepRxQp)
    {
      hw->DeleteRxQp (identity.dip, identity.dport,
                      identity.sport, identity.pg);
    }

  GuardRetiredAckRecord record = {
    identity, 17, 3, GUARD_VECTOR_PREPARE_DECREASE, 25000, 91, 8, 0};
  hw->m_guardRetiredAckRecords.push_back (record);
  hw->m_guardPendingGrantAcks = 0;
  CustomHeader ch;
  ch.dip = identity.sip;
  ch.sip = identity.dip;
  ch.grantAck.dport = identity.sport;
  ch.grantAck.sport = identity.dport;
  ch.grantAck.pg = identity.pg;
  ch.grantAck.generation = record.generation;
  ch.grantAck.phaseTag = record.phaseTag;
  Ptr<Packet> packet = Create<Packet> (60);

  hw->ReceiveGuardGrantAck (packet, ch);
  NS_TEST_ASSERT_MSG_EQ (hw->m_guardRetiredAckRecords.size (), 0,
                         "an exact retired ACK must release its slot");
  NS_TEST_ASSERT_MSG_EQ (hw->m_guardRetiredAcksReceived, 1,
                         "an exact retired ACK must increment its dedicated receipt");
  NS_TEST_ASSERT_MSG_EQ (hw->m_guardGrantAcksReceived, 1,
                         "an exact retired ACK must close the global wire count");
  NS_TEST_ASSERT_MSG_EQ (hw->m_guardPendingGrantAcks, 0,
                         "a retired wire receipt must never decrement pending again");
  NS_TEST_ASSERT_MSG_EQ (hw->m_guardFullyAckedBatches, 0,
                         "a retired wire receipt must not finish a batch again");

  hw->ReceiveGuardGrantAck (packet, ch);
  NS_TEST_ASSERT_MSG_EQ (hw->m_guardRetiredAcksReceived, 1,
                         "a duplicate retired ACK must not be accepted twice");
  NS_TEST_ASSERT_MSG_EQ (hw->m_guardGrantAcksStale, 1,
                         "a duplicate retired ACK must become stale");
}

void
GuardRetiredAckReceiveTest::DoRun (void)
{
  RunExactCase (true);
  RunExactCase (false);

  Ptr<RdmaHw> mismatch = CreateObject<RdmaHw> ();
  GuardQpIdentity identity = {0x0a00000f, 0x0a000001, 5000, 4000, 4};
  GuardRetiredAckRecord record = {
    identity, 17, 3, GUARD_VECTOR_PREPARE_DECREASE, 25000, 91, 8, 0};
  mismatch->m_guardRetiredAckRecords.push_back (record);
  mismatch->ResetGuardFastpathEpoch ();
  NS_TEST_ASSERT_MSG_EQ (mismatch->m_guardRetiredAckRecords.size (), 1,
                         "epoch reset must preserve in-flight retired ACK identity");
  CustomHeader wrong;
  wrong.dip = identity.sip;
  wrong.sip = identity.dip;
  wrong.grantAck.dport = identity.sport;
  wrong.grantAck.sport = identity.dport;
  wrong.grantAck.pg = identity.pg;
  wrong.grantAck.generation = record.generation;
  wrong.grantAck.phaseTag = 1;
  mismatch->ReceiveGuardGrantAck (Create<Packet> (60), wrong);
  NS_TEST_ASSERT_MSG_EQ (mismatch->m_guardRetiredAckRecords.size (), 1,
                         "a phase mismatch must preserve the retired record");
  NS_TEST_ASSERT_MSG_EQ (mismatch->m_guardGrantAcksStale, 1,
                         "a phase mismatch must be accounted as stale");
}

void
GuardFrozenCohortProvenanceTest::DoRun (void)
{
  GuardFrozenActiveProvenanceInput cohort = {};
  cohort.inCohort = true;

  GuardFrozenActiveProvenanceInput deferred = {};
  deferred.inFastpathWaiters = true;
  deferred.registrationMembershipRevision = 6;
  deferred.liveMembershipRevision = 6;
  deferred.frozenMembershipRevision = 5;
  deferred.pendingMembershipChanges = 1;
  deferred.receiverNic = 3;
  deferred.frozenReceiverNic = 3;
  deferred.receiverCapacityBps = 100000000000ULL;
  deferred.frozenReceiverCapacityBps = 100000000000ULL;
  deferred.registerNs = 42000;
  deferred.flowSizeBytes = 8ULL * 1024ULL * 1024ULL;
  deferred.hasFirstGrantGateBytes = true;
  deferred.firstGrantGateBytes = 104000;
  deferred.baseRttSec = 0.00000832;

  std::vector<GuardFrozenActiveProvenanceInput> live;
  live.push_back (cohort);
  live.push_back (cohort);
  live.push_back (deferred);
  uint32_t cohortCount = 0;
  uint32_t deferredCount = 0;
  for (const auto &record : live)
    {
      GuardFrozenActiveProvenanceDisposition disposition =
          RdmaHw::ClassifyGuardFrozenActiveProvenance (record);
      cohortCount += disposition == GUARD_FROZEN_ACTIVE_COHORT ? 1 : 0;
      deferredCount +=
          disposition == GUARD_FROZEN_ACTIVE_DEFERRED_WAITER ? 1 : 0;
      NS_TEST_ASSERT_MSG_NE (
          disposition, GUARD_FROZEN_ACTIVE_INVALID,
          "cohort two plus deferred one must classify without mutating the vector");
    }
  NS_TEST_ASSERT_MSG_EQ (cohortCount, 2,
                         "the frozen ACTIVE count is the cohort only");
  NS_TEST_ASSERT_MSG_EQ (deferredCount, 1,
                         "the live ACTIVE count may include one deferred waiter");

  GuardFrozenActiveProvenanceInput invalid = deferred;
  invalid.inFastpathWaiters = false;
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::ClassifyGuardFrozenActiveProvenance (invalid),
      GUARD_FROZEN_ACTIVE_INVALID,
      "non-waiter outside the cohort must fail closed");

  invalid = deferred;
  invalid.lastIssuedTargetBps = 1;
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::ClassifyGuardFrozenActiveProvenance (invalid),
      GUARD_FROZEN_ACTIVE_INVALID,
      "nonzero deferred potential must fail closed");

  invalid = deferred;
  invalid.pendingMembershipChanges = 0;
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::ClassifyGuardFrozenActiveProvenance (invalid),
      GUARD_FROZEN_ACTIVE_INVALID,
      "deferred waiter without dirty membership must fail closed");

  invalid = deferred;
  invalid.receiverNic = 4;
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::ClassifyGuardFrozenActiveProvenance (invalid),
      GUARD_FROZEN_ACTIVE_INVALID,
      "deferred waiter on another receiver NIC must fail closed");

  invalid = deferred;
  invalid.hasFirstGrantGateBytes = false;
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::ClassifyGuardFrozenActiveProvenance (invalid),
      GUARD_FROZEN_ACTIVE_INVALID,
      "deferred waiter without first-grant metadata must fail closed");
}

void
GuardGrantTraceProvenanceTest::DoRun (void)
{
  const uint64_t capacity = 100000000000ULL;
  const uint64_t frozenDraining = 33776000000ULL;
  const uint64_t frozenAllocatable = 66224000000ULL;
  const uint64_t encodedTargets = 66222000000ULL;
  const uint64_t activationGrant = 22266000000ULL;

  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::ValidateGuardGrantTraceSnapshot (
          capacity, frozenDraining, frozenAllocatable, encodedTargets,
          activationGrant, frozenDraining, false),
      true, "freeze-time D and A must admit the encoded vector");
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::ValidateGuardGrantTraceSnapshot (
          capacity, frozenDraining, frozenAllocatable, encodedTargets,
          activationGrant, 0, true),
      true, "a completed live drain may retain the frozen tuple through its ACK barrier");
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::ValidateGuardGrantTraceSnapshot (
          capacity, frozenDraining, frozenAllocatable, encodedTargets,
          activationGrant, 0, false),
      false, "live D below frozen D must arm a capacity recompute");
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::ValidateGuardGrantTraceSnapshot (
          capacity, frozenDraining, frozenAllocatable, encodedTargets,
          activationGrant, frozenDraining + 1, true),
      false, "live D must never exceed the in-flight frozen reservation");
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::ValidateGuardGrantTraceSnapshot (
          capacity, 0, frozenAllocatable, encodedTargets,
          activationGrant, 0, true),
      false, "substituting live D for frozen D must not validate");
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::ValidateGuardGrantTraceSnapshot (
          capacity, 0, capacity, capacity - 2000000ULL,
          capacity / 2, 0, true),
      true, "the next allocation revision may refreeze the released capacity");
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::ValidateGuardGrantTraceSnapshot (
          capacity, 0, capacity, capacity + 1,
          capacity / 2, 0, true),
      false, "encoded targets must fit frozen A");
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::ValidateGuardGrantTraceSnapshot (
          capacity, 0, capacity, capacity - 1,
          capacity + 1, 0, true),
      false, "an individual grant must fit frozen A");
}

void
GuardMixedPgVectorTest::DoRun (void)
{
  GuardQpIdentity first = {1, 2, 100, 200, 3};
  GuardQpIdentity second = {4, 2, 101, 201, 7};
  std::vector<GuardVectorTargetInput> inputs;
  inputs.push_back ({second, GUARD_VECTOR_WAITER, 9000, 1000,
                     30000000000ULL, 0});
  inputs.push_back ({first, GUARD_VECTOR_INCUMBENT, 8000, 2000,
                     70000000000ULL, 0});
  std::vector<GuardFrozenTargetRecord> records;
  uint64_t hash = 0;
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::EncodeGuardTargetVector (inputs, 5, 100000000000ULL,
                                       0, 100000000000ULL,
                                       100000000ULL, 17, 1, 2, 1,
                                       &records, &hash),
      true, "70/30 mixed-PG vector must fit one 100G receiver");
  NS_TEST_ASSERT_MSG_EQ (records.size (), 2,
                         "every live tuple must have one target record");
  bool canonicalFirst = records[0].identity == first;
  NS_TEST_ASSERT_MSG_EQ (canonicalFirst, true,
                         "records must be canonical rather than insertion ordered");
  NS_TEST_ASSERT_MSG_EQ (records[0].targetMbps, 70000,
                         "targets must record actual encoded wire Mbps");
  NS_TEST_ASSERT_MSG_EQ (records[1].targetMbps, 30000,
                         "mixed PGs must share one capacity budget");

  std::reverse (inputs.begin (), inputs.end ());
  std::vector<GuardFrozenTargetRecord> permuted;
  uint64_t permutedHash = 0;
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::EncodeGuardTargetVector (inputs, 5, 100000000000ULL,
                                       0, 100000000000ULL,
                                       100000000ULL, 17, 1, 2, 1, &permuted,
                                       &permutedHash),
      true, "input permutation must remain encodable");
  NS_TEST_ASSERT_MSG_EQ (permutedHash, hash,
                         "canonical hash must ignore container iteration order");

  uint64_t progressHash = 0;
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::EncodeGuardTargetVector (inputs, 5, 100000000000ULL,
                                       0, 100000000000ULL,
                                       100000000ULL, 17, 1, 3, 1, &permuted,
                                       &progressHash),
      true, "a later progress revision must remain encodable");
  NS_TEST_ASSERT_MSG_NE (progressHash, hash,
                         "late progress must identify a later frozen vector");
  uint64_t reasonHash = 0;
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::EncodeGuardTargetVector (inputs, 5, 100000000000ULL,
                                       0, 100000000000ULL,
                                       100000000ULL, 17, 1, 2, 2, &permuted,
                                       &reasonHash),
      true, "a progress reason must remain encodable");
  NS_TEST_ASSERT_MSG_NE (reasonHash, hash,
                         "reason changes must identify a distinct frozen vector");
  uint32_t frozenTarget = permuted[0].targetMbps;
  inputs[0].requestedBps = 1;
  NS_TEST_ASSERT_MSG_EQ (permuted[0].targetMbps, frozenTarget,
                         "mutable requested rate must not change a frozen target");

  GuardQpIdentity third = {5, 2, 102, 202, 5};
  std::vector<GuardVectorTargetInput> transition;
  transition.push_back ({first, GUARD_VECTOR_INCUMBENT, 7000, 3000,
                         80000000000ULL, 0});
  transition.push_back ({second, GUARD_VECTOR_INCUMBENT, 6000, 4000,
                         10000000000ULL, 0});
  transition.push_back ({third, GUARD_VECTOR_WAITER, 5000, 5000,
                         10000000000ULL, 0});
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::EncodeGuardTargetVector (transition, 5, 100000000000ULL,
                                       0, 100000000000ULL,
                                       100000000ULL, 18, 3, 4, 1,
                                       &records, &hash),
      true, "80/10/10 transition vector must fit one receiver");
  uint64_t incumbentOneTarget = 0;
  uint64_t incumbentTwoTarget = 0;
  uint64_t waiterTarget = 0;
  for (const auto &record : records)
    {
      if (record.identity == first) incumbentOneTarget = record.targetMbps;
      if (record.identity == second) incumbentTwoTarget = record.targetMbps;
      if (record.identity == third) waiterTarget = record.targetMbps;
    }
  bool prepareDecrease = incumbentTwoTarget < 30000;
  NS_TEST_ASSERT_MSG_EQ (prepareDecrease, true,
                         "70/30 to 80/10/10 must prepare the 30G decrease");
  bool continuingIncrease = incumbentOneTarget > 70000;
  NS_TEST_ASSERT_MSG_EQ (continuingIncrease, true,
                         "continuing incumbent increase belongs to activation");
  NS_TEST_ASSERT_MSG_EQ ((incumbentOneTarget + incumbentTwoTarget + waiterTarget),
                         100000,
                         "post-prepare activation potential must remain at C");
  NS_TEST_ASSERT_MSG_EQ (waiterTarget, 10000,
                         "new waiter must activate only at its vector target");

  inputs[0].requestedBps = 80000000000ULL;
  inputs[1].requestedBps = 80000000000ULL;
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::EncodeGuardTargetVector (inputs, 5, 100000000000ULL,
                                       0, 100000000000ULL,
                                       100000000ULL, 17, 1, 2, 1,
                                       &records, &hash),
      false, "two PG-local 80G targets must not manufacture 160G capacity");

  std::vector<GuardVectorTargetInput> rounded;
  rounded.push_back ({first, GUARD_VECTOR_INCUMBENT, 1, 0, 1, 0});
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::EncodeGuardTargetVector (rounded, 5, 100000000000ULL,
                                       0, 100000000000ULL,
                                       100000001ULL, 17, 1, 2, 1,
                                       &records, &hash),
      true, "minimum sender rate must be representable after Mbps rounding");
  NS_TEST_ASSERT_MSG_EQ (records[0].targetMbps, 101,
                         "encoded target must ceil the effective sender minimum");

  Ptr<RdmaHw> allocator = CreateObject<RdmaHw> ();
  allocator->m_mtu = 1000;
  allocator->m_minRate = DataRate (100000000ULL);
  allocator->m_guardRemainingAware = true;
  allocator->m_guardMinShareFraction = 0.0;
  allocator->m_guardRemainingExponent = 1.0;
  std::vector<GuardVectorTargetInput> refresh;
  refresh.push_back ({first, GUARD_VECTOR_INCUMBENT, 1000, 9000, 0, 0});
  refresh.push_back ({second, GUARD_VECTOR_INCUMBENT, 3000, 7000, 0, 0});
  NS_TEST_ASSERT_MSG_EQ (
      allocator->ComputeGuardFrozenRequestedTargets (100000000000ULL,
                                                       100000000000ULL,
                                                       &refresh),
      true, "50/50 progress refresh must fit the receiver capacity");
  NS_TEST_ASSERT_MSG_GT (refresh[0].requestedBps, 74000000000ULL,
                         "1:3 frozen remaining must produce the 75G side");
  NS_TEST_ASSERT_MSG_LT (refresh[1].requestedBps, 26000000000ULL,
                         "1:3 frozen remaining must produce the 25G side");
  bool refreshBounded =
    refresh[0].requestedBps + refresh[1].requestedBps <= 100000000000ULL;
  NS_TEST_ASSERT_MSG_EQ (refreshBounded, true,
                         "remaining-aware refresh must remain bounded by C");

  allocator->m_guardReceiverConcurrency = 1;
  allocator->m_guardConcurrencyMinBdps = 12.0;
  Ptr<RdmaRxQueuePair> longOne = CreateObject<RdmaRxQueuePair> ();
  Ptr<RdmaRxQueuePair> longTwo = CreateObject<RdmaRxQueuePair> ();
  Ptr<RdmaRxQueuePair> longThree = CreateObject<RdmaRxQueuePair> ();
  for (const auto &flow : {longOne, longTwo, longThree})
    {
      flow->m_guard_flow_size = 2000000;
      flow->m_base_rtt_sec = 0.000008;
    }
  std::vector<GuardVectorTargetInput> boundedElephants;
  boundedElephants.push_back (
    {first, GUARD_VECTOR_INCUMBENT, 2000000, 0, 0, PeekPointer (longOne)});
  boundedElephants.push_back (
    {second, GUARD_VECTOR_INCUMBENT, 1000000, 0, 0, PeekPointer (longTwo)});
  boundedElephants.push_back (
    {third, GUARD_VECTOR_WAITER, 3000000, 0, 0, PeekPointer (longThree)});
  NS_TEST_ASSERT_MSG_EQ (
      allocator->ComputeGuardFrozenRequestedTargets (100000000000ULL,
                                                       100000000000ULL,
                                                       &boundedElephants),
      true, "one of three >12-BDP elephants must receive residual service");
  NS_TEST_ASSERT_MSG_EQ (boundedElephants[0].requestedBps, 100000000ULL,
                         "larger elephant must retain the starvation floor");
  NS_TEST_ASSERT_MSG_EQ (boundedElephants[1].requestedBps, 99800000000ULL,
                         "shortest elephant must receive the residual capacity");
  NS_TEST_ASSERT_MSG_EQ (boundedElephants[2].requestedBps, 100000000ULL,
                         "largest elephant must retain the starvation floor");
  NS_TEST_ASSERT_MSG_EQ (allocator->m_guardConcurrencyLimitedAllocations, 1,
                         "bounded frozen allocation must be counted once");
  NS_TEST_ASSERT_MSG_EQ (allocator->m_guardConcurrencyMaxDeferredFlows, 2,
                         "two excess elephants must be recorded as deferred");

  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::ComputeGuardEffectiveElephantConcurrency (2, 3, true), 1,
      "three elephants must retain the low-contention K=1 policy");
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::ComputeGuardEffectiveElephantConcurrency (2, 4, true), 2,
      "four elephants must promote to K=2 under the square-root rule");
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::ComputeGuardEffectiveElephantConcurrency (3, 8, true), 2,
      "sublinear concurrency must not promote before nine elephants");
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::ComputeGuardEffectiveElephantConcurrency (3, 9, true), 3,
      "nine elephants may use the configured K=3 ceiling");
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::ComputeGuardSizeClassElephantConcurrency (2, 3, 1000000,
                                                         2000000, true), 2,
      "the inclusive dyadic boundary must admit K=2");
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::ComputeGuardSizeClassElephantConcurrency (2, 3, 1000000,
                                                         2000001, true), 1,
      "different dyadic size classes must retain K=1 SRPT");

  allocator->m_guardReceiverConcurrency = 2;
  allocator->m_guardAdaptiveElephantConcurrency = true;
  allocator->m_guardConcurrencyLimitedAllocations = 0;
  allocator->m_guardConcurrencyMaxDeferredFlows = 0;
  allocator->m_guardAdaptiveConcurrencyPromotions = 0;
  allocator->m_guardAdaptiveConcurrencyMaxEffective = 0;
  for (auto &input : boundedElephants) input.requestedBps = 0;
  NS_TEST_ASSERT_MSG_EQ (
      allocator->ComputeGuardFrozenRequestedTargets (100000000000ULL,
                                                       100000000000ULL,
                                                       &boundedElephants),
      true, "adaptive K must preserve a valid three-elephant vector");
  NS_TEST_ASSERT_MSG_EQ (boundedElephants[0].requestedBps, 100000000ULL,
                         "E=3 must not promote beyond the K=1 baseline");
  NS_TEST_ASSERT_MSG_EQ (boundedElephants[1].requestedBps, 99800000000ULL,
                         "E=3 must keep residual service on one elephant");
  NS_TEST_ASSERT_MSG_EQ (allocator->m_guardAdaptiveConcurrencyPromotions, 0,
                         "E=3 must not count a promotion");

  Ptr<RdmaRxQueuePair> longFour = CreateObject<RdmaRxQueuePair> ();
  longFour->m_guard_flow_size = 2000000;
  longFour->m_base_rtt_sec = 0.000008;
  GuardQpIdentity fourth = {8, 9, 16, 26, 5};
  boundedElephants.push_back (
    {fourth, GUARD_VECTOR_WAITER, 4000000, 0, 0, PeekPointer (longFour)});
  for (auto &input : boundedElephants) input.requestedBps = 0;
  NS_TEST_ASSERT_MSG_EQ (
      allocator->ComputeGuardFrozenRequestedTargets (100000000000ULL,
                                                       100000000000ULL,
                                                       &boundedElephants),
      true, "adaptive K must preserve a valid four-elephant vector");
  uint32_t aboveFloor = 0;
  for (const auto &input : boundedElephants)
    {
      if (input.requestedBps > 100000000ULL) aboveFloor++;
    }
  NS_TEST_ASSERT_MSG_EQ (aboveFloor, 2,
                         "E=4 must admit exactly two elephants to residual service");
  NS_TEST_ASSERT_MSG_EQ (allocator->m_guardAdaptiveConcurrencyPromotions, 1,
                         "E=4 promotion must be counted once");
  NS_TEST_ASSERT_MSG_EQ (allocator->m_guardAdaptiveConcurrencyMaxEffective, 2,
                         "the maximum effective adaptive K must be observable");

  allocator->m_guardAdaptiveElephantConcurrency = false;
  allocator->m_guardSizeClassElephantConcurrency = true;
  allocator->m_guardSizeClassConcurrencyPromotions = 0;
  allocator->m_guardSizeClassConcurrencyMaxEffective = 0;
  boundedElephants.pop_back ();
  for (auto &input : boundedElephants) input.requestedBps = 0;
  NS_TEST_ASSERT_MSG_EQ (
      allocator->ComputeGuardFrozenRequestedTargets (100000000000ULL,
                                                       100000000000ULL,
                                                       &boundedElephants),
      true, "same-class elephants must preserve a bounded frozen vector");
  aboveFloor = 0;
  for (const auto &input : boundedElephants)
    {
      if (input.requestedBps > 100000000ULL) aboveFloor++;
    }
  NS_TEST_ASSERT_MSG_EQ (aboveFloor, 2,
                         "same-class policy must admit exactly two elephants");
  NS_TEST_ASSERT_MSG_EQ (allocator->m_guardSizeClassConcurrencyPromotions, 1,
                         "same-class promotion must be counted once");
  NS_TEST_ASSERT_MSG_EQ (allocator->m_guardSizeClassConcurrencyMaxEffective, 2,
                         "same-class maximum effective K must be observable");

  boundedElephants[0].remainingBytes = 2000001;
  for (auto &input : boundedElephants) input.requestedBps = 0;
  NS_TEST_ASSERT_MSG_EQ (
      allocator->ComputeGuardFrozenRequestedTargets (100000000000ULL,
                                                       100000000000ULL,
                                                       &boundedElephants),
      true, "different-class elephants must preserve a bounded K=1 vector");
  aboveFloor = 0;
  for (const auto &input : boundedElephants)
    {
      if (input.requestedBps > 100000000ULL) aboveFloor++;
    }
  NS_TEST_ASSERT_MSG_EQ (aboveFloor, 1,
                         "different size classes must retain one residual elephant");

  std::vector<GuardVectorTargetInput> afterDrain = {
    {first, GUARD_VECTOR_WAITER, 10000, 0, 100000000000ULL, 0}
  };
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::EncodeGuardTargetVector (afterDrain, 5, 100000000000ULL,
                                       50000000000ULL, 50000000000ULL,
                                       100000000ULL, 19, 5, 6, 8,
                                       &records, &hash),
      true, "50G drain plus a 100G request must allocate only residual A");
  NS_TEST_ASSERT_MSG_EQ (records[0].targetMbps, 50000,
                         "remove-at-50 plus new-at-100 must not issue 150G");
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::EncodeGuardTargetVector (afterDrain, 5, 100000000000ULL,
                                       100000000000ULL, 0,
                                       100000000ULL, 20, 6, 7, 8,
                                       &records, &hash),
      false, "sole 100G drain must gate a new 100G waiter");
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::EncodeGuardTargetVector (afterDrain, 5, 100000000000ULL,
                                       99950000000ULL, 50000000ULL,
                                       100000000ULL, 21, 7, 8, 8,
                                       &records, &hash),
      false, "D=99.95G must fail below the 100M effective minimum");
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::ComputeGuardDrainingReservationBps (
        50000000000ULL, 100000000000ULL, 100000000ULL),
      100000000000ULL,
      "pending decrease removal must reserve the possibly applied issue");
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::CanGuardAllocationFloorFit (
        100000000000ULL, 99500000000ULL, 5, 100000000ULL),
      true,
      "five 100M floors must exactly fit the residual capacity");
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::CanGuardAllocationFloorFit (
        100000000000ULL, 99500000000ULL, 6, 100000000ULL),
      false,
      "six 100M floors must defer while only 500M remains");
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::CanGuardAllocationFloorFit (
        100000000000ULL, 100000000001ULL, 1, 100000000ULL),
      false,
      "a draining reservation above capacity must fail closed");
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::CanGuardAllocationFloorFit (
        18446744073709551615ULL, 0, 18446744073709551615ULL,
        18446744073709551615ULL),
      false,
      "floor multiplication must not overflow into admission");
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::CanReleaseGuardDraining (10000, 9000), false,
      "an out-of-order final packet must not release draining");
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::CanReleaseGuardDraining (10000, 10000), true,
      "contiguous completion must release draining");
  Ptr<RdmaRxQueuePair> pendingCompletion = CreateObject<RdmaRxQueuePair> ();
  pendingCompletion->m_guard_flow_size = 10000;
  pendingCompletion->ReceiverNextExpectedSeq = 10000;
  GuardDrainingRecord pendingCompletionRecord = {
    second, pendingCompletion, 5, second.pg,
    50000000000ULL, 50000000000ULL, 50000000000ULL, 1, 0, 1,
    GUARD_DRAIN_PENDING
  };
  allocator->m_guardDrainingRecords.emplace (
    PeekPointer (pendingCompletion), pendingCompletionRecord);
  NS_TEST_ASSERT_MSG_EQ (
      allocator->ReleaseGuardDrainingOnCompletion (pendingCompletion), false,
      "pending contiguous completion must wait for the transaction boundary");
  NS_TEST_ASSERT_MSG_EQ (allocator->m_guardDrainingRecords.size (), 1,
                         "pending completion must preserve D until boundary");
  allocator->m_guardDrainingRecords.erase (PeekPointer (pendingCompletion));
  GuardDrainingRecord activeEmptyRecord = {
    first, CreateObject<RdmaRxQueuePair> (), 5, first.pg,
    50000000000ULL, 50000000000ULL, 50000000000ULL, 1, 1000, 1,
    GUARD_DRAINING
  };
  allocator->m_guardDrainingRecords.emplace (
    PeekPointer (activeEmptyRecord.hold), activeEmptyRecord);
  NS_TEST_ASSERT_MSG_EQ (allocator->CanResetGuardCoordinator (), false,
                         "ACTIVE empty with draining nonempty must not reset");

  uint64_t senderCollisionA = RdmaHw::GetQpKey (9, 10, 1, 2);
  uint64_t senderCollisionB = RdmaHw::GetQpKey (9, 10, 3, 0);
  NS_TEST_ASSERT_MSG_EQ (senderCollisionA, senderCollisionB,
                         "regression fixture must exercise the legacy port/PG overlap");
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::ClassifyGuardQpInsertion (0, 0, first), GUARD_QP_INSERT_OK,
      "a fresh sender tuple must be insertable");
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::ClassifyGuardQpInsertion (&first, 0, first),
      GUARD_QP_INSERT_LIVE_DUPLICATE,
      "an exact live sender tuple must not replace its existing QP");
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::ClassifyGuardQpInsertion (&second, 0, first),
      GUARD_QP_INSERT_LIVE_COLLISION,
      "a live sender legacy-key alias must fail closed");
  // Model Add(T) -> Delete(T) -> Add(T): deletion leaves T as the exact
  // tombstone supplied to the insertion classifier, so the replay is retired.
  GuardQpInsertionDisposition senderAfterDelete =
      RdmaHw::ClassifyGuardQpInsertion (0, &first, first);
  NS_TEST_ASSERT_MSG_EQ (senderAfterDelete, GUARD_QP_INSERT_RETIRED_TUPLE,
                         "Add/Delete/Add must not resurrect a sender tuple");
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::ClassifyGuardQpInsertion (0, &second, first),
      GUARD_QP_INSERT_TOMBSTONE_COLLISION,
      "a sender tombstone alias must fail closed");
  bool identitiesDiffer = first != second;
  NS_TEST_ASSERT_MSG_EQ (identitiesDiffer, true,
                         "full tuple identity must distinguish legacy aliases");
  uint64_t receiverCollisionA = RdmaHw::GetRxQpKey (9, 1, 2, 1);
  uint64_t receiverCollisionB = RdmaHw::GetRxQpKey (9, 1, 0, 3);
  NS_TEST_ASSERT_MSG_EQ (receiverCollisionA, receiverCollisionB,
                         "Rx fixture must exercise the legacy sport/PG overlap");

  Ptr<RdmaHw> tombstoneHw = CreateObject<RdmaHw> ();
  Ptr<RdmaRxQueuePair> live = tombstoneHw->GetRxQp (1, 2, 3, 4, 5, true);
  NS_TEST_ASSERT_MSG_NE (live, 0, "fresh full tuple must create one RxQP");
  tombstoneHw->DeleteRxQp (2, 4, 3, 5);
  Ptr<RdmaRxQueuePair> replay =
      tombstoneHw->GetRxQp (1, 2, 3, 4, 5, true);
  NS_TEST_ASSERT_MSG_EQ (replay, 0,
                         "late DATA must not resurrect an exact RxQP tombstone");
}

void
GuardElephantAgingTest::EvaluateAfterWait (void)
{
  for (auto &input : m_inputs) input.requestedBps = 0;
  NS_TEST_ASSERT_MSG_EQ (
      m_allocator->ComputeGuardFrozenRequestedTargets (100000000000ULL,
                                                        100000000000ULL,
                                                        &m_inputs),
      true, "aged K=1 vector must remain capacity bounded");
  NS_TEST_ASSERT_MSG_EQ (m_inputs[0].requestedBps, 99800000000ULL,
                         "oldest deferred elephant must receive one residual quantum");
  NS_TEST_ASSERT_MSG_EQ (m_inputs[1].requestedBps, 100000000ULL,
                         "the shortest elephant yields only for the aged quantum");
  NS_TEST_ASSERT_MSG_EQ (m_inputs[2].requestedBps, 100000000ULL,
                         "other deferred elephants retain the bounded floor");
  NS_TEST_ASSERT_MSG_EQ (m_allocator->m_guardElephantAgingRotations, 1,
                         "one threshold crossing must produce one rotation");
  NS_TEST_ASSERT_MSG_EQ (m_allocator->m_guardElephantAgingMaxWaitNs, 16000,
                         "the observed wait must use the exact per-flow RTT threshold");

  m_longOne->m_guard_flow_size = 1000000;
  for (auto &input : m_inputs) input.requestedBps = 0;
  NS_TEST_ASSERT_MSG_EQ (
      m_allocator->ComputeGuardFrozenRequestedTargets (100000000000ULL,
                                                        100000000000ULL,
                                                        &m_inputs),
      true, "a flow leaving the elephant cohort must remain allocatable");
  NS_TEST_ASSERT_MSG_EQ (m_longOne->m_guard_elephant_deferred_since_ns, -1,
                         "non-elephants must not retain a stale aging timestamp");
}

void
GuardElephantAgingTest::DoRun (void)
{
  m_allocator = CreateObject<RdmaHw> ();
  m_allocator->m_mtu = 1000;
  m_allocator->m_minRate = DataRate (100000000ULL);
  m_allocator->m_guardRemainingAware = true;
  m_allocator->m_guardMinShareFraction = 0.0;
  m_allocator->m_guardRemainingExponent = 1.0;
  m_allocator->m_guardReceiverConcurrency = 1;
  m_allocator->m_guardAdaptiveElephantConcurrency = false;
  m_allocator->m_guardElephantAgingRtts = 2.0;
  m_allocator->m_guardConcurrencyMinBdps = 12.0;

  m_longOne = CreateObject<RdmaRxQueuePair> ();
  m_longTwo = CreateObject<RdmaRxQueuePair> ();
  m_longThree = CreateObject<RdmaRxQueuePair> ();
  for (const auto &flow : {m_longOne, m_longTwo, m_longThree})
    {
      flow->m_guard_flow_size = 2000000;
      flow->m_base_rtt_sec = 0.000008;
    }
  GuardQpIdentity first = {1, 2, 10, 20, 3};
  GuardQpIdentity second = {3, 2, 11, 21, 3};
  GuardQpIdentity third = {4, 2, 12, 22, 3};
  m_inputs.push_back (
      {first, GUARD_VECTOR_INCUMBENT, 2000000, 0, 0, PeekPointer (m_longOne)});
  m_inputs.push_back (
      {second, GUARD_VECTOR_INCUMBENT, 1000000, 0, 0, PeekPointer (m_longTwo)});
  m_inputs.push_back (
      {third, GUARD_VECTOR_WAITER, 3000000, 0, 0, PeekPointer (m_longThree)});

  NS_TEST_ASSERT_MSG_EQ (
      m_allocator->ComputeGuardFrozenRequestedTargets (100000000000ULL,
                                                        100000000000ULL,
                                                        &m_inputs),
      true, "initial V22 vector must preserve the K=1 baseline");
  NS_TEST_ASSERT_MSG_EQ (m_inputs[1].requestedBps, 99800000000ULL,
                         "aging must not preempt before its threshold");
  NS_TEST_ASSERT_MSG_EQ (m_allocator->m_guardElephantAgingRotations, 0,
                         "initial deferral must not count as a rotation");
  Simulator::Schedule (NanoSeconds (16000),
                       &GuardElephantAgingTest::EvaluateAfterWait, this);
  Simulator::Run ();
  Simulator::Destroy ();
}

void
GuardElephantCapSpilloverTest::Evaluate (void)
{
  NS_TEST_ASSERT_MSG_EQ (
      m_allocator->ComputeGuardFrozenRequestedTargets (100000000000ULL,
                                                        100000000000ULL,
                                                        &m_inputs),
      true, "V23 spillover vector must remain capacity bounded");
  NS_TEST_ASSERT_MSG_EQ (m_inputs[0].requestedBps, 44000000000ULL,
                         "the selected elephant keeps 1.1 times its confirmed cap");
  NS_TEST_ASSERT_MSG_EQ (m_inputs[1].requestedBps, 55900000000ULL,
                         "only the donor's unused receiver share reaches rank two");
  NS_TEST_ASSERT_MSG_EQ (m_inputs[2].requestedBps, 100000000ULL,
                         "other deferred elephants retain the fixed floor");
  NS_TEST_ASSERT_MSG_EQ (
      m_inputs[0].requestedBps + m_inputs[1].requestedBps +
          m_inputs[2].requestedBps,
      100000000000ULL, "spillover must preserve the frozen receiver budget");
  NS_TEST_ASSERT_MSG_EQ (m_allocator->m_guardElephantSpilloverVectors, 1,
                         "one qualified allocation must count one spillover vector");
  NS_TEST_ASSERT_MSG_EQ (m_allocator->m_guardElephantSpilloverMaxBps,
                         55800000000ULL,
                         "the exact transferred rate must be auditable");

  m_donor->m_guard_spillover_report_fabric_bound = false;
  m_donor->m_guard_spillover_active = false;
  for (auto &input : m_inputs) input.requestedBps = 0;
  NS_TEST_ASSERT_MSG_EQ (
      m_allocator->ComputeGuardFrozenRequestedTargets (100000000000ULL,
                                                        100000000000ULL,
                                                        &m_inputs),
      true, "an unbound report must restore the K=1 baseline");
  NS_TEST_ASSERT_MSG_EQ (m_inputs[0].requestedBps, 99800000000ULL,
                         "V23 never replaces or permanently throttles the shortest flow");
  NS_TEST_ASSERT_MSG_EQ (m_inputs[1].requestedBps, 100000000ULL,
                         "rank two returns to the floor when the cap disappears");
}

void
GuardElephantCapSpilloverTest::DoRun (void)
{
  m_allocator = CreateObject<RdmaHw> ();
  m_allocator->m_mtu = 1000;
  m_allocator->m_minRate = DataRate (100000000ULL);
  m_allocator->m_guardRemainingAware = true;
  m_allocator->m_guardMinShareFraction = 0.0;
  m_allocator->m_guardRemainingExponent = 1.0;
  m_allocator->m_guardReceiverConcurrency = 1;
  m_allocator->m_guardAdaptiveElephantConcurrency = false;
  m_allocator->m_guardElephantAgingRtts = 0.0;
  m_allocator->m_guardElephantCapSpillover = true;
  m_allocator->m_guardConcurrencyMinBdps = 12.0;

  Ptr<RdmaRxQueuePair> second = CreateObject<RdmaRxQueuePair> ();
  Ptr<RdmaRxQueuePair> third = CreateObject<RdmaRxQueuePair> ();
  m_donor = CreateObject<RdmaRxQueuePair> ();
  for (const auto &flow : {m_donor, second, third})
    {
      flow->m_guard_flow_size = 2000000;
      flow->m_base_rtt_sec = 0.000008;
    }
  m_donor->m_guard_spillover_reported_cap_bps = 40000000000ULL;
  m_donor->m_guard_spillover_fabric_reports = 3;
  m_donor->m_guard_spillover_report_fabric_bound = true;
  m_donor->m_guard_spillover_active = true;
  m_donor->m_guard_spillover_last_report_time = NanoSeconds (1);
  GuardQpIdentity firstId = {1, 2, 10, 20, 3};
  GuardQpIdentity secondId = {3, 2, 11, 21, 3};
  GuardQpIdentity thirdId = {4, 2, 12, 22, 3};
  m_inputs.push_back (
      {firstId, GUARD_VECTOR_INCUMBENT, 1000000, 0, 0, PeekPointer (m_donor)});
  m_inputs.push_back (
      {secondId, GUARD_VECTOR_INCUMBENT, 2000000, 0, 0, PeekPointer (second)});
  m_inputs.push_back (
      {thirdId, GUARD_VECTOR_WAITER, 3000000, 0, 0, PeekPointer (third)});
  Simulator::Schedule (NanoSeconds (2),
                       &GuardElephantCapSpilloverTest::Evaluate, this);
  Simulator::Run ();
  Simulator::Destroy ();
}

void
GuardElephantFabricTargetTest::DoRun (void)
{
  double target = 0.0;
  bool eligible = false;
  const double base = 1.71;
  const double scale = 11.0 / 9.0;
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::ComputeGuardElephantFabricTarget (
          base, true, 1248001, 104000, 12.0, scale, &target, &eligible),
      true, "a valid V25 elephant target must be computable");
  NS_TEST_ASSERT_MSG_EQ (eligible, true,
                         "the size rule must be strict above twelve path BDPs");
  NS_TEST_ASSERT_MSG_EQ_TOL (target, 2.09, 1e-12,
                             "11/9 must map the lambda-1.8 target to lambda 2.2");
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::ComputeGuardElephantFabricTarget (
          base, true, 1248000, 104000, 12.0, scale, &target, &eligible),
      true, "the exact threshold must remain a valid non-elephant input");
  NS_TEST_ASSERT_MSG_EQ (eligible, false,
                         "the exact twelve-BDP boundary must not be relaxed");
  NS_TEST_ASSERT_MSG_EQ_TOL (target, base, 1e-12,
                             "shorter flows must preserve the baseline target");
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::ComputeGuardElephantFabricTarget (
          base, false, 8000000, 104000, 12.0, scale, &target, &eligible),
      true, "the default-off path must accept any flow size");
  NS_TEST_ASSERT_MSG_EQ (eligible, false,
                         "default-off must never classify a relaxed target");
  NS_TEST_ASSERT_MSG_EQ_TOL (target, base, 1e-12,
                             "default-off must be numerically identical");
}

void
GuardElephantReceiverAuthorityTest::DoRun (void)
{
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::IsGuardElephantReceiverAuthorityEligible (
          true, true, false, 1248001, 104000, 12.0,
          25000000000ULL, 100000000ULL),
      true, "a selected flow strictly above twelve path BDPs must qualify");
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::IsGuardElephantReceiverAuthorityEligible (
          true, true, false, 1248000, 104000, 12.0,
          25000000000ULL, 100000000ULL),
      false, "the exact twelve-BDP boundary must retain min-cap composition");
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::IsGuardElephantReceiverAuthorityEligible (
          true, false, false, 8000000, 104000, 12.0,
          25000000000ULL, 100000000ULL),
      false, "authority must not precede a real versioned receiver grant");
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::IsGuardElephantReceiverAuthorityEligible (
          true, true, false, 8000000, 104000, 12.0,
          100000000ULL, 100000000ULL),
      false, "a deferred minimum-rate elephant must retain its fabric cap");
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::IsGuardElephantReceiverAuthorityEligible (
          true, true, true, 8000000, 104000, 12.0,
          25000000000ULL, 100000000ULL),
      false, "tail bypass must remain a distinct policy path");
  NS_TEST_ASSERT_MSG_EQ (
      RdmaHw::IsGuardElephantReceiverAuthorityEligible (
          false, true, false, 8000000, 104000, 12.0,
          25000000000ULL, 100000000ULL),
      false, "default-off must preserve baseline min-cap composition");
}

void
GuardTerminalDrainCompletionTest::CompleteFirst (void)
{
  NS_TEST_ASSERT_MSG_EQ (
      m_allocator->ReleaseGuardDrainingOnCompletion (m_first), true,
      "the first same-tick contiguous completion must release one D");
  NS_TEST_ASSERT_MSG_EQ (m_allocator->m_guardDrainingRecords.size (), 1,
                         "the first completion must retain the second D");
  NS_TEST_ASSERT_MSG_EQ (m_allocator->m_guardFastpathMembershipRevision, 11,
                         "a remaining D must preserve its coordinator epoch");
  NS_TEST_ASSERT_MSG_EQ (m_allocator->m_guardProgressRevision, 13,
                         "a remaining D must preserve progress revision state");
  NS_TEST_ASSERT_MSG_EQ (m_allocator->CanResetGuardCoordinator (), false,
                         "one live D must continue to gate terminal reset");
  m_firstObserved = true;
}

void
GuardTerminalDrainCompletionTest::CompleteSecond (void)
{
  NS_TEST_ASSERT_MSG_EQ (m_firstObserved, true,
                         "same-tick completions must execute in event order");
  NS_TEST_ASSERT_MSG_EQ (
      m_allocator->ReleaseGuardDrainingOnCompletion (m_second), true,
      "the second same-tick contiguous completion must release the final D");
  NS_TEST_ASSERT_MSG_EQ (m_allocator->m_guardDrainingRecords.size (), 0,
                         "the second completion must clear terminal D");
  NS_TEST_ASSERT_MSG_EQ (m_allocator->m_guardFastpathMembershipRevision, 0,
                         "the final D must reset the preserved epoch once");
  NS_TEST_ASSERT_MSG_EQ (m_allocator->m_guardProgressRevision, 0,
                         "the final D must clear the preserved revision state");
  NS_TEST_ASSERT_MSG_EQ (m_allocator->m_guardDrainingCompletionReleases, 2,
                         "both same-tick D completions must be counted");
  NS_TEST_ASSERT_MSG_EQ (m_allocator->CanResetGuardCoordinator (), true,
                         "terminal state must be clean after the single reset");
  m_secondObserved = true;
}

void
GuardTerminalDrainCompletionTest::DoRun (void)
{
  m_allocator = CreateObject<RdmaHw> ();
  m_allocator->m_guardSmallSetFastpathLimit = 15;
  m_allocator->m_guardSerializedDraining = true;
  m_allocator->m_guardFastpathMembershipRevision = 11;
  m_allocator->m_guardFastpathConsumedMembershipRevision = 10;
  m_allocator->m_guardProgressRevision = 13;
  m_allocator->m_guardConsumedProgressRevision = 12;
  m_first = CreateObject<RdmaRxQueuePair> ();
  m_second = CreateObject<RdmaRxQueuePair> ();
  m_first->m_guard_flow_size = 10000;
  m_first->ReceiverNextExpectedSeq = 10000;
  m_second->m_guard_flow_size = 20000;
  m_second->ReceiverNextExpectedSeq = 20000;
  GuardQpIdentity firstIdentity = {1, 9, 101, 201, 3};
  GuardQpIdentity secondIdentity = {2, 9, 102, 202, 4};
  m_allocator->m_guardDrainingRecords.emplace (
      PeekPointer (m_first),
      GuardDrainingRecord {firstIdentity, m_first, 0, firstIdentity.pg,
                           40000000000ULL, 40000000000ULL,
                           40000000000ULL, 1, 0, 1, GUARD_DRAINING});
  m_allocator->m_guardDrainingRecords.emplace (
      PeekPointer (m_second),
      GuardDrainingRecord {secondIdentity, m_second, 0, secondIdentity.pg,
                           60000000000ULL, 60000000000ULL,
                           60000000000ULL, 1, 0, 1, GUARD_DRAINING});

  Simulator::ScheduleNow (&GuardTerminalDrainCompletionTest::CompleteFirst,
                          this);
  Simulator::ScheduleNow (&GuardTerminalDrainCompletionTest::CompleteSecond,
                          this);
  Simulator::Run ();
  NS_TEST_ASSERT_MSG_EQ (m_firstObserved, true,
                         "the first same-tick completion must run");
  NS_TEST_ASSERT_MSG_EQ (m_secondObserved, true,
                         "the second same-tick completion must run");
  Simulator::Destroy ();

  Ptr<RdmaHw> boundary = CreateObject<RdmaHw> ();
  boundary->m_guardSmallSetFastpathLimit = 15;
  boundary->m_guardSerializedDraining = true;
  boundary->m_guardFastpathMembershipRevision = 17;
  boundary->m_guardFastpathConsumedMembershipRevision = 16;
  boundary->m_guardProgressRevision = 19;
  boundary->m_guardConsumedProgressRevision = 18;
  Ptr<RdmaRxQueuePair> pending = CreateObject<RdmaRxQueuePair> ();
  pending->m_guard_flow_size = 30000;
  pending->ReceiverNextExpectedSeq = 30000;
  GuardQpIdentity pendingIdentity = {3, 9, 103, 203, 5};
  boundary->m_guardDrainingRecords.emplace (
      PeekPointer (pending),
      GuardDrainingRecord {pendingIdentity, pending, 0, pendingIdentity.pg,
                           100000000000ULL, 100000000000ULL,
                           100000000000ULL, 2, 0, 2, GUARD_DRAIN_PENDING});
  NS_TEST_ASSERT_MSG_EQ (boundary->CommitGuardPendingDrains (), 1,
                         "a completion-proven D_PENDING must close at boundary");
  NS_TEST_ASSERT_MSG_EQ (boundary->m_guardDrainingRecords.size (), 0,
                         "the boundary must release completion-proven D");
  NS_TEST_ASSERT_MSG_EQ (boundary->m_guardDrainingBoundaryCommits, 1,
                         "the D_PENDING boundary must be counted once");
  NS_TEST_ASSERT_MSG_EQ (boundary->m_guardDrainingCompletionReleases, 1,
                         "the boundary completion must release one reservation");
  NS_TEST_ASSERT_MSG_EQ (boundary->m_guardCompletionReleases, 0,
                         "the boundary must not double-count receive completion");
  boundary->SettleGuardActiveEmptyState ();
  NS_TEST_ASSERT_MSG_EQ (boundary->m_guardFastpathMembershipRevision, 0,
                         "empty boundary completion must reach terminal reset");

  Ptr<RdmaHw> capacity = CreateObject<RdmaHw> ();
  capacity->m_guardSmallSetFastpathLimit = 15;
  capacity->m_guardSerializedProgressRefresh = true;
  capacity->m_guardSerializedDraining = true;
  capacity->m_guardProgressTransactionActive = true;
  Ptr<RdmaRxQueuePair> survivor = CreateObject<RdmaRxQueuePair> ();
  capacity->m_rate_flow_ctl_set.emplace (PeekPointer (survivor));
  Ptr<RdmaRxQueuePair> released = CreateObject<RdmaRxQueuePair> ();
  Ptr<RdmaRxQueuePair> releasedSecond = CreateObject<RdmaRxQueuePair> ();
  released->m_guard_flow_size = 40000;
  released->ReceiverNextExpectedSeq = 40000;
  releasedSecond->m_guard_flow_size = 50000;
  releasedSecond->ReceiverNextExpectedSeq = 50000;
  GuardQpIdentity releasedIdentity = {4, 9, 104, 204, 6};
  GuardQpIdentity releasedSecondIdentity = {5, 9, 105, 205, 7};
  capacity->m_guardDrainingRecords.emplace (
      PeekPointer (released),
      GuardDrainingRecord {releasedIdentity, released, 0,
                           releasedIdentity.pg, 50000000000ULL,
                           50000000000ULL, 50000000000ULL, 3, 0, 3,
                           GUARD_DRAINING});
  capacity->m_guardDrainingRecords.emplace (
      PeekPointer (releasedSecond),
      GuardDrainingRecord {releasedSecondIdentity, releasedSecond, 0,
                           releasedSecondIdentity.pg, 25000000000ULL,
                           25000000000ULL, 25000000000ULL, 3, 0, 3,
                           GUARD_DRAINING});
  NS_TEST_ASSERT_MSG_EQ (
      capacity->ReleaseGuardDrainingOnCompletion (released), true,
      "a D completion with ACTIVE capacity must release its reservation");
  NS_TEST_ASSERT_MSG_EQ (capacity->m_guardCapacityDirty, true,
                         "released D capacity must queue a revector");
  NS_TEST_ASSERT_MSG_EQ (capacity->m_guardProgressRevision, 1,
                         "released D capacity must advance allocation input");
  NS_TEST_ASSERT_MSG_EQ (capacity->m_guardProgressEventsCoalesced, 1,
                         "a live refresh must serialize the capacity revector");
  NS_TEST_ASSERT_MSG_EQ (
      capacity->ReleaseGuardDrainingOnCompletion (releasedSecond), true,
      "a second D completion must join the serialized capacity revector");
  NS_TEST_ASSERT_MSG_EQ (capacity->m_guardCapacityDirty, true,
                         "coalesced capacity releases must remain dirty");
  NS_TEST_ASSERT_MSG_EQ (capacity->m_guardProgressRevision, 2,
                         "each capacity release must advance the revision");
  NS_TEST_ASSERT_MSG_EQ (capacity->m_guardProgressEventsCoalesced, 2,
                         "both busy capacity releases must be coalesced");
}

void
GuardActiveEmptyTransactionTest::RunPhase (uint32_t phase)
{
  Ptr<RdmaHw> hw = CreateObject<RdmaHw> ();
  hw->m_guardSmallSetFastpathLimit = 4;
  hw->m_guardSerializedDraining = true;
  hw->m_guardMixedPgVectorFastpath = true;
  hw->m_guardFastpathPhase =
      static_cast<RdmaHw::GuardFastpathPhase> (phase);
  hw->m_guardFrozenVectorActive = true;
  hw->m_guardFastpathMembershipRevision = 1;
  hw->m_guardFastpathConsumedMembershipRevision = 1;
  Ptr<RdmaRxQueuePair> flow = CreateObject<RdmaRxQueuePair> ();
  hw->m_rate_flow_ctl_set.emplace (PeekPointer (flow));
  hw->m_guardFastpathIncumbents.emplace (PeekPointer (flow));

  NS_TEST_ASSERT_MSG_EQ (
      hw->HandleRccRemove (flow, RdmaHw::GUARD_RELEASE_COMPLETION, 0), true,
      "the last ACTIVE completion must close its current generation");
  NS_TEST_ASSERT_MSG_EQ (hw->m_rate_flow_ctl_set.empty (), true,
                         "terminal completion must empty ACTIVE");
  NS_TEST_ASSERT_MSG_EQ (hw->m_guardFastpathPhase,
                         RdmaHw::GUARD_FASTPATH_IDLE,
                         "terminal completion must close the live phase");
  NS_TEST_ASSERT_MSG_EQ (hw->m_guardFrozenVectorActive, false,
                         "terminal completion must retire its frozen vector");
  NS_TEST_ASSERT_MSG_EQ (hw->CanResetGuardCoordinator (), true,
                         "terminal transaction must settle exactly once");
}

void
GuardActiveEmptyTransactionTest::DoRun (void)
{
  RunPhase (RdmaHw::GUARD_FASTPATH_PREPARE);
  RunPhase (RdmaHw::GUARD_FASTPATH_ACTIVATE);
}

GuardTransitionAuditSinkTest::GuardTransitionAuditSinkTest ()
  : TestCase ("GUARD V14 bounded per-transition audit sink")
{
}

void
GuardTransitionAuditSinkTest::Emit (Ptr<RdmaHw> hw,
                                    GuardTransitionAuditSink *sink,
                                    uint64_t epoch, uint64_t transaction)
{
  GuardTransitionAuditRecord record;
  record.active = true;
  record.receiverNode = 7;
  record.receiverNic = 1;
  record.epoch = epoch;
  record.transaction = transaction;
  record.priorityGroupSetHash = 11;
  record.targetVectorHash = 13;
  record.targetVectorEntries = 5;
  record.receiverCapacityBps = 100000000000ULL;
  record.activeUpperBoundBps = 25000000000ULL;
  record.drainingUpperBoundBps = 400000000000ULL;
  record.activeDrainingUpperBoundBps = 425000000000ULL;
  record.drainingWireUpperBoundBytes = 452000;
  record.prefixTargetBytes = 416000;
  record.prefixObservedBytes = 416000;
  record.startNs = 100;
  record.deadlineNs = 200;
  record.deadlinePolicy = "wire_residual_watchdog";
  record.deadlineOutcome = "ready";
  hw->m_guardTransitionAuditRecord = record;
  sink->records++;
  hw->FinalizeGuardTransitionAudit ("activation_ack_closed");
}

void
GuardTransitionAuditSinkTest::DoRun (void)
{
  GuardTransitionAuditSink sink;
  sink.file = tmpfile ();
  NS_TEST_ASSERT_MSG_NE (sink.file, 0, "temporary audit sink must open");
  sink.max_records = 2;
  Ptr<RdmaHw> first = CreateObject<RdmaHw> ();
  Ptr<RdmaHw> second = CreateObject<RdmaHw> ();
  first->ConfigureGuardTransitionAudit (&sink);
  second->ConfigureGuardTransitionAudit (&sink);

  Emit (first, &sink, 1, 10);
  Emit (first, &sink, 2, 11);
  Emit (second, &sink, 1, 20);
  NS_TEST_ASSERT_MSG_EQ (sink.records, 3,
                         "two transitions and two hardware instances must be counted");
  NS_TEST_ASSERT_MSG_EQ (sink.attempted, 3,
                         "every terminal transition must attempt one record");
  NS_TEST_ASSERT_MSG_EQ (sink.written, 2,
                         "the hard record cap must truncate the third record");
  NS_TEST_ASSERT_MSG_EQ (first->m_guardTransitionAuditRecord.active, false,
                         "first hardware must close its terminal state");
  NS_TEST_ASSERT_MSG_EQ (second->m_guardTransitionAuditRecord.active, false,
                         "second hardware must close its terminal state");

  rewind (sink.file);
  uint64_t rows = 0;
  char line[1024];
  while (fgets (line, sizeof (line), sink.file) != 0)
    {
      rows++;
    }
  NS_TEST_ASSERT_MSG_EQ (rows, 2,
                         "the bounded sink must physically write only capped rows");
  fclose (sink.file);
  sink.file = 0;

  GuardTransitionAuditSink terminalSink;
  terminalSink.file = tmpfile ();
  NS_TEST_ASSERT_MSG_NE (terminalSink.file, 0,
                         "terminal audit sink must open");
  terminalSink.max_records = 1;
  first->ConfigureGuardTransitionAudit (&terminalSink);
  GuardTransitionAuditRecord readyRecord;
  readyRecord.active = true;
  readyRecord.deadlineOutcome = "ready";
  first->m_guardTransitionAuditRecord = readyRecord;
  terminalSink.records++;
  first->FlushGuardTransitionAudit ();
  rewind (terminalSink.file);
  NS_TEST_ASSERT_MSG_NE (fgets (line, sizeof (line), terminalSink.file), 0,
                         "terminal flush must write the resolved record");
  std::string terminalRow (line);
  NS_TEST_ASSERT_MSG_NE (
    terminalRow.find (",ready,simulation_stop\n"), std::string::npos,
    "simulation stop must not overwrite an already resolved deadline outcome");
  fclose (terminalSink.file);
  terminalSink.file = 0;
}

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
    n8Waiters, n8Incumbents, {}, 1000, dataHeaderBytes,
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
    boundaries, {}, {}, 1000, dataHeaderBytes, 100000000000ULL, &budget);
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
    oneWaiter, incumbents, {}, 1000, dataHeaderBytes,
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
  std::vector<GuardTransitionPrefixDrainingBudgetInput> draining = {
    {10000000000ULL, 10000}
  };
  valid = RdmaHw::ComputeGuardTransitionPrefixWireBudget (
    oneWaiter, incumbents, draining, 1000, dataHeaderBytes,
    100000000000ULL, &budget);
  NS_TEST_ASSERT_MSG_EQ (valid, true,
                         "watchdog must accept a bounded draining reservation");
  NS_TEST_ASSERT_MSG_EQ (budget.drainingReservedBps, 10000000000ULL,
                         "watchdog occupancy must include draining D");
  NS_TEST_ASSERT_MSG_EQ (budget.residualBps, 65000000000ULL,
                         "watchdog must serialize at C-active-D");
  NS_TEST_ASSERT_MSG_EQ (budget.maxRttNs, 10000,
                         "watchdog RTT must include draining records");

  valid = RdmaHw::ComputeGuardTransitionPrefixWireBudget (
    oneWaiter, {{100000000000ULL, 8000}}, {}, 1000, dataHeaderBytes,
    100000000000ULL, &budget);
  NS_TEST_ASSERT_MSG_EQ (valid, false,
                         "zero residual capacity must fail closed");
  valid = RdmaHw::ComputeGuardTransitionPrefixWireBudget (
    {{18446744073709551615ULL, 18446744073709551615ULL, 8000}}, {},
    {}, 2, dataHeaderBytes, 100000000000ULL, &budget);
  NS_TEST_ASSERT_MSG_EQ (valid, false,
                         "wire-byte overflow must fail closed");
  valid = RdmaHw::ComputeGuardTransitionPrefixWireBudget (
    oneWaiter,
    {{18446744073709551615ULL, 8000}, {1, 8000}},
    {}, 1000, dataHeaderBytes, 18446744073709551615ULL, &budget);
  NS_TEST_ASSERT_MSG_EQ (valid, false,
                         "incumbent occupancy overflow must fail closed");
  valid = RdmaHw::ComputeGuardTransitionPrefixWireBudget (
    {{2000000000ULL, 2000000000ULL, 8000}}, {},
    {}, 1000, dataHeaderBytes, 1, &budget);
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

GuardInitialWindowPriorityEncodingTest::GuardInitialWindowPriorityEncodingTest ()
  : TestCase ("GUARD initial-window scheduling class keeps immutable PG")
{
}

void
GuardInitialWindowPriorityEncodingTest::DoRun (void)
{
  Ptr<Packet> packet = Create<Packet> (1000);
  SeqTsHeader seq;
  seq.SetSeq (0);
  seq.SetPG (7);
  packet->AddHeader (seq);
  UdpHeader udp;
  udp.SetSourcePort (10000);
  udp.SetDestinationPort (100);
  packet->AddHeader (udp);
  Ipv4Header ip;
  ip.SetSource (Ipv4Address ("11.0.0.1"));
  ip.SetDestination (Ipv4Address ("11.0.0.2"));
  ip.SetProtocol (0x11);
  ip.SetPayloadSize (packet->GetSize ());
  ip.SetTos (CustomHeader::EncodeGuardScheduleClass (3, CustomHeader::ECN_ECT0));
  packet->AddHeader (ip);
  PppHeader ppp;
  ppp.SetProtocol (0x0021);
  packet->AddHeader (ppp);

  CustomHeader parsed (CustomHeader::L2_Header | CustomHeader::L3_Header |
                       CustomHeader::L4_Header);
  packet->PeekHeader (parsed);
  NS_TEST_ASSERT_MSG_EQ (parsed.udp.pg, 7,
                         "the QP and PFC priority group must remain immutable");
  NS_TEST_ASSERT_MSG_EQ (parsed.HasGuardScheduleClass (), true,
                         "the DSCP scheduling marker must survive serialization");
  NS_TEST_ASSERT_MSG_EQ (parsed.GetGuardScheduleClass (), 3,
                         "the initial window must select the unscheduled queue");
  NS_TEST_ASSERT_MSG_EQ (parsed.GetIpv4EcnBits (), CustomHeader::ECN_ECT0,
                         "the scheduling marker must preserve ECN bits");

  uint8_t steady = CustomHeader::EncodeGuardScheduleClass (7, CustomHeader::ECN_CE);
  uint8_t steadyPriority =
      (steady & CustomHeader::GUARD_SCHEDULE_CLASS_MASK) >> 2;
  uint8_t steadyEcn = steady & 0x3;
  NS_TEST_ASSERT_MSG_EQ (steadyPriority, 7,
                         "the steady packet must return monotonically to its static class");
  NS_TEST_ASSERT_MSG_EQ (steadyEcn, CustomHeader::ECN_CE,
                         "steady-class encoding must preserve CE");
}

GuardTransportWindowFloorTest::GuardTransportWindowFloorTest ()
  : TestCase ("GUARD transport window floor preserves exact path BDP")
{
}

void
GuardTransportWindowFloorTest::DoRun (void)
{
  Ptr<RdmaQueuePair> qp = CreateObject<RdmaQueuePair> (
      7, Ipv4Address ("11.0.0.1"), Ipv4Address ("11.0.0.2"), 10000, 100);
  qp->SetWin (52000);
  qp->m_guard_transport_win = 104000;
  qp->m_guard_first_grant_win = 104000;
  qp->m_guard_wait_first_grant = true;
  qp->snd_nxt = 52000;
  NS_TEST_ASSERT_MSG_EQ (qp->m_win, 52000,
                         "the exact path BDP must remain available to policy");
  NS_TEST_ASSERT_MSG_EQ (qp->GetGuardTransportWin (), 104000,
                         "the transport window must use the independent floor");
  NS_TEST_ASSERT_MSG_EQ (qp->IsWinBound (), false,
                         "one local-path BDP must not close the raised first gate");
  qp->snd_nxt = 104000;
  NS_TEST_ASSERT_MSG_EQ (qp->IsWinBound (), true,
                         "the raised first gate must stop at the diameter window");
  qp->m_guard_wait_first_grant = false;
  qp->snd_una = 1000;
  NS_TEST_ASSERT_MSG_EQ (qp->GetWin (), 104000,
                         "steady fixed-window credit must use the same floor");
  qp->m_guard_wait_first_grant = true;
  qp->m_guard_first_grant_win = 52000;
  qp->snd_nxt = 52000;
  NS_TEST_ASSERT_MSG_EQ (qp->IsWinBound (), true,
                         "post-grant-only mode must retain the exact first gate");
  qp->m_guard_wait_first_grant = false;
  NS_TEST_ASSERT_MSG_EQ (qp->GetWin (), 104000,
                         "post-grant-only mode must retain the steady floor");
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
  ack.SetPhaseTag (3);
  NS_TEST_ASSERT_MSG_EQ (ack.GetPhaseTag (), 3,
                         "V14 grant acknowledgement must expose its wire phase");
  NS_TEST_ASSERT_MSG_EQ (ack.GetSerializedSize (), 11,
                         "phased grant acknowledgement must serialize to eleven bytes");

  GuardGrantAckHeader legacyAck;
  NS_TEST_ASSERT_MSG_EQ (legacyAck.GetPhaseTag (), 0,
                         "default ACK phase must preserve legacy semantics");

  Ptr<Packet> ackPacket = Create<Packet> (
      std::max (60 - 14 - 20 - static_cast<int> (ack.GetSerializedSize ()), 0));
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
  NS_TEST_ASSERT_MSG_EQ (parsedAck.grantAck.sport, 4321,
                         "grant acknowledgement must preserve source port");
  NS_TEST_ASSERT_MSG_EQ (parsedAck.grantAck.dport, 1234,
                         "grant acknowledgement must preserve destination port");
  NS_TEST_ASSERT_MSG_EQ (parsedAck.grantAck.pg, 4,
                         "grant acknowledgement must preserve priority group");
  NS_TEST_ASSERT_MSG_EQ (parsedAck.grantAck.generation, 17,
                         "grant acknowledgement must preserve generation");
  NS_TEST_ASSERT_MSG_EQ (parsedAck.grantAck.phaseTag, 3,
                         "grant acknowledgement must preserve its V14 phase");
  NS_TEST_ASSERT_MSG_EQ (parsedAck.GetSerializedSize (), 45,
                         "parsed L2, IPv4, and acknowledgement headers must total 45 bytes");
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
  AddTestCase (new GuardInitialWindowPriorityEncodingTest);
  AddTestCase (new GuardTransportWindowFloorTest);
  AddTestCase (new GuardGrantHeaderTest);
  AddTestCase (new GuardFirstGrantGateTagTest);
  AddTestCase (new GuardDeadlineSameTickTest);
  AddTestCase (new GuardTransitionPrefixWireBudgetTest);
  AddTestCase (new GuardTransitionAuditSinkTest);
  AddTestCase (new GuardMixedPgVectorTest);
  AddTestCase (new GuardElephantAgingTest);
  AddTestCase (new GuardElephantCapSpilloverTest);
  AddTestCase (new GuardElephantFabricTargetTest);
  AddTestCase (new GuardElephantReceiverAuthorityTest);
  AddTestCase (new GuardTerminalDrainCompletionTest);
  AddTestCase (new GuardActiveEmptyTransactionTest);
  AddTestCase (new GuardGrantTraceProvenanceTest);
  AddTestCase (new GuardFrozenCohortProvenanceTest);
  AddTestCase (new GuardRetiredAckIdentityTest);
  AddTestCase (new GuardRetiredAckReceiveTest);
}

static PointToPointTestSuite g_pointToPointTestSuite;

} // namespace ns3
