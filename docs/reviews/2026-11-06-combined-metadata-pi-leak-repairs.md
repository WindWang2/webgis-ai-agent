# Combined-backend follow-up: metadata and scoped Pi retention

This repair covers only the five failures assigned from the completed combined backend run. That run's 21 failures and 28,306 passed are historical results, not a final green gate.

| Finding | Correction | Evidence |
| --- | --- | --- |
| `webgis_selection_scope` is unclassified | The real `interaction_scope_tools` module is classified as inspection: it validates and reports selection scope without writing authoritative state or running analysis. No uncategorized exemption was added. | Complete registry rationalization suite, including a semantic category assertion. |
| `GIS_AWF_PRODUCT_GATE` is missing from the hotpath registry | Registered as stable, default-on, owned by the actual split handler `services/gis_harness/tool_map_product.py`; purpose records the partial-not-final/science-blocked ref gate. The actual handler and its policy were not modified. | Full hotpath registry scan, dead-entry check, kind/default coherence and exact owner assertion. |
| The same setting is absent from the environment template | `.env.example` now documents the actual default `true`, the product eligibility effect and disabling behavior. The root-authorized shared fixture change is exactly one `_ENV_BASELINE` entry set to `true`, matching Settings. | Complete environment-template parity and environment-hygiene files. No allowlist, configuration rule or baseline threshold changed. |
| Pi memory guard reports 106 retained results under the combined run | The fixture registry lacked `args_model`. Every dispatch raised inside PiInputGate and DEBUG `exc_info` capture retained those traceback frames and their eventual result locals. The stub now supplies the query tool's real parameter seam and the workload sends valid location/buffer arguments. The guard observes weak references to all 106 results created by this workload, including warmup; unrelated preexisting process objects cannot inflate it. It still enforces at most five retained results and less than 4,500,000 traced bytes. | The unchanged original case passed alone in 20.20s, but adding only `--log-level=DEBUG` reproduced the exact 106-object failure in 22.38s. The corrected entire leak file then passed with DEBUG capture enabled: 4 passed in 46.83s, including automatic and deferred GC. A counterexample intentionally caches six newly observed results and verifies rejection; after clearing that real bridge cache, release passes while ten unrelated old objects remain reachable. Gate-exception capture is also asserted absent. |

Verification:

- Complete `test_env_hygiene.py`, `test_tool_registry_rationalization.py`, `test_hotpath_flag_registry.py` and `test_env_template_parity.py`: **16 passed in 12.69s**. Actual output: `.planning/2026-11-05-all-issues-deep-review/combined-metadata-final.log`.
- Complete `test_pi_bridge_leak.py` with `--log-level=DEBUG --no-cov`: **4 passed in 46.83s**. Actual output: `.planning/2026-11-05-all-issues-deep-review/pi-leak-debug-focused.log`.
- Mypy: both changed metadata source modules clean. Ruff passes for owned Python source/tests plus the shared one-line conftest addition. `git diff --check` is clean.

No production Pi source, leak threshold, scanner, baseline, generated file, version, commit or push changed. The earlier independent cross-boundary review continues to describe its earlier snapshot; these subsequent metadata/test corrections have the focused evidence above.

Optional read-only security cross-check: all four HIGH findings in the existing 45-block scan are constructed test fixtures or deliberate detector-positive samples. No matched value was output or altered, and no security test was weakened. File and exact matched diff-line hashes plus classification reasons are in `.planning/2026-11-05-all-issues-deep-review/combined-high-scan-independent-classification.json`. MEDIUM/LOW findings were counted by rule but not exhaustively classified by this follow-up, so this is not a blanket publication-clearance claim.

Scope status: **SOURCE_FROZEN**.
