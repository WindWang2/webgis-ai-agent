# ADR-0228: Cartographic Product Manifest V1 —— live↔export 语义一致性证据与制图产品编译一致性

- 状态：Accepted
- 日期：2026-10-20
- 方向：Goal H02（智能制图产品编译与多端质量一致性基础层 VNext）
- 基线：master `0415ad8d`
- 前置：ADR-0225（RenderWork/ACK 执行闭环）、ADR-0214（Composition Contract / Component ABI）、#1617（MapSpec v1.6 RepresentationAtScale）、ADR-0211/0157（导出链诊断/版面 IR）

## 背景与问题

H02 侦察（planning/h02-cartographic-conformance/RECON.md）实证的缺口：

1. `cartographic_fingerprint` 的 layer 投影键不含 v1.6
   `representation`/`visibility` —— 语义缩放变异产生相同指纹，
   RenderWork `work_id`、C13 截图 revision 门、lifecycle `superseded`
   代守卫对 representation-only 变异全部失明。
2. `representation_at_scale_report` 只在只读 review 路径计算且
   `to_dict()` 不序列化、主 mutation 路径不计算 —— 语义缩放质量证据
   无处消费。
3. 产品组合模板身份不落物理 MapSpec（`layout.composition` 唯一写点是
   契约工具）—— 终验 required_slots 兜底 `[["title"],["scale_bar"]]`
   弱于真实模板契约。
4. 显式 export intents（非 report 产品）不触发 export 槽位裁决。
5. 产品语义层不复跑 composition conformance（export_parity_gap 等
   error 级检查绕过）。
6. nodata guard 仅 live 生效（thematic-paint.ts:21-26 自述）：null 值
   live 走无数据色、导出压进最低类 —— 科学制图正确性分歧。
7. 客户端 SVG 矢量导出不水合 ref 源 —— 非 HUD 挂靠层静默丢失。
8. 导出产物缺一个把「数据 revision/显隐/分类字段/色彩分级/legend/
   来源/时间/组件必需性」聚合为可机器验证证据的 versioned 产物。

## 决策

### D1 指纹 v1.6 可见性

`quality_loop._LAYER_METADATA_KEYS` 增补 `representation`/`visibility`。
语义缩放变异自此产生新指纹（新代），下游身份链全部受益；无该二字段的
旧 spec 指纹逐字节不变。

### D2 组合身份落物理 MapSpec

`webgis_map_product` 经 `layout_set(composition=)`（ADR-0214 D2/D3
既有通道）写产品组合身份块；用户显式契约身份（contract_id 非空）不被
agent 再制图覆盖（user-wins）。终验 `resolve_required_component_slots`
按「chapter 证据 → 物理 spec 身份块 → 最小契约兜底」恢复真契约。

### D3 export 槽位裁决 + conformance 产品面

显式 export intents（png/pdf/svg）与 report_product 同等触发
export_layout 必备组合的重选；`produce_product_layer` 附带
`product_conformance` 快照（conformance_report 同真值；状态用既有
pass/fail/warning/not_evaluated 词表；组件/模板缺席 → not_evaluated）。

### D4 nodata guard 双端一致

`legend_spec.nodata.color` → StyleMethod 可选 `nodata` 色（schema 模板
`ts_projection.py` → `types.generated.ts` 再生成）。四个下降点同语义：
TS `compileStyleMethod`（live/headless MapLibre 降低，case 包装与
`withNoDataGuard` 同形状）、TS SVG 孪生、Python SVG 孪生（原生
null/missing 分派）。数值 NaN 行为不变；无 nodata 时逐字节兼容。

### D5 SVG 矢量链 ref 水合 + 结构化省略披露

exporter SVG 分支接入 `injectResolvedRefSources`（live/publication
同款）；未水合层由孪生发 `layer_ref_unavailable`（权威词表
`render_diagnostics.py` + component-catalog 再生成 + TS 词表同步），
不再静默丢层。

### D6 Cartographic Product Manifest v1

`app/lib/cartography/product_manifest.py`（纯函数、确定性、有界）：

- `build_product_manifest`：fingerprint/组合身份/bounded ref descriptor
  （ref 摘要不带原文/坐标/凭据）/图层语义（filter 摘要、legend
  field/method/palette/class 数、representation 模式集）/组件/时间。
- `verify_export_semantic_consistency`：六维验证（data revision 披露、
  图层显隐、专题字段一致 legend↔paint、组件必需性[注册表真契约]、
  导出能力覆盖[renderer 矩阵 has_exporter 单源]、交付降级披露）。
  聚合 = 最差**适用**维度（全 not_evaluated 才整体 not_evaluated；
  能力缺失且已披露 → unsupported 不冒充 pass）。
- 生产挂载：`POST /api/v1/export`（session 在场随 diagnostics sidecar
  落 `{filename}.manifest.json`）。

### D7 版面文本确定性截断

`buildPublicationLayout`（三链单源）对 title/subtitle 设 120/160 code
points 上限，截断置 `truncated` 标记（非截断形态与旧 IR 逐字段一致）；
canvas/SVG 链同发 `title_truncated` 披露。

### D8 场景工厂（跨语言单一真相）

`tests/cartography/golden_corpus/h02_scenarios/`：generate.py 产出
20 正例 + 13 负例，Python（模板契约/主题语义/manifest 验证/确定性）与
TS（style 编译/热力与 cluster 子层 zoom 门/版本门拒收/ACK per-source
stale）消费同一 `scenarios.json`。

## 明确不做

- 不重写 RenderWork/ACK 协议（只补指纹敏感性与消费面）。
- 不建第二注册表/词表/IR（全部复用 composition_templates/
  component_renderers/render_diagnostics 既有真值）。
- 不实现像素级跨浏览器一致性承诺（语义一致性 + unsupported 披露）。
- 他属 issue（#1713/#1726/#1747）只建回归契约（xfail/it.fails），
  不动其实现文件。

## 验证

- `pytest -m cartography`：2259（基线）→ 2283+ 全绿（含新增 60+ 契约）。
- perf：manifest/verify 100 层 ~3.5ms、1000 层 ~27ms、峰值 RSS 48MB
  （tests/benchmarks/test_h02_cartography_perf.py，-m perf 车道）。
- 前端 typecheck/lint/定向 vitest 全绿（含 33 场景 TS 侧 25 测试）。
