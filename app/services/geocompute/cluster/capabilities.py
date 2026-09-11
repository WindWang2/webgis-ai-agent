"""GeoCompute V7 worker 能力剖面（wave 4，01-architecture.md §2.1）。

typed 契约 + 诚实探针：全部字段取自执行环境的**可证事实**，探测缺席 →
typed 缺省（gpu_count=0 / mem_mb=0），绝不虚构。profile 在 worker_ready
探一次（静态事实），心跳只续期 —— 每 10s 重探是浪费且会产生锯齿容量。

诚实边界：探针不请求 root、不加载重库（torch 只做 import 探测并容忍
缺席）；GPU 探测经 ``nvidia-smi --query-gpu`` 子进程（缺席/超时 → 0）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

#: capability JSON 列写入侧钳制（超限截断为 None = V6 profiles-only 语义）。
MAX_CAPABILITY_JSON_BYTES = 4 * 1024

#: GPU 探测子进程上界（无 nvidia-smi 的机器上 PATH 查找本身可慢）。
_GPU_PROBE_TIMEOUT_S = 3.0

#: 能力词表（placement eligible 的封闭维度；开放词表会让 gating 静默失效）。
CAPABILITY_VOCABULARY: frozenset[str] = frozenset({
    "light_cpu", "heavy_cpu", "high_memory",
    "raster", "vector", "science", "gpu", "network", "external_io",
})

_BACKEND_PROBES: tuple[tuple[str, str], ...] = (
    # (backend 名, import 名) —— 版本事实（缺席 = ""，诚实）
    ("raster", "rasterio"),
    ("vector", "geopandas"),
    ("science", "scipy"),
    ("gpu", "cupy"),
)


class GpuCard(BaseModel):
    """单卡清单（V8）：型号 + 显存（探测缺席 → 空表，诚实未知）。

    ``gpu_count``/``gpu_mem_mb`` 仍是放置 gating 的**汇总真相**（V7 语义
    不变）；卡级清单服务于 V8 多卡路由与型号亲和（advisory 层）。
    """

    name: str = Field(default="", max_length=120)
    mem_mb: int = Field(default=0, ge=0)


class WorkerCapabilityProfile(BaseModel):
    """worker 能力剖面（geocompute_workers.capability 列的契约投影）。

    放置语义（01-architecture.md §2.2）：eligible 判断的输入真相；
    缺省（NULL 列）= 只按 profiles 队列匹配（V6 语义，兼容旧 worker）。

    V8 新增（全部可空缺省，旧行/旧 worker 逐字节兼容）：
    - ``gpus``：卡级清单（型号 + 显存）；
    - ``disk_free_mb``：执行机 temp/缓存根的可用磁盘（spill/exchange
      放置的诚实输入；探测失败 = 0 = 未知，绝不虚构）。
    """

    cpu_cores: int = Field(default=0, ge=0, le=4096)
    mem_mb: int = Field(default=0, ge=0, le=4_194_304)
    gpu_count: int = Field(default=0, ge=0, le=64)
    gpu_mem_mb: int = Field(default=0, ge=0)
    gpus: list[GpuCard] = Field(default_factory=list, max_length=8)
    disk_free_mb: int = Field(default=0, ge=0)
    #: backend → 版本（缺席 = ""；≤16 项防词表膨胀）
    backends: Dict[str, str] = Field(default_factory=dict, max_length=16)
    #: 封闭词表能力（CAPABILITY_VOCABULARY 子集）
    capabilities: list[str] = Field(default_factory=list, max_length=16)
    #: locality 域（WEBGIS_WORKER_ZONE，缺省 "default"）
    zone: str = Field(default="default", max_length=64)
    #: 执行栈指纹（python 主次版本 + geo 栈版本哈希前 12 —— 版本漂移可见性）
    version: str = Field(default="", max_length=64)

    def satisfies(self, *, min_cpu: int = 0, min_mem_mb: int = 0, gpu: int = 0) -> bool:
        """run/节点资源 envelope → 本 worker 是否合格（纯函数）。"""
        if min_cpu > 0 and self.cpu_cores < min_cpu:
            return False
        if min_mem_mb > 0 and self.mem_mb < min_mem_mb:
            return False
        if gpu > 0 and self.gpu_count < gpu:
            return False
        return True


def _probe_mem_mb() -> int:
    try:
        with open("/proc/meminfo", "rb") as fh:
            for line in fh:
                if line.startswith(b"MemTotal:"):
                    return int(line.split()[1]) // 1024
    except Exception:  # noqa: BLE001 - 非 Linux / 受限容器 → 诚实未知
        pass
    return 0


def _probe_gpu_cards() -> list[GpuCard]:
    """卡级清单（name + memory.total）。nvidia-smi 缺席/超时/解析失败 → []。"""
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True, text=True, timeout=_GPU_PROBE_TIMEOUT_S,
            check=False,
        )
        if out.returncode != 0:
            return []
        cards: list[GpuCard] = []
        for ln in out.stdout.splitlines():
            parts = [p.strip() for p in ln.split(",") if p.strip()]
            if not parts:
                continue
            name = parts[0][:120]
            mem_mb = 0
            if len(parts) > 1:
                try:
                    mem_mb = int(float(parts[1]))
                except ValueError:
                    mem_mb = 0
            cards.append(GpuCard(name=name, mem_mb=mem_mb))
            if len(cards) >= 8:
                break
        return cards
    except Exception:  # noqa: BLE001 - 无 GPU 是常态不是故障
        return []


def _probe_gpu() -> tuple[int, int]:
    """(gpu_count, total_mem_mb)。nvidia-smi 缺席/超时/解析失败 → (0, 0)。"""
    cards = _probe_gpu_cards()
    return len(cards), sum(c.mem_mb for c in cards)


def _probe_disk_free_mb() -> int:
    """执行机 spill/缓存根的可用磁盘（MiB）。探测失败 → 0（诚实未知）。

    根目录取 ``WEBGIS_BLOB_ROOT``（内容寻址存储根，与 exchange 同一磁盘
    域）；缺席时退化到系统 temp —— 两者都不可得（受限容器）= 0。
    """
    import shutil
    import tempfile

    root = (os.environ.get("WEBGIS_BLOB_ROOT", "") or "").strip()
    target = root or tempfile.gettempdir()
    try:
        usage = shutil.disk_usage(target)
        return int(usage.free // (1024 * 1024))
    except Exception:  # noqa: BLE001 - 非 POSIX/受限容器 → 诚实未知
        return 0


def _version_fingerprint(versions: Dict[str, str]) -> str:
    import sys

    facts = {"python": "{}.{}".format(*sys.version_info[:2]), **versions}
    payload = json.dumps(facts, sort_keys=True, ensure_ascii=False)
    return "v" + hashlib.sha1(payload.encode(), usedforsecurity=False).hexdigest()[:12]


def probe_capability() -> WorkerCapabilityProfile:
    """执行环境 → 能力剖面（worker_ready 一次；诚实降级，绝不抛出）。

    capabilities 全部由**可证事实**推导（round1 M3：不得把全部词表词
    无条件写进每个 worker —— 无 rasterio 报 raster、2GB 报 high_memory
    都会让能力列失去信息量）：
    - backend import 成功 → 对应能力词；
    - gpu_count > 0 → ``gpu``；
    - cpu_cores ≥ 4 → ``heavy_cpu``；> 0 → ``light_cpu``；
    - mem_mb ≥ 8192 → ``high_memory``；
    - network / external_io 无本机可证事实 → 不声明。
    """
    backends: Dict[str, str] = {}
    capabilities: list[str] = []
    for name, module in _BACKEND_PROBES:
        try:
            mod = __import__(module)
            backends[name] = str(getattr(mod, "__version__", "") or "")
            if backends[name]:
                capabilities.append(name)
        except Exception:  # noqa: BLE001 - 未安装是事实不是错误
            backends[name] = ""
    gpu_cards = _probe_gpu_cards()
    gpu_count, gpu_mem_mb = len(gpu_cards), sum(c.mem_mb for c in gpu_cards)
    if gpu_count > 0:
        capabilities.append("gpu")
    mem_mb = _probe_mem_mb()
    cpu = os.cpu_count() or 0
    if cpu >= 4:
        capabilities.append("heavy_cpu")
    if cpu > 0:
        capabilities.append("light_cpu")
    if mem_mb >= 8192:
        capabilities.append("high_memory")
    capabilities = sorted({
        c for c in capabilities if c in CAPABILITY_VOCABULARY
    })[:16]
    versions = {k: v for k, v in backends.items() if v}
    zone = (os.environ.get("WEBGIS_WORKER_ZONE", "") or "default").strip()[:64]
    return WorkerCapabilityProfile(
        cpu_cores=cpu,
        mem_mb=mem_mb,
        gpu_count=gpu_count,
        gpu_mem_mb=gpu_mem_mb,
        gpus=gpu_cards,
        disk_free_mb=_probe_disk_free_mb(),
        backends=backends,
        capabilities=capabilities,
        zone=zone or "default",
        version=_version_fingerprint(versions),
    )


def capability_json(profile: Optional[WorkerCapabilityProfile]) -> Optional[dict[str, Any]]:
    """profile → DB 列投影（≤4KB 钳制；超限 → None = V6 语义，诚实降级）。"""
    if profile is None:
        return None
    try:
        encoded = json.dumps(
            profile.model_dump(), ensure_ascii=False, default=str
        ).encode("utf-8")
    except (TypeError, ValueError):
        return None
    if len(encoded) > MAX_CAPABILITY_JSON_BYTES:
        logger.warning("[geocompute-v7] capability projection exceeds %d bytes",
                       MAX_CAPABILITY_JSON_BYTES)
        return None
    return profile.model_dump()


def capability_from_row(raw: Optional[dict[str, Any]]) -> Optional[WorkerCapabilityProfile]:
    """DB 列 → typed profile（损坏/超界 → None，placement 退回 V6 语义）。"""
    if not isinstance(raw, dict):
        return None
    try:
        profile = WorkerCapabilityProfile(**{
            k: v for k, v in raw.items()
            if k in WorkerCapabilityProfile.model_fields
        })
    except Exception:  # noqa: BLE001 - 旧/脏数据按缺席处理
        return None
    profile.capabilities = [
        c for c in profile.capabilities if c in CAPABILITY_VOCABULARY
    ][:16]
    return profile
