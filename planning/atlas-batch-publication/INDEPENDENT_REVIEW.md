# INDEPENDENT_REVIEW — 独立评审记录

评审员为未参与实现的独立 subagent 上下文；对象是 `git diff origin/master` 全量。

## R1（2026-10-30，评审员 agent_72982482）

结论：**阻断 PR**（2×P1 未清偿）。发现 2×P1 + 4×P2 + 5×P3：

| # | 级别 | 发现 | 清偿（commit 44ecd8a9e） |
|---|---|---|---|
| 1 | P1 | 页指纹不完整（非目标层数据/样式/阈值/视角变化不轮换 → 缓存误命中交付旧数据 + manifest 失真） | data_digest = 页渲染输入全部内联源内容（目标层页粒度+其余源全敏感）+ 并入结构指纹投影；回归测试 ×2 |
| 2 | P1 | export_atlas 显式 session_id 绕过属主校验（跨租户导出） | 一律经 `_resolve_session_id`（runtime 上下文校验）；verified+他人 session → session_forbidden 负例 |
| 3 | P2 | 匿名提交产物无人可下载（sidecar "anonymous" 恒 403） | REST 匿名 401 typed；工具无 origin owner typed 拒绝；executor 无 creator_id fail-closed 披露 |
| 4 | P2 | `_svg_of` 组装期裸读共享缓存 → 逐出竞态 FileNotFoundError | 页体留内存（96MB 同口径预算），不回读缓存 |
| 5 | P2 | dry-run 未水合 ref: 源（误拒/页数失真） | 提交面与执行面同口径先 hydrate |
| 6 | P2 | `_policy_from_request` 类型强转 500 / bool("false")=True | 严格转换 + typed 400（"abc"/伪布尔/未知键三类负例） |
| 7 | P3 | pypdf 缺席时页数复核 fallback 伪装通过 | fallback=-1 → 显式 `pdf_page_count_unverified` 披露 |
| 8 | P3 | manifest 页级 degradations 恒空 | 填充本页 warning+ 诊断（≤8） |
| 9 | P3 | status.json 逐页落盘无消费方且必删 | 移除；落库证据统一 `_evidence_view` |
| 10 | P3 | 前端 manifest 文件名 stem 推导脆弱 | 保留（后端命名契约为固定格式且测试锁定），评审员确认"当前正确" |
| 11 | P3 | policy 白名单外键静默丢弃 | 未知键 typed 400（与 #6 合并清偿） |

评审员同时确认：接缝等价性逐行对照成立（预算数学/诊断码/顺序/封面预算记账/url_fetcher）；测试诚实性总体良好（mock 边界显式声明）。

清偿验证：atlas_batch 46 用例绿；出版族+jobs+API 契约+端点矩阵 304 绿；RISKS R10 与实现对齐。

## R2（2026-10-30，评审员 agent_68324f76，终审）

结论：**阻断（收窄为 1×P2）**。R1 全部清偿逐条复核 ✓（P1×2 真实成立），新发现：

| # | 级别 | 发现 | 清偿 |
|---|---|---|---|
| A | P2 | `hydrate_ref_sources_sync` 在 async 路由内 → run_sync 于运行 loop 抛 RuntimeError → ref 源 REST 提交 500（评审员本地复现） | 提取 `hydrate_ref_sources` **async 本体**（sync 版包装）；路由 await；executor 经 `_run_async_blocking` 桥；ref 源正/负例测试（负例 typed 400 绝不 500，正例 dry-run 3 页） |
| B | P3 | 策略转换 float 截断/数字串/`0 or 默认` 吞噬 | `_strict_int`：仅真 int（bool/float/串一律 400；键缺席才用默认） |
| C | P3 | pypdf 缺席时 lineage `pages=-1` 哨兵入档 | lineage 记 0（契约缺省=未提供）；result 内部保持 -1 诚实 |
| D | P3 | views `_atlas_page_status` 对畸形 page_number 可抛 → 列表 500 | try/except 跳过该行（docstring「形状异常→None」语义兑现） |
| E | P3 | planning 文档漂移（compiler_key/指纹清单/status.json/错误码/manifest 字段/TEST_MATRIX 文件名） | INTERFACE_CONTRACTS/TEST_MATRIX 逐项对齐实现 |
| F | P3 | planner 逐页全源 digest 的 O(页数×载荷) 开销 | RISKS R14.1 记录（正确性优先取舍） |

评审员终审面确认：属主链完整（401/404 fail-closed/owner sidecar）、无路径注入、无新增 SSRF、内存峰值受 96MB 累积预算硬约束、qa_notes 作用域无缺陷、401 先于归属校验属有意收紧、测试诚实性良好。

R2 清偿验证：atlas_batch 47 用例绿；出版族+jobs+API 契约+端点矩阵 351 绿；ruff 全仓绿；前端自 commit 4f93ae707 后零 diff（全量 493 文件/4667 用例绿结论仍有效）。

## R2 后状态

- 无未清偿 P0/P1/P2；P3 全部清偿或记录（F 为有意取舍留档）。
- PR 放行条件满足。
