# GAP ANALYSIS — 现状 vs 离线/内网/信创部署需求

| # | 需求 | 现状 | 差距 → 本分支动作 |
|---|---|---|---|
| 1 | 显式 air-gapped 部署 profile | 无（只有零散开关） | `DEPLOYMENT_PROFILE=cloud\|air_gapped` 设置 + validator |
| 2 | 出网 egress guard | 只有 extension/modelops 两个局部 allowlist；测试专用 socket guard 不进运行时 | `app/core/egress.py`：全局 host allowlist 策略，接到 aiohttp/httpx/requests 三大接缝 |
| 3 | 必须联网功能 typed unavailable | 各客户端失败形态不一（连接错误/超时） | 守卫抛 typed `AirGappedEgressError`（含 reason code + dependency id）；data_fabric 远程源 → `SecurityBlockedError` |
| 4 | 本地 LLM endpoint | `LLM_BASE_URL` 已允许私网（#925） | preflight 校验：air-gapped 下 LLM endpoint 必须本地/私网，且 /models 可达 |
| 5 | 本地数据/底图健康 | `LOCAL_GEODATA_DIR`/`DATA_FABRIC_LOCAL_FILE_ROOTS` 存在但无体检 | preflight 检查目录存在与非空；前端 local basemap provider |
| 6 | NetworkDependencyCatalog | 无机器可读目录（`config/sources/*.yaml` 只覆盖 data-fabric 源；tool `network:` 声明散落） | `app/core/network_dependency.py`：单一事实源，含 endpoint/env key/call sites/offline fallback，可 JSON 导出 |
| 7 | SBOM / asset manifest | 无 | `manage.py sbom`（importlib.metadata 生成，不打包资产）；asset manifest 列出部署所需本地资产（模型缓存/字体/瓦片/geodata），只登记不发行 |
| 8 | 启动自检/doctor | `manage.py check` 只有 4 组件 | `manage.py preflight`：DB/Redis/Celery/LLM 私网+可达/embedding 缓存/geodata roots/tiles 配置/egress 生效性/输出目录可写/端口 |
| 9 | 网络 deny 合成 E2E | 无 | deny 下 local 数据→分析→地图→export 全链路测试 |
| 10 | 信创声明 | 无 | 文档明确"未经实测的国产 OS 不声明支持"，只给可移植性 contract 测试（路径/编码/换行/文件锁） |

## 不做（rescope 边界）

- 不实现新 LLM server、不打包模型权重/字体、不改云模式默认行为、不重写 provider、不做 OS 级 netns 隔离（extensions V3 已有 bwrap 路线）。
- vendor/pi 子进程出网不可从 Python 层拦截 → 文档记录为已知边界：air-gapped 部署需以网络层（防火墙/netns）兜底，Python 守卫是应用层纵深防御第一层。
