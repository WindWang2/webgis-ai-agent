# Typed Tool Surface V1 — Decisions（决策日志）

延续仓库 `<line>-decisions.md` 约定。每条决策 = 背景 → 选项 → 决定 → 理由 → 证据锚点。

## D1 — 任务定位：不做第二套动态面，做三个真实缺口

- **背景**：任务书按"master 只有万能 webgis_execute"假设写了 T0-T9 九个波次；实测 master（ADR-0103 #1150、0104、0119 系列）已落地 spawn dump v2（316 工具注册超集）、per-turn `WEBGIS_ACTIVE_TOOLS`→`setActiveTools`、V3 选择器（reasons/dropped/confidence/abstain）、V6 弃权、proxy wrap-reject。
- **决定**：T1/T3/T5/T6 视为"已实现，测试钉住"；开发量集中于
  1. per-turn 激活面 schema 字节预算 + 披露面（T2 残留）；
  2. Pi 边界 pre-dispatch strict validation + 机器可读 typed error（T4/T7）；
  3. surface 级 metrics + 回归门（T9）+ parity 测试（T8）。
- **证据**：`docs/dev/typed-tool-surface-recon.md` §3 重叠矩阵；probe 实测（327 registry / 316 超集 / native7=17,277B）。

## D2 — 字节预算作用在"激活名单"上，不动 spawn dump

- **背景**：模型每 turn 看到的 schema 字节 = 激活集（native7 + proxy + 动态名单）。V3 `project()` 有 byte_budget，但 Pi 路径只消费 `select()` 名单。dormant schema 压缩被既有纪律明确禁止（`pi_native_surface.py` 注释：压缩会偏离 registry 真相）。
- **选项**：(a) 改 spawn dump 压缩 dormant schema —— 违反既有纪律；(b) 在 `compute_turn_active_tools` 选名单时按 `registry.schema_size()`（#1062 缓存）预算裁剪 —— 只影响投影决策，单一真相不动。
- **决定**：(b)。预算默认 **32KB**（与 V2 ToolCatalog 的 24KB 预算同量纲；native7 前门实测 17.3KB、中位 schema 528B——32KB 保留 ADR-0103 典型 30 工具 turn 不变、只裁大 schema 病理尾；24KB 会把典型 turn 砍到 ~12 个动态工具，过于激进），env `PI_SURFACE_BYTE_BUDGET` 可调，0=off 回归旧行为。native7 + proxy 恒在前门不裁；只裁动态候选（从低分端丢弃，reasons 记 `byte_budget`）。
- **风险**：预算裁剪可能把检索高分工具挤出 —— 有 `list_available_tools` 两跳通道兜底，与 V2 同语义。

## D3 — pre-dispatch 校验闸采用"确定性分层"，零误拒优先

- **背景**：registry `_dispatch_impl` 的权威校验发生在 ref 解引用**之后**（`ref:` 游标与会话别名都会把字符串叶替换成任意载荷），直接在边界做 `model_validate` 会误拒合法的 ref 传参（false reject 打断真实用户流 = 比 late reject 更差）。
- **选项**：(a) 边界全量 `model_validate` —— 有误拒面；(b) 边界先做会话 alias 查表再校验 —— 多一次 HMGET、且仍难完备；(c) 确定性分层：只拒绝"registry 注定拒绝"的输入。
- **决定**：(c)。规则（每条都有 parity 论证）：
  1. 先跑同一 `normalize_tool_arguments`（同一声明表，kebab→snake + 别名折叠，键面与 registry 后续视角一致）；
  2. unknown-field 拒绝（#828 同语义：`model_config.extra != "allow"` 时；归一化后键不变——解析只换值不加键；capture_ref_of 只注入已声明字段）；
  3. required 缺失拒绝（解析不删键；capture 注入只填可选字段）；
  4. 容器性类型硬错拒绝（dict/list 值不会因解析变成标量；int/float/bool 注解收到 dict/list/非字符串标量 → 注定无效）；
  5. 字符串值一律"可能被解析替换"→ 不做类型/enum 误判（`ref:` 前缀与会话别名都可能命中）；
  6. 参数树**无任何字符串叶**时（解析恒等、语义与 registry 校验时完全一致）升级为全量 `model_validate` —— 零漂移零误拒的严格档；
  7. oversized（`_is_args_oversized` 同门）不做 6，只做 1-4（registry 对 oversized 本就旁路深校验）。
