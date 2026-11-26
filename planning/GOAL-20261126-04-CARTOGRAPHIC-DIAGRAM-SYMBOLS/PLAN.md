# PLAN — 04 线地理统计图符与复合专题制图表达

Oracle（终止条件，全部可验证）：
1. ≥3 类查重确认缺失的地理锚定统计图符（候选：①组成比例饼/环形（可选总量面积编码）②同量纲多序列条形/堆叠条形（正负基线）③多变量分段比例符号/双时点变化配对图符）可经 UI 与 Agent 双入口从真实数据生成；
2. 图符数值 oracle 通过（角度/面积/长度/类别与数据一致，面积∝值→半径∝√值）；
3. live MapLibre 渲染真实可见（跟随 pan/zoom/pitch/DPR），交互命中 feature identity 稳定；
4. ≥1 种单幅发布格式真实产出（同一图符定义/单位/类别序/图例/anchor policy），其他格式支持级别如实披露；
5. 图例/字段/refs 语义对账，正负例（组成≠100%、负值/缺失/零、重复类别、密集碰撞、缩放层级）齐全；
6. 性能有实测基线与明确降级（10/100/1k 图符）；
7. 独立 review（A 科学语义 / B 安全与同源 / C 交互旅程）两轮，无本线未解决 P0/P1；
8. PR 已提交至 master（不合并）。

## WP 分解（依任务书 E2，细节待代码审查矩阵回填）
- WP1 表达模型与科学语义：机器可读 data shape（anchor/metric/unit/category order/missing/denominator/normalization/scale policy/颜色图例绑定）；面积编码半径∝√值、长度线性、零/负/缺失分流。
- WP2 构建/布局/数据资格：字段合同→有界图符数据；点/面代表点/线 anchor 策略；确定性 collision/优先级/聚合/引线/密度/缩放层级（增量于既有 label engine）；omitted/aggregated/occluded 计数；geometry descriptor + 稳定 identity + 防 SVG 注入/NaN/无界数量。
- WP3 Live 与导出同源：MapSpec additive → MapLibre 真实渲染；PublicationIR 同一定义导出 ≥1 格式；共享几何 oracle。
- WP4 双写入口：Agent 候选/澄清（不关键词直画饼图）+ UI 编辑（字段/尺度/类别序/色卡/位移引线/图例/显隐），同一 typed mutation/CAS/receipt/revision/ACK。
- WP5 回归与性能：三场景+极端例数值 oracle；10/100/1k 图符性能与降级。
- WP6 独立 review A/B/C + 修复 + PR。

## 里程碑门
- G1（WP1+WP2 后）：模型/资格/几何 descriptor 有单测+负例
- G2（WP3 后）：live 渲染与导出同源断言通过
- G3（WP4 后）：Agent+UI 双旅程真实跑通
- G4（WP5 后）：性能基线入账
- G5（WP6 后）：PR 提交
