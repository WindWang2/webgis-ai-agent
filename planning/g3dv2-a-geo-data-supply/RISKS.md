# RISKS — g3dv2-a-geo-data-supply

| # | 风险 | 处置 |
|---|---|---|
| R1 | 共享路径回归：upload.py/layer.py 被多方依赖 | 只做加法（新 lane 分支/新端点/可选 query token）；既有测试全跑；vector lane 行为零变更 |
| R2 | 换算副本磁盘放大（非米单位 DEM 双份） | 仅按需生成；上传本体 ≤200MB 上界即副本上界；delete_upload 清整个 upload_dir 天然覆盖；PR 说明 |
| R3 | 限流器多 worker 各自为政 | 每 worker 独立窗口，总预算=N×rpm（保守）；文档明示；单 worker 部署（本项目默认 uvicorn 单进程多线程）不受影响——需核实部署拓扑，写入 EVIDENCE |
| R4 | 匿名 ?token= 扩大 token 暴露面 | 与 /sessions/{sid}/raster/{id}.png?token= 既有先例同语义（SEC-08）；ref_id 含 64bit 熵 + token 校验仍在；不新增日志记录 token 值 |
| R5 | 剖析大图幅耗时阻塞 | 摄入剖析沿用降采样（≤2048 边）+ to_thread；不引入全幅读 |
| R6 | #1805 修复合并后 CRS 行为变化 | 本 track 不依赖 classify_crs，只用 rasterio transform_bounds 实测；合并后跑兼容验证（TEST_MATRIX 既有回归节） |
| R7 | C 的 analysis 门消费 receipt 时误解 display_only | contracts schema 文档 + 契约测试锁形状；reasons[].code 全部枚举披露 |
| R8 | master 在 PR 期间前移 | push 前重 fetch + rebase + 重跑定向门（任务书 §6） |
| R9 | quota 接线改变现有瓦片路由响应形状 | 429 仅在超限时出现；默认 rpm 宽松（600/1200），正常测试序列远达不到 |
