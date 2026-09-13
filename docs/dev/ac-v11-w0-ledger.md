# AC-V11 W0 交付台账（契约重铸与债清）

> 波次:W0 · ADR-0160 · 预算 0.9 亿 · 状态:已完成
> 回滚点:tag `ac-v11-w0` · 门禁证据:见「验收」段（终端原文在 PR）

## 交付清单（任务 → 文件 → 测试 → 证据）

| # | 任务 | 文件 | 测试 | 证据 |
|---|---|---|---|---|
| W0.1 | C2 IR 定稿 | `app/lib/cartography/layout_description.py`（+LAYOUT_IR_VERSION=2/validate/build）、`frontend/lib/layout/ir.ts`（新增）、`tests/cartography/golden_corpus/layout_ir/basic.json`（新增） | `tests/cartography/test_layout_ir_golden.py`（13 ✅）、`frontend/lib/layout/ir.golden.test.ts`（5 ✅） | 双端消费同一 fixture 逐字段对拍 |
| W0.2 | 标注排版单点化 | `app/lib/cartography/label_typography.py`（新增，单一实现）、`label_engine.py`/`label_collision.py`（薄适配层，公共名 re-export） | label 全家（engine/collision/export_twin/plan 62 ✅）+ svg/export 消费方 48 ✅ | golden corpus 不动而全绿 → 行为零变化 |
| W0.3a | composition_selection 契约 | `composition_selection.py`（+composition_alternatives_payload，版本 1）、`golden_corpus/composition_selection/school_distribution.json`（新增） | `tests/cartography/test_composition_selection_wiring.py`（6 ✅） | W5 接线面定稿（唯一许可调用形态） |
| W0.3b | ts_projection 钩子 | `ts_projection.py`（+projection_drift/regenerate_projection）、`registry_validation.py`（漂移自检，缺失不判） | `test_ts_projection_contract.py` 既有 byte 级漂移面 ✅；gis_harness 套件 1318 ✅ | 启动自检接线（存在但漂移→报） |
| W0.3c | golden_diff app 入口 | `app/lib/harness/golden_validation.py`（新增）、`registry_validation.py`（自检注册） | 同上套件覆盖（内存合成 PNG 自检） | harness 层消费 G7 归零 |
| W0.4 | 兜底常量单点 | `app/lib/cartography/defaults.py`（新增）；classify/cartography_service/tools/templates/thematic_spec/model_library/bivariate/composite_builder/template_schema 共 8 文件 15 处改引 | `tests/cartography/test_defaults_single_source.py`（10 ✅）+ 消费方 44 ✅ | grep 断言：兜底形态清单归零 |
| W0.5 | 门禁 --smoke | `scripts/quality_gate_local.sh`（+--smoke）、coverage_cartography_gate/golden_baseline/quality_ratchet_gate/quality_trend_report（各 +--smoke） | 端到端 `--smoke` exit 0（lane 全绿/ golden 不起浏览器/ ratchet 小窗/ 趋势控制台） | 冒烟不伪造全量结论（文档化） |
| W0.6 | 44 码契约矩阵 | `docs/dev/ac-v11-contracts/semantic-checks.v1.json`（新增，冻结） | `tests/cartography/test_semantic_checks_contract.py`（8 ✅） | 码清单↔源码扫描逐名对拍；blocking 三码冻结；not_evaluated 化解表 9 条（W1/W2/W3/W7） |
| — | 复核纪要 + 债扫描 | `docs/dev/ac-v11-review-memo.md`、`docs/dev/ac-v11-debt-scan.csv`（S1 产出） | — | §0.2 全部证据链 |

## 验收对照（任务书 W0 验收项）

| 验收项 | 状态 |
|---|---|
| 双实现调用收敛到 1 处（旧模块仅适配层） | ✅ label_typography 单点；engine/collision 薄适配 |
| IR schema 冻结并有 parity 骨架测试 | ✅ LAYOUT_IR_VERSION=2；双端 golden 对拍 |
| 硬编码 grep 断言通过 | ✅ 8 文件清单归零 + 消费方签名绑定断言 |
| `quality_gate_local.sh` 可执行且首轮基线入库 | ✅ 本就 +x；基线 48.55% 如实记录于复核纪要（本机 vs 作者环境差 2pt，W0 新测试抬回） |
| 44 码契约测试全绿 | ✅ 8 契约测试 + 化解表入 ADR-0160 |

## 门禁证据（W0 完成时，SKIP_BROWSER=1 完整形态）

见 PR（终端原文）；要点:cartography 覆盖率 ≥50（W0 新增 ~37 个测试）→ 门禁四步全绿。

## 遗留与移交

- keep-upright 双语义统一 → W4（TS 同步改 + corpus 重生成）。
- `composition_alternatives_payload` 生产接线 → W5（component_composer 主链路）。
- IR 三渲染器收敛与渲染器等价性测试 → W6。
- not_evaluated 化解表按波次落地（W1/W2/W3/W7）；plan 落地后真缺证据保留尾态。
- 孤儿模块清理（catalog_docs/design_system/export_component_catalog/isoline_model/
  layout_solver/style_tokens）→ W9（确认零引用后删）。
- `layout-math.test.ts` 无对应实现文件 → W9 残渣清理。
