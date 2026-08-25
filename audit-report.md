# WebGIS AI Agent 全量深度审计报告（2026-08-26）

> 审计基线: master @ `e349b18` · 方法: 5 个并行专项审查（Harness / GIS 算法 / Tool 系统 / 模型库 / 前端），全部结论经当前代码 `file:line` 实证，非文档转述。
> 前置轮次: 2026-08-25 full-scope review 已闭环 issue #936–#977（全部 closed）。本轮聚焦**残留与新发现**问题。
> 规模实测: `app/` 约 10.8 万行 Python（37 工具模块 / 160 注册工具）+ `frontend/` 约 7.3 万行 TS + `tests/` 约 11.9 万行。

---

## 1. 当前架构总览

```
用户
 ↓ 输入（+ map_state 视口/图层快照双通道感知）
frontend/app/page.tsx → chat-tab.tsx handleSend → useMapBridge.ts send
 ↓ POST /api/v1/chat/stream (+Last-Event-ID 续传, X-Session-Token)
app/api/routes/chat.py:721 chat_stream
 ├─ _guard_body_session(:767) 会话所有权校验 → 立即归还 DB 连接
 ├─ _use_pi_bridge(:758) ──┬─ true → Pi 子进程桥(:821-936)
 │                        │    agent_pi_bridge.py stream_prompt(:2106)
 │                        │    vendor/pi Node 子进程(JSON-RPC)
 │                        │    唯一代理工具 webgis_execute → /pi-tools/execute
 │                        │    → 与 legacy 同一 ToolDispatchService
 │                        └─ false → Legacy ChatEngine(:938)
 ↓ Legacy 引擎: 规划-执行-反思循环
chat/execution_engine.py:1507 chat_stream
 ├─ 整轮会话锁(:1534) + 总预算 TURN_TOTAL_TIMEOUT_S=900s(:346,:1763)
 ├─ 规划: plan_orchestrator.py orchestrate_plan → CanonicalPlan 持久化
 │        ★ gis_harness 附着点(:477-489): intent + recipes，仅建议不接管
 ├─ 执行: for round in range(CHAT_MAX_ROUNDS=60)(:1665)
 │        ToolCatalog.select_schemas → 流式 LLM(llm_client.call_llm_stream)
 │        → 并行工具波 asyncio.wait(FIRST_COMPLETED)（抢占取消/去重哨兵）
 ├─ 反思: 下一轮 LLM 调用即反思（工具结果已回填 messages）
 └─ 熔断: no-progress(:2128) / 空响应诚实失败(:2182)
 ↓ 工具分发（两引擎共用）
tool_pipeline.py → tool_dispatch_service.py:233 → tools/registry.py:466
 ├─ ref: 解引用(:674) / Pydantic 校验(:708) / tier-3 门禁(:655)
 ├─ execution_policy INLINE/ASYNC/THREAD → 重计算 submit_durable_job → Celery
 └─ 大 GeoJSON → session store 铸 ref:geojson-<hex> → SSE 只发 ref
 ↓ 结果回流与渲染
SSE step_result → 前端命令分发(20 命令目录) → MVT(>5000 要素) / 整包 GeoJSON
 → MapSpec compiler → reconciler 增量应用 → MapLibre
 → ACK → cartographic 评估 → repair_action 闭环
```

**LLM 出口现状**: 已收敛为单模块 3 函数（`app/services/chat/llm_client.py` 的 `call_llm`/`call_llm_stream`/`test_llm_connection`，per-loop 共享连接池），消费点 6 处；Pi 子进程模型配置独立（仅继承 os.environ）。

**状态管理四层**: SessionStore（ref 本体 + L1 缓存）/ MapSpec（磁盘原子写 + lifecycle 回滚）/ Durable Jobs（DB analysis_tasks）/ Project Workspace（版本化 DAG + 血缘）。

**GIS 计算栈**: 网络分析（Dijkstra/A*/转角感知、OD、2-opt VRP、等时圈）、统计（Getis-Ord、BH-FDR、Moran 置换、Jenks DP、LISA）、遥感（窗口化读取 + 资源护栏）、MVT 手写编码器（符合 2.1 规范）。经多轮审计，**算法主体正确性高**，本轮未发现 P0/P1 级算法错误。

---

## 2. 核心流程健康度评估

