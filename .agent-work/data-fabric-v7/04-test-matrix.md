# V7 最终测试矩阵（精确结果，2026-09-09）

## 新增测试（本 Epic）
| 文件 | 用例数 | 覆盖 |
|---|---|---|
| tests/unit/test_fabric_connection_registry.py | 16 | 作用域隔离/revision CAS/secret 分离/过期 typed/容量 LRU/sweep/健康状态机/P1 凭证恢复回归 |
| tests/unit/test_fabric_probing.py | 7 | CQL2/maxRecordCount/STAC 探测升级、scoped 缓存 TTL+revision+容量、失败回落默认、rate-limit 头解析、ProbeCost |
| tests/unit/test_fabric_source_facts.py | 9 | descriptor 采集/诚实 None/作用域隔离/observed basis/TTL/失效/统计往返/DB fail-open |
| tests/unit/test_fabric_cql2_json.py | 8 | CQL2-JSON 全 op 词表/非法属性标识拒绝/text-JSON 同 AST 语义/bbox/OGC json 协商/text 逐位/STAC 探测升级与回落 |
| tests/unit/test_fabric_arrow_lane.py | 7 | 通道探测/行形状一致/where+分页/budget fail-fast/dict lane 回落/R1-C1 bbox 行级过滤×2 |
| tests/unit/test_fabric_v7_planning.py | 15 | 不确定性乘子/限流惩罚/delivered 账本/聚合下推唯一键等价×2（唯一键+嵌套左拒绝）/无证明不下推×2/bushy replan 触发+拒绝/server 通道门控×3 |
| tests/unit/test_fabric_feedback_cache.py | 12 | 反馈记录/衰减修正/样本不足诚实/错误不学习/R-C3 无过滤回写守卫×2/缓存披露命中/owner 隔离/负缓存策略/字节上界/R2-C1 dict where 进键×2/R2-M3 超界不驻留 |
| tests/unit/test_fabric_security_differential.py | 12 | SSRF 门×6（私有/元数据/环回/IPv6/localhost）/过期 typed/feedback 无 secret 面/caps 无 secret 面/4 源差分（v5-v6-cache-given）/replan 混沌一致/Bloom 无假阴性 |
| tests/unit/test_fabric_v7_perf.py | 4 | 请求数预算/行预算 fail-fast/缓存命中 0 远端请求/源骤变语义保持 |

**新增小计：90 用例**

## 回归矩阵（最终运行）
| 套件 | 结果 |
|---|---|
| 联邦+fabric 组合（39 个测试文件，含 V5/V6 差分、engine wiring、adapter 契约、安全/可靠性、migration wiring） | **502 passed, 1 skipped**（19.2s，serial，--no-cov） |
| 质量车道 `data`（tests/data/ + fabric 指定面） | **绿**（33.4s） |
| 质量车道 `quick`（tests/quality/ + 漂移 + 生成物一致性） | **绿**（41.8s；账本指纹刷新后） |
| 质量车道 `security` | **绿**（4.8s） |
| tests/test_deploy_migration_wiring.py（drift guard 列比对） | 20 passed |
| migration 单 head | `0034_data_fabric_v7_facts_feedback`（down_revision=0033_geocompute_v6_cluster） |
| ruff（app/ + tests/，仓库 lint 选集） | 0 错误 |

## 差分/等价证明覆盖
- V5 ↔ V6：既有镜像 parity + V6 差分语料（逐位）
- 聚合下推 on/off：唯一键场景逐位一致；无证明路径走本地内核（行为锁定）
- cache on/off、replan on/off（cost vs given）：4 源场景序不敏感精确一致
- server CRS 交付账本：PostGIS/ArcGIS delivered_crs metadata → 执行期变换基准
