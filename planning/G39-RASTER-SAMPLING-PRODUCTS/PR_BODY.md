# feat(g39): 通用数值栅格点集与沿线采样成果 — 计划/预览/执行/成果工作台

## 用户问题

已有能力只能「检视」：G20 probe 一次最多 16 点 × 16 波段的即时像元读数（明示不插值、不产成果），terrain_profile 只覆盖 DEM 高程语义的折线剖面。用户无法给一批监测站持久提取 NDVI/地表温度等多波段数值、无法沿道路/管线对多个数值栅格生成可下载、可追溯、可复核的采样成果 —— 点集/线路 × 数值栅格之间缺一个完整的采样产品域。

## 行为增量

**点集/线路 + 授权数值栅格 → versioned 计划 → 有界预览 → durable 幂等执行 → 完整样本长表 + 站位点层 + 收据 → 剖面曲线/表格/地图联动 → G18 下载与恢复。**

- **站位**（`stations.py`）：点集保留每个输入点与父 feature id（重复/缺失身份披露，不去重丢站）；线路按声明 CRS 距离模式生成站位（地理 CRS = pyproj Geod 测地；米制投影 = 平面），每 part 独立 chainage、断口如实，重复顶点零额外里程；跨经线 >180°/极点/零长/度数平面 typed 拒绝。
- **采样合同**（`kernel.py`）：`source_pixel` 与 G20 probe 同口径（`~transform` floor，边界 tie=左/上）；`bilinear` 保留四支撑像元与权重（严格策略任一有权支撑无效 → `masked_support`；显式 `bilinear_renormalized` 带 `support_ratio`，低于 0.25 → `insufficient_support`）；**插值要求显式 continuous 声明**（分类/未声明一律 `METHOD_INCOMPATIBLE`，dtype 不是证据）；scale/offset **恰一次**（scale=0/负、offset=0 均按声明生效）；nodata/masked/non_finite/outside_extent/transform_failed 分型（含 GDAL mask 折叠下 nodata 优先分型的真实路径回归）；band 17+ 超出 G20 band_meta 帽时直读该波段真实头（不从 band 1 推断）；raw 整数 >2^53 走精确十进制串。
- **读取调度**（`reader_schedule.py`）：每源一次打开；站位按块分组批读（5,000 点同块 = opens 1、窗口读个位数）；预算先于分配（值单元 ≤500k、条带源按真实整行块成本闸、单窗口字节帽）；每批 checkpoint 协作取消 + stat 代次（dev/ino/mtime_ns/size）复核 —— 跨批改写（含 ABA）`SOURCE_MUTATED_DURING_READ` 弃件不发布；解码记账按真实块布局。
- **执行**（`jobs.py`，G23 runner）：幂等键 (session, plan, params_digest)；执行前绑定现行性复核（源漂移 → `SOURCE_BINDING_CHANGED`，收据短路也过闸）；并发同计划收敛单收据；取消/失败走 runner 终态语义；job completed 但收据缺席 → `recovery_required` 如实标注。
- **成果**（`publish.py`）：长表经既有 `materialize_geoparquet`（`ref:fabric-parquet/*` + 台账 + DataObject CAS）→ G18 直接 CSV/GeoParquet 交付；站位层一站一要素（波段宽列）；内容 → 回读验证（行数/schema/抽样值/要素数）→ 引用登记 → 收据发布；成果被 GC → `RESULTS_EXPIRED`（历史收据仍可读）。
- **HTTP/Pi**：`/api/v1/raster-sampling/*`（describe/plan[CAS revise]/preview/execute/status/results/cancel；POST 的 session_id 为 Query 参数与真实守卫同形）；`webgis_raster_sampling_{plan,preview,execute,status,results}` 五个 Pi 工具（typed args + ref_cursor 防透明解引用；bounded 回执；execute wait ≤60s 钳制，超时返回 job 句柄 + status 指引，queued/running 绝不称完成）。
- **工作台**（数据源 → 准备 子页）：选源/波段/语义声明/方法 → 建计划/修订 → 预览（记账披露）→ 执行（可后台）→ 分页长表 + 多单位剖面曲线（part 断口与无效站位**断线**、抽稀方法披露）+ hover 表/曲/同 sample_id 联动 + 行点击地图定位 + 站位点层经既有 layer 通道入图 + 长表经 G18 下载。中英双语文案 120 键。