| 流程 | 状态 | 关键证据 |
|---|---|---|
| 规划→执行→反思循环 | ✅ 基本健康 | 并行工具波、抢占取消、no-progress 熔断、900s 预算齐备 |
| 工具分发与 ref 契约 | ✅ 主链路优秀 | 去重哨兵、透明解引用、tier 门禁；但存在 4 处缺口（见 §4） |
| 大数据渲染契约 | ✅ 成熟 | >5000 要素 MVT、ETag/304、single-flight、gzip LRU |
| 制图闭环 | ✅ 成熟 | MapSpec 单一事实源 + reconciler + repair_action |
| GIS Harness 意图层 | ⚠️ 仅建议权 | intent/recipe/planner 存在但产物被裁剪、执行未被接管 |
| LLM 调用层 | ⚠️ 薄抽象 | 无 usage 采集、无重试、无角色化配置、Pi 双轨 |
| 前端可恢复性 | ⚠️ 缺口 | 回合不可取消、错误文案技术化、失败无重试入口 |

---

## 3. 性能瓶颈（按影响排序）

| # | 瓶颈 | 位置 | 量化影响 |
|---|---|---|---|
| P-1 | 轮内上下文无预算：当前 turn 的全部 tool 消息豁免于 HISTORY_TOKEN_BUDGET，每轮全量重发 | history_compression.py:100-114; execution_engine.py:791-795 | 每轮 ~15KB 增量 × 60 轮上限 → 二次方级 token 增长，终致 provider 400 整轮报废 |
| P-2 | 工具 schema 选集过大且无硬预算：40 个 tier-1 常驻 26.6KB；典型中文请求激活 81 工具/57KB | tool_catalog.py:152-190; execution_engine.py:428-469 | 每轮 1.5–2 万 token 纯 schema，比历史预算大一个数量级；粘性域只增不减 |
| P-3 | 等时圈设施→边投影 O(F×E) 暴力扫描，注释声称的 STRtree 从未构建 | geo_analysis/network.py:124-134 | 20k 边实测: 单设施全扫 75ms，50 设施≈3.7s；STRtree 可提速 50–100× |
| P-4 | Pi 流式路径无整轮总预算 + PiBridge 单例锁跨会话串行 | agent_pi_bridge.py:2106-2198 | 失控 turn（每 <120s 出一事件即续命）可无限阻塞全进程所有会话 |
| P-5 | 图层清单无上限渲染进 system 消息，每轮重发 | context/layer_schema.py:124-165 | 长会话几十个 ref → 数千 token 每轮重复计费 |
| P-6 | HUD 展开期间空闲态仍 60fps rAF setState 循环 | hud/embodied-hud.tsx:81-93 | 空闲每帧一次 React render/commit，移动端耗电 |
| P-7 | MVT 每瓦片对每候选要素全量重扫坐标做反子午线判定 | mvt.py:1170,463-473 | 低 zoom 大多边形热路径纯 Python O(候选顶点总数) 重复计算 |

## 4. 潜在 Bug（正确性缺陷，按严重度）

| # | 严重度 | 缺陷 | 位置 |
|---|---|---|---|
| B-1 | **P0** | `finalize_display` 注册域 `"cartography"` 不在 DOMAIN_KEYWORDS 词表 → legacy 引擎永不推送该工具，SYSTEM_PROMPT 强制的每轮收口钩子不可达 | tools/layer_manager.py:97-101; tool_catalog.py:30-113 |
| B-2 | P1 | harness 前门工具（webgis_map_intent/product）的结构化裁决（intent/candidates/plan/resolved_tool）被 slim_tool_result 的 summary 分支整体丢弃，LLM 只见一行摘要 | gis_harness/tools.py:209-224,682-736; llm_result_formatter.py:233-251 |
| B-3 | P1 | `reproject_coordinates` tier-2 且零 domains → 对 LLM 永久不可见，非 WGS84 上传数据无 canonical 处理路径 | tools/coord_transform.py:110-131 |
| B-4 | P1 | 裸 `{"success": False, "message": ...}`（约 17 站点）逃过错误归一 → 失败被标 completed，同参重试被"已成功执行"谎言拦截 | cartography_tools.py:217 等; llm_result_formatter.py:174-205; tool_dispatch_service.py:317-334 |
| B-5 | P2 | `heatmap_data` native 分支就地变更共享 ref payload，污染会话存储（后续读同 ref 的工具看到上次的热力图元数据）+ 同波竞态 | tools/spatial.py:203-237 |
| B-6 | P2 | turn 级同参去重拦截"上下文已变的合法重调"，且 llm_payload 为成功口吻 | tool_dispatch_service.py:256-283 |
| B-7 | P2 | 变化检测以形状相等代替网格相等判定，跨场景形状巧合相等时静默错位比较 | spatial_tasks.py:334,279-298 |
| B-8 | P2 | DEM 哨兵值（-9999）在降采样**之后**才掩膜，bilinear 混合哨兵逃逸（-4899 等中间值进入地形统计） | rs/spectral_engine.py:148-149; rs/stac_client.py:206-215 |
| B-9 | P3 | Moran's I 使用非对称 KNN 权重未对称化，与 PySAL 口径有系统偏差 | geo_analysis/statistics.py:55-89,247 |
| B-10 | P3 | `nearest_target_id` 输出行号而非要素标识，下游误用导致错误关联 | geo_analysis/network.py:432,440 |
| B-11 | P3 | 标题生成用推理模型 + max_tokens=64 → 推理前缀当标题 | execution_engine.py:856-876 |
| B-12 | P3 | Pi 迟到工具回调把 step 记到 `tasks[-1]` → 归属错任务 | agent_pi_bridge.py:441-454 |
| B-13 | P2 | 热力图例只有定性标签（极低→极高），min/max/unit 全部丢弃 | map/floating-legend.tsx:15-44 |
| B-14 | P2 | MapSpec 色条 `toFixed(1)` 绕开 formatLegendValue → 0–0.004 密度两端同显 "0.0" | map-components/colorbar.tsx:44-45 |
| B-15 | P2 | 流级错误向前端展示原始英文技术串，后端中文 detail 被丢弃 | useMapBridge.ts:287-301; api/transport.ts:105-109 |

