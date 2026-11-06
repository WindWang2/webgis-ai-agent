# INDEPENDENT_REVIEW — 独立评审记录

评审员：独立 subagent（全新上下文，未参与任何实现；具备实证复现能力）。
两轮评审均针对 `git diff origin/master..HEAD` 全量差异。

## R1（2026-10-29 深夜）—— 结论 FIX-FIRST

计数：P0×0 / P1×3 / P2×4 / P3×5。核实过的面（原话结论）：鉴权 8 端点
全过 authorize_session_write、404 语义一致、样本有界无搬运面、closed union
完备、原件不可变成立、descriptor 写回走版本化、快照一致、195 用例全过、
抽查 3 关键测试真锁定。

| # | 级 | 发现 | 清偿（commit ada8e26f3） |
|---|---|---|---|
| 1 | P1 | 幂等键空指纹跨数据集碰撞，B 拿 A 的收据（复现） | find_receipt 加 input_ref 校验 + 收据内容锚 input_content_hash 双锚；回归 ×2 |
| 2 | P1 | auto_execute 无视澄清项执行并谎报 prepared（复现） | 澄清项一律 requires_input；回归 test_auto_execute_respects_clarifications |
| 3 | P1 | 前端闸与后端策略冲突（impossible 在 UI 死路）+ 测试假 severity 掩盖 | hardBlocked 镜像 _AUTO_RESOLVABLE + mock 改真实形状双向锁定 |
| 4 | P2 | STALE_INPUT 两类绕过（空指纹跳过 / 覆写不重铸） | 内容锚（并入 #1）；首次执行盲区残留 → R2-2 P3 |
| 5 | P2 | executed 经 preview 状态回退后可被作废（复现） | preview 对终态 409；并发窗口残留 → R2-3 P3（后修） |
| 6 | P2 | 并发 execute 双 prepared ref（复现） | 每 (session,plan_hash) asyncio 锁；gather 回归 |
| 7 | P2 | receipt.crs_source 永不出现 assumed | payload.crs_source 透传 + reproject 闸尊重透传 |
| 8 | P3 | 单字母词尾误判（max→x） | 分词整词命中（utm_x→x 整词；max 不命中） |
| 9 | P3 | 元数据计划收据无步证据 | field_mapping 证据条目补齐 |
| 10 | P3 | 前端「无候选」选项是澄清陷阱 | __none__ 哨兵区分未选/显式选无 |
| 11 | P3 | llm_payload 断言弱析取 | 拆两条独立断言 |
| 12 | P3 | LOOP_STATE 过时 + R6 与实现不符 | 文档如实化 |

## R2（复审，同评审员）—— 结论 **APPROVE**

R1 全部 12 项 CLEARED（逐项 verdict：实质修复非表面修补；4 个原始复现
脚本全部不可复现；无一例删断言/改前提糊弄）。新发现 6 项全部 P3：

| # | 发现 | 处置 |
|---|---|---|
| R2-1 | 收据 ring 按 hash 跨 ref 互逐 | **已修**：去重键 (plan_hash, input_ref) |
| R2-2 | 内容锚只在收据侧，首次执行有替换盲区（前置链窄） | RISKS 残差（后续：计划携带构建时内容哈希） |
| R2-3 | preview/execute 并发写竞态可重开 #5 缺口（复现） | **已修**：preview 同 exec 锁 + 锁内重读状态 |
| R2-4 | _EXEC_LOCKS 无回收/锁绑事件循环 | RISKS 残差（量级可忽略；多 loop 嵌入场景注记） |
| R2-5 | 工具 docstring blocked/ready 口径不含 _AUTO_RESOLVABLE 裁量 | **已修**：docstring 补口径 |
| R2-6 | 幂等重入 O(n)×2 成本（deepcopy+canonical hash） | RISKS 残差（方向正确；后续计划携带锚前置短路） |

原话总结论："**APPROVE**。……清偿引入的 6 个新发现均为 P3（台账噪音/慢
泄漏/窄竞态/文档口径），无安全、数据损坏或谎报级别问题，不阻塞合并"。

## 退出判定

- 两轮 review-fix-review 闭环：R1 FIX-FIRST → 全清偿 → R2 APPROVE。
- 无未解决 P0/P1/P2；P3 中 3 项当场修复（R2-1/3/5），3 项记入 RISKS 残差
  披露（R2-2/4/6，均不使用户闭环失败）。
