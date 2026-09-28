"""extdemo-ml-pack：V2 model_provider 示例（活文档）。

演示 ModelProviderSpec 全部契约点：
- 声明式投影：``ctx.register_model_provider(spec)`` → 类型化调用工具
  ``extdemoml_wordfreq_invoke``（agent 经 ToolRegistry 正常派发）；
- 流式：invoke 返回事件迭代器（``stream_features`` 同风格的协作式取消）；
- 凭据：``ctx.get_secret("demo_key")``（供给即授权；未供给时 typed 拒绝，
  但本模型把凭据仅用于计数校验，永不回显）；
- 确定性：相同输入永远相同输出（测试 oracle）。
"""

from app.extensions_platform.sdk import ModelProviderSpec

_REQUIRED_SECRET_PREFIX = "demo-"


def _wordfreq(request: dict, ctx):
    """确定性词频模型：流式逐 token 事件 + final 聚合。"""
    text = str(request.get("text") or "")
    top_k = int(request.get("top_k") or 3)
    # 凭据演示：供给存在性校验（值只参与派生的 nonce，不回显）。
    key = ctx.get_secret("demo_key")
    nonce = len(key) ^ len(_REQUIRED_SECRET_PREFIX)
    tokens = [t for t in text.split() if t]
    freq: dict[str, int] = {}
    for token in tokens:
        freq[token] = freq.get(token, 0) + 1
        yield {"type": "token", "token": token, "count": freq[token]}
    ranked = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))[:top_k]
    yield {
        "type": "final",
        "top_k": [{"token": t, "count": c} for t, c in ranked],
        "total_tokens": len(tokens),
        "nonce": nonce,
    }


def activate(ctx):
    # 兄弟模块挂到入口模块命名空间（diagnostics_entry "health:check" 依赖
    # getattr(入口模块, "health") 可解析；与 extdemo-pack 的既有模式一致）。
    globals()["health"] = ctx.load_sibling("health")
    ctx.register_model_provider(
        ModelProviderSpec(
            provider_id="wordfreq",
            description="Deterministic offline word-frequency model (streaming demo).",
            invoke_fn=_wordfreq,
            capabilities=["streaming", "cancellation"],
            credentials_ref="demo_key",
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "input text"},
                    "top_k": {"type": "integer", "description": "report top-k tokens"},
                },
                "required": ["text"],
            },
        )
    )