## 5. 技术债务（架构级）

| # | 债务 | 现状与影响 |
|---|---|---|
| D-1 | **双引擎漂移**: Pi 绕过 classify_followup/should_plan/ToolCatalog/no-progress 熔断/整轮预算；skill_name 与 project_id 在 Pi 路径被显式忽略（chat.py:750-755,830-836）；预算语义三套（legacy 900s / Pi 非流式 300s / Pi 流式 ∞）且注释漂移 | 每修 legacy 侧问题需人工判断移植，历史已多次 parity 类修复 |
| D-2 | **模型库单薄**: 无 usage/token 记账（成本不可观测）、零重试零降级、provider 特例（MiniMax XML/DeepSeek 头）内联在"通用"客户端、temperature 缺失、max_tokens/timeout 硬编码、requirements 声明 `openai` 死依赖；Pi 模型配置双轨（env 不映射、set_model RPC 零调用） | 换供应商=改核心客户端；CostManager/ModelSelector 目标 0% 覆盖 |
| D-3 | **工具元数据债务**: 156/156 工具 version="1.0"/cv1 从未 bump（lineage 指纹无区分度）；无 cost 先验（wave 并发一刀切 5）；CELERY 执行策略 0 使用而 3+ 工具内部私自 apply_async；JSON Schema 约束系统性松散（全库仅 2 处 Literal、1 处 pattern）；错误返回四族形态并存 | "可评价/可版本管理"两维有架子无内容；参数错误多耗一轮自愈 |
| D-4 | **registry 硬编码 skip_keys**: cursor-ref 工具名单写死在 `_dispatch_impl`，新工具接 ref 游标须改核心（registry.py:634-653） | 可组合主路径的扩展成本高 |
| D-5 | **前端巨型文件**: map-panel.tsx 1223 行、reconcile 效果 15 项依赖；生产路径残留裸 console（5+ 处）；`any` 类型 317 处集中于 MapLibre 边界 | 回归温床（历史 #739/#801/#843 均在此文件） |
| D-6 | **零移动端适配**: 布局全桌面像素常量，无断点；≤768px 面板占满视口 | 平板/手机地图不可见 |
| D-7 | **标注字形外部依赖**: glyphs 硬编码 demotiles.maplibre.org，默认标注黑色无光晕 | 内网部署导出缺字；暗色底图标签不可读 |

## 6. 重构建议（按 ROI 排序）

1. **工具目录预算化**（P-1/P-2 合解）: tier-1 收缩至 ≤15 + schema 总字节硬预算（30KB）+ 回合中后段按 plan tool_family 收窄 + 粘性域上限 4。
2. **LLM 调用层角色化**: `resolve_llm_config(role)` 单一解析点（execution/planner/title/spatial 四角色）→ `call_llm*` 返回带 usage 的 LLMResult → TurnEvidence 记账 → connect-phase-only 重试。这是 Model Registry 的最小可行前体，零协议变更。
3. **harness 指导投影**: webgis_map_intent/product 返回有界 `guidance` 投影（capability→resolved_tool 表）并加入 _PRESERVED_META_KEYS，恢复"计划裁决到达 LLM"链路。
4. **工具发现性守护测试**: 所有 tier-2 工具 domains ⊆ DOMAIN_KEYWORDS 键集且非空——一处断言堵死 B-1/B-3 类整族缺陷。
5. **错误契约统一**: is_error_like_result 补 `success is False + message` 第四族；存量渐进迁移 std_error_response。
6. **等时圈 STRtree + 图层清单上限 + HUD 空闲 CSS 化**: 三个小改动，性能收益立现。

---

