# ADR-0140: 数据基础与生命周期治理 V9 — 统一生命周期策略引擎与质量规则引擎

- 状态：Accepted（foundation/data-lifecycle-v9 线交付；P0–P7 全量实现，
  本地验证）
- 日期：2026-09-11
- 关联：ADR-0135（Lakehouse V8）、ADR-0132（Federated Data Fabric V8）、
  ADR-0131（Platform V4 durable job）、ADR-0119（数据湖仓 V6/V7）；
  勘察依据 `docs/dev/data-lifecycle-recon.md`；迁移协议
  `docs/dev/migration-protocol.md`

## 背景

数据面计算能力（lakehouse cube、fabric 联邦、artifact LRU、COG 输出、
geocompute worker_cache）已经很强，但**治理层是薄壳**：

1. 五套过期/回收机制各自为政：lakehouse retention（`data_lifecycle/quota.py`
   + `lakehouse/dataset_retention.py` + `lakehouse_gc`）、fabric 物化 spill
   TTL、`artifact_cache` 磁盘 LRU、COG 会话目录（**无任何清理**——真实缺口）、
   `geocompute_worker_cache` 注册表 purge。没有统一的「数据对象生命周期」视图。
2. `data_quality`（493 行）/ `data_profile`（556 行）/ `templates`
   （118 行）三个本应是子系统的服务是薄壳；质量评估无落库报告实体、
   无 durable job 路径。
3. data-gc 已有同步 dry-run/execute 端点但无审批/回滚闭环。
4. Alembic 编号撞号两轮（0034×4、0035×3），流程性根因（无领号机制）未解决；
   `tests/test_alembic_metadata.py` 当时只断言 `target_metadata` 非空。
5. 结构债：`app/db/` 死包、`app/skills/` 性质不明、`app/tasks/` 无文档、
   `rs/spectral_engine.py` legend_spec 真 TODO。

## 决策

### D1 数据质量规则引擎（P1，migration 0046）

- **规则模型**：字典/YAML DSL（`data_quality/rules.py`）——`RuleSpec`
  （id/type/severity/params/enabled），规则类型封闭词表 16 类（≥15 门禁）：
  空值率、CRS 有效性、几何有效性（自交/退化/未闭合）、空间范围合理性、
  属性值域、主键唯一、外键引用、重复要素、字段类型漂移、时间序列断裂、
  nodata 比例、分辨率漂移、属性编码乱码、波段统计离群、拓扑邻接、几何族混杂。
- **判定纯函数**（`rule_functions.py`）：vector 走 features、raster 走
  band 统计；单趟有界扫描（默认 20k，durable 路径 200k）；拓扑邻接 O(n²)
  有 200 面硬帽，超帽诚实 `skipped`；shapely 缺席诚实降级；单规则异常隔离
  为 `error` 态，绝不炸整场评估。
- **双路径**：`POST /data-quality/evaluate` 同步小数据集（>20k 要素 413
  引导走 job）；`POST /data-quality/reports` 提交 durable job（`analysis_tasks`
  统一任务表，`submit_durable_job` 幂等）。`QualityReport` + `QualityRuleResult`
  落库（有界：≤64 结果行 / metric 投影截断）；大结果走 `result_ref` 指针。
- **恢复语义**：`run_quality_evaluate` 入口查同 (session, ref, ruleset)
  completed 报告即复用（worker 重试不重复评估）；载荷不可读 → `failed`
  报告行（诚实失败，不虚构 pass）。
- **修复建议管线**（`autofix.py`）：plan-only → dry-run（零改动预览）→
  apply（**new-ref 语义**：深拷贝产出新载荷，绝不覆写源；session 提供时经
  SEC-08 所有权守卫注册新 ref）。操作词表单一事实源 = `REMEDIATION_OPS`
  （reproject/repair_geometry/normalize/filter_null/filter_nodata），
  `RuleSpec.__post_init__` 词表校验漂移即 fail-fast。确定性修复：CRS 缺失
  附着/重投影（pyproj 缺席降级为 attach-only 并披露）、闭合环/剔除退化、
  latin1→utf8 重码、空值要素剔除。
- **观测**：`data_quality_rule_evaluations_total{rule,outcome}` /
  `..._seconds_total{rule}`（label 封闭词表，cardinality 有界）+ 报告复用
  / job 完成计数。

### D2 数据画像深化（P2）

- **统一画像**（`data_profile/unified.py`）：vector（字段统计 + 几何族 +
  时间候选）与 raster（波段统计 + 按波段 nodata 映射 + 分辨率）一个有界
  dict；矢量附 **H3 空间分布直方图**（`distribution.py`，cell 超预算自动
  降分辨率重聚合，分辨率下限 3，仍超则截断 + `truncated` 旗标）。
- **增量画像**（`incremental.py`）：可合并状态（逐字段 sum/sumsq/min/max/
  类型计数 + H3 直方图 + 几何族计数），追加批次只扫新要素；`merge_states`
  状态可加性有「增量合并 == 全量扫描」测试钉死；状态 JSON 可序列化。
