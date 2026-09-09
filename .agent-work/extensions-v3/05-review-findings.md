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

## Round 1 — Subagent-A 最终 diff 审查（2026-09-10）

结论（审查者）：Round 0 的 15 条中 13 条真实落地；新发现 1 BLOCKER + 2 CRITICAL + 8 MAJOR，「不可按现状合并」。

### 修复（全部 BLOCKER/CRITICAL/MAJOR + 相关 MINOR/NIT）

| 级别 | ID | 修复 |
|---|---|---|
| BLOCKER | BLK-1 | fabric_bridge async 死锁：泵改为 `asyncio.to_thread` + `call_soon_threadsafe` 无界队列；新增 3 条真 loop 集成测试（消费/取消/缺失条目） |
| CRITICAL | CR-1 | ConnectionProfile 传递：代理每 RPC 携带 `_profile`；worker 按 (source_type, profile) 缓存实例；factory 契约 = `factory(ctx, profile)`；测试断言 `_profile` 必在 |
| CRITICAL | CR-2 | revoke_package 先从磁盘重读合并再写（防覆盖并发吊销）；trust store 写纳入 registry 同一把锁 |
| MAJOR | MAJ-1 | 升级失败：deactivate(drain)→unload→discover→activate；失败时从 versions/ 还原旧版 + typed 失败（坏版本不留 active 位） |
| MAJOR | MAJ-2 | marketplace 四端点挂 `Depends(get_current_user)`（对齐 data_fabric 路由模式） |
| MAJOR | MAJ-3 | 流生命周期持串行锁 + 流错误经 `_fabric_error_from` 映射（Mi-5 同修） |
| MAJOR | MAJ-4 | artifact 子命名空间**实现交付**（api>=1.2 相对路径限定 `<root>/<ext_id>/`；1.1 平铺语义保留 + 语料测试钉死） |
| MAJOR | MAJ-5 | 密钥吊销传播：revoked_key_ids 集变化 → 激活扩展全部重验签，revoked/invalid/tampered → 停用+隔离 |
| MAJ | MAJ-6 | probe 要求 rc==0；smoke 改为真实 bind 面 + /usr/bin/true（本机实测 0） |
| MAJOR | MAJ-7 | 完成证明恒真断言移除；改经 `host._tool_registry` 投影代理调用（非直连 worker 句柄） |
| MAJOR | MAJ-8 | `_revocation_mtime` 仅在 load 成功后更新（失败不缓存，下次重试） |
| MINOR | Mi-3 | 正常结束不发冗余 stream_cancel（worker 取消集不膨胀） |
| MINOR | Mi-6 | get_tile marshal 补 extent |
| MINOR | Mi-8 | 归档目录 os.utime 刷新（防 prune 误判最旧） |
| NIT | — | filter=data 死注释/dead code 移除；.refresh 原子写；marketplace 版本 semver 排序 |

未采纳/留档（MINOR，属既有 V2 面或取舍，记 limitations/PR 说明）：
Mi-1（流中 health 误报 unhealthy——V2 继承语义）、Mi-2（恢复例程 preflight 窗口——激活期重验签兜底）、Mi-4（installer 锁——单机运维序列操作，PR follow-up）、Mi-7（capabilities_v2 代理——explain 退化路径已记录）、流首帧预算/TOCTOU 微窗口。

## Round 2 — Subagent-B 审查（2026-09-10）

结论（审查者）：Round 1 修复无回退；新发现 1 CRITICAL + 6 MAJOR，「需修复后合并」。

### 修复

| 级别 | ID | 修复 |
|---|---|---|
| CRITICAL | C-1 | fabric 泵三合一：有界队列（maxsize=64）+ 泵线程阻塞 put（真背压）；try/finally 兜底哨兵/异常（消费方不悬挂）；收尾先 iterator.close() + stop 事件 + 有界等待（线程不泄漏） |
| MAJOR | M-1 | 吊销传播接线：lifespan 低频 tick（300s，mtime 快路径）→ refresh_revocations + refresh 信号 → discover；discover 前重读 trust store（frozen HostPolicy 内部单点刷新）；marketplace 启动预热 fail-fast（Mi-4） |
| MAJOR | M-2 | _restore_archived 接收实际归档路径（指纹后缀名场景）；还原失败诚实报「restore FAILED, manual intervention」绝不谎报 |
| MAJOR | M-3 | provider 流传宿主侧预算（idle_timeout/max_events/per_frame_max_bytes，与 model provider 路径对齐） |
| MAJOR | M-4 | registry state 读路径 mtime 代际缓存（写路径 use_cache=False 强制重读） |
| MAJOR | M-5 | tar 增量解析（tar.next()）——成员数/尺寸/累计字节预算在读取前强制（gzip 炸弹零膨胀面） |
| MAJOR | M-6 | worker reader 帧队列 maxsize=64 + 阻塞 put（恶意洪泛 → 管道背压）；config/manifest 虚假内存声明修正；perf 测试改为断言双防线 |
| MINOR | Mi-2 | allowlist 拒绝前置到 blob 落盘之前（不产孤儿） |
| MINOR | Mi-5 | 协议版本元组比较（字典序 <"3.0" 死门控/误判修复） |
| MINOR | Mi-6 | perf wall-clock 断言放宽至数量级（CI 不假红） |
| NIT | — | started 死代码移除；_preflight 私有访问改 host.record_views() 公开方法 |

未采纳/留档（PR 说明）：Mi-1（registry 锁心跳——单机运维串行发布，30s 陈旧锁阈值已有接管语义）、Mi-3（EXTENSION_REGISTRY_URLS 保留为远端下载通道的已文档配置面，分发通道见 marketplace.md；实现为 follow-up）。
