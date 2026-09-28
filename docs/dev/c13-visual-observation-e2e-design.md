# C13 — 制图视觉感知闭环 E2E 设计与勘察记录

方向：Screenshot/Scene Observation → Critique → Safe Repair 的生产闭环。
基线：`origin/master = 77d2678d`（2026-09-29 fetch，PR #1569 之后）。
前置：F15（PR #1500 / ADR-0214）已交付 provider/taxonomy/fusion/store/
recurrence/两步 repair；其 review P2-1/P2-2/P2-5 与 Out of Scope 明确把
blob 生命周期、前端采集与审批 UI 留给后续 —— 即本方向。

## 一、执行时证据（非快照复述）

- `git fetch origin --prune` 后 open PR #1570–#1577 均为 H 波架构/重构
  （contract kernel、mutation registry、chat/SSE spine、durable journal、
  capability v2、compute kernel、delegation、dataset descriptor）——
  无一覆盖本方向；lifecycle_engine.py 冲突面（H02）通过本 PR **零改动
  lifecycle_engine** 规避（全部走 `apply_visual_heal_patch` 既有入口）。
- Dependabot PR #1534–#1540 不触碰。
- 相关 issue：#1556（MapSpec 契约无类型）/#1555（e2e 直打 API）与本
  方向相邻但不同面，未扩 scope。

## 二、master 上的真实缺口（勘察结论）

1. **旧观测可驱动新地图**：`latest_screenshot_for` revision 不匹配回退
   旧图；`map_product.visual_findings` 不带观察 revision。
2. **blob 无生命周期**：内容寻址全局共享无 refcount（A 淘汰删 B 的字节）、
   `clear_session` 不回收、无会话绑定解析护栏。
3. **无 AUTO_SAFE 分级**：visual finding 恒 `repair_class=""`；拒绝后
   无记忆，同一 patch 反复索要。
4. **repair intent 无 codec**：提案为裸 dict；无决策因果账本。
5. **前端零接线**：`captureMapCanvas` 存在但未接 `/visual-snapshots`；
   无 approval/preview/diff UI（grep 全 0 命中）。

## 三、设计决策（详见 ADR-0217）

- **D1 stale 双门**：管线严格匹配（revision+指纹）+ provider 纵深门 +
  `observed_revision` 证据盖章与修复面新鲜度过滤。
- **D2 blob 生命周期**：`vref-<sha>` 引用索引存于同一 BlobStore 协议
  （可插拔）；释放只减本会话引用；竞态向泄漏安全；clear_session 挂点；
  sweep GC 维护面。
- **D3 分级与保守默认**：`approval_class_for` 纯函数；自动执行默认关
  （env opt-in），开启后 origin=system + 锁 guard + 三重预算 bounded。
- **D4 拒绝记忆**：按 finding 的 recurrence 指纹持久；approved 撤销
  更早 rejected；被拒提案 apply → 409。
- **D5 intent codec**：typed round-trip 服务持久化面；有意不进
  body_to_intent（避免 approval 门外的第二 apply 入口）。

## 四、闭环时序（生产形态）

```
reconcile settle → cartographic observation POST（fingerprint 门）
  → 响应盖章 mapspec_revision → 前端受控采集（三重门）→ 上传
  → 后台复验（evaluator 已配置时）→ 同 revision 终验拿到新鲜像素
  → findings / recurrence / AUTO_SAFE pass（env 开启时）
  → 用户批准（approval 卡片）或自动修复 → mutation（revision+1）
  → 旧证据全部过期 → 下一轮观察重盖章 …… 拒绝 → 拒绝记忆
```

## 五、测试矩阵（Wave E 落地）

- stale：store 严格匹配 / provider 双门 / plan `stale_findings` /
  corpus 级旧截图零 findings / 自动通道 stale 拦截。
- blob：FIFO 淘汰不删共享字节 / 最后释放回收 / clear_session 钩子 /
  跨会话猜 ref 拒绝 / 去重不重复计数 / sweep 只清老孤儿。
- 分级与预算：policy 矩阵 / 触锁降级 / per-revision 与 per-session
  预算 / 收敛硬停诚实回执 / duplicate 不计预算 / kill-switch。
- 拒绝记忆：reject→409→不再索要→新证据放行→approved 翻转 rejected。
- intent codec：round-trip 等指纹 / 漂移 None / 键序稳定。
- 前端：采集门 5 例 + 上传 4 例；审批卡片 7 例（approve wire 字段、
  锁披露、诚实缺席、硬停）；i18n 28 例 + 无裸 CJK 门禁。

## 六、资源与兼容

- 全部 additive：新字段有默认值、新端点、新 env（`GIS_VISUAL_AUTO_REPAIR`
  默认关）；未配置 evaluator = F15 行为逐字节保留；lifecycle_engine 零改动。
- OpenAPI 快照显式刷新（additive 147 行）；前端 eslint 0 警告、tsc 干净。
