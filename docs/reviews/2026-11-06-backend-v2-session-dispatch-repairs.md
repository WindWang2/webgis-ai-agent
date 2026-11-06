# Backend v2 follow-up: session defaults and Pi test seams

This work addresses only the assigned trusted-session dispatch defect and three legacy test files. It preserves the earlier frozen SSH/Pi production corrections. The completed v2 full run remains a failed historical gate: 16 failures, 4 errors, 28,042 passed; its exact JUnit entries and provisional cause groups are in `.planning/2026-11-05-all-issues-deep-review/postreview-backend-failures.json`.

| Item | Correction | Evidence |
| --- | --- | --- |
| Optional session fields are overwritten with None before validation | Registry dispatch removes the caller's top-level session_id before validation, supplies the trusted dispatch context only when non-None, and no longer overwrites a validated model/function default with None at invocation. Declared defaults such as `default`, the empty string and None remain valid. Required fields still fail validation without trusted context; a caller-supplied victim ID never supplies that context. No tool-name exception, schema exemption or authorization bypass was added. | `test_review_session_injection_defaults.py`: four field shapes × absent/present trusted context; each case dispatches with absent and forged caller input. Hidden-schema behavior, default values, required denial before invocation and unchanged caller dictionaries are checked. The existing real composition registration test still passes its trusted-context/forged-ID/absent-context boundary. |
| Report regression expects a context error | The actual report tool declares `session_id: str = ''`. With the registry default fix, its existing missing-session error executes naturally; the report source and model are unchanged. The test now also supplies an existing victim session through untrusted arguments and verifies no report files are created. | `test_issue584_report_session_injection.py` passes trusted injection and forged override cases plus both no-context cases. Required fields on other tools retain typed VALIDATION_ERROR denial. |
| Pi parity runtime spy cannot accept terminal fencing | The existing runtime spy now accepts and forwards settle_token to the real persistent runtime. Its package-export patch uses the package dictionary so teardown preserves lazy export behavior. | Complete `test_pi_stream_nonstream_parity.py`, including its real durable claim and cancellation terminal assertion, passes. |
| Single-mint assertion uses a legacy singleton alias | The test captures the actual session-keyed active turn entry used by dispatch. It still verifies exactly one minted turn ID and equality with the signed token, and now also verifies the same identity while an unrelated session is active and survives cleanup. The Pi production source is unchanged. | Both parameterized cases in `test_prompt_turn_id_single_mint.py` pass. |

Verification:

- The new session-default matrix first reproduced the defect: **2 failed, 6 passed** (non-None string defaults were rejected with VALIDATION_ERROR).
- New matrix, existing real composition authority test, report injection tests, single-mint cases and complete Pi parity file: **22 passed in 6.79s**.
- The exact seven affected v2 OGC/COG cases, with their source and tests untouched: **7 passed in 4.75s** (OGC inspect four, dual-protocol disclosure one, honest Coverage refusal one, COG data-object publication one).
- Ruff passes for registry and all four owned test files; `git diff --check` is clean.
- A direct optional mypy check of registry reports **9 errors**, so this file is not claimed type-clean. A shadow-file comparison reconstructed the exact pre-change registry and produced the same nine messages in the same order: baseline_exit=1, current_exit=1, added_errors=[]. No unrelated type debt was changed or suppressed.

No large suite was started during the root's frontend timing isolation. No OGC/COG source or test, baseline, version, generated artifact, commit or push was changed. These focused results do not replace a new integrated backend gate.

Scope status: **SOURCE_FROZEN**.
