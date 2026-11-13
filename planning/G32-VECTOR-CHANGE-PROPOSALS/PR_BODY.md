# feat(g32): 持久要素变更提案与字段、几何冲突审阅

## 用户问题

Agent 修正一批设施分类与位置时,现有编辑链只有 10 分钟 TTL 的预览计划:用户不立刻批准,候选即消失;用户改了无关字段后 Agent 的建议全部作废重来;几何/字段同时被改时无差异可看,只能整包重做;提交中断后无法判断数据是否已写。

## 行为增量

- **持久待审提案**:Agent/用户把有界修改集保存为跨页面关闭、服务重启可恢复的候选(状态机 draft→submitted→approved→merging→merged + changes_requested/rejected/withdrawn/recovery_required);当前数据不被触碰。
- **三方冲突审阅**:base 快照(BlobStore CAS,digest 校验)vs 当前数据 vs 候选 —— 字段级冲突(typed 严格:missing≠null、False≠0、0≠"0")、几何双改(确定性摘要对照,绝不平均)、delete/update、split/merge 父退役、snap 依赖漂移、重复自然键/身份不可证(阻断)分别 typed 呈现;同值已满足不重复写;无关要素修改识别为独立(保留双方)。
- **显式裁决**:逐冲突 keep_current/take_proposed/use_new_value(新值经 G14 验证器);裁决/内容/base 变更都 bump revision + 新 digest —— **旧批准自动失效**。
- **批准绑定 + 真实提交桥**:批准绑定候选 digest/revision/base/指纹快照;merge 前服务端重新鉴权 + 冲突复核 clean,经既有 G14 executor(锁/CAS/journal/undo/MapSpec 盖章全链)单 ref 受控提交;幂等键绑定提案版本。
- **崩溃恢复**:提交窗口失败三态判据 —— journal/持久回执可证已执行 → 恢复相同回执(不重复写);指纹未变可证未执行 → 回 approved 可重试;不可证 → recovery_required **阻断盲重试**(只读证据视图 + 证据门 resolve 端点);硬崩溃(kill -9)遗留的 merging 由 merge/recovery 入口路由到同一判据,不再卡死。
- **Agent 工具(无批准权)**:vector_change_proposal_create/list/diff/rebase_preview(tier 2,deferred 面经 tool_search 发现);没有 decision/merge/resolutions 工具 —— 几何/结构提案的批准是 owner 在 UI/REST 的人工动作,actor 由服务端 auth 裁决。
- **审阅 UI**:编辑器工具栏提案队列入口 → drawer(队列/状态排序/来源)→ 详情(base 证据/digest/修订)→ 逐冲突裁决(fieldset+radio,三方 typed 值原样)→ 批准(仅 clean)→ 合并 → 撤回/恢复;读屏语义(dialog focus/aria-live);i18n zh-CN/en-US。

## 复用与范围

- 复用 G14 全部编辑算子/身份/锁/CAS/journal(唯一写路径不变);review 存储模式(原子 JSON+分布式锁+租约);BlobStore CAS;G24 身份链。
- G14 最小适配(向后兼容,123 项既有回归全绿):build_plan 可选 plan_seed;plan 级 op 可选 op 级 wfids 限定(一个 plan 表达多要素异构修改);归一化工具下沉 identity.py。
- 不含:G31 规则引擎、离线同步/CRDT、跨 ref 事务、Pi vendor/RPC 改动。

## 实际验证

| 层 | 结果 |
|---|---|
| 单元/集成(含手工 oracle 冲突矩阵) | **43 passed**(tests/unit/feature_proposals/,P01–P28 场景映射见 planning/G32-VECTOR-CHANGE-PROPOSALS/TEST_MATRIX.md) |
| G14 回归 | **123 passed**(适配无回归) |
| Pi 全链(真实 vendor Pi 子进程 pin b4afdddf + 生产扩展 + 真实 ToolDispatchService + 生产 app;stub LLM) | **4 passed** — tool_search 发现→真实 dispatch 起草→REST owner 批准+merge→真实数据逐要素核对 |
| 前端 | vitest 4 passed;eslint --max-warnings 0;tsc 通过 |
| lint | ruff 全绿 |

未测/披露:真实浏览器(Playwright)端到端未执行(组件级真实交互 + REST/Pi 全链为证);P21(MapSpec stamp 延迟收敛)依赖 G14 既有 stamp_status 披露机制,未单独注入测试;大规模(近 20k 要素)性能基准未做(上限闸已由 typed 拒绝路径覆盖)。

## Review / Fix / Rereview

- 独立 review(未参与实现):**request-changes**(2×P0:无界集合破坏存储+损坏异常逃逸、硬崩溃 merging 卡死;3×P1:UI 裁决 422、结构父裁决空操作、feature_ids 丢失致 merge 提案不可创建;4×P2;10×P3)。
- 全部 P0/P1/P2 与关键 P3 修复,附 10 项回归测试;未处理项(read_fields 派生留档、锁表无界与 review store 同款先例)记录于 planning/.../REVIEW.md。
- 复审结论:见 planning/G32-VECTOR-CHANGE-PROPOSALS/REVIEW.md 第二轮(随 PR 提交)。

## 兼容与并行 PR

- base = ca7b3ec27(fetch 复核 origin/master 未前移)。
- 与 open #2094(G30)/#2095(G33)仅共享 app/main.py、app/tools/__init__.py 注册接缝 —— 本分支改动为纯加性 4 行,git 合并无冲突、语义互不依赖。
- 无 DB 迁移(治理对象与 review/ADR-0203 同生命周期:per-session JSON 文件);回退 = 移除路由/工具注册行与模块目录,既有功能零影响(默认路径行为不变)。

## 迁移/回退

新增文件为主;feature_edit/plan.py 的 plan_seed/wfids 均为可选参数,默认路径行为与历史完全一致(123 项回归证明)。
