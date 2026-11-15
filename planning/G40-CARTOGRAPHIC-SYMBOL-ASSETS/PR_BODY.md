# feat(g40): 可复用点符号与面纹理资产工作台

## 用户问题

用户与 Pi 此前只能用 circle 半径/颜色、线型、fill 纯色表达点/面图层——没有图形资产：无法让点层用三角形/五角星/受控导入图形，无法让面层用可解释晕线纹理，更没有可复用、可版本化、跨图层共享且钉版本的资产目录。MapLibre 的 icon-image/fill-pattern/addImage 原生能力全仓零接线。

## 行为增量（对最新 master 1276692737）

1. **版本化资产目录**：`cartoasset.*` 资产（点符号/面纹理）draft→publish→retire（软退役：钉版本重放只读放行）→restore（旧内容造新 revision）。身份 = `asset_id + revision + content_digest`；名字只是显示名；同内容按 owner 独立成行（sha256 CAS 内容去重不合并权限）。新表 `cartographic_symbol_assets(+versions)`（照 0098 先例；迁移 `g40_cartographic_symbol_assets`），内容入 durable BlobStore。
2. **受控 SVG 子集导入**：defusedxml + 有界预算（字节/节点/嵌套/坐标/路径命令/viewBox 4-256）；script/事件/外链 href/CSS url()/style 元素/DOCTYPE 实体/text/clip/filter/渐变/无名 defs/嵌套 use 全部 typed 拒绝（含具体元素名，绝不静默删除后宣称成功）；path 全命令展平（Q/S/T/A→C、相对→绝对、transform 数值展开）；导入即规范形，预览/live/出版/图例消费同一 digest。
3. **图层绑定**：MapSpec `symbol_asset` additive 块（typed 参数上下限；类别映射 key_type number/string/bool——`1`/`"1"`/`true` 不并类；`prev_paint_snapshot`）。UI 与 Pi 同一服务：预览零 mutation；应用/解绑 = 单笔 `apply_gis_mutation(UpsertLayerIntent, expected_revision)` CAS + checkpoint + undo；agent 命中锁层由引擎统一拒绝；兄弟字段（label/bivariate/scale_profile/visibility/paint）逐字保留。内置 9 资产（6 点形状 + hatch/cross_hatch/dots 纹理）代码定义走同一条校验/渲染路径。
4. **live 渲染**：MapSpecRuntime 预热资产（迟到 epoch 丢弃）→ `map.addImage`（内容+参数寻址 id；pixelRatio 2；LRU ≤32MB；引用计数跨 frame 实例）→ 点符号投影 symbol/icon-*（碰撞合同：`icon-allow-overlap=true + ignore-placement=true` 如实重叠，N 要素 N 图标；`declutter`/map 对齐显式 UNSUPPORTED 拒绝）；面纹理 fill-pattern（类别拆过滤子层 ≤16，未匹配保持主/other 纹理）。headless compiler 同一合同（icon-image match 表达式 + `symbolAssets` 引用清单）。
5. **图例同语义 swatch**：G34 authored legend 条目附资产 swatch（规范形+实例参数预渲染）；无 legend_spec 的绑定层补单条目（同色不同向的两个纹理层可分，S12）；屏上 categorical legend 渲染同一规范形内联 SVG。
6. **出版三格式**：Python SVG 编译消费规范形（`<defs>/<use>` 变换链与 icon-anchor/rotate/size 同式；纹理 `<pattern patternTransform>`；evenodd 洞语义既有）；点符号障碍=变换后保守外接盒进 G16 v1 接口；出口链（G34 submit_export + G26 mixed export）冻结解析（digest pin）并记 `manifest.symbol_assets.{resolved,unresolved}`。**实证 librsvg 2.63.4 回归**（CLI 缩放≠内在尺寸时 pattern 内容丢失）→ `prepare_svg_for_rasterize` 改写根尺寸规避（无纹理路径字节不变）；WeasyPrint 70 pattern/use/洞像素实证通过。
7. **Pi 工具**：`webgis_symbol_asset_list/create`、`webgis_symbol_layer_inspect/preview/apply/unbind`（side_effect 如实；`map_mutations=("symbol_asset",)` 新词表；回执有界）。
8. grammar 诚实提升：`CHANNEL_RUNTIME_STATUS.texture` unsupported→partial（受控资产链已验证；自由纹理/CVD 未成契约；分配门只认 supported 不变）。

## 复用与范围

- 复用：MapSpec 生命周期引擎（CAS/checkpoint/undo）、durable BlobStore、G34 units/legend/marginalia、G16 障碍接口与求解器、0098 资产表先例、composition_assets scope 纪律、G33 服务模式、Pi native surface/journey 架构。不建第二套 registry/MapSpec store/agent loop。
- 不做（诚实边界）：素材市场/网络抓取/图片生成/任意矢量设计/复杂线装饰/沿线箭头/新标签求解器/declutter 首版/map 对齐旋转/G41 双变量语义。

## 实际验证（TEST_MATRIX.md 全记录）

- 后端 40 passed（规范形/导入恶意矩阵 S05-S08/绑定 CAS/undo/锁/S18 引用删除/S02 typed/出版）
- cartography 全量 3654 passed（deselect 1 = 干净 base 同样失败的既有基线 carto_ir_corpus scale_portrayal_cases）
- **真实 Pi 子进程旅程 1 passed**（PI_JOURNEY_E2E=1，vendor v1.5.2 pin b4afdddf4）：list→apply(钉版本)→unbind 全 dispatch，MapSpec 权威断言 10/10
- 出版回读 5 passed：SVG 自包含（defusedxml 解析）+ **PNG（rsvg）与 PDF（WeasyPrint+pdftoppm）真实像素 oracle**（三角/晕线/背景/洞）
- 前端 vitest 13 passed + `tsc --noEmit` 0 error + ruff（G40 触及文件）全绿
- 未测项如实披露：真实浏览器像素级（S15/S16/S24 live 面）未运行——以 MapLibre .d.ts 亲核 + runtime/compiler 单测 + headless 结构断言 + Pi 权威面覆盖语义（TEST_MATRIX「已知未测项」）

## 独立评审

Round 1 / 修复 / 复审记录见 `planning/G40-CARTOGRAPHIC-SYMBOL-ASSETS/REVIEW.md`（reviewer 为独立 subagent，未参与实现，自行复跑测试）。

## 兼容 / 迁移 / 回退

- 全部 additive：新表（可重入 DDL、downgrade 反序）、MapSpec 新可选块（旧文档行为不变——`symbol_assets=None` 时编译输出逐字节不变，测试锁定）、诊断词表 +2 码（带发射点登记）、`MAP_MUTATION_KINDS` +1、i18n 新键。
- 与 open PR #2117（G37）/#2118（G36）共享文件仅 `app/tools/__init__.py` 注册行 + 各自新文件；无逻辑耦合。
- 回退：revert 本 PR 即可（无数据迁移写回；旧会话无绑定块不受影响）。

Task: G40-CARTOGRAPHIC-SYMBOL-ASSETS
