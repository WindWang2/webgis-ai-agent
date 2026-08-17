# 运行巡检执行标准

对运行中的 WebGIS AI Agent 部署做周期性巡检的执行标准:巡什么、怎么判级、产出什么。

> **版本**: v0.1.3 · **状态**: 活文档 · **最后更新**: 2026-08-17
>
> 适用对象:本仓库的本地开发栈与生产部署(docker-compose / k8s)。巡检人可以是值班工程师,也可以是自动化代理。

## 核心定位

巡检是**问题驱动、价值导向**的异常监控,不是状态统计。每次巡检必须有明确产出之一:

1. 处置了发现的异常(修复 / 重启 / 回滚 / 升级告警);
2. 更新了任务或服务状态;
3. 生成了带上下文的待办(issue)。

没有产出的巡检判定为无效工作,禁止执行。发现问题但当下无法处置的,必须落待办,不允许"看过即结束"。

## 巡检探针(按优先级)

### P0 · 存活与就绪(HTTP)

```bash
curl -fsS http://localhost:8000/api/v1/health/live   # liveness:进程活着
curl -fsS http://localhost:8000/api/v1/ready          # readiness:DB+LLM+Redis+Celery 全通
```

- `/health/live` 失败 → 🔴 Critical:进程级故障,查容器/Pod 状态与日志;
- `/ready` 返回 503 → 🔴 Critical:任一依赖(DB / LLM API / Redis / Celery)不通,响应体只回 `{"ready": false}`,逐项用下文探针定位。

一键诊断(本机有代码时):`python manage.py check` 输出 DB / Redis / LLM API / Celery 四项连通性表格。

### P0 · Redis 中间件

```bash
redis-cli -p 16379 -a "$REDIS_PASSWORD" ping
# 容器栈:docker compose exec redis redis-cli -a "$REDIS_PASSWORD" ping
```

失败 → 🔴 Critical:Celery broker 与会话数据(SessionDataManager 提货券)双失效,所有重型工具调用将阻塞。

### P0 · Celery 计算集群

```bash
celery -A app.services.task_queue.celery_app inspect ping
# 或:docker compose exec celery-worker celery -A app.services.task_queue.celery_app inspect ping
```

无存活 worker → 🔴 Critical:空间算子 / 遥感 / Explorer 任务链全部挂起。同时抽查 `durable job` 表中 `heartbeat_at` 长时间未更新的行(`analysis_tasks` 表),判定僵尸任务。

### P1 · LLM 供给

- `/ready` 已覆盖 LLM 连通性;若单独排查,看 `logs/app.log` 中 llm_client 的报错(限流 / 鉴权失败 / 超时)。
- 🔴 级:完全不可用;🟠 级:间歇超时(考虑切换 `LLM_BASE_URL`/`LLM_MODEL` 或检查出口网络与代理配置)。

### P1 · 上下文防爆巡检(Fetch-on-Demand 纪律)

严禁超大 GeoJSON 原文进入消息历史:

```bash
# SQLite(开发):
sqlite3 data/webgis.db \
  "SELECT count(*) FROM messages WHERE length(tool_result) > 50000;"
# PostgreSQL(生产):对应 jsonb 长度判断
```

计数 > 0 → 🟠 Major:存在绕过提货券机制的工具返回,定位到工具后按"Zero Big Data in Context"红线修复(参见 [architecture.md](./architecture.md) 扩展纪律)。

### P2 · 指标与告警(生产)

- Prometheus 告警规则:`deploy/alerts-rules.json`(12 条);Grafana 仪表板经 provisioning 自动加载。
- 巡检时核对 Prometheus UI 活跃告警;`logs/tool_metrics.jsonl` 与 `logs/tool_decisions.jsonl` 可抽样核对工具成功率与失败分类是否异常漂移。

## 异常分级

| 级别 | 定义 | 处置时限 |
|---|---|---|
| 🔴 Critical | 核心链路不可用(存活/就绪/Redis/Celery/LLM) | 立即处置或升级 |
| 🟠 Major | 纪律违反、性能退化、僵尸任务 | 当日处置 |
| 🟡 Minor | 可延后的异常(日志噪声、单次失败已自愈) | 落待办,下个周期复核 |

## 虚假工作判定(零容忍)

以下巡检行为判定为虚假工作,禁止:

1. **无差别全量读取文档**:无明确需求时通读 MEMORY.md、历史记录等静态文件;
2. **重复读取无变更文件**:last modified 未变化却重复读取;
3. **巡检无产出**:结束后无异常处理、无状态更新、无待办生成;
4. **为了巡检而巡检**:仅为符合流程而执行,未解决任何实际问题。

记忆/历史类文件只在四种场景按需读取:新任务需要历史上下文;执行受阻需查历史方案;明确的复盘总结;周期性(每日增量 / 每周全量)归档整理。读取必须支撑决策,禁止空读。

## 记录与存档

- 异常任务记录到 `logs/inspection-exceptions.csv`(时间 / 探针 / 级别 / 处置 / 关联 issue);
- 周期性巡检产出周报,有效巡检产出计入考评。
