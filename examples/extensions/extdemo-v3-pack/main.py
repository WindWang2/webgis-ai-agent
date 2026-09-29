"""V3 Ecosystem Demo Pack —— ADR-0119 完成证明示例。

能力矩阵（全部在 worker 子进程内执行）：
- broker 出网（network allowlist + SSRF gate 由宿主强制）；
- 流式矢量 provider（streaming_vector mixin，合成 GIS-safe 数据）；
- 流式 model provider（V3 协议流帧）；
- 纯计算工具（区域形状指数）。

诚实声明：`synth` model provider 是合成事件流演示，不是真实推理模型；
`streams` provider 的数据全部内存合成，无外部网络依赖。
"""

from app.extensions_platform.sdk import ToolExtensionSpec
from app.extensions_platform.sdk.model import ModelProviderSpec
from app.extensions_platform.sdk.provider import StreamingVectorProvider


def activate(ctx):
    # ── 1) broker 出网工具 ──────────────────────────────────────────
    def fetch_title(url: str) -> dict:
        resp = ctx.broker.http_request(url, method="GET", timeout_s=10.0)
        body = resp.get("body", b"")
        return {
            "status": resp.get("status", 0),
            "content_type": resp.get("content_type", ""),
            "head": body[:200].decode("utf-8", "replace"),
            "truncated": bool(resp.get("truncated")),
        }

    ctx.register_tool(
        ToolExtensionSpec(
            name="fetch_title",
            description="fetch a URL through the capability broker (allowlist + SSRF gated)",
            func=fetch_title,
            side_effect="pure",
            parameters={
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        )
    )

    # ── 2) 纯计算 GIS 工具（正方形近似形状指数）─────────────────────
    def compute_index(area: float, perimeter: float) -> dict:
        if area <= 0 or perimeter <= 0:
            raise ValueError("area/perimeter must be positive")
        # 形状指数（正方形 = 1 / (2*sqrt(pi)) 归一 … 这里用最简单的
        # 周长/面积比演示「GIS-safe 确定性计算」语义）。
        ratio = perimeter / area
        return {"shape_index": round(ratio, 6), "deterministic": True}

    ctx.register_tool(
        ToolExtensionSpec(
            name="compute_index",
            description="deterministic GIS-safe shape index (square approximation demo)",
            func=compute_index,
            deterministic=True,
            parameters={
                "type": "object",
                "properties": {
                    "area": {"type": "number"},
                    "perimeter": {"type": "number"},
                },
                "required": ["area", "perimeter"],
            },
        )
    )

    # ── 3) 流式矢量 provider（合成数据）────────────────────────────
    class SyntheticStreams(StreamingVectorProvider):
        """内存合成矢量流：确定性网格点（100 个），零外部依赖。"""

        def __init__(self, ctx=None, profile=None):
            self._ctx = ctx
            self._profile = profile

        def stream_features(self, query: dict, page_size: int = 25):
            n = 100
            for i in range(n):
                x = (i % 10) * 0.1
                y = (i // 10) * 0.1
                yield {
                    "type": "Feature",
                    "properties": {"id": i, "cell": f"{x:.1f},{y:.1f}"},
                    "geometry": {"type": "Point", "coordinates": [x, y]},
                }

    ctx.register_data_provider_v3(
        "streams",
        SyntheticStreams,
        description="synthetic streaming vector provider (in-memory, deterministic)",
        mixins=("streaming_vector",),
    )

    # ── 4) 流式 model provider（合成事件流）────────────────────────
    def synth_invoke(request: dict, ctx):
        chunks = int(request.get("chunks", 5))
        if chunks <= 0 or chunks > 100:
            raise ValueError("chunks must be in [1, 100]")

        def _events():
            for i in range(chunks):
                yield {"type": "progress", "chunk": i + 1, "of": chunks}
            yield {
                "type": "final",
                "summary": f"synthetic inference over {chunks} chunks",
                "deterministic": True,
            }

        return _events()

    ctx.register_model_provider(
        ModelProviderSpec(
            provider_id="synth",
            description="synthetic streaming inference provider (demo)",
            invoke_fn=synth_invoke,
            capabilities=["streaming", "cancellation"],
            parameters={
                "type": "object",
                "properties": {"chunks": {"type": "integer"}},
            },
        )
    )
