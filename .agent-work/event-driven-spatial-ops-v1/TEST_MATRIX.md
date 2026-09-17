# TEST_MATRIX — TDD 覆盖矩阵

约定：`tests/unit/spatial_events/`（hermetic sqlite fixture，仿 mission_runtime conftest）+
`tests/integration/spatial_events/`（API 级 + cross-tenant）。`asyncio_mode=auto`，xdist `-n 2`。

## A. contracts（先写红测）
| 用例 | 断言 |
|---|---|
| envelope 合法构造 | 字段规范化（kind 词表、org 必填、payload ≤2KB、ref 可选） |
| malformed：未知 kind / 缺 org / 超 2KB payload / 超长 subject | ValidationError/拒绝 |
| event_id 派生确定性 | 同输入同 id；不同 payload 不同 id |
| payload_ref 优先于 inline | 两者同给 → 校验拒绝（防歧义） |

## B. ledger + cursor
| 用例 | 断言 |
|---|---|
| append 幂等（同 event_id 二次投递） | 第二次 ack=duplicate，无新行，零副作用计数不变 |
| 乱序 occurred_at | 不影响 ledger 序（自增 id 因果序），watch 按时间窗自行处理 |
| cursor 推进与恢复 | worker A 处理到 id=N 崩溃（模拟：新 worker 实例）→ 从 cursor+processing 清扫恢复 → 已 processed 不再执行、pending 全部处理 |
| processing 崩溃复位 | stale processing 行被恢复清扫复位 pending 并最终 processed，副作用恰好一次（fire 表 UQ） |
| org 隔离（store 级） | org-b 读不到 org-a 事件；append 拒绝跨 org 冒名（org 从上下文盖章，不信 payload） |

## C. watch 引擎
| 用例 | 断言 |
|---|---|
| threshold（metric ≥/≤/delta_pct） | 达标 fire、不达标不 fire |
| consecutive_n | 连续 N 次达标才 fire（中间断裂清零；状态跨批持久——重启后继续） |
| enter/leave AOI（bbox + polygon via shapely） | 进入 fire enter、离开 fire exit；AOI 外事件不 fire |
| time_window | 窗口内 K 次 → fire 一次；窗口外不计 |
| version_changed | subject revision 变化才 fire；同 revision 不 fire |
| cooldown | 冷却期内再次达标不重复 fire |
| 状态持久 | watch_state 重启后保留（last_value/计数器/last_fired_at） |
| 租户隔离 | org-a watch 永不匹配 org-b 事件（恶意构造也不行） |

## D. mission bridge（真 MissionRuntimeService + hermetic store）
| 用例 | 断言 |
|---|---|
| trigger→create | mission 创建且 root_goal 来源投影事实；org 一致 |
| trigger→revise | goal_revision bump、frontier 失效重排队 |
| trigger→resume | 走 recovery.recover（lease 语义不变） |
| 幂等 fire | 同 (watch,event) 重复执行 → 恰好一次 mission 变更 |
| 跨租户负例 | org 断言失败 → fire 记录 rejected，无 mission |
| stale lease | lease 被他人持有 → bridge 不强抢（记 deferred），不产生双写 |
| bridge flag off | 不产生任何 mission 变更 |

## E. invalidation bridge
| 用例 | 断言 |
|---|---|
| dataset 版本变化 → 受影响实例 | 仅受影响子图节点 STALE（用真 workflow runtime fixture 建 DAG），未受影响节点状态不变 |
| 无关实例 | 不产生 PendingChange |
| evidence 失效 | ClaimStore 中受影响 claim → stale（evidence 无 SUPPORTED 伪造） |
| flag off | 无任何 apply_changes/evidence 副作用 |

## F. situation 投影
| 用例 | 断言 |
|---|---|
| 事件→ring 写入 + 读取 | ring ≤32、持锁幂等（重复事件不重复入环） |
| compiler 第 6 源 | ring 有内容时 data 分区出现事件事实；ring 空/flag off 时编译输出与基线字节等价 |
| 投影先行 | mission 决策依据含投影事实（bridge 输入来自 projected facts） |

## G. worker/burst/背压
| 用例 | 断言 |
|---|---|
| 1000+ synthetic burst | 全部事件达终态；同 subject coalesce 计数正确；批/队列/内存有界（断言队列上限与批大小、无 unbounded 结构）；总耗时受 deadline 控制 |
| priority | interactive 先于 batch 处理 |
| tenant fairness | 双 org 等量事件 → 处理量近似均衡（轮转） |
| governor gate | ENFORCE+通道满 → defer → 事件回 pending 带退避，通道释放后完成；gate flag off → 直通 |
| 取消 | watch 删除后 pending 触发不再执行 |

## H. adapters + webhook + API
| 用例 | 断言 |
|---|---|
| E1 map mutation hook | flag on → ledger 有 map.mutation_applied；flag off → 无行且 mutation 结果不变 |
| E2 artifact revision hook | created=True 才发；幂等复用不重复发 |
| E3 job finish hook | completed/failed/cancelled 各发一次；late-success-cancelled 语义保留 |
| E4 map product hook | data_changed 标志入 payload |
| webhook 验签 | 坏签名 401；缺密钥路由禁用；合法→ingested(source=webhook)；超限 payload 413 |
| API 列表/详情/游标分页 | org 隔离（org-b 404/空）；bounded |
| SSE | 订阅收到新事件摘要；断开清理；backlog 有界 |
| replay | 已 processed 不重放（非 force）；dry_run 零副作用；force 重放幂等 |
| openapi 快照 | API_SNAPSHOT_UPDATE=1 刷新后 test_api_compatibility 绿 |

## I. 回归/静态
- 相邻套件：tests/unit/mission_runtime、tests/unit/workflow_runtime、tests/governor、
  tests/unit/gis_harness（hooks 相关）、tests/integration/test_cross_tenant_isolation.py
- ruff check / ruff format --check（涉及时）；`git diff --check origin/master...HEAD`
