# HARNESS_OPTIMIZATION_PLAN — Harness 流程分析与优化设计

> 生成时间: 2026-08-24 · 依据: PROJECT_ARCHITECTURE_ANALYSIS.md + 五份专项审查(48 条发现)
> 原则: 项目已有大量资产(确定性 Intent/Recipe/Planner、durable jobs、MapSpec 闭环、MVT), 本方案**只补缺口, 不重建已有机制**。

---

## 1. 当前 Harness 流程定位

当前是**混合形态**, 两条路径并存:

```
Legacy 路径(默认):
用户 Intent ──→ [LLM 规划 make_plan] ──→ CanonicalPlan(步骤/工具)
                    ↑ additive 附着: gis_harness.resolve_map_request_intent
                    │ + recipes.select_candidates (仅挂 gis_intent/recipe_id 字段)
                    ▼
              [LLM 反思循环 × ≤60 轮] ←── 工具结果(失败带 correction_hint)回填
                    ↓ 每轮
              ToolCatalog.select_schemas(tier/domain 筛选) → LLM 选工具
                    ↓
              tool_pipeline 并行波 → ToolDispatchService → 159 工具 / Celery
                    ↓
              ref: 提货券 + SSE command → 前端 MapSpec reconcile → MapLibre

Pi 路径(USE_NEW_AGENT=1):
用户 Intent ──→ Pi 子进程(LLM 自主, promptSnippet 引导, 无 CanonicalPlan)
                    └─ webgis_execute 代理工具 → 同一 ToolDispatchService
```

**即: 既不是纯 `User→LLM→Agent→Tool→GIS→Render`, 也不是完整 `Intent→Planner→Workflow→Execution`;
真实状态 = LLM 规划循环 + 确定性 GIS Harness 旁挂(建议性)。**

### 优缺点

优点:
- 确定性意图/配方/组件层已存在且类型化(`app/services/gis_harness/`), 数据/渲染面(ref+MVT+MapSpec)成熟;
- 失败处理闭环(Exception-as-Thought、失败分类、no-progress 熔断、MapSpec rollback)。

缺点(对应审查发现):
1. **LLM 过度参与**: 高置信场景(寒暄、单工具直答、命中确定性 recipe)仍每回合固定付一次串行规划 LLM 调用(H-1);
2. **双路径 parity 缺口**: Pi 有 900s 整轮预算而 legacy 只有 60 轮上限(H-2); 非流式路径缺标题/决策日志(H-4); 失败一律折成 500(H-3);
3. **harness 前门可达性**: `webgis_map_intent` domains 只标 statistics/report, 邻近/可达性/变化检测类请求看不到它(H-8); `webgis_map_product` 重建计划丢 unavailable 能力与 project_verified 证据(H-9);
4. **缓存层级不全**: ref payload 每次全量 Redis GET + json.loads 多 MB(P-1); POI 单要素回填全量拉图层(P-2); 栅格瓦片路由无 ETag/single-flight(P-5);
5. **数据入口偏差**: 本地 POI bbox 查询按 fid 头部截断, 密度结论系统性偏斜(G-1); 行政边界 GCJ-02 无 crs 标注(G-2)。

---

## 2. 优化方案(逐模块)

### 2.1 GIS Task Planner —— 确定性短路(核心, 对应 H-1)

目标形态:
```
User Intent
 ├─ 高置信(命中 recipe / 单工具意图 / 寒暄) → 确定性计划, 0 次规划 LLM 调用
 └─ 低置信(复合分析/歧义) → LLM 规划(现状) + harness 附着(现状)
```
方案: 在 `plan_orchestrator.make_plan` 前加**确定性短路层**: `resolve_map_request_intent` 置信度 ≥ 阈值且 `select_candidates` 唯一命中时, 由 `MapProductPlanner.draft` 直接合成 CanonicalPlan(能力→工具经 CAPABILITY_TOOLS 解析, 步骤间依赖已知); 不满足则回落现有 LLM 规划。保留 LLM 反思循环作为执行期纠错。**收益: 命中场景每回合省 1 次规划调用(约 2-6s 串行延迟), 工具选择稳定可复现。**

### 2.2 GIS Template Engine —— 已存在, 补可达性(H-8/H-9)

`recipes.py + product_templates.py + components.py` 已实现"模板=图例/指北针/比例尺/标题/色带/标注/版面"。优化:
- `webgis_map_intent` 工具 domains 扩至真实覆盖域(proximity/accessibility/change_detection/distribution…), 让各主题请求都能看到前门;
- `webgis_map_product` 重建计划时透传 available_tools 与 project_verified 证据, 兑现诚实记录契约。

