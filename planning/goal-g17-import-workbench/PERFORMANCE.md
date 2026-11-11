# G17 性能实测（本地单机证据）

- 机器：kevin / Windows 11 / AMD64 Family 25 Model 116 Stepping 1, AuthenticAMD
- Python：3.13.9
- 内存口径：tracemalloc（仅解释器分配，不含 GDAL 内部堆）
- fixture 生成：290k 点 GeoJSON = 45.4 MB（1.5s）
- A 单大文件（45.4MB）：创建+发现 3.18s（峰值 1MB）｜计划 24ms｜预览200行 1.39s｜提交 216.96s（峰值 553MB）
  提交结果：completed，输出 290000 行 / 拒绝 0，elapsed_ms=216495
- A staging 落盘合计：93.7 MB（原件+提取+outputs 副本）
- B 多小文件（20×4500 点 = 13.7MB）：创建+发现 1.36s（峰值 262MB）｜提交全部 87.02s｜完成 20/20，合计 90000 行
- 取消响应：120000 行 CSV 提交 0.2s 后取消 → 终态 cancelled，取消往返 6.21s（协作检查点间隔 1000 行）
- 清理：两个批次删除后 import_batches 残留 1 项（期望 1：A）

## 结论与口径说明

- **发现/预览近常数内存**：45.4MB GPKG 级单文件的成员枚举走 `pyogrio.read_info`
  （零行读），峰值 ~1MB；预览按行上限（200 行）读取，1.4s 内完成。清单展示
  不读完整容器（任务书 WP03 合同成立）。
- **提交是 O(N)**：与既有上传→摄入路径同类（全量 FC 载入 + canonical 指纹 +
  profile/quality + store）。45.4MB 源 → 峰值 553MB Python 分配、217s（本机
  无 Redis/DB 服务的内存 store 下；主要耗时在 IngestPipeline 既有步骤）。
  本任务不宣称恒定内存；staging→解释→FC 构造本身为全量路径。
- **取消**：120k 行 CSV 提交 0.2s 后发起取消，往返 6.2s 达到 cancelled 终态
  （协作检查点：解释层每 1000 行、成员间、IngestPipeline 阶段边界）。
- **清理**：批次删除后 staging 目录无残留（验证 2 删 1 留）。
- 未测项（如实）：并发多会话吞吐、真实 Redis/Postgres 下的端到端时延、
  GDAL 内部堆（tracemalloc 不可见）。以上为单进程本地测量。
