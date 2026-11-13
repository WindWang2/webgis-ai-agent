# G31-CROSS-LAYER-TOPOLOGY — TEST_MATRIX

执行环境：worktree g31-cross-layer-topology-20261112T144915Z @ base ca7b3ec27；
Python = 主工作区共享 venv（shapely 2.1.2 / pyproj 3.7.2）；pytest 单进程；
前端 vitest（worktree 内 pnpm install --frozen-lockfile）。真实 Pi = 本 worktree
vendor/pi 子模块 pin b4afdddf44（v1.5.2）本地构建（scripts/build-vendor-pi.sh，重锁）。

## 后端（pytest）

| 文件 | 覆盖 | 状态 |
|---|---|---|
| tests/topology_workbench/test_evaluators_g31.py | T01–T11/T16/T17 语义（人工 oracle：40_000 m² 洞 / 10_000 m² 嵌套 / 9_000 m² 越界 / 0.5m-vs-2m 端点）+ 区域阻断 + 病态几何剔除 | 20 passed |
| tests/topology_workbench/test_api_g31.py | J-G31 完整示范旅程（检查→定位→例外→修复→复验）+ T12/T13/T14/T15/T16/T18/T19/T20/T21/T23 + all-blocked 不显绿 + 导出回读 | 13 passed |
| tests/topology_workbench/test_ruleset_repair_g31.py | DSL typed 拒绝全集 + 修复桥（归属选择/无候选/人工路径） | 13 passed |
| tests/topology_workbench/test_perf_curve_g31.py | 10k 面跨层 / 10k 面 coverage / 10k 线端点资源曲线（wall/RSS/候选对/finding 计数；预算截断诚实 partial） | 3 passed（heavy 量级，见 TEST_EVIDENCE） |
| tests/integration/test_pi_g31_topology_journey.py | 真实 Pi 子进程 + stub LLM + uvicorn app + 工具 dispatch + REST 复核（PI_JOURNEY_E2E=1 opt-in） | 见 TEST_EVIDENCE |

## 前端（vitest）

| 文件 | 覆盖 | 状态 |
|---|---|---|
| frontend/test/project/topology-workbench-panel.test.tsx | 面板 10 例：维度分离渲染（unknown 不显绿）/ 全量计数披露 / 相机定位 / 例外理由必填 / 归属选择披露 / 复验 / 绑定提交 / typed 错误翻译 / stale 披露 | 10 passed |
| frontend/test/project/quality-panel.test.tsx | 既有样例审计回归（T25） | 5 passed |
| frontend/test/project/project-assets-section.test.tsx | 挂载回归（新 tab 不破坏既有） | passed |
| frontend/test/i18n/key-completeness.test.ts | 双语键一致 | passed |
| tsc -p tsconfig.test.json | 类型检查 | 72 errors = clean base 基线（我的文件 0 贡献） |

## 兼容回归（既有套件）

| 套件 | 结果 |
|---|---|
| tests/data/test_quality_rules.py + test_quality_engine_api.py | 88 passed（与 auth 套件合计） |
| tests/unit/feature_edit/（G14 全套） | 123 passed |
| tests/unit/test_selection_handles.py + test_selection_handle_routes.py（G24） | passed（并入 1212 总数） |
| tests/data/ 全量（含 quality/repair/field_compute） | 1212 passed, 11 skipped, **1 pre-existing fail** |
| tests/test_api_docs_drift.py + test_alembic_metadata.py + test_deploy_migration_wiring.py | 全部 passed（api-docs 已再生成） |
| alembic upgrade head（fresh sqlite） | 0105 应用成功，4 张 topology_* 表就位 |

## Pre-existing failures（干净 base 同样失败，非本任务引入）

| 测试 | 证据 |
|---|---|
| tests/test_api_auth_required.py::TestAllMutatingRoutesAuth | 干净 ca7b3ec27 worktree（g29 兄弟树）同败：data_import.py 六个路由缺 auth Depends（G17 遗留）。本任务路由全部 get_current_user，合规 |
| tests/data/test_field_compute_review_v1.py::TestOpenApiCompatibility::test_existing_short_schema_names_preserved | git stash 后干净 base 同败：快照本身缺 PreviewResponse（G17 合并后漂移）。本任务未触 field_compute |

## 未运行 / 边界

- 真实浏览器 E2E（Playwright）：面板为 jsdom 测试覆盖（请求链/分页/键盘可达性经 Testing Library）；未启动真浏览器 —— 如实登记
- 真实多 worker celery/Redis：eager 模式（USE_REDIS=false）路径与生产同形；多副本拓扑未验证（与 G23 同边界声明）
- GEOS 单步不可中断的取消延迟：文档化上限（RULE_WALL_CLOCK_S=120s/规则 + 批间探针 2s 节流），未做中断注入实测
