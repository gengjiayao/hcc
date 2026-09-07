# V35 cap-refresh sensitivity diagnostic

V35 was the final bounded performance-development diagnostic. It asked whether
V34's two narrowly missed Homa confidence gates were caused by the 5% material
fabric-cap-change threshold. The implementation exposes the threshold as a
default-5%, range-checked, cap-refresh-only option. The diagnostic reused the
already-open V34 seeds 235--239 and changed only that threshold to 2%; it did
not constitute a formal campaign or an independent holdout.

Refresh requests changed only from 90/98/107/68/106 to
92/99/105/68/108. Relative to V34, long-flow mean FCT changed by -0.001% with
a paired 95% Student-t interval of [-0.003%, +0.002%]. Overall mean changed by
less than 0.001%; overall P95 and P99 were identical in every seed. Queue mean
changed by -0.002% [-0.008%, +0.004%], while queue P99 changed by +0.118%
[-0.209%, +0.445%]. The difficult seed 238 remained at 68 refresh requests.

Therefore material-change sensitivity is not the cause of V34's remaining
long-flow/P95 uncertainty. V35 is rejected before any fresh formal run. In
accordance with the bounded stopping rule, no V36 performance policy, seed
change, threshold sweep, or relaxed selection gate follows. The final paper
must report V34's actual comparison: strong significant overall, tail, small
flow, and queue improvements over Homa, with the long-flow mean and overall
P95 intervals narrowly crossing zero.

