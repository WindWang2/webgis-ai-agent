# 05 — Review Findings

## Round 1（架构/正确性/回归）— SHIP-WITH-FIXES，全部修复（commit 93cd6e69→6e986c20 前）

| 严重度 | 发现 | 修复 |
|---|---|---|
| CRITICAL | worker stderr 抽取无界阻塞 read：关 stdout 存活的 worker 永久挂起持锁宿主线程 | killpg→有界 wait→select+os.read 非阻塞抽取（client.py `_stderr_tail`），+真实子进程回归 |
| CRITICAL | model provider invoke wrapper 与声明 schema 派发约定不符：扁平 kwargs 被静默丢弃 → 空 request 似是而非结果 | wrapper 规约「单 request 包膜 或 扁平 kwargs 即请求」（context/worker context 双侧）|
| MAJOR | `_activate_worker` 无兜底：spawn OSError 裸抛 + 记录卡 LOADING（重试 AssertionError）| activate worker 分支 catch-all → `_fail_worker_activation` |
| MAJOR | `invoke_model_provider` worker 路径绕过崩溃隔离 | 接 `_on_worker_death`（回滚+计数+隔离）|
| MINOR | 崩溃计数非「连续」；disable 带病置 DISABLED；worker 缺 satisfied_dependencies；network allow host:port 永不命中；`<pid>_invoke` 跨节碰撞；certify「只读」矛盾 | 优雅 deactivate 清零 / 被拒中止 / 补齐 / 规约+原始形态校验 / manifest 解析期拒绝 / CLI+docs 披露 |

## Round 2（性能/安全/UX/可维护性）— SHIP-WITH-FIXES，全部修复（commit 6e986c20）

| 严重度 | 发现 | 修复 |
|---|---|---|
| CRITICAL | worker 经 `app.core.config.settings` + pydantic-settings `.env`（CWD 相对解析）一次读走宿主全部 secrets（实测证实）| spawn 注入 `WEBGIS_EXTENSION_WORKER=1` → Settings 短路 env_file；cwd 移出 repo root；安全边界文档同步；真实子进程钉死测试 |
| MAJOR | broker artifact_read 整文件 read_bytes 后才 cap：GB 级 artifact 打爆宿主内存 | stat 先行 + cap+1 有界读取 |
| MAJOR | broker network_request httpx 整响应缓冲后才 cap | 真实 httpx 走 stream 在 cap 中止 |
| MINOR | timeout_s 负/NaN → 不透明 internal 失败；broker 应答写失败逃逸崩溃路径；worker_result_invalid 文档行失实；崩溃计数清零事件文档缺失；双重序列化输出检查；未收口死代码 | typed deny / 归一 crashed / 文档修正 / limitations 补充 / encode 一次直写（保留 _check_output_size 语义）/ 清理 |

已记录 ride-along NOTES（resolver tie-break 文档语义、load_sibling 双侧重复、broker 审计带 hostname 等）按风险评估随修或列为 follow-up。
