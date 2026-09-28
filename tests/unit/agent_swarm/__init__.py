"""tests.unit.agent_swarm — agent_swarm（ADR-0187/0188/0189）专属测试目录。

覆盖纪律（与 tests/unit/test_swarm_orchestrator.py、
test_specialist_carto_auditor.py、test_specialist_data_compute.py 三个
既有套件互补 —— 那三套覆盖各自模块的 happy path 与主要隔离语义，
本目录专盯契约有界纪律、异常/失败路径与跨模块组合面，少量分解器/
估算面的确定性用例是对既有覆盖的补充锚点）：

- 契约层有界纪律（refs-only / 截断 / 上限）—— delegation_contracts 与
  specialists 输出契约的 fail-closed validator；
- 编排器异常路径：重试穷尽 / 取消收敛 / launcher 崩溃兜底 / 裁决口径；
- 派发器归一化矩阵与 runtime 异常折算；
- 聚合器披露面（failed_capabilities / dropped_refs / fail-open 回写）；
- 专家基座 RBAC / 心跳 / 墙钟熔断与注册表一致性；
- DataScout / GeoCompute / Cartographer / CriticAuditor 的确定性领域方法。

全部 stub 注入（dispatcher / registry / adapter / submitter / clock），
不打 LLM、不打 DB、不打 Redis、不打真实数据源；async 测试裸
``async def``（pytest.ini asyncio_mode=auto）。
"""
