# V17 AliStorage development comparison

This campaign is a development measurement, not the final independent
holdout.  It freezes AliStorage2019 at 50% offered load on the 8-host,
2:1-oversubscribed topology and compares GUARD, HPCC, and Homa on identical
traffic files for seeds 155--159.

The workflow prevents performance-driven admission:

1. `run_general_workloads.py --phase preflight` generates and seals all five
   traffic files before ns-3 runs.
2. `--phase admission` runs the three arms for seed 155.
3. `analyze_guard_v17_general_mechanisms.py --phase admission` reads only
   configuration, completion logs, flow snapshots, PFC, and protocol counters.
   It never opens FCT or queue artifacts.
4. A passing admission file authorizes `--phase formal` for seeds 156--159.
5. The mechanism analyzer must admit all 15 cells before the ordinary general
   workload summarizer may open performance artifacts.

The GUARD arm is the complete mechanism-qualified V17 bundle: serialized
membership/progress/draining vectors, exact prefix barrier, diagnostic wire
watchdog with required-ACK-clock fallback, mixed-priority canonical vectors,
generation ACK reliability, size priority, bounded SRPT service, one-RTT and
tail bypass, and remaining-aware receiver allocation.  The campaign keeps
receiver-concurrency and work-conserving reclamation disabled, so it first
measures the current safe baseline without attributing effects to the older
bounded-elephant policy.

If this development cohort is promising, any paper result must use new seeds
in a separately preregistered holdout.  If it is not, seeds, workload, and
gates remain unchanged; the next attempt is a new algorithm version and new
development cohort.

## Frozen V17 outcome

The sealed campaign is `/tmp/guard-v17-ali50-development-v1` (preflight SHA-256
`068150ac994a0b5b0e647c2c7bf59191f333464e0e847e27e602ad744b2f1339`).
Seed 155 HPCC and Homa completed, while GUARD failed before admission at
`FreezeGuardFastpathTargetVector`: proactive-drain reservations left less
capacity than the 100-Mb/s floor required by the frozen waiter cohort.  The
runner therefore stopped before seeds 156--159 and no FCT or queue artifact
was read.  V17 is rejected for this development workload; the failed campaign
is retained and is not resumed or reinterpreted.
