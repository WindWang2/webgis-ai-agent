# Cartography Harness Feedback Evaluation Upgrade

Repository: WindWang2/webgis-ai-agent
Baseline: origin/master (record SHA at start)
Branch: harness/cartography-feedback-eval-v1

## Goal

Optimize cartography harness **feedback evaluation** along two axes, integrated with **Pi harness** mechanisms:

1. **Visual evaluation** — map screenshot / VLM critic / local visual criteria / self-heal loop quality and honesty
2. **Cartographic template / code generation evaluation** — quality of generated MapSpec / composition templates / component code, not only pixels

Do NOT invent a second agent host. Extend existing:
- `app/lib/harness/visual_judge/**`
- `app/lib/harness/visual_evaluator.py`
- `app/lib/harness/pi_agent_harness.py`
- `app/lib/cartography/verdict_summary.py` / semantic_checks / composition_validation
- cartography quality models, selfheal, mapspec visual_healer
- docs: `pi-harness-verdict-injection.md`, `pi-harness-eval-honesty.md`, ADR cartographic quality gates

## Requirements

- Single unified feedback/verdict path that Pi harness can inject and consume (see existing verdict injection seams)
- Separate scored dimensions for: visual layout/legibility, cartographic semantics (CRS/legend/scale), and template/code generation fitness (schema validity, component reuse, MapSpec compile readiness)
- Honest failure modes: missing viewport, VLM unavailable, template compile failure — no fake OK
- Tests hermetic where possible; document not-executed if tooling missing
- Master read-only; open PR; do not merge; do not wait on online CI

## Deliverables

- ADR or DECISIONS.md under `.agent-work/cartography-feedback-eval-v1/`
- Code + unit tests
- PR against master documenting before/after and Pi integration
