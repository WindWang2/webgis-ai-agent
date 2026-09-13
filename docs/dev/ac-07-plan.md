# AC-07 图面整饰自动排版 — 实施计划（adaptive-cartography/07-layout-auto-compose）

> ADR-0156 · 分支 `adaptive-cartography/07-layout-auto-compose` · 基线 origin/master@1fd4b035
> 本文件是执行期活文档；终态结论以 `ac-07-layout-recon.md`（P0）与 `ac-07-decisions.md` 为准。

## 0. 现状校正（相对任务书的 delta）

任务书基于较早的基线编写；master 已前进。P0 勘察确认的事实 delta：

| 任务书断言 | master 真相 | 影响 |
|---|---|---|
| `inset_map` runtime_status=planned，渲染器未实现 | 前端 `inset-map.tsx` live 渲染器已落地（静态 SVG 投影）；后端 registry `runtime_status="native"`（component_registry.py:538）；export 链 `drawChromeInset` 存在 | P6 收窄为：全链验证 + source 隔离测试锁定 + 残留缺口补齐 |
| 三类版面检查"只告警不自愈" | `COMPONENT_OUTSIDE_CANVAS` 已 fail+auto_safe+suggested_fix（resolve_floating_layout）；`LAYOUT_COLLISION` 确实 warning 无建议；`COMPONENT_LINK_CYCLE` fail+not_repairable | P1 聚焦 LAYOUT_COLLISION 修复建议 + 断环策略 |
| layout solver v3/v4 | V2（solve_component_layout）+ V3（solve_layout_v3）存在；**V4 不存在** | P1 = 实现 V4（选位 + 冲突自愈策略链） |
| `component_composer.py` 已存在 | master 上不存在 | 本线新建（§8 契约明确列为可改 → 新建合规） |
| 22 个组件已注册 | 后端 registry 20 个 descriptor（含 basemap/export_layout 画布级）；前端 catalog.generated.json 20 类型；前端渲染器 16 种 | P0 矩阵以实际数字为准 |
| `pip install -e .` 可用 | pyproject 无 build-system/packages 配置，editable 安装本身失败；CI 从不跑 `-e .` | 环境按 CI 等效：`pip install -r requirements-dev.txt`，仓库根跑 pytest |

## 1. 架构设计

### 1.1 「版面描述中间层」——CompositionDescriptor（本线产出，08 线消费）

新目录 `frontend/lib/layout/`：

- `composition-descriptor.ts` —— 中间层类型（version=1）：
  - `page`: 版式 profile（screen_16_9 / screen_4_3 / a4_portrait / a4_landscape）+ 画布尺寸（可选）
  - `elements[]`: id/type/anchor/slot{index,size}/stackOffsetPx/origin(`spec`|`autofill`|`fallback`)/repair 轨迹
  - `decisions[]`: CompositionDecision（缺项补全、fallback 命中、修复链每步）
  - `chrome`: numericScale{ratio,barMeters,barPx} / declination{degrees,approximate} / graticule{intervalDeg,lineCount*,format}
- 消费方：live（map-spec-chrome）与 export（buildExportChrome 只读消费 chrome 段 —— 整饰描述边界内）。

### 1.2 P1 冲突自愈（后端建议 + 前端执行）

- 后端 `component_composer.py`（新建）：`plan_layout_repairs(...)` —— 确定性策略链
  **改 anchor → 缩尺寸(width_units) → 折叠进溢出面板 → 隐藏最低优先组件**；每步带 evidence。
  `component_graph.py`（可改）：`break_component_cycles(graph)` —— requires/under 环按
  (边类型权重, 端点 priority, id 序) 断开最低权重边，返回 remove_links 证据。
