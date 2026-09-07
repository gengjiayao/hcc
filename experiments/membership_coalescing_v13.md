# Membership-coalescing V13 wire-watchdog holdout

V13 is an armed, preregistered repair to the V12 nominal prefix deadline. It
does not change V12's exact-prefix ready predicate, frozen waiter cohort,
serialized V11 prepare/activate transactions, membership policy, or fallback
implementation. It changes only the timeout budget. V13 remains
default-disabled and outside final GUARD unless the complete frozen campaign
passes.

## Frozen mechanism

The candidate uses both opt-in switches:

```text
--guard_transition_prefix_barrier 1
--guard_transition_prefix_wire_watchdog 1
```

The paired control sets both switches and the small-set limit to zero. The V13
candidate retains `K=4`, the 12,480-ns post-initial membership window, the
16,640-ns initial quiet window, the 83,200-ns hard horizon, fixed sender
windows, PFC on, IRN off, and every V12 isolation setting.

The frozen V13 control manifest always records the explicit watchdog CLI value
zero. To preserve the byte-identical default-off V12 config shape, `run.py`
may omit `GUARD_TRANSITION_PREFIX_WIRE_WATCHDOG` from `config.txt` only for
that control arm. An explicit config value must still equal zero; the candidate
must contain the key with value one.

After all required transition-prepare ACKs close, V13 independently budgets
each frozen waiter `f` from its raw trace target and lifecycle size:

```text
T[f]       = min(flow_size[f], exact_first_grant_gate[f])
packets[f] = ceil(T[f] / MTU)
payload[f] = min(flow_size[f], packets[f] * MTU)
wire[f]    = payload[f] + packets[f] * data_header_bytes
W          = sum(wire[f])
```

The frozen MTU is 1,000 bytes. The true CC11 DATA header is 90 bytes from
`CustomHeader::GetStaticWholeHeaderSize()`. The analyzer recomputes packet
rounding, including a final partial-flow packet, rather than trusting the
watchdog aggregate.

For every incumbent, the raw required `transition_prepare sent` row supplies
the upper-bound rate whose matching required ACK was already closed by the
V11 phase audit. With receiver capacity `C`, acknowledged incumbent occupancy
`O`, and residual service `R`, the bound is:

```text
O                = sum(acknowledged incumbent upper-bound rates)
R                = C - O
serialization_ns = ceil(W * 8e9 / R)
delay_ns         = max_exact_base_RTT_ns + serialization_ns
deadline_ns      = start_ns + delay_ns
```

The analyzer reconstructs each base RTT from the exact topology file at the
sealed simulator Git SHA, using the simulator's propagation and one-way MTU
serialization rule. It does not accept the reported maximum as independent
evidence. Observed prefix progress is not subtracted from `W`. Normal DATA
progress can still satisfy readiness and cancel the timer immediately.

The stats row can be interpreted as one budget only when all three provenance
conditions hold:

```text
records = 1
non_reconstructable = 0
inconsistent = 0
```

A second transition, multiple contributing receiver HWs, mixed scalar fields,
or live terminal budget fails closed. The nominal watchdog remains a liveness
bound, not a universal queue-empty proof; timeout, degraded operation,
fallback, PFC, recovery, or drop still rejects the campaign.

## Frozen identities and stage chain

Candidate-only replay reuses the four V11 seed-126 inputs:

| N | Required flow SHA-256 |
|---:|---|
| 2 | `55cb59f42c008a37d0e24555fbdc33f1bec478e2816e2ba3f79eccdde9e971a2` |
| 4 | `87a41f2df3f9a2e8d70498c0db29764ddc7fb53a96f38ae13022d6c627f2949b` |
| 8 | `4d98144057c964d54299c5005883de6e13d01bb3491602b6a0b13d87e4b496f4` |
| 15 | `430550e35b0d5c53c07ac1da5a408a6b31c24b71e257b9cdffcfbc252c67437f` |

The fresh mechanism-only pilot is seed 137 at N in {4,15}, paired control and
V13, for four runs. Formal evaluation uses fresh seeds 138--142 at N in
{2,4,8,15}, paired control and V13, for exactly 40 runs. The only permitted
order is:

