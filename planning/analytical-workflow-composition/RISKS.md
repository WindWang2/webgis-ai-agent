# RISKS

| # | 风险 | 缓解 |
|---|---|---|
| R1 | 与 g3dv2 系列后续 PR 撞 `tests/acceptance/gis_agent` corpus pin | 新增独立 F 族编号段；pin 文件用既有 CLI 生成器重生成；PR 前二次去重 |
| R2 | 幽灵命令不变量（catalogue-contract.test.ts 扫描后端 command 发射） | 新工具不发射新 command 字符串（command=工具名沿用 3D 先例）；若 vitest 门禁红则同步登记 catalogue.ts 切片（先例 a17cd9a88） |
| R3 | 双执行真相争议（与 workflow_runtime V5） | 编排层只用 registry.dispatch（V5 不动）；文档明示 V5 是 durable 面向、本层是 agent 会话面薄包装；hooks.record_tool_result_safe 可选喂 V5（fail-open） |
| R4 | nested dispatch 的 ref 铸造双重化 | 遵循 #1867 提升模式：内层结果已 store 的 ref 直接复用；未 store 的 geojson 提升外层由 dispatch 缝统一铸造——本层不自行 store 重复副本 |
| R5 | 大结果进 Pi 上下文 | 封套只含 ref/统计/有界 warnings；geojson 提升后由外层缝按既有 size policy 截断 |
| R6 | 网络/权限失败阻塞 PR | 分支+提交全部保留本地；有限重试；仍失败输出可粘贴 PR body + 失败日志（任务书条款） |
| R7 | 科学口径回归（单位/CRS/分母） | 每族负例测试先行（TDD）；单位合同在 plan 期校验而非执行期才报错 |
| R8 | fingerprint 不稳定导致误重算 | canonical JSON + 内容指纹复用既有 analysis_reuse 语义；同内容换 ref 不重算有专门测试 |
| R9 | 状态写竞态（Pi 并发回调） | state 单写者协议：load-modify-save 走 session store with_lock（对齐 session_plan 既有 CAS 惯例）；执行中步骤租约标记 |
| R10 | recipe packs additive 键引发旧测试快照漂移 | 新键可选默认缺省；跑 recipe 注册表全量回归 + 生成目录用原生成器重建 |
