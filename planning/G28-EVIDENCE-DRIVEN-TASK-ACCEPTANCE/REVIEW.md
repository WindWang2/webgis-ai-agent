# G28 REVIEW — 独立评审记录（review → fix → review 循环）

评审人：独立 reviewer 子代理（未参与实现；可复跑测试；只读）。
第一轮全量审查：BASE b8f2a6ecc → HEAD（评审时点 6 commits），含全 diff 阅读 +
独立伪造输入 harness（评审人自建于 repo 外）+ 测试复跑。

## 第一轮结论

**APPROVED（无 P0）**，1 P1 should-fix + 11 P2 notes。评审人独立伪造 20+ 输入，
确认 12 个语料负例全部拒绝说完成（must_not_pass 全咬合）；2+1 个合法无地图
任务不被阻断；授权面（_collect_delivery 走公开 get_delivery_job 授权 +
session 过滤）与 kill switch 验证不放宽；测试 oracle 独立手写（非抄函数输出）。

## P1（第一轮）→ 修复（787cc62ec）

**F1 交付身份闸被前缀伪造绕过**：证据绑定用子串 `fmt=csv` 匹配，
`format="csv-forged"` 的完成回执（无 sha256）可冒充 csv 交付 → fulfilled。
今日生产词表不可达（G18 白名单封闭），但评估面接受任意 bundle。
**修复**：`_delivery_caps_verdict` 以证据行 detail 内 `fmt=<token>` 终结匹配
bundle 行；证据↔bundle 身份不一致 → `artifact_unverified` partial（fail-closed）。
复验：F1c → partial/artifact_unverified（global partial）；F1b 正例 → fulfilled。

## P2 修复（同 commit）

- F2 frame summary 伪造（trusts summary only）：`_frame_gate` 行在场时按行
  重算 required/verified/snapshot_only 取更严侧。复验：F3a（伪造 complete 2/2）
  → partial；F3d（required=0）→ partial；F3b/F3c 负例不回归。
- F6 `bundle_artifact_caps` 读错键（死代码）：改读 families.delivery。
- F9 交付格式词表缺生产者 token：补 shapefile_zip/flatgeobuf/geoparquet。
- F10i wiring 测试恒真式比对：改同输入确定性重评估。
- F10ii JG28 软分支不可达：以 map_finalization_seen 为条件。
- F12 双 Conversation 查询：单查（job_ids + owner 同取）。

## P2 接受为已知限制（有理由，记录于 ACCEPTANCE.md）

- F3 回执↔行绑定 opt-in（bound_ref 缺席 → 不绑定不猜）：A25 兼容姿态；
  不变量「回执族行必带 bound_ref」留作后续 planner 侧改进。
- F4 edit stamp_status 生产不可达（journal 头无该字段）：诚实披露面仅在
  显式 bundle 出现；journal 提交本身是真实事实，无假 pass 方向。
- F5 refresh caps 无消费方（披露面）；A29 由 render/revision 漂移守卫覆盖。
- F7 A17 session 过滤为惰性防御（生产路径 bundle 由 collect(session_id)
  产生，绑定天然成立）。
- F8 compute/import/delivery 回执无陈旧锚（revision 空间不同，已文档化）。
- F11 frame 块持久化仅截断 problems（信任 G15 生产者界）。
- F1 附带确认：评审人独立验证 map-export 回执从未产出 csv（oracle 更新理由成立）。

## 复跑（评审人执行）

- goal_satisfaction 五件套 → 62 passed（评审时点）
- map_completion + state_machine_v7 → 83 passed
- pr1972 raster integrity → 1 failed **BASE 逐位复现**（预先存在）
- 伪造输入 20+ 全部与预期一致；语料 oracle 判定「独立手写，反例咬合」

## 第二轮复审（fix commit 787cc62ec 后）

**VERDICT: APPROVED**。reviewer 独立复跑伪造 harness：
- F1c 前缀伪造 → partial/artifact_unverified（HOLD）；F1b 正例 → fulfilled（无回归）；
  shapefile_zip 交付（词表修复）→ fulfilled/satisfied（bonus 确认）。
- F3a/F3d frame 伪造 → partial（HOLD）；F3b/F3c 负例无回归；**零漂移数值
  验证**：4 个真实 verify_frame_product 块上 max/min/max 重算为数值空操作。
- 新增回归测试确认为手写 oracle（非函数输出复制）；恒真式替换有意义；
  小修全部 sound；JG28 分支以 map_finalization_seen 为条件的硬断言解决
  不可达分支与旧矛盾。
- 复跑：64 passed（全套件）+ 3 例隔离 + finalizer 回归 83 passed。
- 残留 P2（非阻断）：双伪造形态（summary.required=0 ∧ 行 required=False）
  —— 已在终提交按 reviewer 建议加固（行在场 ∧ status=complete ∧ required=0
  → fail-closed frame_summary_rows_mismatch），终验证实 partial。
- 结论：本方向新增 P0/P1/P2 阻断归零；P2 已知限制见 ACCEPTANCE.md。

## 第二轮复审结论

（待 reviewer 返回后追加）
