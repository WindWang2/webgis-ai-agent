# Adversarial Review — GIS Pack SDK & Capability Certification v2 (ADR-0199)

- Reviewer: Subagent B (independent, adversarial)
- Branch: `extensions/gis-pack-sdk-certification-v2` (3 commits on `origin/master` @ `faa453a8`)
- Worktree: `C:/Users/wangj.KEVIN/projects/webgis-wt-extension-sdk-v2`
- Date: 2026-09-17
- Working-tree note: 3 files carry unstaged lint-only edits (removed unused imports in
  `manifest.py`, `pack_catalog.py`, `test_capability_certification.py`). Reviewed as
  HEAD + these no-op edits. An untracked `.goal-loop-ledger.md` is unrelated.
- Read-only review; no code modified. Test runs and throwaway tmp-dir experiments only.

---

## 1. Verdict

**No P0 or P1 findings.** The activation gate itself is fail-closed on every path
traced: no report → rejected; wrong fingerprint → rejected; `certified=false` →
rejected; strict mode unsigned/wrong-key/tampered → rejected; gate check sits before
`LOADING` (no import before the gate); `override_gate` has exactly one caller (the
certifier); reload/upgrade/enable/activate_all/lifespan all re-enter `activate()` and
therefore the gate.

However, **the evidence-producing side does not fully match what the ADR, docs, and
CHANGELOG claim**, and there is one latent gate-bypass primitive (P2-6). Six P2
findings should be fixed before merge — each is a small diff. Sixteen P3s are listed
for the record.

Test runs: the four new test files pass (43 passed). Full `tests/unit/extensions_platform/`
suite: **2532 passed, 8 failed, 3 skipped** — the 8 failures are the known Windows
worker/rlimit/bwrap environment baseline, matching the ADR's claim (though the ADR
quotes "2538 passed"; observed 2532 — see P3-10).

---

## 2. Findings

### P2-1 — runtime_probe never verifies `result_size_policy` (dead check), contradicting ADR/docs/CHANGELOG
- Axis: Correctness / evidence honesty / generated-artifact drift
- File: `app/extensions_platform/capability_certification.py:250` (definition of
  `_classify_result_size`), `:281-318` (`_run_probe_by_registered_name` ends at the
  latency check — no call site anywhere; grep confirms the function has zero callers)
- Description: ADR-0199 D2 ("latency-class and result-size verdict buckets"),
  `docs/extension-platform/capability-certification.md` ("latency / result-size 类别核验
  … `inline_small` ≤ 16 KiB、`bounded`/`ref_offload` ≤ 1 MiB"), the CHANGELOG
  ("latency/result-size verdicts"), and the module docstring (`:22-24`) all claim the
  probe stage verifies the declared result-size bucket. It never does. A tool declaring
  `result_size_policy="inline_small"` whose probe returns 10 MB certifies cleanly.
- Repro: certify any pack; the report contains latency checks but no result-size check.
- Fix: in `_run_probe_by_registered_name`, after the latency check, append
  `_classify_result_size(meta.get("result_size_policy"), first)` as a check row (the
  function already exists and matches the report vocabulary).

### P2-2 — Byte-determinism claim is false for any tool that declares `latency_class`
- Axis: Observability / evidence honesty / ADR drift
- Files: `app/extensions_platform/capability_certification.py:243-247`
  (`_classify_latency` embeds `{elapsed_s:.3f}` in the check detail), docstring `:28`
  and `:313-314` ("报告只记判决不记原始耗时——报告必须逐字节确定"),
  `docs/extension-platform/capability-certification.md` ("无原始墙钟读数……逐字节相同"),
  ADR-0199 D2 ("latency is recorded as a verdict, not a duration").
- Description: The pass/fail latency detail contains a raw wall-clock duration. Two
  certification runs of the same pack produce **different report bytes** whenever the
  measured duration differs at 1 ms precision. Empirically demonstrated: a pack with
  `latency_class="fast"` and a `time.sleep`-based tool produced
  `latency within fast budget (0.019s|0.020s|0.021s observed)` across 8 runs — 3
  distinct reports. The shipped sample pack
  (`extensions/examples/extdemo-certified-pack/main.py:47`, `latency_class="fast"`)
  falls under this claim; a committed `.certification.json` for it will churn on every
  re-certification. The unit test `test_report_is_byte_deterministic` passes only
  because its fixture declares **no** `latency_class` (detail is then the constant
  "latency_class unknown (no assertion)") — the test does not pin the claim it
  advertises.
- Repro: see experiment above (8 runs, 3 distinct byte strings).
- Impact: documented determinism guarantee is false; strict-mode re-signs differ;
  committed reports create git noise; any audit tooling relying on "two runs are
  byte-identical" (an explicit ADR claim) breaks. Does not break the gate (the HMAC
  binds one saved file; the fingerprint excludes the report).
