# Harness V6 Review Findings & Fixes

## Round 1（架构/正确性/状态机/数据一致性/边界/兼容性）— verdict: REQUEST-CHANGES

| # | 级别 | 发现 | 修复 | 回归 |
|---|------|------|------|------|
| C1 | CRITICAL | embedding_retriever 把 ToolRegistry 直接传给 manifest_fingerprint（不可迭代）→ 必然异常 → 模型失败被永久 memoize，默认通道成死功能；宣称的 skipif 测试不存在 | fingerprint 改为 [(name, fp[1])] 全量指纹（与 ToolRetrievalIndex._index_key 同款）；指纹失败按时间戳退化、不毒化模型记忆化；补真实调用路径的有界失败/成功双形态测试 | test_embedding_retriever_bounded_failure |
| M1 | MAJOR | 单字停用字/降权/anti 多字门未受 GIS_TOOL_RETRIEVAL_V6 门控 → 「V6=0 与 V5 逐位一致」契约不成立 | tool_retrieval.rank() 内 `_v6_scoring_enabled()`（同 env，避免 import 环）门控全部三处；V6=0 精确恢复 V5 打分 | test_v6_switch_gates_scoring_fix |
| M2 | MAJOR | ECE 只对命中案例分桶 → 桶内准确率恒 1.0，检测不到高置信度选错 | 全部非 oos 案例入桶，桶内准确率 = top1 命中率 | 评测门集成（miss 占比 ~44%，ECE>0 恒成立） |
| M3 | MAJOR | check_tokens 无生产调用点（假控制） | 接入 wrap_dispatch_with_budget._budgeted（check_wall_time 旁） | 既有 token 单测 + 接线点即生产路径 |
| M4 | MAJOR | LOOP_BUDGETS.replan 无生产驱动点 → 「总预算耗尽 abort」不可达；finalization 接线声明夸大 | LOOP_BUDGETS 去掉 replan（remediation replan 走既有 REMEDIATION_POLICY 预算）；abort 条件覆盖实际驱动回路；ADR/docstring 措辞修正 | test_continuation_budget_exhaustion_aborts（ drained 态现可经生产路径到达） |
| M5 | MAJOR | manifest 丢失/半写后无自愈 → seq 重复、窗口失真 | _heal_manifest：段文件在而账目缺失/落后时全量重扫重建（含 legacy seq 吸收）；append 前以当前段尾 seq 兜底（半写窗口） | 既有 trace 套件全绿 + chaos kill -9 场景 |
| m1 | MINOR | 别名表「影像分割」重复 → 双重加成 | 去重 | — |
| m2 | MINOR | 单候选 margin 膨胀到 1.0（lone 弱候选永不弃权） | len(ranked)==1 → margin 以 s1 自身计（=0） | test_confidence_calibration_and_abstention |
| m3 | MINOR | 注释 4.0 vs 常量 8.0 矛盾 | 注释对齐 | — |
| m4 | MINOR | 否定扣减可致负分候选入选（V5 无此路径） | select() 候选循环加 score>0 地板（non_positive_score drop） | 既有选择套件 |
| m5 | MINOR | exhausted 路径不记 repair 循环 → 裁决与披露打架 | exhausted 也走 update_recovery_state(loop="repair") | — |
| m6 | MINOR | recovery_state 读改写竞态 | 并发纪律写入 docstring（调用方 session lock 兜底；后写覆盖=预算偏松不偏紧） | 文档化 |
| m7 | MINOR | trace 读路径段解析在锁外（与 trim 竞争可静默丢段） | 段解析移入 flock 内（≤5×16 行有界） | 既有增量读测试（访问计数不变） |
| m8 | MINOR | pipeline hoist 改变瞬态失败语义 | 恢复原语义（state 读失败 → load_render_observation(sid, None) 再读） | finalization 回归 |
| m9 | MINOR | 两条恒真断言 | 改为实断言 | — |
| m10 | MINOR | 文档 13 文件/22 条 → 实为 11/20 | 修正 ADR + progress | — |
| m11 | MINOR | record_success 前缀误伤含 '||' 工具名；read 路径 mkdir | 工具名含 '||' 现网不存在（|| 为内部键分隔符，dispatch 层工具名来自 registry 词表）；mkdir 保留（幂等且廉价）——known limitations 记录 | — |
| m12 | MINOR | workflow health docstring 与实现偏差 | docstring 对齐（pending→degraded；硬错误 error 级披露由 finalizer findings 承担） | — |
| 回归追加 | — | trim 边界重写清空段后 pop 不删文件 → 孤儿 .gz 触发误 heal → manifest 重复条目/窗口 66≠64（审查修复过程发现的真实回归） | _pop_seg 统一「弹段必删文件」；heal 同名双形态以 gz 为真相删 plain 孤儿；段号单调递增（名序=时序） | V5 flood 精确 64 窗口 + 全 trace 套件 14 passed |

