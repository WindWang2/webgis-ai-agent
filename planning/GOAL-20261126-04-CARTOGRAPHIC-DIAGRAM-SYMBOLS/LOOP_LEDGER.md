# LOOP_LEDGER — 04 线地理统计图符

格式：`轮次 | WP/阶段 | 改动 | 验证 | 判定 | 下一步`

| 轮次 | 阶段 | 改动 | 验证 | 判定 | 下一步 |
|---|---|---|---|---|---|
| 0 | B 基线核对 | 确认 master=59eb3f527、Pi v1.6.1、开放 PR 仅 #2283/#2284、无本方向在飞分支；建 worktree+分支 | git/gh 实查 | OK | 等待三路代码审查，建 SCOPE/PLAN |
| L1 | WP1a 图符纯库 | 新增 app/lib/cartography/diagram_symbols/{__init__,contracts,geometry,qualification,legend}.py（4 kind 族、面积律/线性律/等角律、typed 错误、公共尺度、fingerprint）+ tests/cartography/test_diagram_symbols_v1.py 54 用例 | 54/54 绿；测试抓出 3 个 oracle bug（total_area_encoding 标志未入 scale、包络半径公式、全 null≠字段不存在）+2 个错误断言，全部修复 | OK | WP1b schema |
| L2 | WP1b MapSpec 2.2 additive | mapspec_schema.py +MapSpecDiagramSizePolicy/+MapSpecLayerDiagramSymbol/+layer.diagram_symbol；LATEST 2.1→2.2 + upgrader；SCHEMA_EXPORT_MODELS 注册；ts_projection 再生成（+24 行）；前端 version.ts 词表+upgrader；contract_manifest.json +3 个测试版本钉 +5 个 corpus 期望 bump | schema/corpus/golden/mutation-parity 全绿（唯一失败=既有 scale_portrayal KeyError 基线故障，L0 已取证）；byte 幂等再生成 | OK | WP1c 模型 pack |
| L3 | WP1c 表达模型 | model_packs/diagram_symbols.py 三模型（composition_glyph_map/multi_series_glyph_map/segmented_proportional_glyph_map，circle 层、qualitative、native+实证前提 pitfalls）注册进 MODEL_PACK_MODELS | validate_model_library 零错误（diagram_symbol_legend 改既有 legend 组件类型——不发明组件名）；pack 计数契约是相对式自动满足 | OK | WP2 布局 |
| L4 | WP2 布局 | layout.py（锚定/确定性避让/预算/会计）+19 测试；commit 69fc61eeb | 73/73 绿；测试抓出 compute_glyph_scale 总量编码 v_ref 语义缺陷（须取跨要素总量最大）已修；ruff 零诊断 | OK | WP3 渲染同源（启动双子代理 SA/SB + 主线 authoring 服务） |
| L5 | WP4 后端双入口 | diagram_symbol_authoring/service.py（inspect/plan/apply，preview 零突变，commit 单笔 CAS，_mutation_dependencies seam 同 G27）+ diagram_symbol_tools.py 三工具 + routes/diagram_symbols.py + main/tools 注册 | 12 测试绿（G27 隔离 fixture 模板：零突变断言/CAS 参数映射/superseded/解绑只删块）；三工具注册 ToolRegistry 验证；ruff 零诊断（修 inline_medium→bounded） | OK | 等 SA/SB 返回做前端接线 |
| L6 | WP4c UI 面板 | lib/diagram-symbols/api.ts（asDiagramError 同源错误提取）+ components/hud/diagram-symbol-panel.tsx（kind/类别行/尺寸政策/计划→应用，epoch 守卫，preview↔commit 视觉区分）+ layer-style-panel 加性挂载 + hud.json zh/en diagramSymbol 命名空间 | tsc 0 错（本线文件）、eslint --max-warnings 0 通过（修 liveRevision 订阅化 + 补 barW 输入）；面板 inspect=资格事实真源 | OK | 等 SB 后接 live 渲染/导出/图例 |
| L7 | WP3b 后端 SVG twin | SA 子代理交付：mapspec_to_svg.py +377 additive（oracle 全链消费/4 类图符 path/leader/会计 data-*）+19 测试；主线独立复核后 commit bedf444c5 | 19/19 绿 + parity/PDF 回归 ~340 绿；ruff 干净；诊断码复用（词表冻结）已记录为合理约束适配 | OK | 等 SB 前端模块 |
| L8 | WP5a 后端性能基线 | tests/cartography/test_diagram_symbols_perf_v1.py（10/100/1k/5k 全链计时 + 会计不变式 + 宽松兜底） | 实测：1k=86ms、5k 候选帽 1000=135ms、会计恒等；数字入账 TEST_EVIDENCE | OK | 等 SB；随后前端帧时基线 |
| L9 | WP3a/c/d 前端集成 | runtime attach/detach 挂钩+词表、integration.ts（色板权威镜像/资格抽取/重放闭包）、legend_spec 同源图例（apply 写/只清自己/derive 消费）、svg-emit.ts+mapspec-to-svg 挂钩（typed 披露）、svg-emit+integration+perf 测试 | 图符前端 72/72；caro-ir 175 绿（修漂移闸镜像遗漏）；tsc 0 新增；geoai/i18n 3 失败干净 master 复现归类既有；1k 帧链 131ms 实测入账 | OK | 提交 WP3 前端 |
