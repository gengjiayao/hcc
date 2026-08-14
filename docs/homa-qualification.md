# Homa ns-3 qualification (simulator commit `cedc302`)

These tests qualify mechanism paths only. They are not selected or reported as
GUARD performance wins. All runs use the storage-safe `bulk` monitor profile,
have at most 6,115 messages, and remain below 1 MiB of output per run.

## Frozen inputs

| Test | Input | SHA-256 |
|---|---|---|
| Native recovery | `experiments/inputs/homa_loss_incast.txt` | `f6b297a91fb06598e3e13266ceb71d80714718c9163b05f95ea31e673a4a0a5d` |
| Sender SRPT | `experiments/inputs/homa_sender_srpt.txt` | `e024c7b89258d14d63470f432d0330c93dd91d496b3c305c5cd155b27f0a47e7` |
| Receiver overcommit | `experiments/inputs/homa_overcommit_blocked.txt` | `f74a99924b88491c863aa4a31634e0a731be56ba0892dfd93618a83d85dff3d1` |

## Admission results

1. **No-loss workload consistency.** Run `440926590` uses 8 hosts,
   AliStorage2019, 25% load, 10 ms, and seed 712. All 6,056/6,056 messages
   complete; tracked/completed messages and completion notices are all 6,056;
   GRANT sent/received is 169,014/169,014; native retransmission, generic RDMA
   recovery, PFC, and switch drops are zero. The CDF-derived priority profile is
   3 unscheduled plus 4 scheduled levels, with cutoffs 18,945 and 98,272 bytes;
   every scheduled priority (4--7) carries DATA.
2. **Native loss recovery.** The first frozen ladder level, per-link error
   `1e-4`, did not replicate DATA loss in every seed and is retained as a failed
   mechanism gate (runs `584059679`, `665800997`, `899151606`, `482523905`,
   `477713734`). The next preregistered level, `5e-4`, passes for all five seeds
   (runs `41759134`, `805869538`, `177896970`, `637211923`, `476166057`): every
   run completes 15/15 messages; native RESEND/retransmit counts are respectively
   7, 14, 9, 12, and 22; generic RDMA NACK, IRN retransmit, and sender timeout
   recovery remain zero.
3. **Sender SRPT.** In run `367696468`, a 4 MiB message starts first and a
   64 KiB message from the same sender starts 10 us later. The short message
   completes in 14.103 us while the long one completes in 383.176 us, with no
   loss or recovery.
4. **Receiver overcommit.** The receiver's shortest message is temporarily
   blocked by a smaller, same-ToR message on its sender NIC; a second receiver
   message is available from another sender. On the identical trace,
   overcommit 1 (`973927304`) completes the 16 MiB receiver message in
   2.582856 ms, whereas overcommit 2 (`944337300`) completes it in 2.222903 ms
   (13.94% lower). The blocked local message is identical at 373.266 us and the
   8 MiB receiver message differs by only 0.05%; all messages complete without
   loss. This test detects the granted-in-flight window bug fixed by `cedc302`.

## Remaining scope limits

This is an ns-3 reimplementation of the Homa paper mechanisms, not a port of
the PlatformLab implementation. Queue 0 is reserved for control, so this model
has seven rather than eight DATA priority levels. The one-way traffic model
uses an ACK-shaped completion notice in place of an RPC response. BUSY,
NEED_ACK, and UNKNOWN are not implemented; in particular, loss of every initial
DATA packet or loss of the completion notice is not yet covered by the native
recovery qualification. Paper plots must label this baseline **Homa (ns-3)**
and disclose these differences.
