# G47-DORLING-CARTOGRAM — INTERFACES

> 唯一真源是**布局 artifact**；HTTP 路由、Pi 工具、HUD 面板、纸面 adapter 都从它读。
> 任何一层都不得自行重算半径/重叠/邻接。

## 1. 布局 artifact（`cartogram_layout.v1`）

由 `app/services/cartogram_authoring/layout.py` 产出、`service.load_layout` 读取、
以 `ref:g47layout-*` 持久化在会话 ref 存储里。

| 字段 | 语义 | 硬约束 |
|---|---|---|
| `kind` / `schema_version` | `cartogram_layout` / `cartogram_layout.v1` | 版本化，破坏性变更必须升 schema |
| `coordinate_space` / `units` / `is_geographic` | `diagram` / `diagram_unit` / `false` | **示意坐标**：没有 CRS、没有经纬度圆心；不得用于距离/面积/路由计算 |
| `extent` | `{width,height,margin,uniform_scale:true}` | 图面是正方形，单一 uniform scale |
| `circles[]` | `{feature_id,label,x,y,radius,value,anchor}` | `radius = sqrt(k·value)`；坐标/半径按 `RADIUS_DECIMALS=6` 量化 |
| `area_scale` | `{coefficient,max_radius,min_radius,normalization_factor,…}` | 全部 `diagram_unit`；`k` 全图唯一，无逐圆 clamp |
| `source` | `{ref_id,content_revision,content_fingerprint}` | 同代锚点；apply 时用它比对 |
| `quantity` | 计数/总量/零/缺失/非法（分列） | 只统计 **scope 纳入** 的单元 |
| `zero_ids` / `missing_ids` / `excluded.ids+reasons` | 三类不画圆但保留身份 | 排除必须逐项写理由 |
| `adjacency` | `{edges[], original_edges, contract}` | 原邻接允许变化，必须披露 |
| `metrics` | `area/overlap/displacement/adjacency/gates/solver` | 全部来自独立 oracle；`overlap` 两道门各自独立 |
| `reference.outlines[]` | `{feature_id, rings[[[x,y]…]]}` | **同一 diagram 坐标**，落在 `extent` 内 |
| `status` / `publication_ready` | `converged|not_converged` / bool | 未达门也不抛错，如实记录 |
| `disclosures[]` | 人类可读披露 | 至少含"非经纬度/不保拓扑/源代次/算法引文" |
| `metrics.layout_fingerprint` | `g47l-<24 hex>` | 不参与自身计算（先占位再回填） |

指纹稳定性：同输入两次计算只允许 `metrics.solver.elapsed_s` 与 `layout_fingerprint` 不同
（`_VOLATILE_KEYS`）。

