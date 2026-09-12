# AC-03 交付台账（任务 → 文件 → 测试 → 证据）

分支 `adaptive-cartography/03-adaptive-symbology`（基线 09d839d3）· 提交 8b661049 + 72da2f55 · 2026-09-13

## P0 勘察（只读）

| 任务 | 产物 |
|---|---|
| 硬编码点销项表 | `docs/dev/ac-03-hardcode-ledger.csv`（116 行；A/B/C 三级分级，C 级逐条附理由） |
| 分类法/色带使用分布 | `docs/dev/ac-03-symbology-recon.md` §2（std_dev 生产零到达、YlOrRd ×12 滥用、Greens/Reds/Dark2/Magma/Inferno/PuOr 代码零使用） |
| 62 SEED 模板 payload 盘点 | recon §3（28 thematic = 27 choropleth + 1 heatmap；method/k/palette 分布表） |
| 同数据多入口一致性基线 | recon §4（改造前 4 入口 4 种答案实测） |
| S1 subagent（Explore，全程第 1 个） | 全仓硬编码扫描原始数据（已并入 ledger） |

## P1–P4 引擎

| 任务 | 文件 | 测试 |
|---|---|---|
| resolve_symbology + SymbologyDecision | `app/lib/cartography/symbology.py`（新） | `tests/unit/lib/test_symbology_engine.py`（37 条：优先级/k 规则/上下文/裁剪/确定性/序列化） |
| CVD 模拟（Machado 2009）+ print 变换 + 采样器 | `app/lib/cartography/palettes.py`（+128 行） | `tests/unit/lib/test_symbology_golden.py`（11 条数值锁定）+ 引擎套件 CVD/print 断言 |
| k 裁决（n<8/唯一值/密度/可分辨上限，[3,7]） | symbology.py `_adjudicate_k` | 引擎套件 5 条 + golden k_cap 数值 |
| clip_policy 四态 + apply_clip | symbology.py | 引擎套件 6 条 |
| choose_classification 近均匀空池修正 | `app/lib/cartography/visualization_plan.py`（±5 行） | `test_choose_classification_uniform_picks_interval`（原 14 条 planner 测试全绿）+ 引擎 `test_uniform_no_pool_picks_equal_interval` |

## P5 接线（销项）

| 入口 | 站点 | 对应测试（全绿） |
|---|---|---|
| `CartographyService.build_thematic_style` | `cartography_service.py`（默认 None=裁决 + decision 透传 + clip 应用 + 模式感知缺省） | test_cartography.py、test_apply_template_contract_557.py、一致性矩阵 |
| `create_thematic_map`（+`create_3d_extrusion_map`） | `app/tools/cartography.py`（args k/palette 默认 None；symbology_decision/classification_plan 下发） | test_cartography.py（28）、一致性矩阵、extrusion 既有套件 |
| `h3_binning` | `app/tools/advanced_spatial.py` | test_gis_round_865_873.py（11）、一致性矩阵 |
| `heatmap_data` | `app/tools/spatial.py`（family↔canonical 映射 + palette_context 显式声明） | test_heatmap_contract.py、test_690_heatmap_guard.py（34+38） |
| `apply_template` | `app/tools/templates.py`（payload → recommended；lisa/categorical 结构模式保留） | test_apply_template_contract_557.py（21）、62 模板对照（59） |
| 次级：composite ThematicSlot（含 lisa 语义路由） | `app/services/mapspec/composite_builder.py` | test_composite_builder.py（5） |

## P6 legend_spec v2

- `app/lib/cartography/thematic_spec.py`：加字段 `k/palette_id/clip_policy/why/nodata_label/out_of_range_label/out_of_range/context`；`upgrade_legend_spec_v2` + `apply_symbology_v2`；normalize 保持 v1 归一语义。
- Schema 快照（冻结）：`docs/dev/ac-03-legend-spec-v2.schema.json`。
- 测试：`tests/unit/test_legend_spec_v2.py`（13：加字段/升级加法性/log 回原域/print 输出变换/非法上下文 fail-loud/schema 一致性）。

## P7 一致性回归

| 门禁 | 测试 | 结果 |
|---|---|---|
| 6 数据集 × 5 入口同一决策 | `tests/unit/test_symbology_consistency_matrix.py` | 8/8（4 入口全字典相等 + heatmap 语义字段相等 + 期望裁决锁定 + out_of_range 披露） |
| CVD/print ΔE/ΔL + WCAG 逐条断言 | 引擎套件 + golden | 全绿 |
| golden 数值 | `tests/unit/lib/test_symbology_golden.py` | 11/11 |
| 62 模板偏好对照（含 28 thematic 端到端） | `tests/unit/test_template_preference_contract.py` | 59/59 |

## P8 收口

- 销项归零：`scripts/symbology_audit.py` → `[OK]`（allowlist = ledger C 级例外；gis_harness 只报告）。
- `CHANGELOG.md`（Unreleased 段）、`docs/adr/0152-adaptive-symbology-engine.md`、`docs/dev/ac-03-decisions.md`（14 条决策日志）。
- 深化杠杆：`docs/dev/ac-03-palette-context-matrix.csv`（18 色带 × 6 上下文 max-k/Δ值矩阵）、`docs/dev/ac-03-template-preference-matrix.csv`（62 模板偏好→裁决对照）、audit 脚本。

## 门禁证据（本机，无 CI）

- ruff（变更 16 文件）：`All checks passed!`
- 全量：`pytest tests/unit -q -n 2 -m "not heavy and not real_services and not perf"` → 10804 passed / 111 skipped / 44 failed（**44 全部为基线预存环境失败或并行抖动**：20+10 与纯净基线 worktree 09d839d3 单进程逐条一致复现——extensions_platform 沙箱/资源限制（Windows 无 bwrap）、postgis 驱动、Redis/socket 类；其余 14 为 -n 2 并行抖动，单进程在本分支与基线均通过，如 geocompute_v5/v6 38 passed）。终端原文见 PR。
- 最终修复后树复跑：见 PR 描述（final gate log）。

## 边界遵守（§8）

未触碰：`frontend/**`、`app/services/gis_harness/**`、`app/services/spatial_quality_service.py`、`migrations/**`、`.github/workflows/**`。
灰色地带披露：`composite_builder.py`（recon ledger B 级接线点，§8 未列但属制图链路；decisions #14 披露）。
