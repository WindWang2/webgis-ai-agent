# ADR-0194: 混合能动性人机空间画布协同（Mixed-Initiative Spatial Canvas Co-Pilot v1）

状态：已接受（2026-09-15）
关联：ADR-0180（GIS Situation 编译器与前端交互观察）、ADR-0138（对话 schema 契约）、ADR-0081（完成度终验）、#521（客户端载荷有界化）

## 1. 背景与靶心

当前工作台交互是一维的：用户在 Chat 输入文字 → 后端计算 → 地图渲染。两个痛点：

1. **空间表达的语言贫乏性**：用文字精确描述一个空间范围（"把刚才选出的水域向西扩大 50 米，避开主干道"）需要多轮澄清；鼠标框选只需 2 秒。
2. **歧义澄清的冷冰体验**：多解/参数权衡（分类断点、缓冲距离、方案对比）要么规则硬猜，要么干瘪的文字反问，缺乏交互式探索工具。

靶心：**画布即 Prompt（Canvas as Prompt）**——前端画布的框选/草图/高亮/测距自动编码为 `SpatialAffordanceToken` 注入后端情境模型；**生成式空间微 UI**——Agent 面临多方案抉择时向前端流式下发交互式小部件（直方图断点调节、卷帘对比、候选范围确认、草图框选回传），形成人机共创闭环。

## 2. 决策

### 决策一：SpatialAffordanceEnvelope —— 画布动作的前向上报契约

新增净新契约 `SpatialAffordanceEnvelope`（前端 → 后端）：

- 携带 1..N 条 `SpatialAffordanceAction`：`kind ∈ {box_select, freehand_lasso, polygon_lasso, highlight, measure, snap_pick, widget_reply}`、GeoJSON 几何（WGS84）、`screen_px` 屏幕像素锚点、`layer_refs` 图层引用、`map_view`（中心/缩放）、`meta`（kind 专属：snap 目标、测量值、widget_reply）。
- 两条通道同源同验证：① 随 `/api/v1/chat/stream` 请求体新字段 `canvas_actions` 整体进入（与 turn 绑定）；② 新端点 `POST /api/v1/chat/sessions/{session_id}/canvas-actions` 即时上报（手势完成即发，不等 turn），复用同一 ingest 服务。客户端在下一 turn 仍会捎带未确认信封，两者内容寻重不双计。

### 决策二：摄取镜像 `record_interaction` 纪律（不新造机制）

`app/services/gis_situation/canvas_affordance.py` 完全镜像 `observation.py::record_interaction` 的纪律（ADR-0180 S4）：

- **白名单规范化**：逐 action 键白名单投影，未知键丢弃；几何类型封闭（Polygon/LineString/Point/MultiPolygon），顶点数 ≤ 256，单 action 字节预算 8KB，信封 ≤ 12 actions / 64KB；
- **内容寻重**：`(action_id)` + payload 哈希去重，双通道重复上报不双计；
- **单调序列 + 有界环**：`map_state["_situation_canvas_affordances"]` 尾部保留最近 16 条，全程持 session 锁；
- **绝不抛出**：ingest 是增值感知面，任何异常降级为 `accepted=False` 的 ack，绝不 500 主链路（fail-open）。

### 决策三：情境注入走上下文投影位，不改 GISSituation 契约

`GISSituation` 是 `extra=forbid` 的封闭契约（ADR-0180），本次**不**新增 partition。画布事实的注入点：

1. `canvas_affordance.render_affordance_context_block(ring)` 渲染有界 `[画布意图]` 文本块（用户刚框选的范围 bbox、涉及图层、顶点数、测量值、最近的 widget_reply），由 `chat.py::_build_situation_env_block` 在结构化 `[GIS 情境]` 之后追加（fail-open，渲染失败不阻断 turn）；
2. 同时提供 `affordance_facts(ring) -> list[SitFact]`（`known()` 事实，source=`canvas_affordance`，ref 指向 ring 条目），供查询 API 与后续编译器集成使用；
3. 草图多边形与当前图层的布尔拓扑（shapely `intersection/difference/union`）由 `topology_derive()` 在**读取时**按需计算（纯函数），派生结果（命中要素数、面积、派生几何 ref）作为事实/value 注入，不落 CanonicalMapSpec——派生几何是"提议"，要经用户确认或显式工具调用才成图。

### 决策四：Generative Widget Protocol —— 声明式微 UI 元语，严禁可执行内容

后端 → 前端新增 SSE 事件类型 `ui_action`，载荷 `{"type":"ui_action","action":"mount_widget","widget":{...}}`。`widget` 为封闭联合 `WidgetSpec`：

| kind | payload（全部声明式数据） |
|---|---|
| `histogram_slider` | `{field, bins[{lo,hi,count}]≤64, breaks[], min, max, unit?, layer_ref}` |
| `swipe_compare` | `{left{label,layer_ref}, right{label,layer_ref}}` |
| `candidate_picker` | `{candidates[{id,label,geometry?,stats}]≤12, selection_mode}` |
| `sketch_box` | `{prompt, baseline_geometry?, constraints{min_area?,max_area?}, layer_ref?}` |

安全模型（纵深防御）：

- payload 是**纯 JSON 数据**，前端一律经 React 文本节点渲染，全程禁 `dangerouslySetInnerHTML`；
- `validate_widget_spec()` 在服务端（pipeline 摘取时）与前端（mount 前 `isWidgetSpecSafe`）双向校验：递归白名单键/标量类型，出现 `script/iframe/object/embed` 键、`on*` 事件键、`javascript:`/`data:text/html` 值、非白名单类型 → 拒绝挂载（服务端 ValidationError，前端静默丢弃并记 telemetry）；
- widget 载荷字节预算 16KB，超限拒绝。