## 7. 第三阶段: 新一代 GIS Agent Harness 重设计分析

### 7.1 目标架构与现状映射

```
User
 ↓
Intent Understanding        ✅ 已有确定性 resolver（intent.py），需 tier=1 可见 + 产物不被裁剪
 ↓
Task Planner                ◐ CanonicalPlan + harness 附着；需: resolved_tool 绑定进 PlanStep、高置信短路
 ↓
GIS Knowledge Layer         ◐ 已有 recipes/product_templates/capability 注册表（#905）；缺 GIS Ontology 统一词汇
 ↓
Template Engine             ✅ 组件化模板已落地（components.py: 指北针/色条/图例可寻址组件）；缺模板质量回归基线扩展
 ↓
Tool Router                 ◐ tier+关键词+粘性；需: 硬预算 + cost 先验 + plan 感知收窄
 ↓
Execution Engine            ✅ 并行工具波 + durable jobs；需: 轮内上下文折叠
 ↓
Validation Agent            ◐ cartographic 评估 + repair_action 已闭环；缺数据级 validation 前置
 ↓
Map Renderer                ✅ MapSpec 单一事实源 + MVT；缺图例量化刻度/标注光晕等读数保真细节
 ↓
Feedback Loop               ✅ ACK + system-message-bridge；缺用户级中断回路（停止按钮）
```

**结论**: 骨架七层中五层已存在且成熟，真正的升级路径不是重建，而是**打通三条断链**:
- **断链 1（意图→裁决）**: harness 计划的 capability→tool 裁决被 slim_tool_result 丢弃（B-2）——修复后 Planner 才真正"看得见"自己的计划。
- **断链 2（计划→执行绑定）**: PlanStep 不携带 resolved_tool，打勾退化为通配（Agent1#7）——修复后执行与计划对账。
- **断链 3（用户→执行中断）**: 后端有 task_cancelled 语义，前端无触发 UI（F-1）——修复后 Feedback Loop 完整。

### 7.2 GIS 知识库设计（在现有注册表之上）

现有: `app/services/gis_harness/`（intent/recipes/planner/components）+ `app/lib/gis/` 算法注册表（#905 引入 artifact/capability/algorithm registries + deterministic resolver）。
增补方向（渐进，不推翻）:
- **GIS Ontology**: 地点/对象/任务/量纲四元词汇表统一（现在 intent.py 城市表与 DOMAIN_KEYWORDS 各维护一套）——一份 YAML，双端消费。
- **Cartographic Rules**: 色带适用域（顺序/发散/分类）、分类法选择（Jenks vs 分位数决策表）、注记密度阈值——进 recipes 的资格复检。

### 7.3 制图模板库

已实现: Template Registry（兼容性感知目录 + selector，commit 51f5dc6），模板=组件组合（BaseMap/Layer/Legend/ScaleBar/NorthArrow/Title/Annotation/ExportConfig 均可寻址替换）。
**本轮补齐**: 热力图例量化刻度（B-13）、色条统一格式化（B-14）、标注默认光晕+字形可配置（D-7）——模板组件的"读数保真"缺口。

### 7.4 Algorithm Library

已实现: SpatialAlgorithmRegistry（#905: buffer/overlay/cluster/hotspot/interpolation/classification/change-detection/terrain 均含 schema+complexity+resolver）。
**本轮补齐算法正确性尾巴**: Moran 权重对称化（B-9）、变化检测网格对齐（B-7）、DEM 哨兵前置掩膜（B-8）、nearest id 语义（B-10）、等时圈投影索引（P-3）。

### 7.5 Model Library 路线（D-2 解法，三步走）

1. **Step 1（本轮落地）**: LLMResult（message+usage）+ stream_options.include_usage + TurnEvidence 记账 + connect-phase 重试 + resolve_llm_config(role) + Settings 化超时/温度/max_tokens + Pi env 映射 + 标题调用修复。
2. **Step 2（近期）**: ModelRole→ProviderConfig 注册表; Anthropic 原生/Ollama 适配位; CostManager 汇聚 usage 出每会话成本。
3. **Step 3（远期）**: ModelSelector 按任务路由（简单查询→小模型 / 复杂规划→reasoning / 代码→coding），PerformanceTracker 用 tool_metrics 同源 JSONL。

---

## 8. 发现总账与 Issue 映射

本轮共确认 **40 项**独立问题（P0×1 / P1×10 / P2×14 / P3×15），全部转为 GitHub Issues（labels: architecture/performance/algorithm/harness/tools/models/frontend + 优先级）。修复按 6 个批次执行，每批独立 worktree + 资源受限本地测试 + `fix(category)` 格式 commit。

*逐项明细见对应 Issue；本报告为索引与架构级综合。*
