"""Remote inference endpoint provider（ADR-0119 §3.7；Epic §C provider 形态）。

安全契约（R1-M8 冻结）：

- **allowlist 定义性权力**：endpoint 命中 operator 显式 allowlist 条目
  （``scheme://host:port`` 精确匹配，大小写不敏感）→ 跳过私网门
  （企业内网推理集群的合法形态；**只**能经配置授予，代码不存在第二入口）；
- 未命中 allowlist → 逐 URL 走 ``DataFabricSecurity.validate_url``
  （私网/回环/云元数据/解析 IP 全检）；
- redirect：``follow_redirects=False``，3xx 的 Location 逐跳重过策略
  （≤3 跳），绝不自动跟随；
- timeout：connect/read 显式；响应字节上限（output bomb）；错误体
  sanitize（ADR-0102 §7 口径）后才进异常文本；
- secret：credentials_ref → 值经 secrets 通道注入 header，值绝不入
  日志/异常/manifest。
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

import httpx
import numpy as np

from app.lib.modelops.capabilities import (
    DEVICE_CPU,
    TASK_SEMANTIC_SEGMENTATION,
    ProviderCapabilities,
)
from app.lib.modelops.descriptor import GeoModelDescriptor
from app.lib.modelops.errors import (
    OutputBudgetExceeded,
    ProviderError,
    ProviderLoadFailed,
    RemoteEndpointPolicyError,
    RemoteInferenceError,
)
from app.lib.modelops.resources import ResourceEstimate
from app.services.modelops.providers.base import (
    InferenceContext,
    LoadedModel,
    ProviderHealth,
    TileBatch,
    TileOutput,
    ensure_not_cancelled,
)
from app.services.modelops.config import remote_allowlist_from_env

logger = logging.getLogger(__name__)

#: remote 协议上限（诚实边界：JSON 嵌套列表编码，小 chip 专用通道）。
REMOTE_MAX_CHIP_SIDE = 128
REMOTE_MAX_BATCH = 4
REMOTE_MAX_RESPONSE_BYTES = 64 * 1024 * 1024
REMOTE_MAX_REDIRECTS = 3
REMOTE_CONNECT_TIMEOUT_S = 5.0
REMOTE_READ_TIMEOUT_S = 60.0


def sanitize_remote_error(body: str, *, max_len: int = 512) -> str:
    """错误体不可信：去控制字符/伪 XML 标签 + 截断（ADR-0102 §7 口径）。"""
    cleaned = "".join(ch for ch in body if ch.isprintable() or ch in "\t")
    for tag in ("<error>", "</error>", "<message>", "</message>"):
        cleaned = cleaned.replace(tag, "")
    return cleaned[:max_len]


@dataclass(frozen=True)
class RemoteEndpointPolicy:
    """remote endpoint 策略（显式 allowlist + 默认拒绝）。

    ``allowlist`` 成员形如 ``http://127.0.0.1:8765``（scheme+host+port
    精确；host 大小写不敏感）。**命中 = 跳过私网门**（定义性权力）。
    测试直接构造本对象注入（不经 env，避开 conftest parity 锁）。
    """

    allowlist: tuple = ()

    @classmethod
    def from_env(cls) -> "RemoteEndpointPolicy":
        return cls(allowlist=tuple(remote_allowlist_from_env()))

    def _entry_of(self, url: str) -> Optional[str]:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return None
        port = parts.port
        if port is None:
            port = 443 if parts.scheme == "https" else 80
        return f"{parts.scheme}://{parts.hostname.lower()}:{port}"

    def check(self, url: str) -> str:
        """策略裁决：返回规范化 endpoint；拒绝 → RemoteEndpointPolicyError。"""
        entry = self._entry_of(url)
        if entry is None:
            raise RemoteEndpointPolicyError(f"remote endpoint not parseable: {url[:120]!r}")
        if entry in {a.strip().lower() for a in self.allowlist}:
            return url
        # 未命中 allowlist：走仓库统一 SSRF 门（私网/回环/云元数据全检）。
        from app.services.data_fabric.security import DataFabricSecurity, DataFabricSecurityError

        try:
            DataFabricSecurity.validate_url(url)
        except DataFabricSecurityError as exc:
            raise RemoteEndpointPolicyError(f"remote endpoint rejected by SSRF gate: {exc}") from exc
        return url


class RemoteInferenceProvider:
    """remote endpoint provider（httpx；JSON 嵌套列表协议）。

    实例 id 形如 ``remote@<scheme://host:port>``（每个 endpoint 一个实例，
    经 ProviderRegistry 注册——``descriptor.provider_ref`` 仍然只解析
    registry 实例 id，R1-C1 语义不破）。
    """

    def __init__(
        self,
        endpoint: str,
        *,
        provider_id: Optional[str] = None,
        policy: Optional[RemoteEndpointPolicy] = None,
        client: Optional[httpx.Client] = None,
        read_timeout_s: float = REMOTE_READ_TIMEOUT_S,
    ) -> None:
        self._endpoint = policy.check(endpoint) if policy else endpoint
        self._provider_id = provider_id or f"remote@{self._endpoint}"
        self._policy = policy or RemoteEndpointPolicy.from_env()
        self._client = client
        self._read_timeout_s = read_timeout_s
        self._lock = threading.Lock()
        self._in_flight = 0

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=self._provider_id,
            provider_type="remote_endpoint",
            semantic_version="remote-json/1.0.0",
            tasks=frozenset({TASK_SEMANTIC_SEGMENTATION}),
            prompt_modes=frozenset(),
            devices=frozenset({DEVICE_CPU}),
            max_batch=REMOTE_MAX_BATCH,
            streaming=False,
            cancellation=False,  # HTTP 通道无协作取消（墙钟 deadline 兜底）
            text_prompt=False,
            max_output_bytes=REMOTE_MAX_RESPONSE_BYTES,
        )

    # ── lifecycle ───────────────────────────────────────────────────
    def load(self, descriptor: GeoModelDescriptor, *, device: str) -> LoadedModel:
        if descriptor.provider_type != "remote_endpoint":
            raise ProviderLoadFailed(
                f"remote provider requires provider_type=remote_endpoint "
                f"(descriptor declares {descriptor.provider_type!r})"
            )
        endpoint = self._policy.check(self._endpoint)
        if descriptor.task_types and TASK_SEMANTIC_SEGMENTATION not in descriptor.task_types:
            raise ProviderLoadFailed("remote JSON provider serves semantic_segmentation only")
        h, w = descriptor.spatial.chip_size
        if h > REMOTE_MAX_CHIP_SIDE or w > REMOTE_MAX_CHIP_SIDE:
            raise ProviderLoadFailed(
                f"remote JSON protocol caps chip at {REMOTE_MAX_CHIP_SIDE}px "
                f"(descriptor declares {h}x{w})"
            )
        return LoadedModel(
            descriptor=descriptor,
            provider_id=self._provider_id,
            device=device,
            handle_id=f"{descriptor.model_id}@{descriptor.model_version}#remote",
            state={"endpoint": endpoint},
        )

    def warmup(self, model: LoadedModel) -> Dict[str, Any]:
        resp = self._request(self._endpoint, method="GET", path="/health")
        if resp.status_code != 200:
            raise RemoteInferenceError(f"remote health returned {resp.status_code}")
        return {"warmed": True}

    def estimate_resources(
        self, descriptor: GeoModelDescriptor, *, batch: int, device: str
    ) -> ResourceEstimate:
        h, w = descriptor.spatial.chip_size
        per_chip = descriptor.input_bands * h * w * 4
        return ResourceEstimate(
            vram_bytes=0,
            host_ram_bytes=per_chip * max(1, batch) * 2,
            recommended_batch=min(REMOTE_MAX_BATCH, max(1, batch)),
            externally_enforced=True,  # 远端内存不可观测——如实标注
        )

    # ── HTTP 核 ─────────────────────────────────────────────────────
    def _client_or_default(self) -> httpx.Client:
        if self._client is not None:
            return self._client
        return httpx.Client(
            follow_redirects=False,
            timeout=httpx.Timeout(
                connect=REMOTE_CONNECT_TIMEOUT_S, read=self._read_timeout_s, write=30.0, pool=30.0
            ),
            limits=httpx.Limits(max_connections=4),
        )

    def _request(self, endpoint: str, *, method: str, path: str,
                 json_body: Optional[Dict[str, Any]] = None) -> httpx.Response:
        current = endpoint.rstrip("/") + path
        client = self._client_or_default()
        own = self._client is None
        try:
            hops = 0
            while True:
                self._policy.check(current)  # 每跳重过策略（redirect 防护）
                # R1-M6：流式读取 + 逐块字节上限 —— 恶意端点不能先打爆
                # 内存再被拒（对齐 data_fabric bounded_get 语义）。
                with client.stream(
                    method, current, json=json_body, follow_redirects=False
                ) as resp:
                    if resp.is_redirect:
                        hops += 1
                        if hops > REMOTE_MAX_REDIRECTS:
                            raise RemoteInferenceError(
                                f"remote redirect chain > {REMOTE_MAX_REDIRECTS}"
                            )
                        location = resp.headers.get("location", "")
                        if not location:
                            raise RemoteInferenceError(
                                "remote redirect without Location header"
                            )
                        current = str(httpx.URL(current).join(location))
                        continue
                    content = bytearray()
                    for chunk in resp.iter_bytes(1024 * 1024):
                        content.extend(chunk)
                        if len(content) > REMOTE_MAX_RESPONSE_BYTES:
                            raise OutputBudgetExceeded(
                                f"remote response exceeds {REMOTE_MAX_RESPONSE_BYTES} "
                                "bytes mid-stream"
                            )
                    resp._content = bytes(content)
                    return resp
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise RemoteInferenceError(f"remote transport failure: {type(exc).__name__}") from exc
        finally:
            if own:
                client.close()

    # ── infer ───────────────────────────────────────────────────────
    def infer(
        self, model: LoadedModel, batch: TileBatch, ctx: InferenceContext
    ) -> TileOutput:
        with self._lock:
            self._in_flight += 1
        try:
            ensure_not_cancelled(ctx)
            n, c, h, w = batch.pixels.shape
            if h > REMOTE_MAX_CHIP_SIDE or w > REMOTE_MAX_CHIP_SIDE:
                raise ProviderError(
                    f"remote chip {h}x{w} exceeds protocol cap {REMOTE_MAX_CHIP_SIDE}px"
                )
            if n > REMOTE_MAX_BATCH:
                raise ProviderError(f"remote batch {n} exceeds cap {REMOTE_MAX_BATCH}")
            payload = {
                "model_id": model.descriptor.model_id,
                "model_version": model.descriptor.model_version,
                "task": model.descriptor.task_types[0],
                "pixels": batch.pixels.round(6).tolist(),
                "width": w,
                "height": h,
                "bands": c,
                "batch": n,
            }
            resp = self._request(self._endpoint, method="POST", path="/infer",
                                 json_body=payload)
            if len(resp.content) > REMOTE_MAX_RESPONSE_BYTES:
                raise OutputBudgetExceeded(
                    f"remote response {len(resp.content)} bytes exceeds cap"
                )
            if resp.status_code != 200:
                raise RemoteInferenceError(
                    f"remote inference returned {resp.status_code}: "
                    f"{sanitize_remote_error(resp.text)}"
                )
            try:
                data = resp.json()
            except ValueError as exc:
                raise RemoteInferenceError("remote response is not valid JSON") from exc
            probs_raw = data.get("class_probabilities")
            if not isinstance(probs_raw, list):
                raise RemoteInferenceError("remote response lacks class_probabilities[]")
            probs = np.asarray(probs_raw, dtype=np.float32)
            if probs.ndim == 3:
                probs = probs[None]
            if probs.shape[0] != n or probs.shape[-2:] != (h, w):
                raise RemoteInferenceError(
                    f"remote probability shape {probs.shape} mismatches batch ({n},{h},{w})"
                )
            return TileOutput(
                task_type=TASK_SEMANTIC_SEGMENTATION, class_probabilities=probs
            )
        finally:
            with self._lock:
                self._in_flight -= 1

    def cancel(self, model: LoadedModel, run_id: str) -> bool:
        return False  # HTTP 通道无协作取消（capabilities 如实声明）

    def health(self) -> ProviderHealth:
        with self._lock:
            return ProviderHealth(healthy=True, in_flight=self._in_flight,
                                  detail="remote provider health is endpoint-scoped")

    def unload(self, model: LoadedModel) -> None:
        return None
