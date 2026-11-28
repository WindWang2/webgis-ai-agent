# DEDUP_MATRIX — 04 线·交互式只读专题地图发布闭环

逐条对照工作包 vs master 现有实现（path:line 证据来自两个只读探查 agent + 主 agent 抽查）。
判定词：present（已存在且可用）/ partial（部分能力）/ missing（不存在）/ covered-by-other（他线/他 PR 独占）/ blocked（外部阻塞）。

## WP1 发布快照契约与可见性边界

| 能力 | 判定 | 证据 / 归属 | 剩余增量 |
|---|---|---|---|
| MapSpec 权威 schema + fingerprint/revision | present | `app/lib/cartography/mapspec_schema.py`（master LATEST_VERSION=2.2）；`app/services/mapspec/store.py:151` content-hash fingerprint | 直接消费；发布快照钉 fingerprint |
| PublicationIR（纸面出版 IR） | present | `app/lib/cartography/publication_ir.py:182`（PDF/atlas 链） | 不重写；交互发布快照是**新契约**，引用而非替代 PublicationIR |
| Report 分享（share_code/撤销/过期） | present | `app/api/routes/report.py:264-308`、`app/api/report_sharing.py:9,16`（限流+字段投影） | 复用范式；Report 表不覆盖交互地图，需新 publication 模型 |
| StoryMap（叙事回放） | present | `app/api/routes/storymap.py`、ADR-0196；全程鉴权非公开页 | 排除项，不重写；只借鉴 standalone 页骨架 |
| 交互地图发布快照（版本化、可授权、可撤销） | **missing** | grep `map_share`/`map_snapshot`/`web_map` 零命中；report share 仅静态文件 | **本线核心新建** |
| 会话解耦的内容寻址 MapSpec 副本 | partial | `app/services/mapspec/checkpoint.py`、`store.py:368`（会话作用域 revisions） | 新建发布快照存储（跨会话、发布作用域） |

## WP2 发布数据面与权限隔离

| 能力 | 判定 | 证据 | 剩余增量 |
|---|---|---|---|
| MVT/栅格瓦片/要素/聚合数据面 | present | `app/api/routes/layer.py:83,288,451,713,972`（全部 require_owned_session） | 新增 publication 授权变体（share token 校验 + 白名单投影） |
| 公开签名 URL | partial | `app/api/routes/map.py:882`（export download 签名）；`frontend/lib/map-kit/tile-auth.ts` 仅注入会话凭据 | 发布 reader 的瓦片/要素匿名凭据通道 |
| durable jobs | present | `app/services/jobs/submit.py:61`、`worker.py:332 durable_job`、`app/api/routes/jobs.py` | 发布物化 job 复用该基建，新 task_type |
| 字段白名单/空间裁剪投影 | partial | `public_report_projection`（字段过滤范式）；数据面无白名单机制 | 新建发布白名单投影（字段/几何精度/范围） |
| 数据许可/license 字段 | missing→探查中 | DataObject 模型字段待 agent-2 确认 | 若无则发布契约内置 source-level license 声明 |

## WP3 只读 MapLibre 阅读器

| 能力 | 判定 | 证据 | 剩余增量 |
|---|---|---|---|
| MapSpec 前端编译器 | present | `frontend/lib/mapspec-compiler/` `compileMapSpec(spec, profile?): MapSpecCompileResult` | 复用 |
| 图例/显隐组件 | present | `frontend/components/map/floating-legend.tsx:86`、`legend-stack.tsx:29`、`layers-tab.tsx:230` | 只读 reader 需轻量复用（不加载编辑工作台） |
| 公开只读路由 | **missing** | `frontend/app` 仅 page.tsx / story/ / geoai/，无 share/public/reader | **新建 `/p/[code]` reader 页** |
| 受限交互能力门（popup/筛选/禁编辑） | missing | story-view 是回放不是受限 viewer，无 capability gate | 新建 reader 行为规范与 store |

## WP4 可访问专题信息投影

| 能力 | 判定 | 证据 | 剩余增量 |
|---|---|---|---|
| accessibility_manifest | present 但**孤儿** | `app/lib/cartography/accessibility_manifest.py:34`（仅 tests/cartography/test_publishing.py 调用） | **接线到发布链**：发布时生成、reader 时消费 |
| 色觉检验 | present | 委托 `context_matrix.evaluate_cell`（:80-95），失败诚实 `checked=False` | 随发布快照携带真实检验结果 |
| 键盘/读屏 reader | missing | 无公开页，无从谈起 | 新建（角色/焦点/播报/数据表等价） |

## WP5 Pi 发布流程与作者端

| 能力 | 判定 | 证据 | 剩余增量 |
|---|---|---|---|
| Pi 工具注册基建 | present | registry/ToolDispatchService（agent-2 回填细节） | 新增 publication 工具族（inspect/prepare/preview/publish/revoke） |
| 作者端发布入口 | missing | 工作台无发布 UI | 新增最小作者入口（复用既有菜单模式） |
| 用户显式批准机制 | partial | `display_confirmation.py` 待 agent-2 细节 | 发布/撤销必须走显式确认 |

## WP6 验证与独立 review

| 能力 | 判定 | 证据 | 剩余增量 |
|---|---|---|---|
| 报告分享权限回归测试 | present | tests 中 report share 用例（回填具体路径） | 同测口径扩展到 publication |
| Playwright/E2E | partial | pytest.ini 提到 REQUIRE_BROWSER nightly lane | reader 交互用真实浏览器验证（browse skill 或 Playwright） |
| ci-local.sh 门禁 | present | `scripts/ci-local.sh`（ruff/mypy/debt/dep-bounds/eslint/typecheck/vitest/pytest） | 按模块跑相关子集 |

## 与他线边界（避免抢占）

- 01 线（开放 PR #2290）：Pi host/provider/model routing — 本线不触碰 vendor/pi、model routing、pi_rpc_client。
- 02 线：移动对象轨迹 — 无交集。
- 03 线：概率抽样/精度估计 — 无交集（raster_sampling/publish.py 是数据发布，本线不重写）。
- 05 线：工程包离线交换 — 本线排除；只读发布是本线独占。
- #2289 文件面板：不重写，必要时仅小范围关联入口。

## 结论

核心增量确认：**交互地图发布快照契约（WP1）、公开授权数据面（WP2）、公开只读 reader 页（WP3）、accessibility 接线（WP4）、Pi 工具与作者端（WP5）均为 missing/partial，无 covered-by-other**。可复用面充足（report 分享范式、durable jobs、MapSpec 编译器、图例组件、durable job 管线范本）。
