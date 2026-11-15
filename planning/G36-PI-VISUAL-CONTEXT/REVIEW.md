# G36-PI-VISUAL-CONTEXT REVIEW（独立评审，2026-11-15）

Reviewer：独立 subagent（未参与实现）；审阅 base 127669273 → 全量 diff + 新文件；
vendor 契约对照 pin 源码；复跑 L1/L2 测试；两个缺陷以脚本实证复现。

## 结论（初审）
**BLOCKING：4（P1 #1–#4）**；P2×10；P3×9。架构面（ref-only 线格式/内容寻址引用/
三重能力门/窄结果投影/迁移/owner 面/预算/旧路径零变化/crop 链/i18n/无 base64 泄漏
——除 #3）**判定 clean**。

## P1（阻塞，全部修复）
1. `visual_context_tools.py` `from app.agent_pi_bridge import pi_bridge` — 名称不存在
   （`ImportError` 被 fail-closed 吞掉 → 工具图像通道恒关）。修复：改
   `get_bridge_pool().bridge_for_session(session_id)` 正确访问面 + 按 session 路由（连修 #14）。
2. V33 预检只在 `if attachments:` 内 —— 已撤图片后**纯文本 turn** 不触发换代，
   历史 pixels 仍在 vendor transcript 续送。修复：预检提升为每 turn（有 turn_sid 即执行；
   空台账早退零成本），两条 prompt 路径都改。
3. `slim_event_result` 排除集缺 `result_images` → `step_result` SSE 会向浏览器/重放
   缓冲泄漏 ≤2MiB base64。修复：加入排除集。
4. store 的 mutate 闭包在 Redis WATCH 重试下重复累计释放 sha → 共享 blob 引用计数
   双减（实证：refcount 2 → 撤一个 → blob 被删、兄弟解析失败）。修复：mutate 纯化，
   从**已提交**结果推导释放集合；`_update_index` 重试时返回 stale 首次结果同修。

## P2（本分支内修复：5/6/8/9/10/11/12/13/14；7 经核实为伪命题——#2036 后无
legacy 对话回退，Pi 不可用=503、turn 未开始，前端草稿回滚路径已覆盖）
5. steer 路径换代与在飞 turn 竞争事件流 → steer 侧改为「发现失效只阻断图片
   （blocked_reason），换代留给下一 turn 边界」。
6. 非流式 completions 不落附件/不绑定 → 复用 `_attachment_persist_meta` + bind。
8. steer 回执客户端丢弃 + accepted=false 不回滚 → 消费回执、失败回滚草稿。
9. 恢复态一律显示 sent → stream_prompt 把回执并入 on_turn_result；持久化合并
   status/reason；恢复按持久化状态渲染。
10. 索引瞬态不可用被当成「全部已撤」→ 换代过激。新增 strict 读取：不可用 →
    blocked_reason（只阻图不换代）。
11. alpha 图无压缩回退 → PNG 超预算时白底摊平走 JPEG 阶梯。
12. draft TTL/孤儿清扫未接线 → 挂到既有周期任务（purge_expired_drafts +
    vev 前缀 sweep）。
13. 索引 512 上限会驱逐 bound → 只对 draft 生效；bound/revoked 永不截断。
14. 工具能力门 `bridge_for_session("")` → 用真实 session_id。

## P3（修复 16/17/20/21/22；15/18/19/23 记录为已知小项）
16. 前端 crop 归一化取整可越界 → 钳制 x+w ≤ W。
17. consumeDrafts 连上传中草稿一起清 → 只清就绪项、上传完成后按归属回收。
20. SentImageLedger.record 死参移除。
21. layout_preview 绕过地图身份不变量（潜伏）→ 改按 MAP_SOURCE_TYPES 判定。
22. vendor maxBytes 语义为 base64 后 → 投影改为 2MiB（base64 口径）。

修复与复测记录见 REVIEW_FIXES.md / TEST_MATRIX.md；复审结论见文末附录。

## 附录：复审结论（commit 399f94238 后，同 reviewer）

- P1×4 全部在代码中核实，其中 #1（fake-pool 双 worker 路由脚本）与 #4
  （原复现脚本重跑：revoke 后 refs {'s1':1}、兄弟可解析）经**独立复现**验证。
- P2 #5–#13 与 P3 修复逐项抽查与描述一致；#7 确认伪命题（#2036 后无
  legacy 回退，503 路径 + 前端草稿回滚覆盖）。
- 修复 diff 未引入新缺陷；此前 clean 面（vendor 契约/迁移/owner/预算/
  旧路径/i18n）未被触碰。
- 残留（全部 P3，可后续跟进）：
  a) `_load_refs` 文件读异常仍按「无引用」→ 罕见瞬态可触发一次多余换代
  （隐私安全方向；索引维度已 strict）；
  b) 孤儿 `vevr-` JSON（配图已删）不在 sweep 枚举内（极小文件泄漏）；
  c) 有送图史会话的每 turn 预检含 ≤128 次受锁文件读（有界，延迟预算
  收紧时再优化）；
  d) steer 回执转录显示裸 vev- id（外观）。
- **终判：No blocking findings. Mergeable.**