## 复用与范围

复用（零重造）：G20 `resolve_source`/band_meta/预算（probe 帽不动）、G23 `submit_async_job`/`run_async_job`/attempt fencing、G18 fabric-parquet 源锚与交付、G24 handle materialize、会话 ref/BlobStore/台账。共享文件仅加性：`jobs/lifecycle.py` +JobKind 枚举 2 行（DB 列 String(20) 无 CHECK）、`tools/__init__.py` +1 行、`main.py` +2 行、`data-sources-tab.tsx` +1 挂载、`messages.ts` +namespace。无新迁移（计划/收据按 field_compute 文件先例）。明确不做：遥感指数/分类算法、LOS/体积、垂直基准转换、raster cube、新网络数据源、G09/G21。

## 实际验证（全部真实执行）

- `tests/raster_sampling/` **57 passed**：站位/内核解析 oracle（不用被测函数生成期望）、调度（S17 5000 点同块 opens=1、S18 条带整行记账+闸、S34 批间改写弃件、S12 band17/65 补读）、服务旅程（S01/S02/S03/S20/S21/S23/S26/S28/S31/S32）、API 全流程 + 真实守卫签名（缺 query 422、body 不携带会话身份）、分类端到端回归（修复前红后绿）。
- **真实 Pi 子进程旅程 1 passed**（`PI_JOURNEY_E2E=1`，node stub LLM → uvicorn 生产 app → vendor/pi bridge → 工具扩展）：plan→preview→execute→results 四轮 + 服务端 parquet/站位层/coverage 核对（S30）。
- **G18 回读（S29）**：长表 ref 经 data_delivery 真实 durable job 交付 CSV → 全行回读（行数/父 id/值/单位/无效原因列一致）。
- 前端 vitest 18 passed（面板 8 + i18n 完整性）+ `tsc --noEmit` exit 0。
- 邻域回归：raster_authoring + data_operations 107 passed；jobs 247；delivery 163（1 失败 `test_fabric_parquet_fast_path` 在**干净 base 同样失败**，预存在非本分支引入）；registry ratchet 8。
- 未运行/如实标注：真实浏览器 Playwright 面板 E2E（组件级覆盖）、Pi 级取消旅程、RSS 峰值测量（TEST_MATRIX 记录）。

## 独立评审（两轮，planning/G39-RASTER-SAMPLING-PRODUCTS/REVIEW.md）

第一轮发现 2×P0（POST session_id 布线：真实调用者 422 + body 会话身份未校验的跨会话漏洞）与 2×P2（无效原因分类在真实路径塌缩成 masked —— 根因是 facts.nodata 从未填充；execute wait 无钳制）及 7×P3。全部修复（含根因修复 nodata descriptor 回退 + 端到端分类回归）。第二轮评审者以**零 override 真实守卫 + 双会话数据库 + 真实栅格（含 GDAL mask band 洞）** 复检全部修复并复跑全部套件 → **APPROVED，无阻塞**；遗留 P3 卫生项已清理。

## 兼容/回退

- base = 1276692737（执行时 origin/master）；开放 PR #2117(G37)/#2118(G36) 仅与 `tools/__init__.py`、`main.py` 的加性行相邻，合流无文件冲突。
- 回退：无迁移/无共享行为改动；revert 本 PR 即完整回退（fabric 工件与会话 ref 随会话生命周期清理）。

## 工件

- planning/G39-RASTER-SAMPLING-PRODUCTS/{BASELINE,DEDUP_MATRIX,PLAN,INTERFACES,TEST_MATRIX,LOOP_LEDGER,REVIEW,PR_HANDOFF}.md