- **画像 → 规则联动**：`suggest_rule_params` 从画像实测统计推导规则阈值
  （值域 = 观测 min/max ±10%、时间断裂字段 = 时间候选、CRS 缺席建议），
  产出 parse_rule_defs 兼容规则集 —— 规则引用画像统计做阈值，不拍脑袋。
- **缓存失效**：画像缓存接入 `ref_lifecycle` 单一失效权威（R6 观察者，
  `install_invalidation_hook` 幂等注册）；数据变更 → 画像过期；正确性
  从不依赖观察者被调用（修订绑定 + TTL 兜底）。

### D3 统一生命周期策略引擎（P3，migration 0047）

- **对象注册表**：五类对象统一登记 `lifecycle_objects`（kind 封闭词表：
  lakehouse_dataset / fabric_materialization / artifact_cache / cog_output /
  worker_cache；(kind, object_id) 唯一自然键）。**可重建投影**——事实源
  仍是各机制自己。
- **适配器只读红线**（`adapters.py`）：五个适配器只枚举/观测，无任何删除
  路径可触达既有机制。**行为保持（本 ADR 安全不动点）**：默认策略全
  `observe` —— 登记一切、分级、但零候选零删除；等价性验证 =
  `tests/data/test_lifecycle_policy_v9.py::test_default_policies_equivalent_to_status_quo`
  （逐 kind 断言 action=observe / policy_enabled=False / candidates=0）+
  适配器登记视图与各机制真实状态对账（文件数/行数/字节）。唯一新增清理
  能力是 COG（现状无清理），默认也只 observe。
- **分级**：last_used → hot/warm/cold（默认 24h/7d；无时间证据 → hot，
  不冤枉）。**复活**：`revive_object` 引用即预热（last_used 刷新 → hot）。
- **策略**：`lifecycle_policies`（name 唯一；action ∈ observe/stage_delete/
  delete；staging_hours 观察期）。`lakehouse_dataset` 在 V9 是 **observe-only**
  —— 真实删除必须走 lakehouse 自有 GC 的保护面（promotion 引用计数/快照
  指针/pin），策略 upsert 与计划创建双处拒绝。
- **端点**（新路由文件 `/api/v1/data-lifecycle`）：objects 分页、assess、
  policies CRUD（admin 写）、gc 计划闭环（下条）。读 optional、状态变更
  强制认证、审批/执行/回滚/物理删要求 admin（与 data-gc plan/execute 同纪律）。
- **观测**：assess 登记/候选计数（kind 封闭 label）+ 计划创建/执行字节/
  回滚计数。

### D4 data-gc 闭环（P5，同 migration 0047）

- **状态机**（`gc_plans.status`）：pending_approval → approve → approved →
  execute → executing → done/failed；reject/cancel 旁路；executing/done/
  failed → rollback → rolled_back。转移表封闭（`gc_plan._TRANSITIONS`），
  非法转移 409。
- **计划 = 证据**：dry-run 树（对象 → 依赖 → 回收原因 → 预估释放量）创建时
  固化（≤64 对象，超出截断 + 旗标）；执行器**绝不重新枚举、绝不扩圈**。
  plan_digest（稳定内容指纹）幂等：同 digest 未终态计划复用同一行。
- **staging 二段式**：执行 = 文件 rename 进 `data/.gc-staging/<plan_id>/`
  （同卷原子；保留相对路径）；worker_cache 行删除为逻辑回收（fail-open
  语义：miss → 重物化，无损）。观察期（staging_expires_at）内 `rollback`
  按清单还原；物理删只经显式 `purge`（终态 + 观察期已过，admin）。
  执行穿 durable job（eager 模式同步；中断恢复 = 幂等重入按清单跳过）。
- **与既有 data-gc 端点关系**：`/projects/{id}/data-gc/plan|execute` 零改动
  （其行为保持不变）；统一闭环是**新增**的跨机制治理面。

### D5 Alembic 防撞号（P6）

`scripts/alloc_migration.py` + `migrations/.alloc.json`（登记簿：分支段位
segments / 历史封号 legacy_merged / 单号预登记 reservations）：
领号（`--alloc`，段位校验 + 预登记 down_revision）→ 写文件 → `--check`
（revision id 全局唯一、前缀跨段位/段位内重复 fail、未领号先写文件 fail、
ScriptDirectory 单头 fail；merge revision 是唯一合法收敛）。
CI：db-migrations lane 增 `--check` 步骤；`tests/test_alembic_metadata.py`
补编号唯一性/单头/自证（重复编号副本必须 fail）断言。
流程文档：`docs/dev/migration-protocol.md`。本线段位 0046–0055（B 线
0036–0045 不动）。

### D6 templates 升格（P4，migration 0048）

