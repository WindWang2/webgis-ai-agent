# C11 Recon — Typed CartoIR / MapSpec / Renderer ABI

## Baseline
- 执行时 origin/master: `77d2678d9f63cab0c726575b8d327f2cba6524ca`（快照 930459ef 已过时）
- Worktree: `/home/kevin/project/webgis-ai-agent-c11-20260929-0740`
- Branch: `zcode/c11-typed-cartoir-mapspec-renderer-abi-20260929-0740`

## Collision Matrix（执行时事实）

### Already merged（本方向的生态）
- #1502 F10 Cartographic Grammar production — Out of Scope 明确遗留：
  1. bivariate 原生 MapModel（目录扩展）
  2. scene_lod 与 label/grammar 带表合并（"有意并存的两套 zoom 心智模型"）
  3. visibility_hints 渲染端真实消费（前端）
  → **这三项 = C11 核心范围，F10 显式 deferred，无重复风险**
- #1501 F11 Component ABI（COMPONENT_ABI_META 手审静态表 + fail-closed 审计）
- #1497 F12 Map Plan Compiler（MapPlanIR → MapSpec mutations）
- #1498 F13 MapSpec→Render Runtime & Data Plane
- #1509 F01 GIS Dataset Semantic Contract（versioned descriptor 全链指纹对账 — 模式可借鉴）
- 后端 `app/lib/cartography/mapspec_schema.py`：V6 typed Pydantic 权威 schema
  KNOWN_VERSIONS=("1.0".."1.4"), LATEST=1.4, migration registry, canonical
  serialization, extra="allow", 只接冷路径（导出/报告/publication 编译边界）

### Open PR active（需避碰）
- #1574 H01 app/contracts kernel：搬 services/gis_harness/components.py、
  services/mapspec/lifecycle_engine.py 的 intents、schemas/mapspec_mutation_schema.py
  消费方改接 → **C11 不动这些文件的结构，新代码放新模块**
- #1575 H02 MapSpec mutation registry：重构 apply_mutation（lifecycle_engine.py 巨石）
  → C11 不碰 lifecycle_engine apply_mutation 内部
- #1578 H10 Unified GIS Action IR（agent 行为层 IR，与 CartoIR 渲染契约不同层）
- #1583 C14 PublicationIR（出版版面，与 CartoIR 互补；注意 schemaVersion 协商模式对齐）
- 其余 H03-H09/E15/W12：无文件交集
- Dependabot #1534-1540：不 checkout

### Review follow-up / deferred
- Issue #1556 [frontend][P2]：adapter.ts(49 any)/compiler.ts(43 any)/use-feature-selection.ts(23)
  修复方向明示 maplibre-gl StyleSpecification/ExpressionSpecification + typed properties
- F10 Out of Scope 三项（见上）

### Still missing on latest master（C11 工作面）
1. 后端权威 schema ↔ 前端 TS 类型无 parity gate（types.ts 手维护镜像，
   types.generated.ts 待 S1 确认来源）
2. visibility_hints 仅后端 scale_rules 定义，前端渲染不消费
3. zoom 语义多套并存（scene_lod / label zoom / grammar zoom band / minzoom-maxzoom）
4. bivariate 靠字符串塞 extra，无原生 MapModel
5. 前端无 schemaVersion migration / 未知版本 fail-safe
6. adapter/compiler 热点 any 未收敛

## 去重判定
- open PR 集合 0 个覆盖 C11 范围 >30%（H01/H02 是 architecture/mutation 重构，
  不做 typed contract parity/visibility/bivariate/migration）
- 不重复实现 H01 的 contracts kernel 搬家；C11 新契约模块放置需与 H01 落地方向
  兼容（若 H01 合并，C11 新模块自身零 app 内依赖即可平移）

## 不做
- 不动 lifecycle_engine.apply_mutation 内部（H02 领地）
- 不搬 contracts 文件（H01 领地）
- 不做 UI 美化、不重写 MapLibre、CartoIR 不承载原始大数据
