# MULTISCALE_2D_3D_CARTOGRAPHIC_SCENE_INTELLIGENCE_REVIEW — 独立对抗性审查记录

- 分支：`cartography/multiscale-scene-intelligence-v1`
- 基线：`origin/master` = `faa453a8`
- 审查方式：2 个独立 adversarial review agent（正确性/安全/并发 与 前端/性能/契约漂移 各一）+ 主 agent 自查 + 修复后回归
- 审查 diff：`git diff origin/master...HEAD`（11 commits）

## Findings（全部经代码验证，非 diff-only）

### P0（1 项 — 已修复）
| ID | 问题 | 修复 | 回归测试 |
|---|---|---|---|
| P0-1 [R1] | `GET /layers/data/{ref}/terrain-tiles/...` 缺 `require_owned_session` 依赖 —— DB 层会话归属校验缺失，无 owner-token 的会话可被跨租户读 DEM（sibling raster-tiles 路由有此依赖） | 路由签名补 `_conv: Conversation = Depends(require_owned_session)`（layer.py）；顺带 503/422 语义分离 | 与 sibling 同款依赖；人工对照两条路由签名 |

### P1（4 项 — 全部修复）
| ID | 问题 | 修复 | 回归测试 |
|---|---|---|---|
| P1-1 [R1] | `SCENE_TERRAIN_SOURCE_REF` 不在 `BLOCKING_VALIDATION_CODES` → 悬空 terrain 源被降级为 warning 并持久化；分支自己的测试把悬空提交固化为成功 | 两码（REF/_TYPE）加入 blocking 集 + coordinator 增加类型检查；fixture 补种 dem 源；新增悬空/类型/pitch 越界拒绝测试 | `test_dangling_terrain_source_rejected_at_commit` 等 3 个新测试 |
| P1-2 [R1+R2 双确认] | `enable3DTerrain` 未声明 `encoding` → MapLibre 按 mapbox 公式解码 terrarium 瓦片（0m → ~+829km 伪地形）；AWS fallback 同样带潜在缺陷 | `TerrainOptions.encoding`（默认 terrarium，同时修复 AWS 潜在缺陷）+ maxzoom 透传；map-panel 传递 spec 源 encoding | `renderer-m4.test.ts` encoding 断言 ×2 |
| P1-3 [R1+R2 双确认] | `scene_terrain_unavailable` 注册的 emitter（runtime.ts）不存在 → 死码门必红；地形失效无披露通道（静默降级） | runtime.ts 实装 map error 监听：声明 terrain 源的瓦片错误 → 证据环 → 导出披露；dispose 对称解绑；防御性挂载（最小 map 桩） | 死码门 73 passed |
| P1-3b [R2] | `plan_map_scene` 建议含 terrain 但无 `source` → 文档化的 plan→set 流程必然被 SetSceneIntent 拒绝 | 工具新增 `terrain_source` 绑定参数；未绑定时诚实省略 terrain + `terrain_source_note` 披露；组合流程测试 | `test_plan_to_set_composed_flow_with_terrain` 等 2 个新测试 |

### P2（4 项 — 3 修复 1 记录）
| ID | 问题 | 处置 |
|---|---|---|
| P2-1 [R2] | 3D effect 依赖 `getCommittedMapSpec`（稳定标识）→ spec 提交后地形参数不重放 | 修复：依赖加 `liveGeneration`；且 effect 移到声明后（TDZ）；并与 #605 re-mount 共用 `buildSceneTerrainOptions`（同时修掉硬编码 1.5 stomp） |
| P2-3 [R2] | symbol layout 透传无白名单/无 StyleMethod 编译/text-field 不补 glyphs → 非法键可致整层静默不渲染 | 修复：`text-*/icon-*/symbol-*` 前缀白名单 + StyleMethod 规范化 + unmapped-key evidence + glyphs 条件扩展 |
| P2-4 [R2] | 证据环追加式 → 数据修复后导出仍报旧披露（虚假披露） | 修复：每遍 reconcile 重建证据环（快照 = 当前现实） |
| P2-2 [R1] | `encode_terrarium` 越界 uint8 静默回绕（单位标错的 DEM 产出合理但错误的高程） | 修复：`TERRAIN_ELEVATION_OUT_OF_RANGE` 域守卫 + 渲染器映射 + 3 个边界测试 |

