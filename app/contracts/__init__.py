"""跨层共享契约 kernel（ADR-0216）。

本包是**最底层**的契约归位处：只允许放跨层稳定的 Protocol、Literal 词表、
Pydantic/TypedDict/dataclass 契约与纯函数。依赖规则（由
``scripts/check_import_boundaries.py`` 强制）：

- 本包**禁止** import app 的任何其他顶层包（core/lib/schemas/services/api）。
- ``app/core``、``app/lib/cartography`` 禁止 import ``app.services``/``app.api``
  ——跨层共享对象必须从本包取。

收纳判据（ADR-0216 §决策）：一个对象进入 kernel 当且仅当它被 ≥2 个层
消费、自身零 app 内依赖（至多 stdlib/pydantic）、且语义是"形状/词表/
纯计算"而非"行为/状态/IO"。services 原模块保留 re-export shim，
既有 import path 全部兼容（tests/test_contract_kernel.py 锁定）。
"""
