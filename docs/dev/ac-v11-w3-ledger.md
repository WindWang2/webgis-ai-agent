# AC-V11 W3 交付台账（数据链路深化）

> 波次:W3 · ADR-0163 · 状态:已完成 · 回滚点:tag `ac-v11-w3`

## 交付清单（任务 → 文件 → 测试 → 证据）

| # | 任务 | 文件 | 测试 | 证据 |
|---|---|---|---|---|
| W3.1 | 阈值单点化 | `app/lib/cartography/data_tiers.py`（新：三锚点 + select_data_strategy 连续策略）；7 站点转换（mapspec_source/mapspec_to_svg/label_plan/dot_density/data_quality/spatial_quality_gate/cost_model） | `test_data_tiers_and_matrix.py`（grep 断言 + 锚点冻结 + 阶梯 + 确定性，5 例） | 数值零变化（纯 import 重构） |
| W3.2 | 50k/500k 基线 | 同上策略函数 + 流水线参考步骤 | `test_data_scale_baseline.py`（3 例：策略路由/50k 全链路 <5s/500k 收紧） | 50k 实测 ~1s |
| W3.3 | 栅格动态拉伸 | `app/services/raster_stretch.py`（新：量化 payload + decode 参照）；`frontend/lib/map-kit/raster-stretch.ts`（镜像 applyRasterStretch）；`golden_corpus/raster_stretch/basic.json`（双端 fixture） | `test_raster_stretch_payload.py`（4 例：有界/ nodata/ 双路径 parity ≤3/冻结）+ TS 镜像 5 例 | 双路径（动态 vs 烘焙）同格差 ≤3/通道；烘焙兜底保留 |
| W3.4 | 19×11 修复矩阵 | `app/services/spatial_repair_matrix.py`（新：19 夹具 + 单 op 实测 runner）；`golden_corpus/repair_matrix/matrix.json`（209 格冻结） | `test_data_tiers_and_matrix.py` 矩阵 4 例（形状/冻结/关键有效格/no_effect=复核线索） | 实测：12 有效/10 需计划上下文/116 明示不可修/71 交叉效应/0 error |
| W3.5 | 增量更新 | `frontend/lib/mapspec-runtime/source-diff.ts`（新：确定性 diff + 策略）；`renderer.ts`（unchanged 跳过 + 2000 上限守卫） | `source-diff.test.ts`（5 例）+ renderer 回归 38 绿 | 服务端重发/会话恢复零成本路径生效 |

## 验收对照（任务书 W3 验收项）

| 验收项 | 状态 |
|---|---|
| 三档阈值单点化（grep 断言） | ✅ |
| 50k 基线建立且性能达标 | ✅ |
| 19×11 修复矩阵完成且每格有结论 | ✅（含 mapped_no_effect 的结构性解释 = W7 输入） |
| 栅格动态拉伸端到端可用 | ✅ 协议+双端+parity（renderer 消费接线 → W6，见 ADR §4） |
| 增量更新有测试 | ✅ |

## 数值（与 W2 对照）

| 指标 | W2 | W3 |
|---|---|---|
| 阈值字面量站点 | 7 处散落 | 1 单点 + grep 断言 |
| 修复映射矩阵 | 纸面 `_CODE_TO_OP` | 209 格全实测冻结 |
| 栅格换带请求 | 必须服务端重渲染 | 动态路径零请求（导出兜底保留） |
| 图层同内容更新 | 全量 setData（≤2000 要素面） | diff 跳过 |

## 遗留与移交

- 前端三档阈值同义词表（renderer.ts / filter-evidence.ts / table-data.ts）→ W4/W7。
- 栅格 payload 的 renderer 消费（动态通道替换烘焙 image）→ W6。
- `updateData` 增量应用（add/update/remove 分段）→ W6/W7。
- 修复矩阵的 mapped_no_effect 10 格 → W7 自愈策略库的参数化上下文输入。
