# 04 — Test Matrix（随 waves 更新）

运行约定：串行分阶段；pytest workers 默认 `-p no:cacheprovider -x -q`；
扩展域 `tests/unit/extensions_platform` + `tests/unit/test_pi_extension_hardening.py`。

| 层 | 命令 | 状态 |
|---|---|---|
| 扩展域全量 | pytest tests/unit/extensions_platform -q | 待跑 |
| 静态 | ruff check app/extensions_platform tests/unit/extensions_platform | 待跑 |
| 契约 | corpus（并入扩展域全量） | 待跑 |
| 集成 | worker 子进程生命周期（新增文件） | 待跑 |
| 回归 | runtime manifest + meta_tools + pi hardening | 待跑 |
