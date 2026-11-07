# G04-CARTOGRAPHY-COMPOSER PLAN

使命:在 #1975/#1993 已落地的出版制图地基上,把「数据/要求→地图类型→组件组合→实时显示→验证→出版」做成深层连通 —— 不重做出版引擎,不建第二求解器/MapSpec/renderer。

## Oracle 表(可检验完成条件)

| # | Oracle | 验证方式 |
|---|---|---|
| O1 | #1999:显式 A4/A3 导出链执行 `solve_layout_v5`(浏览器任务链携带求解结果或等价接线),回执如实披露 evaluated/not_evaluated | characterization 失败测试 → 修复 → 转绿;两次运行 |
| O2 | #2000:默认矢量 PDF 字节稳定 —— 已求值导出不通过持久标记污染后续默认导出;求解结果不落第二事实源 | 失败测试:先 A4 导出再默认导出,默认输出与无 A4 前置完全一致 |
| O3 | #2002:组件族锁(`legend` 锁保护 `legend__1`/`legend-{role}`)在约束求解中生效,`user_lock_kept` 如实披露族命中 | 失败测试 → `is_entity_locked` 接入 → 转绿 |
| O4 | #2003:矢量链指北针随 `view.bearing` 旋转,与 live/canvas `rotate(-bearing)` 语义一致 | SVG 输出含 rotate transform;parity 测试 |
| O5 | CM-01:判选输入单一投影器 —— planner.py 与 tool_map_product.py 共用同一 ctx 构建函数,fixture 锁双侧一致 | 新模块 + 双侧接线 + 一致性测试 |
| O6 | CM-02:模板→组件契约可机读 —— 主要模板的必需/可选/禁忌组件经 ComponentRegistry/契约校验;apply_contract per-layer 展开语义与 composer 对齐(或 typed 披露差异) | conformance 测试 |
| O7 | CM-03:图例/图表与图层分类同步 —— 过滤/改分类后图例与图表不留过期数 | 联动测试(前端 vitest + 后端语义检查) |
| O8 | CM-06:实时 vs Canvas PNG vs SVG vs PDF 关键图元 parity(旋转指北针/图例口径/单位/中文字体)有机器测试 | parity 测试套件 |
| O9 | CM-08:增量语义 —— 组件改色不重跑 GIS 算法(digest 不变/无新 artifact);"unknown/partial" 诚实披露 | 事务测试 |
| O10 | CM-09:导出 receipt/manifest 贯通真实导出,verdict 可读 | 集成测试 + 产物 |
| O11 | CM-10:黄金案例集(成都小学点位+行政统计+图表、重庆医院可达性、多变量、时序、A4 专题图、empty-state)以真实本地 fixture 生成 | 生成命令 + 产物 + 结构断言 |
| O12 | 三份文档:`CARTOGRAPHY_COMPOSITION_GRAMMAR.md`/`RENDER_EXPORT_PARITY.md`/`MAP_PRODUCT_GOLDENS.md` + QA 报告 | 文件存在且与代码一致(引用处可核) |
| O13 | 独立 review(子代理 B 冷读)无未处理 P0/P1 | REVIEW.md |
| O14 | 真实 PR 创建(base=master),含证据/测试命令/风险披露 | PR URL |

## Wave 结构(每 wave = 语义化 commits)

- **W1 断链清偿(#1999/#2000/#2002/#2003)**:characterization 失败测试先行 → 修复 → 回归。这是全部后续 Oracle 的地基。
- **W2 组合语法(CM-01/CM-02)**:判选输入单一投影器;契约覆盖与 per-layer 对齐;`CARTOGRAPHY_COMPOSITION_GRAMMAR.md`。
- **W3 一致性与证据(CM-06/CM-09)**:parity 套件(旋转/图例/单位/字体);receipt/manifest;`RENDER_EXPORT_PARITY.md`。
- **W4 增量语义与黄金案例(CM-08/CM-10 + CM-03/CM-07 回归)**:改色不重跑;unknown/partial;黄金案例;`MAP_PRODUCT_GOLDENS.md` + QA 报告。
- **W5 收口**:全量回归 ×2、独立 review、master 同步、PR。

## 红线

- 单求解器 `solve_layout_v5`;单 MapSpec;单组件实例 id 惯例;不碰 `requirement_ir/`、`execution_compiler/`(PR #2011 域);不碰 Pi Bridge/Data Fabric。
- 资源:pytest 无 `-n` 或受控;前端 vitest 分批;-j4 上限。
- 默认导出字节稳定是契约(#2000 修复不得改变既有默认输出)。
