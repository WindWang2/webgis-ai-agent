# feat(g33): 可复现的专题表达编辑工作台——已有矢量层的分布/断点/类表/比例符号 → 预览 → 单笔权威提交

## 用户问题

对话生成的专题图（create_thematic_map）一旦落图就没有作者编辑面：想换字段、把自动分级改成精确的 0/10/20/50、给类别改名换色排序、把点大小改成「面积∝数值」、改发散中心——都只能重新生成一张新图。既有的 HUD 样式面板只提交常数色/宽/半径；histogram 滑块 widget 的回复只进对话上下文，不形成权威 mutation；自动分级证据也从不被人工决定替换。

## 行为增量

对**已有**矢量专题层（fill/circle/line + legend_spec）：

- **作者检查**（`GET /api/v1/thematic-authoring/sessions/{sid}/layers/{id}/inspect` / `webgis_thematic_style_inspect`）：当前表达指纹/证据、字段清单、同版本全源分布（总数/有效/无效分型 bool/string/NaN/Inf/null/缺失、直方图 ≤96 bins、类别 top+other、类型歧义键）；统计总体恒为提交源全量，无全源（纯瓦片层）如实 degraded，编辑入口拒绝——绝不用可见瓦片/视窗冒充全域。
- **typed 作者计划**（schema_version=1，extra=forbid）：六模式 graduated_auto / graduated_manual / categorical / continuous / divergent / revert_auto；断点严格递增逐对拒绝（带索引，不自动排序）；类别表（键=源值不改数据、显示名/颜色/顺序可编辑、显式 other 桶）；发散中心必须落在有效值域内（全正值域拒绝，不伪造正负结构）；比例符号/线宽映射（radius↔circle、width↔line；area_sqrt=面积∝数值的**精确**函数——stops 携带 r²、输出 √，非近似分段）。
- **预览零 mutation**：与提交同一确定性构建器；类表+计数（统一求值器与 MapLibre step 逐点一致：末断点是图例上界非阈值、恰等断点归上类、首末类吸收越界值）；requested vs actual 类数差异、越界 under/over、无效值口径、重复值退化全部披露。
- **单笔权威提交**：同 id 整层 `UpsertLayerIntent`（一次 CAS / 一个 checkpoint / 一条 provenance），只改本域键（paint.color / legend_spec / data_binding.field / 证据块 / 本域 size 键），source/层 id/opacity/visibility/标签(G16)/scale_profile(G27) 全保留；证据块 `thematic_authoring` 记录人工决定（method="manual"+why 替换旧自动裁决）与完整先前表达快照；superseded→409 nothing-written；源 content_revision 锚定。
- **恢复自动**：优先恢复上一笔作者决定前的完整快照（categorical/LISA 固定科学类别也能真正恢复），无快照的类别层保持当前表并如实披露；一次 undo 恢复完整表达（不止颜色）。
- **UI 工作台**：layer-style-panel 挂载（对齐 G20 raster 面板先例）——分布直方图（按类着色）、模式/方法/色带/反转、断点精确键盘输入（逐对错误定位到 index）、类表（色/标签/顺序/other 桶/计数）、发散中心、大小映射（面积语义声明+样本预览）、预览/应用/取消/恢复自动；epoch 迟到守卫（切层不串台）；预览态与已提交态视觉区分；业务校验全部在服务端。
- **G15 边界**：真实 FrameProductContract.v1 形状识别共享表达作用域（frames[].data.layer_id 成员归属）；共享组成员单边编辑被拒（提示走多画幅产品入口），不复制 shared_expression 计算器。

## 复用与范围（不新建第二套）

分类算法/色带（classify.py/palettes）、legend_spec 合同与 paint 投影（thematic_spec.spec_to_paint 单点）、MapSpec 生命周期引擎（CAS/checkpoint/undo）、G20 raster_authoring 的服务/路由/工具架构模式、Pi 工具注册与 dispatch 链。共享文件均为加性改动：thematic_spec（manual builder、类别 key_type 类型化 + 显式 other、常数字段断点去重归一 [v,v]+单类哨兵 stop——修复常数字段被 MapSpec 校验 NON_INCREASING_STOPS 拒绝的既有缺口）、mapspec_to_svg（match 类型敏感比较与 MapLibre 严格类型对齐、interpolate output_transform=√）、compiler.ts + ts_projection 同步、thematic-paint.ts 镜像、categorical 图例 other 条目、i18n 双语。

不做（他人领域）：栅格样式(G20)、纸面排版(G34)、时间媒体(G35)、OD(G21)、字段派生(G19)、标注(G16)、尺度策略(G27)、新符号资产。

