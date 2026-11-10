# feat(g15): 持久化多画幅/小倍数制图产品 — frame_product.v1 全链贯通

## 问题

`small_multiple_map` 长期 `runtime_status="planned"`：现有能力是请求级多帧导出
（ExportRequest.frames → canvas grid/pdf pages）与 atlas 逐帧分页 —— 都没有
**产品内多画幅语义**（画幅无稳定身份、无数据绑定、无共享表达契约、无逐画幅
编辑/联动/终验）。「三年并排、同一分级、联动查看、改第二幅、导出一张图版」
只能给临时拼图。

## 用户获得

- 「把成都各区 2020/2022/2024 指标并排做成三幅图」→ **持久化多画幅产品**：
  画幅身份稳定（frame_id；重排/隐藏/改名不换身份）、数据绑定带 revision、
  保存后刷新可恢复，可继续编辑。
- **共享分级是科学契约**：breaks 在跨画幅真实数据并集上只算一次；计数 vs
  归一化率拒绝共享色带（typed 拒绝 + 回落独立图例 + 披露）；domain_status
  complete/partial/unknown 如实；同值同色同级。
- **Map Studio「画幅」子页签**：三幅恢复/改名/排序/激活/同步组 A-B/图例策略，
  全部经 set_frame_product + expected_revision CAS（409 superseded 静默收敛）；
  frames 整表 patch 原样携带 data 绑定（schema 契约拒绝丢绑定的 patch）。
- **同页多画幅出版**：`atlas.driver=product` 单页 2×2/1×3 图版，逐帧
  layerOverrides/时间窗/共享图例真实生效（PDF 文本层 + 栅格像素级核验）；
  SVG id/clipPath 槽级命名空间不碰撞。
- **Pi 工具面** frame_product_{plan,bind,update,status,unbind}：「只改第二幅
  标题」经键级 patch 精确落位；refs-only 摘要携带激活画幅/同步组/共享分级。
- **逐画幅终验**：frame_id+revision 对账，live/export/snapshot/not_observed
  分级（快照不冒充实时），空帧显式披露，单帧成功不汇总为产品完成；
  goal_satisfaction 逐画幅证据（≤12 行）。

## 实现边界（诚实声明）

- WeasyPrint 内嵌 SVG 渲染器不支持 g transform/多嵌套 svg（实证探针）——
  产品页 PDF 走 HTML 组合（CSS 定位槽位）；SVG 单文件面保留组合 SVG。
- 前端 canvas PNG/SVG 网格不承载 layerOverrides/纸型/共享图例带 ——
  适配器返回 limitations 披露；精确语义走后端产品页（PDF/vector）。
- `small_multiple_map` → native 仅 polygon/point 实证组合；raster 未实证，
  诚实留在 pitfalls（V5 导出 frames 承接降级）。
- 相机同步组 = 契约 + 纯函数（resolveSyncPair N 方推广）；多画幅交互视窗
  运行时（快照 LRU/按需激活、主图与激活画幅联动传播）**未接线** ——
  本 PR 不冒充该运行时已交付（诚实边界）。
- 客户端 canvas 导出（PNG）对产品画幅真实承载 where 过滤/范围/标题，
  并以 `layer_filter_unsupported` 诊断披露不可承载语义；PDF/vector 自动
  路由 `atlas.driver=product`（后端精确语义）。

## 关键文件

- 契约：`app/lib/cartography/mapspec_schema.py`（v2.0 additive）、
  `docs/adr/0288-frame-product-contract-and-per-frame-verification.md`
- 服务：`app/services/gis_harness/frame_products/`（resolver/shared_expression/
  service/verification）
- 出版：`app/lib/cartography/publication_ir.py`（frame_slots + driver=product）、
  `app/services/publication_export.py`（产品页编译/HTML 组合）
- 工具：`app/tools/frame_product_tools.py`；前端：`frontend/lib/cartography/
  frame-product.ts`、`frame-sync.ts`、`components/workbench/frame-product-panel.tsx`

## 已执行命令与结果（本地，无线上 CI 依赖）

