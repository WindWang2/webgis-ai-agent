# PR Overlap Matrix — G3D01（提交前须重跑 fetch + rebase 后更新）

对账时间：2026-10-26，基线 `1115d683a`。开放 PR 数：**0** → 无未合并重叠。

## 近期合并 PR 内容级对照（均已包含在基线，只复用不复制）

| PR | 主题 | 与本方向关系 | 结论 |
|---|---|---|---|
| #1763 | 模板资产生命周期（版本/授权/退役/恢复） | 模板资产 ≠ 底图/DEM 资源；其"授权/退役"模式可借鉴 | 无文件级重叠；复用思想 |
| #1762 | 任务级端到端验收 lab | 本方向 E2E 走 cartography/journey 既有 lane | 无重叠 |
| #1761 | 工具能力 8 面对账 | 若新增工具能力需登记；本方向不新增 agent 工具 | 无重叠 |
| #1760 | 科学方法契约/三段守门 | DEM 资格输出需与其 `qualification` 状态词表兼容（eligible/degraded/blocked/unknown） | 对齐词表，不改其文件 |
| #1759 | 数据集语义身份/资格生产 | `descriptor_fingerprint` 模式（ADR-0265）被本方向 profile 借鉴 | 只读消费 |
| #1755 | product conformance / export parity | 署名导出面（export-chrome）已有 attribution 通道；本方向修复数据源侧 | 数据流上下游，无冲突 |
| #1622 | RenderWork Protocol | source ACK/`_SOURCE_KINDS` 若有新 kind 需 corpus 再生 | 本方向不新增 source kind（raster-dem 已存在） |
| #1623 | Interaction IR | 不触碰 | 无重叠 |
| #1621 | 项目恢复 runtime | 地形/底图切换恢复依赖其既有机制 | 只读消费 |
| #1617 | 语义缩放 v1.6 | MapSpec 版本=1.6；本方向若动 schema 按加法升 1.7 | 谨慎：优先不动 schema 版本 |
| #1613 | maplibre v6 worker/style-loaded | 前端改动需适配其语义 | 已在测试基线内 |

## 关键"部分实现"面（避免重复造轮子的判定）

| 既有能力 | 本方向动作 |
|---|---|
| `pmtiles_adapter.read_tile_bytes`（无路由） | **新增** HTTP tile 路由复用 reader —— 增量，不重写 |
| `raster_tile_service.render_terrarium_tile` | 只读复用；不另起 DEM 服务 |
| `source_registry.SourceDefinition`（license/quota/auth） | 底图/高程 profile 是**另一域**（渲染资源 vs 数据集源），但字段设计对齐；不混入 data fabric 注册表 |
| `network_dependency.py` 对 browser 瓦片外流的披露 | 本方向把默认 AWS 地形回退收编为显式 profile，消除"未治理默认" |
| `map-panel.tsx` 自愈 watchdog | 保留；本方向补 typed 降级证据，不替换 |

## 结论
零开放 PR 重叠；近期合并提供可复用契约（RenderWork ACK、scene terrain 门、qualification 词表、fingerprint 惯例）。本方向全部为增量接缝：新模块 + 既有文件小改 + 契约测试。
