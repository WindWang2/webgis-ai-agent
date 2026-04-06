# 全局巡检执行标准V3.0（2026-04-04生效）
## 🎯 核心定位
巡检核心目标从"状态统计+全量文档检查"升级为**问题驱动、价值导向**的子任务全生命周期异常监控，彻底杜绝无意义的虚假工作。所有巡检动作必须有明确产出（解决异常/更新状态/生成待办），没有产出的巡检直接判定为无效工作禁止执行。所有巡检结果直接推送至当前飞书群（oc_3425238dfc6c1bc273f590752ff3e36a），无需额外配置发送渠道。

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
### 第一层：巡检执行层（双轨制，彻底隔离虚假工作）
#### 🔹 A类巡检：核心监控巡检（每10分钟执行1次，整点额外执行，**完全不读取任何历史/记忆文档**）
#### 1. 基础信息采集（每次必采，无任何文件读取操作）
```bash
# 1. 采集所有coder子任务状态
ALL_CODER_TASKS=$(openclaw sessions --all-agents --json | jq '[.[] | select(.kind == "subagent" and .agentId == "coder")]')

# 2. 采集活跃任务（最近10分钟有更新的运行中任务）
ACTIVE_TASKS=$(echo "$ALL_CODER_TASKS" | jq '[.[] | select(.status == "running" and (.updatedAt / 1000) > (now - 600))] | length')

# 3. 采集异常候选任务
STALE_CANDIDATES=$(echo "$ALL_CODER_TASKS" | jq '[.[] | select(.status == "running")]')

# 4. GPU利用率采集（计算密集型任务判定用）
GPU_UTIL=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits 2>/dev/null | awk '{ sum += $1 } END { if (NR > 0) print sum/NR; else print 0 }')

# 5. PR/Issue状态采集
OPEN_PR=$(gh pr list --repo WindWang2/webgis-ai-agent --json number,title | jq length)
OPEN_ISSUE=$(gh issue list --repo WindWang2/webgis-ai-agent --json number,title | jq length)
```

#### 2. 历史会话清理
```bash
# 清理超过24小时的所有历史会话
openclaw sessions cleanup
```

---
#### 🔹 B类巡检：记忆/文档巡检（按需触发，**禁止高频执行**）
#### 触发条件（满足任一即可执行）：
1. 新任务下发，需要历史上下文支撑
2. 任务异常需要排查，历史方案有参考价值
3. 每日00:00固定记忆整理
4. 每周一00:00全量记忆归档

#### 执行规则：
```bash
# 1. 先检查文件变更，仅读取有更新的文件
FILES_TO_READ=()
for file in "MEMORY.md" "USER.md" "SOUL.md" "memory/$(date +'%Y-%m-%d').md" "memory/$(date -d 'yesterday' +'%Y-%m-%d').md"; do
    CURRENT_MTIME=$(stat -c %Y "$file" 2>/dev/null || echo 0)
    LAST_MTIME=$(cat .cache/file_mtimes.json | jq -r ".\"$file\" // 0")
    if [ "$CURRENT_MTIME" -gt "$LAST_MTIME" ]; then
        FILES_TO_READ+=("$file")
        # 更新缓存
        jq ".\"$file\" = $CURRENT_MTIME" .cache/file_mtimes.json > .cache/tmp.json && mv .cache/tmp.json .cache/file_mtimes.json
    fi
done

# 2. 仅读取需要的文件，没有变更的文件直接使用缓存
# 3. 读取后必须生成明确产出：更新任务上下文/生成解决方案/更新MEMORY.md
```

---
### 第二层：异常判定层（核心升级，7类异常精准识别）
#### 异常判定矩阵（满足任一条件即判定为异常）
| 异常等级 | 判定条件 | 处理策略 | 通知对象 |
|---------|---------|---------|---------|
| 🔴 Critical | 1. 任务运行超过60分钟无任何进度更新<br>2. 连续3次模型调用失败/超时<br>3. 任务返回明确错误状态 | 立即终止任务，自动重发（优先级>=High的任务立即重发，低优任务进入队列） | PM + 全栈群@全体 |
| 🟠 Major | 1. 运行超过30分钟无输出更新<br>2. 运行超过15分钟且GPU平均利用率<10%（计算密集型任务）<br>3. 任务状态异常（如pending超过10分钟） | 告警观察10分钟，仍无更新则终止重发 | PM |
| 🟡 Warning | 1. 运行超过15分钟无心跳上报<br>2. 任务进度远低于预期（如预计30分钟完成的任务15分钟完成度<20%） | 记录日志，持续观察 | 仅日志 |
| ✅ Info | 正常运行 | 无操作 | 无 |

