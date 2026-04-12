# 全局巡检执行标准 V3.1（WebGIS V2.0 专属版）
## 🎯 核心定位
巡检核心目标从"状态统计+全量文档检查"升级为**问题驱动、价值导向**的子任务全生命周期异常监控，彻底杜绝无意义的虚假工作。所有巡检动作必须有明确产出（解决异常/更新状态/生成待办），没有产出的巡检直接判定为无效工作禁止执行。

*(前文 270 行 Coder 代理监控逻辑与执行器框架已隐式继承。以下为 V3.1 针对 WebGIS V2.0 架构特别增加的生产力探针约束。)*

---

## 🚀 WebGIS V2.0 专项探针 (Health Check in V2.0)

针对 V2.0 重构的混合架构，巡检脚本必须增加以下基建设施监控环节，一旦发现服务掉线，立即触发 🔴 Critical 级告警：

### 1. 异步超算网络巡检 (Celery & Redis)
计算密集型 GIS 操作已被剥离，巡检必须保障重武装地带正常：
```bash
# 验证中间件挂载态
REDIS_STATUS=$(redis-cli ping 2>/dev/null)
if [ "$REDIS_STATUS" != "PONG" ]; then
    echo "🔴 Critical: Redis 持久层熔断，Fetch-on-Demand 缓存链将全面崩溃！"
fi

# 验证计算节点存活
CELERY_WORKERS=$(celery -A main.celery_app status 2>/dev/null | grep "OK" | wc -l)
if [ "$CELERY_WORKERS" -eq 0 ]; then
    echo "🔴 Critical: Celery 空间计算集群下线，所有重型 Tool Call 将被阻塞挂起！"
fi
```

### 2. LLM 流水线异物阻塞拦截 (Huge Context Sniffer)
严禁 Agent 将过大的 GeoJSON 原文暴露在会话缓存内：
```bash
# 巡检数据库中 tools_result 是否存有越界字符串
OVERSIZED_PAYLOADS=$(sqlite3 data/webgis.db "SELECT count(id) FROM messages WHERE length(tool_result) > 50000;" 2>/dev/null)
if [ "$OVERSIZED_PAYLOADS" -gt 0 ]; then
    echo "🟠 Major: 发现异常宏大的 JSON 上下文！未严格执行 Fetch-on-Demand 剥离机制！"
fi
```

---

## ❌ 虚假工作判定标准（零容忍）
所有符合以下特征的巡检操作均属于虚假工作，严格禁止：
1. **无差别全量读取历史文档**：无明确需求情况下读取MEMORY.md、历史daily notes、AGENTS.md等静态文档
2. **重复读取无变更文件**：文件last modified时间无变化时重复读取相同内容
3. **巡检无产出**：巡检结束后没有任何实际动作（无异常处理、无状态更新、无待办生成）
4. **为了巡检而巡检**：单纯为了符合流程执行巡检操作，没有解决任何实际问题

## 🔍 记忆/历史文档巡检规则（彻底重构）
**完全取消高频全量记忆巡检，仅在以下明确场景下按需读取：**
1. 新任务下发时，需要相关历史上下文支撑任务执行
2. 任务执行遇到异常/阻塞，需要查阅历史解决方案
3. 明确要求做项目复盘/历史总结场景
4. 每日00:00固定执行一次记忆整理（仅读取近3天的历史文件）
5. 每周一00:00执行一次全量记忆归档（读取所有历史文件，更新MEMORY.md）

**优化机制：**
- 建立文件变更缓存：记录所有历史文件的last modified时间，仅当文件更新时才重新读取
- 增量读取：仅读取与当前需求相关的章节/内容，不读取完整文件
- 读取后必须有产出：读取的内容必须用于支撑决策/解决问题，禁止空读

---

## 🚨 强制执行框架（四层架构）
*(同 V3.0 原标准保持完全一致。维持双轨巡检制、7 类异常精准判定与自动异常恢复等内容，此处为保证文档轻量级已抽象。执行器依旧使用基于 `openclaw` 的探针命令。)*

### 记录日志存档声明
所有异常任务记录到 `logs/inspection-exceptions.csv`。每日通过巡检周报呈现。有效巡检产出强制计入考评体系。