- Fix: record the verdict only (e.g., "latency within fast budget"), drop the measured
  number from the detail; keep quantized values out of the report. Add a determinism
  test with a latency-declared fixture.

### P2-3 — Doctor "gate awareness" is dead code: `EXTENSIONS_REQUIRE_CERTIFIED` never reaches the gate-check block
- Axis: Correctness / observability / docs-vs-code
- Files: `app/extensions_platform/cli.py:499` (gate block keyed on
  `settings_raw.get("EXTENSIONS_REQUIRE_CERTIFIED") == "True"`) vs `cli.py:113-126`
  (`_settings_summary()` emits only the pre-V4 keys; the three V4 settings
  `EXTENSIONS_REQUIRE_CERTIFIED` / `EXTENSIONS_CERTIFICATION_TRUST` /
  `EXTENSIONS_CERTIFICATION_KEY` are absent).
- Description: The doctor gate-hint block can never execute. Verified live:
  `EXTENSIONS_REQUIRE_CERTIFIED=true EXTENSIONS_DIRS=extensions/examples python -m
  app.extensions_platform doctor` with 4 discovered packs, none certified, reports
  **zero** certification problems. CHANGELOG claims "doctor gate awareness"; ADR
  implies gate diagnostics. An operator turning the gate on with uncertified packs
  gets an all-clear from doctor.
- Fix: add the three V4 keys to `_settings_summary()` (bool rendered as "True"/"False"
  like `EXTENSIONS_ENABLED`), plus a test that doctor flags `require_certified` +
  missing report.

### P2-4 — Probe can record "expectation holds" without asserting or even executing the tool
- Axis: Correctness / fail-open evidence
- Files: `app/extensions_platform/capability_certification.py:287-299`;
  `app/extensions_platform/sdk/tool.py:233-241`; `manifest.py`
  `CertificationProbe` (no cross-field validation of `expect_value`).
- Description: Two concrete vacuous-pass paths in the probe stage:
  1. `expect_value` defaults to `None`; a non-dict result (or a dict missing the key
     whose value is legitimately `None`) yields `actual=None`, and `None == None`
     passes. The report then records "expectation <key>≈None holds" as runtime
     evidence that never asserted anything.
  2. For async SDK tools the registered callable is a coroutine-function wrapper
     (`sdk/tool.py:233-241`). `_probe_callable` calls it synchronously: the probe
     obtains a coroutine object, never awaits it, never runs the tool. With
     `expect_value=None` this is a vacuous pass; otherwise it produces a false
     "deterministic replay mismatch" (digest of two distinct coroutine reprs incl.
     memory addresses) plus a `RuntimeWarning: coroutine was never awaited`.
  The probe stage is author-attested by design, so this is not a privilege
  boundary crossing — but a stage whose verdict row can say "holds" for a probe that
  did not execute the capability is fabricated evidence, and the ADR's honesty rules
  claim otherwise.
- Fix: manifest validator — when `expect_key` is set, require `expect_value is not
  None`; in the probe, typed-fail on a coroutine result (`iscoroutine`) with a clear
  "async tools cannot be probed in-process" message.

### P2-5 — Worker-mode packs are structurally uncertifiable for algorithm probes, with a false report detail
- Axis: Correctness / evidence honesty / availability under gate
- Files: `app/extensions_platform/capability_certification.py:464-491` (`_stage_tests`
  reads `record.module`), `app/extensions_platform/host.py:960-1187`
  (`_activate_worker` never sets `record.module`).
- Description: For a worker-mode pack, the host never loads the entry module, so
  `record.module is None` and `_stage_tests` finds no module-level `ALGORITHMS`. Any
  worker pack that declares an algorithm certification probe then fails the tests
  stage with the detail "pack does not expose an AlgorithmExtensionSpec (module-level
  ALGORITHMS list)" — which is **false**: the worker exposes algorithms via the
  handshake (`worker.algorithms`) and they are projected into the algorithm registry;
  the probe stage itself (`_stage_runtime_probe` → descriptor → `tool_candidates` →
  worker proxy) would work. Net effect: with `EXTENSIONS_REQUIRE_CERTIFIED=true`, a
  worker-mode algorithm pack can never be admitted (unless builtin-exempt), and its
  report misattributes the cause. ADR's honesty rule ("worker packs … reported as
  failures") is met in letter, not in spirit — the failure reason is a host-side
  blind spot, not pack behavior.
