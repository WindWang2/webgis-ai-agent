# F11 — Independent Review Report & Fix Ledger（ADR-0214 方向）

- Reviewer：Subagent C（独立深审，非实现者自评）
- Review 范围：`origin/master(9e1ad229)...HEAD` 全量 diff + 69 个增量 golden
- 结论：**APPROVE-WITH-FIXES** → 全部 P1 已修复，P2 除明确声明外全部修复

## Findings 与处置

| 级别 | 发现 | 处置 |
|---|---|---|
| P1-1 | conformance `export_parity_gap` 误报：`label_layer`（矩阵诚实声明空，经图层子通道渲染）与矩阵外类型被判 error，`webgis_apply_composition` 被永久阻断；disabled 实例也被计入 parity 义务 | **已修**（composition_conformance.py）：parity 只对在场（`enabled is not False`）实例（与 `viewport_export.assess_export_parity` 同口径）；矩阵外类型跳过（`unknown_component_type` warning 已覆盖）；矩阵空声明 → 新 warning 码 `export_channel_indirect`（披露级）。回归测试 ×2 + 既有误报断言改写。不动 `component_renderers.py`（MUST NOT TOUCH，单源矩阵） |
| P1-2 | `expected_revision` CAS 只存在于未提交编辑，且无回归测试（读-改-写丢失更新窗口） | **已修**：`layout_set` 透传 `expected_revision` → `_apply` CAS（superseded 语义）；回归测试 `test_apply_cas_stale_revision_superseded`（stale → 拒绝 + spec 逐位不变） |
| P2-1 | 引擎锁拒绝/超时拒绝返回空 `reason_codes`（裸中文 message） | **已修**：`error_code=layer_locked` → `component_locked:user_wins`（附 locked_component_ids）；`status=superseded` → 新码 `apply_superseded`（入词表，测试锁）；回归测试 ×1（族前缀陈旧锁经引擎拦截场景） |
| P2-2 | `apply_contract` 空槽物化不检查锁集（库级直调无锁保护） | **已修**：分配 id ∈ 锁集 → 跳过物化 + 披露；回归测试 ×1 |
| P2-3 | `validate_props` int-enum 分支死代码；`required` 声明从未消费 | **已修**：int 枚举判定前移至范围检查之前；`missing_required` 有界检查启用（存量 schema 无 required=True，零行为变化）；回归测试 ×2 |
| P2-4 | 文档与实现脱节：ADR D3 仍写 `user_lock` 字段（与 D4 自相矛盾）；design doc topology/ContractSlot 形状/priority=29 过时；recon 仍用 `webgis_replace_component` 旧名 | **已修**：ADR D3 重写为最终 additive 面（composition 块 + SetLayoutIntent 双字段 + CAS，锁单一事实指向 D4）；design doc 三处对齐（ContractSlot 引用语义/46/purpose+contract_version）；recon 工具名与锁故事对齐 |
| P2-5 | `applied_revision` 实为 apply 所基于的 revision 而非写后 revision | **按建议采 doc-side 修复**：design doc 语义注释更正（写后 revision 需二次 touch，成本不值；based-on 语义对 drift 归因更有用） |
| P2-6 | 引擎 links 通道校验松于 schema（`type` 只查 str，非法词表值入库后打破 canonical parse） | **已修**：引擎校验 `type ∈ COMPONENT_LINK_TYPES` + `dst_kind ∈ {component,layer,source}`（schema 单源导入） |
| P2-7 | 契约创作期不查 slot 链接环（环契约首个 apply 写入后自锁会话） | **已修**：`ContractRegistry.validate` 增加 `_contract_slot_cycle`（确定性 DFS，全边型有向环）；回归测试 ×1 |
| P2-8 | 实例 id 单一真值仍有两处无守卫手抄子表 | **已修**（测试锁）：`_COMPONENT_DEFAULT_IDS`（completion）与 `_LEGEND_PRIMARY_IDS`（composer）逐一对照 `instance_id_for_type`，漂移即红 |

## Reviewer checklist 结论（复述）

单一真相 PASS（ABI=投影+接地审计；契约引用不复制；写路径唯一）／
user-wins PASS（在场实例零触碰测试锁；引擎守卫事务内权威）／并发 PASS
（CAS 落地后 superseded 端到端验证）／幂等 PASS（byte-identical 断言）／
有界性 PASS（全载荷封顶，无未界循环，async 工具无阻塞 I/O）／schema 兼容
PASS（全 additive；canonical round-trip；TS 再生 1 行）／指纹捕获 PASS
（版本变化必变、无身份块逐位不变）／测试 PASS（真实负例 + 变异式锁测试 +
生产 dispatch 路径）／制图语义 PASS（分类模板槽位/区域合理；通用型零默认
漂移由 577 例 golden 证明）／文档 FAIL→已修（P2-4）。

## 测试证据（修复后）

- review 影响面套件：100 passed（ABI 22 + contract 29 + schema-identity 7
  + conformance 30 + tools 14，另有 packs/presets/intelligence/selection 邻域）
- ruff 全仓 `app/ tests/`：0 error
- golden corpus：577 passed（69 例纯新增，0 修改）
- master 既有失败（与本分支无关，干净基线复现）：`test_validate_and_compile`、
  `test_thematic_convergence`×1、`test_v4_cartography_libs`×1、
  `test_vector_pdf_route`×3
