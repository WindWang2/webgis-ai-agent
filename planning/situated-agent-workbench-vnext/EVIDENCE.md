# EVIDENCE — situated-agent-workbench-vnext

> 全部本地运行（Python 3.12 venv / Node pnpm / Chromium 1.64）；样本与命令如实记录；
> PASSED/FAILED/NOT_RUN 分层标注。基准 origin/master `40cda057a`。

## 后端（pytest，全部 PASSED）

| 套件 | 命令 | 结果 |
|---|---|---|
| E0 事实审计（12 操作 characterization） | `pytest tests/test_gis_situation_e0_trace.py` | 15 passed |
| S1 AnalysisScope（纯派生+验证链+fact+投影行） | `pytest tests/test_gis_situation_analysis_scope.py` | 17 passed |
| S3 selection history（多轮指代） | `pytest tests/test_gis_situation_selection_history.py` | 7 passed |
| S1 工具（真实 registry dispatch + in-memory store） | `pytest tests/unit/test_interaction_scope_tool.py` | 7 passed |
| S2 IR 词表+投影（含 manifest 双闸） | `pytest tests/unit/gis_harness/interaction_ir/` | 91 passed |
| S4 turn chain | `pytest tests/test_gis_situation_turn_chain.py` | 9 passed |
| S4 路由（situation/turn-chain） | `pytest tests/unit/test_situation_views_route.py` | 3 passed |
| S6 回执 invertible | `pytest tests/unit/gis_harness/map_operations/` | 66 passed |
| E5 八组多轮旅程（真实 dispatch 链） | `pytest tests/acceptance/gis_agent/test_e5_multiturn_journeys.py` | 8 passed |
| situation 族回归 | `pytest tests/test_gis_situation_{contract,compile_diff,observation_turn,projection_queries}.py` | 全 passed（schema dump 已再生） |
| 能力面/工具注册回归 | `pytest tests/unit/test_capability_registry_parity.py tests/unit/capability_runtime/` | 118 passed |
| Pi ABI 邻域 | `pytest tests/unit/test_pi_abi_adversarial.py` | 11 passed |
| API 生成物 | `pytest tests/quality/test_api_compatibility.py tests/test_api_docs_drift.py` | 12+3 passed |

## 前端（vitest / tsc / playwright）

| 套件 | 命令 | 结果 |
|---|---|---|
| interaction/selection/workbench/agent 邻域 | `vitest run lib/interaction lib/selection lib/workbench lib/map-operations components/workbench components/agent components/map/map-components` | 290+242+17 passed（分轮） |
| 状态条 + 投影 store | `vitest run components/workbench/situation-status-bar.test.tsx` | 5 passed |
| emitter/面板接线 | `vitest run lib/interaction/emitters.test.ts components/map/map-components/panel-ir-emit.test.tsx` | 7+1 passed |
| i18n / e2e-lanes 门 | `vitest run test/i18n tests/e2e-lanes` | 32+13 passed |
| typecheck | `tsc --noEmit`（双 tsconfig 脚本） | 0 错误 |
| 浏览器旅程 | `playwright test e2e/journeys/j10…j12` | 3 passed |
| @smoke 全量 | `playwright test --grep @smoke` | 4 passed；**j9 FAILED＝预存环境失败**（stash 干净基线复现——mock 档下 map-action-ack/display-confirmation 等未打桩端点打到 localhost:8001 真实后端致 CORS 噪声；与本 PR 无关，本 PR 的 api-stubs 补桩反而消除了 situation 端点泄漏面） |

## NOT_RUN / BLOCKED（如实）

- `E2E_MODE=real` 档旅程：本地无真实后端栈（uvicorn+SQLite+Redis+worker）与夜间账号；
  j10-j12 声明 modes:['mock']，real 档不在本 PR 声明面。
- Redis 集成行为：本机 Redis 不可达（端口 16379 拒连），全部测试走
  MemorySessionStore 降级路径（与 CI real-services lane 互补，未冒充）。

## 覆盖的验收点（任务书「可验收终点」对照）

- [x] ≥8 组多轮 journey 可回放（后端 8 组真实 dispatch 链 + 浏览器 3 组）；
  地图选择经 ValidatedScope 真正约束后续动作（J1：feature_edit plan 目标来自选择）
- [x] 单一真相：situation/IR/MapSpec/回执无新 store（turn_chain/situation 视图纯只读派生；
  测试断言投影失败不影响权威状态）
- [x] refresh/reconnect/cancel/session switch/stale：J6 服务端恢复 + J7 迟到旧观察不升格 +
  状态面 error 诚实降级（j12）+ outbox 会话切换既有行为未动（回归绿）
- [x] 用户可见状态准确：六态状态条（j11 断言来自服务端事实）；暗亮主题经 V4 token
  （tokens.contract 既有门）；a11y role=status+aria-live（j10 断言）
- [x] 与 #1873/#1874/#1875 不重复：复用其确认门/回执环/linked views，仅 additive；
  与 #1972/#1965 文件零重叠（git diff 文件清单核对）
