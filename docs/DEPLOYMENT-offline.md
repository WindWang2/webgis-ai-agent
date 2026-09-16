# 离线 / 内网 / 信创部署运行手册（ADR-0197）

> 目标读者：在**无互联网出口**环境部署 WebGIS AI Agent 的运维/集成人员。
> 本文档描述系统级离线部署 profile（`DEPLOYMENT_PROFILE=air_gapped`）的
> 配置、预置资产、启动自检与降级行为。

## 0. 边界声明（先读）

- **未做 OS 级实测**：本仓库未在统信 UOS、麒麟等国产 OS 上执行过认证
  测试。本文给出的是应用层可移植性契约（跨平台路径/编码/换行/文件锁
  contract 测试，`tests/unit/test_offline_portability_contract.py`）与
  依赖清单。OS/硬件选型与合规认证属运营流程。
- **纵深防御**：应用层 egress 守卫不是安全边界（仓库对技能代码执行的
  同等立场）。STAC（pystac-client）、远程栅格（rasterio /vsicurl）、
  DDG 搜索、HuggingFace 下载、浏览器侧瓦片、vendor/pi 子进程不出
  Python 守卫。**最终防线是部署网络层**（防火墙出网白名单 / netns）。
- **仓库不发行受限资产**：模型权重、字体、瓦片不在 git/镜像内，见 §3
  资产预置清单。

## 1. 核心配置

`.env`（生产示例，完整键见 `.env.example`）：

```bash
# ── 部署 profile ─────────────────────────────────────────────
DEPLOYMENT_PROFILE=air_gapped          # 启动校验强制下一行为 allowlist
NETWORK_EGRESS_MODE=allowlist
NETWORK_EGRESS_ALLOW=                  # 逗号分隔显式主机 / *.suffix 通配
NETWORK_EGRESS_ALLOW_PRIVATE=true      # 放行私网/回环（内网 LLM/PG/MinIO）

# ── 本地 LLM（OpenAI 兼容；ollama/vllm/lmdeploy 等）──────────
LLM_BASE_URL=http://llm.intranet:8000/v1
LLM_API_KEY=<内网网关签发的 key>
LLM_MODEL=<内网模型名>

# ── 本地数据 / 模型 ──────────────────────────────────────────
LOCAL_GEODATA_DIR=/data/geodata        # 行政区 SHP / OSM GPKG / POI / 年鉴
DATA_FABRIC_LOCAL_FILE_ROOTS=/data/geodata,/data/rasters
RAG_EMBEDDING_OFFLINE=true             # 强制；模型缓存预置见 §3
WEBGIS_OBJECT_STORE_BACKEND=filesystem # 或内网 MinIO（WEBGIS_S3_*）

# ── 视觉评审 VLM（可选，指内网）──────────────────────────────
CARTO_VISUAL_JUDGE_PROVIDER=openai_compat
CARTO_VISUAL_JUDGE_BASE_URL=http://vlm.intranet:8000/v1

# ── ModelOps 远程推理（可选；默认 deny，需显式 allowlist）────
MODELOPS_REMOTE_ALLOWLIST=modelops.intranet:8100
```

语义要点：

- `air_gapped` 与 `NETWORK_EGRESS_MODE=unrestricted` 组合启动即报错；
  `LLM_BASE_URL` 指向公网 host（且不在 allowlist）同样启动即报错——
  chat 是启动必需能力，矛盾提前暴露。
- 私网/回环目标默认放行（内网 LLM、PostGIS、MinIO、瓦片服务器正是部署
  形态）；`NETWORK_EGRESS_ALLOW_PRIVATE=false` 可收紧为"全部显式登记"。
- 云元数据端点（169.254.169.254 等）任何情况下都被守卫拒绝。
- 出网拒绝是 **typed** 的（`AirGappedEgressError` / data-fabric
  `SECURITY_BLOCKED`）：必须联网的能力显式 unavailable/degraded，
  不伪装成网络事故。

