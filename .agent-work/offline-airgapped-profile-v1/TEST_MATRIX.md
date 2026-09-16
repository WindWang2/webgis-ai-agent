# TEST MATRIX — offline-airgapped-profile-v1

## 单元 / contract（新增 tests/unit/）

| 测试 | 覆盖 |
|---|---|
| test_egress_policy.py | 决策纯函数：cloud=全放行；air_gapped：公网 deny/私网 allow/allowlist allow/大小写/端口/IPv6/裸 IP/bad URL；reason code；profile 关闭零行为变化；cancel-safe（无 IO） |
| test_egress_wiring_aiohttp.py | 共享 session on_request_start 拒绝（MockTimer 不真连）；cloud 模式不挂 trace（或挂 trivially-pass trace） |
| test_egress_wiring_httpx.py | LLM 池 client event hook 拒绝公网、放行私网 LLM；ad-hoc helper |
| test_egress_wiring_requests.py | SSRFSafeHTTPAdapter.send 前置守卫；redirect 跳也过守卫（fake server loopback 200 → Location 公网 → 拒绝） |
| test_network_dependency_catalog.py | 登记完整性（id 唯一/category 封闭/env key 存在于 Settings）、JSON 序列化稳定（schema version）、tools network=True 交叉引用矩阵 |
| test_settings_offline_profile.py | cloud 默认不变；air_gapped 强制 egress=allowlist（矛盾组合报错）；air_gapped+prod 要求 LLM 私网；非法 profile 值拒绝 |
| test_health_network_policy.py | /status/detailed 增组件语义（词表封闭） |
| test_offline_profile_env_parity.py（并入既有 hygiene） | 新键三同步（.env.example + _ENV_BASELINE + Settings） |

## 集成 / E2E（tests/integration/）

| 测试 | 覆盖 |
|---|---|
| test_airgapped_e2e_local_pipeline.py | egress deny + 真实 socket 守卫下：local geodata 加载（LOCAL_GEODATA_DIR fixture）→ 缓冲/聚合分析 → MapSpec 组装 → GeoJSON export；远程 geocode 调用 typed 拒绝 |
| test_data_fabric_offline_policy.py | air-gapped 下远程源 probe/query → SecurityBlockedError（含 profile reason）；本地源不受影响 |
| test_manage_preflight.py | preflight JSON/table 输出、exit code 语义、坏目录/不可达 LLM 各组件 down |
| test_manage_network_catalog_sbom.py | catalog/sbom/asset-manifest 子命令输出、JSON 可解析、不含大 binary 断言 |

## 跨平台 contract（tests/unit/test_offline_portability_contract.py）

- 路径分隔符/盘符（Windows）、UTF-8 编码清单读写、换行（manifest 生成 LF 稳定）、文件锁原语（msvcrt/fcntl 抽象存在性）。synthetic fixture，不碰真数据。

## 回归（既有，必须同口径绿）

- tests/unit/test_env_hygiene.py（parity）
- tests/unit/network/（若有）/ provider health 相关
- data_fabric security tests（SSRFSafeHTTPAdapter 行为不回退）
- LLM client lifecycle tests（event hook 不破坏池语义）

## 资源纪律

- pytest `-n 2` 上限；heavyweight 全量串行、不与 next build 并行。
- 前端 targeted vitest（providers 相关 + 新增 test）。
