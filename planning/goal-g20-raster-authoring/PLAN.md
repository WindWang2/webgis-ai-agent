# PLAN — G20-RASTER-AUTHORING

## 目标

把已有栅格波段/拉伸/色带/瓦片能力接成**权威 MapSpec 驱动的单幅制图闭环**：
真实波段与统计 → 严格表达契约 → 一次持久提交 → 真实瓦片像素变化 → 源像元检视 →
刷新/撤销恢复 → 图例与单幅导出同一语义。UI 与 Pi 复用同一领域服务。

## 执行原则

- 先打通一条真实纵向路径（WP02→03→04→06），再扩展错误面/UI/Pi/导出（WP05、07–12）。
- 每轮：读调用链 → 选一个可独立验收的增量 → 实现 → 真实验证 → 更新 LOOP_LEDGER → 提交。
- 不改 G12–G19、G21 所有权；共享文件只做 additive 最小接线。
- 不等待线上 CI；本地定向测试必须真实执行。

## 阶段划分

| 阶段 | 工作包 | 可独立验收产物 | 主要文件 |
|---|---|---|---|
| P0 | WP01 | BASELINE / DEDUP / INTERFACES / 首个失败旅程 | planning/* |
| P1 | WP02 | 严格表达契约：单波段/RGB、1-based、2 波段默认、per-channel stretch、色带/重采样枚举、具体错误码；Python↔TS 一致 | `app/schemas/raster_spec.py` 新增模型；`app/services/raster_authoring/errors.py`, `contract.py` |
| P2 | WP03 | 一次持久提交 + 恢复 + 数据身份；不重算遥感、undo/redo 生效 | `app/lib/cartography/mapspec_schema.py`（2 模型+1 字段）、`service.py`、`types.generated.ts` |
| P3 | WP04 | 真实 tile 消费完整样式；缓存身份含 style_id/data_revision；全局范围一次解析；边缘/mask/0值/常值 | `raster_tile_service.py` additive 参数、`app/api/routes/raster_authoring.py` |
| P4 | WP05+WP11 | descriptor/histogram/probe、CRS、scale/offset/单位、状态区分、预算与并发 | `app/services/raster_authoring/{source,descriptor,probe,legend}.py` |
| P5 | WP06+WP07 | compiler/runtime numeric_raster 分支、同源双层、局部工作台 UI、预览/待提交/已提交/失败 | `compiler.ts`、`runtime.ts`、`raster-authoring-panel.tsx`、i18n |
| P6 | WP08 | Pi @tool ×4、stubLLM 旅程、有界投影 | `app/tools/raster_expression_tools.py`、tests |
| P7 | WP09+WP10 | 图例/表达说明同源、冻结修订 PNG、SVG/PDF 栅格分支与降级披露 | `legend.py`、`snapshot.py`、`mapspec-to-svg.ts`、`mapspec_to_svg.py`、`exporter.ts` |
| P8 | WP12 | 1/2/4 波段 + nodata=0 + 零值有效 + 分类 + 常值 + 非 WGS84 fixture；真实 route / 像素 / MapSpec diff / 导出证据 | `tests/fixtures/raster_authoring/`, `tests/unit/test_raster_authoring_*.py`, `tests/integration/...` |
| P9 | F | 独立 reviewer → 修复 → 复审 → fetch 最新 master 校正 → push → PR | `REVIEW.md`, `PR_HANDOFF.md`, `PR_BODY.md` |

## 关键设计决定（已定）

1. **不新增 mutation intent / Body**：复用 `UpsertSourceIntent` + `UpsertLayerIntent`，
   避免改动 `mutation_registry` 一致性 gate 的硬编码集合与 parity golden；
   仍走 `apply_gis_mutation` 的锁/CAS/checkpoint/undo。
2. **MapSpec 文档版本不 bump**：纯 additive optional + `extra="allow"` + canonical 深拷贝，
   旧文档逐位不变；用独立 `raster_expression.v1` 子契约版本，避开 G15/G16/G21 抢 1.10。
3. **样式在 layer，数据在 source**：同源两层的 MapLibre 源 id 由 compiler/runtime 派生，
   互不串色。
4. **tile 授权复用 `transformRequest`**：URL 只带 `session_id`/`layer_id`/`style_id`，
   凭证在 header；后端用当前 MapSpec 校验 `style_id`，过期即 409。
5. **像元检视只读源栅格**（rasterio window/index 读取 + CRS 变换），绝不从 PNG 反推。
6. **导出冻结一次修订**：服务端生成真实 PNG（含图例+表达说明）；SVG/PDF 用有界
   data-URI 嵌入，无法忠实表达则显式降级/拒绝。

## 验收口径

见 `TEST_MATRIX.md` 与任务书「五、针对性验证与完成清单」；必须观察请求参数、
返回像素、MapSpec 前后 diff、probe 原始值、图例端点、导出文件字节，
不以控件文案/mock 次数判定。
