# ADR-0163: V11 W3 — 数据链路深化（阈值单点、50k/500k 基线、栅格动态拉伸、19×11 修复实测矩阵、增量更新）

- 状态: Accepted
- 日期: 2026-09-13
- 线: adaptive-cartography/v11-master（W3）
- 关联: ADR-0160（defaults 单点纪律）、ADR-0153（修复血缘）、ADR-0159（质量事实库）、audit F31（setData 引用跳过）

## 1. 背景与靶心

缺口 G10：5000/20000/50000 三档阈值散落互不相认；栅格色带烘焙进 PNG（换带必须
重渲染）；修复 op 与诊断码的映射无实测矩阵；图层更新无增量通道。

## 2. 决策一：三档阈值单点（`data_tiers.py`）

- 常量 `TIER_INLINE_FEATURES=5000` / `TIER_SCAN_CAP_FEATURES=20000` /
  `TIER_EXPORT_FEATURES=50000` = **既有校准锚点**（数值即行为，改动走 ADR）；
  7 个业务站点（mapspec_source / mapspec_to_svg / label_plan / dot_density /
  data_quality / spatial_quality_gate / cost_model）全部改为 import。
- `select_data_strategy(feature_count, geometry_complexity, viewport)`：连续
  策略（inline 有效预算 = 5000 × 视口亚线性缩放（封顶 2×）÷ 几何复杂度，
  下限 1000）→ 阶梯 inline / scan / export / 收紧 export。**grep 断言**
  （赋值形态）锁定清单文件字面量归零；`app/core/config.py` 的 Settings 字段
  属配置层（环境可调），不在收敛清单（分层诚实）。
- 前端同义词表未收敛（G10 的前端半边 → W4/W7 与符号律/自愈阈值统一时处理）。

## 3. 决策二：50k/500k 基线（W3.2）

- `test_data_scale_baseline.py`：50k 确定性合成要素 → 策略 → 视口裁剪 →
  等距抽稀全链路预算断言（首轮 ~1s，上限 5s = 5× 余量，只拦回归性劣化）；
  500k → 复杂度惩罚收紧（聚合/MVT 流水线决策面）。

## 4. 决策三：栅格动态拉伸客户端化（W3.3）

- 服务端 `raster_stretch.py`：`build_raster_stretch_payload` —— 有限值按
  P2–P98 截断 → 线性归一 → **uint8 量化**（base64，每格 1 字节；255 =
  nodata 保留档）+ nodata 位图 + 色带 stops + 拉伸参数。载荷有界。
- 前端 `map-kit/raster-stretch.ts` 镜像 `applyRasterStretch`：同算法实时
  着色 —— 换色带/改拉伸**零请求**。
- **双端 parity 锚**（三处教训入 golden）：①舍入统一 `floor(x+0.5)`（np.rint
  银行家舍入 vs JS Uint8ClampedArray 四舍五入在 x.5 偶边界分歧）；②nodata
  契约 = **透明黑**（RGB 置零 + alpha=0）；③量化 `rint` 非截断。
  golden fixture（`golden_corpus/raster_stretch/basic.json`）双端消费，
  逐字节对拍。
- **双路径 parity**：同一阵列的动态路径着色与烘焙 PNG（`render_array_to_png`，
  保留为导出/离线兜底）同格差 ≤3/通道（量化 1/254 × stop 插值联合误差）。
- 前端动态渲染的**接线**（renderer 消费 payload）→ W6（三渲染器收敛时统一
  接线；本波交付协议 + 双端实现 + parity 锁）。

## 5. 决策四：19×11 修复实测矩阵（W3.4）

- `spatial_repair_matrix.py`：行 = `_CODE_TO_OP` 的 19 诊断码（同源词表），
  列 = `CANONICAL_OP_ORDER` 11 op；每格在该码合成夹具上**真实执行单 op**，
  以前后特征规范化 JSON 深比较记录实际变更数（op 级 evidence 的 affected
  语义因 op 而异 —— 有的记触碰数，矩阵以真实变更为准）。
- 实测结果（golden 冻结）：**mapped_effective 12 / mapped_no_effect 10 /
  unmapped_no_effect 116 / unmapped_side_effect 71 / error 0**。
- `mapped_no_effect` 是如实登记的复核线索（全部有结构性解释）：NULL_ISLAND
  需破坏性 drop_zero_coordinates；dedup 键 = geom+attrs 的语义收窄；
  crs_transform / flag 类 op 单 op 执行缺计划上下文（inference / outlier_fields /
  null_heavy_fields 参数在 plan 层）。这些格子是 W7 自愈策略库的直接输入。
- 「明示不可修」= unmapped_no_effect 116 格（每格实测无效果）。

## 6. 决策五：增量更新（W3.5）

- `mapspec-runtime/source-diff.ts`：确定性 FeatureCollection diff
  （身份 = feature.id，签名 = sortKeys 规范化 JSON）→ 策略
  `unchanged / incremental（churn ≤ 0.30）/ full_setdata` + add/update/
  removed 分档。
- renderer 接线：引用不同但内容 unchanged → **跳过 setData**（引用升级；
  F31 引用相等跳过的延伸）；规模守卫 `SOURCE_DIFF_MAX_FEATURES=2000`
  （diff stringify 成本 O(n)，超大集合 diff 自身可能比全量 setData 贵 ——
  trimmed 视口集合 ≤ 渲染预算是目标面）。
- `updateData` 增量通道的应用（按 diff 的 add/update/remove 分段）→ W6/W7
  随渲染器收敛接线（本波交付决策面 + unchanged 跳过已生效）。

## 7. 验收对照

| 任务书 W3 验收 | 状态 |
|---|---|
| 三档阈值单点化（grep 断言） | ✅ data_tiers + 7 站点 + 赋值形态断言 |
| 50k 基线建立且性能达标 | ✅ 全链路 ~1s（预算 5s）|
| 19×11 修复矩阵完成且每格有结论 | ✅ 209 格全实测冻结（error=0） |
| 栅格动态拉伸端到端可用 | ✅ 协议 + 双端实现 + 双路径 parity；renderer 消费接线归 W6（台账登记） |
| 增量更新有测试 | ✅ source-diff 5 例 + renderer unchanged 跳过（38 回归绿） |

## 8. 风险与回滚

- 阈值数值未变（锚点冻结）—— 转换是纯 import 重构，行为零变化（受影响
  模块测试全绿）；回滚点 tag `ac-v11-w3`。
- renderer 的 unchanged 跳过：签名误判风险由 sortKeys 规范化 + 2000 上限
  控制；误判后果是漏一次 setData（下轮数据变更自愈），无视觉冻结风险。