## 2. HTTP（`/api/v1`，全部 `require_owned_session`）

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/sessions/{sid}/layers/{lid}/cartogram/inspect` | 字段画像 + 支持矩阵 + 源锚点（零 mutation） |
| POST | `/sessions/{sid}/layers/{lid}/cartogram/plan` | 资格预检 + 面积比例预览（零 mutation、零求解） |
| POST | `/sessions/{sid}/layers/{lid}/cartogram/run` | 提交 durable job（幂等键见 §5） |
| GET | `/sessions/{sid}/jobs/{job_id}` | 作业状态（terminating/stale 词表共用） |
| GET | `/sessions/{sid}/cartogram/layouts` | 本会话布局清单 |
| GET | `/sessions/{sid}/cartogram/layouts/{ref}` | 全量 artifact |
| POST | `/sessions/{sid}/layers/{lid}/cartogram/apply` | 单笔 CAS 应用（层证据块 `cartogram_authoring`） |
| GET | `/sessions/{sid}/cartogram/layouts/{ref}/export?fmt=svg|png|pdf` | 真实导出 |

错误面统一 `{code,message,field?,correction_hint?,details?}`，`code` 取值见 §4。

## 3. Pi 工具（6 个，`webgis_dorling_cartogram_*`）

`inspect` / `plan` / `run` / `status` / `apply` / `export`。
全部经 `@tool` 注册表 → 生产 dispatch → `cartogram_authoring.service`；回执有界
（`bounded_receipt`：指标 + ≤8 个样例圆 + 计数 + 披露，不回传全量布局）。
`export` 只回 `bytes/sha256/media_type/filename/download_path`，不把二进制塞进对话。

**注册完整性是硬门**：`memory_class` 非法会让模块加载抛错并被上层降级为 warning，
导致后 3 个工具静默消失 —— 由 `tests/unit/test_g47_review_regressions.py::test_all_six_pi_tools_register_without_swallowing_errors` 钉住。

## 4. 错误码（`errors.AuthoringError`）

| code | HTTP | 触发 |
|---|---|---|
| `INVALID_PLAN` / `INVALID_QUANTITY_SPEC` / `INVALID_SOLVER_PARAMS` / `INVALID_QUALITY_GATE` | 422 | 合同字段非法 |
| `RATE_OR_MIXED_UNIT` | 422 | 率/密度/混合量纲不是可加总量 |
| `FIELD_NOT_FOUND` / `FIELD_NOT_NUMERIC` / `MISSING_QUANTITY` / `ZERO_QUANTITY` / `NO_VALID_QUANTITY` / `ZERO_TOTAL` | 422 | 数量语义拒绝 |
| `EXTREME_VALUE_RANGE` | 422 | 跨度大到无法可靠表达（拒绝"最小可见圆"伪装比例） |
| `UNSUPPORTED_GEOGRAPHY` / `AXIS_ORDER_SUSPECT` / `INVALID_GEOMETRY` / `SCOPE_IDS_NOT_FOUND` | 422 | 地理资格 |
| `QUALITY_GATE_FAILED` | 409 | 未达发布资格门且未显式 `allow_diagnostic` |
| `STALE_SOURCE` | 409 | 源代次/引用与布局锚点不符（需 `acknowledge_stale_source`） |
| `SOURCE_VERIFY_FAILED` | 409 | 无法判定同代（descriptor 缺失/代次非整数）—— **拒绝写入** |
| `CAS_SUPERSEDED` | 409 | `expected_revision` 不符，nothing written |
| `JOB_NOT_FOUND` / `LAYOUT_NOT_FOUND` | 404 | 跨会话或已淘汰 |

## 5. 幂等与代次

- 布局作业幂等键：`g47:{session_id}:{plan_digest}:{content_revision}:{ALGORITHM_VERSION}`。
- 应用幂等键：`g47:{layout_ref}:r{expected_revision}`（工具侧）/ `g47:{client_mutation_id}`（面板侧）。
  `apply_gis_mutation` 的 `duplicate` 会**透传**：回执 `committed=false, mutations=0, can_undo=false`，
  不再把重放报成新提交。
- 计划摘要 `plan_digest()` 剔除 `source_anchor`；候选摘要 `candidate_digest(anchor)` 含锚点。

## 6. 纸面 `diagram` 组件（G34 窄接缝）

- `print_layout_schema.COMPONENT_KINDS += "diagram"`；`PrintDiagramSpec` 严格模式
  **拒绝** `crs`/`bbox`/`coordinates`（`extra="forbid"`）。
- `compile._compile_diagram(spec, box)`：等比 contain（`fit="contain"`），
  圆与**参考轮廓 path 套同一个 `scale`/`origin`**，输出 `coordinate_space="print_canvas_units"`。
- `html._diagram_img`：内联 SVG data URI（自包含，无外链），PDF/PNG 纸面路径共用同一场景。

## 7. 前端

- `lib/cartogram/{contracts,geometry,api}.ts`：类型、等比图面数学（纯函数）、API 客户端。
- `components/cartogram/dorling-diagram.tsx`：真 SVG 图面（滚轮缩放/拖拽平移/点击命中/方向键/Esc），
  `preserveAspectRatio="xMidYMid meet"` + 统一 `transform scale`，禁止非等比拉伸。
- `components/hud/dorling-cartogram-panel.tsx`：inspect→plan→run→poll→layout→apply→export；
  **诊断草稿需显式勾选**（`allowDiagnostic` 默认 false，换布局即复位）。
- i18n：`messages/{zh-CN,en-US}/cartogram.json`（`panel.*` / `diagram.*` / `toast.*`），
  入口标题在 `hud.json` 的 `dorlingCartogram.title`；`local/no-hardcoded-cjk` 零告警。

## 8. 与原地理的关系（不可混淆的两件事）

- **本任务交付**：圆面积（Dorling）变形 —— 示意位移布局 + 原地理轮廓参照 + `diagram` 出版 adapter。
- **仍未支持**：面域多边形变形（Gastner-Newman 等）。`cartogram_map` 视图仍是 `planned`，
  `cartogram_unsupported` 诊断码继续对它诚实降级；两处文案已分别说清，不再笼统声称"cartogram 未实现"。
