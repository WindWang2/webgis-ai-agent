# Production fail-closed gate is CartographicQuality, not the 5-float AND

`evaluate_cartographic_session` called `require_evaluated=False`, so empty MapSpec/Cursor were `not_applicable_exempt` / passed. ToolChoice / ErrorRecovery / empty StepEfficiency still scored 100 and were always `evaluated`. `overall_passed` ANDed every dimension, so it was often false even when cartography passed — and the human chat card never showed that flag anyway.

**Decision:** Fail-closed applies to the **production cartographic gate**, not to new user chrome. CartographicQuality / live Observed Map is that gate; missing required runtime evidence cannot pass (`not_evaluated` is not pass). Float oracles leave this call (telemetry and benchmarks keep them). `overall_passed` means that cartographic check only. The Agent already sees `pass` | `fail` | `not_evaluated` (ADR-0062). HUD/chat stay lifecycle review this spec — no harness badge.