## 2. 出网依赖目录

`python manage.py network-catalog --format json` 输出机器可读清单
（17 类依赖：端点、调用面、是否受守卫覆盖、当前 profile 下可用性）。
部署审计 / 采购合规直接消费该 JSON；`schema_version` 变更时重新对账。

典型 air-gapped 状态：本地 LLM/本地数据/内网对象存储 available；
amap/baidu/千帆搜索/GBIF/WorldBank 等公网依赖 unavailable（对应工具
typed 失败，正文见 tool_network_matrix 的离线替代列）。

## 3. 资产预置（构建机出网一次）

| 资产 | 预置方式 | 校验 |
|---|---|---|
| RAG embedding 模型 | 构建机预热 HF 缓存 volume（`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`），随镜像/卷分发 | `RAG_EMBEDDING_OFFLINE=true` + preflight |
| 本地 LLM 权重 | 内网推理服务自带（ollama/vllm） | preflight `llm_endpoint` |
| 本地地理数据 | 出网环境执行 `manage.py osm-ingest` / `gd-poi-ingest` / `yearbook-ingest`，拷贝 `LOCAL_GEODATA_DIR` | preflight `local_geodata` |
| 地图字形（glyphs） | `NEXT_PUBLIC_MAP_GLYPHS_URL` 指向内网字体 PBF 服务 | 浏览器侧 |
| 本地底图瓦片 | 内网 XYZ/PMTiles 服务；前端 `NEXT_PUBLIC_LOCAL_BASEMAP_URL` | 浏览器侧 |

`python manage.py asset-manifest` 输出上述清单与存在性判定
（present/absent/operator_supplied——运营供给项仓库不做存在性断言）。

## 4. 启动自检

```bash
python manage.py preflight                 # 表格报告；required-down → exit 1
python manage.py preflight --json --out /var/log/webgis-preflight.json
```

检查项（11）：deployment_profile / egress_policy / network_dependencies /
database / redis / celery_worker / llm_endpoint / rag_embeddings /
local_geodata / data_fabric_local_roots / data_dirs。

- exit 0：required 检查无 down（degraded/not_configured 是诚实状态，
  不阻断）；exit 1：有 required-down，先修复再启动（可接 systemd
  `ExecStartPre` / compose healthcheck 门禁）。
- `--only db,redis` 收窄范围（分阶段上线）。
- 运行面：`GET /api/v1/status/detailed`（鉴权）的 `network_policy` 组件
  随时反映守卫状态与离线依赖可用面。

## 5. 离线下的能力形态

- **可用**：本地文件/GeoPackage/本地 PMTiles/COG 数据面、buffer/聚合/
  统计等本地空间分析、MapSpec 制图与导出、RAG（预置模型）、
  ModelOps 本地模型推理。
- **typed unavailable**：在线 geocoding/POI（amap/baidu）、Overpass 在线
  查询、千帆/DDG 网络搜索、公网 STAC/远程栅格、公网 ModelOps 端点。
- **降级路径**：geocoding → `LOCAL_QUERY_FIRST` 本地行政区/POI；OSM →
  本地 GPKG；搜索 → 能力缺失（工具诚实报错）。
- 合成 E2E 证明：`tests/integration/test_airgapped_e2e_local_pipeline.py`
  在双重网络 deny 下完成 数据→分析→地图→导出 全链路。

## 6. SBOM

`python manage.py sbom --out sbom.json`：Python 发行清单
（importlib.metadata）+ 前端依赖声明。**仅元数据**，不含任何资产本体；
与 `network-catalog`/`asset-manifest` 组成部署合规三件套。

## 7. 回滚

`DEPLOYMENT_PROFILE=cloud`（保持其余不动）→ 守卫不激活，行为与 master
历史版本一致。air-gapped 内不允许单独把 egress 切回 unrestricted
（启动校验拒绝）。
