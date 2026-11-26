# 纸面目标比例尺制图综合（paper_scale.v1 / paper_scale_plan.v1）使用说明

GOAL 04（20261124 轮）—— 在既有 zoom 带多尺度表达（`scale_profile.v1`，见
`docs/cartographic-closed-loop.md`）之上补齐的**固定纸面比例尺出版综合**
能力。两者互补：屏幕浏览走 zoom 带档案；固定比例尺交付（1:50万、A4 横版）
走本文的纸面契约。

## 核心概念与单位纪律

- **PublicationScaleContract（paper_scale.v1）**：目标分母 1:N × 页面
  （ISO A 系/自定义 mm × DPI）× 用途 × 投影声明 × 硬约束 × 误差预算。
  纯数据 + 纯函数（`app/lib/cartography/multiscale/paper_scale.py`）。
- 换算单一出口：`ground_m_per_mm = N/1000`、`ground_m_per_pixel =
  N·0.0254/dpi`、`equivalent_display_zoom`（纸面 ↔ MapLibre zoom 的唯一
  衔接点）；**地理坐标不当米用**，一切容差出口都是 meters_ground。
- **投影诚实**：Web Mercator 比例尺随纬度变化（cos(φ)），南北边缘变化比
  ≥5% 强制披露；非 Mercator 投影换算 `not_evaluated`，绝不猜测变形。
- **用途词表**：`overview / reference / planning / navigation /
  thematic_exhibition`。navigation ⇒ 网络连通性硬约束（QA fail 阻断上版）；
  thematic_exhibition ⇒ 专题量纲守恒硬约束。
- **硬约束**（`HardConstraint`）：`keep_feature_ids`（user-wins 恒保留）、
  `keep_values`（词表值恒保留，如「县城」）、`keep_region`（重点区域 bbox
  内不消除）。综合永不让步。

## 综合管线（按几何族）

`publication_compile.compile_paper_scale_plan`（纯函数）：

| 族 | 管线 | 引擎 |
|---|---|---|
| polygon | 小面积消除（`minimum_mappable_area_m2`，硬约束/区域保护）→ 拓扑一致共享边简化（契约容差） | `polygon_eliminate.py` → `topology.py` |
| line | 等级取舍（用途保留秩 `PAPER_KEEP_RANKS_BY_PURPOSE`）+ 连通性桥接（BFS 最短路径重加被抑制段） | `network_select.py` |
| point | 目标尺度格网聚合（5mm 纸面地面格、≥30 要素触发、sum_fields Σ守恒） | `point_aggregate.py` |

关键保证（全部有独立 oracle 测试，非引擎自述）：

- **无缝共享边**：相邻面共享边链只简化一次，两侧重建引用同一份坐标
  （逐字节一致 ⇒ 不可能缝隙）；回退传播保证链级全或无。顶点未配齐的
  脏数据不伪装保真（`partial_vertex_match` 披露），真实缝隙由 QA 审计。
- **网络连通**：保留子网多分量时按最短桥接路径重加被抑制段；预算尽或
  无路径 ⇒ 披露断连分量数，不静默假装连通。
- **量纲纪律**：sum_fields（计数/总量）Σ守恒（消融成员可对账）；未声明
  的数值字段不平均、不臆造权重（率/比分母未知拒绝重算）——统计/图例
  口径恒 `original_source`。
- **display-only**：一切工件是独立新 ref；原始数据、分析通道、图例分类
  全程不变。

## QA 门（fail-closed）

`qa_pack.run_paper_scale_qa`（独立 shapely/图算法审计）：几何有效性、
逐对面缝隙/重叠、最小可辨尺寸、计数/总量守恒、网络连通性（按用途硬/软）、
比例尺一致性 + Mercator 变化披露。`blocking > 0 ⇒ 拒绝冻结`（工具与 REST
同门禁），检查行随结构化结果返回。

冻结计划落 `layer.scale_profile.paper_plans[fingerprint]`（additive），
表达窗口经 `upsert_representation_window` 精确裁剪插入：纸面等效 zoom
±0.5 跨度内冻结工件优先，跨度外保持原始数据（无带档案的普通层自动合成
raw 全域覆盖）；被裁掉窗口的旧计划自动移除并披露（`superseded_plans`）。
导出/前端经既有 `window.source` 裁决消费同一冻结工件（与 Agent、实时
渲染同合同；`carto.paper_scale_plan` 语义检查核对跨面一致）。

## 使用入口（同一服务面）

- **Agent（Pi 工具）**：`webgis_inspect_paper_scale_generalization` →
  `webgis_preview_paper_scale_generalization` →
  `webgis_apply_paper_scale_generalization`（mode=preview/commit，
  operation_id 幂等，expected_revision CAS）。
- **REST**：`GET/POST /api/v1/chat/sessions/{sid}/paper-scale`
  （require_owned_session 验权；preview 零突变；commit 走 CAS + 回执环）。
- **工作台 UI**：图层样式面板 → 「纸面比例尺综合」区块（冻结计划清单 +
  预览/冻结，qa_blocked 如实呈现）。
- **自然语言示例**：「出一张 1:50万 总览图，保留县城与主干道路，A4 横版」
  → inspect（证据/披露）→ preview（QA）→ apply（kept_values 锁定词表值 +
  hard constraint / purpose=overview）→ 冻结后导出即读冻结工件。

## 测试与证据

- `tests/cartography/multiscale/`：paper_scale 契约、共享边拓扑 oracle、
  消除/网络/点聚合引擎、qa_pack、编译器、导出旅程（真实 SVG 双 zoom）、
  语义检查、性能对照（`MSP_PERF=1 pytest ... -m PerfHeavy` 显式跑）。
- `tests/unit/gis_harness/paper_generalization/`：服务闭环（真实播种无
  mock）+ 工具注册/派发。
- `tests/unit/api/test_paper_scale_routes.py`：REST 路由。
- `frontend/components/hud/paper-scale-panel.test.tsx`：面板 vitest。

## 已知边界（诚实声明）

- 栅格族不进本通道（重采样/金字塔走既有能力）。
- 点聚合代表要素的 label 绑定沿用既有标注通道，本模块不另写 label 引擎。
- 真实浏览器 journey（断连重连/截图对照）未执行：以 SVG 真实编译 +
  结构化 feature/legend 断言为导出证据；浏览器 evidence 标 not_evaluated。
