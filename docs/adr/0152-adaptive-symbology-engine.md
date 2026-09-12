# ADR-0152: 自适应符号化引擎 —— resolve_symbology 唯一裁决、CVD/print 上下文与 legend_spec v2

日期：2026-09-13
状态：Proposed（随 adaptive-cartography/03-adaptive-symbology 分支交付）
前置：ADR-0073（Cartographic Planner C3：choose_classification 分布驱动裁决）、
ADR-0078（legend_spec 单一真相契约）、ADR-0118（Professional Cartographic Rendering V5）
任务书：adaptive-cartography/03-adaptive-symbology（AC-03）

## 背景

ADR-0073 的 `choose_classification` 已实现高质量的分布驱动裁决（重尾→head_tail、
近均匀→equal_interval/quantiles、其余→natural_breaks，rejected/authority 一等化），
但全仓只有 `create_thematic_map` 一处接线；其余入口仍是硬编码：

- `CartographyService.build_thematic_style` 签名默认 quantiles/5/YlOrRd；
- `h3_binning` 完全硬编码 quantiles/5/YlOrRd；
- `apply_template` 把 62 个 SEED 模板 payload 的 method/k/palette 当**命令**执行；
- 色带侧有 CIEDE2000/WCAG/感知均匀工具但**无 CVD 模拟、无 print 变换、无上下文裁决**；
- 离群值把 quantile 色带拉爆且无任何披露。

后果：同一份数据走不同入口出图质量参差（P0 勘察实测四入口四种答案，
见 docs/dev/ac-03-symbology-recon.md §4）——这是"自适应制图"最刺眼的裂缝。

## 决策

### D1 — `resolve_symbology` 唯一裁决入口（`app/lib/cartography/symbology.py`）

```
resolve_symbology(profile, intent, constraints) -> SymbologyDecision
```

纯函数、无 IO。`SymbologyDecision{method, k, palette, clip_policy, context,
reasons[], rejected[], confidence, source, low_confidence, clip_low/high, n_clipped}`
为 pydantic v2 一等工件，随五个入口的结果下发；`rejected[]` 同时是 09 线自愈的
可替换动作清单（kind ∈ method/k/palette/clip，统一 {kind, value, reason} 形状）。

**裁决优先级**（§0.4 默认决策表落地）：
1. 调用方**显式** method/palette → 尊重（source=explicit），k 边界与无障碍
   硬约束校正一律写 rejected[]，禁止无声改数；
2. **重尾证据**（mean ≥ 1.5×median，ADR-0073 同阈值）→ head_tail，可推翻模板偏好；
3. 模板/模型**推荐偏好** → 证据不反对时获尊重（source=recommended）；
4. 证据不足（n<8 / 全等）→ equal_interval + k=3 + low_confidence；
5. 其余 → `choose_classification` 分布裁决（**复用不重写**，仅修正其近均匀
   空池分支按文档承诺落到 equal_interval/quantiles，此前掉进 natural_breaks 默认）。

### D2 — k 裁决（P2）

基准 k = 显式 > 模板偏好 > CLASSIFICATION_METHODS.default_k（std_dev=6 的
死元数据就此接线）。四重下修（每次留 rejected）：
n<8 → 3；唯一值数不足 → n_unique−1；屏幕要素密度（要素数/视口面积）
超软/硬上限 → −1/−2；**色带可分辨上限**——由
`min_adjacent_delta_e` 在上下文门限内反推最大可分级数（k_cap），k 以此封顶。
全程夹逼 [3,7]（沿用 ADR-0073 边界）。

### D3 — 色带上下文裁决（P3）

`PaletteContext = screen | projector | print | cvd_deuteranopia | cvd_protanopia | cvd_tritanopia`。
候选序 = 显式 > 模板偏好（审美恒居次席，族不匹配只披露不降序）> 语义族
（PALETTE_KINDS 知识）> CVD 上下文把非色盲安全色带后置；暗底（亮度<0.3）
感知均匀族前置。每个候选按**可分辨上限**接纳或落选（落选逐条进 rejected）：

- **CVD**：`simulate_cvd`（Machado et al. 2009 severity=1.0，线性 RGB 矩阵，
  落在 palettes.py）模拟后做 CIEDE2000 可分辨校验。阈值 ΔE00≥10 的校准使
  screen 下 18 条带全部通过（**零默认行为变化**），而 CVD 模拟下 ColorBrewer
  sequential 族（8–11）自然落选、感知均匀族（12.4–16.9）与 RdBu/PuOr 入选——
  "CVD 优先"由阈值涌现而非特判；RdYlGn（colorblind_safe=False）无条件后置。
