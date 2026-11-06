# RISK_REGISTER — GOAL 05

| # | 风险 | 等级 | 缓解 | 残余 |
|---|---|---|---|---|
| R1 | marker 检测误伤（权限/429 词面撞正常消息） | 中 | 词表窄化（"429"/"rate limit" 等强信号；中文仅限流/权限短语）；确定性语料钉死映射；误分类最坏影响=处置变保守（问用户而非自动重试） | 低 |
| R2 | last_recovery 每失败额外一次 envelope save | 低 | 仅裁决变化时写（重复同形状失败幂等跳过）；失败频率低 | 低 |
| R3 | [GIS Recovery] 投影行增加 turn 上下文长度 | 低 | 单行 ≤240 + 指令行 ≤240；仅存在失败行时追加第二行；全绿计划零新行 | 低 |
| R4 | goal_verification 与 ADR-0183 判定不一致被误读为第二 oracle | 中 | 对账层只读不重算；contradiction 是披露非改判；ADR-0279 D4 明文红线 + 文档 | 低 |
| R5 | 处置六值与既有 SAFETY_CLASSES 语义混淆 | 低 | ADR-0279 D1 三层正交定义；词表各自封闭互不映射 | 低 |
| R6 | 粗分类器 marker 增强（429/5xx→timeout）改变既有预算行为 | 中 | 全仓无 429/5xx 消息的既有期望（failure_corpus/taxonomy 测试 grep 核对）；语义本就该如此（类文档"远端不可达"） | 低 |
| R7 | Pi 子进程路径未起真实进程验证 | 中 | 双引擎收敛点=ToolDispatchService（模块 docstring 既有 rendezvous）；Pi 消费同一 llm_payload/raw_result；列为未验证项 | 中（如实披露） |
| R8 | open PR #1876/#1877 后续合并造成冲突 | 低 | 已核对 changed files 无交集；本 PR 不触碰 temporal/spatial-semantics 契约 | 低 |
| R9 | 基线存量红（4+1）被误归因本 PR | 低 | git stash 双向复现核对并记录于 EVIDENCE | 低 |
| R10 | 恢复语料 oracle 与生产 seam 漂移 | 中 | 三 oracle 族全部 import 生产纯函数（wiring/diagnose 同一入口）；E2E 门走真实 dispatch seam 兜底 | 低 |
