# G41 接口契约

## 后端领域模块

`app/services/bivariate_reliability_authoring/`

| 文件 | 职责 |
|---|---|
| `contract.py` | `AuthoringPlan`（`extra="forbid"`，`PLAN_SCHEMA_VERSION=1`，`ALGORITHM_VERSION="g41-authoring.v1"`）、`BivariateSpec`、`AxisSpec`、`MeasureBinding`、`VisualPolicy`、`ReliabilitySpec`、`SourceAnchor`、`parse_plan`、`plan_digest()`、`candidate_digest()` |
| `snapshot.py` | `freeze_source()` 强版本原子冻结、`FrozenSource`、`check_anchor()`、`source_ref_of()`、`content_fingerprint()` |
| `stats.py` | `scan_pairs()` / `PairedScan`、`value_kind()` / `admit()`、`ReliabilityScan`、有界直方图/散点 |
| `classify.py` | `axis_breaks()`, `assign_axis()`, `axis_intervals()`, `compute_bivariate()`, `row_col_meta()`, `PAIR_CLASS_FIELD="__g41_pair_class"` |
| `reliability.py` | `compute_reliability()`, `RELIABILITY_BUCKET_FIELD="__g41_rel_bucket"` |
| `legend.py` | `build_bivariate_legend()` / `build_reliability_legend()`，`LEGEND_SCHEMA_VERSION="g41_legend.v1"` |
| `expression.py` | `build_*_candidate()`, `make_derived_fc()`, `expression_fingerprint()`, `AUTHORING_KEY="bivariate_reliability_authoring"` |
| `cache.py` | 有界缓存（键含源版本/内容指纹/两字段/单位/度量类型/全部参数/算法版本） |
| `service.py` | `inspect_layer()`, `preview_expression()`, `commit_expression()`（UI 与 Pi 共用） |
| `errors.py` | `AuthoringError{code,message,field,index,status_code,correction_hint,detail}` |

### typed plan（闭合词表）

```
AuthoringPlan {
  schema_version: 1
  layer_id: str
  mode: "bivariate_matrix" | "reliability_dual_channel" | "revert"
  source_anchor?: { ref_id, content_revision >= 1, content_fingerprint? }
  title?: str
  bivariate?: {
    n: 2|3
    axis_a / axis_b: { field, label?, unit?, measurement_note?,
                       method: quantiles|equal_interval|manual,
                       breaks?: float[] (manual 时必须恰好 n-1 个，严格递增) }
    matrix_id?: "BiPurpleOrange"|"BiTealRose"   # 与 colors 二选一
    colors?: "#rrggbb"[] (恰好 n*n 个，行主序，可非对称)
    boundary_rule: "upper_inclusive"|"legacy_lower_inclusive"   # 缺省 upper_inclusive
    swap_axes: bool    # 类别/色阵/计数/图例同步转置
    flip_rows: bool    # 格序/端点/箭头同步反转
    missing_label?, missing_color?, scatter_sample_size?, histogram_bins?
  }
  reliability?: {
    estimate_field, estimate_label?, estimate_unit?, estimate_method?, estimate_k?, estimate_palette?
    measure: {
      kind: standard_error|variance|confidence_interval|prediction_interval
      value_field?: str          # SE / variance
      low_field? / high_field?   # CI / PI
      level?: float (0,1)        # CI/PI 必填，不自动填 0.95
      unit?: str                 # variance 必填（平方单位）
      source: author_declared|analysis_evidence
      evidence_ref?: str         # analysis_evidence 必填
      method_note / evidence_note # author_declared 必填其一
      allow_noncentered?, require_interval_contains_estimate?
    }
    visual: { channel: opacity|stroke_width, direction, min_value, max_value,
              min_output, max_output, unknown_output, buckets(2-8), clip: clamp|reject,
              unknown_label? }
    boundary_rule?, missing_label?, scatter_sample_size?
  }
}
```

## HTTP（`/api/v1/bivariate-reliability`，`require_owned_session` + `X-Session-Token`）

| 方法 | 路径 | 请求 | 回执要点 |
|---|---|---|---|
| GET | `/sessions/{sid}/layers/{lid}/inspect` | — | 强版本源锚、有界字段分型画像、当前表达/证据、支持矩阵、共享组资格、`revision` |
| POST | `/sessions/{sid}/layers/{lid}/preview` | `{"plan": {...}}` | 零 mutation；canonical 图例（≤9 格 / ≤10 桶）、统计口径、披露、`candidate_digest`、`revision` |
| POST | `/sessions/{sid}/layers/{lid}/commit` | `{"plan", "expected_revision", "preview_digest", "client_mutation_id?"}` | 单笔 `UpsertLayerIntent`；`revision`/`mapspec_fingerprint`/`can_undo`/`mutations=1` |

