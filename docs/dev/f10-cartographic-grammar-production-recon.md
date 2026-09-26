# F10 — Cartographic Grammar Production Adoption · Recon（只读勘察）

- 基线：`origin/master = 9e1ad22907e99cd7b4721153294448ce6496c717`（2026-09-24 05:00 +0800，
  Merge PR #1494 dependabot pandas）。执行时重新 `git fetch origin --prune` 后确认即为最新。
- 主 checkout（`/home/kevin/project/webgis-ai-agent`，本地 master `d5315716`）**落后 origin/master
  187 个提交且不包含 #1480/#1488 的 grammar 文件**；另有 4 个与本方向无关的脏文件
  （geo_raster env/reader、endpoint-scope-matrix）。本任务全部工作只在独立 worktree
  `wt-webgis-f10-cartographic-grammar-production-20260926-9e1ad229`（branch
  `zcode/f10-cartographic-grammar-production-20260926-9e1ad229`）进行，主 checkout 零改动。
- 同刻并发 worktree：F08/F09/F11/F12/F13/F14/F15（同基线）。冲突热区预判：F11（template
  composition）、F12（map plan compiler）、F15（visual observation repair）——本分支尽量以
  新模块为主、对共享文件做小接线。

## 1. GitHub 状态（执行时实读）

- **Open PR**：仅 #1489（dependabot docker node 镜像升级）——与本方向零文件交集。
- **最近 merged**：#1490–#1496 全部 dependabot；功能性最近波次为 #1479–#1488（2026-09-21 合入）。
- **Issues**：open 仅 #1377（质量项延期跟踪）、#1436（前端 i18n）；最近关闭为 #1346–#1445
  审计批次（与制图语法无直接交叠）。
- **本方向锚点 PR**：
  - **#1480**（feat(carto): Cartographic Grammar 基座，ADR-0205/0204）：`visual_variables.py`
    (510L)、`grammar_solver.py` (690L)、`grammar_types.py` (59L)、`scale_rules.py` (211L)、
    `quality_loop.py` grammar_audit 段；接线证明仅 `create_thematic_map` 与 `apply_template`
    两处。其 **Out of Scope 原文**即本方向工作包。
  - **#1488**（feat(gis): measurement semantics，ADR-0207）：`app/lib/gis/measurement.py`
    (683L, 11 值 `MeasurementKind` + `derive_field_semantics` + `measurement_to_data_kind`)、
    `field_resolver.py`、`scale_semantics.py`、`dataset_profile.py`/`semantic_profile.py`。

## 2. 复核 #1480 Out-of-Scope 在最新 master 上是否仍成立（逐条）

| #1480 Out-of-Scope 声明 | 最新 master 现状 | 结论 |
| --- | --- | --- |
| 表达选择在 recipe/harness 全面接线 | `planner.py:1044-1060` 直用 `recipe.primary_cartography`，无 grammar 资格检查；`plan_candidates.py:196-214` 只按 intent/质量评分 | **仍缺失（M2）** |
| `spatial.py`/`advanced_spatial.py` 等其余 `symbology_decision_from_values` 调用点迁移 | 共 11 个生产调用点，#1480 只接 2 个（详 §3） | **仍缺失（M1）** |
| `review_and_repair_cartography.grammar_decision` 生产传入 | quality_loop 参数已在（:561-618），但全部 6 个生产调用方都不传（lifecycle_engine:2668/3206、mapspec_store:344、runtime_validator:132、auditor:146、cartographer:446） | **仍缺失（M3/M4）** |
| diverging golden corpus | 仅有端到端色带族断言（test_grammar_entry_wiring_v1） | **仍缺失（M6）** |
| 类别收纳执行器 | `_collapse_spec`（grammar_solver.py:455-462）只声明；`cartography_service.py:103-190` 有 legend-only 部分收纳；`cartographer.py:257-278` 无收纳且颜色循环会产生误导图 | **仍缺失（M5）** |

