# RISKS — G3D01

| 风险 | 等级 | 缓解 |
|---|---|---|
| OpenFreeMap 公共实例可用性非 SLA | 低 | 契约内明确"公共源可用=合法配置入口而非 SLA"；cache_policy 记录离线限额；自托管路径（PMTiles 路由 + NEXT_PUBLIC_LOCAL_BASEMAP_URL）已提供 |
| 许可条款漂移（OpenFreeMap/AWS/各家 ToS 变更） | 中 | 每条 profile 带 license_url/tos_url + 核验日期（模块 docstring）；契约测试只锁字段一致性，条款复核属运营流程 |
| PMTiles 路由被当批量出口（绕过 OSMF 等禁止条款） | 中 | 路由仅服务**已注册受治理归档**（tenant 门 + 归档本体的许可由注册方负责）；cache_policy.offline_bulk_download=forbidden 语义在画像层表达，未加出站限流（D11 显式留白） |
| AWS 地形兜底仍在（行为保留） | 低 | 已从"未治理硬编码"升级为"画像治理 + air_gapped 禁用 + display_only 资格 + 署名可见"；彻底移除留给后续（ADR-0199 D10 契约） |
| 合成 DEM 被误用于分析 | 低 | 三重防线：模型校验（synthetic⇒display_only）、文件自描述 tag、资格函数 typed reason_code；E2E 溯源测试 |
| base_layers 派生化改变 LLM 目录内容 | 低 | 12 条既有全部保留（顺序前插 OpenFreeMap×3）；消费方测试（context_builder/apply_template）回归验证 |
| 双语目录（中文名）匹配漂移 | 低 | vitest 契约锁逐条比对 name/keywords；TS/Python 两侧任一漂移即红 |
| PMTiles content-type 缓存旁路记录驱逐漂移 | 低 | 魔数嗅探兜底（封闭类型集 PNG/JPEG/WebP/AVIF/gzip-MVT）+ 专测 |
| 未跑真实浏览器 E2E（maplibre 真实例程） | 中 | journey lane（Playwright mock 模式）与本分支 vitest/jsdom 面覆盖行为；REQUIRE_BROWSER=1 heavy lane 为 nightly 专属（CI 纪律，不本地伪造） |

## 显式不做（边界外）

- 3D Tiles mesh renderer、viewshed 算法、Agent 语义规划、导出管线（G3D02/03/04 域）
- Quota 出站限流执行、WMTS TileMatrixSet→MapLibre URL 派生、TileJSON 端点（后续增量）
- MapSpec schema 版本变更（1.6 保持）
