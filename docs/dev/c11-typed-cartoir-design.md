# C11 Design — Typed CartoIR / MapSpec / Renderer ABI（v1.5 additive）

## 结论（单一事实源不变）

**CartoIR 不是新并行文档**。MapSpec 已是版本化权威 schema（Pydantic，
`KNOWN_VERSIONS 1.0–1.4`）+ ts_projection.py → types.generated.ts 字节级投影。
CartoIR = MapSpec 的 **v1.5 additive typed 语义层** + **前端 Renderer ABI**
（maplibre 官方类型的单一转换边界）。理由：新建平行 IR 文档 = 第二事实源
（违反架构不变量 4「MapSpec 是唯一 desired state」）；v1.5 additive 是既有
1.1–1.4 同款演进路径，零行为变化、可回滚。

## S1 侦察关键事实（改变设计的证据）

1. ts_projection 代码生成链路已存在（Pydantic→TS byte parity，gate 在
   pytest cartography lane）→ **同源生成已解决**，C11 只需扩展面 + 收紧 gate。
2. `layer.visible`（StrictBool）在 compiler/runtime 无消费者；真实通道是
   `layout.visibility`（live-spec/user-mutation 折叠）。后端今天不写
   `visible` → 潜在契约漂移而非活 bug。
3. `visibility_hints`（scale_rules.ScaleTier）只随 layer_meta 下发
   （advisory），前端零消费。
4. zoom 语义 4 套：label/scale bands（0/8/11/14/24，契约测试锁定相等）、
   scene_lod（3/6/10/14/19，后端未接线）、前端 data-plane LodBand（8/12，
   数据传输关注点）、源级 minzoom/maxzoom 缺省。
5. bivariate 已有后端核心（BIVARIATE_MATRICES 3×3、`__biv_class` match
   paint），MapSpec 无 native 字段——藏 legend_spec extra。
6. compiler/adapter 全线无 maplibre 类型（全库零 StyleSpecification 引用）；
   adapter `as any`×49、compiler `: any`×11 + `as any`×33、
   use-feature-selection `: any`×10 + `as any`×13。
7. Component ABI v1 后端闭环（component_abi.py），前端无协商点。
8. RenderApplyAck 是前端↔后端版本化契约唯一先例（`render_apply_ack.v1`）。

## 契约面（v1.5，全部 additive）

### Backend（app/lib/cartography/mapspec_schema.py）
- `KNOWN_VERSIONS += "1.5"`；`LATEST_VERSION = "1.5"`；upgrader 1.4→1.5
  identity（纯 additive，同 1.1–1.4 惯例）。
- 新 typed models（全 `_SpecModel` 基类，extra="allow"，未知键披露不拒绝）：
  - `MapSpecLayerVisibility`: `min_zoom?/max_zoom?: Number` +
    `hints?: Dict[StrictStr, Number]`（词表
    `VISIBILITY_HINT_KEYS = ("street_detail_minzoom", "admin_boundary_detail")`，
    与 scale_rules 生产方用跨模块契约测试锁定）。
  - `MapSpecLayerBivariate`: `x_field/y_field: StrictStr`、
    `matrix: Literal[2,3]=3`、`class_field: StrictStr="__biv_class"`、
    `palette_id?: StrictStr`。**只承载语义身份，不复制色值**（色值仍在
    backend bivariate.py 产的 match paint 里——单一色源 #679 原则）。
  - `MapSpecLayerDataBinding`: `field: StrictStr`、
    `field_type?: Literal["number","string","boolean","date"]`、
    `class_field?: StrictStr`。
- Layer 新可选字段：`visibility` / `bivariate` / `data_binding`。
- `SCHEMA_EXPORT_MODELS` 登记新模型 → ts_projection 自动投影。
- `visible` 语义文档化：authored default，运行时 `layout.visibility`（用户
  durable 决策）恒优先。

### Expression AST 决策
**不发明新 AST**。MapLibre `ExpressionSpecification` 就是表达式 AST；
compiler.compileStyleMethod（StyleMethod 语义方法表）是既有"声明式→AST"
编译器。C11 把它的输出类型锚到 ExpressionSpecification + 单一转换边界。
FieldBinding = `data_binding`/`bivariate` typed 块（声明引用哪些字段），
配合前端 `FeatureProperties` typed accessor 消灭 OBJECTID 魔法 as any。

