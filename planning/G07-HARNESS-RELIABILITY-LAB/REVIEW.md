# 独立 Review 与修复记录

当前状态：独立冷审完成，P2 修复与复验进行中；尚无二次复审通过结论。

## 冷审输入与独立性

- Reviewer B（第三个 distinct subagent）冷读 `git diff origin/master...HEAD` 全 39 文件，未复用主实现通过结论，未修改文件、未派生代理、未访问公网/付费模型。
- PR type：additive 可靠性/可观测性工程。Scope Check 未发现第二 trace store/planner/executor；M0–M7 证据一致，M8/M9 分开。
- Reviewer 独立复跑：journey 24、selection/recovery 15、bench 7、manifest 1、真实 dispatch 30 场景、recovery gates 6；另有 flush=1 vs 16 独立 perf 抽查（1.29×，同向）。

## 冷审 Findings 与处置

| Finding | 证据与影响 | 修复与复验 |
|---|---|---|
| P2-1（confidence 8）inline 成功结果可能 `overall=pass` 且 `artifact_persisted=false` | 声明阶梯自相矛盾 | `journey.py` 对任何成功结果缺 artifact 均报 `artifact_persistence_unverified` 且 settlement fail；新增 inline/ref 两个负例；52 项相关测试通过 |
| P2-2（confidence 9）独立 UTF-8 参照未处理 float 归一 | >6 位小数时实现 14B、旧参照 17B，测试“独立”失真 | 两个测试文件的独立 cleaner 实现 round6/-0.0/NaN/Inf，不 import 生产函数；新增固定 0.123456789→14B 样例，52 项相关测试通过 |
| P2-3（confidence 8）omitted 计数在 512B 校验前加入，贴边 payload 从接受变拒绝 | 未披露的 fail-closed 兼容变化 | DECISIONS 明确约 25B 变化；新增 500B→525B 边界测试，验证拒绝而非超帽；相关 11 项通过 |
| P2-4（confidence 9）文档声称区分 artifact bytes/emitted events，原始报告缺字段 | 文档过度声明 | benchmark 脚本和报告增加 `unmeasured`：artifact bytes、生产 SSE events、渲染 frames、Redis mutation scaling 全部 `not_evaluated`；已重新生成，perf 契约 7 项通过 |

P0/P1：均无。

## Appendix 处置

- A1：taxonomy 已注明输入必须是 schema-packed ReplayTrace；原始未打包 GisTrace 缺字段时只能保守判缺证据。
- A5：文档明确“10 组双侧场景 + 跨城/选择 focused 补齐”，不再把 30 场景误读成 12 组全部双侧。
- A2/A3/A4：均为保守 fail-closed 或不可达边界，记录但不扩大实现。

## 二次 Review

待 reviewer B 对 P2 修复后的完整 diff 复审。只有全部 P0/P1 保持为零、P2 修复与复验完成后才能填写通过结论。