- `semantic_checks.py`（仅修复建议段）：`LAYOUT_COLLISION` warning + suggested_fix
  （operation=`resolve_layout_collisions`, repairability=auto_safe；status 保持 warning ——
  quality_loop 只自动修 fail，user-wins 语义不回退）；`COMPONENT_LINK_CYCLE` fail +
  suggested_fix（operation=`break_component_cycle`）+ repairability=`auto_with_semantic_risk`
  （断边是语义手术，需 explicit intent —— 与 quality_loop 现有语义对齐，不制造不可执行 auto 通道）。
- 前端 `frontend/lib/layout/composition-repair.ts`：对 resolveComponentLayout 的碰撞输出
  执行同一策略链（live 自愈），产出 RepairStep 轨迹进 descriptor。

### 1.3 P2 缺项自动补全

- 后端 `component_composer.py`：`required_components_for(purpose, content)` ——
  purpose ∈ {screen_16_9, screen_4_3, a4_portrait, a4_landscape} × 横竖已含；
  content ∈ {有/无投影信息, 有/无数据来源, 有/无统计面板, 有/无时序}。
  输出必配清单 + 逐项 reason；数据来源缺失 → 自动补 `数据来源：—（待补充）` + advisory。
- 前端 `frontend/lib/layout/required-components.ts`：同规则 TS 镜像（单源规则表由测试对齐），
  map-spec-chrome 挂载前主动补全，替代 `__fallback_*` 被动注入（fallback 保留为安全网）。

### 1.4 P3–P5 能力组件

- P3 数字比例尺：`frontend/lib/layout/numeric-scale.ts` —— 随 zoom+纬度的比率式 1:xx
  （metersPerPixelAt 单位换算，含 cos 纬度修正）；scale_bar 渲染器并存渲染（variant/option 可关）。
- P4 图廓注记 + 真北偏角：`frontend/lib/layout/graticule-labels.ts`（度/度分/度分秒随跨度自适应）
  + `frontend/lib/layout/magnetic-declination.ts`（bbox 中心近似模型 + `approximate` 标记）；
  graticule 渲染器补角注记/格式自适应，north_arrow 渲染器加偏角注记。
- P5 密度自适应：`frontend/lib/layout/graticule-density.ts` —— 优选序列
  [30,20,10,5,2,1,0.5,0.2,0.1,0.05,0.02,0.01]° 内选使网格线数∈[3,10] 的间隔；
  显式 options.interval 覆盖优先；无 bounds 时回退 zoom 表。

### 1.5 P6 inset_map、P7 图例统一

- P6：native 状态测试锁定 + source 隔离断言（不 mount 第二 MapLibre、不读主图 source）+
  残留缺口（P0 确认后列）。
- P7：`frontend/lib/layout/legend-labels.ts` —— 消费 unit/nodata/out_of_range_label/method/k
  （局部窄类型，不改共享 types.ts）；legends.tsx/colorbar.tsx 接线；fixture 驱动快照测试。

## 2. 阶段门禁

P0 → recon 文档+CSV；P1 → pytest tests/unit/test_layout* 绿；P4/P7 → 里程碑 pytest tests/unit；
P8 → 20 MapSpec × 4 版式三类检查归零 + 唯一 next build + typecheck + eslint 变更文件。

## 3. 边界声明（§8 契约的执行解释）

- `layout_solver.py`（V4 落点）不在 §8 可改列举中，但"把 solver 从选位升级为选位+冲突自愈"
  的唯一可行落点即 solver 本体；禁改清单未含它、无兄弟线认领。本线在此追加 V4 纯增量
  （V2/V3 API 与行为零改动，测试锁定），PR 协调点显式声明。
- `lib/map-kit/types.ts`、`legend-model.ts`、`meters-per-pixel.ts`、`graticule-math.ts`
  一律不改（P3–P5/P7 全部经 frontend/lib/layout 新模块 + 渲染器接线实现）。
- `mapspec-compiler/**`、`mapspec-runtime/**`、`exporter.ts`、`frame-composer.ts`、
  后端 symbology 族、migrations、workflows：不碰。
