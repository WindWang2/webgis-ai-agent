# G32 LOOP_LEDGER — 执行账本

## 轮次记录

- R1(2026-11-12T17:46Z–): 基线复核(fetch 后 master=ca7b3ec27 与快照一致;open PR 仅 #2094/G30 无冲突)、双 Explore 研究报告、worktree 创建(pin b4afdddf init)、G14 适配(plan_seed/op 级 wfids/EditOpModel)→ 123 项 G14 回归 PASS。
- R2: feature_proposals 核心八模块 + schema + 14 REST 端点 + 4 Agent 工具;修循环导入(包 __init__ 不导 store)、`side_effect=none`→cacheable_read、`from __future__ annotations` 破坏 registry 模型生成(与 G14 工具同约束)。
- R3: 测试调通关键修复 — 未物化数据自然 id 解析(G24 WFID 链首后 identity.resolve_feature_ids 不再支持自然键 → candidate.resolve_ids_on_payload 双形态:index 映射 + natural_to_wfid_index);current 未物化预演锚=base 指纹;未物化 current 按自然 id 对齐(不按数组位置,P14);normalize 覆盖 split 单值 wfid。旅程 4 项 PASS。
- R4: 冲突矩阵 oracle 13 项 + 恢复/并发/权限 9 项;发现并修复真实缺口 — service.merge 未兜底非 typed 异常(→ _settle_failed_merge 统一恢复判定)、recovery_required 可被直接 merge 重试(→ merge 入口拒绝 + recovery/resolve 证据门端点)、ProposalRevisionMismatch 500(→ 继承 FeatureProposalConflict)。33 项 PASS;commit 1。
- R5: 前端全套(API client/store/drawer/入口/i18n×2)+ vitest 4 项;修 JS 切片语法笔误、store 单例重置、eslint exhaustive-deps;commit 2。
- R6: vendor Pi 构建(heavy 锁);Pi 旅程 driver — 修 Redis 挂起(USE_REDIS=false)、stub 懒读脚本(免重启换端口)、session_id 参数顺序(G14 惯例:末位带默认)、工具中文描述(BM25 可发现)、os._exit(stdout 管道挂住);**Pi 全链 4 项 PASS**(tool_search 发现→真实 dispatch→REST 审阅→merge→数据核对)。双实例 CAS 测试(P17b)补齐,33+1 项 PASS。
- R7: 文档(INTERFACES/TEST_MATRIX/LOOP_LEDGER)→ 本文件;commit 3 → 独立 review。

## 已验证行为(证据索引)

见 TEST_MATRIX.md(33 单元 + 4 前端 + 4 Pi + 123 G14 回归)。

## 决定与理由

- D1 提案真相=per-session JSON 文件(与 review/ADR-0203 同模式;不建 DB 表/不进 map_state)— 跨重启+有 owner+原子写。
- D2 base 快照=BlobStore CAS 全量 canonical(≤8MB 拒绝);引用计数释放。
- D3 冲突比较=快照 vs 实时载荷直比(不依赖 journal);未物化 current 按自然 id 对齐 + base 指纹预演锚。
- D4 提交桥=build_plan(plan_seed=op_seed)+executor.commit_plan(原 CAS/journal/盖章全链);幂等键 fpr:{pid}:{rev}:{digest12}。
- D5 recovery_required 只能经 resolve-recovery 证据门离开(executed/not_executed/unprovable 三态)。
- D6 Agent 工具中文关键词描述(仓库 BM25 发现面惯例;G14/G26 同)。

## 未完成/后续

- 真实浏览器 E2E 未跑(组件级 vitest + Pi 全链为证;如实披露)。
- P21(MapSpec stamp 延迟)依赖 G14 既有 stamp_status 披露,未单独注入测试。
- G09/G21 历史分配未占用,与本域无关。

- R8: 独立 review request-changes(2×P0+3×P1+4×P2+10×P3,全部实证)→ 全部修复+10 项回归(commit 4)→ 复审 **approve**(reviewer 探针复核全部真实修复;新报 2×P2+6×P3)→ R2-1/R2-2/R2-5 同批清偿+2 项回归(commit 5)。最终:单元+G14 168 passed、Pi 全链 4 passed、前端 4 passed、ruff/eslint/tsc 全绿。

## 下一步

push → 创建 PR(base ca7b3ec27;与 #2094/#2095 仅加性注册接缝,无冲突)。