#### 异常判定执行脚本
```bash
# 1. 终止Critical级异常任务
CRITICAL_TASKS=$(echo "$STALE_CANDIDATES" | jq -r '.[] | select((.updatedAt / 1000) < (now - 3600) or (.status == "failed") or (.error != null)) | .sessionId')
for task in $CRITICAL_TASKS; do
    openclaw sessions kill $task
    # 自动重发高优先级任务
    TASK_INFO=$(echo "$STALE_CANDIDATES" | jq --arg id "$task" '.[] | select(.sessionId == $id)')
    TASK_PRIORITY=$(echo "$TASK_INFO" | jq -r '.priority // "Medium"')
    if [[ "$TASK_PRIORITY" == "High" || "$TASK_PRIORITY" == "Critical" ]]; then
        # 重新创建相同任务
        TASK_NAME=$(echo "$TASK_INFO" | jq -r '.label')
        TASK_CONTENT=$(echo "$TASK_INFO" | jq -r '.task')
        openclaw sessions spawn --agent coder --label "$TASK_NAME" --task "$TASK_CONTENT" --runtime subagent --model sglang/MiniMax-M2.5
    fi
done

# 2. 处理Major级异常任务
MAJOR_TASKS=$(echo "$STALE_CANDIDATES" | jq -r '.[] | select((.updatedAt / 1000) < (now - 1800) or ((.updatedAt / 1000) < (now - 900) and '"$GPU_UTIL"' < 10)) | .sessionId')
for task in $MAJOR_TASKS; do
    # 记录告警，10分钟后再次检查
    echo "[$(date +'%Y-%m-%d %H:%M:%S')]  Major异常任务：$task，将在10分钟后再次检查" >> logs/inspection-alerts.log
done
```

---
### 第三层：告警分发层（自动通知对应处理方）
#### 1. 标准巡检报告输出（每次巡检必发飞书群）
```markdown
📋 【巡检报告】${执行时间}
━━━━━━━━━━━━━━━━━━━━━
🔹 Coder状态：${状态标签} | 活跃任务：${ACTIVE_TASKS}个
🔹 异常任务：${异常数量}个 | 已自动处理：${已处理数量}个
🔹 Open PR：${OPEN_PR}个 | Open Issue：${OPEN_ISSUE}个
🔹 GPU平均利用率：${GPU_UTIL}%
━━━━━━━━━━━━━━━━━━━━━
⚠️ 异常详情（如有）：
${异常列表}
✅ 下一步自动处理动作：
${处理动作列表}
```

#### 2. 异常告警推送规则
- 异常数量>0时，报告自动@PM（Kevin）
- Critical级异常额外标记【紧急】，立即推送，无需等到整点
- 无异常时报告简化为精简模式

---
### 第四层：闭环处理层（异常全流程跟踪+巡检有效性校验）
#### 1. 任务异常跟踪机制
所有异常任务记录到`logs/inspection-exceptions.csv`，包含：
- 任务ID、任务名称、优先级
- 异常类型、发生时间、处理方式
- 重发任务ID、完成状态

#### 2. 巡检有效性校验机制（杜绝虚假工作）
每次巡检结束后自动校验有效性，不符合要求的判定为虚假工作：
```bash
# 有效性判定逻辑
HAS_EXCEPTION_HANDLING=$([ "$异常数量" -gt 0 ] && [ "$已处理数量" -gt 0 ] && echo 1 || echo 0)
HAS_STATUS_UPDATE=$([ "$状态变更" == "true" ] && echo 1 || echo 0)
HAS_TODO_GENERATION=$([ "$新增待办数量" -gt 0 ] && echo 1 || echo 0)

# 有效巡检：满足任一条件
IS_VALID=$((HAS_EXCEPTION_HANDLING || HAS_STATUS_UPDATE || HAS_TODO_GENERATION))

if [ "$IS_VALID" -eq 0 ]; then
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] 无效巡检（虚假工作），无任何实际产出" >> logs/inspection-ineffective.log
    # 连续3次无效巡检自动调整巡检频率，减少资源浪费
fi
```

#### 3. 记忆巡检闭环规则
所有记忆/文档巡检必须有明确产出：
- 用于新任务：更新任务上下文，输出任务执行建议
- 用于异常排查：输出问题解决方案，指导任务修复
- 用于记忆整理：更新MEMORY.md，归档无用历史信息
- 无产出的记忆巡检直接判定为虚假工作

#### 2. 任务自动重分配规则
- Coder空闲时（活跃任务=0），自动从优先级队列取最高优先级任务分配
- 队列优先级：Critical > High > Medium > Low
- 同优先级任务按创建时间排序，先创建先分配

#### 3. 状态自动同步
- 每次巡检自动更新`docs/task-board.md`中的Coder状态、异常统计字段
- 状态变更自动提交到GitHub仓库，提交信息格式：`chore: 巡检更新 ${执行时间} | Coder状态：${状态标签}`

