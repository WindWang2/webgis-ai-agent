# H02 PLAN — 制图产品编译与多端质量一致性基础层 VNext

目标（唯一）：让现有制图组件库与模板库组合出的地图产品，在真实地图交互、实时渲染与 PNG/PDF/SVG 导出之间语义一致、组件完整、布局可靠、来源可追溯、质量可审查。

## 边界

- H02 拥有：gis_harness 模板选择/完整性（template_selector/catalog/product_completeness/product_shapes）、app/lib/cartography/、mapspec 表达层/编译接线、mapspec_to_svg 与导出服务、frontend carto-ir/mapspec-runtime/render-protocol/map-kit/渲染器/HUD/导出 + 全部该范围测试。
- H01 只读：workflow_compiler/plan_graph/数据资格/app/tools/chat pi_*/tool_dispatch_service。
- 不做：第二 IR/注册表/词表；重写 RenderWork；动 Pi vendor；绕过 CI gate；他属 issue 的单点修复（只建回归契约，见 RECON §5b）。
- 用户显式操作 user-wins 最高优先级不可破坏。

## Wave 计划（每波 = goal-loop 一轮：单一主题改动 + 验证 + 记账）

- W1 缺口固化：G1–G8 failing test（H02 自有）+ #1747/#1726/#1713 回归契约（xfail，新文件）。验证：新测试红灯原因正确、既有 lane 不受影响。
- W2 后端一致性修复：G1 指纹纳入 representation/visibility（含 canonical 序列化确定性测试）；G2 representation report 序列化 + 主路径计算；G3 组合身份落 layout.composition（产品编译路径 additive）；G4 required_slots 从注册表真契约推导（保留旧兜底为最后手段并披露）。
- W3 产品装配完整性：G5 export 槽位裁决（显式 export intents 时选择含 export_layout 的组合或披露阻断）；G6 conformance 接入产品编译/终验消费面；user-wins 持久化回归（重复制图/更新保持用户决策，复用 Requirement–MapSpec Binding）。
- W4 布局几何契约：碰撞/越界/标题截断/A4 横竖/16:9 的确定性检测测试（复用 resolve-layout/layout solver）；用户 pin 不被自动移动的断言；不可能版式 fail-honest 证据。
- W5 live↔export 语义一致性：G7 nodata guard 双端一致；G8 SVG 导出 ref 水合（或诚实披露）；语义比较投影（只读投影 MapSpec+ACK+render_diagnostics，比对数据 revision/显隐/过滤/class field/色级/legend/来源/时间/组件必需性）；Cartographic Product Manifest（versioned，从真实结果生成，记 fingerprint/data refs/template IDs/export formats/quality findings，无绝对路径/token）。
- W6 QA 门与修复闭环：conformance/completeness/semantic checks 在产品层与终验层的分层接线维持 hard gates（缺证据不 PASS、unsupported 不记 pass、旧代拒收）；有限自动修复幂等复验契约；#1713 回归契约引用。
- W7 golden 工厂：18+ 正例（点/聚合热力/KDE/choropleth/比例符号/栅格/双变量/图表+地图/超长标题/双语/A4 横竖/多 zoom/no-data/图层隐藏/用户锁定/无源 attribution/旧 ACK/重复导出）+ 12+ 负例（不支持组件/字段不一致/filter 漂移/图例误配/剪裁碰撞）跨 Python golden/TS 编译/语义 manifest 三层。
- W8 perf 预算 + 文档：100/1k 图层元数据装配、template search、collision check、重复导出稳定性有界基线；ADR + 文档更新 + 回滚说明。
- W9 独立 review（subagent 独立读码复现）→ red-test 修复 → 二次 review → Oracle 两遍 → PR。

## 验证纪律

- 后端只跑定向 lane（全量受 #1753 阻塞，BASELINE.md 实证）；前端 typecheck/lint/定向 vitest 串行；NODE_OPTIONS=--max-old-space-size=2048 起。
- 关键验证跑两遍；golden 变更记录理由。
- 每波 WORKLOG.md 记：目标/假设/diff/测试命令+退出码/基线对照/review/下一步。
