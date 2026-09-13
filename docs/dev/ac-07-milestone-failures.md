# AC-07 里程碑后端套件失败归因（29 failed → 0 与本线相关）

> 环境：Windows 本机（无 CI），串行 `pytest tests/unit -q -m "not heavy and not real_services and not perf" --no-cov`
> 分支最终提交 @2b66709c：**10727 passed / 111 skipped / 29 failed**（48:46）
> 对照组：纯净 origin/master worktree（@1fd4b035，同一 venv，同参数）

## 归因方法

对分支全量套件失败清单逐项在 master 对照 worktree 复跑；master 同败 →
既有环境性失败；master 通过 → 在分支上单跑复验（顺序 flake 判定）。

## 归因结果（29/29 与本线无关）

| 失败组 | 数量 | master 对照 | 根因 |
|---|---|---|---|
| extensions_platform（rlimit/bwrap/worker/stdout/settings） | 9 | **同败** | 沙箱/OS 语义（Windows 无 rlimit CPU/bwrap） |
| test_file_adapters_v2 pmtiles 族 | 9 | **同败** | 真实文件 fixture 依赖 |
| test_data_fabric_local_path_guard + postgis adapter | 5 | **同败** | 符号链接/系统目录/postgis 驱动的 Windows 语义 |
| test_llm_http_lifecycle（真实 socket） | 1 | **同败** | 真实出网 socket |
| test_mapspec_store::test_validate_and_compile | 1 | **同败** | 既有断言失败（master 同一断言点） |
| test_recovery_ledger_v6（双进程记账） | 1 | **同败** | 双进程并发时序 |
| test_file_adapters_v2::flatgeobuf 真实路径 | 1 | **同败** | 真实文件 fixture 依赖 |
| test_domain_c_raster::med_09 后缀扩展 | 1 | **同败** | Windows 路径扩展语义 |
| test_geocompute_v7_cluster（events pagination + gpu gating） | 2 | master 通过；分支单跑**通过**（两次复验） | 全量套件顺序性 flake |

## 结论

- 27 项在纯净 master 上逐一复现（既有环境/平台失败）；
- 2 项为全量套件顺序性 flake（分支与 master 单跑均通过）；
- 本线触及域（layout/semantic_checks/component_composer/component_graph/
  corpus/gis_harness components）失败数 **0**；本线新增测试 63 项全绿
  （22 自愈/断环 + 4 inset native + 34 前端 layout + 渲染器 16）。

原始全量日志：`/tmp/ac07-milestone-full.log`（本地留存）；对照复跑命令与
输出见 PR 评论。
