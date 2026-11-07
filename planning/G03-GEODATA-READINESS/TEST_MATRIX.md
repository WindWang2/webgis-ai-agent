# G03-GEODATA-READINESS — 测试矩阵与 Oracle 对照

## Oracle 对照（PLAN.md O1-O10）

| # | Oracle | 证据 | 状态 |
|---|---|---|---|
| O1 | 恶意/缺失/不一致输入不产生 ready | TestB9 + TestF1GuardBudgetBypass（绕过反例）+ TestGd09RemoteHostile + TestF3NanBbox + 切城阻断 | ✅ |
| O2 | 维度级 provenance 三态、未知不虚构 | TestB8（tri-state + crs unknown + license observed） | ✅ |
| O3 | explorer 数据可被语义面消费 | TestB1（descriptor 存在、仅成功行成要素、全败不铸 FC） | ✅ |
| O4 | 裸分发遇 stale/corrupt 阻断且可解释、flag 可关 | TestB2 六项 + TestB2DispatchWiring 两项（真实 dispatch 全路径） | ✅ |
| O5 | 远程 GeoJSON URL 预算+SSRF 受控 | TestB3 三项 + TestGd09（429/5xx/超预算/慢滴 deadline）+ 既有 SSRF 套件 | ✅ |
| O6 | CSV+坐标上传产出 descriptor | TestCharacterization（链路完整审计修正） | ✅ |
| O7 | 切城重资格、零记录置信边界 | journey 切城阻断 + TestF5 mapspec 接线 + 零记录诚实（空 FC 0 要素不 truncated 不合成） | ✅ |
| O8 | 50/5k/100k/1M 有界资源 | tests/perf/test_g03_ingest_bounded.py（不变量断言 + INGEST_PERFORMANCE.md 实测） | ✅ |
| O9 | 零模拟旅程：本地输入→资格→真实算法→图层 | TestChengduSchoolRateJourney（真值对账 5/5 区、率值=分子/分母、科学语义披露在场） | ✅ |
| O10 | 独立 review 无未处理 P0/P1 + 真实 PR | REVIEW.md（15 项全清偿 + 复审记录）+ PR URL（M9） | ✅（复审见 REVIEW.md） |

## 测试文件清单

| 文件 | 覆盖 | 规模 |
|---|---|---|
| tests/data/test_g03_readiness_chain.py | 6 断缝红灯→绿 + characterization + dispatch 接线 | 30 tests |
| tests/data/test_g03_ingest_ops.py | GD-06 取消/幂等/回滚、GD-10 计时、零记录、GD-04 fail-closed、GD-07 失效链、GD-09 远端敌意 | 12 tests |
| tests/data/test_g03_journey_chengdu.py | 成都率值图旅程/切城阻断/scope 复用失效 | 3 tests |
| tests/data/test_g03_review_fixes.py | 评审 F1/F3/F4/F5/F7/F12 反例回归 | 14 tests |
| tests/perf/test_g03_ingest_bounded.py | 50/5k/100k/1M 分级基准 + 网络预算 | 5 tests（1M heavy） |

## 回归批次（全部绿）

- g03 全家 + ingest/qualification/dataset_semantics/mapspec 复用门：190 passed
- spatial_semantics/utm_cache/normalizer 消费面/scientific_regression/ADR 契约：185 passed
- explorer 全套 + ads adapters + SSRF：52 passed
- explorer task_chain/durable + upload 面 + preparation API：55 passed
- 质量门：ruff（改动面 0）/mypy（新模块 0）/ADR 编号契约 3 passed

## 未验证边界（如实披露）

- 无真实 PostGIS/外网 OGC 服务联机（FakeSourceServer 离线矩阵；real_services CI 车道未运行）；
- 前端未改动（本 PR 后端面）；浏览器旅程未跑（无 Node 环境变更）；
- 线上 CI 未运行不宣称通过（本地验证为准）。
