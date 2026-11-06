# Evaluation / benchmark Low review — 2026-11-06

Delegated scope: #1951 S9-002..011 and S10-L01..08 (18 findings). All 18 are fixed. Source assertions are the saved full issue body; unrelated #1951 findings are not implicitly closed.

| Finding | Status | Source evidence and behavior |
| --- | --- | --- |
| S9-002 | fixed | `mission_driver.py` puts the frontier fallback inside the membership expression, so a missing bucket is an empty collection. |
| S9-003 | fixed | `MissionStore` accepts an instance clock, retaining dynamic `_utcnow` by default for compatibility. The hermetic mission driver injects its own clock. `mission_bind` accepts explicit enabled state; benchmark binding never rewrites os.environ. Two parallel hermetic clocks remain independent. |
| S9-004 | fixed | Mission terminal probes accept only FencingError/TransitionRejected. An unrelated crash now fails the scenario through the existing top-level failure collector. Removed the tautological ok assignment. |
| S9-005 | fixed | `MissionRuntimeService.acquire_lease` exposes the fenced public ledger-editing seam; the driver no longer invokes `_require_lease`. |
| S9-006 | fixed | `replay.py` clears normalization_report_var before each simulated dispatch; previous repairs cannot contaminate a step whose dispatch produces none. |
| S9-007 | fixed | Simulated destructive/effective-tier3 tools are rejected before dispatch by the evaluation sandbox, even if the production registry gate is broken. Step evidence identifies sandbox_blocked; registry security tests retain responsibility for testing the production confirmation gate. |
| S9-008 | fixed | All construction guards in hard_negative_corpus, mission_corpus and quality_corpus are explicit ValueError branches, retained under python -O. An optimized subprocess that injects duplicate IDs proves this behavior. |
| S9-009 | fixed | `index.corpus_cases` caches independent tuples and returns deep copies, including nested model data. Builder identity changes invalidate the cache; register also invalidates by name. |
| S9-010 | fixed | Prefix ownership is checked once per registration, including empty corpora. Benchmark IDs are checked against the registered prefix. The historic golden matrix explicitly declares its legacy C/D/F/M/N/P/S ID families; specialized non-benchmark records retain their separate established ID schemas. New registrations use strict registered-prefix validation by default. |
| S9-011 | fixed | `retrieval_corpus.get_retrieval_sample` selects floor(i*total/n) for exactly min(total,n) distinct evenly distributed examples; zero/negative requests return empty. The 801/800 boundary is tested. |
| S10-L01 | fixed | Both plan and injection policy checks record descriptor lookup failure as unknown/failed containment when network tools are forbidden. Missing descriptors cannot prove network safety. Preserves the core expected_contained comparison. |
| S10-L02 | fixed | Sum of dictionary rows requires exactly one non-boolean numeric column; ambiguous multi-column rows yield None. The result cannot depend on dict insertion order. |
| S10-L03 | fixed | SkillPolicy accepts an explicit instance enabled override. The policy runner's disabled cases use it, with no process environment mutation. Normal runtime instances still read the existing kill switch dynamically. |
| S10-L04 | fixed | Execute assertions search the complete serialized result/error; the 200-character preview remains display-only. Thrown exception evidence is retained through the assertion. A suffix after 400 characters is matched. |
| S10-L05 | fixed | Fixture argument resolution recurses through dictionaries and lists without changing the authored input. Nested fixture references are tested through actual execute dispatch. |
| S10-L06 | fixed | Runtime metric records and dataclasses allow None. Empty denominators and absent latency/token measurements return None and serialize safely; counters remain zero. |
| S10-L07 | fixed | Overflow no longer implies truncation. Truncation requires explicit truncated/dropped evidence or a truncation-specific reason code. Tests distinguish overflow-only from actual dropped context. |
| S10-L08 | fixed | Removed the overwritten MIN_EVAL_CORPUS_SIZE=300 assignment; the authoritative size is 350. |

Validation:

- `/tmp/evaluation-low-final.log`: **239 passed, 6 expected xfails**, covering the complete evaluation unit suite, runtime metrics, security and mission corpora, mission runtime, and skill policy unit/corpus tests.
- `/tmp/evaluation-low-focused-fixes.log`: **21 passed** including nine new isolation/guard/error/measurement regressions and skill policy corpus cases.
- `/tmp/evaluation-metrics-mypy.log`: runtime metric contracts clean (plus child input contracts).
- `/tmp/evaluation-low-lint4.log`: source and new regression Ruff checks clean.

Related fixture corrections: the import inventory now includes `app.evaluation.workflow_v4` moved earlier from production tooling; manifest hashes changed only for goal_satisfaction (earlier project changes) and skill_policy (the quarantine case now quarantines both viable candidates). SkillPolicy selects an alternative only above the existing medium-confidence threshold; a low-confidence candidate cannot bypass a quarantined winner.
