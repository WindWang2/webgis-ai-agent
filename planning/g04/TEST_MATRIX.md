# G04 TEST MATRIX

命令环境：worktree `.venv`（uv + requirements.lock 约束）。纪律：不跑全库 suite（#1753 挂起）；cartography lane 串行默认；前端 worker≤2；NODE_OPTIONS=--max-old-space-size=4096。

| # | 目标 | 命令 | 基线（clean master） | 结果 |
|---|---|---|---|---|
| T0 | 制图 lane 回归门 | `.venv/bin/python -m pytest -m cartography --no-cov --timeout=120 --timeout-method=thread -q` | 见 W0 回填 | 待跑 |
| T1 | lib 契约层 | `.venv/bin/python -m pytest tests/cartography/test_template_assets_lib.py -q --no-cov` | 新文件 | 待写 |
| T2 | DB/迁移 | `.venv/bin/python -m pytest tests/unit/test_composition_template_assets_db.py tests/test_alembic_metadata.py tests/test_deploy_migration_wiring.py -q --no-cov` | 后两项存量绿 | 待写 |
| T3 | REST API | `.venv/bin/python -m pytest tests/unit/test_composition_template_assets_api.py -q --no-cov` | 新文件 | 待写 |
| T4 | 工具面 | `.venv/bin/python -m pytest tests/unit/test_composition_asset_tools.py tests/unit/test_composition_tools_v1.py -q --no-cov` | 后者存量绿 | 待写 |
| T5 | apply e2e + 产品 | `.venv/bin/python -m pytest tests/cartography/test_template_asset_apply_v1.py tests/cartography/test_h02_product_manifest.py -q --no-cov` | 后者存量绿 | 待写 |
| T6 | 失败/并发 | 并入 T2-T5 文件（矩阵标注） | — | 待写 |
| T7 | ruff | `.venv/bin/python -m ruff check <changed>` | 0 | 待跑 |
| T8 | 前端 typecheck | `cd frontend && NODE_OPTIONS=--max-old-space-size=4096 pnpm exec tsc --noEmit -p tsconfig.json` | 0 | 待跑 |
| T9 | 前端定向 vitest | `cd frontend && NODE_OPTIONS=… pnpm exec vitest run lib/api/composition-template-assets.test.ts --maxWorkers=2` | 新文件 | 待写 |

失败注入矩阵（必须出现在测试中）：并发版本撞号；跨租户读/写；retire 后 apply/重放；非法 payload fail-closed；锁命中 user_wins；CAS superseded；损坏身份块宽容读。
