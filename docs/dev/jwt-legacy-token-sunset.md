# JWT legacy token（无 type/ver claim）sunset 计划

> audit ISSUE-005（#1377 延期项）。本文是运维执行文档：三阶段推进、每阶段的
> 进入/退出判据、回滚方式，以及对应的可执行测试锚点。
> 能力（开关、拒绝路径、观测指标）已在 `app/core/auth.py` 落地；本文不改变
> 任何默认值——`JWT_REJECT_LEGACY_TOKENS` 默认 `False` 保持不变。

## 1. 背景

S41 起 JWT 携带 `type`（access/refresh）与 `ver`（对应 `User.token_version`，
bump 即 logout-everywhere）两类 claim。在此之前签发的旧 token 只有
`sub/username/role/exp`，为避免部署瞬间全员被踢（flag-day），校验路径对无
`type`/`ver` claim 的 token 做 back-compat：**视为 `type=access, ver=0`**。

代价（ISSUE-005）：`ver` 吊销对存量旧 token 失效——logout 后旧 access token
在剩余 TTL 内（旧版 access TTL 曾为 7d）仍然有效。这是一个有限窗口（部署后
最长 ~7 天自然耗尽），但需要显式关闭而非依赖遗忘。

## 2. 已落地能力（代码锚点）

| 能力 | 位置 | 行为 |
| --- | --- | --- |
| sunset 开关 | `app/core/config.py` `JWT_REJECT_LEGACY_TOKENS`（默认 `False`） | `True` 时无 type/ver claim 的 token 一律 401 |
| 拒绝/计数路径 | `app/core/auth.py` `_check_legacy_token` | 兼容模式计指标+去重告警；强制模式抛 401（detail 含 "Legacy token format no longer accepted"） |
| 覆盖的依赖 | `get_current_user` / `get_current_user_optional`（降级匿名）/ `get_current_user_with_version`（查库前拒绝）/ `authenticate_ws_token`（WS 4001） | 全部 HTTP + WS 认证入口 |
| 观测指标 | `app/core/auth_metrics.py` `auth_jwt_legacy_accepted_total` | 每接受一个 legacy token +1；随 `/metrics` 暴露 |
| 去重告警日志 | `app/core/auth.py` `_legacy_warned`（cap 10k，按 token hash 去重） | 同一 token 只 WARN 一次，附翻转提示 |

## 3. 三步走

### 阶段 1：观测期（当前态，默认 off）

- **动作**：无配置变更。部署含本能力的版本后观察：
  - `auth_jwt_legacy_accepted_total` 增速（PromQL：
    `rate(auth_jwt_legacy_accepted_total[5m])`；绝对量
    `increase(auth_jwt_legacy_accepted_total[24h])`）；
  - 应用日志中 `legacy JWT (no type/ver claims) accepted` WARN（每 token 一次，
    含 `sub=`，可定位存量来源）。
- **退出判据**：曲线归零（24h 窗口 increase == 0）并保持 ≥ 7 天（覆盖旧
  access token 的最大剩余 TTL，且不少于一个完整的 refresh 轮换周期）。
  归零前禁止进入阶段 3。
- **回滚**：默认态即回滚态，无需操作。

### 阶段 2：通知期（仍 off）

- **动作**：向运维/前端/第三方集成方公告翻转时间窗（changelog / 部署说明）：
  翻转后所有无 `type`/`ver` claim 的 token 将收到 401
  （`detail: "Legacy token format no longer accepted; please re-login"`，
  WS close 4001），客户端需重新登录获取新格式 token。
- **退出判据**：公告发布 ≥ 一个通知周期（建议 ≥ 72h）且阶段 1 判据仍满足。
- **回滚**：默认态即回滚态，无需操作。

### 阶段 3：强制期（翻转开关）

- **动作**：部署配置置 `JWT_REJECT_LEGACY_TOKENS=true`（env 注入），滚动重启。
  不改代码、不改默认值：开关只经环境变量覆盖。
- **行为变化**：无 type/ver claim 的 token 在所有认证入口一律 401（optional
  依赖降级为匿名哨兵）；`auth_jwt_legacy_accepted_total` 从此不再增长
  （拒绝走 `auth_jwt_validation_errors_total` 之外的普通 401 路径，不掺入
  accepted 曲线——该语义有测试固化）。
- **回滚**：置 `JWT_REJECT_LEGACY_TOKENS=false` 并重启，即时恢复 back-compat
  （视为 access/ver=0）。纯运行时开关：无数据迁移、无 schema 变更、无状态
  残留，回滚无副作用。
- **翻转后观察**：401 投诉应集中于"旧客户端长期未刷新"；若
  `auth_jwt_legacy_accepted_total` 在回滚后重新增长，说明仍有存量消费方，
  回到阶段 1 重新定位（日志 `sub=` 字段即来源）。

## 4. 验收测试锚点（防止能力被重构静默破坏）

| 场景 | 测试 |
| --- | --- |
| off：legacy 接受、按 ver=0、指标 +1、告警去重 | `tests/unit/test_jwt_legacy_sunset.py`（13 用例） |
| on：legacy 一律 401 / 匿名降级 / 拒绝不计 accepted 指标 / 拒绝先于查库 | 同上 |
| 新格式 token 双状态不受影响 | 同上 + `tests/test_token_refresh.py` |
| WS 入口双状态 | `tests/test_ws_auth.py` |
| `/auth/me` 集成层 back-compat | `tests/test_token_refresh.py` |

## 5. 边界与不变量

- 本计划**不翻转默认值**：合并后 `JWT_REJECT_LEGACY_TOKENS` 仍为 `False`，
  翻转是独立运维动作。
- 不改签名算法（HS256）、TTL、claim 语义——那些是独立决策。
- 双签过渡（dual-sign）方案被否决：存量旧 token 无法重签（签名即在客户端），
  重登录是唯一迁移路径，指标+开关已足够覆盖。
