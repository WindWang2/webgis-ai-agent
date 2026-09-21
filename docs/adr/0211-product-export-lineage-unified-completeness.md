# ADR-0211: Product → Export Lineage & Unified Product Completeness Verdict

方向 10（Map Product Compiler / MapSpec / Live Render / Export Consistency）基础能力收口。
状态：Proposed → 本 PR 落地。

## Problem Statement

MapProductPlan → Product Compiler → MapSpec → Live Runtime 管线的**语义半区**
（spec / 编译 / 观察）在 ADR-0081/0091/0118/0134/0183 中已建成，但**导出半区
仍是断链**：

1. **导出成品无 lineage**。`POST /api/v1/export` 只落 `data/exports/` 文件 +
   `.owner` sidecar + `.diagnostics.json` sidecar（app/api/routes/map.py:159-246）。
   artifact_registry（ADR-0082）不感知导出成品；product_graph 的 KIND_EXPORT
   节点只投影模板 export_profile 意图（product_graph.py:420-430），从不回链
   实际产物。MapProductSpec.delivery.targets 与真实导出产物零机器可读关联。
2. **goal_satisfaction 的 export 需求评估在生产中恒 absent**。其证据契约
   `chapter["export_receipts"]`（goal_satisfaction/evidence.py:197-225，
   形状 `[{format, revision, created_at}]`）在 app/ 生产代码中**零生产方**
   （grep 全仓仅 reader + 评估 corpus 合成 fixture）——「用户要导出而未导出 →
   不 PASS」的任务语义在生产路径上永不触发。
3. **三套 completeness 语义无统一 verdict**。finalizer（completion/pipeline.py）
   不消费 product_completeness 的语义发现；`chapter["map_product"].status` 与
   `product_completeness.complete` 可矛盾且无仲裁。DoD 5（completion 消费统一
   completeness contract）不成立。
4. **导出 parity 口径分裂**。`assess_export_parity`（viewport_export.py:51-83）
   维护一张与支持矩阵漂移的硬编码豁免表（export_layout/inset_map/annotation
   豁免已因矩阵真值化而冗余）；`product_completeness._LIVE_ONLY_FAMILIES`
   硬编码 `(chart_panel, statistics_panel)`，与 component_renderers 矩阵
   （两者均有 canvas 导出消费方）矛盾——同一事实两处口径且一处已陈旧。
5. **product_compiler chart alias 循环内序贯突变**（product_compiler.py:199-239）：
   后续 chart 视图的 kind 解析依赖前一视图的 chart_kind（视图序敏感），
   违反同输入同输出的 determinism 纪律。

## Current Architecture（相关切面）

- MapProductSpec v1（product_spec.py，storage digest 随行）→
  compile_product_spec（纯函数，compile_digest）→
  validate_product_completeness（7 检查语义完整性）。
- finalizer：run_map_finalization（validate→repair→revalidate ≤2）→
  map_product_block 持久化 `gis_chapter["map_product"]`（additive 键族）。
