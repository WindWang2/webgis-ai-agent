# TEST_MATRIX · G35（24 个必须覆盖场景 → 真实测试）

| 场景 | 测试 | 层级 | 结果 |
|---|---|---|---|
| 1 五个真实观测各 1 秒 | `test_planning::test_scenario_1…` + `test_pipeline::test_scenario_1_real_apng_decode_and_evidence` | 计划 + 真渲染/编码/解码 | ✅ 5 观测 / 5 容器帧 / 5000 ms / `total_duration_ms=5000` |
| 2 只有 2019/2021/2024 | `test_planning::test_scenario_2…` | 计划 | ✅ 缺期 2020/2022/2023 明示、不产生假观测、跨度披露 |
| 3 按真实时间间隔 | `test_planning::test_scenario_3…` / `…3b` | 计划 | ✅ 2:3 比值 ±0.02；超 10 年压缩披露 |
| 4 缺期沿用画面 | `test_planning::test_scenario_4…` / `4b` | 计划 | ✅ `held_from` + 标签含「沿用 2019 观测」、身份非观测 |
| 5 相邻桶边界记录 | `test_pipeline::test_scenario_5_half_open_boundary_only_one_bucket` | 真渲染 + oracle | ✅ `boundary-2020` 只在 2020 帧 |
| 6 跨时区日界/DST | `test_pipeline::test_scenario_6_dst_day_boundaries_use_contract_timezone` | 真渲染 | ✅ America/New_York 2024-03-09/10/11，预览/最终窗口一致 |
| 7 epoch_s / epoch_ms / date_string | `test_pipeline::test_scenario_7_representation_is_not_guessed` (3 参数) | 真渲染 | ✅ 按绑定表示解释；未知表示 build_plan 拒绝 |
| 8 倒序播放 | `test_planning::test_scenario_8…` | 计划 | ✅ 源时间不变、方向显式、extent 不变 |
| 9 相邻两观测画面相同 | `test_planning::test_scenario_9…` + `test_pipeline::test_scenario_9_identical_pictures…` | 计划 + 真编码 | ✅ 3 观测身份保留，容器按 PNG 字节合并，manifest 映射完整、总时长 3000 |
| 10 一段停留编码多相同帧 | `test_pipeline::test_scenario_10…` + `test_faults::test_resource_measurement…` | 真编码 | ✅ 20 容器帧 / 观测仍 5 / 唯一渲染帧 5 / 总 5000 ms |
| 11 不同年份值域变化 | `test_faults::test_scenario_11_fixed_style…` | 真渲染 | ✅ style 指纹贯穿全部帧，不逐帧重算色域 |
| 12 任务中平移/换样式 | `test_faults::test_scenario_12…` + `test_jobs::test_scenario_17_source_change_before_publish_aborts` | 冻结 + 发布前复检 | ✅ 冻结文档深拷免疫 live 变更；修订变化 → 中止且不发布 |
| 13 两个时间绑定图层 | `test_pipeline::test_scenario_13_two_temporal_layers_are_both_filtered` | 真渲染 | ✅ 两层各自过滤、每层 feature_ids 与独立 oracle 一致 |
| 14 MVT 只有当前瓦片样本 | `test_faults::test_scenario_14…` | 支持矩阵 | ✅ `mvt_tile_sample_not_full_extent` → `BLOCKED_INCOMPLETE` |
| 15 静态数值栅格背景+矢量时序 | `test_faults::test_scenario_15…` | 支持矩阵 + G26 绑定 | ✅ `frozen_static_raster` + 冻结 binding 进计划；`partial=false` |
| 16 不支持时变栅格/外部底图 | `test_planning::test_blocking_policy…` + `test_faults::test_scenario_16…` | 计划 + 编译文档 | ✅ 默认 409；显式 partial 才移除并披露，不暗中掉层 |
| 17 源在第 N 帧变化 | `test_jobs::test_scenario_17…` | runner | ✅ `SOURCE_CHANGED_MID_RENDER`，failed 且无成果、临时目录清理 |
| 18 编码退出 0 但文件损坏/时长错 | `test_pipeline::test_scenario_18_encoder_exit…` / `…wrong_total_duration` | 独立解码 | ✅ checksum / duration 双红检 |
| 19 编码中取消 + worker 迟到 | `test_jobs::test_scenario_19…` + `test_faults::test_scenario_19_cancel_latency…` | runner + store | ✅ cancelled 保持、迟到成功不覆盖、成果不登记、临时清理、延迟 <5s |
| 20 重启后恢复 | `test_jobs::test_scenario_20_recovery…` | 缓存恢复 | ✅ 同指纹 5/5 命中、0 重渲染；换计划后 0 命中 |
| 21 两 owner 相同计划 | `test_jobs::test_scenario_21…` + `test_routes::test_owner_scope…` | owner 隔离 | ✅ 状态/计划/缓存/成果 `.owner` 均隔离 |
| 22 UI/Pi 同一计划、pending 不冒充完成 | `test_jobs::test_service_preview_submit_status_result_chain` / `…pi_tool_dispatch…` / `test_routes::…` / Pi 旅程 | service + dispatch + HTTP + 真 Pi | ✅ 同一 plan_hash/ref；`RESULT_NOT_READY`；Pi 旅程 `pending_not_upgraded=true` |
| 23 标签提前一帧 / 全片首帧 / 删除变化帧 | `test_pipeline::test_scenario_23_fault_*` (3) | 内容 oracle | ✅ 三类故障全部红检（不只比较 JSON 计划） |
| 24 接近硬帽长片/大画面 | `test_planning::test_scenario_24…` + `test_faults::test_scenario_24…` + `test_faults::test_resource_measurement…` | 预检 + 实测 | ✅ 帧数/像素/磁盘预检拒绝；记录唯一帧 5、编码帧 20、解码像素 20×480×270、RSS、磁盘 |

