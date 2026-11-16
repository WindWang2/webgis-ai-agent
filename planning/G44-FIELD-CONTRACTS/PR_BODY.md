# feat(g44): 业务字段契约与结构化要素录入 —— 声明/完整评估/激活门/全写入口统一执行/Pi 同链

## 用户问题

「数据里目前出现过哪些值」（观测画像）与「owner 允许录入哪些值」（业务域）此前
混为一谈：观测词表会把声明合法的新值（如从未出现过的 `retired`）误拒，脏值
（`unknown`）也只因「出现过」就获准；结构化录入只有裸 JSON 输入，业务规则
（必填/区间/组合唯一/条件必填）没有声明、检查、激活与追溯的载体。

## 行为增量（用户可见）

1. **声明合同**：owner 在工作台对可编辑矢量数据集声明字段规则（严格类型/
   必填/可空/空串政策/枚举≤256/数值闭开区间/长度/ISO 日期/带 offset 的
   datetime/条件必填/同类型比较/1–4 字段组合唯一 + null 政策），版本化不可变
   行 + canonical digest + revision CAS。
2. **完整评估**：durable job 对冻结数据版本逐行 + 受控哈希唯一性检查；总数/
   检查数/违反数/完整性/溢出计数入收据；违规分页（wfid 或 source-row 定位）；
   取消/崩溃 → incomplete，**永不产生可激活证明**。
3. **激活门**：仅「completed + 零违反 + 规则 digest 一致 + 编辑锁内数据指纹
   复核通过」可激活（数据变了必须重评估）；每绑定至多一个 active（DB 部分唯一
   索引兜底）；退役是 owner 显式动作。
4. **全部写入口统一执行**：REST 预览/提交、Pi 工具、G32 提案合并、undo/redo、
   create/split/merge 继承属性 —— 同一中央 validator 在候选态执法；预览后合同
   变化 → 旧 plan 409 `contract_changed`；rename 声明字段 typed 阻断（重绑走
   合同修订）；undo 恢复态违反现行合同 → typed 阻断且不改写历史。**无合同数据
   集行为与旧版完全一致**；声明字段允许写入枚举中的未观测新值，不再被
   observed vocabulary 误拒。
5. **结构化录入**：地图创建/属性面挂合同驱动表单（枚举不默认首项、数值中间态
   不转 0、null 与缺失显式区分、offset/精度客户端校验 + 服务端同一裁决）；raw
   JSON 保留为高级入口，同样过中央校验。
6. **Pi 同链**：`webgis_field_contract_{inspect,draft,assess,result}`（tier2，
   有界回执）；激活/退役/放宽策略**无工具面**；要素编辑走既有
   `vector_feature_edit_preview/commit`（同一 validator，模型读到的约束与提交
   使用的 revision 对齐）。

## 复用与范围

- 唯一写路径仍为 G14 `executor.commit_plan`（锁/CAS/journal/盖章）—— 合同校验
  是它的新增读集，不是第二写链；G32 桥/undo-redo 原样接线。
- durable job 复用 G23 runner；选择句柄复用 G24；观测画像复用 attr_ops（语义
  未动，作为对照证据呈现）。
- 新 DB 表 2 张（migration `g44_field_contracts`，down=g40 head）+ 新 JobKind
  成员 + 3 个封闭 finding codes + 9 条 REST 路由 + 4 个 Pi 工具。
- 共享 infra 两处加性修复：sqlite lane WAL/busy_timeout（E2E 可靠性，Postgres
  路径零改动）；数据源子页签行 `overflow-x-auto`（330px 侧栏下工作台入口被
  裁剪的真实可用性缺陷）。

## 实际验证（全部本地真跑，详见 planning/G44-FIELD-CONTRACTS/TEST_MATRIX.md）

- 验证内核 oracle（人工真值表，F04–F10/F37）：27 passed。
- 执行链（真实 store+sqlite+G14 写链；F01/02/03/11/12/13/15/17/18/19/21/22/23/
  29/31）：15 passed；G14/G32 回归 168 passed（零回归）。
- REST 生命周期 + F26：4 passed；迁移升/降级 + active 部分唯一索引实证；openapi
  快照刷新后 12 passed；生成物 staleness up-to-date；触及文件 ruff/eslint/tsc
  干净。
- **Pi 旅程**（真实子进程：stub LLM→uvicorn→vendored Pi RPC→chat/stream→
  tool_search→dispatch→服务）：**13/13 gates**，合法编辑以数据/journal 回读裁决
  （origin=agent），非法编辑服务端阻断且数据不变，草拟不改 active。
- **浏览器旅程 j24**（real 档，真实后端+Pi+浏览器）：**1 passed** —— 种子→完整
  评估→激活门→绑定权威断言→reload 后仍 ok→owner API 非法值 committable=false。
- 前端：API/hook/表单/面板组件测试 66 绿；i18n zh/en 同步。
- 分层披露：真实浏览器内「编辑器选择器→表单」腿因冷 dev server 图层水合时序
  不稳定未在 j24 断言（组件级全绿 + 壳层时序证据记录于 TEST_MATRIX）；
  F01–F37 逐项结果见 `planning/G44-FIELD-CONTRACTS/F_SCENARIOS.md`。
- 既有失败（pristine base 同条件复现，非本分支引入）：alembic percent-URL 门、
  4 文件裸 CJK、topology.py F841、cartography-result-card tsc（#2123 域）。

## 迁移 / 回退

`alembic upgrade head` 落 `g44_field_contracts`（两张 additive 新表 + 索引，
`_table_exists`/`_index_exists` 幂等守卫；downgrade 反序删表）。无既有表改动；
无合同数据集零行为变化；回退 = downgrade 一步。

## 独立评审

未参与实现的 reviewer 对全量 diff 评审 → 修复 → 复审；记录见
`planning/G44-FIELD-CONTRACTS/REVIEW.md`。

## 已知边界

- 派生数据（join/字段计算副本）不自动继承合同（绑定按 (session, ref) 身份；
  工作台观测×声明分列披露来源）。
- 只读/外部源不可绑定合同（typed `dataset_not_eligible`，指向派生编辑副本）。
- 跨数据集外键、任意表达式求值、量纲换算、隐藏 trim/casefold：明确不做
  （typed unsupported）。
- 不等线上 CI（按仓库约定）；未改动任何测试/审批配置。
