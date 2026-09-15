# AGENT-04 审查备忘录：Data Scout & GeoCompute 专精子智能体

- 分支：`agent/04-specialist-agents-pack-data-compute`（worktree
  `../webgis-wt-agent-04`，基线 origin/master @ 3eb2cc6a）
- 日期：2026-09-14
- ADR：`docs/adr/0188-specialist-agents-data-compute.md`
- 规格：`docs/dev/data-compute-agents-spec.md`
- 测试：`tests/unit/test_specialist_data_compute.py`（23 用例全绿）

## 1. 变更清单

| 文件 | 性质 |
|---|---|
| `app/services/agent_swarm/__init__.py` | 新增：包入口 + import 期幂等角色注册 |
| `app/services/agent_swarm/contracts.py` | 新增：SpatialProfileRef（8KB 闸）、DataScoutReport、ComputeRequest/Submission、VolumeEstimate |
| `app/services/agent_swarm/base.py` | 新增：BaseSpecialistAgent（RBAC/心跳/墙钟熔断/委派）+ 两个 typed 错误 |
| `app/services/agent_swarm/data_scout.py` | 新增：DataScoutAgent（声明式回退链、D1 descriptor、实体对齐） |
| `app/services/agent_swarm/geocompute.py` | 新增：GeoComputeAgent（durable 任务图、UTM 防御、提货券） |
| `app/services/agent_swarm/registry.py` | 新增：SPECIALIST_REGISTRY + ensure_subagent_roles_registered（幂等、冲突显式失败） |
| `tests/unit/test_specialist_data_compute.py` | 新增：T1–T16 测试矩阵，23 用例 |
| `docs/adr/0188-…md`、`docs/dev/data-compute-agents-spec.md` | 新增：设计决策与实现规格（先于实现产出） |

## 2. 工程纪律验证（验收核心）

### 2.1 Celery First — 绝对遵守

- 编排器 `plan_computation()` 进程内只做：构造 `ExecutionNode`/`ExecutionPlan`
  → `validate_plan()` → `submit_durable_job`（提交缝可注入）→ 提货券组装。
  模块内**不存在任何算子执行代码**（无 shapely/rasterio/PostGIS 调用）。
- 任务图全部节点 `policy=DURABLE_JOB`（T10 逐节点断言）；任务体是既有
  `geocompute.tasks.run_geocompute_node` durable 路径（进度落库、取消、
  幂等、input_refs 交接全复用，零新表零新运行时）。
- 上游数据经 `input_refs={"input": dataset_ref}` 交接（T11），编排器与
  提交参数中**无原始几何**（`coordinates`/`geometry`/`features` 数据断言）。
- 测试桩（`RecordingSubmitter`）证明编排路径可在无 Redis/DB 环境完整
  走通 —— 默认提交缝才是唯一触 Celery/DB 的点。

### 2.2 Zero Big Data in Context — 绝对遵守

- 结果唯一通货 `SpatialProfileRef`：`to_json_bytes()` 序列化硬上限
  `SERIALIZATION_BUDGET_BYTES = 8KB`（T11/T12 正常路径断言 < 8KB）。
- 超限语义分级：metadata 按「值体积」从大到小逐键丢弃 +
  `truncated=True` 诚实标注（T13a）；无 key 可截 →
  `SpatialProfileTooLargeError` typed 失败，绝不静默裁剪载荷字段（T13b）。
- `summary`（≤600 字符）与 `notes`（≤8 条 × 200 字符）写入即截断；
  `ref_id` 是确定性取货位 `gc-{plan_id}-{尾节点语义指纹}`（T12 合法性
  断言），结果本体存 session ref / durable job 行，经
  `get_execution_run` / job 轮询解析 —— 上下文永远只见提货券。

### 2.3 RBAC 工具白名单 — fail-closed

- `data_scout`（19 工具，ADS/Fetch 面）与 `geocompute`（24 工具，计算
  分析面）名单**不相交**（专项不变量测试）；越权调用 typed
  `SpecialistToolDeniedError`（T5：data_scout×`execute_execution_plan`/
  `buffer_analysis`/`h3_binning`；T6：geocompute×`connect_data_source`/
  `apply_layer_style`/`create_thematic_map`/`webgis_layer_remove`）。