## 实际验证（全部本地真实执行）

| 面 | 结果 |
|---|---|
| 后端单测 tests/unit/thematic_authoring/ | **77 passed + 2 skipped**（perf 按 #664 隔离跑 2 passed：100k 扫描 <20s/内存 <256MB/缓存命中/revision 失效） |
| 一致性 oracle（验收 22） | 只改 legend 或只改 paint 的注入分叉被 `integrity.assert_expression_consistent` 检出；正常提交零问题 |
| 边界孪生 | assignClass/sizeForValue Python↔TS 共享 fixture（90+18 例）双端断言 |
| SVG 导出语义 | 边界 fixture 同值同色（恰等断点/左右邻/越界/null→nodata）、类型化类别（数值 1 不命中文本 "1"）、radius 面积精确/width 线性 |
| 真实 Pi 旅程（PI_JOURNEY_E2E=1） | 真子进程 + 生产 uvicorn app + 生产 dispatch：inspect → tool_search 激活 → manual apply(断点 [0,10,20,50]+证据 decided_by=agent) → 图例回读 → revert_auto，7 门全过 |
| 共享合同回归 | jenks/legend_v2/converter/mapspec_to_svg/raster_authoring/cartography **153+1skip** 无回归 |
| 前端 | vitest 13 passed + tsc --noEmit 干净 + 受影响回归（compiler/paint-bridge/adapter 等）通过 |
| Lint | ruff 触及文件全绿 |

22 项验收场景逐项映射见 `planning/G33-THEMATIC-AUTHORING/TEST_MATRIX.md`（21 项 passed；场景 22 由 oracle 测试族覆盖）。

## 独立评审 → 修复 → 复审

- Round 1（未参与实现的独立 reviewer，真实链路复现）：**NO-GO**——3×P1（G15 共享识别用错契约形状永不触发 / size 换通道留幽灵键且 revert 清不掉 / bool 类别键 live 镜像词表失配全落 default）+2×P2+7×P3。
- 修复 commit 63161bb7c 逐条对应；P3-2（扫描 O(distinct) 内存）按评审论证维持已知边界（要素本就整体水合，≤250k floats 瞬时 ~8MB）。
- Round 2（新独立复审者携 Round-1 报告，独立复现脚本不 import 作者测试助手）：**GO**——P1×3/P2×2 全部真实链路闭环（含作者测试未覆盖的分支），复跑数字逐位一致；新发现 4×P3（不阻塞）已在 dd37de249 落地（revert 快照 binding 字段一致性、fixture 过权威 schema 校验、去重回执口径、三条测试锚）。

## 与最新 master / 相关 PR 兼容

- base = origin/master `ca7b3ec27`（PR 时 fetch 复核无新提交）；开放 PR 仅 #2094（G30 技能作业，无共享文件冲突）。
- 工具名/路由/目录/组件/i18n 键全部带 thematic 前缀，无编号冲突；legend_spec 改动为加性（key_type/default/哨兵 stop 缺省行为逐字节不变，由 153 项共享合同回归锁定）。
- mapspec_to_svg match 类型敏感化仅影响「数值 case + 数值属性」的新增路径，存量 spec 键均为字符串（回归确认）。

## 迁移 / 回退

- 无 DB/存储迁移；新键（thematic_authoring 证据块、key_type、default、output_transform）均为加性可选，旧读者忽略。
- 回退 = revert 本 PR；既有层不受影响（无本域证据块的层保持原行为）。

## 未测项与边界（如实披露）

- 前端面板的浏览器 E2E（Playwright）未运行（本任务验证层级：组件 vitest + 服务端全链 + Pi 真实子进程旅程）；浏览器层沿用与 G20 面板相同的挂载/提交通道。
- 纸面 PDF/PNG 通道的逐像素比对未做——本任务在出版链共用的 SVG 求值核心上做语义一致性断言（能力表测试同时防止向作者回执塞入未验证的导出承诺）。
- 大层（>25 万值）统计走确定性步进采样并披露 approximate；未做跨进程分布式统计。

## 交付物

- 分支 `zcode/g33-thematic-authoring-20261112T174914Z`（6 commits）
- worktree `/home/kevin/projects/webgis/webgis-worktrees/g33-thematic-authoring-20261112T174914Z`
- 账本 `planning/G33-THEMATIC-AUTHORING/`（BASELINE/DEDUP_MATRIX/PLAN/TEST_MATRIX/REVIEW/PR_HANDOFF）