- **决定**：闸失败返回**独立 typed error**：`details.error="schema_validation_rejected"`、`code="SCHEMA_VALIDATION_REJECTED"`、`issues=[{path,message}]` 机器可读 + 修复证据；HTTP 层不伪装成工具业务失败（不进 harness_failure 业务分类、不记 `tool_failed` 会话事件）；registry 校验仍是最终权威（闸是前置快路径，不是替代）。
- **落点**：`agent_pi_bridge._dispatch_tool_bound` 在 resolve/classify + 存在性 + tier 检查之后、`ToolDispatchService.dispatch` 之前（dedup/wave 排队之前）——非法调用不再占用 wave 槽位排队。校验器实现独立模块 `app/services/chat/pi_input_gate.py`，registry 新增 additive 公开访问器 `args_model(name)`。

## D4 — 校验语义零漂移的实现约束

- 复用 `normalize_tool_arguments`（argument_normalization 公开函数）与 registry 的 oversized 判据，**不自造第二套校验规则**；全量档直接用 registry 注入的同一 Pydantic model（新 `args_model()` 访问器）。
- 负例"schema generator 漂移"由 parity 测试钉住：同一批（合法/非法）参数在 gate 与 registry 直派下结论必须一致（gate 只许少拒，不许多拒；registry 拒的 gate 必拒——对 1-4 类）。

## D5 — metrics 面与回归门（T9）

- 新 `app/services/chat/pi_surface_metrics.py`：进程级单调计数器（invalid_tool_name / schema_validation_rejected / proxy_wrapped_calls / direct_surface_calls / last surface bytes + dropped 计数）+ `snapshot()`；`/api/v1/metrics/digest` additive 段 `pi_surface`（admin 门沿用）。
- Stage.TOOL_SURFACE 链发射 additive 字段：`surface_bytes` / `budget_dropped`（观测面，绝不阻断）。
- 回归门测试：golden fixture 任务断言（激活集含必达工具、无 tier-3、字节 ≤ 预算、gate 拒绝延迟不进 wave 队列）。

## D6 — 不做的事

- 不改 `ToolDispatchService` 核心语义（闸在 bridge 层，service 无感）；
- 不改 `index.mjs`/`index.ts` 扩展协议（marker + MAX_ACTIVE=48 已够；避免 .ts/.mjs 双写漂移面）；
- 不把 327 工具全部常驻激活；不删 proxy；不动 tier3_confirmed 闸；
- 不吞 #1270（其文件面与本线零交集）。

## D7 — ADR 与文档

- 新 **ADR-0180**（下一个空闲号，Subagent A 核对 master=0179、无在途占号）：记录 D2/D3/D5 三个增量契约。
- 更新 `docs/agent-runtime/tool-surface.md` 的 Pi 动态注册面小节。
- 测试锚点：`tests/unit/test_pi_surface_budget.py`、`tests/unit/test_pi_input_gate.py`、`tests/unit/test_pi_surface_metrics.py`、`tests/unit/test_pi_surface_parity.py`。

## D8 — Review 教训预注入（来自 #1271/#1272 review）

- bandit 只认 `# nosec B608`，不认 `# noqa: S608`（#1272 教训）——本线无 SQL 拼接，如遇动态 SQL 直接避免；
- ruff F401 未用导入在提交前自检（#1272 教训）；
- 迁移面：本线无 Alembic 变更（无 schema 触碰），迁移门不适用；
- 前端类型声明：本线不改 frontend（零 TS 面）。
