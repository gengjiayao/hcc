# V33 dyadic elephant concurrency diagnostic

V33 was a bounded post-V32 development diagnostic, not a formal campaign. It
tested a default-off K=2 rule that promotes a second elephant only when its
remaining size is no more than twice the shortest elephant's remaining size.
The allocator otherwise remains K=1, and all V14 vector safety checks stay
enabled.

On the already-open V32 seeds 230--234, the rule promoted in every run but did
not produce a stable improvement. Relative to the V32 candidate, paired
long-flow mean FCT changed by -1.32% with a 95% Student-t interval of
[-6.61%, +3.96%], while overall mean FCT changed by -0.45%
[-2.16%, +1.25%]. Relative to Homa, long-flow mean was +1.16%
[-7.44%, +9.76%] and overall mean was -1.65% [-4.29%, +0.99%].
The result therefore rejects dyadic K=2 as the next GUARD configuration; the
implementation remains default-off and is not used by V34.