- `pytest tests/cartography/ tests/unit/gis_harness/ tests/unit/test_mapspec_lifecycle_engine.py
  tests/unit/test_mapspec_store.py tests/test_export_frames_v5.py tests/unit/test_mapspec_to_svg.py
  tests/unit/test_model_library.py` → **6946 passed / 0 failed**（4:55）
- `pytest tests/unit/gis_harness/frame_products/` → 43（resolver/shared/service/
  tools/verification）
- 真实导出工件：WeasyPrint PDF（文本层断言）+ pdftoppm 栅格（四画幅区像素
  着墨断言）+ SVG 结构（4 clipPath 唯一 + 标题/图例标签）
- 基准（真实耗时，测量环境 = 本机 pnpm/uv 环境）：2/4/6 幅 bind 42-63ms /
  resolve 1-2ms / page compile 3-7ms / PDF 102-167ms；SVG 字节随画幅线性
- 前端：`vitest run`（新增 lib/panel/spec-frames/comparison/studio/i18n 组）
  → 166+ passed；`tsc --noEmit` → 0 错误
- 浏览器旅程：`playwright test e2e/journeys/j14-frame-product.journey.ts`
  → 1 passed（mock 档，真实 app shell：SSE 提交 → Studio 画幅面板三幅恢复 →
  改名 CAS → 激活）
- 视觉验收（visual-judge，2×2 图版）：首轮 FAIL（空白帧/标题重叠/图例失配）
  → 修复（viewBox 保留 + HTML 组合 + paint 重投影 + 图例透明度对齐 + fixture
  跨级）→ 复审结构全过；残余两项（近空白帧为 fixture 全同级所致、右缘裁切为
  fixture extent 不含最东区）已随 fixture 修正并像素复验。

## 独立评审

- **R1**（reviewer 未参与实现；全量 diff + 现场复跑 + 3 项对抗复现）：
  APPROVE-WITH-FIXES —— 3 blocker（面板 patch 丢 data 绑定 / PDF 图例带
  读局部分级 / 导出路由与同步组为无消费者死码）+ 2 P2 + 6 P3，全部在
  commit f8dfe6bad 修复（细节 `planning/G15-MULTIFRAME-CARTOGRAPHY/REVIEW.md`）。
- **R2 复审**：APPROVE-WITH-FIXES —— 发现 R1 修复引入的 CAS-superseded
  假成功（P0）+ atlas 门（P2）+ 2 P3，commit db8d79357 修复（superseded
  typed 拒绝、链式 CAS 基线、门提升、测试判别力、词表早校验 + 3 个
  CAS 语义测试）。
- **R3 终审**：**APPROVE** —— reviewer 独立执行验证（含交错写入者竞态、
  atlas 门 5 场景）；遗留 2 项 P3 非阻塞（其一已顺手修复：显式
  category/feature 选择不再被产品静默覆写）。

## 与其他 PR 的关系

- 消费不复制：#1975 出版链、GOAL03 temporal（时间桶/过滤）、#2011 编译器、
  #2034 Studio、#2035 undo（SetFrameProductIntent 走同一 mutation 事务面，
  自动进入 Agent 撤销栈）、#2036 Pi-only（工具走 ToolRegistry，未触碰 bridge）。
- 边界：#2021 简报、#2022 刷新、#2032 UI 重设计（仅新增子页签 + 独立子组件，
  冲突段已在 INTERFACES.md 披露）、#2073 能力生态（能力包按既有域模块模式登记）。

## 迁移/回退

- MapSpec 1.9 → 2.0 纯加法（identity upgrader）；存量 spec 行为与指纹不变
  （IR goldens 仅增 `frame_slots: null`）。回退 = revert 本 PR（无数据迁移）。

## 未测限制

- real 档 Pi 旅程（需 nightly LLM stub 短语产出 frame_product_bind —— 工具面
  已就绪，j14 real 档留占位）；六幅以上交互布局；栅格多画幅。
- 基线既有失败（非本 PR）：`test_adr_numbering_contract` 0286/0287 撞号在
  BASE_SHA 816705218 复现。