- Fix: in `_stage_tests`, when `record.worker is not None`, either source specs from
  the worker handshake (needs spec payloads over RPC) or emit an honest
  warn/fail ("tests stage cannot replay worker specs host-side") and skip the
  AlgorithmExtensionSpec accusation for worker packs.

### P2-6 — No try/finally around the certifier's `override_gate=True` activation: a mid-pipeline exception leaks an activated, uncertified pack
- Axis: Fail-open / concurrency-idempotency (latent)
- File: `app/extensions_platform/capability_certification.py:357-385`
- Description: `run_pack_certification` activates with `override_gate=True` (the only
  gate bypass in the codebase) and only deactivates if the staged checks are reached
  to completion. An exception in stages 3-5 propagates without deactivating — e.g.,
  `ValueError: Circular reference detected` from `json.dumps(default=str)` inside
  `_result_digest` (`:84`, `:306`) when a probe result contains a reference cycle, or
  a registry import error in `_stage_implementation`. The pack remains ACTIVE in a
  process whose gate is ON and whose report requirement was never satisfied. Today the
  only caller is the CLI (process exit reaps the leak; grep confirms no API/route
  caller of `run_pack_certification`), so this is latent — but any future in-process
  certification surface (the obvious next step for this feature) inherits a real gate
  bypass primitive.
- Fix: wrap stages 3-6 in `try/finally` with `if deactivate_after:
  host.deactivate(extension_id)` in the finally block; additionally guard
  `_result_digest` with a typed conversion.

---

### P3 findings

- **P3-1 — evidence mode: a report carrying any `hmac` field skips the honesty
  warning while the HMAC is never verified.**
  `capability_certification.py:754,785-793`. The "UNSIGNED … not tamper-evident"
  warning fires only when `"hmac" not in doc`. In evidence mode a tampered report
  with a stale/garbage `hmac` value is accepted **silently**, looking stronger than
  an unsigned one. Fix: in evidence mode, warn whenever the report is accepted and
  its HMAC was not verified (signed or not).

- **P3-2 — `certify --staged --save --json` exits 0 even when the save failed.**
  `cli.py:_cmd_certify_staged` drives the exit code off `report["certified"]` only;
  `report["saved"] == "failed: …"` is ignored. Scripted certification pipelines will
  record success; the gate then rejects activation with `certification_required`.
  Fix: nonzero exit (or at least `saved`-failure flag) when save fails.

- **P3-3 — report write is not atomic.** `capability_certification.py:701`
  (`write_text` directly onto `.certification.json`). Crash/disk-full mid-write
  leaves a truncated file → gate fails closed (`certification_invalid`) until manual
  re-certification; two concurrent `--save` runs can interleave. Fix: temp file +
  `os.replace`.

- **P3-4 — TOCTOU: gate trusts the discovery-time fingerprint.**
  `certification_gate_diagnostic` compares the report against `record.fingerprint`
  captured at discovery. Content modified after discovery (before activate) still
  passes the gate with evidence bound to the old bytes; for `local_untrusted` packs
  the post-load re-fingerprint only warns (`host.py:857-864`). Fix: recompute the
  fingerprint inside the gate (one `compute_fingerprint` call) or re-verify before
  LOADING for all trust levels when the gate is on.

- **P3-5 — untyped crash paths in the gate.**
  `hmac.compare_digest(actual, str(expected))` raises `TypeError` for a non-ASCII
  `hmac` value in a crafted report (`:780`); deeply nested report JSON raises
  `RecursionError` rather than `ValueError` (`:729`). Both "fail" (the exception
  aborts activation; the lifespan wrapper would disable the whole extension platform
  at startup — `app/main.py:151-198`) but violate the typed-diagnostic contract.
  Fix: wrap verification in `try/except Exception` → `CERTIFICATION_INVALID`.

- **P3-6 — dead diagnostic codes.** `PROBE_FAILED`, `ORPHAN_PROJECTION`,
  `SKILL_DECLARATION_INVALID` (`diagnostics.py:100-102`) are declared, never used;
  the pipeline reports probe/orphan/skill failures as free-text check details with
  the generic gate codes. Either wire them or drop them.

