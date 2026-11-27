# SCOPE — 03 多源空间联动探索

## 所有权（本线可写）

首选所有权（新增/修改实现）：
- `app/services/linked_exploration/` — 多源计划模型、指标解释、作用域传播、快照
- `app/tools/linked_exploration_tools.py` — 新增 Pi 工具面（多源）
- `app/api/routes/linked_exploration.py` — 必要的 REST 薄接线
- `frontend/components/linked-exploration/`、`frontend/lib/linked-exploration/` — 多源面板/钻取 UI
- `tests/unit/test_g46_*`、`tests/unit/test_multi_source_*`（新增）、`tests/integration/test_g46_*`
- i18n：linked-exploration 相关 key（局部追加，不重排整文件）

只读复用（不改实现，只 import）：
- `app/services/selection_handles.py`、`app/services/feature_pages.py`（typed where evaluator + digest）
- `app/services/gis_situation/selection_history.py`（resolve_selection_ref / ClaimedScope）
- `app/services/data_join_authoring/`（JoinPlan 合同 + executor + 诊断，作为已批准关系来源）
- `app/services/bivariate_reliability_authoring/snapshot.py`（freeze_source 同代冻结）
- `app/services/feature_aggregate.py`、`app/services/data_fabric/query/accumulators.py`（算子语义参照）
- `app/services/field_contracts/`（单位/类型声明读取）
- `app/services/mapspec_store.py`（patch_component CAS 唯一地图写入口）
- `app/services/artifact_registry.py`、`artifact_revisions.py`、`session_data*`

禁区（其他线所有权）：
- `vendor/pi`（01 线）、`data_fabric/` 供给与融合核心（02 线）、制图符号/复合表达（04 线）、
  发布验证（05 线）。共享入口（registry 注册行、路由挂载行、locales 索引）只做必要薄接线并记录原因。

## PR 去重结论

- 开放 PR=0；#2281–#2282 后无新活动。无本方向重复实现。
- G46（#2169）= 单源联动探索 —— 本线在其上做多源扩展，**不重写单源路径，保持兼容**。
- #2273 数据融合线 ≠ 本线；G38 data join authoring 是「关系合同来源」，不是竞品。

## 契约边界（跨模块依赖承诺）

1. 多源探索 plan = 现有 ExplorationPlan v2 的**向后兼容扩展**（新 schema 版本号 + optional 字段；
   旧 v2 单源视图保存/重开/导出不受影响）。
2. 跨源关系一律引用**已存在的 JoinPlan（G38）**或用户显式声明的 spatial relation；
   本线不发明第二套 join/表达式语言。typed where 一律走 feature_pages.evaluate_where。
3. 指标计算合同新增于本域模块（metrics），算子语义与 feature_aggregate/AggregateDriver 对齐：
   count/distinct count/sum/mean/weighted mean/rate(ratio)；quantile 类只在有界本地数据上提供，
   大数据诚实声明不可用。分母为零 → `unavailable`，绝不返回 0 冒充。
4. 地图写入唯一走 `mapspec_store.patch_component`（mutation_revision CAS）；Agent 工具与 UI
   复用同一 service/receipt。
5. selection/scope：引用 selection_handles（revision 绑定、410 stale）与
   selection_history.resolve_selection_ref（"previous"/epoch_seq；不可解析 → 最小澄清）。
6. 执行有界：分块/分页 + 预算 + 取消；结果带 generation，迟到按 generation 丢弃；
   大数据不把全量列表塞进 LLM/前端。

## 文件冲突预期

- `app/services/linked_exploration/service.py`、`contract.py` 为本线主战场（他人只读）。
- `app/tools/linked_exploration_tools.py` 注册行追加新工具。
- 若与他线共享文件冲突（如 `app/tools/__init__.py` 接线行），按「局部追加、不重排」原则处理并在 HANDOFF 记录。
