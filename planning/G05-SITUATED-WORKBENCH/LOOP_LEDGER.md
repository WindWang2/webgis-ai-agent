# LOOP_LEDGER — G05-SITUATED-WORKBENCH 迭代账

| 轮 | 输入事实 → 决定 | 代码差异 | 失败→修复 | 本地验证 | 结果 |
|---|---|---|---|---|---|
| 1 (M0) | gh/git 实测 master=585812ee4；#2011/#2012 在途 0 交叠；存量分支 ahead=0 | worktree/venv/pnpm；BASELINE.md（含子代理 A 10 节审计） | — | 后端基线 191 绿 / 前端 194 绿 | 通过 |
| 2 (M1) | #2004 未修：contracts.py:248 value[:8] + bus.ts:209 slice(0,8) + 白名单无 matched_count + 无 id 零上行 + selection_history 死标志 | 11 失败测试 → 契约/投影/声称/验证/史代修复 + 前端 emit/镜像 | 2 旧 characterization 断言旧行为 → 语义更新；EventVerdict 下标错误 → .verdict | IR+situation 3464 绿；vitest 312；tsc/ruff/mypy 0 | 通过 (7be12d2) |
| 3 (M1+) | categories 前端 store ≤20 vs IR 8 失配；bbox 谓词只在客户端 | categories 20 + WIDE_BUDGET_KINDS + bbox 白名单/透传/声称面 | 5 旧断言连带更新（chart fat 载荷改 layer_style 钉扎 512B） | 163 绿 + 前端 33 绿 + tsc 0 | 通过 (fe149e0) |
| 4 (M3) | symbol/paint before/after 在回执但 invertible 只认 presentation | invertible 扩展 + styleCommand + 录制扩展 | mixed 测试期望翻转 → 新语义 + null-before 新例 | map_ops 68 + 前端 74 | 通过 (6cc0c24) |
| 5 (M3) | display auto 静默 = 「渲染已发生」冒充闭环 | FinalAcceptanceLine + auto 复查 + i18n | waitFor 挂 fake timers → 移除冗余断言；eslint waitFor 未用 → 清 | harness-cards 8 绿 | 通过 (af6bdbe) |
| 6 (M3) | 面板手动快照 | invalidations 订阅 + 防抖 + 会话复核 | — | agent 13 绿 | 通过 (af554bf) |
| 7 (M3) | recharts 无键盘路径 | 原生 select 同链 + i18n + chart 选择态反映 | — | panel-ir-emit+chart 10 绿 | 通过 (3c7d805) |
| 8 (M2/M6) | 金标缺失（新语义无回放钉扎） | 4 金标语料 + ADR-0287 + 三契约文档 + planning 五件套 | — | replay 29 绿；后端全域 3488 passed 4 skipped | 通过（本 commit） |

## 遗留与边界

- e2e 真浏览器档未跑（无凭证环境；mock 档回归）。
- display required 模式端到端（env 翻转）不在默认环境；组件级覆盖。
- 视口史代（"上一张图"）、服务端 map-op redo：记录为后续（ADR-0287）。
