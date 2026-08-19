# Inventory: PiAgentHarness success-without-evidence paths

**Ticket:** [#645](https://github.com/WindWang2/webgis-ai-agent/issues/645) (map [#644](https://github.com/WindWang2/webgis-ai-agent/issues/644))
**Question:** Where does PiAgentHarness, its production gate, or cartography status still treat missing evidence or “the tool did not error” as pass, 100, or an exemption?
**Method:** Primary sources only — `app/lib/harness/`, `app/agent_pi_bridge.py`, `app/lib/cartography/verdict_summary.py`, `app/tools/cartography_tools.py`, and the tests that lock those paths. No fixes.

Contract the code claims (`app/lib/harness/pi_agent_harness.py:1-12`, `app/lib/harness/evidence.py:6-14`, `CONTEXT.md` EvaluationEvidence): missing evidence is `not_evaluated` / `0.0` — never pass, never 100, never a production exemption. L1 “tool did not error” is not “the map is correct.” Several live paths still violate that.

---

## 1. Inventory: still reports pass / 100 / exempt

| Path | file:line | Reports | Evidence actually present |
|---|---|---|---|
| `compute_tool_choice_accuracy([])` | `app/lib/harness/pi_agent_harness.py:1680-1682` | **100.0** | No expected-tool oracle. Production `evaluate_with_evidence()` is called with no args (`app/agent_pi_bridge.py:1021`), so this is always 100 on the production gate. |
| `compute_error_recovery_rate` with zero exceptions | `app/lib/harness/pi_agent_harness.py:1741-1747` | **100.0** | “No tool exception” — the didn’t-error default. `dims_evaluated["ErrorRecoveryRate"] = True` always (`evaluator.py:128`), so the gate treats 100 as a real evaluated pass. Locked by `tests/unit/test_harness_interaction_v3.py:543-566` (`overall_passed is True` on a no-exception run). |
| `compute_step_efficiency` when actual=0 and ideal=0 | `app/lib/harness/pi_agent_harness.py:1731-1737` | **100.0** | No steps were expected and none ran. Same always-evaluated treatment (`evaluator.py:127`). |
| `evaluate_all` float surface | `app/lib/harness/pi_agent_harness.py:1830-1838` | Feeds the three 100s above into every `evaluate_with_evidence` metrics dict | Comment at `1796-1801` admits this: telemetry overrides to `null`; the gate-facing path still returns 100.0. |
| Production cartography gate | `app/agent_pi_bridge.py:1021-1026` | `require_evaluated=False` (default `require_interaction=False`) | **Explicit production exemption.** Unevaluated MapSpecValidity / CursorResolutionRate become `passed=True`, `reason=not_applicable_exempt` (`evaluator.py:158-161`). Cartography itself is forced on (`require_cartography=True`). Tests that mirror production: `tests/cartography/test_cartographic_quality_review.py:941,962,1010,1062`. |
| Default `evaluate_evidence` interaction dims | `app/lib/harness/evaluator.py:141-149` | **exempt / passed=True** when `issued == 0` | Score is honest 0.0 + `evaluated:false`, but `passed` is True (`not_applicable_exempt`). Locked by `tests/unit/test_harness_interaction_v3.py:762-771`. |
| `evaluate_all` omits V3 interaction keys when no issued actions | `app/lib/harness/pi_agent_harness.py:1840-1852` | Keys absent → `evaluate_session` **skips** them | Skip is documented as equivalent to `not_applicable_exempt` (`evaluator.py:59-63`, `tests/unit/test_harness_interaction_v3.py:504-512`). |
| Default `require_cartography=None` | `app/lib/harness/evaluator.py:109-116,201-203` | CartographicQuality **exempt / passed=True** unless a `webgis_layer_upsert` mutation is in evidence | Production overrides this to `True`. Library default still exempts view/layout/init-only runs. Exempt score is 0.0 (`evaluator.py:211`). |
| `InteractionStateConvergenceRate` override | `app/lib/harness/evaluator.py:220-231` | **100.0 / passed=True / evaluated=True**, reason `trusted_runtime_cartographic_evidence` | ACK may be `store_mounted` and explicitly **not verifiable** (`pi_agent_harness.py:194-207`). Cartographic PASS is substituted for the ACK convergence metric. |
| `CartographicQuality` synthetic score | `app/lib/harness/evaluator.py:210-212` | **100.0** when `passed and evaluated` | Categorical status collapsed to 100. Not missing-evidence; still a fake percentage. |
| Markdown report | `app/lib/harness/evaluator.py:262-268` | **🟢 PASS** whenever `passed` is True | Exemptions (`not_applicable_exempt`) render as PASS, not as NOT EVALUATED. Only `not_evaluated_policy_fail` gets the ⚫ icon. |
| L1 `execution_validity` | `app/lib/harness/pi_agent_harness.py:1579-1587` | **pass** if any tool evidence and none `is_error` | Evidence is “the tool did not error.” No MapSpec, runtime, or quality proof. |
| `HEADLESS_RUNTIME_EXECUTION` | `app/lib/harness/pi_agent_harness.py:961-975` | **pass** if no `fatalError` and no `pageErrors` | `mapLoaded` is recorded but **not** used. Empty-canvas / idle / console errors are ignored. Message: “reported no fatal or page execution error.” `RUNTIME_VALID` is defined as `mapLoaded + 无错误 + 非空 canvas` (`evidence.py:75`) and is never applied. |
| `RUNTIME_RECONCILE_EXECUTION` | `app/lib/harness/pi_agent_harness.py:1246-1256` | **pass** when `reconcile_error` is empty | Absence of an error string. Style-loaded is a separate check (`1233-1245`) that does require a positive `style_loaded is True`. |
| `mutation_accepted` default | `app/lib/harness/pi_agent_harness.py:441-443` | Missing `success` key ⇒ accepted (`True`) | “Didn’t error” + defaulted success. This only writes `MUTATION_ACCEPTED` (not counted as valid — see honest table). Still the weakest positive ladder step from silence. |
| `webgis_cartography_status` with no stored review | `app/tools/cartography_tools.py:605-610` | **`success: True`**, `cartography.status=not_evaluated`, `overall_passed=False` | No harness verdict. Tool-transport success, not cartography pass. Locked by `tests/unit/test_cartography_tools_evidence.py:576-584` (`success is True`). |
| `dims_evaluated` for ToolChoice / StepEfficiency / ErrorRecovery | `app/lib/harness/evaluator.py:126-128` | Always **`evaluated: True`** | These three dimensions can never take the `not_evaluated_policy_fail` / exempt branch. Combined with the 100 defaults, missing oracles become passing scores. |

`harness_runner.run_benchmark_scenario` (`app/tools/harness_runner.py:50-57`) is the sync consumer: `evaluate_all` → `evaluate_session`. It inherits the 100 defaults and the interaction-key skip. It does **not** go through `evaluate_evidence`.

---

## 2. Already honest (missing → `not_evaluated` / `0.0`)

| Path | file:line | What happens | Tests |
|---|---|---|---|
| No MapSpec mutations | `pi_agent_harness.py:1696-1704` | `MapSpecValidity = 0.0`, never 100 | `tests/unit/test_pi_harness.py:72-75` |
| Mutation “didn’t error” without `is_compiled` | `pi_agent_harness.py:444-448,1698-1701` | `is_valid=False` → 0.0; tier `MUTATION_ACCEPTED` is **not** `is_valid` (`evidence.py:161-163`) | `tests/unit/test_pi_harness.py:50-59` |
| No refs / no resolver | `pi_agent_harness.py:548-555,609-610,1715-1726` | Cursors stay `SYNTACTICALLY_VALID`, `is_resolved=False`, rate 0.0 | `tests/unit/test_pi_harness.py:91-102` |
| Default `evaluate_evidence` (`require_evaluated=True`) | `evaluator.py:154-157` | Unevaluated MapSpecValidity / Cursor **fail** (`not_evaluated_policy_fail`) | `tests/unit/test_pi_harness.py:193-205`; `tests/cartography/test_cartography_closed_loop.py:454-467` |
| Production telemetry digest | `pi_agent_harness.py:1793-1819`; `/metrics/digest` via `app/api/routes/metrics.py:24-26` | Unevaluated rates are **`null`** + `evaluated:false` (overrides the 100 defaults) | `tests/test_runtime_observability.py:254-262`; `tests/benchmarks/test_runtime_observability_perf.py:82-90`; `tests/unit/test_harness_dispatch_hook.py:140-169` |
| Issued map action, no terminal ACK | `pi_agent_harness.py:505-509,741,1128-1137,1754-1760` | Status stays `ISSUED`; coverage 0.0; cartography `not_evaluated` / `runtime_action_ack_pending` | `tests/unit/test_harness_interaction_v3.py:785-793` |
| `store_mounted` / `store_updated` ACK | `pi_agent_harness.py:194-207,221-222` | Not verifiable; never counts as converged | `tests/unit/test_harness_interaction_v3.py:456-465` (and surrounding store-ACK cases) |
| `InteractionRecoveryRate` with no failed ACKs | `pi_agent_harness.py:1779-1789` | **0.0**, not 100 (opposite of ErrorRecoveryRate) | `tests/unit/test_harness_interaction_v3.py:453-457` math; zero-failure ⇒ 0.0 by construction |
| Issued>0 but no ACK, Coverage | `evaluator.py` + `compute_interaction_evidence_coverage` | evaluated=True, score 0, **fail** (not exempt) | `tests/unit/test_harness_interaction_v3.py:785-793` |
| Cartography, no mutation | `pi_agent_harness.py:849-851` | `status=not_evaluated`, `termination_reason=no_mapspec_mutation`, `trusted=False` | implied by closed-loop gate test |
| State reader missing / error | `pi_agent_harness.py:877-898` | `not_evaluated` / `state_reader_unavailable` or `state_reader_error` | — |
| Missing / mismatched fingerprint | `pi_agent_harness.py:1005-1027` | `not_evaluated` or `superseded`; never pass | `tests/cartography/test_cartographic_quality_review.py:1245-1256` |
| Stale / unowned runtime observation | `pi_agent_harness.py:1174-1188` | `not_evaluated`, `stale_runtime_observation` | `tests/cartography/test_cartographic_quality_review.py:1241-1243` |
| Missing runtime viewport / visibility / opacity / style / projection | `pi_agent_harness.py:1318-1530` | check `not_evaluated` → overall `partial`, `passed=False` | `tests/cartography/test_cartographic_quality_review.py:1260-1290` |
| Untrusted or contradictory cartography payload | `evaluator.py:180-198` | `passed=False`, reason `inconsistent_or_untrusted_evidence` | `tests/cartography/test_cartographic_quality_review.py:947-968` |
| Tool-transported review is not an oracle | `pi_agent_harness.py:676-679,869-875` | Repair history only after fingerprint match; desired review is recomputed from session MapSpec | `tests/cartography/test_cartographic_quality_review.py:909-944` |
| No session harness / persist fail / deleted session | `app/agent_pi_bridge.py:885-895,964-974,1051-1077` | `not_evaluated` or `superseded`, `passed=False`, `overall_passed=False` | — |
| `webgis_cartography_status` cartography payload | `app/tools/cartography_tools.py:605-618` | Forwards stored `status`; missing review is `not_evaluated` (even though `success: True`) | `tests/unit/test_cartography_tools_evidence.py:576-584` |
| Verdict injection | `app/lib/cartography/verdict_summary.py:17-18,32-55` | No inject on missing/mismatched fingerprint; no inject on `no_session_harness` / `no_mapspec_mutation` | `tests/unit/test_verdict_summary.py:53-69` |
| Empty verdict render | `verdict_summary.py:91-96` | `status`/`desired_status`/`runtime_status` default **`not_evaluated`** | `tests/unit/test_verdict_summary.py:139-141` |
| Failed/not_evaluated checks projected to the LLM | `verdict_summary.py:58-81` | Failures and `not_evaluated` checks are included; passing checks are not | `tests/unit/test_verdict_summary.py:95-102` |
| Desired semantic review, no applicable evidence | `app/lib/cartography/semantic_checks.py:10-11,156-158,206-208,888-889` | `status=not_evaluated`, `passed=False` (`ok` requires `complete`) | `tests/cartography/test_cartographic_quality_review.py:228-235`; `tests/cartography/test_cartography_closed_loop.py:295-305`; `tests/cartography/test_thematic_convergence.py:334-344` |
| Ref resolver store miss / error | `app/lib/harness/ref_resolver.py:54-64` | `NOT_FOUND`, never resolved | `tests/unit/test_pi_harness.py:105-134` |

L5 is also honest by omission: `_success_levels` hard-codes `goal_satisfaction` to `not_evaluated` (`pi_agent_harness.py:1594-1596`) because no visual/goal oracle is installed.

---

## 3. L1–L5 and MapSpecValidityTier: which rungs production actually writes

### 3.1 MapSpecValidityTier

Defined in `app/lib/harness/evidence.py:65-75`:

| Value | Name | Meaning | Written in production? |
|---|---|---|---|
| 0 | `NOT_EVALUATED` | No evidence | **Yes** — `_validity_for_mutation` else-branch (`pi_agent_harness.py:1635-1636`) when the mutation result is neither accepted nor rejected. |
| 1 | `MUTATION_REJECTED` | Tool error / transaction reject | **Yes** — `res.get("is_error")` (`1630`). |
| 2 | `MUTATION_ACCEPTED` | Tool did not error (`success` default True) | **Yes** — `1633-1634`. Weakest positive rung; **not** `is_valid`. |
| 3 | `SEMANTIC_VALID` | `is_compiled is True` (lifecycle `validate()`) | **Yes** — `1631-1632`. This is the **ceiling** of the live ladder. `is_valid` is True from here up (`evidence.py:161-163`). |
| 4 | `COMPILE_VALID` | TS compiler compile-report success | **Never.** No assignment anywhere except the enum. |
| 5 | `RUNTIME_VALID` | Headless `mapLoaded` + no errors + non-empty canvas | **Never.** Headless results become a cartography *check* (`HEADLESS_RUNTIME_EXECUTION`), not a tier lift. |

`_validity_for_mutation` (`pi_agent_harness.py:1623-1643`) is the only writer. It never sets `compile_success`, `compile_errors`, `runtime_valid`, or `runtime_fatal_error` on `MapSpecValidityEvidence` (`evidence.py:153-156`) — those fields stay `None` / `[]`. `_evidence_to_dict` (`1646-1669`) serializes only `tier` / `is_valid` / `evaluated` / `semantic_errors` / `checkpoint_id`.

Production injects `mapspec_validator=validate_mapspec` (`app/agent_pi_bridge.py:545`) but `PiAgentHarness` **never calls** `self.mapspec_validator` (assigned at `pi_agent_harness.py:264`, zero reads). Semantic validity is trusted from the tool result’s `is_compiled` flag, which the bridge does forward (`agent_pi_bridge.py:383-405,918-930`).

### 3.2 L1–L5 (`success_levels`)

Computed every `evaluate_with_evidence` (`pi_agent_harness.py:697,1576-1597`):

| Level | Key | How status is chosen | Written into persisted production review? |
|---|---|---|---|
| L1 | `execution_validity` | `fail` if any `is_error`; else **`pass` if any tool evidence**; else `not_evaluated` | **No.** Dropped. |
| L2 | `map_state_validity` | `cartography.runtime_status` (default `not_evaluated`; `fail` / `pass` on the runtime path) | **Indirectly yes** — persisted as `cartography.runtime_status` (`evidence.py:210`, filled at `1554-1572`). |
| L3 | `cartographic_structural_validity` | `pass` iff `desired_review.passed is True`; `fail` iff `desired_status == "fail"`; else `not_evaluated` | **Indirectly yes** — persisted as `cartography.desired_status`. |
| L4 | `cartographic_quality` | `"pass"` if `cartography.passed` else `cartography.status` (so `passed_with_warnings` is flattened to `"pass"`) | **Yes** — persisted as `cartography.status` / `passed`. This is the production quality signal. |
| L5 | `goal_satisfaction` | Always **`not_evaluated`** | **No.** Dropped; never anything else. |

Production persistence (`app/agent_pi_bridge.py:1027-1032`) keeps only:

```text
{session_id, cartography, gate: CartographicQuality check, overall_passed}
```

`success_levels`, the five/nine float metrics, the per-tool validity ladder, and the full `checks` map are **not** stored. `webgis_cartography_status` therefore cannot show L1 or L5; it shows `cartography.*` plus `gate` (CartographicQuality only) plus `overall_passed`.

No test references `success_levels`.

### 3.3 Production overall_passed is a false-fail, not a false-pass

`evaluate_cartographic_session` calls `evaluate_with_evidence()` with empty `expected_tools` and `ideal_step_count=0` (`agent_pi_bridge.py:1021`). After any recorded tool call:

- `StepEfficiency = (0 / actual) * 100 = 0.0` → fail (threshold 80).
- If map actions were issued and all succeeded, `InteractionRecoveryRate = 0.0` (no non-success ACK) → fail (threshold 100).

So persisted `overall_passed` is structurally **False** on the production path even when `cartography.status == "passed"` and `gate.passed` (CartographicQuality) is True. Tests that go through `evaluate_cartographic_session` assert `cartography.passed` / `gate.passed`, not `overall_passed` (`tests/cartography/test_cartographic_quality_review.py:1570-1572`). `render_verdict_for_llm` still projects `overall_passed` into the LLM block (`verdict_summary.py:98`).

The 100s in §1 therefore do **not** currently paint the stored overall flag green. They **do** mark those dimensions `passed` inside the in-memory gate, would 🟢 PASS a markdown report, and would turn `overall_passed` true if StepEfficiency / InteractionRecoveryRate were later “fixed” without removing the 100 defaults.

---

## 4. How the production surfaces compose

```text
dispatch_tool / record_cartographic_dispatch_evidence
  → PiAgentHarness.record_event  (is_compiled forwarded; is_error from dispatch status)
  → evaluate_cartographic_session
       evaluate_with_evidence()          # no expected_tools, ideal_step_count=0
         evaluate_all → ToolChoice=100, ErrorRecovery=100, StepEfficiency=0|100
         _validity_for_mutation → max tier SEMANTIC_VALID
         _collect_cartographic_evidence → L2/L3/L4 statuses
         _success_levels → computed, then discarded
       HarnessEvaluator.evaluate_evidence(
           require_evaluated=False,      # MapSpec/Cursor exempt if empty
           require_cartography=True,     # quality not exempt
       )
       persist {cartography, CartographicQuality gate, overall_passed}

webgis_cartography_status → stored review; success=True even if not_evaluated
verdict_summary.should_inject_verdict → skip pass/superseded/no-activity/bad fingerprint
/metrics/digest → get_telemetry_summary() (null, not 100)
```

---

## 5. Tests that lock the remaining lies

| Behaviour locked | Test |
|---|---|
| No-exception run + real `is_compiled` ⇒ sync `overall_passed is True` (needs ErrorRecoveryRate=100) | `tests/unit/test_harness_interaction_v3.py:543-566` |
| `issued==0` ⇒ interaction `passed=True` / `not_applicable_exempt` | `tests/unit/test_harness_interaction_v3.py:762-771,796-818` |
| No issued actions ⇒ interaction keys omitted; `evaluate_session` skip | `tests/unit/test_harness_interaction_v3.py:504-539` |
| Production-shaped gate uses `require_evaluated=False, require_cartography=True` | `tests/cartography/test_cartographic_quality_review.py:940-942,960-963,1009-1011` |
| Status tool `success is True` with `not_evaluated` | `tests/unit/test_cartography_tools_evidence.py:576-584` |

Honesty tests for MapSpecValidity / Cursor / default `require_evaluated=True` / telemetry `null` are listed in §2. They do **not** cover the production `require_evaluated=False` call, the empty-`expected_tools` 100, or the ErrorRecoveryRate no-exception 100 on `evaluate_evidence`.
