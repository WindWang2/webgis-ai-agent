# Spatial Science & GeoAI Platform V3（science-v3）

> 状态：随 `feat/spatial-science-geoai-platform-v3` 合入。基线 16d1c70（Foundation V3 之后）。
> 审计与决策依据：`.agent-work/science-v3/`（8 域只读审计 + 综合文档）；
> ADR-0117（Backend SDK V3）。

## 0. 这一轮是什么

Foundation V3（171 算法）交付后，本轮（Platform V3）不追求数量增长，重点
是**科学诚实性收敛与不确定性/规模契约升级**：

1. **Phase 0 全量审计**：8 个域（统计/地统计/点格局/网络/地形/光学/SAR/基建）
   逐算法核对「声明-实现-测试」三件套，产出 fake-native 检查、契约缺口、
   数值风险、规模后端、不确定性、引用验证六类 findings；
2. **Backend SDK V3**（ADR-0117）：结构化资源包络、精度分类、数值容差、
   取消画像、BackendEvidence；
3. **不确定性契约闭环**：`uncertainty_producer_tests` 机器校验
   （declared → produced → tested）+ 近似披露类型；
4. **审计修复包**：全部 BLOCKER/MAJOR/MEDIUM findings 修复；
5. **P1 科学增强**：GWR/MGWR 局地推断、克里金预测区间与 z 校准、
   MCLP 精确 MILP、EHA 热点演化、FCLS 线性解混、medoid 合成等。

## 1. Backend SDK V3（ADR-0117 摘要）

| 概念 | 落点 | 语义 |
|---|---|---|
| ResourceEnvelope | descriptor.resource_envelope | bytes/feature·cell、对预算、硬上限；select_backend 消费做估算与预警（声明面；硬闸仍在实现层） |
| ApproximationClass | descriptor/variant.approximation_class | exact/approximate/heuristic/sampling/streaming；与 approximate 布尔交叉校验 |
| NumericalTolerance | descriptor.tolerance | rtol/atol/policy（数值验证锚） |
| CancellationProfile | descriptor.cancellation_profile | none/coarse/chunk_boundary/fine（诚实披露取消能力） |
| BackendEvidence | backend_selection.BackendEvidence | 结构化选择证据（含近似披露） |
| uncertainty_producer_tests | descriptor 映射 | 声明的不确定性 → 真实产出并断言它的测试节点（validate AST 校验） |
| Benchmark Manifest | docs/science/BENCHMARK_MANIFEST.md | heavy 算法声明面投影（parity 测试锁定） |

## 2. 审计修复（要点）

- **地统计**：indicator 阈值守卫对象错误（字符串长度→阈值个数）；
  indicator typed raster_uncertainty 块；idw→kriging fallback 语义降为
  approximation；RBF/NN 补引用；kriging 变体规模窗口；插值域 complexity。
- **地形**：openness/horizon/SVF 内存重构（(n_az,h,w) 栈→逐方位累加，
  O(h·w)）+ 方位×像元联合包络；viewshed lib 护栏；水文组合链修复
  （persist_filled → 下游 D∞/D8 可消费填充面）；tarboton1997 标签修正
  （D∞ 论文）+ D8 改引 O'Callaghan & Mark 1984；viewshed 补 R3 引用 +
  approximate=True；坐标约定复核（GDAL 仿射原点=角点，contours/viewshed-xy
  半像元偏差根因修复）。
- **光学**：strict 波段语义（本地 TIFF 路径位置猜波段默认类型化拒绝，
  放行路径强制披露）；NDWI 拆名（McFeeters/Gao 不可互换）；MAD χ² dof
  2k→k（Nielsen/Canty 惯例）。
- **统计**：GWR/MGWR 逐系数局地 SE/t（sandwich 近似）+ FieldUncertainty
  typed 块；h3_hotspot 接线修复；join_count 口径同步（non-free）；
  hotspot 族真实 StatisticalSignificance 证据块；GWR 带宽敏感性 envelope。
- **网络**：p-median 错引勘误（ReVelle & Swain 1970 题录按出版方核验）；
  2SFCA 谱系引用补全；LA/accessibility/gravity/huff 统一 OD 规模闸。
- **点格局**：EHA（Emerging Hotspot Analysis）落地并修正 temporal.hotspot
  语义失配；pcf 带宽守卫（NaN 不再入 JSON）；descriptor 元数据补齐。
- **SAR**：vh_ratio dB 宣称删除；acquisition_comparability 接线；
  ENL/coherence 置信区间；热噪声 ESA 引用；dB 域显式声明参数。

## 3. P1 科学增强（新能力）

| 域 | 能力 | 出处 |
|---|---|---|
| 地统计 | 克里金 95% 预测区间面 + LOOCV z-score 校准指标 | Isaaks & Srivastava 1989 |
| 地统计 | 各向异性自动拟合（directional scan → 椭圆）+ robust variogram（Cressie-Hawkins） | Webster & Oliver 2007; Cressie & Hawkins 1980 |
| 网络 | MCLP 精确 MILP（HiGHS；枚举对拍 + 统一规模闸） | Church & ReVelle 1974 |
| 点格局 | Emerging Hotspot Analysis（Gi*+MK，17+1 类互斥完备） | ESRI EHA / Getis-Ord 1992 |
| 光学 | FCLS 线性光谱解混（丰度+RMS 残差面） | Heinz & Chang 2001 |
| 光学/SAR | medoid 时间合成 | Flood 2013 |

## 4. 验证体系

- registry validate()（含科学契约/producer tests/节点 AST）= 0；
- catalog + benchmark manifest 字节级 parity；
- oracle 语料回放全绿；golden/性质/守卫三件套随新能力交付；
- 两轮独立 review（架构/正确性/性能/安全 + 复审）零 BLOCKER/CRITICAL/MAJOR。

## 5. 已知限制（诚实口径）

- 存量算法的资源包络/复杂度声明上收按域推进（插值域已完成；
  其余域在 manifest 中显示为「未声明」而非虚构）；
- lib 层长循环取消点未全面下沉（CancellationProfile 声明优先于实现）；
- 未实现项（KED/SGS/时空克里金/HAND/breaching/solar radiation 等）
  见 `.agent-work/science-v3/02-planned-algorithms.md` 的 P2/P3 清单。
