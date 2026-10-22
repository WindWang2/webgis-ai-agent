# G04 PLAN — 模板资产权威与演进模型（分 wave）

使命：把 ADR-0214 显式留白的「用户自定义 composition template 的 DB 存储面」做成生产级：
**TemplateAsset（引用+版本+scope+lifecycle）** 持久层 + 既有组合工具/产品路径的真实消费 + 窄前端入口。
不造第二注册表/第二 MapSpec 写入口/第二一致性 checker。

## 架构决策（D 编号，实施期可增量补充）

- **D1 资产 ≠ 新模板真相**：`TemplateAsset` 是存储/生命周期/授权面；运行时槽位语义仍物化为 `MapCompositionTemplate` 进**既有** `CompositionTemplateRegistry`（按需 lazy merge，模式同样式模板 DB 合并）。资产 id 命名空间 `asset.*`（builtin 资产 `asset.builtin.*` 代码定义；用户/项目资产 DB 行 uuid 后缀），与系统 `composition.*` 物理不可撞（registry.register fail-closed + 前缀校验双保险）。
- **D2 变体优先于复制**：资产 payload = `{base_template_id, slot_overrides, name/desc}` 引用既有 seed/pack + 参数绑定覆写；或 `{template: <inline MapCompositionTemplate>}`（保存自会话/从零）。物化 = base（registry 单一真值）+ overrides 深合并 → MapCompositionTemplate + 派生 CompositionContractV1（contract_id=`contract.asset.<asset_id>@r<revision>`，不进全局 ContractRegistry，apply 时构造）。
- **D3 生命周期状态机**：asset.status ∈ draft→published→retired；version.status ∈ draft→published→retired（不可变快照，revision=max+1，savepoint 重试同 0048/CP-18）。publish 门 = 单模板校验（类型注册表/preferred/zone conformance/ABI/模型可解析）零 error。restore = 以旧版 payload 造**新** revision 再 publish（不重写历史，可逆操作）。retire 后新 apply 拒绝（reason code），**已钉 revision 的重放（打开/导出旧图）只读放行**。
- **D4 作用域**：visibility ∈ private（creator）/ org / project（project_id 必填）。读取 = builtin ∪ creator ∪ org ∪ (project 同项目)；写 = creator 或 admin。照抄 scope.py 纪律（org NULL ≠ public；不可见 404；SEC-02 谓词注入；工具面 ContextVar 身份 + DB 失败降级 builtin-only）。
- **D5 引用进身份块**：`CompositionIdentity` additive 字段 `asset_id:str=""`、`asset_revision:int=0`（旧 spec 逐位不变；随 layout 自动入 cartographic_fingerprint 链）。apply 工具/产品身份/manifest 投影带上资产引用；manifest composition 投影与 `_product_composition_identity` additive 扩展。
- **D6 写入通道不变**：资产 apply 复用 `webgis_apply_composition`（新增可选 `template_asset_id/template_asset_revision` 参数）→ 同一 conformance 预检 → 锁预检 → `apply_contract` → `mapspec_store.layout_set`。新工具仅 2 个：`webgis_list_composition_template_assets`（只读有界）与 `webgis_save_composition_template`（会话→资产）。reason codes additive。
- **D7 内置 4 族资产**（代码静态定义，revision=1，同解析路径）：heat_stats_portfolio（density_map+chart 绑定）、choropleth_graduated_report（statistical_map）、temporal_change_pair（temporal_change_report）、rs_classification_change（rs_classification）——全部 D2 变体，不复制模板文件。
- **D8 注册表新鲜度**：`CompositionTemplateRegistry.upsert_asset(tpl, revision)` 单调（仅 `asset.*` 前缀；revision 回退忽略）；服务层 resolve 时惰性刷新。进程内一致性优先，多 pod 陈旧性在 ADR 披露（与样式模板 on-demand 合并同语义）。
- **D9 导出验证可解析**：export 前（services 层）若身份块含 asset_id，先 ensure-registered 再 `verify_export_semantic_consistency`（lib 不碰 DB 的边界保持）。

## Wave 计划

- **W0**（本文件）：侦察/venv/干净 master cartography lane 基线/planning 文档。✔
- **W1 纯契约层**（lib，无 DB）：`app/lib/cartography/template_assets.py`（TemplateAssetReference/AssetPayload/物化/校验/指纹/内置 4 族）+ registry upsert + CompositionIdentity additive + 单测（tests/cartography/）。红→绿。
- **W2 生产入口**：模型 `CompositionTemplateAsset(+Version)` + 迁移 0098 + 服务（create/save-from-session/new_version/publish/retire/restore/list/resolve + scope）+ REST 路由 + 3 工具接线 + 产品身份/manifest 扩展 + e2e 测试（真 mapspec_store）。
- **W3 失败/恢复/并发**：版本并发重试、IDOR 矩阵、retire→钉版本重放、恢复旧版、损坏 payload、资产模板升级后旧图再渲染。
- **W4 前端 + 回归**：`frontend/lib/api/composition-template-assets.ts` + 窄入口组件 + vitest + tsc；后端 cartography lane + 受影响 unit 全绿对照基线。
- **W5 独立 review**（subagent C，未改所审代码）：逐文件 diff 审 → 修复 → 再审至无 P0/P1。
- **W6 ADR + PR**：新 ADR（引用 0214/0228）+ 单 PR 提交（不合并）。

## 停机条件（DoD 对照）

见任务书 §5；核心：planning/g04/ 七文件齐、本地测试真实退出码+基线对照、独立两轮 review 无 P0/P1、PR URL 落盘。