### Frontend（新 `frontend/lib/carto-ir/`，单文件单职责）
- `version.ts`：`SUPPORTED_MAPSPEC_VERSIONS` + `migrateMapSpec()`（逐级
  upgrader，与后端注册表同构）+ forward/未知版本 fail-safe
  （`{ok:false, reason:"forward_version"}`，不渲染新 spec、保 last-good）。
  `SUPPORTED_COMPONENT_ABI_VERSION` + `checkComponentAbi()`（读
  layout.composition.component_abi_version，>支持版本 → 披露 + 降级不渲染
  组件交互面）。
- `visibility.ts`：**唯一 precedence resolver**（纯函数）：
  1. `layout.visibility`（用户 durable 决策通道）最终裁定
  2. `visible === false`（authored）→ 缺省 layout.visibility "none"
     （若 1 显式存在则 1 胜）
  3. `visibility.min_zoom/max_zoom`（结构 zoom 门）→ LayerSpecification
     minzoom/maxzoom
  4. advisory hints（layer_meta.scale_visibility_hints / tier band）仅对
     显式 opt-in 的层参与推导，永不覆盖 1–3
  + `compileZoomGate(layer)`；+ zoom 语义统一文档：0/8/11/14/24 是唯一
  语义带表；scene_lod 分界是呈现 LOD 子模型（经 build_label_zoom_bands
  投影进 zoomBands，不直接参与可见性裁决）；data-plane LodBand(8/12) 是
  传输调度关注点，不属于渲染可见性语义。
- `style-abi.ts`：maplibre `LayerSpecification/ExpressionSpecification/
  StyleSpecification` 的**单一转换边界**：`asExpression(raw)`（唯一 audited
  cast + 便宜 runtime 形状守卫）、`PaintFor<T>`、`toLayerSpecification()`。
  adapter/compiler 的 95 处 `as any` 收敛到此。
- `feature-props.ts`：`FeatureProperties = Record<string, string|number|
  boolean|null|undefined>` + `propOf(feature, key)` typed accessor。
- `bivariate.ts`：typed 块校验 + 降级决策（matrix 不支持/`__biv_class`
  缺失 → 单场 choropleth 兜底 + 披露，绝不白图）。

### 生产接线（Wave D，全部有真实 caller）
- runtime.ts `reconcile` 入口：migrateMapSpec 门（版本协商唯一收口）。
- compiler.ts：compileStyleMethod → ExpressionSpecification；
  maplibreLayer: LayerSpecification；visibility 块 → minzoom/maxzoom；
  visible:false → layout.visibility 缺省；bivariate 校验+降级。
- adapter.ts：paint 构造经 style-abi（hudStateToMapSpec 产出 byte-identical，
  adapter.test.ts 契约锁定不动）。
- analysis_cartography_converter.py：bivariate 分支同产 typed 块
  （legend_spec 保留过渡）；thematic 面产 data_binding。
- use-feature-selection.ts：propOf 替换 OBJECTID as any。

### Parity / 版本协商 gate
- `tests/cartography/carto_ir_corpus/contract_manifest.json`：backend 写出
  （版本列表、hint 词表、bivariate 词表、矩阵规模）——backend pytest 断言
  与 schema 常量相等；frontend vitest 断言与 carto-ir 常量相等。**双向
  漂移即红**。
- ts_projection byte-parity（已有）扩 v1.5；frontend types.contract.test
  找 v1.5 形状。
- corpus：`tests/cartography/carto_ir_corpus/*.json`（heatmap/choropleth/
  categorical/raster/bivariate/v1.5 blocks）——backend parse golden +
  frontend compile golden 共用同一批 fixture。

## 禁止破坏的 invariants
- adapter.test.ts byte-identical paint 契约（hudStateToMapSpec 输出不变）。
- MapSpec 是唯一 desired state；mutation 热路径不经 schema 冷路径。
- extra="allow" round-trip 保真；version 字段永不改写。
- SCALE_TIERS ≡ DEFAULT_ZOOM_BANDS 分界锁。
- debt ratchet 只减不增（baseline 随收敛下调）。
- MapLibre zoom 表达式/cluster/源缺省行为不变（visibility 块缺省 None 时
  零行为差异）。

## Out of Scope
- scene_lod 生产接线（它已能投影进 zoomBands；接线属 ADR-0199 场景调度面）
- data-plane LodBand(8/12) 重定界（传输调度关注点，只做文档化）
- UI 美化 / MapLibre 重写 / CartoIR 承载原始大数据
- lifecycle_engine.apply_mutation 内部（H02 领地）、contracts 搬家（H01 领地）
- OpenAPI→前端 TS 全面 codegen（前端契约类型本就脱钩 OpenAPI，引入是独立方向）
