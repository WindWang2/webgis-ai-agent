# PR_HANDOFF — G46-LINKED-EXPLORATION

- base: 655f769ca17f17c5797da13b4c299b8fa600e370（origin/master；PR 前复核无新合并）
- branch: zcode/g46-linked-exploration-20261115T154748Z
- head: 见 git log（R1 修复 + ref_cursor/schema 兼容修复后）

## 一句话
围绕单一授权矢量源的多图联合筛选与持久探索视图：分类图+区间图+地图+结果表
按同一组合条件、同一源版本联动；命名视图保存/重开（源锚校验）；Pi 与 UI 同一
服务面；SVG/PNG/PDF 冻结导出真实反映过滤范围。

## 行为增量（用户可感知）
1. 两图一地图联动：图 A 选类别 + 图 B 选区间 → 地图 live 过滤/匹配计数/结果表
   同步（typed identity：1/"1"/true 互不混同；条件间 AND、同控件内 OR）
2. 条件 chips：删单个/清单图/清全部三种范围；关闭面板不清条件；地图点击不替换
   作者条件
3. facet 口径：图内统计=排除自身条件的"其它条件下可选项"；匹配总数=全条件口径
   （两套分标，不冒用同一"总数"标签）
4. 命名视图：保存时冻结核对锚；重开同代重放；源推进 → stale 明确要求重预览；
   并发修改 → CAS 冲突不盲覆盖
5. Pi 工具 ×7：inspect/preview/view_save/views(list|open)/view_update/apply/export
6. 导出三格式：组合条件编译进图层 filter（与 live 同一表达式）+ facet 图表统计
   印入出版面 + 口径注记；冻结期源变化 typed 拒绝

## 复用与范围
- 复用：feature_pages typed where（不建第二查询语言）、G41 freeze_source（同代
  冻结）、G24 where-scope handle（完整集合）、MapSpec 组件通道、print-layout
  编码器（SVG/PNG/PDF）、chart_panel 目录/渲染协议
- 不做：跨源关联（G38）、字段合同（G44）、坐标（G45）、cartogram（G47）、配准
  （G48）；不修全仓 CI 基线（issue #2123）

## 验证（全部真实执行）
- 后端单元 54（独立 oracle 手算；含 evaluate_where↔mapspec_filter↔MapLibre 三处
  语义对齐差分测试）
- 集成 24：typed/E02-E14 oracle；CAS/stale/越权 E19-E27；apply CAS 链（P1-1
  回归）；生产形态图层导出（P1-3 回归）；三格式真实编码回读（PNG 魔数/尺寸、
  PDF 魔数、三格式同计划 digest/计数一致；统一重阶段锁内）
- 迁移：空库 alembic upgrade head 实测（exploration_views 落地；head 唯一）
- 前端：22 lib/组件 + 既有相关面 465 项回归全绿；eslint/tsc 净（本域零告警）
- Pi journey：真实 Pi 子进程 + 扩展 + dispatch（PI_JOURNEY_E2E=1；结果见
  TEST_MATRIX 更新）

## 独立评审
- Round 1：3×P1 + 2×P2 + P3 清单（实证复现式）→ 全部 P1/P2 + 关键 P3 修复 +
  5 项回归锚（REVIEW.md）
- Round 2：原 reviewer 复审记录见 REVIEW.md 追加

## 已知限制 / 未测（如实）
- 浏览器 e2e（Playwright journey）未运行——逻辑面由组件测/竞态世代测覆盖
- HTTP 路由对同会话源恒走 trusted_dispatch（require_owned_session 已挡越权；
  深度防御权衡记录于 REVIEW.md P3-2）
- 既有失败（干净 base 复现）：alembic % 编码 URL 用例、cartography-result-card
  tsc 2 错——均属 issue #2123 基线，非本域

## 迁移/回退
- 单张 additive 表 exploration_views（迁移 g46_linked_exploration_views ←
  g40_cartographic_symbol_assets；可 downgrade；ID 已在共享保留锁登记）
- 回退：downgrade 该迁移 + 移除组件注册行即可；无数据转换
