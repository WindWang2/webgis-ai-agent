# AGENT-10 审查备忘录 — 混合能动性人机空间画布协同与生成式微 UI

分支：`agent/10-mixed-initiative-canvas-copilot-ui`（worktree `webgis-wt-agent-10`，基线 origin/master @ 3eb2cc6a）
日期：2026-09-15
ADR：`docs/adr/0194-mixed-initiative-canvas-copilot.md` ｜ 规格：`docs/dev/mixed-initiative-copilot-spec.md`

## 1. 交付范围

| 任务书要求 | 交付物 | 状态 |
|---|---|---|
| ADR | `docs/adr/0194-mixed-initiative-canvas-copilot.md`（7 项决策 + 否决替代 + 验收证据） | ✅ |
| 技术规格书 | `docs/dev/mixed-initiative-copilot-spec.md`（线格式 / 字节预算 / 延迟预算 / 测试 seam） | ✅ |
| 后端单测套件 | `tests/unit/test_canvas_affordance.py`（31 用例） | ✅ 31/31 |
| 前端组件测试 | `frontend/components/copilot/canvas-copilot.test.tsx`（23 用例） | ✅ 23/23 |
| 后端实现 | `app/services/gis_situation/canvas_affordance.py`（净新深模块） | ✅ |
| 前端 sketch 工具 | `frontend/components/copilot/spatial-sketch-tool.tsx`（框选/手绘圈选/多边形套索） | ✅ |
| 4 类生成式微 UI | `frontend/components/copilot/generative-widgets/`（histogram_slider / swipe_compare / candidate_picker / sketch_box + WidgetHost） | ✅ |

## 2. 感知闭环（Canvas as Prompt）实现路径

1. **上报双通道**：手势完成 → `reportAffordance()` 即时 `POST /api/v1/chat/sessions/{id}/canvas-actions`（fire-and-forget）＋ `copilotSlice.stageCopilotEnvelope()`；下一轮 `/chat/stream` 请求体 `canvas_actions` 捎带（`streamChat` 第 9 参，`useMapBridge` consume 即清）。服务端按 `action_id + payload hash` 内容寻重，双通道不双计。
2. **摄取**：`ingest_canvas_actions()` 完全镜像 `observation.record_interaction` 纪律（ADR-0180 S4）：白名单规范化（封闭几何类型、坐标范围、顶点 ≤256、单动作 8KB、信封 12 动作/64KB）→ 内容寻重 → 单调 sequence → 有界环 `map_state["_situation_canvas_affordances"]`（16 条）→ 全程 session 锁 → 任何异常降级 rejected ack（fail-open，绝不 500 主链路）。
3. **情境注入**：`chat.py::_build_situation_env_block` 在结构化 `[GIS 情境]` 之后追加 `render_affordance_context_block()` 的 `[画布意图]` 块（≤1600 字符：范围/顶点/图层/测量/widget 裁决）；`turn_stream` 的 ingest 位点在 env 块构建之前，保证本轮动作本轮可见。legacy 引擎在流起始 ingest（动作进环，下一轮可见——legacy 自建上下文不经 env 块，此为已披露边界）。
4. **布尔拓扑**：`topology_derive()`（shapely 纯函数）`select_intersect / difference / union` 三算子，等面积投影 EPSG:6933 计面积，`make_valid` 容错，空交集诚实返回 None/0，绝不抛出。派生几何是"提议"，不写 CanonicalMapSpec（要经用户裁决/显式工具调用才成图）。

## 3. 生成式微 UI（mount_widget）实现路径

1. **下发链**：工具结果载荷可选 `ui_actions: [{action: "mount_widget", widget: {...}}]` → `ToolExecutionPipeline.execute_tool_call` 经 `extract_pending_widgets()` 校验摘取，挂 `ToolExecutionResult.pending_widgets`（新增字段，默认空 tuple 向后兼容），并从 `llm_payload` 剥离 `ui_actions`（widget 不进 LLM 上下文）→ legacy 引擎在 `tool_result` 后逐个 yield `sse_event("ui_action", ...)`；Pi 路径在 `pi_event_mapper._handle_tool_execution_end` 摘取（`cached.raw_result`）拼接下发。两路径均随 `TurnEventBuffer` 记录（resume 重放一致）。摘取失败仅 WARNING，不影响工具成败。
2. **安全模型（纵深三层）**：① 服务端 `validate_widget_spec()`（pydantic extra=forbid + 递归类型白名单 + 键黑名单 `script/iframe/.../on*` + 值黑名单 `javascript:/data:text-html/<script` + 4 类 kind 形状校验 + 16KB 预算）；② 前端 `extractMountedWidget()`（`isWidgetSpecSafe` 同规则，mount 前最后一道）；③ 渲染面全程 React 文本节点，无 `dangerouslySetInnerHTML`。恶意矩阵（6 组 payload + 越界 bins/candidates/超预算）在双端测试中固化。
3. **双向绑定**：用户交互 → `widget_reply` action（`meta.widget_reply={widget_id,kind,value}`）走同一画布通道回流 + `onReply` 本地回调。sketch_box 卡片 CTA 激活框选工具，高亮环完成自动回传 WGS84 几何——完整人机共创闭环。

