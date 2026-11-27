# REVIEW_R1 — 独立审查（2026-11-28，审阅代理逐行 diff + 运行时实验）

结论：**有条件进入 R1 修复** —— 1×P0、4×P1、8×P2、8×P3；域建模/ZIP 防线/状态机骨架/测试矩阵质量较高。

## P0
1. manifest 引用路径无包容校验 → 任意文件读取（payload.path/graph_spec_path/mapspec_path 直拼 staging；数据集载荷不校验 sha256）。修复：统一 resolve+relative_to 包容校验 + 强制摘要对账。

## P1
2. 补偿式回滚死代码（mapping 仅成功时赋值 → except 永远看不到 project_id）。修复：边注册边填充。
3. inspect/plan 路由 `user.get("id")` 键错（真实形态是 user_id）→ 私有项目 404。修复 + 真实形态路由测试。
4. ImportStatusResponse extra=forbid 遇 receipt.job_id → 回执端点必 500。修复：schema 加字段。
5. gpkg 成员 payload.sha256 记录中间态 → 指纹链失真。修复：终态统一回填。

## P2
6. 提交后失败（verify/receipt 写盘）项目已持久化无人补偿 → 失败终态前项目级补偿。
7. manifest.files 未覆盖条目照解压 → 解压后强删未声明文件 + 回执披露。
8. 导出阶段 A 重 IO 在事件循环 → to_thread 包裹。
9. 打包 IO 放大 4-5x → gpkg 终态单次哈希（随 #5），其余记录为已知取舍。
10. 地图栅格恢复 idx 口径错位 → 两侧统一按 raster_ref 非空计数。
11. CRS 缺失静默捏造 EPSG:4326 → 保留空 + 披露，不写默认。
12. 包上传 org 兜底越权面 → 收紧 owner-only。
13. 前端裸 <a> 下载 → 改 authenticated-download。

## P3
14. receipt 服务函数 actor 参数未用 → 删参数（REST 路由侧已有项目授权）。
15. manifest 读取按 plan.arcname（防 ./manifest.json 等价名 KeyError）。
16. 纯 session_ref 项目 EMPTY_SELECTION 误拒 → 预检口径与执行期一致。
17. inspector .tiff 漏判 → endswith((".tif",".tiff"))。
18. staging/孤儿清扫 → 提交时 TTL 清扫（>24h）。
19. 幂等 check-then-act 竞态 → 与 data_delivery 同款已知取舍（进程内 _ACTIVE_RUNNERS 守卫），记录不扩改。
20. 前端 ExchangePlan 类型继承失真 → 拆分独立类型。
21. 匿名会话导出 owner 永不可下载 → receipt 下载链接走 export_download_url（session 主体签名）。

## 核查通过项
i18n 键集对称 67/67；_job_param_sync 归属谓词与 jobs API 一致；ZIP 静态防线扎实；直挂与 attach_dataset 生产语义等价（无被绕过管线）；挂载无遮蔽；deferred 注册未动前门；404 不泄露存在性正确。
