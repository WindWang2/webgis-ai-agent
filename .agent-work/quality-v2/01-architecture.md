# 01-architecture — Quality V2 目标架构

## 原则（继承 ADR-0104）

派生优先（registries 是唯一事实源，一切产物是投影 + 字节闸）；线索可执行化（findings 从"报告"升级为"棘轮债务"）；生产零开关；资源有界；诚实披露。

## current → target

```
[current]                                [target V2]
registries ──► compile_quality_manifest  registries ──► compile_quality_manifest (v2: +owner/domain/surface/behavioral_state)
                    │                                        │
                    ▼                                        ▼
        findings(241, 无闸, 纯报告)          findings_ratchet(baseline json, 只降不升) + waivers(带 expiry)
                                                             │
                                                findings=0 后 baseline 锁零 → 新增未测试工具/算法直接红
```

### 组件决策

1. **QualityManifest V2**（app/lib/quality/manifest.py，扩展不重写）
   - finding dict 增加：`domain`（subject 前缀派生：tool→tools，algo 第一段，capability 最后段）、`surface`（tools/algorithms/capabilities/artifact_types）、`behavioral`（`static`|`behavioral`|`none`，来自新 behavioral discovery 索引）。
   - 新 `docs/quality/findings-baseline.json`（提交物）：`{code: max_count}` 棘轮；`docs/quality/waivers.json`：`[{code, subject, reason, expires}]`，过期 waiver = gate 红。
   - 新函数 `evaluate_findings_ratchet(manifest) -> ratchet_report`；gate = 富化闸 AND ratchet AND waivers-valid。
   - 不新建第二 registry/runtime——仍是 manifest.py 单编译器 + gen 脚本薄壳。

2. **Behavioral coverage discovery**（app/lib/quality/behavioral.py，新）
   - 静态 AST 扫描 `tests/`：`registry.dispatch("name")` / `dispatch_service.dispatch(..., "name")` / `ToolDispatchService` 调用点 → behavioral set；纯字符串提及不算。
   - 消费方：manifest v2 `tools[].behavior`、TOOL_UNTESTED 判定改为 behavioral（静态引用仍记录为 hints）。保持向后兼容：`discover_test_references` 不动。

3. **WS/SSE 契约**（app/lib/quality/api_compat.py 扩展）
   - `snapshot_realtime_contract()`：WS 消息（direction/type/required fields，来自 ws_service handlers + 客户端 send 分支）+ SSE 事件（event 词表 + 必备字段，来自 app/utils/sse.py + chat.py 发射点）。
   - 快照 `tests/quality/snapshots/realtime-contract.json` + diff 分类（type 移除=breaking、required 扩张=breaking、新可选=additive）。刷新 env：`REALTIME_SNAPSHOT_UPDATE=1`。

4. **安全收口**（最小语义变更，不造第二 authz 层）
   - SEC-KG-01：`app/services/artifact_registry.py` get/list/mark 增加 `owner_session` 校验参数（默认 None=旧行为，调用方显式传入即强制），data_fabric/report 等调用点接入；回归 = 跨 session 不可读写矩阵。
   - SEC-KG-02：templates/knowledge delete 增加 owner 校验（复用 verify_session_owner 语义）+ #1109 同款 owner/token 矩阵测试；security_manifest 状态 KNOWN-GAP→TESTED。

5. **生成式/fuzz harness**（tests/fixtures/generative.py + fuzz_corpus/）
   - 零新依赖（不引入 hypothesis：资源/依赖纪律 + 机器可重复）；seeded random 生成器 + 有界迭代 + 失败样本落 `tests/fixtures/fuzz_corpus/` 作回归语料。
   - 覆盖：GeoJSON/WKT 解析、MapSpec Intent 序列不变量、cache key 稳定性、manifest JSON fuzz、artifact ref fuzz。

6. **差异化存储 harness**（tests/quality/test_storage_differential.py）
   - SQLite 恒跑；PostgreSQL 由 `TEST_POSTGRES_URL` 开启（缺省 graceful skip，与 real_services 纪律一致）。
   - 语义矩阵：FK 落序、约束/唯一、NULL 排序、JSON 往返、时区、事务回滚、部分索引语义（迁移层）。

7. **chaos/cancellation**
   - chaos.py FAULTS 增加 STORAGE 类瞬时故障（注入 asyncpg/sqlite 层的确定性故障包装）。
   - cancellation checkpoint 补进 4 个无检查点大循环文件（terrain/density/rs_v3/geo_raster.chunk），再生成 CANCELLATION_COVERAGE。

8. **顺序卫生 + runner V2**
   - conftest 增加 seeded 顺序 shuffle（`QUALITY_ORDER_SEED` 环境变量，默认不启用，runner changed/full-local profile 启用轮换 seed）+ registry 泄漏自检 fixture。
   - quality_runner.py 增加 profiles：quick / changed（git diff 驱动）/ full-local；报告目录迁至 `.agent-work/quality-v2/`（gitignored）。

9. **性能门稳定化**
   - `tests/fixtures/perf_budget.py`：median-of-N + floor/factor 语义（对齐 test_perf_harness 模式）；迁移最脆的固定墙钟断言（<0.5s 预算的 unit 文件优先）；结构量闸优先于墙钟。

10. **生成物依赖图 + stale detector**
    - `app/lib/quality/artifact_graph.py`：每个生成物记录输入指纹（registry schema_fingerprint + 源文件内容 hash）→ `docs/quality/generated-artifacts.json`；`scripts/check_generated_staleness.py` 比对当前输入指纹 vs 记录 → stale 清单（merge-ref 前置检查，替代事后手动再生成纪律）。

## 兼容性

- manifest_version 1→2：json 消费方只有 gen_quality_report.py 与闸测试，同步更新；MD 渲染保持结构兼容。
- findings-baseline/waivers 为新增文件，不改既有产物 schema 的既有字段。
- behavioral 判定替代静态判定：TOOL_UNTESTED 语义收紧（更严格），修复路径 = 写真实 dispatch 测试。

## 性能预算 / 资源

- 全部新增测试 bounded（无网络、无大数据；fuzz 迭代有上限；differential PG 测试默认 skip）。
- 闸新增成本：behavioral AST 扫描 O(tests 源文件)，一次性缓存于进程内。

## 与并行 Epic 的接口边界

- 不动 runtime_manifest.py 的运行时快照语义；只在 quality 投影层新增字段。
- app/tools/*.py register 调用点仅追加富化 kwargs（capabilities/tags/side_effect），不改工具行为。
- app/lib/gis/algorithms/*.py 仅追加 conformance_tests/backend_variants 声明与 checkpoint 调用；不改算法数值路径（除非 KNOWN-GAP xfail 修复，本 Epic 不动 4 个科学/制图 xfail——归属 science-v4/cartography 域，避免跨界）。
