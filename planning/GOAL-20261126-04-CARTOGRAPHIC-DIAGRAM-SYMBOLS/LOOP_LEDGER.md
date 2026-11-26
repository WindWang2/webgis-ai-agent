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
