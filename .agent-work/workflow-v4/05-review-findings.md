# Workflow V4 — Review Findings

## Round 1（架构/正确性/回归，独立 Explore reviewer）

范围：1be24fac..3c6273b5（8 commits）。Reviewer 实跑 69 个 V4 单测 + 74 个基线契约测试，并对关键语义做真实 registry 只读探针。

### 红线核对结论（reviewer 确认）

- 单一事实源 ✅（无 V3 词表复制；ACQUISITION_ALTERNATIVES 为声明式加法投影，观察项）
- 确定性 ✅（双编译同指纹实测）
- COMPILER_STAGES 零漂移 ✅（基线文件零改动 + 测试锁定）
- 生产零漂移 ✅、产物有界 ✅

### Findings 与处置

| ID | 级别 | 摘要 | 处置（d320ed65） |
|---|---|---|---|
| MAJOR-1 | MAJOR | 编译管线喂 resolution 态（bound/external/unresolved/degraded）给资格引擎（词表是资格五态）→ role_fit 退化常数、角色阻断死码 | `_derive_role_states`：显式映射 + wf_profile 在场时重放 qualify_workflow_data_roles（blocked 真事实可达）✅ |
| MAJOR-2 | MAJOR | REQUIRES_TRANSFORM 被当硬拒绝 → 地理 CRS 下整族方法全灭 | precondition 四态归一（pass/unknown/transform/fail）；transform 软惩罚 0.6 + 披露 ✅ |
| MAJOR-3 | MAJOR | primary_output 可指向不存在节点（语料 7 查询 4 个悬空）且校验器不查 | 只指向真实 emit 节点 + `TYPED_DAG_DANGLING_PRIMARY_OUTPUT` 检查 ✅ |
| MAJOR-4 | MAJOR | 方法未声明 requires_roles 时 analysis 节点与数据输入全断连（合法 query 稳定产出 blocked DAG） | 零入度 analysis 节点兜底接线全部角色供给 ✅ |
| MAJOR-5 | MAJOR | diff→recompute 桥接 3/7 no-op；parameter_change 永不产生；边不比较 | target 投影真实节点（方法→owner 节点/义务→主输出/移除→存活下游）+ parameter 维 + 边级 diff ✅ |
| MAJOR-6 | MAJOR | recompute 参数 owner 读生产从不产出的节点形状（死路径，测试假形状掩盖） | node.parameters 由 compiler 注入 bounded DAG；测试改真实形状 + 新增生产可达测试 ✅ |
| MINOR-1 | MINOR | 部分声明机制未接线（subworkflow/多源继承/semver 推进） | 观察：分阶段上线设计意图；docstring 已如实。follow-up |
| MINOR-2 | MINOR | evaluation 死条件分支 | 已修 ✅ |
| MINOR-3 | MINOR | 注册期校验承诺与实现不符（requires_roles/geometry 词表） | validate() 补词表校验 ✅ |
| MINOR-4 | MINOR | 事件循环内同步二次编译 ~300ms | 成本注记写入 docstring（memo 命中后毫秒级；失败隔离）✅ |
| MINOR-5 | MINOR | Plan.workflow_v4 会话内易失 + 规划期无 profile 时资格中性 | 与 gis_intent 同模式无兼容风险；证据加 `qualification_basis` 审计披露 ✅ |
| MINOR-6 | MINOR | semver 接受尾随换行/前导零；next_version 非法 change 静默 | fullmatch 严格化 + 非法 change raise ✅ |
| MINOR-7 | MINOR | 私有符号跨模块引用 | `geometry_category` 公共别名 ✅ |
| MINOR-8 | MINOR | 测试条件断言静默零断言 + 用例名不副实 | 改无条件/自洽断言 + 更名 ✅ |
| MINOR-9 | MINOR | blocked 阶段不截停且包不带分级 | `blocked_stages` 入包 compiled form ✅ |
| MINOR-10 | MINOR | pydantic v4_stages 别名脆弱 | 显式回赋 ✅ |

**Round 1 verdict**：需修复后合并 → 6 MAJOR 全修 + 8 MINOR 顺手修，922 绿。

## Round 2（性能/安全/可维护性/UX，独立 Explore reviewer）

范围：origin/master..HEAD（9 commits，含 Round 1 修复）。reviewer 实跑全部 V4 单测 + 只读探针，确认 Round 1 六 MAJOR 修复落地且未引入新问题。

### 红线核对结论（reviewer INFO 确认）

- 性能复杂度 ✅（全模块 O(V+E)/O(N log N)，registry 全进程单例，无意外 O(n²)）
- 内存 ✅（全部产物有界；图构造期截断；截断悬空由 TYPED_DAG_* 诚实暴露）
- 安全 ✅（query pydantic 400 界；semver fullmatch 无 ReDoS；profile 注入面类型检查只读；无泄露）
- 多租户 ✅（无 session 数据/凭据进证据；无跨会话读取）
- unknown ≠ 满足 ✅ 全程保持；可维护性 ✅ 无循环依赖；ADR-0118 承诺与实现一致 ✅

### Findings 与处置（c26df354）

| ID | 级别 | 摘要 | 处置 |
|---|---|---|---|
| MAJOR-1 | MAJOR | CPU-bound 全链编译在事件循环上同步执行（tool 注册为 async → ASYNC 策略） | tool 改 sync def（registry 自动 THREAD）；orchestrator 证据路径 asyncio.to_thread ✅ |
| MINOR-1 | MINOR | tool 与 orchestrator 资格披露不对等，note 措辞过强 | tool 补 qualification_basis + 条件化 note ✅ |
| MINOR-2 | MINOR | _MAX_COMPILED_FORM_BYTES 死常量 | emit 时运行时强制（>64KB raise）✅ |
| MINOR-3 | MINOR | integer 参数接受任意浮点 | 严格 int 校验（bool/float 拒绝→回落默认+披露）✅ |
| MINOR-4 | MINOR | validate 死分支 + 循环内重复 import | 谓词在场跳过 task set + import 提升 ✅ |
| MINOR-5 | MINOR | Plan.workflow_v4 无渲染消费方 | ADR 标注渲染面为 follow-up ✅ |
| MINOR-6 | MINOR | orchestrator 输入无长度界（tool 有 400） | user_message[:400] 对齐 ✅ |
| MINOR-7 | MINOR | 恒真断言 + memo 收益未锁定 | 真异常注入测试 + second ≤ first×1.5 断言 ✅ |
| MINOR-8 | MINOR | ADR 模块计数 10→实际 11 | 已修 ✅ |
| MINOR-9 | MINOR | "dem" 子串过匹配 demographic | 边界化 " dem"/"dem "（实测 demographic 不再误路由，DEM 查询保持命中）✅ |

**Round 2 verdict**：需修复后合并（1 MAJOR）→ 全修，986 绿。

## 最终状态

- 两轮独立 review 完成；BLOCKER/CRITICAL/MAJOR 全部修复；
- 全部 MAJOR 修复有具名回归测试锁定。
