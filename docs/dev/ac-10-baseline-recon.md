# AC-10 基线侦察笔记（P0 recon）

- 日期：2026-09-13；分支：`adaptive-cartography/10-quality-baseline`（worktree webgis-wt-ac-10）
- 性质：只读侦察 + 三个产出文件（本文件、`ac-10-metrics-inventory.csv`、`ac-10-rule-distribution.csv` + `ac-10-rule-baseline-first.json`）。不含设计提案。
- 评测命令：`evaluate_cartography_semantics(mapspec)`（`app/lib/cartography/semantic_checks.py:1558`），分位数用 `scripts/calibrate_cartography_thresholds.py:_percentile` 的普通插值。

## (a) 度量资产清单（摘要）

逐资产明细（persisted / regression_capable / gate_used 三列语义）见 **`docs/dev/ac-10-metrics-inventory.csv`**（19 个资产）。摘要：

| 资产 | 位置 | 数量 | 持久化 | 回归能力 | 今日闸门 |
|---|---|---|---|---|---|
| 无头浏览器场景 | `tests/fixtures/runtime/` | 9 目录（mapspec.json + probes.json 各 9） | 否 | 部分 | 仅 nightly runtime-validator（REQUIRE_BROWSER=1，`test_runtime_fixture`） |
| 计算性能基线 | `tests/benchmarks/baselines.json` | 15 条（median_ms/floor_ms/iterations） | 是（git JSON，PERF_UPDATE_BASELINES=1 刷新） | 是（warn 1.75x-4x 带） | CI test-perf（`-m perf --no-cov`） |
| 传输性能基线 | `tests/benchmarks/transport_baselines.json` | 5 条 | 是 | 是 | 同上 |
| 结构基线 | `tests/quality/structural_baselines.json` | 6 条（context_chars_* + schema_count_*） | 是 | 是 | backend 车道（`test_structural_perf_gates.py`） |
| 6 条量化制图规则 | `app/lib/cartography/semantic_checks.py` | 6 条（carto.*） | **否**（内存；仅会话级 map_state 副本） | **否**（无分布基线/棘轮） | 无指标闸（单测随 `-m cartography` 跑） |
| 阈值校准脚本 | `scripts/calibrate_cartography_thresholds.py` | 5 指标 | 否（只打印） | 否 | 无 |
| 制图检查 id 全集 | 同上 | 44 个 distinct id | 否 | 部分 | `-m cartography` |
| 质量场景语料 | `app/evaluation/quality_corpus.py` | ≥5000 案例（参考对 ≥500） | 否（进程内确定性生成） | 是（计数+确定性锁） | backend 车道（`test_quality_scenario_corpus.py`） |
| 结构 golden 语料 | `tests/cartography/golden_corpus/goldens/` | **505** 个 golden JSON | 是（GOLDEN_CORPUS_UPDATE=1 才可写，写后强制红） | 是（digest+sha 逐字段比对） | `-m cartography`（test_golden_corpus.py pytestmark） |
| 制图跨系统回归 | `tests/quality/test_cartographic_regression.py` | 9 组场景（结构认证，非像素） | 否 | 部分 | backend 默认车道 |
| findings 棘轮 | `docs/quality/findings-baseline.json`（`app/lib/quality/manifest.py:38`） | 10 个 code（TOOL_UNTESTED=8；min_behavioral_dispatch=151） | 是（git 账本 + waivers.json） | 是（**质量债计数**，非制图指标） | backend 车道 + release readiness 字节闸 |
| 运行时评审证据 | `runtime_validator.py:132` / `cartography_runtime.py:640` | 每次 validate 一份 `_cartographic_review` | 内存/会话（可选 Redis 会话级） | 否 | 无 |

## (b) 六条量化规则首基线（17 场景）

方法：9 个 runtime 夹具（原始 `mapspec.json`）+ 8 个 golden 派生场景（point/line/polygon/raster/heatmap/flow/3d/categorical 各一）。逐观测见 `ac-10-rule-distribution.csv`（102 行），分布见 `ac-10-rule-baseline-first.json`。

**重大事实：golden 语料文件是结构 digest（selection/components/layout/validation + sha），不含 sources/layers，无法直接消费。** 已按各 golden digest 的 model/template，用仓库权威构件（`profile_geojson_source` + `thematic_spec.build_graduated_spec`/`spec_to_paint`）重建协议忠实 MapSpec；`scene_source` 列标注 `digest->protocol-faithful mapspec`。

