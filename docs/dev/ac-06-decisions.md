# AC-06 决策日志（adaptive-cartography/06-symbol-law-runtime）

按 §0.5 全自动默认决策 + 实施中的边界裁决，逐条留痕。权威决策叙述见
[ADR-0155](../adr/0155-adaptive-symbol-law-runtime.md)。

| # | 岔路 | 决策 | 理由 / 证据 |
|---|---|---|---|
| 1 | 符号律 vs 后端 legend_spec 冲突 | **后端为准**，前端只做投影 | §0.5；03 线冻结 v2 schema，本线只消费（thematic-paint.ts） |
| 2 | 新增 paint 键后端未下发 | 符号律兜底 + evidence（law-applied），不静默 | §0.5；paint-bridge `toMapLibrePaint(layer, {featureCount})` 只补缺失键 |
| 3 | 增量更新失败 | 回落 recompile（remove+add 完整保留为安全网）+ `incremental-fallback` evidence + perf 计数 | §0.5；runtime-incremental.test.ts 锁定收敛语义 |
| 4 | 未知 source 类型 | **显式报错**（UNKNOWN_SOURCE_TYPE 编译错误）+ evidence；不再产出空 FeatureCollection | §0.5；旧行为测试（compiler.test.ts）按新契约改写 |
| 5 | 密度阈值 | cluster=5000 / heatmap=20000 / line=5000，与 VIEWPORT_RENDER_BUDGET、MVT 5000 同基准，可配 | §0.5 |
| 6 | density_signal 与 05 线重复 | **本线实现**（05 线未落地），导出 `density_signal` 别名供其 import | §8 协调点；branch 检索无 05 线分支 |
| 7 | 符号绝对基准值 | 归本线（DEFAULT_SYMBOL_LAW）；05 线只给 size_ratio | §8 |
| 8 | 类型扩展路径 | 改 `mapspec_schema.py` + `ts_projection.py` 再生成，**不手改 generated** | ADR-0138 单一真相；§8 允许的胶水范畴（与 03 线 schema 接口无交集，无冲突）；决策已同步 ADR |
| 9 | live-spec.ts 的层型→opacity 键表缺新层型 | 最小 additive：background→background-opacity、hillshade→hillshade-exaggeration | 类型并集扩展的强制编译面；文件本身为共享中立区，改动 2 行，diff 佐证 |
| 10 | `resolveHeatmapRadiusPx` 的 `return 30` | **保留** —— 它是后端 heatmap_contract 的前端镜像（显式意图归一器），非 paint 常量；符号律消费其产物作 zoom=8 锚点 | 常量销项 CSV 该行 disposition；渲染 paint 已无常量 px |
| 11 | runtime.ts 的 05 线领地 | `addLabelSublayerSafe`（:710-743 段）、removeLayerSafe/applyFilterSafe 的 label 镜像行**零改动**；addLayerSafe 的 label 挂载条件行仅 additive 两个层型守卫（background/hillshade 不挂 label 子层，无源层挂 label 必崩） | §8 红线；`git diff` 逐行核查（见台账） |
| 12 | heatmap 对象颜色（StyleMethod 对象） | constant 方法对象接受（等价裸 hex）；非 constant 对象 → unmapped evidence | 原实现只认裸字符串 —— 契约内形态被静默丢弃属缺口 |
| 13 | `out_of_range` 在 categorical 上出现 | 不映射（match default 臂吸收越界）+ unmapped evidence | v2 schema 数值域语义 |
| 14 | P0 性能基线的「帧率」口径 | 无浏览器 10k 帧率设施（S1 确认）→ 以 diff/compile CPU 中位时 + render-debouncer FrameStats 为代理；P7 增补 MapLibre 调用计数断言 | recon §3；任务允许「简易 performance.now 采样」 |
| 15 | diff 键级分解替换 isFilterOnlyChange | 单次走查完成分类（旧实现 isDeepEqual + rest-spread 双重走查）；paint+filter 同帧变化由「paint patch + filter patch」两条承载（旧为 recompile） | 语义超集；reconciler.test.ts 全绿 |
| 16 | z-order 最小移动集的并列裁决 | LDS 并列时取实现确定的选择（最早可保留者优先）；验收语义 = 移动数最小 + 最终栈序正确 | renderer-m4.test.ts 锁定具体行为 |
| 17 | content_revision 相等即跳过 inlineData 比较 | **信任该字段是数据版本 CAS**（生产契约：改 inlineData 必须 bump revision；既有 MVT cache-buster `v=` 已同样信任它）。revision 相等但对象身份重建 → 只比较其余键 | review R2-P2；契约说明进 ADR-0155 D5；指纹在此路径不做交叉校验（省 10k 级 stringify） |
| 18 | 共享源不参与密度自适应 | 同一 geojson 源被 ≥2 个 circle 层引用时跳过 auto-cluster/heatmap 改写 —— 源级聚合会静默改写兄弟层的数据视图 | review R2-P2；compiler.density.test.ts 回归用例 |
| 19 | fallback re-add 强制 z-order 重同步 | paint/layout patch 的 layer-absent 回落路径置 `zSyncForcedByFallback`，双路径 gate 消费后复位 —— 防 lastLayerOrderKey 停留旧值导致持久 z 漂移 | review R2-P2；runtime-incremental.test.ts 回归用例 |
| 20 | strokeWidthExpression 无生产消费者仍保留 | 符号律公开默认面的一部分（05 线 label 描边可 import）；非投机抽象 | review R2-P3；§8 协调点 |
| 21 | paintKeys/layoutKeys 仅作信息位 | reconciler 保持纯 diff；runtime 以 native 字典重diff 决定 setter 目标（方言转换后才可见真实目标键） | review R2-P3；可接受的信息重复 |
| 22 | heatmap 自动改写的图例分歧披露 | 带 legend_spec 的层改写 heatmap 时 evidence 携带 `legend_divergence: true` | review R3；compiler.density.test.ts |

## 与 05 线的文件切分声明（§8 硬约束）

- 本线新增独占：`symbol-law.ts`(+test)、`ac-06-*` 文档、schema 快照。
- 本线修改的独占文件：renderer.ts（符号/尺寸/色带 + z-order 算法 + registry note）、
  compiler.ts、reconciler.ts、types.generated.ts（生成）、paint-bridge.ts、
  thematic-paint.ts、runtime.ts（标注函数段外）、perf-counters.ts（新增计数器）。
- runtime.ts 标注函数段（`addLabelSublayerSafe` :710-743、removeLayerSafe/applyFilterSafe
  的 label 镜像行）**零改动** —— `git diff lib/mapspec-runtime/runtime.ts` 可核。
- 未触碰：`frontend/components/**`、`app/**` 业务逻辑（仅 mapspec_schema.py /
  ts_projection.py 两处契约胶水）、`migrations/**`、`.github/workflows/**`、
  `label-layout.ts`（05 线文件，尚不存在）。
