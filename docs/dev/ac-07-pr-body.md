# feat(layout): self-healing map composition — collision resolution, required-component auto-fill, numeric scale, graticule density, inset map (ADR-0156)

## 目标

把图面版面从「摆上去」升级为「排出来」：碰撞可自愈、缺项主动补全、数字
比例尺/真北偏角/经纬网密度/图廓注记能力齐备、版面决策可审计（中间层工件），
P0 语料 20 MapSpec × 4 版式三类版面检查修复后**归零**。

## 《复核纪要》（§0.2 防重复复核）

1. **PR 谱系**（`gh pr list --state all --search "layout OR chrome OR 版面 OR 图例…"`）：
   组件化模板谱系 #905/#1093/#1145（V1–V3）、设计系统 V4 #1151、渲染引擎
   V5 #1170 / V6 #1181（typed MapSpec + publication engine + include_chrome）、
   V7 组合 #1195；AC 兄弟线 #1257（02）/ #1258（03）OPEN。**无任何先前
   PR 做「冲突自愈 / 缺项补全 / 数字比例尺 / 密度自适应 / 中间层」**。
2. **Issue 谱系**：#884（scale_bar+colorbar 同槽互压，已由 U-2 修复）、
   #1079（top-left 同叠，已由 v2 顶槽堆叠修复）、#805（export_layout 死配置，
   08 线域）、#614（A3 折叠）、#1075（graticule 谎报 interactive —— 与本线
   无关的 registry 词表项）。无重复施工。
3. **分支谱系**：`git branch -a | grep -iE "layout|chrome|compose|legend"`
   仅本线命中 layout。
4. **现状校正**（任务书 → master 真相）：
   - inset_map 已全链 **native**（live inset-map.tsx + export drawChromeInset
     同链；registry:538）—— P6 收窄为真值钉住 + source 隔离测试 + 注释漂移
     修正（gis_harness/components.py:33-37 仍写 planned）；
   - COMPONENT_OUTSIDE_CANVAS 已 fail+auto_safe（resolve_floating_layout），
     真正「只告警」的是 LAYOUT_COLLISION（warning 无建议）与
     COMPONENT_LINK_CYCLE（fail not_repairable）；
   - 后端 solver 只到 V3 —— 本线新增 V4；「22 个组件」实为 20 类型 /
     19 descriptor / 18 live 渲染器；
   - 后端 include_chrome **不注入** fallback（user-wins）—— 三通道兜底
     语义缺口由中间层 provenance 裁决（本线决策，见 ADR-0156 §1）。
5. **环境偏差**：任务书 §0.1 的 `pip install -e .` 在 master 上不可构建
   （pyproject 无 build-system/packages 配置，CI 从不使用）—— 按 CI 等效
   `pip install -r requirements-dev.txt` 执行。
6. **ADR 谱系**：master 最高 0147；AC-01/02/03/04 占 0150–0153、AC-09 占
   0158；**0156 空闲 → 本线占用 ADR-0156**（§0.3 契约）。

## 变更摘要

- **版面描述中间层**（本线产出、08 线消费）：`frontend/lib/layout/composition-descriptor.ts`
  —— version=1 可序列化工件：elements（provenance `spec|autofill|fallback`
  + 修复轨迹）/ decisions（与后端 CompositionDecision 同构）/ chrome
  （numericScale / declination / graticule 配置）。
- **P1 冲突自愈**：`solve_layout_v4`（V2/V3 零改动纯增量）策略链
  改 anchor → 缩尺寸 → 折叠溢出面板 → 隐藏最低优先；
  `component_composer.plan_layout_repairs` 规划器；
  `LAYOUT_COLLISION` 附 auto_safe 建议（**status 保持 warning** ——
  quality_loop 零回退）；`break_component_cycles` 最低权重断环 +
  `COMPONENT_LINK_CYCLE` auto_with_semantic_risk 建议；前端
  `composition-repair.ts` 执行同链（user-pinned 绝不动）。
- **P2 缺项补全**：`required_components_for(purpose, content)` 后端语义源 +
  前端镜像（双侧测试锁定）；chrome 族 `__autofill_*` 注入；数据承载件只记
  advisory（诚实渲染边界）；数据来源未知补
  「数据来源：—（待补充）」占位 + advisory；`__fallback_*` 降为安全网。
- **P3 数字比例尺**：`numericScaleAt` 1:N（96dpi + cos 纬度当地尺度修正），
  与图形条并存（可配）；赤道/中纬/高纬 ≤5% 误差测试锁定。
- **P4 图廓注记 + 偏角**：度/度分/度分秒三档随跨度自适应 + 四角注记；
  磁偏角偶极子近似（恒 approximate，可关）。
- **P5 密度自适应**：双维联合约束线数 ∈ [3,10]；显式 interval 覆盖优先；
  zoom 表回退；全球跨度如实出超。
