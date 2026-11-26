# ADR-0300: GIS 情境化多方案候选 Harness（G05）

日期：2026-11-25 · 状态：已实现（PR: feat/contextual-gis-scenario-harness-20261124）

## 背景

同一 GIS 任务中，用户常想"再试一种分级/配色/阈值"而不破坏当前地图。既有能力：
- MapSpec 事务机器与 user-wins 守卫（ADR-0054/0057/0058/0072）；
- What-If 场景分支（ADR-0193）：派生会话 `{parent}__wif_{branch}` 的 fork/干预/回滚/对比，
  但**无工具面、无 API、无基准漂移失效、无采纳回父会话的事务**——是服务层"死能力"；
- GISSituation 11 分区投影（ADR-0252）、工作上下文（ADR-0206/0215）、turn context receipt
  （ADR-0295）、situated workbench 交互闭环（ADR-0285）；
- 候选审阅/锁定的局部先例：G45 坐标操作（计划→服务器签发锁定→执行），语义不通用。

## 决策

### D1 编排层，不是第二套候选体系

`app/services/scenario_harness/` **只做编排**：隔离机制复用 ADR-0193 分支（唯一一套），
权威状态只有父会话 MapSpec 一份。候选 = "可重放提案（DeltaIntent 白名单序列）+ 分支产物
refs"，注册表存父会话 map_state 独立键（`_scenario_candidates/_baselines/_locks/_adoptions`），
与 whatif 的 `_whatif_branches` 分键共存（其 pydantic 校验会丢弃未知字段，分键防 schema 干扰；
分支 id 强制 `scc` 前缀并跨注册表查重，防派生会话命名空间撞车）。

### D2 共享基准绑定与失效分级（WP2/WP3）

`ScenarioBaseline` 一次捕获、多候选引用：MapSpec revision+指纹、数据集 content-revision 锚、
目标摘要 digest。漂移观察（`BaselineMove`）确定性逐锚披露；候选影响分级四档：
`current / recompute_required（在分支上局部重算）/ adopt_blocked（父会话已改相关字段，需裁决）/
unresolvable（数据版本推进，旧结果不可采纳）`——不做虚假综合评分，不可比如实标注。

### D3 用户锁定与三方合并（WP1/WP4）

`ScenarioLock` 是**用户显式任务级 pin**（user-wins 守卫只在 mutation 当刻从 provenance 推导
presentation ownership；锁定覆盖"配色定死/分母固定"等守卫不可知的语义）。释放仅限 user origin。
采纳 = 确定性三方字段合并：`base_values`（提案时观察值）vs 父会话当前值——相等才重放；
base 缺记录保守 `agent_conflict`（诚实优先于便利）；同字段漂移绝不 last-writer-wins。
事务：采纳前 `CheckpointIntent` 锚（兼回退锚）→ 链式 CAS 逐条重放（批通道现状仅接受
presentation 意图，引擎无混合批事务）→ 任一失败/superseded 立即回滚锚点——等价单事务，
绝不半采纳。回执逐项裁决落账，全拒绝也是合法决议；幂等键绑定锚 revision。

### D4 对话分支 ≠ GIS 候选

Pi session tree 是对话分支；GIS 候选是工作区事务分支。本 harness 不建 Pi 分支、不复制聊天
历史；指代解析（"上一个方案"）走注册表稳定 id + 创建序（`resolve_scenario_reference`），
解析不了返回 None 最小澄清——不依赖 LLM 回忆。

### D5 工具面与 UI 同链

`webgis_scenario_*` 8 工具与 `/chat/sessions/{sid}/scenario/*` 路由调**同一服务函数**、
过同一守卫（CAS + user-wins + 锁）——UI 采纳不因走 HTTP 绕过任何检查。GISSituation 的
`analysis.scenarios` 为 additive fact（default unknown，零改动兼容；投影只读、失败降级 unknown）。

## 边界（不做什么）

不复制聊天全文、不做第二世界状态、不平行权威 MapSpec、候选不写主地图/发布/导出
（派生会话 id 过不了 `require_owned_session`，API 面天然隔离）；候选数据零 payload
（注册表只存摘要/有界 delta；大数据留在会话 refs）。MapSpec schema 零改动。

## 证据

- 单测：`tests/unit/scenario_harness/`（契约/编解码/锁定/投影/基准/候选/失效/对比/采纳/回退，
  真实引擎播种零 mock）+ `tests/unit/test_scenario_harness_routes.py`（路由）；
- 前端：`frontend/components/workbench/scenario-panel.test.tsx`（4 旅程）；
- 端到端：`tests/unit/scenario_harness/test_scenario_journey.py`（"成都小学分布"多轮闭环）。
