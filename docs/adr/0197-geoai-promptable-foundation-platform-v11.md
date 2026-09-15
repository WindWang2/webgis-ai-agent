# ADR-0197: GeoAI Promptable Foundation Platform 11——GeoPrompt artifact 与可提示推理契约深化

- 状态: Accepted
- 日期: 2026-09-15
- 线: geoai/promptable-foundation-platform-11
- 关联: ADR-0119（Spatial ModelOps V2——本 ADR 的推理平面基底）、ADR-0137（model_* 能力词汇族）

## 1. 背景

ModelOps V2/V3（ADR-0119）已交付 descriptor/registry/provider/planner/stitching/
fingerprint/reuse/loaded_cache/VRAM ledger 与 promptable 契约（PromptSpec：
point/box/mask/text；地理坐标经 `prompt_crs: bool` 声明）。缺口（Phase 0 审计
`.agent-work/geoai-promptable-foundation-platform-11/CURRENT_ARCHITECTURE.md` §6）：

- prompt 没有**版本化 artifact**：无身份、无 CRS 身份（仅 bool）、无时间语义、
  无目标模型/波段绑定、无 provenance；polyline/polygon/reference-layer 不存在；
- 地图 CRS↔像素往返无显式容差契约与 known-answer；
- 先验掩膜只按**数量**进复用键（同数量不同内容会命中同一 reuse——错结果复用）；
- mask-only prompt 实际不可达（provider 从 JSON payload 重建 PromptSpec 时丢失
  先验数组，纯掩膜 prompt 在重建处崩溃）；
- promptable GeoJSON 地理参考系统性错位（`georeference_polygon` 把 GDAL 系数序
  直传 shapely——shapely 期望 `(a,b,d,e,x_off,y_off)`）。

## 2. 决策

### 决策一：artifact 是授权/互换契约，PromptSpec 仍是运行时契约

`GeoPromptArtifact`（`app/lib/modelops/geo_prompt.py`，schema v1）承载版本、
CRS 身份、几何（points/boxes/**polylines/polygons**）、mask sidecar 引用
（内容寻址）、reference-layer 引用、时间语义、目标模型/波段绑定、provenance。
`compile_prompt`（纯函数 + 注入式 IO）把它编译为运行时 `PromptSpec` + 审计
（`GeoPromptAudit`）。polyline/polygon/reference-layer 编译为**确定性栅格化先验**
（多边形=像元中心包含；折线=触及像元；参考层=nonzero/threshold 策略），不新增
provider 词表成员——provider 契约（point/box/mask/text）不变，能力门照旧生效。

**理由**：词表是封闭真值（capabilities.py 静态断言 + 注册门）；把栅格化
编译放平台侧让所有 promptable provider 立即受益，且编译是可审计的确定性步骤。

### 决策二：身份 = 内容寻址，provenance 是元数据

`artifact_id` = sha256(canonical JSON of identity_payload)。进身份：schema 版本、
CRS、几何、text、mask digest、reference 战略、combine、labels、time、target。
不进身份：created_by/note/source_refs（出处不改变推理语义；inspect 面呈现）。
引擎把 `prompt_artifact_id` 与**先验内容 digest**（shape/dtype/bytes）条件加入
fingerprint——不携带的旧请求保持字节级同 key（reuse 兼容）。

### 决策三：anchor_box 只影响窗口放置，不是语义 prompt

mask-only/多边形 prompt 的窗口锚定框由编译期派生（含派生掩膜包围盒），进
`PromptSpec.anchor_box` + fingerprint + `prompt_span`/`prompt_windows`（大 anchor
网格化分窗，行主序）。它**不进** `required_prompt_modes`、不下发 provider——
避免把"窗口放置"伪装成"box prompt 语义"。

### 决策四：往返容差是显式契约

`PROMPT_ROUNDTRIP_TOL_PX = 1e-6`（仿射正逆是精确数学，容差吸收浮点舍入）；
编译期对每几何断言 像素→地图→像素 闭合误差，超限 typed 拒绝；退化（行列式
为 0）仿射 typed 拒绝。known-answer 用**前向**手算（测试选已知像素推地图
坐标，实现走逆变换）。

### 决策五：顺带修复的既有缺陷（同一 diff 内，因为被新路径暴露）

1. `georeference_polygon` 系数序（P1）：GeoJSON 输出对非平凡仿射系统性错位；
2. mask-only prompt 崩溃：provider 改为直接消费 extras payload 契约（引擎侧
   已验证权威 PromptSpec，JSON 重建是冗余且丢先验的管道）；无种子时以窗内
   相似度为种子再 ∩ 先验（point/box+mask 语义不变，zero-prior 测试不回归）；
3. 先验内容 digest 进复用键（修 count-only 错结果复用洞）；
4. sidecar/reference 单波段读取归一化 `(1,H,W)→(H,W)` + 网格对齐校验。

## 3. 后果

- `InferenceRequest` 新增 `prompt_artifact_id`/`prompt_audit`（可选；manifest
  增 `prompt_audit` 节）；`ModelOpsService.compile_geo_prompt` 是 artifact 编译
  唯一服务入口（mask sidecar digest fail-closed、满幅读取前像素上限检查、相对
  路径必须显式 mask_root）。
- 后续 Phase 在此之上：mask 候选集与质量契约（WP-C 深化）、embedding cache
  （WP-D）、text/原型 seam（WP-E）、tools/API/UI（WP-F/G）。
- 已知限制：v1 多边形无洞（可后续 additive）；编译期物化先验受
  `MAX_COMPILED_MASK_PIXELS`（256M px）约束，超限 typed 拒绝。