```text
preflight -> replay -> seal replay -> pilot -> seal pilot
          -> formal -> validate all 40 mechanisms
          -> unseal performance -> aggregate all five paired seeds
```

The runner never opens FCT or queue artifacts. Replay and pilot analyzers are
mechanism-only. The formal analyzer opens performance only after all 40
identities pass; no failed seed, N, or arm may be omitted.

## Mechanism admission

All runs retain the complete V11 safety audit, completion-only lifecycle
closure, retry-inclusive frame accounting, and zero drop, recovery, timeout
recovery, PFC, retry, stale, rejected-generation, pending, and terminal-state
requirements.

At N in {2,4}, prefix and watchdog activity must both remain zero. Frame caps
are 7 and 26. At N in {8,15}, exactly one prefix barrier starts, becomes ready,
and produces one required normal activation generation. Every raw waiter row
must satisfy `prefix_observed_bytes >= prefix_target_bytes`; ready waiters must
equal required waiters; remaining bytes, timeout, degraded, fallback, retry,
stale, PFC, recovery, drop, barrier, order, and terminal counters must be zero.
Frame caps are 43 and 64.

Every high-N run must have one reconstructable watchdog record. The analyzer
recomputes rounded payload, packet count, wire bytes, incumbent count and
occupancy, capacity, residual service, serialization, maximum RTT, delay, and
absolute deadline from raw flow, lifecycle, phase, and frozen-topology
evidence. For the known N=8 replay, the exact required reconstruction is:

```text
rounded_payload_budget_bytes = 728000
packet_count                  = 728
header_per_packet             = 90
wire_bytes                    = 793520
occupancy_bps                 = 12500000000
capacity_bps                  = 100000000000
residual_bps                  = 87500000000
serialization_ns              = 72551
max_rtt_ns                    = 8320
delay_ns                      = 80871
```

The known N=15 replay must also close through normal `ready`, never fallback.

## Performance gates

The gates are unchanged from V12. For every N, five paired seeds produce a
Student-t 95% interval. Mean-FCT and completion-span percentage CI upper
bounds must be at most +1%; the Jain-index difference CI lower bound must be
at least -0.005.

At N in {2,4,8}, candidate-minus-control queue-mean and queue-p99 absolute-byte
CI upper bounds must each be at most 60 bytes. At N=15, queue-mean percentage
CI upper must be at most -10% and queue-p99 at most -20%. Every N=15 candidate
has zero PFC, at most 64 control frames, and candidate/control frame ratio at
most 0.30. Each fresh N=15 seed individually must improve queue mean by at
least 10% and queue p99 by at least 20%. Every gate is conjunctive.

## Bounded serial execution

All 48 simulator identities run serially. The 26 unique traffic inputs share
paired pilot/formal files. Limits are 1,000 flows, 4,096 grant-trace rows, 32
lifecycle rows, 16 MiB per run, 1,800 seconds and 8 GiB RSS per process, and 1
GiB for the campaign. The minimum simulator revision is
`b5c3e51e5ee225d52872e63711699e670e688519`.

```bash
SPEC=experiments/membership_coalescing_ladder_v13.json
CAMPAIGN=/absolute/path/to/v13-campaign

python3 experiments/run_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --phase preflight
python3 experiments/run_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --phase replay
python3 experiments/analyze_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --scope replay \
  --json-out "$CAMPAIGN/known_replay_admission_v13.json"
python3 experiments/run_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --phase seal-replay \
  --replay-admission "$CAMPAIGN/known_replay_admission_v13.json"

python3 experiments/run_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --phase pilot
python3 experiments/analyze_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --scope pilot \
  --json-out "$CAMPAIGN/pilot_admission_v13.json"
python3 experiments/run_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --phase seal-pilot \
  --pilot-admission "$CAMPAIGN/pilot_admission_v13.json"

python3 experiments/run_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --phase run
python3 experiments/analyze_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --scope holdout \
  --json-out "$CAMPAIGN/holdout_analysis_v13.json"
python3 experiments/aggregate_membership_coalescing_holdout.py "$SPEC" \
  "$CAMPAIGN/holdout_analysis_v13.json" \
  --output-dir "$CAMPAIGN/summary"
```

Any failed replay, pilot, formal mechanism, aggregate CI, or per-seed gate
rejects this frozen V13 campaign. It does not authorize changing a seed,
parameter, threshold, workload, or subset.

