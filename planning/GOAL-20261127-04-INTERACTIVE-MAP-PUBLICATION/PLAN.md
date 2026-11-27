# PLAN — 04 线·交互式只读专题地图发布闭环

## 目标（一句话）

让一张已验证的 MapSpec 形成面向读者的**版本化、可授权、可撤销、交互式只读 WebGIS 专题地图**：读者无编辑工作台/Agent 会话权限，凭作者显式签发的只读链接即可缩放/筛选/查看图例与统计口径；无 WebGL/键盘/读屏仍可取得等价专题信息；默认 private，发布与链接签发均为作者显式操作。

## 架构总览

```
作者端（工作台 Publication 面板）          Pi Agent（工具族，只读建议）
        │  owner auth                          │  /pi-tools/execute（bridge secret）
        ▼                                      ▼
   app/api/routes/publications.py  ←── 共用 ──→  app/tools/publication_tools.py
        │                                     （inspect/prepare/preview/validate；无发布权）
        ▼
   app/services/publication/（唯一领域服务层）
     contract.py   发布快照契约（typed，v1，fingerprint= digest_of）
     projection.py 有界 release projection（字段白名单/几何精度/bbox/预算）
     store.py      DB(MapPublication) + data/publications/{pid}/ 产物目录
     access.py     share_code 签发/校验/撤销/过期（fail-closed，常量时间比较）
     service.py    prepare/preview/publish/revoke/link 生命周期 + 幂等
        │
        ├─ 复用：mapspec store(fingerprint/revision) · accessibility_manifest(接线)
        ├─ 复用：rate_limiter · signing · report 分享范式（token_urlsafe+naive UTC）
        └─ durable job：materialize 阶段挂 submit_durable_job（atlas_batch 范式）
        ▼
读者端 /p/[code]（Next.js 公开页，无登录）
   reader-api.ts → 公开只读路由（share_code + 限流 + CSP 头 + 字段投影）
   compileMapSpec(冻结 spec) → 只读 MapLibre（显隐/图例/弹窗/白名单筛选/统计）
   等价信息：可访问数据表（分页有界）+ accessibility manifest 展示
```

## 阶段计划（每阶段小步 commit）

### P0 台账与基线（本文件）— 完成证据：BASELINE/DEDUP_MATRIX/PLAN 提交
### P1 WP1 发布快照契约与模型
- 新建 `app/models/map_publication.py`（MapPublication 表：product_id 稳定产品 ID、version 递增、
  status draft→published→revoked/expired CHECK、access_policy private|link、share_code 唯一、
  share_expires_at naive UTC、spec_fingerprint、spec_revision、snapshot JSON（冻结 MapSpec doc +
  白名单 + license/attribution/purpose + accessibility_manifest + qa_summary）、归属三元组
  creator_id/org_id/owner_token（ADR-0052 范式））
- 迁移 `g5202_map_publications.py`，down_revision=`aa356ce2fcdc`（master 单 head），batch_alter/sqlite 兼容
- `app/services/publication/contract.py`：PublicationSnapshotDocument v1 + typed blockers +
  PublishPlan；禁止字段（会话历史/私有 ref/owner_token/provider key/绝对路径）显式排除表
- 验收：契约单测 + 迁移 single-head + 禁字段负例
### P2 WP2 有界投影与发布数据面
- `projection.py`：字段白名单裁剪、几何精度钳制、bbox 裁剪、总预算（typed 超限降级/阻断）、
  远程 source 只记投影引用不复制；写时投影 + 读时二次过滤（纵深防御）
- `store.py`：产物目录 `data/publications/{publication_id}/layers/{layer_id}.geojson` +
  snapshot.json；`access.py`：share_code（token_urlsafe(12) 范式）、过期（naive UTC）、撤销、
  限流键 `publication-read:{ip}`
- 路由（作者侧 auth + 读者侧匿名限流）：
  - 作者：POST /publications/preview、POST /publications（草稿）、POST /{id}/publish（显式确认令牌）、
    POST /{id}/link、DELETE /{id}/link、POST /{id}/revoke、GET /{id}、GET /sessions/{sid}/publications
  - 读者：GET /publications/shared/{code}（元数据+等价信息）、/shared/{code}/map（冻结 reader spec）、
    /shared/{code}/layers/{lid}/data、/shared/{code}/layers/{lid}/features（分页/筛选/白名单）、
    /shared/{code}/summary（显式分母）
- durable job：物化走 submit_durable_job（kind 加性扩展），幂等键=publication_id+version
- 验收：跨 owner/未发布/撤销/过期/错版本/白名单外字段负例全绿
### P3 WP3 读者 MapSpec 与前端只读阅读器
- `frontend/lib/publication/reader-api.ts`（skipAuth）+ `reader-store.ts`（局部状态，不回写）
- `frontend/app/p/[code]/page.tsx`：compileMapSpec(冻结 spec) → 只读 MapLibre；显隐（获准层）、
  图例、要素弹窗（白名单字段）、白名单内筛选、summary（显式分母）、禁用交互解释文案
- 无 WebGL/断网/撤销/过期状态真实呈现；小屏布局
- 验收：vitest 组件/逻辑测试 + 真实浏览器 journey
### P4 WP4 可访问等价信息
- accessibility_manifest 接线：publish/preview 时生成入库；reader doc 携带
- reader 页：可访问数据表（分页≤200 行/页）、键盘焦点顺序、aria 播报（筛选/选择）、
  色觉检验结果如实展示（checked=false 如实标注）、非图形等价读取
- 验收：后端 manifest 契约测试 + 前端 a11y 结构测试；人工键盘流程记录
### P5 WP5 Pi 工具与作者端 UI
- `app/tools/publication_tools.py`：publication_inspect / publication_prepare /
  publication_preview / publication_validate（全只读，tier≤2，无发布权）；
  `_TOOL_MODULES` 注册；契约 tier 测试跟随
- 作者端：`frontend/components/workbench/publication-panel.tsx`（复用 map-studio/export 入口惯例，
  LeftTab/RAIL_GROUPS 最小接线）；预览→发布确认→链接签发→撤销/版本历史
- 验收：Pi 工具注册契约 + RPC 真实调用（本地受控 provider）；作者 UI journey
### P6 WP6 安全/性能验证 + 独立 R1/R2
- 负例矩阵（DEDUP WP6 表）全绿；与 report share 权限回归同测
- 性能对比：发布物化 p50/p95、读者首屏、features 分页延迟（同 fixture 基线 vs 改后）
- R1 独立审查（subagent C 或主 agent 隔离上下文逐行）→ 修复 → R2 复查
- 必过旅程 1-4 真实跑通（合成区县学校数据）
### P7 交付
- 重新 fetch/核 master/开放 PR/冲突面；`scripts/ci-local.sh` 相关门禁；分模块 commit；
  推分支 + `gh pr create --base master`

## 每阶段验收通用门禁

- ruff/mypy（改动文件）；pytest 相关子集；迁移 single-head；OpenAPI/scope-matrix 登记
- 新增产物不泄露：owner_token、session 内部路径、私有字段、provider key
- 性能数字具体化（不虚设门槛）