## 额外覆盖

- 关闭词表：未知 format、任意 shell/路径字段 → 422（`test_routes`）。
- 未知/漂移计划版本：`PLAN_VERSION_MISMATCH`（`test_jobs`, `test_routes`）。
- 视频 codec probe：部署 flag 关闭时 `mp4/webm available=false` 且有 reason（`test_jobs`）。
- 重复提交幂等：复用同一 job，不产生孤儿 pending（`test_jobs`）。
- 帧文件损坏 / 单层过滤未生效：renderer 的 `escaped` oracle 与 verifier checksum 双闸。
- i18n 双 locale 键完整性、前端命令目录契约：`vitest` 14 例通过。

## 套件与真实执行结果（本地，未等 CI）

| 套件 | 命令要点 | 结果 |
|---|---|---|
| 后端 G35 单测 | `pytest tests/unit/temporal_media -q --no-cov`（单进程，OMP=1） | **61 passed** |
| Pi 全链旅程 | `PI_JOURNEY_E2E=1 pytest tests/integration/test_pi_g35_temporal_media_journey.py`（heavy 锁） | **1 passed**，`verdict.fail=[]` |
| 既有 Pi surface / 导出鉴权回归（10 文件） | `pytest tests/unit/test_pi_* … tests/test_map_*.py` | **120 passed** |
| 前端 vitest（5 文件） | `vitest run … --maxWorkers=1` | **35 passed** |
| 前端 typecheck | `tsc --noEmit -p tsconfig.json` | **0 error** |
| ruff | `ruff check app/services/temporal_media …` | **All checks passed** |
| compileall / `git diff --check` | — | 通过 |

### 独立 reviewer 指定的 3 个补测（均已实现并通过）

- **T1** `test_T1_frozen_geographic_window_is_fixed_across_all_frames`：spy 记录 5 帧编译的
  `bounds`，断言全部等于冻结 extent；`test_T1b_layout_frame_extent_takes_precedence` 对照组。
- **T2** `test_T2_gif_merges_identical_frames_with_explicit_segment_mapping` +
  `test_T2b_gif_with_fps_is_typed_rejected_before_any_work`：GIF 合并帧真实解码回读与
  typed 拒绝。
- **T3** `test_T3_g35_tools_follow_production_deferred_surface_contract`：断言
  `default_active == NATIVE_TOOL_NAMES`（既有契约不破坏）、6 工具在超集、deferred、
  带完整 schema；旅程删除 dump 改写后仍 `verdict.fail=[]` 且 `deferred_discovery=true`。
- 附加：30 轮 cancel/finalize 并发竞态、派发失败补偿、held 窗口注记、自动重预览、
  按 created_at 恢复、GIF 折叠分支真实触发（U2）、子集勾选→预览→提交保留（U1）。

## 可复核的命令与文件清单（R2 U8 证据）

