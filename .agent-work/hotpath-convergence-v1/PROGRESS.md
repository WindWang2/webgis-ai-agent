# PROGRESS — Hot-path Convergence v1

## Done

- [x] Adapter package `hotpath_convergence/` (flags, skill_bind, mission_bind, claim_ingest, pi_card, session_ctx)
- [x] Wire SkillPolicy at `compile_workflow` plan seam + `webgis_map_intent`
- [x] Wire optional Mission bind in `SwarmBridge.delegate_compound_task`
- [x] Wire claim ingest + pi card on `map_product_block` settle
- [x] Hermetic tests `test_hotpath_convergence_v1.py` (14 passed)
- [x] ruff clean on changed files
- [x] Docs: RECON, DECISIONS, PROGRESS, review note
- [x] Push branch + open PR (do not merge)

## Test command

```bash
/workspace/webgis-ai-agent/.venv/bin/python -m pytest \
  tests/unit/gis_harness/test_hotpath_convergence_v1.py -o addopts= -q
# → 14 passed
```

## Known limits

1. Skill guidance is additive metadata — does not yet re-rank plan candidates by skill step ordering.
2. Mission auto-bind only on swarm compound delegation path when `GIS_MISSION_HOTPATH=1`.
3. ClaimStore remains process-local (no Alembic); settle ingest projects available map_product facets/layers/goal evidence.
4. Pi card is attached on intent tool + map_product block; not every SSE event.
