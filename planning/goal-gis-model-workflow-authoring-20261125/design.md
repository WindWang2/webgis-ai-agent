# Design — 模型作者域（model_authoring）

## 分层契约（immutable authoring model ≠ compiled plan ≠ instance）

```
ModelDocument（作者模型，可变草稿，CAS 修订）
  ├─ validate.py  → typed findings（静态语义验证，只读）
  ├─ compile.py   → TypedWorkflowGraph + WorkflowPackage(umodel_*, semver, 指纹)  [不可变]
  │                   └─ PackageRegistry.register/publish（既有权威，draft/published/deprecated）
  ├─ imports.py   ← 项目工作流 revision / SessionPlan promotion 行 / 内置模板
  └─ service.py   → 实例化 adapter：instantiate + 输入绑定(bound_ref) + 参数解析($param:)
                      └─ WorkflowRuntimeService.instantiate/run_instance（不重写 driver）
实例执行/复用/重算/解释 = 既有 runtime（instance/events/recompute-plan/inspector REST）
```

模型只保存声明/版本/ref：草稿存声明文档；发布存包指纹与版本映射；运行态一律读既有实例投影。

## ModelDocument v1（app/schemas/model_authoring_schema.py）

- `ModelParamSpec`：name（`^[a-z][a-z0-9_]{0,47}$`）、kind ∈ {number,integer,string,enum,boolean,field,unit}、label ≤120、required、default（有界 JSON ≤512B）、min/max、choices ≤16、unit ≤24、description ≤500、input_node（field 参数宿主 data_input）。
- `ModelNode`：node_id（同 name 正则，全图唯一）、kind ∈ {data_input,transform,analysis,cartography,output}、capability（analysis 必填 ⊆ CapabilityRegistry）、transform_op（transform 必填 ⊆ 可执行词表 buffer/clip/dissolve/overlay/filter/reproject）、role（data_input ⊆ DATA_ROLES）、params（≤16 键，值 ≤256B；字符串值可用 `"$param:<name>"` 引用模型参数）、title ≤120、optional、description ≤500。
- `ModelEdge`：from_node/from_port/to_node/to_port（from_port 缺省 "out"，data_input 输出端口名 = role；to_port 按 kind 缺省主输入）。
- `ModelDocument`：schema_version="1"、name ≤120、description ≤2000、source{kind ∈ {manual,project_workflow,session_plan,template}, ref}、params ≤16、nodes ≤32、edges ≤64、output_node_ids ≤8（完成义务：每个 output 节点必须被边指到或为显式输出）、layout（UI 坐标 ≤32 项，x/y ∈ [0,4000]，**不参与科学指纹**）、template_id。
- 上限纪律：编辑器 32 节点 < runtime 64 上限（余量给编译扩展）；全文档 canonical JSON ≤ 256KB。

## compile.py：document → 包（纯函数，同输入同指纹）

1. 文档 canonical 校验（结构闭合：边引用存在、端口存在、无环、output 可达）。
2. 端口推导：data_input 输出端口 artifact_type 由 role 词表映射（15 角色默认 artifact/geometry）；analysis 输入输出端口从 `CapabilityDescriptor.input/output_artifact_types` 推导；transform 输入 feature_collection 输出 feature_collection；cartography 输入 feature_collection 输出 map_image（ArtifactType 校验），output 节点汇聚。
3. 端口兼容检查（复用 binding.verify_port 同维度词表：artifact_type/geometry_kind/crs_class）→ 不兼容 = typed finding（error 级阻断编译）。
4. `$param:` 解析：node.params 中引用 → node.parameters 引用清单 + 节点 params 落**默认值**（无默认的 required 参数缺失 = finding）。
5. 组装 `TypedWorkflowGraph`（pydantic 直构，不经 15 阶段编译器；side_effect 按 kind 派生词表）→ wire dict（bounded）。
6. `WorkflowPackage(package_id="umodel_<model_slug>", ...)`：compiled_form = {schema_version, model:{id,name,description,source,template_id}, typed_dag, parameters(声明投影), model_fingerprint}；复用 `_canonical_fingerprint`。recipe_fingerprint=""（诚实：非 recipe 派生）。
7. 注册/发布经 PackageRegistry（draft→published→deprecated）；发布返回 (package_id, version, fingerprint)。

## store.py：草稿持久（新迁移，表 `model_drafts`）

列：id(uuid)、project_id FK、model_slug（`^[a-z][a-z0-9_]{2,40}$`，project 内唯一）、name、description、document(JSON)、revision_no(≥1 CAS)、latest_published_package_id、latest_published_version、created_by、created_at、updated_at。唯一键 (project_id, model_slug)；revision CAS 乐观锁。发布历史不复制执行态——只记指针。

迁移编号策略：基于 0109 新分支头（0111_model_drafts，revision id 唯一前缀 `m0111_`）；交付前 re-fetch master，若 0110 已合并则改 down_revision 链，避免双头。

## bindings/service.py：实例化 adapter