补充发现（任务书未列，实际存在）：
- **测量语义双引擎重复**（O1–O4）：`MEASUREMENT_KINDS`(8) vs `MeasurementKind`(11) 两套词表、
  `derive_data_kind` vs `measurement_to_data_kind` 两个 data_kind 推导点、两套名称词素表；
  `create_thematic_map` 每请求**同时跑两个引擎且不协调**（cartography.py:282 与 :341）。
- 第 11 个未接线调用点：`create_extrusion_layer`（cartography.py:577）。
- `scale_rules.ScaleTier.visibility_hints/boundary_detail` 声明了但全仓无消费者（:47-48 自述
  "渲染端消费者按现状落地"）。

## 3. Grammar Adoption Matrix（`symbology_decision_from_values` 全部生产调用点）

权威签名（post-#1480/#1488，symbology.py:754-770）：已接受
`data_kind`/`measurement_kind`/`recommended_*`/`origin`。唯一裁决仍是 `resolve_symbology`（:663）。

| # | 调用点 | 宿主 | 现状 | 可用证据 | F10 接线 |
| --- | --- | --- | --- | --- | --- |
| 1 | `app/lib/harness/scale_matrix.py:128` | eval 矩阵 run_combo | 硬编码 sequential；合成值 | `combo.map_type`/`data_state` | 以 map_type 为字段名过统一推导，工件入 artifacts |
| 2 | `app/lib/cartography/symbology_v2.py:219` | extrusion_dual_channel | 无 | `color_field` 名 + 值 | 统一推导 → data_kind/measurement_kind |
| 3 | `app/lib/cartography/thematic_spec.py:197` | build_graduated_spec（decision 为 None 分支） | 无 | geojson+field | decision 缺失分支过统一推导 |
| 4 | `app/services/cartography_service.py:82` | build_thematic_style 数值分支 | 无 | geojson+field | 统一推导；结构分支 categorical/lisa 不动 |
| 5 | `app/services/mapspec/composite_builder.py:405` | CompositeMapSpecBuilder | 无 | `thematic_slot.field` | 统一推导 |
| 6 | `app/services/agent_swarm/specialists/cartographer.py:294` | CartographerSpecialist.compose | 数值分支无；**类别分支无收纳且颜色循环误导** | survey categories/values | 数值分支统一推导；类别分支接 collapse 执行器（M5） |
| 7 | `app/tools/spatial.py:146` | heatmap_data 调色裁决 | 无 | weight_field 名+值 | 统一推导 + nominal→热力不适用披露 |
| 8 | `app/tools/advanced_spatial.py:2640` | h3_binning | 无（但 stat 语义已知：count/sum/mean） | stat_field_name | 显式 measurement（count→absolute_quantity 族）零漂移入档 |
| 9 | `app/tools/cartography.py:346` | create_thematic_map | **已接（参考实现）**但双引擎不协调 | 全量 | 改走统一 adapter（单次推导） |
| 10 | `app/tools/templates.py:695` | apply_template choropleth | 已接（结构模式跳过） | payload+FC | 改走统一 adapter |
| 11 | `app/tools/cartography.py:577` | create_extrusion_layer | 无 | c_field | 统一推导 |

## 4. 关键契约事实（实现必须遵守）

- **裁决权威不可动**：`resolve_symbology`（ADR-0152）、`classify`/`choose_classification`、
  `label_plan`（DEFAULT_ZOOM_BANDS 与 scale_rules 有 import 期契约断言）、
  `semantic_checks.py`（grammar 只读共享 `_VISUALVAR_*` 常量）、`required_components_for`。
- **user-wins**：`pinned_representation/channels/palette`、`fields[].measurement` 显式 pin
  （solver 记 user_wins，冲突只披露不覆盖）；模板 payload 偏好降级为 recommended；
  legend `visible=False` 的 suppressed_repairs。
- **grammar 求解确定性**：同输入 `model_dump()` 逐字节相等；指纹 sha256(版本+规范化输入)；
  `GRAMMAR_VERSION` 任何裁决语义变化必须 bump（grammar_types.py:15）。
