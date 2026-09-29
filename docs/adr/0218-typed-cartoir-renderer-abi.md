# ADR-0218 — Typed CartoIR / MapSpec / Renderer ABI（MapSpec v1.5 additive）

- 状态：Proposed（C11；如与他方向合并序冲突，合并时按当时最大号重编）
- 关联：ADR-0120 W2/W3（V6 权威 schema + TS 投影）、ADR-0205（制图语法，
  其 Out of Scope 三项由本 ADR 收口）、ADR-0199（多尺度场景）、
  ADR-0214（Component ABI）、issue #1556（MapSpec 编译/渲染面 any）

## 背景

MapSpec 契约脊柱在 V6 已收口：`app/lib/cartography/mapspec_schema.py`
是唯一权威（Pydantic），`ts_projection.py` 确定性投影为
`frontend/lib/mapspec-compiler/types.generated.ts`（byte parity gate）。
但**语义层**仍是开放 dict 字符串约定，四类漂移不可测：

1. **可见性双轨漂移**：`layer.visible`（StrictBool，schema 脊柱）在
   compiler/runtime 无消费者；实际通道是 MapLibre `layout.visibility`
   （user-mutation/live-spec 折叠）。grammar 尺度带的
   `visibility_hints`（`scale_rules.ScaleTier`）随 layer_meta 下发，
   前端零消费（F10 显式 deferred）。
2. **zoom 语义四套并存**：label/scale bands（0/8/11/14/24，契约测试
   锁定相等）、scene_lod（3/6/10/14/19，投影进 zoomBands）、前端
   data-plane LodBand（8/12，传输调度）、源级 minzoom/maxzoom 缺省。
   渲染可见性没有单一 precedence。
3. **bivariate 藏 extra**：backend `bivariate.py` 已有核心（3×3 色阵 +
   `__biv_class` match paint），MapSpec 无原生声明 —— 语义身份靠
   `legend_spec.class_field` 字符串约定。
4. **Renderer ABI 无类型**：compiler/adapter 全线无 MapLibre 官方类型
   （全库零 `StyleSpecification` 引用）；adapter `as any`×49、
   compiler `: any`×11 + `as any`×33、use-feature-selection
   `: any`×10 + `as any`×13（issue #1556）。paint 双方言（语义短键 vs
   原生键）只有 live 桥（paint-bridge）知道，headless 编译器另写一份。

## 决策

**CartoIR 不是新并行文档**——MapSpec 已是唯一 desired state（架构不变
量 4），新建平行 IR = 第二事实源。CartoIR = **MapSpec v1.5 additive
语义层 + 前端 Renderer ABI 模块族**：

### D1 — v1.5 typed 块（`mapspec_schema.py`，identity upgrader）

- `MapSpecLayerVisibility`：`min_zoom/max_zoom`（结构 zoom 门）+
  `hints`（词表 `VISIBILITY_HINT_KEYS = (street_detail_minzoom,
  admin_boundary_detail)`；与 scale_rules 产出跨模块契约锁定）。
- `MapSpecLayerBivariate`：`x_field/y_field/matrix(2|3)/class_field/
  palette_id` —— 只承载语义身份，**色值权威仍在 match paint**（#679
  单一色源）；与 `legend_spec.class_field` 双写过渡，一致性契约测试锁定。
- `MapSpecLayerDataBinding`：`field/field_type(词表)/class_field` ——
  字段引用一等声明（number → interpolate/step；string → match）。
- 转换器（`analysis_cartography_converter.py`）生产接线：bivariate
  分析结果 → typed 块；`legend_spec.field` → data_binding。

### D2 — 显隐/zoom 单一 precedence（渲染端唯一裁决器）

`frontend/lib/carto-ir/visibility.ts`，live（`addLayerSafe`）与 headless
（`compileMapSpec`）共用：

- **显隐维度**：`layout.visibility`（用户 durable 决策）＞
  `visible === false`（authored，此前无消费的漂移收口）＞ visible。
