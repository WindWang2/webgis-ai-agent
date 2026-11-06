# Generic library findings — scoped closure, 2026-11-06

Delegated subset of #1864 S5-003/004/005. All **42** saved findings across **27 files in six directories** were inspected against the current source before editing. The saved JSON retains the original line anchors; the table below records every delegated finding. No lint-rule or debt-baseline changes.

| Source | Original findings | Disposition |
| --- | --- | --- |
| `app/lib/cartography/accessibility_manifest.py` | S110@94 | fixed: exception-class-only DEBUG diagnostic preserves the existing optional fallback/skip without logging payload or credentials |
| `app/lib/cartography/component_abi.py` | S110@498 | fixed: exception-class-only DEBUG diagnostic preserves the existing optional fallback/skip without logging payload or credentials |
| `app/lib/cartography/component_graph.py` | B905@552 | fixed: adjacent-pair zip explicitly strict=False, with intentional length-difference comment |
| `app/lib/cartography/component_registry.py` | B007@803, S110@1121 | fixed: exception-class-only DEBUG diagnostic preserves the existing optional fallback/skip without logging payload or credentials; fixed: unused loop target explicitly renamed with underscore |
| `app/lib/cartography/dot_density.py` | S112@118 | fixed: exception-class-only DEBUG diagnostic preserves the existing optional fallback/skip without logging payload or credentials |
| `app/lib/cartography/export_semantic_corpus.py` | B018@62 | fixed: removed no-op expression; retained the compatibility export through an explicit self-alias import |
| `app/lib/cartography/extrusion_model.py` | B905@188 | fixed: correspondence zip explicitly strict=True; mismatched dimensions no longer silently truncate |
| `app/lib/cartography/label_engine.py` | B905@166 | fixed: adjacent-pair zip explicitly strict=False, with intentional length-difference comment |
| `app/lib/cartography/layout_score.py` | B007@83 | fixed: unused loop target explicitly renamed with underscore |
| `app/lib/cartography/mapspec_schema.py` | B905@1078 | fixed: adjacent-pair zip explicitly strict=False, with intentional length-difference comment |
| `app/lib/cartography/multiscale/generalize.py` | S112@114, S112@322, S112@550 | fixed: exception-class-only DEBUG diagnostic preserves the existing optional fallback/skip without logging payload or credentials |
| `app/lib/cartography/palettes.py` | B905@202, B905@500, B905@551 | fixed: correspondence zip explicitly strict=True; mismatched dimensions no longer silently truncate |
| `app/lib/cartography/quality_loop.py` | B905@343, B905@361, B905@365, B905@369 | fixed: correspondence zip explicitly strict=True; mismatched dimensions no longer silently truncate |
| `app/lib/cartography/selfheal_actions.py` | B905@328, B905@332, B905@336, S112@370, B905@396 | fixed: correspondence zip explicitly strict=True; mismatched dimensions no longer silently truncate; fixed: exception-class-only DEBUG diagnostic preserves the existing optional fallback/skip without logging payload or credentials |
| `app/lib/cartography/semantic_checks.py` | B905@356 | fixed: correspondence zip explicitly strict=True; mismatched dimensions no longer silently truncate |
| `app/lib/cartography/svg_charts.py` | B007@204 | fixed: unused loop target explicitly renamed with underscore |
| `app/lib/harness/replay/bench.py` | B007@285 | fixed: unused loop target explicitly renamed with underscore |
| `app/lib/harness/replay/explain.py` | B905@81 | fixed: correspondence zip explicitly strict=True; mismatched dimensions no longer silently truncate |
| `app/lib/harness/replay/recorder.py` | S110@174 | fixed: exception-class-only DEBUG diagnostic preserves the existing optional fallback/skip without logging payload or credentials |
| `app/lib/harness/replay/roundtrip.py` | B905@364, B905@385 | fixed: correspondence zip explicitly strict=True; mismatched dimensions no longer silently truncate |
| `app/lib/harness/replay/scenarios.py` | B007@335 | fixed: unused loop target explicitly renamed with underscore |
| `app/lib/harness/visual_judge/contracts.py` | B905@64 | fixed: correspondence zip explicitly strict=True; mismatched dimensions no longer silently truncate |
| `app/lib/harness/visual_judge/vlm_provider.py` | S110@321 | fixed: exception-class-only DEBUG diagnostic preserves the existing optional fallback/skip without logging payload or credentials |
| `app/lib/integration/ownership.py` | B007@145 | fixed: unused loop target explicitly renamed with underscore |
| `app/lib/observability/spans.py` | S112@85, S110@328 | fixed: exception-class-only DEBUG diagnostic preserves the existing optional fallback/skip without logging payload or credentials |
| `app/lib/quality/drift.py` | S110@217, S112@327 | fixed: exception-class-only DEBUG diagnostic preserves the existing optional fallback/skip without logging payload or credentials |
| `app/lib/storymap/camera_planner.py` | B007@273 | fixed: unused loop target explicitly renamed with underscore |

Validation:

- `/tmp/frontend-generic-lib-final-lint.log`: all five requested rules clean across the six directories. Default Ruff also passed (`/tmp/frontend-generic-default-lint2.log`).
- `/tmp/frontend-generic-domains-regression1.log`: broad cartography + full replay + observability run, **3,388 passed, 2 skipped, 3 expected failures**, with ten older contract/derived-artifact failures. Those ten were investigated rather than silently accepted.
- `/tmp/frontend-generic-broad-residuals-fixed.log`: **156 passed**, all five previously failing complete cartography files after reconciling explicit wrapped inset bounds, existing diagnostic emitters, current title-content IR and real CRS transformation evidence. The publication JSON diff contains only the intended page-title content additions. The CRS golden changes only two declared projected-source cells; independent inverse Mercator checks prove actual coordinate transformation, legal WGS84 bounds and affected-feature evidence.
- `/tmp/frontend-generic-observability-regression1.log`: **14 passed**, complete span tests.
- `/tmp/frontend-tail-debt-final2.log`: debt gate OK, unchanged baselines. Default Ruff and diff checks clean.