- **P3-7 — private-attribute reach-ins.** `registry._tools` / `registry._metadata`
  (`capability_certification.py:276,314`), `host._policy.trusted_publishers`
  (`:127`). The pipeline is a peer module, not the registry owner; a small
  `lookup(name) -> (func, meta)` accessor on ToolRegistry would keep the layering
  honest (consistent with the branch's own "no second registry" claim).

- **P3-8 — probe failures persist exception text and `repr(actual)` into the report.**
  `:285` (`probe raised: {exc}`), `:296` (`got {actual!r}`). The report is written
  inside the pack dir and is meant to be committed/shipped; exception messages are an
  unbounded, potentially secret-bearing channel (connection strings, paths). Fix:
  truncate + scrub, or record only the exception type.

- **P3-9 — key-reuse guidance.** `app/core/config.py` comment says the
  `EXTENSIONS_CERTIFICATION_KEY` can be "`ext keygen` 产物可直接复用" — but `ext
  keygen` produces Ed25519 signing keypairs; reusing an asymmetric private key as an
  HMAC secret is cross-protocol key reuse. It would *work* (both sides read the same
  bytes) but should not be the documented posture. Provide a symmetric keygen or fix
  the comment.

- **P3-10 — evidence numbers drift.** ADR "Local evidence" says "2538 passed";
  observed in this environment: 2532 passed / 8 failed / 3 skipped. Minor, but the
  ADR's evidence section is meant to be reproducible one-for-one.

- **P3-11 — `.certification.json` write follows symlinks.** A planted symlink at that
  path in the pack dir redirects `write_text` (`:701`). Low risk (an attacker who can
  create the symlink can already modify pack code), noted for completeness.

- **P3-12 — doctor dead branch:** `gate_mode != "off"` at `cli.py:507` compares
  against a value (`"off"`) that `_parse_certification_trust` rejects — unreachable
  condition (subsumed by P2-3's fix).

- **P3-13 — supply-chain check re-verifies signature per certification run using
  `host._policy.trusted_publishers`** — fine, but note `signature.status == "missing"`
  passes certification (`:133`), i.e. unsigned packs are certifiable. Consistent with
  platform defaults; flagging so nobody mistakes `certified` for a signature
  guarantee (the docs mostly get this right via the trust-mode section).

- **P3-14 — `_settings_summary` inconsistency risk:** the doctor block's
  `== "True"` string contract is fragile; a future switch to `Settings.model_dump()`
  or env-raw values (`"1"`, `"true"`) silently re-breaks it. Prefer reading
  `settings.EXTENSIONS_REQUIRE_CERTIFIED` directly in `_cmd_doctor` (P2-3 fix should
  do this).

- **P3-15 — sample-pack certification writes into the repo tree.** README instructs
  `certify extdemo.certified --staged --save`, which creates
  `extensions/examples/extdemo-certified-pack/.certification.json` inside the repo.
  `.gitignore` has no rule for `*.certification.json`/`.certification.json`; combined
  with P2-2 the committed report churns. Add a gitignore rule or document that the
  report is a build artifact.

- **P3-16 — `_probe_callable` swallows the args-binding distinction.** A probe whose
  `args` do not match the tool signature surfaces as the same "probe raised:
  TypeError…" row as a genuine tool failure — acceptable, but a schema-stage
  signature check would give authors a much better error earlier.

---

## 3. Suspicions investigated and cleared

1. **Gate bypass via already-ACTIVE no-op** (`host.py:654-655`): the no-op returns
   only for records already activated in this process; `HostPolicy` is built from
   settings once per process (`from_settings`, lifespan `main.py:157`), so "gate off
   at activation, on later" cannot occur within a process. Cleared.
2. **Gate bypass via `reload()` / `upgrade()`**: both funnel into `self.activate(...)`
   without `override_gate` (`host.py:1727`, `:1802`) — gate applies. Cleared.
3. **Gate bypass via `enable()`**: `enable()` only returns to DISCOVERED + revalidate;
   activation still goes through the gated `activate()`. Cleared.
4. **`activate_all()` / lifespan**: `main.py:167` uses `activate_all()` → per-id
   `activate()` → gate. Cleared.
5. **Gate placement**: check occurs after compatibility/pins and before
   `state = LOADING` (`host.py:683-693`); quarantine / trust / disabled checks all
   return before import as well. No code loads before the gate. Cleared.
6. **`override_gate` sprawl**: grep across app/tests/docs shows the only
   `override_gate=True` call site is the certifier (`capability_certification.py:361`).
   Cleared.
7. **Re-certification deadlock under gate ON**: `_stage_supply_chain` excludes the
   three certification diagnostic codes from the baseline-error check
   (`:101-114`), so a pack failed by the gate can still be certified to heal itself
   (pinned by `test_missing_report_blocks_activation`). Cleared.
8. **HMAC construction**: domain-separated prefix + key_id + fingerprint + canonical
   JSON; sign side excludes `hmac` but includes `hmac_key_id`; verify side pops `hmac`
   and recomputes symmetrically (`:649-656`, `:775-780`); `compare_digest` used; key
   bytes never logged; report file bytes never hashed (autocrlf-safe, tested by
   `test_hmac_covers_canonical_json_not_file_bytes`). Cleared (modulo P3-5 TypeError).
9. **Report as fingerprint poison**: `CERTIFICATION_FILENAME` excluded from the
   fingerprint at every directory level (`discovery.py:88-95`); pinned by
   `test_report_excluded_from_fingerprint`. Cleared.
10. **`certified=false` reports admitted by gate**: `load_certification_report`
    requires `doc.get("certified") is True` (`:743`). Cleared.
11. **Path traversal via `extension_id`**: certification only operates on records
    returned by `host.get_record` (discovery-produced ids/paths); save/load paths are
    record.path + constant filename. Cleared.
12. **Probe explosion / report size**: certification keys ⊆ declared items
    (manifest validator, fail closed); declared sections capped by
    `MAX_DECLARED_ITEMS = 128`; probe args ≤ 16 KiB; skill contracts ≤ 32 KiB; report
    read capped at 256 KiB. Cost profile is O(declared items) + 2× probes. Cleared.
13. **Backward compat of `sdk/tool.py` vocab change**: no in-repo pack or test uses
    the removed `bounded_small/…/unbounded` values; packs using them were already
    rejected at registration by the core descriptor — the change converts a delayed
    registry failure into an immediate validation error. Fix, not break. Cleared.
14. **1.0–1.2 manifests**: V4 fields are default-absent; `_validate_v4_features`
    rejects old-api manifests carrying V4 sections (tested both ways);
    `conformance.py` incompatible representative moved 1.3.0 → 1.4.0;
    `test_v2_contract.py` updated consistently. Cleared.
15. **CLI backward compat**: `certify` without `--staged` dispatches to the old
    pack-level suite; `catalog` output unchanged unless `--certified-only`; gate
    default off (`HostPolicy.require_certified=False`, kill switch pinned by
    `test_default_gate_off_activates_uncertified_pack`). Cleared.
16. **Test quality**: the adversarial suites are substantive, not tautological —
    ghost declarations, real nonce-based replay mismatch, latency lie (forced via a
    monkeypatched tolerance factor), orphan injection by hijacking `unregister`,
    HMAC wrong-key/tamper/unsigned matrix, stale detection via real content mutation.
    Gaps worth adding: determinism with latency-declared fixtures (P2-2), result-size
    verdict (P2-1), worker-mode certification (P2-5), save-failure CLI exit code
    (P3-2), evidence-mode tampered-with-hmac acceptance (P3-1).

---

## 4. Cross-PR hot-file matrix

Our changed files vs open PRs #1335, #1336, #1351-#1356 (via `gh pr diff --name-only`):

| Our file | #1335 | #1336 | #1351 | #1352 | #1353 | #1354 | #1355 | #1356 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `app/extensions_platform/*` (all 12 files) | — | — | — | — | — | — | — | — |
| `app/core/config.py` | — | — | — | — | — | — | — | — |
| `CHANGELOG.md` | — | **overlap** | — | — | — | **overlap** | — | **overlap** |
| `UBIQUITOUS_LANGUAGE.md` | — | — | — | — | — | — | — | **overlap** |
| `docs/**`, `extensions/examples/**`, `tests/unit/extensions_platform/*` | — | — | — | — | — | — | — | — |

- Zero overlap in any `app/` source file with any open PR. The only collisions are
  top-of-file append-style conflicts in `CHANGELOG.md` (three PRs) and
  `UBIQUITOUS_LANGUAGE.md` (one PR) — trivial rebase noise, not semantic.
- Note: `app/main.py` is touched by #1336, #1353, #1355 — our branch does not touch
  `app/main.py` (lifespan gate wiring comes free via `activate_all`), which is the
  right call for merge hygiene.
- Architecture: no second registry (pipeline reuses certification.py helpers,
  compute_fingerprint, host.activate/deactivate, live ToolRegistry/algorithm/fabric
  registries), no second manifest parser (V4 sections live in `manifest.py` with the
  same `_StrictModel` + floor gating), no new discovery path. Layering violations are
  limited to the P3-7 private reach-ins.

---

## 5. Bottom line

Mergeable **after** the P2 batch: P2-1/P2-2 (make the report mean what the docs say),
P2-3 (doctor hints), P2-4 (vacuous probe), P2-5 (worker honesty) are each small,
localized diffs; P2-6 is a five-line try/finally that removes the only latent gate
bypass primitive. The gate, persistence, trust modes, and manifest fail-closed matrix
are solid and well-tested.

---

## Disposition record (main agent, post-review round 3)

All six P2 findings fixed with regression tests (commit "fix(extensions): round-3
adversarial-review hardening"); P3 items fixed where safe, documented otherwise.

| Finding | Disposition |
| --- | --- |
| P2-1 result-size check dead | **Fixed**: `_classify_result_size` now executed per probe from registry metadata; `test_result_size_violation_fails` / `test_result_size_within_budget_passes` pin it. |
| P2-2 latency detail breaks determinism | **Fixed**: pass/fail details contain only verdict + deterministic budget (no observed wall clock); `test_report_deterministic_even_with_latency_class` pins two-run byte equality with a `latency_class` pack. |
| P2-3 doctor gate hints dead | **Fixed**: `_settings_summary()` now emits `EXTENSIONS_REQUIRE_CERTIFIED` / `EXTENSIONS_CERTIFICATION_TRUST` / `EXTENSIONS_CERTIFICATION_KEY`; unreachable `gate_mode != "off"` removed. |
| P2-4a vacuous expectation on non-dict results | **Fixed**: `expect_key` declared + non-object result → hard fail; pinned by `test_non_dict_result_with_expect_key_fails`. |
| P2-4b async tools never awaited | **Fixed**: `_probe_callable` drives `asyncio.run` outside loops and refuses (typed failure, no fabricated evidence) inside a running loop; pinned by `test_async_tool_probe_runs_without_loop_and_refuses_inside_loop`. |
| P2-5 worker packs falsely failed in tests stage | **Fixed**: `record.module is None` (worker mode) → pack-level warn "module-level spec evidence unavailable"; algorithm probes still execute through the worker proxy. |
| P2-6 stage exception leaks ACTIVE pack | **Fixed**: defensive `try/finally` deactivates the pack if any stage raises mid-certification; pinned by `test_defensive_deactivate_on_stage_exception`. |
| P3 evidence mode ignores hmac field | **Fixed**: evidence mode verifies the HMAC when the operator key is available (tamper → hard fail) and otherwise warns "NOT verified"; three gate tests pin the matrix. |
| P3 non-atomic report write | **Fixed**: temp file + `os.replace`. |
| P3 `--save --json` exits 0 on save failure | **Fixed**: CLI returns 1 when `saved` reports a failure. |
| P3 dead diagnostic codes | **Fixed**: `PROBE_FAILED` / `ORPHAN_PROJECTION` / `SKILL_DECLARATION_INVALID` removed before they ever shipped. |
| P3 config comment suggesting `ext keygen` PEM as HMAC key | **Fixed**: comment now prescribes a random-bytes file and warns against reusing asymmetric material. |
| P3 sample-pack `.certification.json` churn | **Fixed**: `extensions/examples/.gitignore` excludes the report (machine-local evidence). |
| P3 probe details carry exception text / repr | **Fixed**: `_bounded()` caps detail length (400 chars). |
| P3 TOCTOU discovery-time fingerprint | Documented (inherent to fingerprint-at-discovery design; the gate reads the same snapshot the host loads; re-discover closes the window). |
| P3 untyped crashes in gate (non-ASCII hmac / deep JSON) | Documented (fail closed either way; lifespan catches). |
| P3 unsigned packs certifiable | Documented (consistent with supply-chain semantics: signature is a separate trust axis from capability evidence). |
| P3 private reach-ins (`registry._tools`/`_metadata`, `host._policy`) | Documented (same-package precedent in `certification.py`; public accessors are a separate refactor). |

Post-fix suite: **2541 passed / 8 failed (Windows environment baseline, unchanged) /
3 skipped**; `ruff check` clean on all touched paths.