- `instantiate_model(published pkg, version, params_values, input_bindings{node_id: ref}, session_id, project)`：
  1. PackageRegistry.resolve → 兼容检查（复用 check_compatibility）。
  2. WorkflowRuntimeService.instantiate（既有）。
  3. data_input 节点绑定：ref 必须 session 可读（session_data 权威）+ binding.verify_port 对端口校验 descriptor → store.transition_node(READY, patch bound_ref)（镜像 _chat_complete_node 语义，但仅预绑不定成——执行由 driver BINDING_PASS_THROUGH 完成）。
  4. 参数解析：声明校验（required/default/enum/min/max）→ 逐节点 node_params（node.params ∪ $param 解析值）→ run_instance(node_params=...)（既有签名，REST 增量暴露）。
- 读投影：instance_projection + explain（既有 projection.py）直接消费；模型层只加「model 维度」投影（节点→参数/输入绑定回执）。

## REST（app/api/routes/model_authoring.py，prefix /model-authoring，全部认证+项目写权限）

- `GET /capabilities`（可声明节点目录：CapabilityRegistry native + transform op 词表 + port 推导预览）
- `POST /projects/{pid}/models`（建草稿）、`GET /projects/{pid}/models`（列表）、`GET/PUT /projects/{pid}/models/{slug}`（读/CAS 更新 revision）
- `POST /projects/{pid}/models/{slug}/validate`（typed findings，只读）
- `POST /projects/{pid}/models/{slug}/publish`（validate 零 error 才可发布 → 包 register+publish；body: version change ∈ {major,minor,patch}）
- `POST /projects/{pid}/models/{slug}/instantiate`（published 版本 + params + input_bindings → instance 投影）
- `POST /projects/{pid}/models/{slug}/export`、`POST /projects/{pid}/models/import`（小型模型文档 JSON；导入只建草稿，绝不自动执行）
- 运行/取消/重试/事件 = 既有 /workflow-runtime/instances/*（不重复端点）

## Pi 工具（app/tools/model_authoring_tools.py，tier=2，model-builder 域）

`model_builder_list_capabilities`、`model_builder_create_draft`、`model_builder_inspect`（读草稿+findings）、`model_builder_patch`（受限补丁：add/remove node/edge/param/set_param/bind_input，服务端白名单校验，不收自由代码）、`model_builder_validate`、`model_builder_publish`、`model_builder_instantiate_run`、`model_builder_list_models`。写操作 side_effect=state_mutation；全部走 service 层（与 REST 同链）。`app/tools/__init__.py` 最小接线一行。

## 前端（WP3）

- rail tab `model`（六步挂接：hud-types/workbenchSlice/nav-rail/context-panel/layout.json×2）。
- `components/model-builder/model-builder-tab.tsx`：左 palette（capabilities 目录）、中自绘 SVG 画布（分层布局 + 拖动 + 点击端口连线 + 选中）、右参数/属性面板 + findings 列表 + 运行按钮。
- 草稿态 store：新建 `lib/store/modelBuilderStore`（zustand slice：document、selection、dirty、undo 栈经 workbench undo 命令）。
- 运行 overlay：WorkflowNode.state → 画布节点色（复用 runtime-inspector 状态词表），实例轮询用既有 useBoundedPoll + events after_id 增量。
- 科学/执行 digest 与 UI 坐标分离：layout 单独放 store/PUT 的 layout 字段，不进 document 指纹（服务端 compile 忽略 layout）。
- i18n namespace `modelBuilder.json` ×2 + messages.ts 最小登记。

## WP6 模板（data/templates + 服务端内置模板注册）

四个可执行模型（全部走已接线执行阶梯，负例覆盖缺输入/非法连接/漂移）：
- A 设施按行政区计数：data_input(subject=poi) → analysis:admin_aggregation(field=区名) → cartography → output。
- B 设施服务区：data_input(subject=poi) → transform:buffer(距离参数 $param:buffer_m) → data_input(boundary) → transform:overlay → analysis:category_breakdown → cartography → output。
- C 环境采样统计（替代「栅格统计」——runtime 无栅格算子接线，理由记录）：data_input(环境采样点) → transform:filter → analysis:category_breakdown → cartography → output；CRS 义务在 validate 声明。
- D 两期对比：data_input(baseline) + data_input(comparison_time) → 各自 analysis:admin_aggregation → 两个 cartography → output（交叉表未接线，理由记录）。
fixtures：真实小 GeoJSON（本地可控），经 session_data 注入 ref。

## 测试矩阵

- 单元：schema 边界、validate 全 finding 码、compile 指纹确定性 + roundtrip、imports（项目工作流/plan→draft）、bindings（含越权 ref 拒绝）、store CAS。
- 集成：REST journey（草稿→validate→publish→instantiate→run→events→recompute dry_run）真实 driver（local dispatcher）；Pi 工具 journey（同服务经 dispatch）。
- 前端：组件测试（palette/画布渲染/参数面板/findings）、i18n key 完整性、e2e journey（mock 模式）。
- 性能：20/100 节点 validate+compile 耗时上限断言（100 节点仅服务端 compile 面允许超编辑器 32 上限——用直构文档测编译器规模）。

## 风险与对策

- 迁移双头 → 交付前 rebase 到最新 head。
- `umodel_` 包与 recipe 包同表共存 → package_id 前缀区分 + compiled_form.model 节点区配；Registry 逻辑零改动（register/publish 本就 id 无关）。
- data_input 绑定越权 → ref 必须 session 可读 + verify_port；跨项目数据天然被 session 隔离阻断。
- 前端无图库 → 自绘 SVG（lineage-graph 模式），无新依赖（避免 dependabot 冲突）。
