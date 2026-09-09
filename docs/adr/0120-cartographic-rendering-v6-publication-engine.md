# ADR-0120: Cartographic Rendering V6 — Typed MapSpec + Publication Engine

- 状态: Accepted（本 PR 落地）
- 日期: 2026-09-10
- 关联: ADR-0118（Cartography V5）； Epic 07（Cartographic Rendering V6）

## Context

V5 建立了 render diagnostics 词表、layout intent user-wins、label fit/wrap、
SVG 双孪生（字节 parity）、真矢量 SVG 导出与语义 oracle，但审计（Phase A，
`.agent-work/cartography-v6/00-baseline.md`）确认四类缺口：

1. MapSpec 契约无权威 schema —— 后端 dict 约定 + 前端手写 TS 镜像并行漂移，
   version 字段无验证、无迁移；
2. 出版物（WeasyPrint 报告 PDF）图面零整饰 —— 无图例/指北针/比例尺/图框，
   与 live/canvas 导出语义脱节；
3. 用户面 PDF 为栅格壳（matplotlib PNG 嵌 A4），无可选文本、无真矢量；
4. `solve_labels` 碰撞求解器零生产接线；frames 仅请求级（ExportRequest），
   spec 级 atlas 缺席。

## Decision

### 1. Typed MapSpec（authoritative schema）

- 唯一权威 = `app/lib/cartography/mapspec_schema.py`（Pydantic v2 strict）。
  strict 数值/布尔/字符串脊柱：类型不符 → 结构化披露（invalid_fields），
  **绝不 coerce**（pydantic lax 会把 `"false"`→False、`"2000"`→2000，
  静默翻转渲染语义 —— 架构挑战 R1-C2 实证）。
- unknown fields：全模型 `extra="allow"`，任意深度保真 + 披露（消费方 =
  vector-pdf 响应元数据 / report 元数据 / export sidecar）。
- canonical 形式 = **保序深拷贝**（非 model_dump —— dump 重排键序且受 lax
  转换污染）；golden corpus 锁定 `dumps(canonicalize(x)) == dumps(x)`。
- version：已知 {1.0, 1.1}；1.1 纯 additive（layout.frames、layout.labels、
  fabric 源 inlineData 键面）；迁移 = 显式 upgrader 注册表（1.0→1.1 identity
  语义升级，不改写存储文档）；更新版本 → typed 拒绝（publication 链）。
- TS 投影：`ts_projection.py` 确定性生成 `types.generated.ts`；开放面
  （StyleMethod、paint/layout 索引签名、inlineData）由生成器内**策略表**
  显式声明；`types.ts` 收敛为 re-export + compile-runtime 类型。幂等契约
  测试（byte 级）+ `satisfies MapSpec` 编译闸锁定。
- schema 层只接**冷路径**（导出/报告/publication 编译边界）；
  mutation 热路径不加解析（Issue #1082 读放大对齐）。

### 2. Canonical Render Scene（跨语言 oracle）

- 后端 `render_scene.py` = 前端 `describeRenderScene`/`resolveMapComponents`
  忠实镜像；共享 golden fixtures（`tests/cartography/golden_corpus/`）由
  pytest 与 vitest 消费同一批文件（手写 oracle，双语言逐字段一致）。
- legend 条目推导收敛单源：前端 `legend-model.ts` deriveLegendModel /
  后端 `derive_legend_items` 镜像；六调用点语义差异逐条登记
  （`.agent-work/cartography-v6/05-legend-convergence-diff-table.md`），
  有意 delta 均有闸更新。

### 3. Publication Engine

- 后端孪生新增 `include_chrome`（canonical scene 组件 → 整饰：标题块/
  指北针/投影感知比例尺（cos 纬度修正）/图例框（单源条目）/帧框/
  经纬网/locator 型 inset）与 `bounds`（显式范围，帧几何）。组件驱动
  user-wins：缺席/disabled 不画、无 fabricated 兜底。
- `publication_export.py` + `POST /api/v1/map/export/vector-pdf`：
  schema 校验 → 逐帧 publication 编译 → HTML（@page=帧尺寸，系统字体栈）
  → WeasyPrint。文本全矢量可选可检索（pypdf extract 断言）。
- 安全：`url_fetcher` deny-all（SSRF/外联/任意读封闭）；栅格/瓦片层
  **诚实省略** + `raster_layer_unavailable_vector_pdf`（不伪造嵌入）；
  WeasyPrint 进程级互斥（官方无线程安全承诺），忙 → 429 `vector_pdf_busy`；
  weasyprint 缺席 → 503 `vector_pdf_unavailable`（前端回退栅格 + 披露）。
- 预算：帧 ≤ 50、页 ≤ 1200mm、载荷 ≤ 50MB、标签 ≤ 400/导出
  （`label_budget_exceeded`）、诊断 64（帧级子配额 + `diagnostics_truncated`
  元披露）、编译协作式超时（wait_for 仅保护调用方返回，R1-C1）。

### 4. Label Engine V6（确定性碰撞导出）

- `label_collision.py`（便携子集，无 shapely）+ `label-solver.ts` 1:1 移植；
  `layout.labels.collision == "deterministic"` 时双孪生标签组置顶渲染；
  缺省关闭（legacy byte-stable）。差分 fixtures（6 场景）双语言逐坐标
  （3 位小数）一致 + 不变量测试（placed 盒两两不重叠）+ 确定性重复求解。
- 诊断：`label_collision_relaxed`（位移/省略聚合）、`label_budget_exceeded`。

### 5. 诊断死码门（自动化收口）

`EMITTER_REGISTRY`（code → 真实发射点）+ 契约测试：词表无死码、无幻影码、
发射点漂移红灯。新增码必须同 PR 登记发射器 + 探针测试。

## Consequences

- 正面：契约单源可验证；出版物达到出版级（矢量文本/图例/整饰）；
  atlas 双端可用；标签导出不再重叠堆叠；诊断全链有据可查。
- 代价/限制：canvas 导出不映射 layerOverrides/pageSize（MapLibre 语义
  差异，由后端 publication 承接）；continuous/dynamic 图例 live 组件保留
  专用渲染（条目语义已单源）；矢量 PDF 栅格层省略（dataUrl 嵌入为
  follow-up）；weasyprint/pypdf 进 requirements（部署需 pango 系统库）。
- 兼容：legacy 孪生入口 byte-stable；schema v1.0 往返等价；无 DB migration；
  types.ts 导出面不变。report 图面视觉 delta 经 publication_chrome 标志 +
  CHANGELOG 披露（有意变化）。
