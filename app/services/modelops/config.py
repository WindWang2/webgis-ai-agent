"""ModelOps runtime knobs —— env 旋钮（``app/lib/geo_raster/env.py`` 同模式）。

约定：新旋钮必须同步 ``.env.example`` 与 ``tests/conftest.py``
``_ENV_BASELINE``（parity 锁）。全部旋钮有界（上限硬编码），env 只能
在界内取值；解析失败回退默认值（typed 告警不入日志噪音，仅 debug）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

#: 进程内同时 loaded 的模型数上界（LRU 驱逐超出者）。
_MAX_LOADED_MODELS_CAP = 64
_DEFAULT_MAX_LOADED_MODELS = 4

#: loaded 模型累计内存估计上界（bytes；真实 VRAM 探测不可用时按此记账）。
_MAX_VRAM_BUDGET_BYTES_CAP = 256 * 1024**3  # 256 GiB 硬上限
_DEFAULT_VRAM_BUDGET_BYTES = 8 * 1024**3

#: 并发推理运行上界（引擎线程池大小）。
_MAX_CONCURRENT_INFERENCES_CAP = 32
_DEFAULT_MAX_CONCURRENT_INFERENCES = 2

#: 单次推理默认墙钟 deadline（秒；可按调用覆盖，硬上限 3600）。
_DEFAULT_INFERENCE_DEADLINE_S = 600.0
_INFERENCE_DEADLINE_CAP_S = 3600.0

#: reuse cache 缺省上界。
_DEFAULT_REUSE_MAX_ENTRIES = 128
_REUSE_MAX_ENTRIES_CAP = 4096
_DEFAULT_REUSE_MAX_BYTES = 2 * 1024**3
_REUSE_MAX_BYTES_CAP = 64 * 1024**3

#: V3 §B：子进程 worker deadline（秒；超时 kill）。
_DEFAULT_SUBPROCESS_DEADLINE_S = 120.0
_SUBPROCESS_DEADLINE_CAP_S = 900.0


def _env_int(name: str, default: int, cap: int, floor: int = 1) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(floor, min(value, cap))


def _env_float(name: str, default: float, cap: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(0.0, min(value, cap))


def _env_allowlist(name: str) -> List[str]:
    """逗号分隔的 endpoint allowlist（scheme://host:port 精确项）。"""
    raw = os.environ.get(name, "")
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def _env_raw_list(name: str) -> List[str]:
    """逗号分隔的原始条目（不 lower——路径大小写在 Windows 有意义）。"""
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class ModelOpsSettings:
    """进程级快照（启动时读取一次；测试可显式构造覆盖）。"""

    registry_dir: Path
    max_loaded_models: int = _DEFAULT_MAX_LOADED_MODELS
    vram_budget_bytes: int = _DEFAULT_VRAM_BUDGET_BYTES
    max_concurrent_inferences: int = _DEFAULT_MAX_CONCURRENT_INFERENCES
    inference_deadline_s: float = _DEFAULT_INFERENCE_DEADLINE_S
    reuse_max_entries: int = _DEFAULT_REUSE_MAX_ENTRIES
    reuse_max_bytes: int = _DEFAULT_REUSE_MAX_BYTES
    remote_allowlist: List[str] = field(default_factory=list)
    #: V3 §B：operator 登记的子进程 worker（slot=/abs/path，原始大小写）。
    subprocess_workers: List[str] = field(default_factory=list)
    subprocess_deadline_s: float = _DEFAULT_SUBPROCESS_DEADLINE_S
    #: V3 §E：warm pool 常驻模型 id 清单（逗号分隔）。
    warm_pool: List[str] = field(default_factory=list)

    @classmethod
    def load(cls, *, base_dir: Optional[Path] = None) -> "ModelOpsSettings":
        registry_dir = Path(
            os.environ.get("MODELOPS_REGISTRY_DIR", "") or (base_dir or Path("data")) / "modelops"
        )
        return cls(
            registry_dir=registry_dir,
            max_loaded_models=_env_int(
                "MODELOPS_MAX_LOADED_MODELS", _DEFAULT_MAX_LOADED_MODELS, _MAX_LOADED_MODELS_CAP
            ),
            vram_budget_bytes=_env_int(
                "MODELOPS_VRAM_BUDGET_BYTES",
                _DEFAULT_VRAM_BUDGET_BYTES,
                _MAX_VRAM_BUDGET_BYTES_CAP,
                floor=0,
            ),
            max_concurrent_inferences=_env_int(
                "MODELOPS_MAX_CONCURRENT_INFERENCES",
                _DEFAULT_MAX_CONCURRENT_INFERENCES,
                _MAX_CONCURRENT_INFERENCES_CAP,
            ),
            inference_deadline_s=_env_float(
                "MODELOPS_INFERENCE_DEADLINE_S",
                _DEFAULT_INFERENCE_DEADLINE_S,
                _INFERENCE_DEADLINE_CAP_S,
            ),
            reuse_max_entries=_env_int(
                "MODELOPS_REUSE_MAX_ENTRIES", _DEFAULT_REUSE_MAX_ENTRIES, _REUSE_MAX_ENTRIES_CAP
            ),
            reuse_max_bytes=_env_int(
                "MODELOPS_REUSE_MAX_BYTES", _DEFAULT_REUSE_MAX_BYTES, _REUSE_MAX_BYTES_CAP, floor=0
            ),
            remote_allowlist=_env_allowlist("MODELOPS_REMOTE_ALLOWLIST"),
            subprocess_workers=_env_raw_list("MODELOPS_SUBPROCESS_WORKERS"),
            subprocess_deadline_s=_env_float(
                "MODELOPS_SUBPROCESS_DEADLINE_S",
                _DEFAULT_SUBPROCESS_DEADLINE_S,
                _SUBPROCESS_DEADLINE_CAP_S,
            ),
            warm_pool=_env_raw_list("MODELOPS_WARM_POOL"),
        )


def remote_allowlist_from_env() -> List[str]:
    """供 remote client 读取 operator allowlist（默认空 = 全部拒绝）。"""
    return _env_allowlist("MODELOPS_REMOTE_ALLOWLIST")
