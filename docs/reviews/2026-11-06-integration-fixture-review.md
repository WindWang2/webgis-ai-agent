# Final integration fixture reconciliation — 2026-11-06

Delegated failures from core's full167 unit run were verified before adapting the tests. No production authorization or resume-verification behavior was weakened.

| Scope | Root cause / correction | Meaningful assertions retained or added |
| --- | --- | --- |
| test_composition_template_assets_api, full file | JWT subjects had no live User rows. The asynchronous fixture also did not redirect the synchronous asset service or live-identity session factory to its isolated database. Seed real active users and an organization, and point sync/async factories at the same per-test SQLite file; invalidate identity caches between tests. | Anonymous create 401; private asset IDOR 404; same-org shared read 200 / member write 403; real same-org admin publish; version/publish/restore/retire lifecycle; payload errors remain 422. |
| test_deep_review_security_sec02, full file | Tokens referred to absent users; old tests assumed an admin could read an unrelated organization's or unscoped private template. Seed real actors and organizations with both database session paths isolated. Admin success cases now explicitly use the same organization. | Foreign/private admin read 404; forged org claim does not override the live organization; a downgraded admin JWT cannot write; same-org viewer read 200 / write 403; cross-template parent and legacy dirty inheritance rejection remain enforced. |
| test_harness_e2e_scenarios_v6 scenario9 | The test cleared every payload, then expected the restored workflow fingerprint to remain live even while asserting missing refs and stale stages. Missing bindings change the authoritative row fingerprint. | Assert stale before recomputation; retain all three actual recomputations, readable newly stored refs and all-satisfied final stages. |

Validation:

- `/tmp/frontend-owned-full167-final.log`: **81 passed**, covering all three owned files and the related complete templates API / API Low regression files.
- Ruff passed for the three changed test files. Production sources were unchanged by this batch.

## CI shard172 follow-up

Five delegated failures across three files were reconciled with the current authoritative contracts:

| Scope | Correction / regression |
| --- | --- |
| test_collab_v6 lock family | The engine test now uses the explicit `layer-b__child` physical-family delimiter instead of treating an unrelated hyphenated ID as a child. |
| test_collab_v6 anonymous WebSocket | Its raw SQL fixture bypasses ORM bind processors, so it now seeds the hash that an actual ORM insert or migration persists. Real ORM reads retain the typed verifier. The matrix checks valid raw-token acceptance and plain database-digest replay rejection with close4003. |
| test_contract_kernel family | Both family directions are tested with `__`; ordinary hyphenated IDs are explicitly independent and cannot cause a false lock match. |
| test_contract_kernel reader | Read all100 authoritative locks and prove the last one still blocks its target. The old64-item display-style truncation expectation was unsafe for an authorization reader. |
| test_runtime_chaos_engine global task capacity | Full live capacity produces QUOTA while the existing running task remains tracked, uncancelled and therefore controllable. Completing a different task admits a new task and removes the terminal task's session bookkeeping. The separate per-session eviction/cancellation regression remains unchanged. |

`/tmp/frontend-owned-shard172-final.log`: **96 passed**, all three complete files. Ruff and `git diff --check` passed. This follow-up changes tests only, retaining the strengthened production boundaries.