- **版本化**：`template_versions` 不可变快照（version = max+1 唯一）；
  主表 payload 前移为 **effective**（继承链深合并）payload —— 既有
  apply_template 读路径零感知。
- **继承**：`parent_version_id` 链（可跨模板）；覆盖语义 = 子 payload
  深合并于父（dict 递归、标量/列表整体覆盖）；深度帽 16；新建行拓扑上
  不可能成环（无子边），守卫承担深度帽 + 脏数据防御。
- **失效迁移**：`deprecated_at` 标记不物理删 —— 兼容读取继续可用，
  响应显式携带 deprecated 标记。
- **Cartography V7 对齐**：payload 组件引用提取（显式 components 列表 +
  layout 布尔开关 → 组件 id 有界映射）→ `component_registry` 校验
  （unknown/runtime_unavailable = violation；deprecated = warning）；
  引用与违规随版本快照落库。REST 面挂新路由文件
  `/templates/{id}/versions*`（既有 `templates.py` 零改动）。

### D7 结构债（P7）

- `app/db/`：零 import 死包 → **删除**。
- `app/tasks/explorer/task_chain.py`：活跃消费方（explorer orchestrator +
  celery include）→ **保留并文档化**（`app/tasks/README.md`）；新任务
  惯例落领域服务包。
- `app/skills/`：文件系统数据目录（非空包）→ README 说明性质与消费方。
- `rs/spectral_engine.py:88` legend_spec 真 TODO：接通
  （`RasterAnalysisResult.legend_spec` → `to_llm_response`；下游
  tool_dispatch_service / chat 白名单 / project_memory 消费面已就绪）；
  行为钉子 `tests/unit/test_spectral_legend_spec_v9.py`。
- 顺带修复（环境健壮性，#1221 D-7 同类）：weasyprint 在 Windows 无 GTK
  时 import 期抛 `OSError` 而非 `ImportError`，只捕 ImportError 使 app
  import 链（含测试收集）不可达 → `publication_export.py` /
  `report_service.py` 守卫扩为 `(ImportError, OSError)`，两处 cartography
  测试的 `importorskip` 同修。

## 等价性验证（行为保持）

- 结构等价：适配器无删除路径（代码评审面）；默认策略全 observe。
- 行为等价测试：`test_default_policies_equivalent_to_status_quo`（五 kind
  候选=0）+ 适配器枚举与机制真实状态对账（artifact/cog/spill 文件计数、
  worker_cache 行、lakehouse 数据集）。
- 既有 parity 测试全数保持：`test_quota_retention_v5`（retention plan/
  execute parity + 四机制保护集一致）、`test_gc.py`（session GC + 磁盘
  孤儿清扫 verbatim 形状断言）、`test_wave1_promotion_gc`（单一 blob
  保护谓词）——本线零触碰其被测代码。

## API 形状预告（F 线协调）

- 新前缀：`/api/v1/data-quality`、`/api/v1/data-lifecycle`、
  `/api/v1/templates/{id}/versions`（全部新路由文件；既有路由签名零改动）。
- 信封/分页遵循 master 现状：列表 `Page[T]`（limit/offset），其余 dict 直返
  （success/status 键）；A 线 ADR-0138 合入后以其为准，届时由 F 线适配。
- org_id：本线新表全部带 nullable 占位列（无 FK），查询侧兼容读取 ——
  B 线合入后再补约束。

## 风险与回滚

- 默认策略 = 现状即回滚面：生命周期引擎在新表上运行，旧机制零改动；
  停用 = 不调用新端点（或迁移 downgrade 反序）。
- 迁移可逆：0046/0047/0048 全部 additive + downgrade 反序回滚（up/down
  往返测试覆盖）。
- GC 误删面：审批门（admin）+ 计划树固化（不可扩圈）+ staging 观察期 +
  回滚 + lakehouse observe-only；最坏情形 = staging 区未 purge，文件可
  手工还原。

## 验收对照（任务书 §5）

- ✅ alembic 单头 + 编号唯一性断言绿；0046 up/down 往返测试绿
- ✅ 16 类质量规则各有单测；QualityReport durable job 路径有恢复测试
  （复用 + 载荷缺失诚实失败）
- ✅ 五类对象全部登记注册表；默认策略与现状行为等价（本 ADR 附录 +
  test_default_policies_equivalent_to_status_quo）
- ✅ gc 审批状态机全转移路径测试（含 reject/cancel/非法转移 409/中断恢复
  幂等重入/回滚/观察期 purge）
- ✅ 模板版本化/继承契约测试绿；V7 组件约束校验绿
- ✅ `alloc_migration.py --check` 在重复编号临时副本上 fail（自证）
- ✅ data_lifecycle 测试引用数 8 → ≥25（本线新增 15 个测试文件引用 +
  既有 8 文件）
- ✅ `pytest tests/unit -n 4 -m "not heavy and not real_services and not
  perf"` 全绿；ruff 变更文件 0 告警；覆盖率 ≥75%