---
## 📌 巡检输出规范
### 1. 精简模式（无异常时/普通10分钟巡检）
```markdown
✅ 【巡检正常】${执行时间}
Coder状态：🟢 空闲/🟡 轻度繁忙/🔴 繁忙 | 活跃任务：X个
Open PR：X个 | Open Issue：X个
GPU利用率：X%
```

### 2. 异常模式（有异常时）
```markdown
⚠️ 【巡检告警】${执行时间}
━━━━━━━━━━━━━━━━━━━━━
🔴 Critical异常：X个 | 🟠 Major异常：X个 | 🟡 Warning异常：X个
异常列表：
- 任务[${任务名称}]：运行超过X分钟无更新，已终止并自动重发
- 任务[${任务名称}]：GPU利用率过低，观察中
━━━━━━━━━━━━━━━━━━━━━
✅ 处理动作：
- 已自动重发X个高优先级任务
- X个任务进入观察队列
@Kevin 请确认是否需要调整任务优先级
```

---
## ⏰ 整点汇报专项规范（强制真实性校验，零容忍虚报）
### 核心要求
整点汇报必须**100%基于实时运行时数据**，禁止任何硬编码、虚报、瞒报行为，一旦发现立即触发告警。

### 强制输出字段
整点汇报必须包含以下真实采集的信息，缺一不可：
```markdown
📊 【整点汇报】${YYYY年MM月DD日 HH:00}
━━━━━━━━━━━━━━━━━━━━━
🔹 当前Coder状态：${状态标签} | 活跃运行中任务：${ACTIVE_TASKS}个
🔹 进行中任务列表（实时采集）：
${running_tasks_list}
🔹 今日已完成任务：${今日完成数}个
🔹 Open PR：${OPEN_PR}个 | Open Issue：${OPEN_ISSUE}个
🔹 GPU平均利用率：${GPU_UTIL}%
━━━━━━━━━━━━━━━━━━━━━
✅ 后续待执行任务：
${pending_tasks_list}
```

### 真实性校验机制
```bash
# 进行中任务列表必须从实时session数据生成，禁止硬编码
running_tasks_list=$(echo "$ACTIVE_TASKS_DETAIL" | jq -r '.[] | "- 「" + .label + "」（运行中，已执行" + (now - (.startedAt / 1000) | tostring | split(".")[0]) + "分钟）"')

# 虚报判定：如果汇报的任务列表与真实运行任务不一致，立即触发告警
REPORTED_TASKS="..." # 从汇报内容提取
REAL_TASKS=$(echo "$running_tasks_list" | md5sum)
if [ "$(echo "$REPORTED_TASKS" | md5sum)" != "$REAL_TASKS" ]; then
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] 整点汇报虚报！真实任务：$running_tasks_list，汇报内容：$REPORTED_TASKS" >> logs/false-report-alerts.log
    # 立即推送告警给PM
fi
```

### 示例（正确真实版）：
```markdown
📊 【整点汇报】2026年04月04日 14:00
━━━━━━━━━━━━━━━━━━━━━
🔹 当前Coder状态：🟢 空闲 | 活跃运行中任务：0个
🔹 进行中任务列表：
无
🔹 今日已完成任务：2个（T005报告生成预览、B004空间分析引擎MVP）
🔹 Open PR：0个 | Open Issue：3个
🔹 GPU平均利用率：2%
━━━━━━━━━━━━━━━━━━━━━
✅ 后续待执行任务：
- B004空间分析引擎完整版本开发
- 前端react-map-gl构建问题修复
```

### 禁止行为（虚报典型案例）：
❌ 禁止虚报不存在的进行中任务，例如："当前进行中任务：T005 报告生成预览功能开发"（实际该任务早已完成）
❌ 禁止隐瞒运行中异常任务
❌ 禁止伪造任务完成数量
---
## 🔧 实施要求
### 基础执行要求
1. 所有巡检任务必须严格按照本标准执行，禁止自定义判定逻辑
2. 巡检日志保存30天，便于回溯问题
3. 每周一00:00自动生成巡检周报，统计异常发生率、任务完成率等核心指标
4. 本标准自动生效，无需人工触发

### 虚假工作防范保障
1. **零容忍机制**：发现一次虚假工作立即优化巡检逻辑，连续3次自动降低巡检频率
2. **资源消耗监控**：巡检token消耗超过阈值（单次>5k token）自动触发审核，排查是否存在无意义文档读取
3. **产出考核**：巡检有效性必须>90%，低于阈值自动重构巡检流程

### 缓存机制初始化
首次执行前创建缓存目录和文件：
```bash
mkdir -p .cache
echo '{}' > .cache/file_mtimes.json
```
