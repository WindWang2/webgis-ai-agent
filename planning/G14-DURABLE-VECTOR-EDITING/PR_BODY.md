# feat(g14): 真实矢量数据编辑 —— 增/删/顶点/分割/合并事务 · 稳定身份与血缘 · 草图升格 · 拓扑闸 · Pi/UI 双入口

**任务号 G14-DURABLE-VECTOR-EDITING**。Base `816705218`（创建时最新 master，PR 前复核未移动）。扩展既有 feature_edit 事务栈（#1873/ADR-0277），不建第二套写路径。

## 用户获得什么

- **画出来的东西不再是临时草图**：手绘服务区可「保存为数据集」→ 真实 ref/descriptor/图层/血缘登记，刷新仍在，Pi 与数据工具立即可分析，并可继续编辑。
- **真实图层的完整编辑闭环**：选目标层后，点/线/面创建、删除、顶点拖拽、两阶段分割、按选中合并 —— 每个手势 = 一次服务端事务（preview → owner approve → CAS → journal → MapSpec 盖章 → 增量刷新），可撤销/重做，Escape 取消零写入。
- **Pi 同一事务面**：「把这两个面合并」「新增一所学校」「沿这条线分开」→ 结构性操作恒需用户在提案卡确认（即使 allow_safe_attribute）；新增 `vector_dataset_provision` / `vector_feature_lineage` 工具。
- **分析/制图与编辑闭环**：编辑提交 → 依赖分析产物显式 stale（ref_overwritten）→ 重算绑定新 revision → MapSpec 源盖章/导出取新数据。
- **诚实边界**：声明制拓扑规则（未声明零规则）、只读源派生编辑副本（原源零写入）、journal 单条字节闸、血缘投影给下游显式失效（绝不按行号猜对象）。

## 实现要点

- `STRUCTURAL_OPS`（create_features/delete_features/replace_geometry/split_feature/merge_features）进封闭词表；`FeatureDelta.presence`（created/removed + 完整快照 + 原位 index）向后兼容旧 journal。
- 新生身份 `wf1-<sha256(plan_id:op:child)>` 决定性派生 —— preview/commit/幂等重放/redo 同身份；removed 带 index 原位重插恢复精确列表序（链指纹断言依赖）。
- `datasets.py` provisioning：空 typed 图层（envelope `geometry_type`+`topology` 声明）、草图升格（升格即物化稳定身份）、只读派生副本；全部走既有 ingest 管线（artifact ledger 注册 → catalog/Pi 可见）。
- `topology.py`：no_overlap（STRtree 邻域 + Geod 米制容差换算）/no_self_intersection/no_duplicate_points，预览与提交双侧复核（提交期在 overwrite 之前）。
- 前端：`layer-edit-bridge`（一手势恰两次 REST；mousemove 零 REST；回执驱动刷新/撤销栈/退役身份 selection 剪除）、`feature-layer-editor` 手势面、`feature-edit-toolbar` 目标选择、sketch 升格按钮、`_wfid` 置顶三条 FE id 链（与后端 ADR-0277「物化后一律 WFID 寻址」对齐）。

## 已执行验证（本地真实执行）

- 后端：`tests/unit/feature_edit/` **120 passed**（新增 structural 25 + transactions 24 + flagship 2 + g14 tools 6；既有 66 回归含 SW-02 selected_ids 契约陈旧断言修正——该测试在干净 master 即红，生产代码注释已引用 #2004 仲裁）；`tests/unit/gis_harness/interaction_ir/` 126 passed。
- 前端：vitest **5042 passed / 2 既有失败文件**（`test/pdf-dependency-security.test.ts` 15 例 jsdom、`tests/e2e-lanes/changed-lanes.test.ts` j13 漏登记 —— 均在纯净 origin/master 临时 worktree 复现确认，与本分支无关）；`eslint . --max-warnings 0` 全绿；`tsc --noEmit`（含 test tsconfig）全绿。
- 性能（单 worker，内存存储）：200 目标 set_fields preview 7.2ms/commit 274ms；50×64 顶点 create 21/81ms（journal 单条 92KB，超 64KB 滚动预算触发 FIFO 丢弃并披露 dropped）；undo/redo 链 22-80ms；50×1024 顶点超预算拒绝 174ms（entry_too_large 诚实拒绝）。
- V01–V20 映射与证据见 `planning/G14-DURABLE-VECTOR-EDITING/TEST_MATRIX.md`（V04/V05 前端面在 bridge 测试断言 fetch 计数）。

## 独立评审

未参与实现的独立 reviewer 全量评审（R1：无 P0，1×P1 + 8×P2 + P3）→ 全部修复于 c0dc82393 → 同一 reviewer 复审（结论见 PR 评论/PR_HANDOFF.md）。关键 P1：乱序选择父要素合并会让 undo 链指纹断言 409（合并永久不可撤销）—— 已修复并有回归。

## 与其他 PR 的关系

- 消费不修改：Pi pin/bridge（G12）、能力资格（#2073/G13）、数据刷新（#2022/G10）、MapSpec 提交撤销（#2035）、fabric/分析算法。
- 共享文件接触均为最小加法：`map-toolbar-hud.tsx` +7 行（挂独立组件）、`map-panel.tsx` +4 行、`edit-proposal-card.tsx` +5 个标签键、`agent-run-panel.tsx` +1 行；与 #2032（UI 重设计）潜在冲突段即上述两处挂载点。
- #2074（G19 字段计算）是互补的派生属性工作台（新命名空间），无文件重叠。

## 迁移/回退

- 纯加法：旧 journal（无 presence 字段）按 update 语义回放不变；旧五操作词表不变；未物化数据集首编辑物化路径不变。envelope 新键（geometry_type/topology/read_only/derived_from/origin_kind）对旧读取方透明。
- 回退：revert 本 PR 即回到 #1873 行为；由本 PR 创建的数据集（含升格草图）仍是普通 session ref，可继续被读取/分析。

## 未测限制（如实）

- MVT（>5000 要素）层无内联要素 → 地图手势无法拾取要素（服务端操作/REST 仍可用）；待 MVT 侧要素拾取通道。
- no_overlap 容差 m²→deg² 换算用经度尺度近似（高纬偏松，finding detail 披露）。
- api_contract 字段快照测试在干净 master 即红（/health、/export/vector-pdf 漂移来自已合并的 #2036/#2015 未刷新快照）—— 本分支未新增漂移（新端点返回 Dict[str,Any]）；未代为刷新他人快照。
- 浏览器级 E2E（Playwright）未新增 journey（mock 模式 stub 面较大，留待 #2032 合并后统一补）；前端行为由组件/桥接单测 + 类型门覆盖。
