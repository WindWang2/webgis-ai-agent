# Test matrix

| Check | How | Result |
|---|---|---|
| Name mismatch still rejects | `test_pi_gis_usability_fixes.py` + V2 name test | pass, 24 tests in the combined run |
| Missing names are not a mismatch | V2 `test_missing_names_do_not_invent_a_mismatch` | pass |
| Disjoint Chengdu bbox vs Beijing scope | V2 bbox tests | pass |
| Partial overlap stays unverified | national bbox vs Beijing | pass |
| Projected CRS is not compared as lon/lat | EPSG:3857 fixture | pass |
| Admin lookup only without place tokens | lookup spy | pass |
| Admin lookup failure is unverified | raising lookup | pass |
| Default publication document identity | `prepare_publication_document(page_profile=None)` | pass |
| `a3_landscape` unsupported by the solver | not_evaluated, same object | pass |
| `a4_landscape` evaluates v5 | report + cloned spec | pass |
| Screen cartographer stays v4 viewport | `resolve_cartographer_layout` | pass |
| Evaluated chrome can move | north arrow SVG differs only with the flag | pass |
| Cartographer ledger / constraint spec regression | 70 tests with the V2 file | pass |
| Product design-trace / H02 / Pi session plan host | 15 tests | pass |
| R2 title/legend placement | subtitle does not drag the title; bottom legend stays on the page | pass, 17 V2 tests |
| Real Playwright journey | not run | NOT_RUN — no browser acceptance in this pass |
| Real Pi extension heatmap journey | not run | NOT_RUN |
| Performance before/after | not run | NOT_RUN — no hot-path rewrite |
| Remote CI | not waited on | NOT_RUN |

Interpreter: `/home/kevin/projects/webgis/webgis-ai-agent/.venv/bin/python -m pytest ... --override-ini='addopts='`
