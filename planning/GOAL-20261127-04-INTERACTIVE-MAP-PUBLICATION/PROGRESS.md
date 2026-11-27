# PROGRESS — 04 线·交互式只读专题地图发布闭环

## 已完成（全部小步 commit）

| 阶段 | 内容 | 证据 | commit |
|---|---|---|---|
| P0 | 启动审计/去重矩阵/worktree/台账 | BASELINE/DEDUP_MATRIX/PLAN | 45d7a7a2c |
| P1 WP1 | publication_snapshot.v1 模型/迁移/契约/红线扫描 | 迁移 upgrade/downgrade 实测；契约单测 9 | 31fe9e68d |
| P2 WP2 | 有界投影/access/路由 13 端点/durable job/读者面 | 集成测试 11（旅程骨架+负例矩阵） | 500c33b07 |
| P3 WP3 | 读者页 /p/[code]（MapLibre/图例/弹窗/筛选/数据表/统计/无 WebGL） | vitest 14 + typecheck/eslint | 4bda37e2a |
| P4 WP4 | accessibility 接线与等价信息 | 测试 5（诚实性断言） | 985a949b9 |
| P5 WP5 | Pi 工具族 + 作者端发布面板 | 工具测试 4 + 前端 API 测试 4 | 23838f090 |
| J1/2 | 旅程验证修复（指纹语义分离/eager 物化线程化/meta 继承） | 真实服务旅程全绿 | 9b86c5467 |
| J3/4 | 旅程 3/4 全链 + accessibility 生产语义对齐 | 浏览器四变体 + Pi 工具链测试 | 62b747b24 |
| WP6 | 性能基准 + R1/R2（本文件 REVIEW_R1/R2） | 见下 | — |

## 必过旅程状态（全部真实跑通）

1. ✅ 作者端预览→选择公开字段/图层→显式确认发布→读者缩放/筛选/图例/统计（真实 uvicorn+sqlite+HTTP；
   读者数据与快照一致：12 学校点、无 internal_budget；无编辑/Agent 入口）
2. ✅ 编辑原图后旧版不变（spec_fingerprint 跨版本一致）、撤销后读者 404、跨 owner 403、
   白名单外字段 400、confirm 缺失 400、无许可 422
3. ✅ 桌面/窄屏(390×844)/键盘(Tab 达 tablist+Enter 激活)/无 WebGL(横幅+0 canvas+数据表 12 行+说明面板)
   真实浏览器（Playwright chromium，生产 next build）；截图见 /tmp/pub-journey/*.png
4. ✅ Pi 生产工具链（registry.dispatch 与 /pi-tools/execute 同链）：
   publication_inspect/prepare（license 阻断解释）/draft（私有）/validate（can_publish+同意边界）；
   跨会话 fail-closed；工具族无 publish/link/revoke

## 性能（旅程 fixture：12+3 要素，本机单进程）

- projection（12 要素全投影）：p50 0.025ms / p95 0.036ms
- features 分页（12 要素）：p50 0.007ms / p95 0.008ms
- 读者 HTTP 端点（真实 uvicorn）：reader_doc 6.9ms、reader_map 6.8ms、layer_data 6.9ms、
  features 7.2ms、summary 6.4ms（p50；p95 ≤9.4ms）
- 快照 JSON（v2）：10,303 字节
- 浏览器旅程筛选数学一致性：students≥500 → 5 行（服务端 where 与地图 filter 同谓词）；清除→12 行

## 已知限制（诚实披露）

- 发布投影 v1 仅矢量 FeatureCollection（geojson ref/inline）；raster/MVT/data_fabric/WxS 源
  typed 阻断并从读者面剔除（blockers 诚实下发）。
- Pi RPC 全链（bridge secret + active turn token）未在无凭证环境验证；已验证同一 dispatch 链
  （registry.dispatch + bind_runtime_context，与 /pi-tools/execute 消费的执行面相同）。
- 发布链接为逐版本（v1 不可变语义）；产品级"永远指向最新版"别名不在本期。
- master 基线既有失败（非本线）：test_endpoint_scope_matrix::test_every_route_is_in_matrix
  （331 个他线未登记端点，master 原样失败）；tsc tsconfig.test.json 的
  lib/mapspec/live-spec.test.ts 'tiles' 类型错（master 原样）；runtime manifest strict 的
  capability 悬空（journey 用文档化 GIS_MANIFEST_STRICT=0 启动）。