- dispatch 边界复用既有 `AllowlistDispatchRegistry`：白名单外结构化
  `TOOL_NOT_ALLOWLISTED` 且**绝不执行**（T7 断言 executed 为空）。
- 构造期存在性校验：白名单引用 registry 中不存在的工具 → 构造即
  `ValueError`（T4）—— 防止名单腐烂成静默放行。

## 3. 既有不变式一致性（未破坏项）

- **不是第二 agent 框架**：专家执行宿主仍是 `SubagentDispatcher` +
  独立 ChatEngine；本包只加角色档/白名单/确定性编排方法；
  `delegate()` 仅注入提示词边界与 `role=` 参数。
- **角色注册零侵入**：`ensure_subagent_roles_registered()` 幂等注入
  `SUBAGENT_ROLES` 并重跑 `validate_subagent_role_registry()`；spawn
  工具描述动态渲染自动纳入（专项测试）；同名异档冲突显式失败。
  既有 87 个 subagent/fallback 测试全绿（零回归）。
- **D1–D4 契约复用**：Data Scout 产出既有 `D1DatasetDescriptor`，
  降级留痕复用 D3 `FallbackDecision` / D4 `AcquisitionFact`；
  `execute_fallback_chain` / `resolve_chain` / `CircuitBreakerRegistry`
  原样复用（熔断场景 T2 直接对模块级 breaker 置 OPEN 验证自动跳链）。
- **诚实默认（ADR-0094）**：CRS/bbox/行数未知即 None；
  `estimate_volume` 三级降级 + `method` 披露（T8）；全链失败
  `ok=False` 不虚构 descriptor（T3）；UTM 推导无 bbox 诚实跳过（T9）。

## 4. 已知限制与后续工作

1. **SpatialProfileRef v1 冻结**：追加字段必须可选 + 升级测试
   （与 data_fabric.contracts 同门规则）；`plan_built` 字段为进程内
   便利通道，不参与序列化。
2. **适配器接线**：默认 `adapter_factory` 只接 `local_file` /
   `stats_api` 两个协议（任务书指定桥接对象）；其余 fabric 协议
   （postgis/ogc_api/stac…）按需在工厂补行，未接协议 typed 失败走回
   退链，不静默。
3. **白名单维护**：名单是手工声明，构造期只校验「存在性」不校验
   「类目语义」；类目级禁区由测试断言守卫。工具目录演化时需同步
   更新（存在性校验会在 CI 期暴露腐烂）。
4. **probe 采样上界**：字段对齐观察窗 `features[:64]`、descriptor
   fields ≤32 —— 轻量探测定位，全量 schema 仍应走 `profile_dataset`。
5. **后续候选**：`run_geocompute_node` 结果回填 `SpatialProfileRef`
   的 completed 态投影（当前提交态 `submitted` 已可轮询 job 行）；
   data_scout 域工具（`query_local_yearbook` 等）按需扩充白名单。

## 5. 环境与验证记录

- 环境偏差：worktree 内直接使用全局 anaconda Python 3.13（依赖齐全：
  shapely/pyproj/celery/pytest；`app` 包按 cwd 解析，`import app`
  验证指向 worktree 副本），未重建 `.venv`（`pip install -e .` 在此
  布局下冗余）。
- 验证命令与结果：
  - `python -m pytest tests/unit/test_specialist_data_compute.py -v`
    → **23 passed**；
  - `pytest tests/unit/test_subagent_team_v4.py test_subagent_roles_v2.py
    test_subagent.py test_subagent_budget_class_v6.py
    test_subagent_accounting_v5.py test_data_fabric_fallback.py`
    → **87 passed**（零回归）；
  - `ruff check app/services/agent_swarm/
    tests/unit/test_specialist_data_compute.py` → **All checks passed**。
- TDD 过程：测试先于实现产出并确认先红（ImportError → 逐步转绿）；
  实现期共修复 3 个测试期望错误（`SourceRegistryError(file, message)`
  双参签名、UTM zone 手算笔误 32759、穿透分支返回形状），实现侧修复
  1 个 pydantic v2 私有属性陷阱（`ClassVar` 注解）。
