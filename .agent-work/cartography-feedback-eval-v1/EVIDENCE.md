# EVIDENCE — Cartography Harness Feedback Evaluation v1

## Before

- Visual judge (ADR-0158 / ADR-0185) attaches `visual_evidence` record-only.
- GIS semantics via `evaluate_cartography_semantics` → `desired_status`.
- `[CARTOGRAPHY_VERDICT]` projected status / failed checks / optional visual summary — **no template/codegen axis**, no unified scored feedback blob.
- Composition / component template registries existed for planner, not for Pi feedback eval.

## After

| Artifact | Role |
|---|---|
| `app/lib/harness/template_codegen_evaluator.py` | Hermetic template/codegen axis |
| `app/lib/harness/cartography_feedback.py` | Unified three-axis feedback + attach |
| `app/lib/harness/evidence.py` | `CartographicReviewEvidence.feedback` |
| `app/lib/harness/pi_agent_harness.py` | Attach after visual judge |
| `app/lib/cartography/verdict_summary.py` | Inject `feedback.scores` (incl. on pass) |
| `tests/unit/test_cartography_feedback_eval.py` | Hermetic unit tests |

## Tests executed (local)

```text
/workspace/audit2-venv/bin/python -m pytest \
  tests/unit/test_cartography_feedback_eval.py \
  tests/unit/test_verdict_summary.py \
  -q
```

Result: **34 passed** (test_cartography_feedback_eval + test_verdict_summary) in ~25s via `/workspace/audit2-venv`.

## Not executed

- Online CI / full suite (per GOAL: do not wait on CI)
- VLM / browser visual critic (hermetic path uses stored `visual_evidence` summaries only)
- Mission Runtime scenarios (explicitly out of scope)

## Honesty locks exercised

- Missing MapSpec → template axis `not_evaluated`, `score=null`
- `is_compiled=False` → fail (never silent pass)
- Unknown `templateId` → fail
- Missing visual judge → visual axis `not_evaluated`
