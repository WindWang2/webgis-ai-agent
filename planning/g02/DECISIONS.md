# G02 DECISIONS

| # | 决策 | 理由 | 替代方案（弃） |
|---|---|---|---|
| D1 | deferred 用显式 bool 字段而非消息/facts_used 启发式 | temporal_field_required :150 fact-backed PASS 无 facts_used，启发式必错 | __post_init__ 解析 "deferred" 消息（脆） |
| D2 | not_evaluated 独立通道，不混入 scientific_warnings | warnings 语义是"通过但有警告"；not_evaluated 是证据缺席披露，消费者（final gate/制品）需分别处理 | 复用 warnings 加前缀（语义污染） |
| D3 | 五值 verdict 语义与 combine_verdicts 排序不变 | resolver 兼容承诺（候选非阻断资格）；deferred-PASS 仍是 PASS 资格 | 引入第六值 PASS_DEFERRED（破坏词表） |
| D4 | MethodUseContract 是只读投影非第二 registry | SSOT 纪律：registry/contracts/references 是权威，contract 是视图 + schema version | 独立元数据存储（平行真相，禁止） |
| D5 | 执行 fail-closed 仅限科学性拒绝码集合 | registry 视图未知/工具退役回退是既有兼容承诺（rerun explainable），科学拒绝（数据事实违反）才是硬风险 | 全部 unavailable 都拒（破坏旧 manifest rerun） |
| D6 | final gate 默认 advisory，block 仅 G-FALLBACK-SEMANTICS 未披露 | 非关键不确定性诚实降级不阻断；不等价替换静默通过才是科学事故 | 全 block（阻断用户正常操作，违反边界） |
| D7 | gate oracle 用 pytest-native 测试而非冻结 JSON 语料 | gen_science_oracles.py 被 #1758 修改中；守门 oracle 是行为断言非数值 golden；避免语料重签冲突 | 加 science_oracles 新域（文件冲突+钉扎重签） |
| D8 | planner.py 与 #1758 同文件但 hunk 不相交，照改并在 PR 披露 | 该文件是转录断链唯一修复点；机械避开会牺牲真实生产接线 | 等待 #1758 合并（阻塞且无必要） |
| D9 | profile_digest 用 canonical_dumps + sha256 作 freshness token | 复用 provenance.fingerprint.canonical_dumps（既有权威序列化） | 自造序列化（第二协议，禁止） |
| D10 | 不动 evidence_claim/verify.py 与 carto_binding | #1755/#1758 语义领地；G02 final gate 与 claim 生态正交 | 接通 claim（越界 + 冲突） |