```bash
# 后端（单进程；OMP/OPENBLAS/MKL=1）
pytest tests/unit/temporal_media -q -p no:cacheprovider --no-cov   # 61 passed
# 五个文件：test_planning.py / test_pipeline.py / test_jobs.py /
#           test_routes.py / test_faults.py（+ __init__.py / conftest.py）

# 固定 pin 真实 Pi 子进程（显式 opt-in；公共 heavy-stage 锁）
PI_JOURNEY_E2E=1 pytest tests/integration/test_pi_g35_temporal_media_journey.py -q --no-cov
# 1 passed；/tmp/pi-g35-journey-*/verdict.json 记录 fail=[] 与全部 journey 布尔值

# 既有回归（10 个 pre-existing 文件，共享 surface / 导出鉴权契约）
pytest tests/unit/test_pi_native_surface.py tests/unit/test_pi_surface_parity.py \
  tests/unit/test_pi_dynamic_surface.py tests/unit/test_pi_surface_receipt.py \
  tests/unit/test_pi_surface_budget.py tests/unit/test_pi_tool_spec.py \
  tests/unit/test_pi_abi_adversarial.py tests/unit/test_pi_tools_route_auth.py \
  tests/test_map_api.py tests/test_map_export_download_auth.py -q --no-cov  # 120 passed

# 前端（1 worker）
cd frontend && vitest run lib/api/temporal-media.test.ts \
  components/map/temporal-media-panel.test.tsx \
  components/map/temporal-time-controller.test.tsx \
  test/i18n/key-completeness.test.ts \
  lib/map-commands/catalogue-contract.test.ts --maxWorkers=1            # 35 passed
# 计数口径（均以 vitest 实际输出为准）：
#   14 = i18n 键完整性 + 命令目录契约（两个 pre-existing 文件）
#   18 = L5 阶段中间态：面板(4) + API(4) + 时间轴(10)
#   35 = 最终五文件合计：面板(7) + API(4) + 时间轴(10) + i18n/命令目录(14)
# 前端 typecheck：tsc --noEmit -p tsconfig.json → 0 error
# 后端静态：ruff check（新增域/工具/路由/测试）→ All checks passed
```

R2 是独立 reviewer 的**静态冷读**（未运行测试）；上述 61 / 1 / 120 / 35 / tsc / ruff
是作者在 R2 的 U1–U7 修复后真实复跑的输出，不是 R2 现场复跑。

## 合流最新 master 后的复跑（2026-11-13）

创建时 base `ca7b3ec27` → 提交前 `git fetch origin master` 得最新 base
`ba0c84b84`（G29–G34 / #2094–#2099 共 36 个提交已合并，open PR=0，
六个新 PR 的 reviews/inline/conversation 均为空）。普通 `git merge origin/master`：
两处同锚接线冲突（`app/services/jobs/lifecycle.py` 的 `JobKind`、
`app/tools/__init__.py` 的 `_TOOL_MODULES`）双侧保留（G30 `skill_job`、
G34 `print_layout_tools` 与 G35 项共存），无冲突标记残留。

| 合流后套件 | 结果 |
|---|---|
| 后端 G35 单测 | **61 passed** |
| 固定 pin Pi 全链旅程（heavy 锁） | **1 passed**，`verdict.fail=[]` |
| 既有 Pi surface / 导出鉴权回归（10 文件） | **120 passed** |
| 前端 vitest（5 文件） | **34 passed / 35**（1 例基线失败，见下） |
| `tsc --noEmit` / `ruff check` / `git diff --check` | 0 error / 全通过 / CLEAN |
| 编译器接口回查 | `compile_mapspec_to_svg_detailed` 仍接受 `bounds` / `include_chrome` / `raster_bindings`（master 只加可选 `print_layout` 等参数）；`pi_native_surface.default_active == NATIVE_TOOL_NAMES` 不变；`index.mjs` 的 `tool_search` 发现入口仍在 |

**1 例合流基线失败（非本 PR 引入）**：`lib/map-commands/catalogue-contract.test.ts`
报幽灵命令 `steer`，来源为 master 的 G29 `app/api/routes/pi_interactions.py:252`。
本 PR 未改 `frontend/lib/map-commands/` 或任何 G29 文件；已在**同条件干净
`origin/master` 临时 worktree** 最小复现相同断言失败
（`expected [ 'steer' ] to deeply equal []`）。按「不放宽断言、不改他人文件」
保持原样并如实披露，临时 worktree 已清理。

## Pi 旅程裁决（固定 pin，真实子进程）

```
pi_vendor_head == pi_expected_pin == b4afdddf44e059c67ccf5097e5317d913521b470 (v1.5.2)
pi_entry_kind = bundle ; surface_tools = 439 ; g35_tools_registered = 6
tool_names = tool_search → capabilities → plan → submit → status → result
（生产 deferred 发现路径；surface dump 零改写，default_active_count=7）
tools_in_production_superset=true; tools_deferred_contract=true; tools_have_schema=true
deferred_discovery=true; dispatch_no_error=true; pending_not_upgraded=true; agent_settled=true
animation_download_and_decode=true (decoded_frames=3)
evidence_package_readback=true (manifest verification.passed, gaps=3, total=3000ms)
layers: pi_subprocess / production_extension / production_dispatch / domain_service 同链；
        browser_same_chain=false（UI 另测）；durable_celery_enqueue=in-process worker
```
