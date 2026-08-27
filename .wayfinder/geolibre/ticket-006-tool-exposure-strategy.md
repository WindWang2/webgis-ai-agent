# Ticket: 算子子集暴露与 ToolRegistry 分类策略

**Label**: `wayfinder:grilling` | **Type**: HITL | **Status**: closed
**Blocked by**: ticket-001-whitebox-wasm-consumption.md, ticket-004-data-path-decision.md

## Question

1000+ 算子中暴露哪些给用户/agent：按类目映射（先水文/地形/LiDAR/viewshed/kriging 等缺口类目）还是全量？暴露的工具如何在 ToolRegistry 中分类（tier、domain、light/medium/heavy 成本、执行策略标注为客户端）？agent 可见的参数 schema 如何生成（Whitebox 参数定义 → pydantic/JSON schema）？

## Resolution

**类目白名单 + manifest 动态生成 + 客户端预检护栏。**

- **暴露范围**：首批白名单类目 = 水文(~100) / 地形(~99，含 viewshed) / LiDAR(~65) / 插值家族 + 精选厚栅格算子，合计约 260 个；矢量基础类目不开（buffer/overlay/dissolve 等与现有 156 工具语义重复，agent 会选择困难）。类目内常用算子 tier 2（domain 触发载入）、长尾 tier 3（仅显式列举）；后续按需扩类目。PoC 不受白名单约束（验证物理，不验证暴露）。
- **注册方式**：`listManifests()` → 生成器动态注册（GeoLibre `manifestToWhiteboxTool` 同款范式）：manifest→JSON schema、类目→tier/domain/cost 缺省映射表 + 例外覆盖表、`wb_` 命名前缀防撞名、执行策略枚举新增 `client`（现有 inline/async/thread/celery 之外）。
- **体量护栏**：客户端喂 `/work` 前预检（矢量 ~10⁵ 要素 / 栅格 ~4000×4000），超限返回结构化错误，经现有 self-healing hints 机制提示 agent 回落服务端 Celery 路径。
- **Agent 接线方向性结论（供 ADR 引用）**：ToolRegistry 新增的 `client` 执行策略即接线锚点——agent 工具调用经 dispatch 下发浏览器执行，结果按结果回流决策回传挂层；SSE/WS 委托-等待回路与 tier 授权的详细设计归后续 effort。
