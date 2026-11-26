# PLAN — GOAL-20261126-01 Pi 原生决策、工具契约与交互运行治理

> WP 循环纪律：复核现状 → 失败用例/独立 oracle → 最小实现 → 真实接线 →
> 正/负/并发验证 → 独立 review → fix → regression → commit → 更新 LOOP_LEDGER。
> 主 Agent + ≤3 只读/独立边界 subagent；审查 Agent 不写被审文件。

## WP1 · 兼容实测与能力消费台账（基线已部分完成）

- [x] 上游 release/tag 实查（v1.6.1 最新，无 prerelease/draft/新 tag）→ 保留 pin
- [ ] 在真实 vendored entry（build 后）探查：feature snapshot、`models.classify`
      可达性（codemode only？）、`blockedKind`（dialog 事件词表是否经 RPC 可达）、
      `notices.*` 设置消费面、compaction 钩子、provider retry 事件
- [ ] 台账：能力 × {已消费 / 未消费可用 / 不可达 / 有风险 / 无需消费} × 宿主调用点
- [ ] 证明 `--no-builtin-tools` 下 shell/fs/network 不可达（既有契约测试复核）
- [ ] floor v1.5.1 / pin v1.6.1 / unknown 版本行为测试复核（能力快照已有，补消费面）

## WP2 · GIS 决策能力安全启用

- [ ] 失败用例先行：decision payload 越权（未注册工具 ID/扩大数据范围）必须拒绝
- [ ] `pi_decision.py`：有界投影（task_type + 资格摘要 + 候选 ID 集 + 歧义原因）
      → `choice/reason/uncertainty/responseId` 子集；确定性闸后接受
- [ ] `models.classify` 真实可达性判定（WP1 结论驱动）：适合则 opt-in 适配器，
      否则 host 侧 stub provider 车道验证降级路径
- [ ] fallback：无 provider/未授权/低置信/超时/重复 → 确定性规则 + 明确状态
- [ ] 三类场景接线：任务识别、制图模型候选、是否需要澄清（生产调用方）
- [ ] 不开启默认：feature gate（env/config）+ kill switch

## WP3 · Provider-aware schema 与工具可执行性

- [ ] 复核 C20 现状：`query_local_poi` / `webgis_component_update` xfail(strict)
      是否仍钉；长尾 fallout 252/529 本地复现
- [ ] schema corpus：$ref/$defs、oneOf/anyOf、裸 const、nullable、default、
      中文描述、深层嵌套 → 上游谓词移植的真实分桶
- [ ] 单一来源修复（registry args_model 或投影边界），不删约束换 strict
- [ ] strict 启用条件：adapter/probe 证实 + 语义等价；分类 strict/non-strict/
      unsupported 显式化并测试
- [ ] tool_search/codemode 嵌套调用 parent/child ID、取消、错误证据对账复核

## WP4 · 原生交互阻塞语义与上下文治理

- [ ] `blockedKind` RPC 可达性实查（vendor 源码 + rpc docs）；不可达则 Web 侧
      保留显式确认协议（记录证据，不假冒）
- [ ] 三态映射（澄清/权限确认/需登录）→ 既有 WebGIS 交互结构；取消/会话切换/
      晚到答复/重复确认/过期 nonce fail-closed（负例）
- [ ] compaction 前后 scope/dataset/ref digest + 计划义务 + 未确认操作有限重建
      验证；不塞大地理数据入 transcript
- [ ] `notices.*` 仅在真实设置消费点需要时接入；redaction + 明确状态
- [ ] provider 失败可操作提示（复用 PiStderrDiagnostics/typed 诊断，非新日志系统）

## WP5 · 产品级回归、评估与可观察性

- [ ] 七类旅程（stub provider + 真 RPC + 真 dispatch + SSE）：
      ①成都小学分布候选合理 ②先选要素再统计旧 scope 拒绝 ③发现/嵌套 read-write
      授权拒绝 ④长对话压缩续跑/429 成功与耗尽/abort/断线重放结算 ⑤无 strict
      支持 fallback ⑥权限询问取消 ⑦纯文本不出图
- [ ] 评估 corpus（双语+对抗+模糊）对比：纯规则 vs opt-in decision vs 降级；
      指标：正确候选率、越权/误执行=0、false-success=0、schema 有效率、p50/p95
- [ ] 全部新模型调用纳入既有预算/redaction；多租户与 recycle 复核

## WP6 · 独立 review、修复与交付

- [ ] 子代理 A（代码/兼容）、B（安全/状态机）、C（真实旅程/性能）串行轮换审查
- [ ] P0/P1 全修 + 回归；P2 逐项修复或书面豁免
- [ ] final review-fix-review：全量 diff 反向验证（错误输入/stale refs/取消/
      跨租户/空值/权限不足）
- [ ] 本地闸：Ruff、本域 pytest、vendor contract、schema golden、journey E2E、
      doctor 六域；前端仅相关接线 typecheck/lint
- [ ] push 分支 + `gh pr create --base master`（正文含旅程/架构/新增内容/限制/
      测试/review 结论/回滚）

## 完成判定（Oracle）

1. WP1–WP5 核心闭环完成且有真实调用路径与正负验收证据。
2. 本线 P0/P1 = 0（独立 reviewer 复核）；P2 全部处置。
3. `docs/PI_UPSTREAM_COMPAT_MATRIX.md` / `PI_RUNTIME_CONTRACT.md` 同步更新。
4. 本地闸绿（失败如实记录在 TEST_EVIDENCE.md）。
5. PR 已提交（URL/number/head SHA 记录于 HANDOFF.md），不合并、不删 worktree。
