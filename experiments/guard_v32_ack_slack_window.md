# V32 ACK-slack window revalidation

V32 revalidates the unchanged V31 path-adaptive window after repairing the
terminal coordinator edge that invalidated V31. It changes no performance
parameter: the candidate still uses
`min(topology-diameter BDP, path BDP + 16 MTU packets)` as its post-grant
window and as the first gate only when the whole flow fits. K=1 GUARD, HPCC,
Homa, the workload, the load, and every performance gate are unchanged.

The terminal repair is orthogonal to target selection. When the final ACTIVE
flow leaves a PREPARE or ACTIVATE generation, every required ACK obligation
has already closed or moved to the retired-ACK ledger. With pending ACKs at
zero, the controller finishes the empty frozen generation before its single
terminal reset. Dedicated C++ tests exercise both phases, and the exact V31
seed-226 snapshot completed in a mechanism-only diagnostic after the fix.

V32 uses fresh seeds 230--234. Seed 230 is the four-arm mechanism admission;
all 20 cells must then complete with exact configuration, matched flow hashes,
zero drop/recovery/timeout/PFC, and closed terminal state before the
performance summarizer may open FCT or queue artifacts. The V31 selector and
all thresholds are reused. Passing development still nominates the rule only
for a separate independent holdout; failure retains K=1 without post-hoc
changes.

## Frozen outcome

All 20 formal cells completed and passed the frozen mechanism gate. Every run
completed its exact traffic snapshot with zero drop, recovery, timeout, and
PFC activity, and every terminal V14 record closed. The ACK-slack arm limited
4,510--4,666 transport windows per seed. Performance was opened only after
the 20/20 mechanism report passed.

The selector retained K=1 because the candidate passed 12 of 17 gates. Against
K=1, it improved greater-than-1-MiB mean FCT by 2.93% with paired t95 interval
[-4.84%, -1.02%] and overall mean FCT by 1.18% [-1.87%, -0.49%]. Queue mean,
however, rose 4.66% [2.79%, 6.53%], whose upper endpoint exceeded the frozen
5% guard, and the Jain difference was -0.00889
[-0.00938, -0.00840].

Against Homa, the candidate significantly reduced overall P95 by 1.68%
[-3.22%, -0.15%], P99 by 3.85% [-7.47%, -0.23%], queue mean by 38.13%
[-41.24%, -35.02%], and queue P99 by 28.76% [-40.31%, -17.21%]. It did not
establish overall-mean or greater-than-1-MiB mean superiority: their intervals
were [-5.47%, +3.13%] and [-11.66%, +17.46%]. The long-flow variance is
concentrated in seed 233 at destination 6, where K=1 serial elephant service
creates head-of-line delay. This observation is a development diagnosis, not
permission to change V32 or its frozen decision.

The admitted campaign is `/tmp/guard-v32-ack-slack-window`. Its spec,
preflight, mechanism, report, and selection SHA-256 values are respectively
`28dbd1c074d9264d89501e81fe81103c61024137d2c280fe62f6c74ec23182ca`,
`49a5ab3c845fbe41dffe9ed99296aac1b1ce25553bbe475f58858f488eebe00e`,
`3872b120e627e4986c87d486e8f3cd18a6f14af109a07cc037f1834ab7788aa8`,
`ad1f40b7994d600693debe03bb0808d192482737e875d7a339861f69cbcb6e18`,
and `6a51ecc560b2cc3c07ae37015d99ee5429cb0c935e4db6b65ca5d4d15a81e24f`.
