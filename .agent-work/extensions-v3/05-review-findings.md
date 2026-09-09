# 05 — Review Findings

## Round 0 — Subagent-A 架构挑战（2026-09-10，实现前）

结论：方向正确但按原稿实现会产出带 P0 缺陷的代码；全部 BLOCKER/CRITICAL/MAJOR 已落入 01-architecture v2。

| 级别 | ID | 问题 | 处置 |
|---|---|---|---|
| BLOCKER | B-1 | bwrap ro-bind repo 根 → worker 直接 open `.env` 击穿 V2 secrets 卫生 | 最小 bind 面（app/ + site-packages + stdlib）；恶意语料加沙箱内 open(.env) 失败断言 |
| CRITICAL | C-1 | installer 对已激活扩展 discover+activate = 静默留旧版 | 已激活走 host.upgrade()；集成测试钉 status_report().version==新版本 |
| CRITICAL | C-2 | 两次 rename 中断窗口 + staging 清扫误删 + versions 撞名 | 换装固定序 + 唯一临时名 + 启动恢复例程（先于清扫）+ rename 故障注入测试 |
| CRITICAL | C-3 | 流式整 call deadline 杀长流且误入崩溃计数 | 逐帧 idle timeout + 流超时不映射 _on_worker_death |
| CRITICAL | C-4 | retired key 验签可过 → 提权 = 降级攻击 | 新 status signed_retired：不提权 + warning |
| CRITICAL | C-5 | rollback 绕过 revocation/trust store | install/upgrade/rollback 统一 preflight |
| CRITICAL | C-6 | worker 串行 × fabric 16 线程并发 = in-flight 风暴 + 熔断误跳 | proxy 层队列化；并发退化写入 AdapterSpec.notes；worker 侧单例生命周期 |
| CRITICAL | C-7 | broker 等待循环收到 credit/cancel 帧 → 协议违规崩溃 | 每个阻塞读点帧白名单 + 迟到 credit 幂等入账 |
| MAJOR | M-1 | 协议协商与旧严格相等判定冲突 | 放弃协商；lockstep 严格相等 3.0（同仓 spawn 无偏差面） |
| MAJOR | M-2 | V3 门控放宽点有 4 处，漏一处运行期炸 | manifest/context/host 对账/invoke gate checklist 写入架构 |
| MAJOR | M-3 | JSON+rename 不是 CAS；GC 未定义；digest 形状 | 文件锁串行 publish；锁内 GC 规则；digest `^[0-9a-f]{64}$` 校验 |
| MAJOR | M-4 | tar 防护缺 hardlink/device/FIFO/sparse | 成员白名单（仅 REGTYPE）+ 前置预算统计 + filter="data" 兜底 |
| MAJOR | M-5 | host 收帧不查 max_output_bytes，window×cap 上界是假的 | host 收帧处强制 + 聚合路径总字节预算 |
| MAJOR | M-6 | credit 短超时把背压变硬失败 | 无 credit 阻塞至 credit/EOF；仅 host 显式 abort 才 FLOW_CONTROL |
| MAJOR | M-7 | provider 代理语义丢失（sync()/capabilities_v2/pydantic 重校验/错误映射/mixin 探测） | 语义表落入架构（动态 mixin 继承 + model_validate + 错误映射表） |
| MAJOR | M-8 | artifact 子命名空间破坏 V2 扩展 | api>=1.2 门控，1.1 平铺语义不变 |
| MAJOR | M-9 | per-spawn bwrap 失败静默回退 | typed 激活失败；探测仅服务显式降级 |
| MINOR | Mi-1..6 | revoke 暴露窗口/回滚预检/drain no-op/versions 排序/元数据指纹/registry 超限策略 | 全部落架构 v2 |
| NIT | N-1..3 | refresh 单文件/HMAC v1 载荷不动/协商从简 | 采纳 |

## Round 1 — Subagent-A 最终 diff 审查

（待填）

## Round 2 — Subagent-B 最终 diff 审查

（待填）