| 规则 | evidence key | n | min | p33 | p50 | p66 | p90 | max |
|---|---|---|---|---|---|---|---|---|
| carto.load.ratio | load_ratio | 4 | 0.0012 | 0.0013 | 0.0018 | 0.0024 | 0.0028 | 0.0030 |
| carto.color.separability | min_adjacent_delta_e | 8 | 12.53 | 12.63 | 12.63 | 20.00 | 48.98 | 48.98 |
| carto.legend.completeness | thematic_visible_layers_count | 7 | 1 | 1 | 1 | 1 | 1 | 1 |
| carto.visualvar.overload | encoded_field_count | 10 | 1 | 1 | 1 | 1 | 1 | 1 |
| carto.label.collision_est | label_ink_ratio | **0** | — | — | — | — | — | — |
| carto.scale.svs | avg_feature_area_px | 3 | 22295.2 | 22295.2 | 22295.2 | 43698.6 | 75803.8 | 89180.9 |

缺失模式（如实记录，全部为空单元格，未造数）：
- **carto.label.collision_est 全体缺席**：17 个场景没有任何 symbol/text 层 + `layout.text-field` + profile `sampleValues` 的组合。
- **9 个 runtime 夹具没有嵌入 profile**（sources 仅 type+载体）→ 6 规则中只有 carto.visualvar.overload（只需 paint 数据驱动字段）在 6 个夹具各发 1 条 pass；load/svs/label/colorsep/legend 全部不进证据域。
- carto.legend.completeness **跳过 raster 层**（`_check_map_legend_completeness` 对 `type=="raster"` continue）→ classified_raster 场景无此检查（仅得 color.separability）。
- carto.scale.svs 仅适用 fill/fill-extrusion 且全 polygon 几何 → 仅 3 个多边形类场景发出。
- 全部 42 个有限观测状态均为 **pass**（健康场景样本；warning/fail 尾部无样本，首基线只能钉住"正常区间"，不能钉住退化形态）。

## (c) 检查 id 全集（任务书"17 carto.* checks"的核实）

**实际数字：`carto.*` 前缀的量化规则是 6 条（不是 17）；`evaluate_cartography_semantics` 及其子检查发射的 distinct check/rule id 共 44 个**（全部出自 `app/lib/cartography/semantic_checks.py`，regex 提取 `add_check(` 与 `check="..."`）。

6 条 carto.*：`carto.load.ratio`、`carto.color.separability`、`carto.legend.completeness`、`carto.visualvar.overload`、`carto.label.collision_est`、`carto.scale.svs`。

其余 38 个结构/条件 id（按主题分组）：
- 源/数据在位：`SOURCE_LAYER_REF`、`SOURCE_ADDRESSABILITY`、`RESULT_DATA_PRESENCE`、`EMPTY_DATA`（finding-only）、`RASTER_ARTIFACT_READY`、`RASTER_BOUNDS_VALIDITY`
- 可见性/透明度：`RESULT_VISIBILITY`、`OPACITY_VALIDITY`、`VISUAL_OVERLAP`（恒 not_evaluated，等像素证据）
- 坐标/范围：`CRS_EVIDENCE`、`BBOX_VALIDITY`、`CRS_BBOX_COMPATIBILITY`
- 样式字段：`GEOMETRY_LAYER_TYPE`、`PAINT_FIELD_EXISTS`、`INTERPOLATE_NUMERIC_FIELD`、`STOPS_DATA_RANGE`、`STYLE_EXPRESSION_SUPPORT`
- 专题一致性：`THEMATIC_LEGEND`、`THEMATIC_FIELD`、`CLASSIFICATION_INTEGRITY`、`LEGEND_STYLE_EQUIVALENCE`、`CLASSIFICATION_CARDINALITY`、`DIVERGENT_DOMAIN`、`PALETTE_CARDINALITY`、`NO_DATA_SEMANTICS`、`CLASSIFICATION_DOMAIN_COVERAGE`、`CATEGORICAL_DOMAIN_CONSISTENCY`、`LEGEND_FIELD_CONSISTENCY`
- 溯源：`RESULT_MAP_PROVENANCE`
- 组件布局：`LAYOUT_COLLISION`、`DUPLICATE_LEGEND_BINDING`、`COMPONENT_LINK_CYCLE`、`COMPONENT_OUTSIDE_CANVAS`
- 3D/等值线（ADR-0095）：`EXTRUSION_HEIGHT_FIELD_VALID`、`EXTRUSION_HEIGHT_DISTRIBUTION`、`EXTRUSION_PITCH_ADVISORY`、`EXTRUSION_OCCLUSION_WARNING`、`CONTOUR_LEVELS_VALID`

## (d) 明确确认