用户与 widget 的交互经决策一同通道回流：`kind="widget_reply"`，`meta.widget_reply={widget_id, kind, value}`。

### 决策五：Tool Pipeline 支持 mount_widget 下发

`ToolExecutionResult` 新增 `pending_widgets` 字段（默认空 tuple，向后兼容）。`ToolExecutionPipeline.execute_tool_call` 在工具返回后从结果载荷摘取 `ui_action.mount_widget` 块，经 `validate_widget_spec` 校验后挂到该字段（非法块剥离并记日志，不影响工具本身成败）。两个引擎在 yield `tool_result`/`step_result` 的位点随后 yield `ui_action` 事件（legacy：`execution_engine.chat_stream`；Pi：`pi_event_mapper` 纯函数摘取），并写入 `TurnEventBuffer`（resume 重放一致）。工具在 worker 进程执行（Pi 路径）也不受限——widget 随工具结果载荷回流，不依赖进程内 ContextVar。

### 决策六：前端 —— 复用既有 sketch 底座，copilot 为净新目录

- `frontend/components/copilot/`（净新）：`spatial-sketch-tool.tsx`（自由手绘圈选/多边形套索/框选，SVG 覆层虚线高亮，`snapToVertex` 吸附）、`generative-widgets/`（4 类微卡片 + `widget-host` 注册表）；
- 捕获逻辑抽纯函数 `lib/copilot/sketch-capture.ts`（指针序列 → 环/框），组件只做粘合，保证 jsdom 可测；
- 全局状态走 Zustand 切片 `copilotSlice`（激活工具、高亮环、已挂载 widgets、上报延迟遥测），接入 `useHudStore`；
- SSE：`SSEEventType` 增加 `ui_action`，`use-sse-stream.ts` 分发到 copilotSlice；
- 上报通道：`lib/copilot/affordance.ts` 统一封装（立即 POST canvas-actions 端点 + 随下轮 stream 捎带）；样式遵循语义 token（surface.*/ink.*/edge.*）。

### 决策七：延迟预算

交互上报（手势完成 → 规范化信封离开前端）预算 **< 100ms**：上报为 fire-and-forget（不 await 网络完成即恢复 UI），信封构建与 dispatch 全同步（无分支网络调用、无重几何计算——拓扑派生在后端按需做）。后端 ingest 单信封处理预算 < 100ms（纯规范化 + 一次有界写）。以单元级计时测试固化（见验收证据）。

## 3. 替代方案（否决理由）

- **GISSituation 新增 canvas partition**：契约 `extra=forbid` 且编译器/投影/schema-doc 四处联动，改动面大且画布动作高频易变，适合环式观察不适合分区事实。否决，走决策三的投影位。
- **复用 MapAction 通道（COMMAND_CATALOGUE）下发 widget**：该通道语义是"服务端驱动地图效果"，方向相反（widget 是地图/面板上的人机控件）；且 MapActionPayload 无 widget 生命周期（挂载/裁决/过期）。否决。
- **前端直接渲染 LLM 文本里的 HTML widget**：注入面不可接受，与安全模型冲突。否决，声明式协议 + 双向校验。
- **用 ContextVar 在 tool pipeline 收集 widget**：Pi 路径工具在 worker 进程执行，ContextVar 不跨进程。否决，改为工具结果载荷摘取（决策五）。
- **改造既有 sketch-editor 承担框选上报**：sketch-editor 语义是"编辑地图要素"（进 undo 栈、写 sketch store），画布 copilot 语义是"向 Agent 表达意图"（不污染用户图层）。否决，仅复用其几何工具函数。

## 4. 影响

- 后端：`chat_schema.py` 增 `canvas_actions` 字段与 push DTO；`chat.py` 两个 turn 位点接入 ingest + env block 追加；`tool_pipeline.py` 增摘取；`execution_engine.py`/`pi_event_mapper.py` 增 ui_action yield；净新 `canvas_affordance.py`。
- 前端：净新 `components/copilot/`、`lib/copilot/`、`copilotSlice`；`chat.ts`/`use-sse-stream.ts` 各一处扩展。
- 兼容性：全部字段 optional，旧客户端零感知；`ui_action` 事件旧前端按未知事件忽略（SSE 解析器按 event 名分发，未匹配即丢）。
- 性能：ingest 与渲染均有界（16 条环 / 12 actions / 16KB widget），无无界循环风险。

## 5. 验收证据

- `tests/unit/test_canvas_affordance.py`：信封规范化/拒收、ingest 去重/有界环、布尔拓扑派生、widget 契约校验（含恶意载荷拒绝）、pipeline 摘取、ui_action SSE 事件形态、ingest 延迟 < 100ms。
- `frontend/components/copilot/canvas-copilot.test.tsx`：框选工具 dispatch 全局状态并渲染虚线高亮框、信封构建与即时上报（mock fetch）、widget 挂载渲染与双向绑定、不安全 widget 拒挂。
- 门禁：pytest 目标套件全绿；`pnpm test -- canvas-copilot.test.tsx` 全绿；eslint `--max-warnings 0`；`tsc --noEmit` 两遍。

## 6. 后续接口点

- 编译器集成：`affordance_facts` 可在 ADR-0180 编译器新增 partition 时直接复用（本 ADR 不做）。
- widget 生命周期扩展：`unmount_widget`/`expires_at` 过期回收（本期只做 mount + 前端手动关闭）。
- 服务端按 `candidate_picker` 裁决驱动 MapSpec mutation transaction（衔接 ADR-0183 事务系统）。