- **P6 inset_map**：native 真值四项回归锁定 + 渲染器不 mount 第二 maplibre
  runtime 静态扫描 + planned 注释漂移修正。
- **P7 图例统一**：legend_spec v2 的 unit / nodata_label /
  out_of_range_label / method / k 在图例卡与色条统一消费；v1 干净回退。

## 四类版式对照表（P0 基线 → 修复后）

| 口径（20 案例 × 4 版式） | LAYOUT_COLLISION warning | COMPONENT_OUTSIDE_CANVAS fail/warn | COMPONENT_LINK_CYCLE fail |
|---|---|---|---|
| native 修复前 | **72**/80（90%） | 0 | 0 |
| native 修复后 | **0** | 0 | 0 |
| corrupt（注入越界浮动件）修复前 | 72/80 | **80**/80 | 0 |
| corrupt 修复后 | **0** | **0** | 0 |

- 版式维度（viewport / presentation_16x9 / a4_portrait / a4_landscape）逐档
  归零；原始数据 `docs/dev/ac-07-baseline.json`；门禁测试
  `tests/cartography/test_ac07_zero_regression.py`。
- 基线结论：三检查是 zone 计数/图语义模型，四版式计数天然相同 —— 版式
  敏感性经由 solve_layout 重排 + margins 进入检查（见 ac-07-decisions.md D12）。

## 交付台账

见 `docs/dev/ac-07-ledger.md`（任务 → 文件 → 测试 → 证据全表）。

## 本地门禁证据（无 CI，本机即门禁）

- 前端 vitest（components/map + lib/map-kit + lib/layout，**最终提交树复跑**）：
  **721 tests passed**（2026-09-13 07:15:46）。
- 后端 pytest `tests/unit -q -m "not heavy and not real_services and not perf"`：
  **10727 passed / 111 skipped**，29 failed —— 逐项归因均与本线无关（见下节
  与 `docs/dev/ac-07-milestone-failures.md`）。
- `tsc --noEmit` 0 错误；`next build` 通过（最终提交树复验，exit 0）。
- `ruff check <变更文件>` All checks passed；`eslint <变更文件>` 0 problems。
- 未修改 `.github/workflows/**`、`migrations/**`、mapspec-compiler/runtime、
  exporter.ts/frame-composer.ts、后端 symbology 族。

## 里程碑失败归因（29 failed → 0 与本线相关）

完整清单与逐项对照见 `docs/dev/ac-07-milestone-failures.md`。在纯净
origin/master worktree（@1fd4b035，同一 venv 同参数）上复跑同样用例：

- **27 项 master 同败**：extensions_platform 9（rlimit/bwrap/worker ——
  Windows 沙箱语义）、pmtiles 族 9（真实文件 fixture）、data_fabric 路径
  守卫 + postgis 5（符号链接/系统目录）、flatgeobuf 真实路径 1、
  domain_c_raster 后缀 1、llm 真实 socket 1、mapspec_store 1、recovery
  ledger 双进程 1；
- **2 项顺序 flake**：geocompute events/gpu-gating —— master 通过、分支
  单跑两次通过（全量套件时序敏感）；
- 本线触及域失败数 0；新增 63 项测试全绿。

## 风险与回滚

- **行为变化（有意）**：缺 attribution 的 live 地图自动出现
  「数据来源：—（待补充）」占位（诚实署名原则，§0.5）；autofill 注入
  north/scale 与旧 fallback 等价（id 变为 `__autofill_*`）。
- **兼容**：V2/V3 API 零改动（559 项 solver/corpus 回归锁定）；
  quality_loop 自动通道零改动；`__fallback_*` 保留；v1 legend_spec 干净
  回退；既有 720 项前端测试零改动通过。
- **回滚**：单分支 revert 即可（无 migration、无 schema 变更、无 API 契约
  破坏）。

## 协调点

- **版面描述中间层**（本线 ↔ 08 线）：结构定义置顶 = `frontend/lib/layout/
  composition-descriptor.ts`；export 侧只读消费 chrome 段与 provenance
  （08 先合入则本线适配其定义 —— 当前以本线为准）。
- **legend_spec v2**（03 线冻结，本线只读消费）：消费面为局部窄类型
  （`frontend/lib/layout/legend-labels.ts`），03 合入后由类型通道自然接管，
  无需本线改动。
- **09/10 线**：decisions 工件（CompositionDescriptor.decisions）即评审/
  回归输入；后端 suggested_fix（resolve_layout_collisions /
  break_component_cycle）即 harness 侧修复载荷。
- **layout_solver.py 边界披露**：§8 可改清单未列举 layout_solver.py，但
  「把 solver 升级为选位+自愈」唯一落点即 solver 本体且无兄弟线认领；
  本线以纯增量（V4）落地并锁定 V2/V3 回归，请 reviewer 确认接受。