- artifact_registry：per-session、`ref:` 为 artifact_id、有界（128 记录 /
  24 metadata 键）、inputs 血缘边 + replaces 替换链、注册是增值记录绝不阻断；
  磁盘 cursor 先例：ref:raster/*（probe + GC unlink）。
- 导出链：canvas 链（前端 exporter.ts → POST /export）与 publication 矢量链
  （backend-only /export/vector-pdf → mapspec_to_svg.py）。
- 组件支持矩阵单源：component_renderers.py `_SUPPORT_MATRIX`（canvas 链真值）；
  publication 矢量链的组件面真值散落在 mapspec_to_svg._render_chrome_groups
  的处理序（无导出词表）。

## Ownership / Authority

- **导出成品真相**：`data/exports/` 文件 + owner sidecar（既有，不变）。
- **导出血缘记录**：artifact_registry ledger（会话内派生记录层，ADR-0082
  纪律：只记录、绝不驱动行状态/spec）；`ref:export/<filename>` 是会话内
  指针，物理文件路径仍是实现细节。
- **导出回执**：`gis_chapter["export_receipts"]`（goal_satisfaction 既有
  契约键，本 ADR 补上生产方；形状/语义不新增第二契约）。
- **产品语义完整性**：product_completeness.py（唯一验证器）；finalizer 是
  消费方，不是第二验证器。
- **导出支持真值**：canvas 链 = component_renderers 矩阵；publication 矢量
  链 = mapspec_to_svg 导出的组件词表（本 ADR 新增导出常量，单一来源在该
  模块的处理序旁）。

## Canonical Data Contracts

```
ref:export/<filename>            # 会话内导出成品指针（artifact_id）
ArtifactRecord(artifact_type="map_export",
  inputs=[MapSpec source refs ≤16],
  metadata={format, vector, title, dpi, pages, spec_digest,
            mapspec_revision, degradation_codes ≤8})
gis_chapter["export_receipts"] = [{format, revision, created_at, filename}]
  # ≤8 条、format 去重保最新；revision = 导出时服务端
  # _cartographic_mutation_revision（goal_satisfaction stale 判定的对照键）
MapExportResponse.lineage = {ref, receipt_recorded}   # additive
F_PROJECT_PREFIX = "product_"   # finalizer 侧产品语义 finding 码前缀
PUBLICATION_COMPONENT_TYPES     # mapspec_to_svg 导出（publication 链可渲染族）
```

## State Transitions

- 导出成品记录：valid（probe 命中）→ stale（不再被引用）/ expired（文件
  缺失）；**绝不进入孤儿 GC 删除**（用户交付物，物理生命周期归
  exports 目录策略；`_gc_protection_skip` 显式保护）。
- 回执：append-only per format（新回执覆盖同 format 旧条目 = 最新交付事实）；
  goal_satisfaction 按 revision 对照自动判 stale/present，无状态机。

## Integration Seams

- `POST /api/v1/export`：新增可选 Form 字段 `session_id`；在场且通过
  `verify_session_owner`（SEC-08 跨租户写守卫）→ 记录 lineage + 回执。
  任何失败（无 plan / 校验失败 / 存储异常）→ 静默跳过，导出成功语义不变。
- `POST /api/v1/export/vector-pdf`：同 helper（session_id 可选 body 字段）。
- 前端 exporter.ts `uploadExport`：从 `getMapSpecSessionCursor()` 附带
  `session_id`（若有）；无会话/匿名局部态 → 不带，行为同旧。
- finalizer `_validate_all`：chapter 持 product_spec 时，编译 +
  validate_product_completeness 的发现以 `product_` 前缀并入统一 findings；
  `map_product_block` 增 additive `product_completeness` 摘要键。

## Failure Semantics

- lineage/回执是**增值证据**：全部 try/except 降级为键缺席，绝不阻断导出、
  绝不让导出失败被误判为 live 地图失败（既有隔离语义不变）。
- product 语义发现是**披露**：error 级参与状态阶梯（不撒谎的 complete），
  但不携带 repair 动作（修复归组装/执行通道，finalizer 不自造第二修复器）。

## Idempotency / Replay

- register_artifact 幂等 upsert（同 ref 重注册安全）。
- 回执按 format 覆盖写：同 format 重复导出幂等（最新 revision 胜）。
- finalizer 幂等门（revision + render_seq + rows_fingerprint）不变；
  product findings 是纯函数派生，不引入新的门钥匙。

## Security / Permission

- `session_id` 是客户端声称的跨租户写面：必须先 `verify_session_owner`
  （DB 元数据属主查询，SEC-08 同款）；不匹配 → 整个 lineage 跳过（不泄露
  存在性，导出本身不受影响）。
- 回执/lineage 载荷全部有界（format ≤8、码 ≤8、标题截断）。

## Resource / Cost

- 导出热路径新增成本：一次 DB 元数据查询（仅带 session_id 时）+ 一次
  mapspec/plan 读取 + 一次 ledger 写（既有 per-session lock 纪律）。全部
  在既有 async to_thread/锁模式内；失败即跳过，无重试风暴面。
- finalizer 新增成本：chapter 内 product_spec 的纯函数编译+校验（毫秒级、
  零 IO——compile_product_spec 以 plan=None 降级即够语义检查）。

## Observability

- 响应 `lineage` 键 + 结构化日志（export_lineage logger，跳过原因分级）。
- `map_product.product_completeness` 摘要键随块持久化（可审计）。

## Backward Compatibility

- 全部 additive：Form/body 可选字段、response 可选键、chapter additive 键、
  finding 新码（`product_*` 不在既有码词表内，旧读者按未知码处理）。
- 旧行为回归面：无 product_spec 的章节零新发现（finalizer 行为不变）。

## Migration Plan

无迁移：ledger/chapter 键均为运行时会话数据，按会话自然滚动。

## Rollback / Feature Flag

不需 flag：逻辑全部是增值披露路径；回滚 = revert（无持久化格式变更）。

## Acceptance Matrix

| 验收 | 测试 |
|---|---|
| 上传带 session → ref:export/* 注册 + source refs 血缘边 + 回执落章 | test_export_lineage.py |
| 无 session / 越权 session → 跳过、导出仍成功 | test_export_lineage.py |
| export ref 永不被孤儿 GC 删除；probe 命中 | test_export_lineage.py |
| goal_satisfaction 消费真实回执（present/stale） | 复用 evaluator 单测 + 回执形状对齐测试 |
| finalizer 对缺 product 面的章节产出 product_* error、verdict 压档 | test_map_product_unified_completeness.py |
| assess_export_parity 豁免单源化、行为不变 | 既有 test_map_completion 回归 |
| check 7 与 publication 链真值一致（面板族披露） | test_product_completeness 扩展 |
| 视图序无关的 chart kind 解析 + digest 稳定 | test_product_compiler 扩展 |