- **zoom 维度**（与显隐正交）：`min_zoom/max_zoom` 结构门＋
  `hints.street_detail_minzoom`（opt-in 真阈值）参与 max 合成下限；
  `admin_boundary_detail` 是泛化档标记**不**参与可见性门控（参与会
  把参考语境边界层在小 zoom 藏掉 —— 语义反转）；未知键只披露；
  病态门（min≥max）→ 清空 + 披露。
- zoom 带表关系文档化：0/8/11/14/24 是唯一语义带表；scene_lod 经
  `build_label_zoom_bands` 投影进 zoomBands；data-plane LodBand(8/12)
  是传输调度关注点，不属渲染可见性语义。

### D3 — Renderer ABI 单一转换边界（`style-abi.ts`）

- `asExpression`/`asPaintValue`：开放 JSON → 官方类型（
  `ExpressionSpecification` 等）的**唯一 audited cast 集**（便宜形状
  守卫 + 单点 `as`）。
- `CompiledLayer`/`CompiledStyleView`：构建态官方形状视图；官方
  `StyleSpecification` 在构建边界把关，视图供测试/导出内省。
- compiler.ts `as any` 全清零；原生键透传的语义 StyleMethod dict 改经
  `compileStyleMethod` 降级（与 live paint-bridge 同语义 —— 双路径方言
  漂移收口，属行为修复：此前该形状到 MapLibre 即被拒）。

### D4 — 版本协商（`version.ts`，fail-safe）

- 已知版本（1.0–1.5）零拷贝透传（identity upgrader 语义下行为逐位
  一致）；缺失 version 视为 1.0（存量行为不变）。
- **forward/未知版本 → 拒绝渲染**：runtime `reconcile/reconcileAsync`
  与 headless `compileMapSpec` 同一道门 —— 保留 last-good spec、
  结构化证据（`mapspec_version_unsupported`）+ `lastError`。静默渲染
  未知语义就是契约谎言。
- Component ABI：`checkComponentAbi` 在 `resolveMapComponents` 收口 ——
  `layout.composition.component_abi_version` 高于前端支持（1）→ 组件
  交互面降级 + 披露，placement/options 渲染不受影响。

### D5 — 契约词表双向漂移闸 + 共享 corpus

- `tests/cartography/carto_ir_corpus/contract_manifest.json`：backend
  pytest 断言 ≡ schema 常量；frontend vitest 断言 ≡
  `lib/carto-ir/contract-manifest.ts`。任一侧改词表不更新 manifest 即红。
- corpus 5 fixture（choropleth/bivariate/categorical+hints/heatmap/
  raster+bivariate-degraded）双侧消费：backend parse golden、frontend
  compile golden（minzoom/降级决策）。
- debt ratchet 基线随 any 收敛下调（compiler.ts 11→0、
  use-feature-selection.ts 10→0 等 8 文件）。

## 后果

- **兼容**：v1.5 纯 additive（identity upgrader）；存量 spec（含缺失
  version）行为逐位不变（adapter.test.ts byte-parity 契约 74 用例、
  compiler/runtime 446 用例全绿）。唯一行为变化是三类此前**未定义**的
  边际：authored `visible:false`（后端今天不写）、forward 版本 spec
  （此前静默渲染）、原生键下的语义 dict paint（此前 MapLibre 必拒）。
- **性能**：协商门 O(1) 词表判定 + 已知版本零拷贝；visibility 解析
  O(hints)（≤2 键）；bivariate 校验深度限 12 的 paint DFS 仅对声明层
  触发。无新增 O(n) 全量扫描/无界结构。
- **不解决**：scene_lod 生产接线（ADR-0199 场景调度面）、OpenAPI→前端
  TS 全面 codegen（前端契约类型与 OpenAPI 本就脱钩，是独立方向）、
  MapLibre filter 表达式的深度类型化（开放词表，保持 `unknown[]`）。
