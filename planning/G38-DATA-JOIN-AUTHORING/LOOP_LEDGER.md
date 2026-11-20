# G38 LOOP_LEDGER — 实现循环记录

## L1 后端核心（WP02–WP09）

- 键/计划/执行器首版 → 属性路径 oracle 测试驱动修 3 处：默认投影不得含 `_wfid`
  （身份列自动输出）；count 聚合缺省名与系统 match_count 同义（合一，不另起列）；
  J09 夹具键不匹配（测试侧修正）。
- 空间 oracle 校准：P3(10,5) 同时是 R1 边界与 R2 内部（fixture 认知错误）→
  增设独立 R4/P7 边界夹具，P6 移出面外；shapely contains/covers 实测核对后
  修 4 处断言。
- aggregate 策略对单命中也产出聚合列（列恒存在，否则行间 schema 漂移）。
- 执行体移入 `asyncio.to_thread`（长关联不阻塞看门狗/事件循环 —— 自查发现）。
- OperationCancelled 误入 failed 分支 → 独立分支落 cancelled（J20 由此转绿）。

## L2 服务/幂等（WP09）

- J28 暴露：run 幂等键唯一约束阻碍失效后重执行 → 去唯一约束改 service 层收敛
  （活跃复用 + 最新收据短路 + 世代键 `:gN` 绕开终态 job 键占据）。
- G18 `SourceResolutionError` 属性名（error_code/reason 非 code/message）。
- UNAUTHORIZED 码匹配大小写（`code.upper().endswith("UNAUTHORIZED")`）。

## L3 Pi 旅程（WP10/WP12）—— 5 轮

1. stub JS 复制笔误 `(b).text` → 崩溃；2. 漏设 `WEBGIS_API_BASE`（Pi 回调
   fetch failed）；3. 能力严格闸：`data_join_authoring` 未登记 CapabilityRegistry
   → app 启动失败（登记后过）；4. registry 透明 ref 解引用把 source.ref 展开
   成整份 FC → skip_keys 白名单（G30 bindings 先例）；5. 驱动程序在「建计划
   turn 之前」读 plan_id（顺序 bug，曾误判为 DB 可见性灵异 —— 用
   engine/settings/raw-sqlite 三方取证后定位为读早了）+ G18 create 需 JWT
   用户（种子用户+editor 角色，CHECK 词表）+ 源 CRS 证据通道
   （properties.schema_info.crs —— G18 拒绝默认 4326 是正确行为，输出侧如实
   标注权威 CRS）。15 门全绿。

## L4 质量闸

- 迁移领号：.alloc.json 段位协议（check 只认 segments）。
- 派生工件电池：gen 依赖序 + REALTIME_SNAPSHOT_UPDATE + staleness --update。
- 5 项 master 既有失败以干净 base worktree 复现留证（见 TEST_MATRIX）。

## 环境事件

- 共享主仓 .venv 被外部进程破坏（pytest 消失）→ 自建 `.venv-g38`（uv，锁文件安装）。
- vendor/pi 子模块 GitHub 克隆网络失败 → 从主仓本地 repo `--shared` 克隆并检出
  pin b4afdddf（构建产物仅本地验证用，不入提交）。