- **兄弟键先例**：MapSpecLayer 的 `provenance`/`heatmap`/`correction_hint`/`context_role`/
  `extrusion`/`legend_spec` 均为 compiler 透传的兄弟键；`quality_loop._presentation_copy`
  深拷白名单含 `provenance`/`cartographic_intent`（:482-487）——layer 级 grammar 工件
  需加入该白名单才能在 review/repair 拷贝中存活。
- **披露载体**：`classification_plan.grammar`（cartography.py:370-373）、
  `layer_meta.measurement_checks`、`symbology_decision.to_dict()`、`correction_hint`、
  review checks 稳定点分码、`GRAMMAR.{MEAS,CHAN,REP,PAIR,SCALE,PIN,AUDIT}.*` reason codes。
- **测试基线**：`tests/cartography` 在 worktree 上 collect-only 1929 用例无收集错误
  （repo venv：`/home/kevin/project/webgis-ai-agent/.venv/bin/python`）。

## 5. 五表

### Overlap（重复面，需统一而非新增）
| 项 | 位置 |
| --- | --- |
| 测量词表 8 vs 11 | visual_variables.py:46 vs gis/measurement.py:60 |
| data_kind 推导 ×2 | visual_variables.py:319 vs gis/measurement.py:631 |
| 推断引擎 ×2（词素表不同） | visual_variables.py:371 vs gis/measurement.py:435 |
| create_thematic_map 双引擎并发 | cartography.py:282 + :341 |
| zoom 带表 ×2（0/8/11/14 vs 3/6/10/14） | label_plan.py:469+scale_rules.py:53 vs scene_lod.py:20（并存心智模型，契约测试锁定边界相等性） |
| 手写 categorical 发射器 ×3 | cartography_service.py:111-186、cartographer.py:257-278、composite_builder.py:435-442 |

### Already Done（不可重建）
D1 Grammar solver 版本化+指纹+user-wins+audit；D2 resolver 已收 data_kind/measurement_kind；
D3 两处入口接线+classification_plan.grammar；D4 quality_loop 只读 grammar_audit；
D5 scale_rules 分带锁定+密度门；D6 collapse 声明式 spec；D7 #1488 量纲画像栈；
D8 categorical 溢出 legend+paint 收纳（cartography_service）；D9 golden/契约测试基建
（tests/cartography/golden_corpus/、golden_diff.py）。

### Still Missing（F10 交付面）
M1 九个调用点迁移；M2 recipe/harness 表达接线；M3 grammar_decision 生产传递（6 调用方）；
M4 MapSpec layer 记录 GrammarDecision；M5 collapse 执行器（数据+图例+tooltip 同口径）；
M6 diverging/多态 golden corpus；M7 测量语义统一（单一推导源）；M8 未知语义保守降级披露；
M9 visibility_hints 消费面。

### Must Not Touch
N1 resolve_symbology 裁决权；N2 user pins；N3 choose_classification；N4 label_plan 权威；
N5 semantic_checks.py；N6 legend user lock/suppressed_repairs；N7 冻结词表（改动必须走
GRAMMAR_VERSION bump）；N8 既有 golden 基线（sequential 路径字节稳定）。

### Integration Seams
S1 每调用点：`derive_semantic_inputs(...)` → `symbology_decision_from_values(..., data_kind=,
measurement_kind=)`；S2 lifecycle_engine:2668/3206 从 layer 收集 decision 传入
review_and_repair；S3 converter `_build_layer`/`_presentation_copy` 白名单挂 `grammar_decision`
兄弟键；S4 planner.py:1044-1060 表达资格 + recipe fallback 链；S5 三处 categorical 发射器接
collapse 执行器；S6 `derive_semantic_inputs` = #1488 画像优先 + #1480 证据补残 +
11→8 投影单点；S7 披露走既有载体；S8 golden corpus 进 tests/cartography/golden_corpus/。
