# ADR-0230: Composition Template Asset Lifecycle V1（G04）

状态：Accepted（2026-10-22）
关联：ADR-0214（Composition Contract / Component ABI，本 ADR 收口其显式留白的
「用户自定义 composition template 的 DB 存储面」）；ADR-0228（Product
Manifest，组合身份投影的 additive 扩展）；ADR-0238（pack 化扩容纪律）。

## 背景

ADR-0214 交付了 versioned 组合契约 + 组件 ABI + apply/discover/plan 工具面，
并在非目标中明确：「不做用户自定义 composition template 的 DB 存储面（只读
身份与锁定；存储面是独立方向，需 scope/租户/退役路径完整设计）」。

截至本 ADR，组合模板（`MapCompositionTemplate`）全部是进程内静态注册表
（seed + `composition_packs/`）：无用户/项目持久化、无版本状态机、无
组织共享。样式模板线（`cartography_templates` + `template_versions`）有
DB 存储，但只管 basemap/symbology/layout/thematic preset，不覆盖「组合
版式」这一资产类别。结果是：用户调好一个版面只能留在会话里，无法保存、
共享、复用、版本化。

## 决策

**D1 资产 ≠ 新模板真相。** 新增 `TemplateAsset`（存储/生命周期/授权面）：
DB 主表 + 不可变版本快照 + `asset.*` 命名空间。运行时槽位语义仍物化为
既有 `MapCompositionTemplate` 进既有 `CompositionTemplateRegistry`——
不建第二注册表、不复制模板文件、不改槽位语义真值。

**D2 变体优先于复制。** 资产载荷（`TemplateAssetPayload`）恰居其一：
引用式（`base_template_id` + `slot_overrides` 参数绑定覆写）或 inline
（完整模板 dict，保存自会话/从零）。物化 = base 真值 + 覆写深合并 →
`MapCompositionTemplate`（id 强制 `asset.*`）。派生契约
（`contract.asset.<id>@r<revision>`）只引用槽位，不进全局 ContractRegistry。

**D3 生命周期状态机。** asset/version 均为 `draft → published → retired`。
`revision = max+1`（savepoint 重试，0048/CP-18 同款）；发布门 = 物化 +
单模板校验零 issue（复用 `CompositionTemplateRegistry.validate_template`
既有口径）；retire 为软退役——新 apply 拒绝，**钉 revision 重放只读放行**
（旧图可安全打开/导出，不静默改用户绘图）；restore = 旧版 payload 造**新**
revision 再发布（不重写历史，可逆操作）。apply（含显式钉版本）要求目标
版本 published——发布门是准入闸，钉版本不绕过。

**D4 作用域显式化。** `visibility ∈ private | org | project`。读取 =
creator ∪ (org ∧ visibility=org) ∪ (project ∧ 项目属主/同 org 成员)；
admin 全见；匿名不见任何 DB 资产（builtin 4 族是代码定义的公共内容）。
写 = creator 或 admin（共享是只读语义）。纪律照抄
`app/services/templates/scope.py`：不可见 = 404 不泄露存在性，404 先于
403；`org_id IS NULL` 从不当作 public。

**D5 注册表纪律（跨租户边界）。** 组合模板注册表是进程级无鉴权结构：
只有 builtin（公共命名空间）资产 upsert 进去（单调 revision）；**DB 资产
永不全局注册**——apply 走 `apply_contract(template=...)` 显式钉版本模板，
发现/列表走按调用者 scoped 查询（同样式模板 on-demand 合并语义），导出
验证走 `verify_export_semantic_consistency(extra_templates=...)` 显式注入。

**D6 引用进身份块。** `CompositionIdentity` additive `asset_id` /
`asset_revision`（旧 spec 逐位不变；随 layout 自动进
`cartographic_fingerprint` 链）。产品路径（`webgis_map_product`）与
manifest 投影同源扩展；用户显式契约身份不被覆盖（ADR-0228 user-wins）。

**D7 内置 4 族资产。** 代码定义、revision=1、走同一条解析/物化/应用链：
heat_stats_portfolio（点分布+热力+柱状统计）、choropleth_graduated_report
（分级设色+图例统计）、temporal_change_pair（时序对比双图）、
rs_classification_change（遥感分类/变化）。全部是既有 seed/pack 的
**参数绑定变体**，不是模板复制。

**D8 写入口不变。** MapSpec 突变仍唯一经 `mapspec_store.layout_set` /
`SetLayoutIntent`。Agent 工具面 additive：`webgis_apply_composition` 新增
可选 `template_asset_id` / `template_asset_revision`（同一 conformance 预检
+ 锁预检 + CAS），新增只读 `webgis_list_composition_template_assets` 与
`webgis_save_composition_template`（会话属主守卫后派生落库）。

**D9 save-as-my-template 保真。** 从 live MapSpec 派生：实例按类型解析回
基底槽位（`INSTANCE_ID_BY_TYPE` 约定），只存差异覆写；**无法用差异表达
的新增组件 → 诚实降级 inline**，不吞用户编辑。已与用户显式契约身份
（contract_id 非空）互不覆盖。

## 后果

- 正面：用户/项目组合版式首次可持久化、版本化、共享、复用；旧图重放有
  明确语义（published/retired 只读放行，draft 拒绝）；模板版本变化有
  typed diff（fingerprint）与 compat（发布门）可机读。
- 约束：DB 资产不进进程注册表 → 每进程需按调用者解析（有界、scoped）；
  多 pod 场景 DB 资产新鲜度以 DB 为准（builtin 单调 upsert 一致）。
- 非目标（仍不做）：新地图引擎、第二注册表/第二 MapSpec/第二 QA 体系、
  ComponentType 词表扩容、组件级细粒度权限（版式级 scope 已覆盖主体场景）。

## 迁移

0098 additive 新表（create_all-coexistence guard + downgrade 反序），既有
表零改动；`migrations/env.py` 显式 import 新模型模块（autogenerate 纪律）。
