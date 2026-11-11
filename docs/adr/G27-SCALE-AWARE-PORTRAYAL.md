# ADR（方向命名）— G27 多尺度表达策略的生产接线与可解释控制

- 状态：Proposed（本方向；**文件名故意不含四位编号**：G12–G27 并行，按 `docs/adr/G20-RASTER-EXPRESSION.md` 先例，合并时再按当时最大号重编。当前 `docs/adr` 最大号 0289）
- 基线：`8a9a79f76875eb95d31bd448baad971edb2e61d3`（MapSpec **v2.1**）
- 关联：ADR-0154（zoomBands）、ADR-0199（scene_lod）、ADR-0205（scale_rules）、ADR-0218（CartoIR / visibility precedence，其「v1.5 / 不解决 scene_lod 生产接线」为历史快照）、ADR-0279（ScaleRepresentationProfile.v1）、ADR-0054/0057/0058（desired/observed）

## 背景（实证，不是文档推断）

真实调用图核对结论：

1. `visibility.ts` precedence、`representation.py` 的 `lod_for_zoom` 投影、PR #1880 的 ScaleRepresentationProfile.v1（CAS + receipt）、G04 live/export parity **均已完整接线**，只回归、不重建。
2. 真实缺口：`build_label_zoom_bands()`、`terrain_maxzoom_for_zoom()`、`LOD_BANDS.decimate_budget` **零生产消费者**；组件 registry/composer 无档位输入；`PublicationIR` 无 scale/CRS/DPI/viewport 输入面；没有「有效策略 + 有界解释 + UI/Agent 局部覆盖」入口。
3. 四类尺度（语义 zoom 带 / data-plane 传输档 / source min-maxzoom / 输出 DPI 与物理比例尺）**有意并存**，契约测试 `test_scale_reconciliation_v1.py` 锁定差异 —— 不是 bug，不删表、不合并。
4. ADR-0218 的「MapSpec v1.5」已过时：当前 `LATEST_VERSION="2.1"`。

## 决策

### D1 — 单一运行时解释层，不是第二套 resolver

新增 `app/lib/cartography/scale_portrayal.py`：**只读复用**既有权威纯函数（scene_lod / scale_rules / representation / visibility 后端孪生），把「用户/Agent 显式 opt-in policy」合成为有版本的 `EffectiveScalePortrayal`。它不是 MapSpec 文档、不是并行事实源、不改变 desired state。

precedence：用户隐藏（全 zoom 保持、`applied=false`）> 人工 pin/锁 > 结构 zoom 门（正交）> 表达窗口 hidden > 自动 LOD > hysteresis（仅显示）> 物理比例尺（DPI 仅此通道）。未知/非法 policy → typed diagnostics + `applied=false`。

### D2 — 只投影既有 spec 字段

`project_scale_portrayal_spec()` 默认只产出 `layer.label.zoomBands`（既有字段）。`decimate_budget` 保留在有界解释面作为 display-budget 披露，**不自动改写**文档级 `thresholds.maxFeatures`（R1 F5：原实现投影后被两条落笔路径丢弃，且改写会影响存量导出语义）；仅 `include_budget=True` 显式请求时才返回该投影。policy 持久化在**既有开放 dict** `layer.scale_profile.g27_scale_portrayal` 命名空间内 —— 不新增 schema 字段、不 bump MapSpec 版本、不重生成 `types.generated.ts`。旧 spec 无该键 → 零改动（A01）。

**修复的真实漂移**：`scene_lod.LOD_BANDS` 的 [3,19) 是参考区间；直接投影会让 z<3 与 z≥19 出现无档死带，且前端 `activeBandIndex` 与后端 `lod_for_zoom` 的 [3,18] 钳制在 z=19 处 `sizeRatio` 不一致（1.0 vs 1.1）。`spec_label_zoom_bands()` 只把首档 `minZoom` 钳到 0、末档 `maxZoom` 钳到 24，四档 topRatio/sizeRatio 与相邻边界一字不改；共享 corpus 双向漂移闸锁定（A03/A04）。

### D3 — 生产接线 + observed/desired 分离

- 后端：`analysis_cartography_converter.py` 的既有 C3 落笔面加 **opt-in 补丁**，仅在 layer 显式声明 policy 时写 4 档 zoomBands；authored `zoomBands` user-wins。
- 前端：`normalizeLabelStrategy` → `buildTextSizeExpr` / `bandTopRatio` / `selectTopLabels` 是既有真实 runtime 消费者；`runtime.ts` 既有「跨档才重算」zoomend 链（幂等、有界）保证快速缩放不每帧重算。
- 新增 `webgis:zoom` CustomEvent 是**只读广播**，供 UI 显示 observed camera；zoom 手势绝不写 desired revision。显式策略修改才经 REST → service → `UpsertLayerIntent` + `apply_gis_mutation`（CAS / undo / provenance）。

### D4 — 出版等价视窗（只改 scale resolver seam）

`publication_ir.plan_scale_contexts()` 是**加性解析入口**，逐页传入真实 frame extent / CRS / 逻辑 viewport / target_dpi；不修改 `PublicationIR` 模型（fingerprint 逐位不变）。DPI 只影响采样与物理尺寸；非 Web Mercator 或缺有效纬度 → `scale_denominator=None` + 披露，不对所有 CRS 套固定公式。`publication_export.py` 仅在 `layout.scale_viewport` 显式声明时调用，旧项目零变化。

### D5 — 科学语义与有界解释

小尺度省略标注/符号是 **display-only**，固定 disclosures 明确：不写回 source / 分析结果 / 统计 count；`radius_px`（屏幕像素）≠ `bandwidth_m`（真实距离），跨 zoom 不保证同量纲；`decimate_budget` 与 data-plane 传输 LOD 均非统计样本。legend / colorbar / labels 组件恒 `full_domain`，不新增未经 renderer 支持的 variant，不改布局矩形；组件状态经 `inspect_scale_portrayal` 真实回执成为生产消费者（R1 F5），但**未接真实 renderer 变体渲染**（ACCEPTANCE 标 partial）。`bounded_explanation()` 字段/数组/字节全有界，裁剪优先保留 diagnostics。

### D6 — 明确不做

不重做 G15 frame、G16 逐要素标注、G20 raster、G24 MVT、G26 混合 PDF；不升级 Pi vendor（G22 独占）；不改数据传输 LOD 为分析语义；不扩展 LiDAR/SAR/3D terrain；不新增通用算法库/模板大批次；不新增 mutation intent（避免触碰 registry 一致性 gate）；不接管 R1 issues；不把 #2084/#2085/#2086 的修复计为本方向工作量。

## 后果

- **兼容**：全链路 opt-in。旧项目 / 未知 schema / 无效 policy 逐位不变且 typed 降级；新策略回退 = 移除命名空间子键与自投影 zoomBands（authored 值保留）。
- **权限**：REST 挂 `require_owned_session`；工具走 ToolRegistry tier/session/CAS 守卫；未放宽任何 user-wins / 锁 / 取消语义。
- **性能**：纯函数、零 IO；每档切换 ~0.33 ms、峰值 18–34 KB、5 zoom 仅 4 次状态更新、0 次 frame mutation；不遍历/上传要素属性。
- **未实证**：真实 Pi 子进程 RPC 全链、本地真实浏览器 E2E 被宿主 `ulimit -v` 4 GiB 硬限（所有 wasm 工具链无法实例化）阻断，见 `planning/G27-SCALE-AWARE-PORTRAYAL/TEST_EVIDENCE.md`。vitest 用例已入库供 CI。
