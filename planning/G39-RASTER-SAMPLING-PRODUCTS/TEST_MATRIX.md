# G39 TEST_MATRIX — 场景 → 证据映射（2026-11-15 实测）

执行环境：worktree venv（requirements.lock 解析 + pyarrow 26.0.1）；前端 pnpm node_modules + vitest；真实 Pi 子进程（vendor pin b4afdddf44，build-vendor-pi.sh）。

## 后端套件 `tests/raster_sampling/`（53 passed）

| 文件 | 覆盖场景 |
|---|---|
| test_stations_oracle.py (12) | S01(点身份/重复披露)、S13(重复顶点/零长/短于步长)、S14(part 独立 chainage+断口)、S15(测地解析 oracle、跨经线/极点拒绝、3857/度数/无 CRS 拒绝)、预算分配前拒绝 |
| test_kernel_oracle.py (13) | S04(线性梯度解析 oracle+权重和=1)、S05(floor tie 同 probe)、S06/S07(分类拒插值、dtype 非证据、未声明拒插值)、S08(nodata/masked/non_finite 分型、outside 不钳位)、S09(严格 masked_support vs renorm 0.75 解析值+insufficient)、S10(scale=0/负/offset=0 恰一次)、S16(旋转/非方形仿射反算)、S33(2^53+7 精确串) |
| test_reader_schedule.py (6) | S17(5000 点同块 opens=1、window_reads≤8、逐点解析 oracle)、S18(条带源整行记账+闸下 typed 拒绝)、S19(值单元超预算分配前拒绝)、S34(批间改写弃件)、S03(双源各自网格)、S12(band 17 band_meta 内/band 65 补读，含 resolve_source bands_truncated 披露) |
| test_service_journeys.py (12) | S01(全旅程：预览→执行→收据→成果→站位层)、S02(三波段值/单位/换算域)、S03、S20(源改写 STALE)、S21(样式变化幂等复用同收据)、S23(并发 gather 收敛 1 收据)、S26(他人会话 PLAN_NOT_FOUND/foreign ref 创建即拒)、S28(status/results 恢复+分页)、S32(GC→RESULTS_EXPIRED+历史收据可读)、S06、S04、S31(中部改写拒绝) |
| test_api_routes.py (5) | HTTP 全流程（describe/plan/preview[stale 409]/execute/status/results/plans/cancel[409]）、404/422 typed、**S29（G18 真实 durable 交付 job → CSV → 全行回读含单位/无效原因列）** |
| test_publish_and_tools.py (5) | S27(抽稀保端点/缺口/极值+方法披露)、part 断口 gap 标记、工具注册合同（tier/network/side_effect 词表）、参数签名 |

## Pi 真实旅程（S30）

`tests/integration/test_pi_g39_raster_sampling_journey.py` — **1 passed (22s)**：
node stub LLM → uvicorn 生产 app（真实 vendor/pi bridge + webgis-tools 扩展）→ 4 turns（plan/preview/execute/results，每轮 tool_search 激活）→ 裁决：
- plan_receipt（typed 参数 → 真实 dispatch → 计划 + 站位摘要）
- preview_bounded / execute_completed（completed=true + 长表 ref + 站位层 ref）
- results_rows（分页行 + coverage）
- server_receipt_check（服务端：parquet 存在 + 站位层 5 要素 + coverage=5/5 —— 真实成果非 transcript）
门控 PI_JOURNEY_E2E=1 + node + vendor build（显式 opt-in，绝不静默绿）。

## 前端 `raster-sampling-panel.test.tsx`（8 passed）+ tsc --noEmit 绿

describe 列表、全流程（预览记账 66KB/覆盖率 2/3 → 执行 → sample-table-ref → 行）、曲线（缺口断线=1 circle、抽稀披露）、hover 同 sample_id（曲线点亮 r=3.5 橙色）、行点击 setViewport([x,y],13)、站位层 addLayer/fetchRefGeoJSON/updateLayer、下载 dialog(fabric_parquet, session, ref)、错误 toast。

## 邻域回归（全部绿）

- raster_authoring（probe/描述子/瓦片/导出）+ science_oracles terrain/probe/sample：513 passed
- data_operations + jobs：247 passed；delivery：163 passed（1 失败 `test_fabric_parquet_fast_path` 为**干净 base 127669273 同败**——预存在，与本分支无关，证据见 LOOP_LEDGER）
- tooling audit（registry ratchet）：8 passed
- 前端 i18n key-completeness + data-sources-tab：16 passed

## 未运行/已知限制（如实）

- S24（原生读取中取消+迟到 attempt）—— 取消链经 G23 runner 的 watchdog/attempt fencing（runner.py 语义已被 tests/data_operations/test_runner.py 覆盖）；G39 侧批间 checkpoint 取消语义由 S34 测试的 checkpoint 路径间接覆盖；未做专门 Pi 级取消旅程（记为未运行）。
- 真实浏览器（Playwright）面板 E2E 未运行（vitest 组件级覆盖；浏览器层标未测）。
- S17 的内存峰值未做 RSS 级测量（以窗口字节记账 + 预算闸为证据；峰值测量标未运行）。
