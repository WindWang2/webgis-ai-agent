# G04 TEST MATRIX（执行回填）

命令环境：worktree `.venv`（uv + requirements.lock 约束；ruff 走 `uv tool run ruff`）。
纪律：不跑全库 suite（#1753 挂起）；cartography lane 串行默认；前端 worker≤2；
NODE_OPTIONS=--max-old-space-size=4096。

| # | 目标 | 命令 | 基线（clean master） | 结果 |
|---|---|---|---|---|
| T0 | 制图 lane 回归门 | `.venv/bin/python -m pytest -m cartography --no-cov --timeout=120 --timeout-method=thread -q` | 2345P/19S/3xf/0F（163.9s） | **2345P/19S/3xf/0F**（103.6s，与基线逐项一致，G04 新测试计入） |
| T1 | lib 契约层 | `pytest tests/cartography/test_template_assets_lib.py -q --no-cov` | 新文件 | 21 passed |
| T2 | DB/迁移 + 元数据闸 | `pytest tests/unit/test_composition_template_assets_db.py tests/test_alembic_metadata.py tests/test_deploy_migration_wiring.py tests/test_model_metadata_registration.py -q --no-cov` | 闸存量绿 | 22 + 29 + 3 passed |
| T3 | REST API | `pytest tests/unit/test_composition_template_assets_api.py -q --no-cov` | 新文件 | 10 passed |
| T4 | 工具面 + 既有契约回归 | `pytest tests/unit/test_composition_asset_tools.py tests/unit/test_composition_tools_v1.py -q --no-cov` | 后者存量绿 | 14 + 14 passed |
| T5 | apply e2e + 产品 | `pytest tests/cartography/test_template_asset_apply_v1.py tests/cartography/test_h02_product_manifest.py -q --no-cov` | 后者存量绿 | 11 + 存量绿 |
| T6 | 失败/并发矩阵 | 并入 T2-T5（并发撞号/IDOR 矩阵/retire 重放/draft 钉版本拒/外国项目/坏身份块/CAS/锁） | — | 全部覆盖并绿 |
| T7 | ruff | `uv tool run ruff check app/ tests/` | 0 | **All checks passed** |
| T8 | 前端 typecheck | `cd frontend && pnpm exec tsc --noEmit -p tsconfig.json` | 0 | exit 0 |
| T9 | 前端定向 vitest | `pnpm exec vitest run lib/api/composition-template-assets.test.ts components/drawers/composition-template-assets-panel.test.tsx test/i18n --maxWorkers=2` | 新文件 + i18n 门 | 14 + 9 + i18n 全绿 |
| T10 | eslint 新文件 | `pnpm exec eslint components/drawers/composition-template-assets-panel.tsx lib/api/composition-template-assets.ts lib/i18n/messages.ts` | 0 | exit 0 |
| T11 | CJK 门 | `pnpm exec vitest run test/i18n/no-raw-cjk.test.ts` | 绿 | passed（首轮抓到全角括号，已修） |

失败注入矩阵落地位置：并发版本撞号（test_revision_monotonic_and_unique_guard）、
跨租户读/写（test_scope_visibility_matrix / test_idor_not_found_not_403 /
test_project_asset_foreign_project_rejected / test_apply_asset_other_user_not_found）、
retire 后 apply/重放（test_retire_blocks_apply_but_allows_pinned_replay /
W3 工具 e2e）、draft 钉版本拒绝（test_pinned_draft_revision_apply_rejected）、
非法 payload fail-closed（test_payload_mode_fail_closed）、锁命中 user_wins
（W3 test_locked_component_zero_touch_on_apply）、CAS superseded（W3
test_pinned_revision_and_cas_superseded）、坏身份块宽容读（W3
test_corrupt_identity_block_tolerated）。

顺序矩阵（review P2-3）：`test_composition_asset_tools.py + test_composition_packs_v3.py`
= 23 passed；`test_template_asset_apply_v1.py + test_composition_packs_v3.py` = 20 passed
（autouse registry 重置 fixture 后顺序无关）。

reviewer 环境注记：其 venv 无 ruff（用 uv tool run），T7 由主 agent 执行并绿。
