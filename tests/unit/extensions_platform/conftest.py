"""扩展平台测试的全局状态隔离（fixture 自动生效）。

扩展激活把 cartography 组件投进进程级 ComponentRegistry；runtime
manifest（§5 组件表投影）与 harness 组件组合测试都假设「干净的内置
注册表」。本包测试会激活各种演示扩展（acme.* / extdemo.*），不恢复
就会泄漏到同进程后续测试 —— CI 全量顺序下已实录两例：

- test_component_composition::test_component_registry 的 validate()
  因泄漏的扩展组件缺 renderer 支持矩阵而红；
- test_scientific_contracts_vnext::test_fingerprint_stable_and_sensitive
  的 manifest 指纹因组件表混入扩展组件而失稳。

每个测试后：unregister 全部本测试新增的组件（公开 API，代次单调递增
自动失效派生缓存），并 refresh runtime manifest 单例。
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _restore_global_component_registry():
    from app.lib.cartography.component_registry import get_component_registry

    reg = get_component_registry()
    baseline_ids = set(reg.all_ids)
    yield
    leaked = set(reg.all_ids) - baseline_ids
    for cid in leaked:
        reg.unregister(cid)
    if leaked:
        from app.lib.gis.runtime_manifest import get_runtime_manifest

        get_runtime_manifest(refresh=True)
