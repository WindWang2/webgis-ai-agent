# TEST_MATRIX — GOAL-20261127-02

环境: 本地 dev（CachyOS, Python 3.12.15 venv 共享, Node via pnpm 10.34.9 共享 store）；USE_REDIS=false（conftest）；无 LLM key（工具面不依赖）。

## 命令与结果

| 套件 | 命令 | 结果 |
|---|---|---|
| 领域+服务+工具+旅程 | `pytest tests/unit/moving_object -q --no-cov` | 78 passed |
| 基线（启动时） | `pytest tests/test_temporal_profiler.py tests/unit/spatial_events -q --no-cov` | 135 passed（改前基线） |
| MapSpec 契约族 | `pytest tests/cartography/test_mapspec_schema_v6.py tests/cartography/test_mapspec_schema_v19_multiscale.py tests/cartography/test_mapspec_schema_v16_representation.py tests/unit/test_whatif_branching.py tests/unit/test_mapspec_schema_v18_temporal.py tests/unit/test_flow_product_mapspec.py tests/unit/test_mapspec_schema_v14_scene.py tests/unit/test_mapspec_schema_v17_scene_assets.py -q --no-cov` | 155 passed |
| mutation registry 一致性 | `pytest tests/unit/test_mutation_registry_consistency.py -q --no-cov` | 17 passed |
| parity corpus | `pytest tests/cartography/test_mutation_parity_corpus.py -q --no-cov` | 74 passed, 1 skipped |
| ADR 编号契约 | `pytest tests/test_adr_numbering_contract.py -q --no-cov` | 3 passed |
| 工具注册门禁 | `python scripts/check_tool_descriptor_coverage.py` | executable 613；side_effect/tags/latency/memory 100% PASS；capabilities 80% FAIL（=master 基线 122 missing，无新增） |
| 工具能力 conformance | `python scripts/check_tool_capability_conformance.py` | overall=fail 14（=master 基线 14；trajectory 工具 0 fail，apply 为合法 warning） |
| 前端新测试 | `pnpm exec vitest run lib/moving-object/ test/project/trajectory-panel.test.tsx` | 16 passed |
| 前端全量 | `pnpm run test` | 6410 passed / 50 failed / 11 unhandled —— 50 失败经 master parity worktree 抽样核实为**既有基线失败**（map-panel 族/geoai/i18n placeholder 等），与本任务无关 |
| 前端 typecheck | `pnpm run typecheck` | 新代码零错误；唯一失败 lib/mapspec/live-spec.test.ts tiles —— master 既有（parity worktree 同样复现） |
| 新文件 lint | `ruff check app/services/moving_object/ app/tools/moving_object_tools.py app/api/routes/moving_object.py app/lib/gis/{algorithms,capabilities}/moving_object.py tests/unit/moving_object/` | All checks passed |
| 新文件 eslint | `pnpm exec eslint lib/moving-object/ components/moving-object/ test/project/trajectory-panel.test.tsx lib/i18n/messages.ts components/sidebar/map-studio-tab.tsx --max-warnings 0` | 0 problems |
| mypy | 仅白名单文件参与门禁；moving_object 不在白名单。手动 `mypy app/services/moving_object/` 7 处发现已全部修复（含 2 处真实问题：fromisoformat(None) 守卫、精度/类型显式化） |

## 规模实测（tests/unit/moving_object/test_scale.py，20 实体合成轨迹）

| n（修复点） | ingest | build | dwell | events |
|---|---|---|---|---|
| 1k | 4ms | 10ms | 1ms | 17ms |
| 10k | 34ms | 301ms | 15ms | 73ms |
| 100k | 346ms | 1304ms | 180ms | 361ms |

events 阶段经 O(1) bbox 预筛优化：100k 由 11.6s → 0.36s（32×）。

## 未执行/未验证（诚实披露）

- Playwright E2E journey（`frontend/e2e/`）：未新增轨迹 journey spec——真实浏览器旅程未跑；UI 交互真实性由组件测试（16 用例，真实渲染链+mock 网络边界）与后端旅程测试覆盖，浏览器链路标为**未验证**。
- Pi 真实子进程 RPC E2E（`tests/integration/test_pi_g46_*` 模式需要 PI_JOURNEY_E2E=1 + pi 二进制）：本机 vendor/pi 为 gitlink 占位未构建；J4 以 ToolDispatchService（Pi 回调的同一调度面）同链断言替代，真实 pi 子进程链路标为**未验证**。
- real_services（Postgres/Redis/Celery）marker：未执行（本地无服务容器）。