另：**P2-1 [R1] `layer.extrusion` 类型化使畸形历史 spec 从 unknown 披露变 invalid 披露** —— 判定为**接受的语义收紧**（生产写入面只有 converter，恒带 height_field；影响面 = template_codegen_evaluator 评分与披露种类；显式披露优于静默容忍，ADR-0120 R1-C2 同哲学）。已写入 ADR-0199 已知边界。

### P3（记录）
- [R1] `TERRAIN_RENDER_FAILED` → 503（基础设施）与数据拒绝 422 分离。**已修复**。
- [R1] `scene.camera.pitch/bearing` 写入无界。**已修复**（[0,85]/[-180,180] 写入校验 + 测试）。
- [R1] `coordinator.validate` 对 `terrain.source=None` 的报错文案误导。**已修复**（显式非空校验）。
- [R2] `MapSpecLayerExtrusion` 缺 `height_legend` 键 → 未知键披露（工具写入该键）。**接受**（extra=allow 保留数据，披露是收口的预期行为；后续可加键）。
- [R1] 地形模块 LRU 无 epoch 守卫（并发失效窗口 ≤5s 路径 TTL）。**记录**（既有 color 路径同类残余，ref-lifecycle 键清扫已覆盖 terrain 键）。
- [R1] `scene_quality_report_safe` 内部异常 fail-open 返回 passed=True。**记录**（门未接线前的防御包装；接线 PR 需改为 fail-closed + not_evaluated 语义）。
- [R1] `enable3DTerrain` 不更新已存在源的 URL（换 DEM 需刷新）。**记录**（scene 换源属罕见操作；下一个接线 PR 处理）。
- [R1] comparison-view 副屏不应用 terrain（既有缺口）。**记录**。
- [R2] scene_quality/degradation/selfheal/camera/lod 为已测库、无生产消费方。**记录**（ADR-0199 已声明自愈编排/质量门接线为下一 PR；commit message 不再暗示运行时行为）。
- [R2] `plan_map_scene` 对越界词表静默钳制。**接受**（规划面保守档；决策 reasons 全量披露）。

### 误报/复核澄清
- R2 初判 "M8 wiring DOA"（P1-1）与 R1 的 blocking 缺失（P1-1）相互独立但同域 —— 均验证为真，分别修复。
- R2 报告 renderer.ts 曾有 `hasHeightProperty` 影响断言 —— 实际 `renderer-m4` 无此断言；以实际测试结果为准（1186 全绿）。

## 与最新 master / open PR 的交叉文件

- `origin/master` 于执行窗口复核两次（faa453a8 无新提交）。
- PR #1335（evidence_claim/hotpath/mission_runtime）与 #1336（geoai/modelops/frontend geoai）与本分支**零文件交集**；本分支触碰 `lifecycle_engine.py`/`layer.py`/`raster_tile_service.py`/`compiler.ts` 等制图面，两 PR 均未触。
- semantic-checks 契约文件（`semantic-checks.v1.json`）为唯一跨域共享文件，v1→v2 显式升级（blocking 集扩编是契约设计的预期变更路径）。
- **不需要 integration PR**。

## 结论

- P0：1 → 已修复并测试。
- P1：4 → 全部修复并测试（含死码门 CI 红）。
- P2：4 修复 + 1 接受记录；P3：6 记录（1 项顺手修复）。
- 无未处理 P0/P1。
- 双跑 Oracle 一致：cartography 1125✓（19 pre-existing skips）/ scene+回归 356✓ / perf 6✓ / frontend 1186✓ / ruff+tsc+diff-check ✓。
