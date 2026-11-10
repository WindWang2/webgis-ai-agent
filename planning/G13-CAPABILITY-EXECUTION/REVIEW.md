# G13 — 独立冷审记录

> 独立 reviewer（未参与实现）冷读 `git diff 816705218...HEAD` 全量，
> 不复用主 Agent 通过结论；含实机并发探针与真实八面编译复核。

## R1 冷审结论

**APPROVE-WITH-FIXES**：P0=0，P1=0，P2=2（要求合并前修复），P3=8。

六项头条断言全部在生产路径实证（reviewer 逐项核验）：
a) 生产接线 ✓（surface: pi_turn_context→compute_turn_active_tools→select 步骤 6.8；
   dispatch: 1.42b 在 bind 后、IR 闸前；例外：summarize 为契约 API——P3-1）；
b) 无「只有URI」✓（v2 封套 + seam 铸真 ref；残余 artifact:// 属无关子系统/probe）；
c) native_verified 真实获得 ✓（八面 pass + planner algorithm_backed；
   两工具名未在 conformance/availability 模块特判；计数 255 来自 2 条真实描述符）；
d) 缓存无越权 ✓（键=结构代；权限逐请求投影；扩展授权仅作失效摘要）；
e) 测试真实执行 ✓（真实 selector/dispatch/primitive；kill-switch 反验为真实
   故障注入，非 mock；C01 经生产 dispatch 对拍 numpy 真值）；
f) 科学语义 ✓（秒≠米 typed；分母披露；无等价性膨胀；缺口 P3-4）；
g) 并发/生命周期 ✓（8 并发=1 构建；漂移键不覆盖；atexit 幂等；内存有界；
   三个消费方全部 to_thread）；
h) 兼容 ✓（rerank 语义逐位对拍；G12 文件零改写；cutoff 必填为有意破坏性对齐）；
i) 取消/释放 ✓（拒绝释放占位；BaseException 包裹覆盖 to_thread 取消；
   H-1 覆盖 analysis_ref）；
j) 测试大多行为化；两处空洞断言已点名。

### R1 发现与处置

| ID | 级别 | 问题 | 处置 |
|---|---|---|---|
| P2-1 | P2 | 退避只在有 last-good 时生效：冷进程持续失败 → 每次 turn 重复秒级全量审计（风暴） | ✅ 重设计：`AvailabilitySnapshotBackoff`——窗口内同键一律不重编译、不服务旧代；消费方按证据缺席零贡献降级；新增 `backoff_suppressed` 计数与 storm 回归测试（冷/热两分支） |
| P2-2 | P2 | 退避窗口服务**跨代** last-good 且无消费方查代次（撤权后 ≤5s 两闸同读旧状态） | ✅ 同上：跨代一律证据缺席（不冒充）；boot 标志进键（P3-2 一并修） |
| P3-1 | P3 | summarize 无生产调用方且快照获取无守卫（冷上下文抛 ValueError） | ✅ fail-open 包装（snapshot_unavailable:not_evaluated 诚实视图）；契约 API 定位已写明 |
| P3-2 | P3 | 缓存键不含 bootstrap 标志（同键异源注册表实例） | ✅ `boot:{0|1}` 进键 |
| P3-3 | P3 | CORE(4) vs NATIVE(7) 前门集不一致 → 抑制披露与实际激活自相矛盾（潜伏） | ✅ selector 豁免 CORE∪NATIVE（惰性导入，失败按 CORE） |
| P3-4 | P3 | 网络原语无运行时经纬度检测（WGS84 静默把度当米） | ✅ 全坐标落经纬度域 ⇒ science.geographic_crs_warning + summary 后缀（披露不拒绝；时间模式不误报） |
| P3-5 | P3 | read_back_hint 在 data 键内，slim 丢弃 → LLM 看不到「传 ref 回来」 | ✅ 提升为顶层 preserved 键 |
| P3-6 | P3 | 发现面文案在复核 kill switch 关闭时过度承诺 | ✅ 文案按 `dispatch_recheck_enabled()` 分支 |
| P3-7 | P3 | reload 测试改全局态无 teardown；patch 直赋值 | ✅ try/finally 还原 registry/env/模块态；monkeypatch 化 |
| P3-8 | P3 | kill switch 关闭时 as_dict 仍带空 availability_suppressed（行为逐位、trace 形状非逐位） | 记账不改：无 as_dict 消费方；行为反验已证逐位一致 |
| （review 另记） | — | 发现面原语断言被 if 守卫（空洞风险） | ✅ 改硬断言（raster 域必含） |

## 复审（R2）

同一 reviewer 对修复 diff 复核 → 见下方追加记录。
