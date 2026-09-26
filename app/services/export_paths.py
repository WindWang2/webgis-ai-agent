"""Export 路径单一真相（F14 / ADR-0211 增补）。

``DATA_DIR/exports`` 的**唯一**派生点。此前三轨并行取值：

- ``app/api/routes/map.py`` import 期 ``EXPORT_DIR`` 常量 + makedirs；
- ``app/services/artifact_lifecycle.py`` import 期 ``EXPORT_DIR`` Path；
- ``app/services/artifact_registry.export_file_path`` 调用时实时取值。

DATA_DIR 在运行时被配置/测试覆写后，registry probe（调用时）与写盘/GC
（import 时快照）指向不同目录 —— probe 报 not-exists 而文件在旧目录。
本模块把三轨收口为一个调用时函数：所有消费方每次调用都重新解析
``settings.DATA_DIR``，monkeypatch/配置变更即刻全链生效。

职责边界：本模块只管 **root 目录**；``ref:export/<filename> → 路径`` 的
解析（charset 白名单等）仍归 artifact_registry（ref 语义与其GC/血缘
上下文同模块），其路径段必须经 ``exports_root()`` 派生。
"""
from __future__ import annotations

from pathlib import Path

from app.core.config import settings


def exports_root() -> Path:
    """全局导出交付物目录（调用时取值 —— 单一真相）。"""
    return Path(settings.DATA_DIR) / "exports"


def ensure_exports_root() -> Path:
    """exports_root() + 幂等 mkdir（写入方入口；读路径不应有副作用）。"""
    root = exports_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:  # pragma: no cover - 只读盘等环境故障由写路径自爆
        pass
    return root


__all__ = ["exports_root", "ensure_exports_root"]