### 2.3 Tool Registry 优化 —— cost 元数据 + 版本语义(对应 E 类配套)

已有: schema 自动生成、tier、domains、execution_policy、timeout。补:
- 注册时声明相对 cost(cheap/medium/heavy)进入 `_metadata`, 供 ToolCatalog 排序与 harness 计划合成时预算控制;
- contract 变更时 bump contract_version(现状全库 cv1 从未变过, 元数据形同虚设)。

### 2.4 Workflow Runtime —— 补 parity(H-2/H-3/H-5)

durable jobs(状态落库/心跳/取消/幂等)已达标。补:
- legacy 回合加总时长预算(对齐 Pi 的 900s), 超时诚实收尾, 防止持会话锁以小时计;
- `/chat/completions` 非流式端点透传诚实失败分类(空补全/max_rounds/no_progress 不再折成 500);
- 规划阶段纳入抢占式取消 watch(当前取消要等 120s planner 返回)。

### 2.5 GIS State Manager —— 已达标, 补缺口(H-7)

SessionStore(16 操作)/MapSpecStore(checkpoint/rollback)/map_state(单调 seq)已覆盖"连续地图编辑"。补: Pi 路径失败回合也落库 user 消息(当前只有 completed 才存, 崩溃回合丢用户输入)。

### 2.6 Cache System —— 三级缓存补全(对应 P-1/P-2/P-5/P-6)

| 级 | 现状 | 缺口 → 方案 |
|---|---|---|
| Data Cache | L1 只盖 map_state/metadata; tool_cache 盖工具结果 | **ref payload 进程内 LRU**(P-1): 解引用命中时免全量 GET+loads(实测 50k 要素 171ms/次) |
| Analysis Cache | tool_cache(JSONL 事后) | POI 单要素回填复用已驻留的 spatial_index_cache 而非全量拉图层(P-2); observation 链去 3× 重复 get_map_state(P-6) |
| Render Cache | MVT LRU+ETag+single-flight 完备 | 栅格瓦片路由补 ETag/single-flight/鉴权缓存(P-5); API 层 GZip(P-7) |

### 2.7 Renderer 优化 —— 保守增量

MVT(>5000)+视口裁剪+增量 reconcile 已是正确架构, **不引入** Vector Tile 重构/WebWorker 大改(风险>收益, 已有 worker-bridge 卸载 diff)。仅做: map-panel 受控 viewState 移动期重渲染抑制(P-9)、GeoJSON 序列化去 pretty-print/字符串往返(P-7/P-8)。

### 2.8 数据入口正确性(对应 G-1/G-2, 制图可信度的前提)

- 本地 POI bbox/polygon 查询改为均匀采样截断 + truncated 披露贯穿 `try_local_osm_poi` 信封;
- `get_local_admin_boundary` 输出标注 crs 并提供 WGS84 转换出口, 消除 GCJ-02 混叠。

---

## 3. 实施批次(与 Issue 处理顺序一致)

1. **[harness]** H-1 确定性短路; H-2 回合预算; H-3 诚实失败透传; H-4/H-5/H-6/H-7 parity; H-8/H-9 前门可达性
2. **[performance]** P-1 ref payload LRU; P-2 单要素回填; P-3 harness 评估 O(n²); P-4 并行 gather; P-5 栅格瓦片; P-6~P-9
3. **[gis]** G-1 采样偏差; G-2 CRS 标注; G-3~G-9 算法质量
4. **[frontend]** U-1 样式面板入口; U-2 色条/比例尺叠压; U-3~U-9 交互缝隙
5. **[engineering]** E-1 lint 红(先修以恢复门禁); E-2/E-3 分层收口; E-4/E-5 env 模板; E-6~E-12

验证: 每类完成跑 `scripts/ci-local.sh --fast` + 相关 pytest marker(cartography/perf), 全绿后 commit 并关闭对应 Issues。

## 4. 明确不做(避免过度工程)

- 不替换双引擎架构为单引擎(Pi/legacy 双路径是既定 ADR 裁决, 只补 parity);
- 不引入新的规划 DSL/工作流引擎(durable jobs + CanonicalPlan 已覆盖);
- 不做后端栅格推送(违反 No Raster Push 红线);
- 不重写 MVT/渲染内核(已达标, 风险不对称)。