- **print**：`print_desaturate`（向单调灰阶目标 ramp 35% 混合：降饱和+明度
  单调趋势）后做灰度相邻 ΔL≥0.06 可分级校验（themes.print_paper 知识同阈值）；
  定性色带在灰度打印下全部不可分级——保留族内首选并披露"打印需形状/图案
  辅助"，不虚构 Viridis 能印出可分辨类别。
- 全部颜色经 `resolve_thematic_colors` 单点解析（ADR-0078 三路分歧不回潮）。

### D4 — 离群值与值域策略（P4）

`clip_policy = none | clip_p99 | head_tail | log`：重尾 → head_tail（分类法
天然吸收长尾）；全正且跨 ≥4 个数量级 → log（log10 空间分级、breaks 回原域）；
max > 1.5×p99 → clip_p99（截断在分类前应用）；显式指定尊重。
**裁剪禁止静默**：clip_p99 实际截断时 legend 必带 `out_of_range`
{color, label, count, upper} 条目（颜色=最高类色，step 语义下越界值渲染为最高类色）。

### D5 — 五入口全接线（P5 销项）

`build_thematic_style`（签名默认改 None=裁决）、`create_thematic_map`
（tool args k/palette 默认改 None，`symbology_decision`/升级版
`classification_plan` 随结果下发）、`h3_binning`（按网格统计值裁决）、
`heatmap_data`（热力族经 HEATMAP_LEGEND_PALETTE_KEY 映射为规范色带 id 后
交引擎做上下文校验，screen 恒等映射——**默认渲染零变化**；可选
palette_context kwarg）、`apply_template`（payload 降为 recommended 偏好，
lisa/categorical 作为结构模式显式保留，`recommended` 留痕对照）。次级站点：
`create_3d_extrusion_map`、`composite_builder` ThematicSlot（旧白名单把
std_dev/head_tail/lisa 静默降级 quantiles 的缺陷一并消除）。

### D6 — legend_spec v2（P6，schema 已冻结）

**纯加字段**（ADR-0078 先例）：`k`、`palette_id`、`clip_policy`、`why`
（SymbologyDecision.why()）、`nodata_label`、`out_of_range_label`、
`out_of_range`（条件键）。v1 字段语义零变化；`normalize_legend_spec` 保持
v1 归一语义，升级走 `upgrade_legend_spec_v2`；`apply_symbology_v2` 供已持有
decision 的入口复用。JSON Schema 快照：
`docs/dev/ac-03-legend-spec-v2.schema.json`——**冻结**，06 线（paint 投影）、
07 线（图例渲染）、09 线（自愈动作 = rejected[]）按此消费，后续只允许加字段。

## 后果

- 正向：多入口不一致从"结构性存在"变为"测试锁死的不变量"（6 数据集 × 5 入口
  全字典相等）；CVD/print 从"无此维度"变为"硬约束 + 逐条断言 + 矩阵落档"；
  模板从"命令"变为"可解释的偏好"（重尾数据上 quantiles 模板不再照错）；
  硬编码回潮有 `scripts/symbology_audit.py` 门禁（allowlist 即 C 级例外台账）。
- 中性：色带裁决阈值 ΔE00=10 / 投影仪 +2 / 灰度 ΔL=0.06 是可调约束默认值，
  测试锁定行为；CVD 模拟采用 Machado 矩阵（比 Brettel/Viénot 查表法更适合
  纯函数实现，任务书允许的简化），severity 固定 1.0。
- 限制（诚实登记）：cyclic 数据类型仓内无专用色带，降级感知均匀族并披露；
  LISA 五色为制图学固定语义色，不参与色带裁决；heatmap 的 radius 默认 30px
  是 heatmap_contract 命名常量（非分级语义，C 级例外）；raster PNG 渲染族
  （Viridis/Gray）与光谱引擎连续带为栅格/光谱语义，登记不迁移。

## 关系到既有 ADR

- **延续 ADR-0073**：choose_classification 仍是分类裁决核心，本 ADR 只把它
  接进更大的一致性引擎（并修正其近均匀空池分支与文档承诺不符处）。
- **延续 ADR-0078**：legend_spec 仍是单一真相，v2 是其 additive 演进；
  resolve_thematic_colors 单点解析不被绕过。
- **不触碰 ADR-0012/0017**：CartographyService 仍是分类引擎、两个 converter
  仍是独立渲染器；本引擎是纯函数层，不新增 service。