## Frozen campaign result

The campaign was executed serially at simulator revision
`e691cac48d8b5c42decf68c34bd549e1743d51b5`. All four replay runs, four pilot
runs, and 40 formal runs passed their mechanism gates. Replay and pilot
admission kept performance sealed; the formal analyzer opened FCT and queue
artifacts only after validating all 40 formal mechanisms.

The paired five-seed result is `admitted` for the frozen receiver-share scope.
The table reports candidate-minus-immediate-control effects and the relevant
Student-t 95% interval bound. A negative queue value is an improvement.

| N | Mean FCT, mean (CI upper) | Span, mean (CI upper) | Queue result | Candidate/control frames |
|---:|---:|---:|---:|---:|
| 2 | -0.259% (+0.022%) | -0.213% (+0.055%) | mean -1.23 B; p99 0 B | 7/4 |
| 4 | -0.496% (+0.828%) | -0.265% (+0.002%) | mean -0.43 B; p99 0 B | 16/16 |
| 8 | +0.456% (+0.881%) | -1.049% (-0.671%) | mean +1.20 B; p99 0 B | 22--25/64 |
| 15 | +0.332% (+0.334%) | +0.402% (+0.402%) | mean -98.765% (upper -98.692%); p99 -99.325% (upper -99.253%) | 38/225 |

At N=15, every individual seed reduces mean queue by 98.68--98.84% and p99
queue by 99.22--99.35%. The immediate control records 570--617 PFC pause
events per seed, whereas V13 records none. V13 uses 38 control frames instead
of 225, a ratio of 0.1689. All candidate runs complete without switch drops,
recovery, timeout recovery, prefix timeout, degraded fallback, grant retry,
stale generation, trace truncation, or terminal state.

The 13 high-N watchdog records across replay, pilot, and formal runs are each
independently reconstructable. N=8 yields 793,520 wire bytes, 87.5 Gb/s
residual service, 72,551 ns serialization, and an 80,871 ns deadline offset.
N=15 yields 1,246,960 wire bytes, 93.334 Gb/s residual service, 106,882 ns
serialization, and a 115,202 ns deadline offset. Every record has one source,
zero inconsistency, and zero terminal budget.

### Result provenance

| Artifact | SHA-256 |
|---|---|
| Frozen specification | `ceeec72905ec02d0a7c870c8cbc6a0920553472cc785f230e0355bb7920f7ed0` |
| Preflight manifest | `4ac40cdcf4ffa37e3161d612b1efb836ebf1fffbb2a319f38bc2b387ea521be0` |
| Campaign manifest | `661abf87dfc8026653e03f4125a7e52e076a429f0ea43545867d0715e8454612` |
| Replay admission | `6ce21b4220fccb91ccdbecd12fed11091e2d049989d76cdc7ed07f39524187c3` |
| Pilot admission | `0dda6205a84c97c73cee8f4c5b4d79dca93d85013e14863bb135df5ca26298e4` |
| Formal analysis | `1a0558a59969c1bc5b3530c901b302e120f131283d6898eefae80b27d0af37db` |
| Aggregate admission | `eca00366a68385af6d0c863f2f64dec423fc76ba96dac9aa7f44c85ec02dd3b8` |
| Per-seed CSV | `18cd6b0a086efbc01d2deb22706f3406eaaf2e8e29c619c8a8a3e03b21efd22b` |
| Paired t95 CSV | `38889298c7730971461b201d81a88f00d6bf33576c0b31e4eb1a5dfed73d5067` |

An independent raw-artifact audit re-ran the analyzer, reconstructed all 13
watchdog budgets and all paired intervals, and found zero Critical, Major, or
Minor discrepancies. The 48 run outputs occupy 1.57 MB; the campaign metadata
occupies 1.20 MB, and the largest run is 80,128 bytes.

This admission is deliberately narrow. It establishes V13 for synchronized,
same-receiver, same-priority, equal-size receiver-share traffic through N=15.
It does not establish final-GUARD or Homa superiority, nor does it authorize
enabling V13 with proactive release, remaining-aware allocation, size
priority, sender SRPT, or multi-receiver workloads without new compatibility
validation.
