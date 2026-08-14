# GUARD adaptive-target scope admission

This artifact records a mechanism-only admission failure for the frozen
AliStorage50 scope grid on seed 86. No performance metric was inspected and no
five-seed comparison was authorized.

All four arms completed 6,199 flows with identical input hash
`06ec28bfe9246e3c0d2dff92960d6b39a04c2016dccc89dcd062df2b9a1225d8`,
zero switch drops, and zero recovery activity. The 4- and 8-BDP scopes produced
zero adaptive-target updates, while the 12-BDP scope produced seven. Therefore
the preregistered all-arm mechanism gate failed and the campaign stopped after
four runs. Raw output IDs are recorded in `admission.json`.

