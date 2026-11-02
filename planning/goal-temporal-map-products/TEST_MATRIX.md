# TEST_MATRIX — GOAL 03 temporal map products

运行口径：后端 `/home/kevin/projects/webgis/webgis-ai-agent/.venv/bin/python -m pytest -q <path> --no-cov -p no:cacheprovider`；前端 `pnpm exec vitest run <path> --maxWorkers=2`（worktree frontend，pnpm --offline 安装）。

| 面 | 文件 | 数量 | 覆盖 |
|---|---|---|---|
| schema 契约 | tests/unit/test_mapspec_schema_v18_temporal.py | 20 | 单期拒绝/重叠/坏时区/ISO 解析/1.7→1.8/指纹在场投影 |
| 帧推导+编译器 | tests/unit/gis_harness/temporal_products/test_frames_and_time_filter.py | 25 | 5 表示/时区分桶/缺期/半开区间/表示归一/指纹敏感 |
| 服务层 | tests/unit/gis_harness/temporal_products/test_service.py | 26 | bind/control/compare/unbind/status + user-wins + R1 回归（时区/REST pin/unpin/compare 守卫） |
| IR+工具 | tests/unit/gis_harness/temporal_products/test_ir_and_tools.py | 13 | 对照/播放/保留短语、digest 显式、5 工具注册 |
| E2E | tests/unit/gis_harness/temporal_products/test_e2e_golden.py | 4 | 三情境×2 遍 + ISO 跨年边界 |
| atlas time | tests/cartography/atlas_batch/test_time_driver.py | 8 | 逐帧分页/计数真值/cursor 盖章/SVG 注记/typed 拒绝/截断 |
| where 语法 | tests/unit/test_feature_query_v2.py（更新） | 41 | 字符串范围算子（放宽）+ 既有全量 |
| parity corpus | tests/cartography/test_mutation_parity_corpus.py | +4 case | temporal intent 4 场景（33 失败=master 预存，stash 复核） |
| 前端 lib | lib/temporal-map/{filter,controller,linkage}.test.ts | 29 | 门控/乱序/LRU 防风暴/取消/同窗键/双源语义 |
| 前端组件 | components/map/temporal-time-controller.test.tsx | 6 | 不渲染条件/门控标签/seek CAS/unpin/compare 徽标 |
| 回归面 | map-panel 族 + map-components + data-plane | 163 | 插槽无回归 |

合计本任务新增/更新直接相关用例 ≈ 192（后端 137 + 前端 55）。全量套件见 EVIDENCE.md。

预存红灯隔离（非本任务引入，修复前后失败集一致）：
- test_algorithm_taxonomy / test_registry_validation_includes_component_system / test_workflow_profile_validation / test_registry_cross_validation_clean（data_preparation/layer_browse native 无算法 —— #1868/#1875 遗留）
- test_mutation_parity_corpus 33 例（master 即红）
- frontend tsconfig.test.json 3 例（#1875 遗留）
