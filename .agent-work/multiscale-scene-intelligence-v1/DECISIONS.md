# DECISIONS — 关键决策记录（执行时点，全文 ADR 见 docs/adr/0199）

| # | 决策 | 理由 | 备选与否决原因 |
|---|---|---|---|
| D1 | SceneMode 词表 = `2d / 2.5d / 3d`；2.5d = terrain/hillshade 呈现但无垂直挤出（或反之：挤出但无地形），3d = terrain + 挤出 + 高 pitch | 与任务书里程碑 6（overview/detail/compare 相机、2D↔2.5D↔3D 退化）对齐；避免"半 3D"语义漂移 | 单布尔 is3D 升级为枚举 — 否决：表达不了退化链中点 |
| D2 | MapSpec v1.4：顶层 `scene`（mode/terrain/camera_preset/reason_code/degrade_to/reduced_motion）+ `MapSpecLayer.extrusion` 类型化（吸收既有开放 dict 键面，字段全 Optional 除 height_field） | 沿 1.1/1.2/1.3 纯 additive + identity upgrader 既有模式；热路径不受影响 | 塞进 view — 否决：terrain/extrusion 不是相机状态；layout — 否决：语义不是版面 |
| D3 | terrain 源 = 既有 `RasterDemMapSpecSource`（url/tileSize/encoding）+ `scene.terrain` 只持 `source` 引用 id、exaggeration、vertical_unit、elevation_ref | 单一数据面（sources），scene 只是指针 + 参数 —— 不是第二事实源 | scene 内嵌 DEM url — 否决：绕过 sources 数据面，破坏 ref 纪律 |
| D4 | session DEM → MapLibre terrain：raster_tile_service 增加 terrarium 编码渲染模式（窗口化、有界、nodata→透明、非 DEM ref → 结构化 404，不伪造 0 高程） | 唯一能让 terrain 由**会话内证据**驱动而不依赖外部硬编码源的路径；fail-closed | 始终用 AWS terrarium — 否决：Oracle 要求无证据不伪造 + 数据脱钩 |
| D5 | 前端自动挤出（is3D 时所有 polygon 层）改为证据门控：layer.extrusion 契约 或 paint 高度表达式 或 要素 height 字段存在性证据；否则跳过并披露 `scene_extrusion_no_height_evidence` | Oracle G2 直接要求；披露走 render-scene 既有 degradations 通道 | 保留默认 20 — 否决：伪造 |
| D6 | scene mode 切换 = `SetSceneIntent`（presentation 分区），只改顶层 scene 字段；layers/sources/legend_spec/thresholds 字节不变 → 统计/分级/legend 不漂移由构造保证 + 质量门复核 | presentation-only 既有语义（TRANSIENT/SEMANTIC 分区表）；lineage 不重跑分析 | 重建 spec — 否决：破坏 lineage |
| D7 | 自愈面：scene 缺陷只映射到既有 mutation intents（SetViewIntent 调 pitch、SetSceneIntent 调 exaggeration/mode、PatchLayerPresentationIntent 调不透明度）；visual_healer 4 类 op 词汇表不动 | 任务书红线"自愈只产生现有 MapSpec mutation intents"；SetSceneIntent 在 M2 之后即为既有 intent | 扩 heal op 词汇 — 否决：越权改 ADR-0186 边界 |
| D8 | 质量门 = 确定性 `scene_quality.py`（阻塞/警告分级）挂入 lifecycle 校验链；VLM 侧零改动（ADR-0185 契约冻结），视觉遮挡由既有 5 轴覆盖 + 确定性遮挡代理（pitch×高度×密度的保守估计） | 契约 extra=forbid + "exactly 5 dimension scores" 不变量；确定性优先可回放 | 加 3D VLM 轴 — 否决：破坏冻结契约，需独立版本化 Epic（ADR 记录） |
| D9 | camera planner = `scene_camera.py` 纯函数（overview/detail/compare），zoom 数学独立实现（Web Mercator fit），pitch 分角色档位（对齐 storymap ARC_PITCH_BASE 语义但不共享常量 —— storymap 是章节叙事域） | 产品相机与叙事相机关注点不同；复用会引入跨域耦合 | 复用 storymap camera_planner — 否决：域不同（见上） |
| D10 | LOD = `scene_lod.py` 纯函数：zoom 分档 → {label topRatio、symbol scale、terrain maxzoom、抽稀预算}；落点是既有 spec 能力（label.zoomBands ADR-0154、thresholds.maxFeatures）+ 前端消费 terrain maxzoom | spec 已有 LOD 承载面；不新造 spec 字段，符号律先例（纯函数+evidence） | 新 scene.lod spec 字段 — 否决：承载面已存在，避免双源 |
| D11 | 降级链 3d→2.5d→2d 确定性规则 + 披露码（scene_degradation.py），触发：terrain 源失效/挤出无证据/媒介静态（打印）/pitch 不安全 | 与 exporter/SVG 既有披露码词汇一致（`*_degraded` 风格） | 静默降级 — 否决：证据诚实红线 |
| D12 | ADR 编号 0199 起（#1336 ledger 用至 0198）；tests 全部新文件；types.generated.ts 仅经 ts_projection.py 再生成 | 并行冲突纪律 | — |
| D13 | 不限制 token 统计口径的诚实性：不伪造统计；subagent 用量以 harness 报告为准 | 任务书红线 | — |
