# Cartography V5 — Plan & Progress

## 执行顺序
1. W1 主：`app/lib/cartography/render_diagnostics.py`（权威词表 + pydantic 模型）→ catalog 导出段 → 测试。commit `feat(cartography): render diagnostics contract v5`
2. W2 主：lifecycle_engine user-wins 守卫 + legend 字段级 merge + popitem 修复 + webgis_layout_set controls 类型 → 回归测试（含 "legend visible=False 提交后仍 False"）。commit `fix(mapspec): ...`
3. W3+W4+W11 → Subagent A（后端）
4. W5-W9 → Subagent B（前端）
5. W7 sidecar 端点 → Subagent A 完成 W3/W4/W11 后接手
6. W10 主：describeRenderScene + golden corpus
7. W12 主：docs
8. Review R1/R2 → 修复 → rebase origin/master → 复测 → push + PR

## 测试命令基线
- 后端 targeted: `.venv/bin/pytest tests/cartography/<file> tests/unit/test_mapspec_to_svg.py -x -q --no-cov --timeout=120 --timeout-method=thread`
- 后端 cartography lane: `pytest -m cartography --no-cov --timeout=120 --timeout-method=thread -q`
- 前端 targeted: `cd frontend && pnpm exec vitest run <paths>`
- lint: `ruff check app/ tests/` + `cd frontend && pnpm exec eslint . --max-warnings 0` + `pnpm exec tsc --noEmit`（双 tsconfig）

## 进度日志
- 2026-09-09 Phase A 审计完成（2 subagents，证据入 00-baseline.md）。
- 2026-09-09 Phase B 架构冻结（01-architecture.md）。