## Round 2（性能/安全/并发/资源/UX/可维护性）— verdict: APPROVE-WITH-FIXES

| # | 级别 | 发现 | 修复 | 回归 |
|---|------|------|------|------|
| M-1 | MAJOR | embedding_retriever 每次 select 新建 FaissVectorStore → SentenceTransformer 每查询重载（模型可得部署的 turn 延迟主导项；「单进程内复用」注释与实现不符） | 模型本体入 _embed_state["model"] 进程级缓存（懒加载一次）；ADR 措辞同步 | embedding 双形态测试路径覆盖 |
| m-1 | MINOR | 指纹退化哨兵 ts:time.time() → 每查询全量重编码 | 稳定哨兵 "degraded" | — |
| m-2 | MINOR | 全局 _WRITE_LOCK 串行所有 session 读写且临界区变大（heal/gzip 在锁内；V5 读者锁外 vs V6 锁内） | 接受为已知取舍（正确性换吞吐；锁持有 ms 级、窗口有界），known limitations 记录；按 session 分锁列为后续演进 | — |
| m-3 | MINOR | manifest 引用段文件丢失（unlink 与 save 崩溃窗口）不被 heal 检出 → 窗口软失效 | heal 增加 missing-known 检测触发重建 | trace 套件 |
| m-4 | MINOR | abstain 无模型/用户面披露（只有 logger + 链事件） | compute_turn_active_tools 增加 additive disclosure 出参；_active_tools_block_for 注入模型可读提示（list_available_tools 两跳指引） | pi_turn_context/pi_native_surface 回归 |
| m-5 | MINOR | _channels 字符串前缀塌缩 v6:* 为同通道 → 覆盖度少计；reason 文本成隐性协议 | 通道名取 reason 头部到 "("（v6:capability_alias / v6:methodology 分立） | 置信度套件 |
| m-6 | MINOR | dispatch 路径同步文件 IO + flock 落事件循环 | 接受并文档化上界（≤256 条 JSON、ms 级锁；kill -9 契约排除持锁卡死） | 代码注释 |
| m-7 | MINOR | anti_examples 无数量/长度上限（负证据可静默挤掉正确工具） | descriptor 校验：≤8 条、单条 ≤200 字符（errors 面与非空/去重同门） | tools init 296 + descriptor 套件 |
| m-8 | MINOR | 文档漂移（16→17 场景；direct/ambiguous 计数；skipif 措辞） | ADR + 门 docstring 修正 | — |
| INFO | — | chaos marker 等待 5s 冷缓存风险；测试冗余赋值泄漏 memoized 态；heal 按名重建需段号单调 | marker 等待 300×0.05s；冗余赋值删除（段号单调已在 R1 回归修复） | — |

### Known limitations（诚实记录，不修的具体理由）
1. 全局 trace 写锁（m-2）：按 session 分锁需重排 manifest 生命周期，收益
   （多 session 并发写吞吐）在本部署形态（单 uvicorn worker + Celery 隔离）
   下有限；窗口有界 + 锁持有 ms 级，风险可控。列为 follow-up。
2. dispatch 路径同步 IO（m-6）：asyncio.to_thread 包裹会引入线程池调度
   开销且改变异常传播时序；文件小、flock 快路径无竞争，接受。
3. oos 弃权为部分信号（21.4%）：置信度分布 hit/miss/oos 重叠大，全局阈值
   防乱选主力在 dispatch 校验 + finalizer evidence（W8/W10），弃权是披露面。
4. hard_negative top-5 陷阱命中（invalid 0.25）为暴露线：兄弟工具同族
   语义使 top-5 全避让不现实；指标已钉线防回归。