## 4. 延迟验证（< 100ms 交互上报预算）

- 前端：`canvas-copilot.test.tsx` 断言 `copilotReportLatencyMs < 100`（performance.now 差值覆盖信封构建 + dispatch + fetch 发起；网络完成刻意不 await）。
- 后端：`test_ingest_latency_under_100ms`（6 动作信封，规范化 + 锁写全路径 < 100ms，实测 ~1ms 量级）。
- 架构面：上报路径无分支网络调用、无重几何计算（拓扑派生在后端按需）；渲染面 SVG 覆层仅在手势后保留一个 polygon + polyline。

## 5. 门禁结果

| 门禁 | 结果 |
|---|---|
| `pytest tests/unit/test_canvas_affordance.py -v`（任务书指定） | ✅ 31/31 |
| `pnpm test -- canvas-copilot.test.tsx`（任务书指定） | ✅ 23/23 |
| 后端回归（chat_api / chat_engine / situation 观察+契约 / pi_event_mapper / tool_pipeline） | ✅ 96/96 |
| `pytest tests/unit` 后端全量 | ✅ 11710 passed；37 失败 = 35 项主干存量（干净 master 基线逐一复现，与本任务无关）+ 2 项契约门已修复（见下） |
| 契约门修复：新端点补 `CanvasActionsAckResponse` response_model + `API_CONTRACT_FIELDS_UPDATE=1` 刷新快照（diff 纯新增） | ✅ `pytest tests/unit/api_contract` 51 passed |
| 前端全量 `pnpm vitest run` | ✅ 3674/3674（含 no-raw-cjk 守卫：新组件文案键化为 `copilot` i18n 命名空间，zh-CN + en-US） |
| `ruff check`（仓级规则，改动文件） | ✅ All checks passed |
| `pnpm typecheck`（主工程 + tsconfig.test.json） | ✅ |
| `pnpm lint`（eslint `--max-warnings 0`） | ✅ |

## 6. 兼容性与风险

- **零破坏面**：`canvas_actions` / `ui_action` 均为 optional/新增事件；旧前端对未知 SSE 事件按名分发未匹配即忽略；旧客户端不发送该字段时后端行为逐字节不变（`ChatRequest` 其余字段未动）。
- **GiSSituation 契约未动**：`extra=forbid` 契约保持封闭，画布事实走独立环 + 上下文投影位（ADR 决策三），后续编译器 partition 化有现成 `affordance_facts()` 可复用。
- **已知边界**：① Pi 工具经 worker 代理回 web 进程执行，widget 摘取依赖 dispatch cache 命中路径；cache miss 降级分支（Pi-echoed result）已同款摘取。② legacy 引擎的提示注入不走 `[GIS 情境]` 投影位，画布动作在 legacy 下只进环不进当轮提示（USE_NEW_AGENT=1 为默认路径，影响面小）。③ jsdom 无 PointerEvent，前端测试以 MouseEvent 子类 polyfill（生产无影响）。
- **工程扰动**：`useMapBridge.test.ts` 6 处位置参数断言按 #558 先例扩至 9 参；map-panel 系列测试的部分 store mock 使 `WidgetHost`/`useMapBridge` 消费端增加了 `?? []` / `?.()` 容忍（对真实 store 无行为差异）。

## 7. 与任务书逐条对照

- ✅ 阶段一：ADR + 规格书先行（实现未早于文档）。
- ✅ 阶段二：测试先行，红灯确认（ModuleNotFoundError / 断言失败）后实现转绿；前端覆盖任务书指定的"框选 dispatch 全局状态 + 虚线高亮框"。
- ✅ 阶段三：`canvas_actions` 载荷 → 空间实体事实（SpatialAffordanceToken / SitFact / [画布意图]）注入情境模型；Tool Pipeline 支持 `ui_action: mount_widget` 流式事件；自由手绘圈选 / 多边形套索 / 要素吸附接口（`snapToVertex` 复用 `lib/edit/sketch-geometry`，套索逐点注入点可用）。
- ✅ 阶段四：指定两套测试全绿；ruff/eslint/typecheck 门禁全绿；延迟预算以测试固化；本备忘录 + git 提交。

## 8. 复核记录（收尾补充）

- 前端全量 `pnpm vitest run`：**3674/3674 全绿**。期间 no-raw-cjk 守卫（ADR-0144）捕获新组件裸 CJK → 全部键化为 `messages/{zh-CN,en-US}/copilot.json`（`useT('copilot')`），守卫复跑通过。
- 后端全量 `pytest tests/unit`：11710 passed / 37 failed。判定：2 项为本任务引入（V9 契约门要求新端点挂 response_model + 字段签名快照登记）→ 已修复并按仓库流程刷新快照（diff 纯新增本端点）；其余 35 项在干净 master 基线（主仓库检出 + 本 worktree venv）逐一复现，为环境相关存量（runtime validator / file adapters 真实文件依赖 / extensions platform 沙箱 / Windows 路径），与本任务无关。