1. **calibrate 脚本不写任何文件**：`scripts/calibrate_cartography_thresholds.py` docstring 行 26「脚本绝不写文件。」；`main()` 仅 `print(render(...))`，无 open/write 调用。输出是"建议值"，按 ADR-0069 须人工审后手改 `.env`。
2. **全仓不存在像素级 golden 图像 diff**：grep（tests/ + app/ + scripts/）无 `ImageChops`、无 golden png 比较逻辑；`Image.open` 仅出现在栅格单元测试里断言 PNG 属性（band/尺寸/像素值）。cartography golden 语料是 JSON 结构 digest。**"渲染图 vs 存储黄金图"的回归通道今日不存在。**
3. **`_cartographic_review` 不持久化**：`app/services/runtime_validator.py:132` `cartographic_review = review_cartography(mapspec).to_dict()`（注意：未传 `source_profiles`）；`app/services/cartography_runtime.py:640` 经 `session_data_manager.set_map_state(session_id, "_cartographic_review", result)` 落会话 map_state —— 内存 dict（可选 Redis 会话后端），无 DB、无 migration、无跨运行文件。与 `docs/cartographic-closed-loop.md:235-236` 一致：「Evidence uses existing in-memory/session/harness retention; no database or migration is introduced.」
4. **CI backend 车道排除 cartography**：`.github/workflows/production.yml:202`
   `run: pytest --cov=app --cov-report=xml --cov-report=term-missing --cov-fail-under=75 --timeout=60 --timeout-method=thread -m "not perf and not cartography and not real_services" -v`
   （:196-201 注释：REL-01 审计，cartography 集合由独立 job 拥有）。专属闸门：`:399` `cartography-smoke` job、`:452` `pytest -m cartography --no-cov --timeout=120 --timeout-method=thread -q`（release-blocking，`:725` needs 链）；nightly：`:520` `nightly-matrix`、`:558` `pytest -m "cartography or perf" --no-cov --timeout=180 ... -q`；Playwright runtime-validator lane `:454-462`（nightly + 手动，REQUIRE_BROWSER=1 硬失败）。

## 与任务书 §0.2 的出入（事实核对）

- **"17 carto.* checks" 不成立**：carto.* 量化规则 6 条；全 reviewer 44 个 distinct id。
- **golden 语料不含 sources/layers**（结构 digest），§0.2 若假设其可直喂语义评审，则不成立；本次以协议忠实重建替代并如实标注。
- **runtime 夹具无嵌入 profile**：原样评测时 6 规则几乎全部 not_evaluated（仅 visualvar 发射）；量化证据要等运行时采集 profile 后才有。
- findings 棘轮（`test_findings_ratchet_gate.py`）锁的是**工具/算法质量债**（`docs/quality/findings-baseline.json`，10 个 code），与制图指标无关。
- 三份基线文件规模：`baselines.json` 15 条、`transport_baselines.json` 5 条、`structural_baselines.json` 6 条。
- 产出的首基线全部为 pass 态健康观测（n=42），当前**没有任何跨运行机制消费这 6 条规则的数值**（无 DB、无文件、无棘轮、无 CI 阈值闸）——calibrate 脚本是唯一入口且只打印。

---

## (e) 执行期补充（主 agent，2026-09-13）

以下数字产自实现阶段的本地实测，补入本侦察记录：

- **cartography lane 覆盖率现状**（P0 第 4 项 / P4 下限参考）：`-m cartography`
  lane（708 passed, 21 skipped）下 scope 计量 `app/lib/cartography` =
  **50.10%**（7207 stmts）、`app/lib/harness` = 46.33%（1131 stmts，09 线所有，
  仅报告不设闸）、合并 49.59%。计量命令与输出见 PR 门的步骤 1
  （`scripts/coverage_cartography_gate.py`，pytest `-o addopts=` 覆盖 ini 的
  `--cov=app` 后按 scope 求和）。
- **渲染确定性实证**：9 场景 golden generate 后同机 verify，全部
  `within_ratio=1.0`、`max_channel_diff=0` —— same-env 下渲染逐像素确定，
  像素 golden 可作 0 容差回归锁；±16/48/98% 容差只为跨环境/跨版本漂移留裕度。
- **墨量实测**（golden 场景非背景像素占比）：raster-overlay 0.71、其余场景
  0.0007–0.0062 —— 证实"小要素整块消失"不会跌破 98% 像素通过线，verify 因此
  叠加墨量带校验（`|Δink| ≤ max(0.001, 0.4×ink)`）。
- 本文件 (b) 节基线已作为 42 条 provisional 基线入库
  （`quality_ratchet_gate.py baseline --from-json`），激活时机见
  `docs/dev/ac-10-decisions.md` D2。
