# INDEPENDENT REVIEW — G3D01

两轮独立评审均由未参与实现的 subagent 执行上下文承担（独立 context、实读源码、实跑测试）。

## Round 1 — GIS 科学正确性 / 数据诚信 / 许可署名 / 安全

**结论：PASS-WITH-FIXES**（修复后复核通过）

要点：
- 许可/署名目录逐条与官方口径核对**无捏造**（OpenFreeMap/OSM/Carto/ESRI/OpenTopoMap/高德/天地图/AWS terrarium；AWS 多源署名清单与 tilezen/joerd attribution.md 逐项一致）。
- synthetic→display_only、analysis_grade 门、terrarium 编码/量化/nodata 语义、PMTiles 路由鉴权-顺序/路径守卫/字节帽/content-type 治理均达 fail-closed 标准，负例为"移除守卫即红"的真测试。

发现问题：
- **P1-1（已修）**：map-panel 底图切换后的 terrain 重挂点无条件 enable —— air_gapped 部署一次底图切换即静默重挂外网 DEM 兜底。修复：`frontend/lib/map-kit/terrain-governance.ts` 统一治理入口，两个挂载点共用 + 三态回归测试。
- P2-1（已修）会话 DEM 套用 AWS 兜底署名 → 仅兜底 URL 才带兜底署名；P2-2（已修）AWS terrarium max_zoom 14→15（官方 z0-15）；P2-3（已修）高德 GCJ-02 偏移披露；P2-4（已修）`{env:}` 豁免收紧为整段 URL（http+占位混合拒绝 + 3 个新测试）；P2-5（已修）确定性措辞收敛（同一 NumPy 构建内逐位一致）；P2-6（已修）AVIF 嗅探 brand 校验。

## Round 2 — 前后端 ABI / 并发 / 回归覆盖 / 运维可靠性

**结论：PASS-WITH-FIXES**（修复后复核通过）

要点（通过面）：
- 跨语言契约锁双向扎实（byte 幂等 + id 集合全等 + 逐字段/顺序敏感比对 + `{env:}` 展开语义对拍）。
- base_layers 派生：原 12 条 name+keywords 与旧硬编码**逐条一致**；全部消费方（context_builder/layer_manager/templates/#557 套件）实跑通过；无 import 环（前端链与后端链均验证）。
- PMTiles 服务并发：跨 event-loop coalescer（#1683 修复形态）、side dict 仅 leader 协程写、双前缀失效、ETag 确定性 —— 逐项核验 OK；API-01 负例真实有效。
- #605「3D terrain survives basemap switch」实跑通过；导出面 88 tests 无金样破坏；生成物 diff 无夹带。

发现问题：
- **P1-1（已修）**：`scene_basemap_unavailable` / `scene_terrain_no_source_air_gapped`（及既有的 `scene_terrain_unavailable`）只进证据环无消费面（exporter 词表/转发缺口）。修复：export-chrome 词表 + 后端 `RENDER_DIAGNOSTICS`/`EMITTER_REGISTRY` + exporter 环转发（basemap 码不限 is3D）；诊断契约/死码门 106 passed。
- P2-2（已修）`_providers_by_id` 不过滤 llm_visible 的刻意性 docstring 说明；P2-3（已修）getMapStyle per-provider 署名直接回归测试（导出纯函数 + 全量 raster 条目逐条断言）；P2-4（已修）离线部署文档补充"构建期 inline，翻转须重建前端"与 G3D01 地形诚实边界。

## 复核验证（两轮修复后）

- 后端：68（新增套件）+ 106（诊断契约）passed；ruff clean；生成物三件套（drift/ledger/OpenAPI 快照）经官方通道刷新，快照 diff 纯 additive。
- 前端：vitest 全量 478 files / 4501 passed；typecheck 0 错误；eslint 干净。
- 既有环境失败（parity corpus 等 35 项）经干净 origin/master 同 venv 复现同红 —— 非本分支责任（证据见 EVIDENCE.md §2）。
