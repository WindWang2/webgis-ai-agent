# TEST_MATRIX — 能力 → 独立 oracle → 命令 → exit → evidence

原则：oracle 独立于被测实现（合成几何用 rasterio/shapely 直接构造期望，
不用平台栅格化器生成期望；容差断言用独立数学重算）。
命令统一：`cd <worktree> && <venv-python> -m pytest <path> -o addopts= -q`。

## Phase 1（WP-A）

| 能力 | 独立 oracle | 测试 | 状态 |
|---|---|---|---|
| artifact 构造校验/身份稳定性 | 同 payload → 同 id；变一字节 → 变 id | tests/unit/modelops/test_geo_prompt.py | done (33 passed) |
| CRS↔pixel 往返容差 | 独立仿射重算（affine 库直算对照） | done | done |
| polyline/polygon 栅格化 known-answer | shapely 构造 + 手算期望掩膜 | done | done |
| mask sidecar digest 失配 fail-closed | 篡改文件 → typed error | done | done |
| crs 声明但无 transform fail-closed | 构造缺失 → PlanningError | done | done |

## Phase 2（WP-C/D）

| 能力 | 独立 oracle | 测试 | 状态 |
|---|---|---|---|
| 候选集契约 + 参考候选 | 手工种子区域期望 | tests/unit/modelops/test_candidates.py | done (10 passed) |
| cache hit/miss/回填 | 两次 run + 计数 provider | tests/unit/modelops/test_embedding_cache.py | done (14 passed) |
| 部分失效（model/asset 前缀） | 失效后 miss 断言 | done | done |
| digest 篡改 → miss+驱逐 | 写坏 entry 文件 | done | done |
| 上界驱逐（entries/bytes） | 小上界 + 超量写入 + 存活集断言 | done | done |
| resume（部分窗口已有） | 预填一半窗口 → provider 只算另一半 | tests/integration/modelops/ | done (geo_prompt_platform 6 / embedding_cache_platform 4 / prompt_candidates 5 / hardening 7 / e2e 5) |
| 原子性（失败无残件） | 注入写失败 → 目录无 tmp 残件 | done | done |

## Phase 3（WP-E）

| 能力 | 独立 oracle | 测试 | 状态 |
|---|---|---|---|
| 无 encoder → typed refusal | 不 wire 任何 encoder | tests/unit/modelops/test_multimodal.py | done (6 passed) |
| stub encoder 确定性 | 同文本同向量 | done | done |
| zero-shot known-answer | 构造正交原型 → 映射必须命中 | done | done |

## Phase 4（WP-F/G）

| 能力 | 独立 oracle | 测试 | 状态 |
|---|---|---|---|
| 工具 schema/行为 | registry 集成 + 合成栅格 | tests/integration/modelops/test_geoai_tools.py | done (14 passed) |
| API route 合约 | FastAPI TestClient | 路由合同并入 test_geoai_tools.py | done |
| UI 面板 | frontend 既有组件测试模式 | frontend/components/geoai/geoai-panel.test.tsx | done (6 passed) |

## Phase 5/6（WP-H）

| 能力 | 独立 oracle | 测试 | 状态 |
|---|---|---|---|
| tile seam | 跨窗口几何闭合断言 | tests/integration/modelops/ | done (geo_prompt_platform 6 / embedding_cache_platform 4 / prompt_candidates 5 / hardening 7 / e2e 5) |
| 100k prompt 逻辑规模 | mock 计数 + 上界 | 逻辑规模并入 test_geoai_hardening.py（10k 窗纯函数） | done |
| cancel/OOM 新路径 | 令牌触发点注入 | done | done |
| provenance replay | 重放指纹一致 | done | done |
| provider absence | 全 typed-refusal 矩阵 | done | done |

（"状态"在实现后更新为 done + exit code + 证据日期。）


## 实测汇总（2026-09-15）

- 后端全量：`tests/unit/modelops tests/integration/modelops -o addopts= -q` → **334 passed, 9 skipped**。
- 前端：`vitest run` 全量 **3709/3709**；geoai+i18n 定向 31 passed。
- 双跑验证与 exit code 证据见 EVIDENCE.md（Phase 8 补两遍连续运行）。