`preview_digest` **必填**：任何参数/源代次变化 → `STALE_PREVIEW`（B27/B36）。

## Pi 工具（tier-2，走 `webgis_execute` 长尾代理，不注册进 `NATIVE_TOOL_NAMES`）

| 名称 | 参数 | 语义 |
|---|---|---|
| `webgis_bivariate_reliability_inspect` | `layer_id` | 只读；`trusted_dispatch=True` |
| `webgis_bivariate_reliability_preview` | `layer_id`, `plan` | 零 mutation，返回 `candidate_digest` |
| `webgis_bivariate_reliability_apply` | `layer_id`, `plan`, `expected_revision`, `preview_digest` | 单笔提交，`mutation_id=f"g41:{plan_digest}:r{rev}:{digest[:12]}"` |

回执有界：≤9 格、≤10 桶、≤12 披露、≤40 字段；不回传完整要素、栅格数组或密钥。

## canonical legend（`g41_legend.v1`）

单源由 `legend.py` 产出，供 live DOM / Canvas(PNG) / 前端矢量 SVG / 服务端 G34 SVG·PDF / Pi 回执共同消费。

- bivariate 保留旧键 `type="bivariate"`, `colors`, `n`, `label_a`, `label_b`, `breaks_a`, `breaks_b`，新增
  `class_field`, `row_major`, `axes{row,col}`, `cells[]`（屏幕序：`class_index/color/count/empty/row_interval/col_interval`）,
  `joint[][]`, `requested/actual_classes_*`, `boundary_rule`, `counts{valid_pairs,only_a_missing,only_b_missing,both_missing,reconciliation}`,
  `missing{count,label,color,policy}`, `nodata`, `unit_a/unit_b`, `unit_declared_*`, `source{ref_id,content_revision,content_fingerprint,feature_count,algorithm_version,plan_digest,snapshot_guarantee}`,
  `stats_basis`, `scatter`, `histograms`, `disclosures`, `approximate`, `notes`。
- reliability 为 `type="reliability_dual_channel"`，含 `estimate`, `measure`, `channel`, `buckets`, `bucket_paint`,
  `missing`, `counts`, `source`, `disclosures`, `entries`（G34 扁平兼容），`unit`。

## 权威复用 / 不新增

- 写路径唯一：`apply_gis_mutation(UpsertLayerIntent)`；CAS 用 `_cartographic_mutation_revision`；checkpoint/undo 由 `MapSpecLifecycleEngine` 负责。
- paint 投影唯一：`thematic_spec.spec_to_paint`（bivariate match / graduated step）；第二通道用既有 `match` StyleMethod（live `compileStyleMethod`、服务端 `_resolve_paint_value` 均已支持）。
- G34 唯一决定图例位置/尺寸/字体/页框；本任务只加 canonical 内容与 `render_g41_canonical_legend` 绘制 adapter。
- 共享表达成员资格复用 `_shared_expression_scope`（G33），不复制组计算器。
- 色阵/分位复用 `bivariate.py`；度量词表复用 `app/lib/gis/uncertainty.py`。
- 不新增 agent loop / DAG / registry / MapSpec store / 全局 ID / 撤销栈 / 事件总线 / 第二证据库 / 第二渲染器。

## 主要错误码

`INVALID_PLAN`, `UNKNOWN_PLAN_VERSION`, `MODE_FIELD_MISMATCH`, `SAME_FIELD_TWICE`,
`BREAKS_NOT_STRICTLY_INCREASING`, `MANUAL_BREAK_COUNT_MISMATCH`, `COLOR_COUNT_MISMATCH`,
`INSUFFICIENT_COLORS`, `AMBIGUOUS_COLOR_SOURCE`, `UNKNOWN_MATRIX`, `INVALID_COLOR`,
`NO_STRONG_REVISION`, `SOURCE_MUTATION_DURING_READ`, `STALE_SOURCE`, `STALE_PREVIEW`,
`NO_STRONG_REVISION`(非 ref 源), `CAS_SUPERSEDED`, `MUTATION_REJECTED`,
`NO_VALID_PAIRS`, `DERIVED_FIELD_COLLISION`, `DERIVED_PAYLOAD_TOO_LARGE`,
`SOURCE_TOO_LARGE`, `NO_UNCERTAINTY_EVIDENCE`, `INVALID_MEASURE_VALUES`,
`INVALID_VISUAL_DOMAIN`, `UNKNOWN_OUTPUT_NOT_DISTINCT`, `VISUAL_DOMAIN_VIOLATION`,
`VISUAL_CHANNEL_LAYER_TYPE_MISMATCH`, `SHARED_EXPRESSION_MEMBER`, `SOURCE_UNAVAILABLE`,
`REVERT_SNAPSHOT_INVALID`, `PAINT_PROJECTION_FAILED`.
