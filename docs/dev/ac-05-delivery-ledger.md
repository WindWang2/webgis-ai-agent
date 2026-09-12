# ac-05 交付台账（任务 → 文件 → 测试 → 证据）

> 线：adaptive-cartography/05-auto-labeling · ADR-0154 · 基线 `1fd4b035`
> 门禁原文见 PR 描述；本文是一一对应索引。

| 任务 | 文件 | 测试 | 证据/说明 |
|---|---|---|---|
| §0.2 复核纪要 | `docs/dev/ac-05-label-recon.md` §6 | — | PR/issue/分支全查无重复线；`label_engine`/`label_collision` 判定为导出域，边界互不侵入 |
| §0.3 编号 | `docs/adr/0154-automatic-labeling-engine.md` | — | ADR-0154 未被占用（01→0150/02→0151/03→0152/07→0156） |
| P0.1 ground truth | `tests/fixtures/labeling_datasets.py`、`docs/dev/ac-05-label-groundtruth.csv` | `test_label_plan.py::test_all_ten_datasets_hit_currently` | 10 数据集确定性合成（seed 固定，两次构造逐位一致） |
| P0.2 失败模式 | `docs/dev/ac-05-label-recon.md` §1（F1–F7） | — | 逐条 file:line 核对（S1 勘察 + 人工复核） |
| P0.3 collision 基线 | recon §2 | `test_label_strategy_semantic_check.py`（对照） | 30 格扫描告警率 **83%**（23 fail + 2 warn） |
| P0.4 registry 差异 | recon §3 | `test_label_plan.py::test_label_layer_descriptor_registered` | 61 分类 vs 19 描述符；content 四叶子全未注册 |
| P1 字段挑选 | `app/lib/cartography/label_plan.py` | `test_label_plan.py`（确定性/准确率/拒绝工件/无字段退化/tie-break/基数惩罚） | 10/10 准确率（门禁 ≥90%）；sensors 诚实拒绝（禁 ID 标注有专项断言） |
| P2 策略编排 | 同上（`plan_label_strategy`/`build_label_spec`） | 密度三档/优先级词表/4 档 bands/spec 形状 | `DENSE=2000`、`EXTREME=20000`；top_n 档位 400/250 |
| P3 前端避让抽稀 | `frontend/lib/mapspec-runtime/label-layout.ts`、`runtime.ts` 标注段 | `label-layout.test.ts`（selectTopLabels 双策略+诚实 skip）、`runtime.label.test.ts`（thin filter 挂层/evidence） | sort-key 优先级 + Top-N filter；skipped 理由落 evidence |
| P3 降级四级 | 同上（`resolveDegrade`） | `label-layout.test.ts::degrade ladder`（L0–L4 全触发） | 缩字号→去晕圈→抽稀加密→关闭；每级 `getLabelEvents()` |
| P4 zoom 分级 | label-layout（bands/step expr）+ runtime（zoomend 再抽稀） | `label-layout.test.ts::zoom bands`、`runtime.label.test.ts::zoom band crossing`（4 档 + 幂等） | 档位 10/25/60/100%；跨档 filter 重算有 band 事件 |
| P5 样式自适应 | label-layout（`resolveLabelStyle`/luminance/CJK） | `label-layout.test.ts::adaptive style`、`runtime.label.test.ts::auto halo`（深底 mock） | 显式声明恒胜；CJK 不动字号（06 线接口） |
| P6 label_layer 注册 | `component_registry.py`（descriptor+keywords+preview）、`component_renderers.py`（矩阵条目，如实留空）、`components.py`（Literal/工厂/rebind 白名单） | `test_label_plan.py` P6 组（可寻址/自动与拒绝/rebind 局部突变/非白名单拒绝/mutate patch） | `registry.validate()==[]`；换字段 rebind 只动 options |
| P6 换字段不重建图层 | `runtime.ts`（label-only 快路径 + z-order 门） | `runtime.label.test.ts::label-only field change`（removeLayer/addLayer 事件计数） | 主层零重建；refield 事件 |
| P7 回归基座 | `scripts/label_quality_report.py` + 上列测试 | pytest 28 新 + vitest 40 新 | 准确率 100%；墨量降 91%/碰撞对降 99%（3200 点）；告警率 83%→50% |
| P7 semantic_checks 策略消费 | `semantic_checks.py`（仅标注检查段） | `test_label_strategy_semantic_check.py`（6）+ 既有 phase4 13 条回归 | evidence 带 `label_strategy` 块 |
| P8 收口 | `CHANGELOG.md`、ADR-0154、本文 | 全量 `tests/unit -q -n 2 -m "not heavy..."` + `vitest lib/mapspec-runtime lib/map-kit` + eslint/ruff 0 告警 + 一次 `next build` | 门禁原文贴 PR |

## 与 06 线的文件切分（§8 硬约束核对）

- **零触碰**：`reconciler.ts`、`paint-bridge.ts`、`map-kit/renderer.ts`（含符号/尺寸/reconcile 部分）、
  `app/lib/cartography/{symbology,visualization_plan,classify,palettes,thematic_spec}.py`、
  `frontend/components/map/**`、`migrations/**`、`.github/workflows/**`（`git diff --stat` 佐证）。
- **runtime.ts**：只动标注子层相关段（`addLayerSafe` 的 label 挂载参数、`addLabelSublayerSafe`
  重写、`applyFilterSafe` 的 label filter 合成、label-only 快路径、`hasStructuralLayerChange`
  的 label-only 例外、label 状态字段与 evidence）；删除行核对（15 行）全部属上述段。
- **compiler.ts**：仅 label 子层发射条件一处（raster/heatmap parity，4 行）。
- **新增**：`label-layout.ts`（+测试）、`label_plan.py`、`labeling_datasets.py`、
  `test_label_plan.py`、`test_label_strategy_semantic_check.py`、`runtime.label.test.ts`。
- **协调面最小触点**（双方任务书均未独占，P6/P7 必需）：`app/lib/cartography/mapspec_schema.py`
  （additive optional 策略字段 + `types.generated.ts` 再生成）、`app/services/gis_harness/components.py`
  （ComponentType/工厂/rebind）、`component_renderers.py`（矩阵条目，反向覆盖检查强制）、
  `semantic_checks.py`（标注检查段）、`.gitignore`（scripts 白名单一条）、
  `frontend/lib/map-components/component-catalog.generated.json`（按再生成纪律新增 label_layer
  条目，+23 行纯增量）。
- **密度信号单份**：阈值常量只在 `label_plan.py`；06 线若落地 `density_signal()` 本线改 import。
- **字号接口**：本线只产出 `size_ratio` / band `sizeRatio` / 降级因子，绝对基准归 06 线。
