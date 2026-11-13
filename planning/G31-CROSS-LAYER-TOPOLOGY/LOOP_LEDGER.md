# G31-CROSS-LAYER-TOPOLOGY — LOOP_LEDGER

执行循环账本（每轮：确认闭环 → 调用链 → 增量 → 验证 → 修正 → 记账）。

## Round 1（2026-11-12 晚）—— 研究与基座

- [x] 复核 master ca7b3ec27、open PR=0、#2087–#2093 评审记录（G14/G23/G24/G28 终审结论读毕）
- [x] DEDUP_MATRIX 25 项逐条定状态（含研究子代理"模块缺失"误报的勘误 —— 它读了旧主工作区 HEAD 816705218）
- [x] worktree + branch：zcode/g31-cross-layer-topology-20261112T144915Z
- [x] WP01–WP07 后端：contracts/ruleset/model+0105 迁移/snapshots/geometry_core/evaluators×5/runner/jobs/service
- [x] 语义冒烟 T01–T11 全绿（oracle：40_000 m² 洞 / 10_000 m² 嵌套 / 覆盖排除）
- 关键修正：关系运算必须在**源 CRS 空间**（顶点级 UTM 变换在共享边界产生 ~5 m² 伪缝隙 → 差集误报）；度量在 UTM 分量级变换（MeasureSpace）

## Round 2（2026-11-13 凌晨）—— REST/工具/前端

- [x] REST 17 端点 + main.py 挂载 + api-docs 再生成（drift 测试过）
- [x] 6 个 @tool 注册（_TOOL_MODULES；tier/latency/memory 词表修正）
- [x] 例外/复验/修复桥/导出（exceptions_svc / repair）
- [x] 前端：typed client + 工作台面板（topology 页签）+ i18n（zh/en）+ 10 例测试
- 关键修正：apiFetch 只收 2 参；registry 用注解对象（去 `from __future__ import annotations`）；tsc 基线回到 72（= clean base）

## Round 3（2026-11-13 凌晨）—— 测试矩阵与真实修复

- [x] test_evaluators_g31 20 例（oracle 断言）
- [x] test_api_g31 13 例（J-G31 全旅程 + T12–T23）
  - 缺陷修复①：复用运行不克隆 findings（复用后问题列表为空）→ `_clone_reused_run` 全量投影 + 例外按锚点重应用
  - 缺陷修复②：stale 判定比对提交期快照（永不触发）→ 改为与源**当前** revision 比对
  - 缺陷修复③：apply_scope 的 evaluate_where 实参序反了（where, feature）
- [x] test_ruleset_repair_g31 13 例；test_perf_curve_g31 3 例（10k 级实测：预算截断如实 partial）
  - 缺陷修复④：端点无候选距离逐次变换 10k 点并集（O(n²)，120s）→ 预变换基准（→5s）
- [x] 回归：quality 88p / feature_edit 123p / data 全量 1212p（2 个 pre-existing 失败以干净 base 复现归档）
- [x] vendor/pi 子模块 pin b4afdddf 本地构建（重锁内）

## Round 4（2026-11-13）—— 真实 Pi 旅程（J-G31）逐层打通

真实缺陷修复（每项都由旅程失败暴露）：
- ⑤ LLM stub 环境必须在 app 导入前设置（config 单例冻结）——driver 重排 stub → env → imports → uvicorn
- ⑥ deferred 工具面：长尾工具须先 tool_search 发现再裸名调（ADR-0288 dump v4 语义）
- ⑦ Pi 注入 session_id：工具 turn 脚本不显式传（schema 已剥离 → 显式传会被拒）
- ⑧ WEBGIS_API_BASE 须在 app 导入期前设置（扩展回调基址冻结）
- ⑨ agent 无用户身份：submit_run/update_ruleset 会话锚定所有权（`anchor_session_id`，仅工具路径显式传 —— REST 请求体 session_id 不构成所有权证明，安全收紧）
- ⑩ 执行期再解析同用锚定（run.session_id = 提交期已验证会话；绑定同会话免二次校验，跨会话仍显式鉴权）
- ⑪ dispatch 透明解引用：ref: 字符串参数到工具手时已是内联载荷 → `_rebind_inline_payloads` 重物化为会话 ref（保持 revision/stale 语义与 run.bindings 有界）
- ⑫ 跨面可见性：会话内创建的无主资源（agent 路径）→ 会话属主兜底（Conversation 属主校验）
- ⑬ driver 后段 asyncio.run 拆散调用拖垮共享 loop → 复核改走 SSE 回执；driver 判定后 os._exit（Pi 子进程拖住 stdout EOF）

最终旅程（driver18 + pytest）8/8 全绿：缺口 39,919.6 m² vs oracle 40,000 ±1%。

## Round 5（2026-11-13）—— 收口

- [x] ruff 全清（47 → 0；含 E741 l→loc、F841、E702）
- [x] 全回归复跑：topology 46 + feature_edit/selection/quality 173 + data 1212 + 前端 29（4 文件）+ tsc 72=base
- [x] commit fe2291941（43 文件）
- [x] PR_BODY.md / TEST_EVIDENCE.md / TEST_MATRIX.md / INTERFACES.md 落盘
## Round 6（2026-11-13）—— 独立评审闭环与出口

- [x] Round 1 评审：无 P0；P1×2（越权会话 stamp / boundary_touch 静默无效）+ P2×6 → 77485cb77 清偿
- [x] Round 2 复审：8/10 确认；抓到 P2-4 声称与事实不符（no-op 替换）+ NEW-1
      预算序回归 → d412e84b9 清偿（真接线 + 阈值先行 + digest 口径撤漂移）
- [x] Round 3 终审：**APPROVE**（探针含负例对照；预算触顶路径单独复证）
- [x] REVIEW.md / PR_HANDOFF.md / TEST_EVIDENCE 终稿落盘；注释修正
- [x] push → PR（见 PR_HANDOFF.md）
