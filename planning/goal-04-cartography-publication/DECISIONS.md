# DECISIONS — goal-04-carto-pub（R2 前为草案占位，定案后更新状态）

| # | 决策 | 状态 | 理由/边界 |
|---|---|---|---|
| D1 | 约束 DSL 做独立可序列化模块 `LayoutConstraintSpec v1`（新文件 layout_constraint_spec.py），从现有 ComponentPlacement/描述符派生默认，不先改 mapspec_schema.py | 草案 | 规避 R3 跨域红线；schema 接线作为后续最小 additive 单独 commit（若确需） |
| D2 | solver 扩展走新入口（如 solve_layout_v5 / solve_with_constraints），不修改 V2–V4 既有行为与 golden | 草案 | R9 回归锁定 |
| D3 | D4 发现词表落 semantic_checks 系新模块 + selfheal_actions 动作注册，不写 quality_loop.py | 定案 | #1965 占用（PR_OVERLAP_MATRIX A） |
| D4 | 双实现（Python/TS）扩展一律共享 golden corpus 对拍，坐标/文本 3 位小数对齐沿用 | 草案 | 既有跨树模式（label-solver.test.ts 先例） |
| D5 | 六类出版用例生成器 + 结构 golden 优先，像素级仅在 Chromium 可行时执行并如实标注 | 草案 | R5 |
| D6 | must-fit 语义：关键说明（methodology_note/attribution/title 副题等 key_note）在溢出时折叠/转栏/换行，不截断、不静默删除；其余可 suppress+披露 | 草案 | 任务 D1"关键说明不能截断" |
| D7 | 缺瓦片披露走有界聚合（计数 + 首 N 样本 + 来源层），不做全量清单 | 草案 | 预算边界（closed-loop 性能节先例：bounded evidence） |
| D8 | ADR-0283（出版约束布局与标注披露）随 R3 纵切起草，R8 review 前定稿 | 草案 | 遵循仓库 ADR 纪律（docs/adr/ 0283 为下一个编号） |
