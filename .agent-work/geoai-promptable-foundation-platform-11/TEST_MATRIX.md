# TEST_MATRIX — 能力 → 独立 oracle → 命令 → exit → evidence

原则：oracle 独立于被测实现（合成几何用 rasterio/shapely 直接构造期望，
不用平台栅格化器生成期望；容差断言用独立数学重算）。
命令统一：`cd <worktree> && <venv-python> -m pytest <path> -o addopts= -q`。

## Phase 1（WP-A）

| 能力 | 独立 oracle | 测试 | 状态 |
|---|---|---|---|
| artifact 构造校验/身份稳定性 | 同 payload → 同 id；变一字节 → 变 id | tests/unit/modelops/test_geo_prompt.py | planned |
| CRS↔pixel 往返容差 | 独立仿射重算（affine 库直算对照） | 同上 | planned |
| polyline/polygon 栅格化 known-answer | shapely 构造 + 手算期望掩膜 | 同上 | planned |
| mask sidecar digest 失配 fail-closed | 篡改文件 → typed error | 同上 | planned |
| crs 声明但无 transform fail-closed | 构造缺失 → PlanningError | 同上 | planned |

## Phase 2（WP-C/D）

| 能力 | 独立 oracle | 测试 | 状态 |
|---|---|---|---|
| 候选集契约 + 参考候选 | 手工种子区域期望 | tests/unit/modelops/test_candidates.py | planned |
| cache hit/miss/回填 | 两次 run + 计数 provider | tests/unit/modelops/test_embedding_cache.py | planned |
| 部分失效（model/asset 前缀） | 失效后 miss 断言 | 同上 | planned |
| digest 篡改 → miss+驱逐 | 写坏 entry 文件 | 同上 | planned |
| 上界驱逐（entries/bytes） | 小上界 + 超量写入 + 存活集断言 | 同上 | planned |
| resume（部分窗口已有） | 预填一半窗口 → provider 只算另一半 | tests/integration/modelops/ | planned |
| 原子性（失败无残件） | 注入写失败 → 目录无 tmp 残件 | 同上 | planned |

## Phase 3（WP-E）

| 能力 | 独立 oracle | 测试 | 状态 |
|---|---|---|---|
| 无 encoder → typed refusal | 不 wire 任何 encoder | tests/unit/modelops/test_multimodal.py | planned |
| stub encoder 确定性 | 同文本同向量 | 同上 | planned |
| zero-shot known-answer | 构造正交原型 → 映射必须命中 | 同上 | planned |

## Phase 4（WP-F/G）

| 能力 | 独立 oracle | 测试 | 状态 |
|---|---|---|---|
| 工具 schema/行为 | registry 集成 + 合成栅格 | tests/integration/modelops/test_geoai_tools.py | planned |
| API route 合约 | FastAPI TestClient | tests/unit/api/（或同级既有模式） | planned |
| UI 面板 | frontend 既有组件测试模式 | frontend/components/geoai/*.test.tsx | planned |

## Phase 5/6（WP-H）

| 能力 | 独立 oracle | 测试 | 状态 |
|---|---|---|---|
| tile seam | 跨窗口几何闭合断言 | tests/integration/modelops/ | planned |
| 100k prompt 逻辑规模 | mock 计数 + 上界 | env opt-in scale test | planned |
| cancel/OOM 新路径 | 令牌触发点注入 | 同上 | planned |
| provenance replay | 重放指纹一致 | 同上 | planned |
| provider absence | 全 typed-refusal 矩阵 | 同上 | planned |

（"状态"在实现后更新为 done + exit code + 证据日期。）
