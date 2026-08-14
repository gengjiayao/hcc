# GUARD work-conserving receiver grants

## Problem and algorithm

The original receiver loop assigned every active registered flow `C/N`.  This
is safe, but it is not work conserving when one sender cannot consume its
share: another ready sender is not allowed to use the idle capacity.

`--guard_work_conserving=1` enables demand-aware share reclamation.  Every
200 us the receiver samples delivered bytes without adding packet-level logs.
It considers reclamation only after aggregate receive goodput is below 75% of
the line rate for three consecutive samples.  Within the same samples, a flow
is marked demand-limited only after receiving below 75% of its grant three
times.  A max-min water fill caps that flow at its measured rate divided by
0.75 and divides the residual capacity among flows with unsatisfied demand.
All grants are updated atomically.  Membership changes reset the evidence and
restore equal `C/N`, and the sender still paces at
`min(HPCC rate, receiver grant)`.

The receiver-utilization gate is important.  It prevents ordinary fabric
contention from being mistaken for unused receiver capacity.  The feature is
configured by:

```text
--guard_work_conserving 0|1
--guard_rebalance_interval_us 200
--guard_demand_threshold 0.75
--guard_receiver_util_threshold 0.75
```

Simulator commit for the results below: `34279a5`.  Parser compatibility was
added in `4a55e3e`.  All cells used 16 hosts, OS4, 100 Gb/s, PFC on, IRN off,
bulk monitoring, lambda 1, beta 0.125, gamma 1, no size priority, and five
pre-generated paired seeds.  Fixed and adaptive cells for a seed have an
identical private flow-snapshot SHA-256.  No cell had a switch drop, PFC event,
recovery event, incomplete flow, or oversized artifact.

## Frozen mechanism and safety qualification

The directed heterogeneous trace has two 16 MiB flows to receiver 15.  Source
0 is independently constrained by three same-leaf background flows, while
source 1 is unconstrained.  This endpoint construction was frozen before the
adaptive result was inspected.  The homogeneous safety trace has eight 8 MiB
flows to one receiver.

| Qualification | Paired adaptive minus fixed result (five seeds, t95) | Interpretation |
|---|---:|---|
| Heterogeneous, unconstrained-flow FCT | -7.851% [-7.855%, -7.847%] | Idle receiver share is reclaimed |
| Heterogeneous, constrained-flow FCT | 0.000% [0.000%, 0.000%] | Reclamation does not penalize its measured demand |
| Heterogeneous, all-flow mean FCT | -0.873% [-0.873%, -0.873%] | Background flows remain unchanged |
| Homogeneous N=8, mean FCT | 0.000% [-0.000%, 0.000%] | Gate suppresses false reallocation |
| Homogeneous N=8, P99 FCT | 0.000% [0.000%, 0.000%] | Exact per-flow safety equivalence |

Every heterogeneous adaptive run emitted 12 adaptive grant packets; every
homogeneous run emitted zero.  The five paired run IDs are:

```text
heterogeneous fixed/adaptive:
840492842/87496568  894221744/756822183  289561825/944914726
476821474/974712458  596244716/505968211

homogeneous N=8 fixed/adaptive:
936751486/113940122  692160103/989163239  247975163/980550639
579987111/826083992  969134339/422662460
```

Flow SHA-256 values, seeds 1--5:

```text
heterogeneous:
12e2bb4e582103136b585a3f4d8d136c2129393811ac0c9e0c3afda34d56cc38
b926e7eb30c0b82b9b42dd076681bcfd1e6c95067035a13c2176733ebd2bf1ae
cef3ac803799e236ef0a7658505cdd3e655966fb55a9440d2697ccd7115bdf42
1de294d272cb79a8a27fc8d2d97cc9aca42d912379f3ef52bb0818c9c3dfd9a0
12406d410130b9f629535d09ee12886ff8dae77d6651efa5b970fc603e1bc332

homogeneous N=8:
14330dd406ad41ec98f24d1c2ecd2270b12be39037ddd137de0e8f2c55f306be
48c6630dd942fc975eb692f0b5b6988aff67e670d4c14c99320474af3f0f07b3
808cb771a2f7c4c3dbbaba95eecf4e3bc45c06d55426d2835c89d971e8415088
7836b3aee4313707f887597e16c6abdfa582c50db0fc172c7d5b34e2a90c2741
056ad24f9248db318074035c0f6178cc4a979d69e00d078edc556f971c3385f9
```

## General-workload safety gate

The same frozen AliStorage2019, WebSearch, and FbHdp traces used by the general
workload study were rerun as fixed/adaptive pairs.  AliStorage never triggered
adaptive grants.  WebSearch triggered in three seeds (4, 6, and 4 packets), and
FbHdp triggered in two seeds (2 packets each).  No reported FCT, slowdown, or
queue difference had a t95 interval excluding zero:

| Workload | Overall mean FCT | Overall P99 FCT | Mean queue | P99 queue |
|---|---:|---:|---:|---:|
| AliStorage2019 | +0.0005% [-0.0033%, +0.0044%] | 0.000% | +0.004% [-0.048%, +0.057%] | -0.050% [-0.165%, +0.064%] |
| WebSearch | +0.015% [-0.278%, +0.307%] | -0.744% [-2.817%, +1.329%] | +0.326% [-1.675%, +2.327%] | -2.857% [-10.790%, +5.076%] |
| FbHdp | -0.032% [-0.446%, +0.382%] | -0.015% [-0.052%, +0.021%] | +0.154% [-0.317%, +0.624%] | +0.597% [-0.561%, +1.755%] |

Run IDs (fixed/adaptive, seed order 1--5):

```text
AliStorage2019:
817086857/801751288  636760825/781258696  393343422/670140375
742006671/51098981  118729696/569983781

WebSearch:
940433246/594657973  232160187/704709950  143511949/341038918
279905649/770968985  361079116/724400886

FbHdp:
352289632/427215629  995885173/862660178  792783027/626522319
572194776/648144975  429905726/118667305
```

This qualification supports the narrow claim that GUARD now reclaims receiver
capacity in the diagnosed heterogeneous case without a measurable regression
in these safety workloads.  It does **not** support a claim that adaptive
grants improve every workload or every metric.
