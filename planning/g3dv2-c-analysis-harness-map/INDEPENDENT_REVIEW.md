# INDEPENDENT_REVIEW — g3dv2-c-analysis-harness-map

评审人：独立 subagent（未参与任何实现；general-purpose 上下文）
方式：通读 6 commit 全量 diff + 关键文件全文 + **实际运行**全部新增测试
（unit/scence3d/acceptance/golden）+ 自写独立证伪脚本（/tmp 临时，只读仓库）
+ 边界实验（EXP1–EXP5）。

## 第一轮结论（2026-10-28）

**需修复后合**：1×P1 + 2×P2 + 7×P3。亮点：科学内核（记账恒等式/单位
治理/fail-closed）与架构纪律（单一铸 ref 缝/单一绑定存储/模板不抢占）
质量高；测试以真实全链为主（profile seam/staleness E2E 走真实 dispatch
与 Interaction IR）。

### 发现清单（→ 修复 commit 5a82cd646）

| 级别 | 发现 | 修复 |
|---|---|---|
| P1 | B1/G1/F4：选择上下文对**所有** goal 盖章——非观察者分析（cut_fill）换选要素被误判旧（EXP1 实测复现，击穿 D8 语义收窄）；无对应回归测试 | 戳记收窄到 _OBSERVER_GOALS；登记 ack 回传最终 binding（echo 同形态）；2 条回归锚（非观察者 fresh/观察者同选 fresh+换选 stale） |
| P2 | E1/G2：顶点预算在单大连通多边形下不可达（EXP2：77019 > 20000）——"bounded" 承诺退化 | vertex_budget_exceeded 显式披露 + docstring 口径诚实化（数硬有界/预算尽力）；渗流 fixture 标定测试 + F2 or 恒真拆除 |
| P2 | F1：F27 负例只断言 layer_count==0——不钉类型码，任何意外 bug 失败也绿 | runner 收集 scripted_errors/scripted_error_messages（JSON+内嵌 [CODE] 双形态）；f27-06/07/08/09 钉码/regex；run_3d_analysis 全部失败消息附类型码+内层摘要 |
| P3 | G3：options 未知键静默丢弃（typo 按默认跑） | ignored_options_keys 回显 |
| P3 | G4：跨族参数交叉污染（cut_fill+observer_x → generic 校验错） | _FAMILY_PARAMS 表过滤 + ignored_top_level_params 回显 + 测试 |
| P3 | F3：_parsed 修复无回归测试 | test_parsed_init_regression_reuse_disabled（REUSE=0 下登记必须仍运行） |
| P3 | C1/A5：分区几何硬编码 0.3048 第二真相 + base 舍入不一致 | meta z_to_m/base_z_m 单一真相（未舍入） |
| P3 | A4：cell_size inf → inf 体积（EXP5） | isfinite/1e9 上限防护 |
| P3 | A1：求积"解析精确"措辞过强 | method 注明离散闭式口径 |
| P3 | G6：剖面图抽稀丢末端点 | 切片补终点 |

### 确认无问题面（第一轮）

- A2/A3：像元原子记账恒等式（含 45° 旋转仿射）数学成立；无度²冒充 m² 路径；
- B2/B3：fail-closed 核对面（无 DEM decline/datum 混算拒/nodata 拒/空掩膜拒）；stats 零漂移真实钉住；
- C：单一铸 ref 缝/单一绑定存储/无第二执行引擎；模板无抢占（golden 5 新增、既有零改写）；
- D：白名单扩展无越权面（所有权校验+载荷形状校验+前缀收紧）；
- E：sync 工具经 THREAD 策略不阻塞事件循环；内存护栏与取消点在位。

## 第二轮复验（2026-10-28，修复 commit 5a82cd646）

评审员重跑证伪实验（EXP1/EXP2/EXP5）+ 受影响 6 套测试（217 passed）+
F1 反证对拍（evaluate_expectations 对错误码 → FAIL、正确码 → PASS）：

| 项 | 状态 | 复验证据摘要 |
|---|---|---|
| B1 盖章收窄 + ack.binding 回传 | CLOSED | EXP1 复跑：cut_fill 换选后 fresh（上轮 stale）；echo 同形态 |
| E1 vertex_budget_exceeded | CLOSED | EXP2 复跑：exceeded=True（77019>20000），恒等式仍成立；F2 双分支钉住 |
| F1 负例类型码 | CLOSED | 反证实测：错误码期望 → FAIL；恒绿路径已封 |
| G3/G4/F3/C1+A5/A4/G6/F2（7×P3） | CLOSED | 逐项 file:line 核验 + EXP5 复跑（inf cell → ValueError） |

新问题扫描：无回归（消息加码/additive 键/族过滤均不破坏既有断言）。
轻微残留（不阻塞）：options 透传未按族过滤（与修复前行为一致，非回归）。

**第二轮结论：全部 9 项 CLOSED，可合。**

## 最终复验

（全量门禁复跑记录见 EVIDENCE.md / LOOP_STATE.md）
