# G44 DEDUP_MATRIX — 已核基线 vs 本任务增量

判定词：present=已完整存在 / partial=部分存在 / missing=缺失（本任务做）/ owned-by-other=他任务
所有权 / blocked=外部阻塞。每项落到生产调用链 file:line（base 655f769ca）。

| 能力 | 判定 | 证据（file:line） | G44 动作 |
|---|---|---|---|
| 观测字段画像（类型/词表/扫描上限） | present | app/services/feature_edit/attr_ops.py:36-103 | 作为观测层保留呈现；不改变其语义 |
| 观测枚举拒绝（enum_violation） | present | attr_ops.py:170-180 → plan.py:246,339 | 声明字段时被合同判断覆盖（跳过观测枚举），未声明字段原行为 |
| set_fields/rename_field/create_features 校验 | present | feature_edit/plan.py:229-347 | 合同校验挂在候选态，不改这些 per-op 校验 |
| 提交 CAS（revision+fingerprint）+ 锁 + 幂等 | present | feature_edit/executor.py:200-249 | 合同 read set 并入 plan/commit；新增 contract_changed 冲突码 |
| undo/redo 逆 delta 同链 | present | executor.py:440-605 | undo/redo 增加当前合同对恢复态的校验（F23） |
| G32 提案合并桥 | present | feature_proposals/bridge.py:114-228 | merge 复核当前合同（build_plan 传合同）→ F22 |
| G24 选择句柄（完整范围/revision 绑定） | present | app/services/selection_handles.py:56-230,323- | 批量填写消费 handle/materialize，不建第二选择引擎 |
| durable job 运行时 | present | app/services/jobs/lifecycle.py:33-62; data_operations/runner.py | 新 JobKind `field_contract_assess`，复用 runner |
| 域元数据 DB 表模式（版本化+CAS+drift 闸） | present(模式) | app/models/data_join_authoring.py; migrations 0107 | 新表 field_contracts / field_contract_assessments（g44 迁移） |
| owner REST ACL | present(模式) | app/api/routes/feature_edits.py:44-235 | 同款 require_owned_session；actor 服务端裁决 |
| 工具注册/发现/dispatch | present(模式) | app/tools/__init__.py:11-172; registry.py:475- | 新模块 field_contracts_tools，Pi 只读+草稿+评估 |
| owner 声明业务字段合同（类型/必填/枚举/区间/唯一/条件） | **missing** | 全仓无 field contract 域（grep 无） | WP02/WP03：声明模型+有限语义 |
| 完整数据版本评估（冻结快照/预算/分页/取消） | **missing** | 无 | WP04/WP11：纯内核+durable job |
| 草稿/激活/退役 + 并发资格（与编辑共享竞争边界） | **missing** | 无 | WP05：激活在 `_edit_lock` 内复核数据指纹 |
| 声明字段在全部写入口统一执行 | **missing** | 无 | WP06：build_plan/commit/undo/G32 同一 validator |
| 结构化录入表单（typed 控件/逐字段错误） | **missing** | frontend/components/map/feature-layer-editor.tsx:242-261 仅 JSON 输入 | WP08：structured-feature-form 组件 |
| 合同工作台（作者页/评估进度/激活/历史） | **missing** | 无 | WP09：field-contracts 侧栏面板 |
| Pi 读合同/草拟/评估工具 | **missing** | 无 | WP10 |
| 独立 oracle 测试 + 双旅程 | **missing** | 无 | WP12 |

## 边界与不占用

- G38 数据关联/富集、G19 字段计算、G31 拓扑、G40 资产：**owned-by-other**（已合并，只消费）。
- G09 参数化批量分析、G21 OD 流图：**owned-by-other**（历史分配，不重新占用）。
- 全量读取权威/查询引擎、journal/稳定 _wfid/权限系统：present，复用不重建。
- 派生数据（join/计算副本）不自动继承合同（F33）；provenance 记录可选携带。
- 跨数据集外键、任意表达式/SQL/JS/regex 求值、企业表单平台：明确不做（typed unsupported）。
