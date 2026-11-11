"""audit4 #997: LLM 配置的单一解析点（Model Registry 的最小前体）。

此前 ``LLMConfig`` 的组装散落在 6 处（engine __init__/title/planner、
spatial_reasoning 直读 settings、config 路由、测试），且运行时改配
（``POST /api/v1/config/llm``）只写引擎实例字段 —— 同进程内 spatial_reasoning
等直读 settings 的调用点永远用旧配置，行为不可复现。

本模块收敛为 ``resolve_llm_config(role)``：
- 基础三元组（base_url/model/api_key）：运行时覆盖 > Settings；
- 角色差异化：execution / planner / title / spatial 各自的模型与输出预算；
- 采样参数：temperature 全局（角色级细分待 Model Registry 全面落地）。

这是零协议变更的一步（仍只有一个 OpenAI 兼容协议）；Provider 接口拆分
（Anthropic/Ollama/vLLM 适配位）在 Model Registry 后续阶段引入。
"""
from __future__ import annotations

import threading
from enum import Enum

from app.core.config import settings
from app.services.chat.llm_client import LLMConfig


class ModelRole(str, Enum):
    EXECUTION = "execution"   # 主执行循环（流式 tool-call）
    PLANNER = "planner"       # CanonicalPlan 规划调用
    TITLE = "title"           # 会话标题/摘要等轻量辅助任务
    SPATIAL = "spatial"       # 空间推演工具的内嵌 LLM 调用


# 角色默认输出预算（tokens）。title 只需要一行短文本；spatial 推演输出
# 结构化 JSON；执行角色用 Settings 的全量预算。
_ROLE_MAX_TOKENS = {
    ModelRole.TITLE: 512,
    ModelRole.SPATIAL: 4096,
}

_lock = threading.Lock()
# 运行时覆盖（admin 面板 POST /api/v1/config/llm 写入；进程生命周期内有效）
_runtime_overrides: dict[str, str] = {}


def set_runtime_override(
    base_url: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
) -> None:
    """运行时覆盖基础三元组（None 表示不改）。由 config 路由与引擎 update_config 调用。"""
    with _lock:
        if base_url:
            _runtime_overrides["base_url"] = base_url.rstrip("/")
        if model:
            _runtime_overrides["model"] = model
        if api_key:
            _runtime_overrides["api_key"] = api_key


def clear_runtime_overrides() -> None:
    with _lock:
        _runtime_overrides.clear()


def _base_triple() -> tuple[str, str, str]:
    with _lock:
        ov = dict(_runtime_overrides)
    return (
        ov.get("base_url") or settings.LLM_BASE_URL,
        ov.get("model") or settings.LLM_MODEL,
        ov.get("api_key") or settings.LLM_API_KEY,
    )


def resolve_llm_config(role: ModelRole | str = ModelRole.EXECUTION) -> LLMConfig:
    """按角色解析一份 LLMConfig —— 全进程唯一的 LLM 基础配置解析点。"""
    try:
        role = ModelRole(role)
    except ValueError:
        role = ModelRole.EXECUTION
    base_url, model, api_key = _base_triple()
    if role is ModelRole.PLANNER and settings.LLM_PLANNER_MODEL:
        model = settings.LLM_PLANNER_MODEL
    elif role is ModelRole.TITLE and settings.LLM_TITLE_MODEL:
        model = settings.LLM_TITLE_MODEL
    return LLMConfig(
        base_url=base_url,
        model=model,
        api_key=api_key,
        use_prompt_caching=settings.LLM_PROMPT_CACHING_ENABLED,
        max_tokens=_ROLE_MAX_TOKENS.get(role, settings.LLM_MAX_TOKENS),
        temperature=settings.LLM_TEMPERATURE,
        timeout_s=settings.LLM_TIMEOUT_S,
    )


# ---------------------------------------------------------------------------
# 配置代际（ADR-0288 / W08，#2061）：期望态 vs 实际生效态的可对账指纹。
#
# Pi 子进程在 spawn 时读取一份解析后的配置快照（models.json/settings.json
# 由 ``pi_rpc_client._spawn_process`` 写盘）；运行时覆盖（/config/llm）改的
# 是本模块的 override —— 两边可能不一致（旧 worker 仍在跑旧配置）。
# ``config_generation()`` 给解析结果一个**不可逆**短指纹：日志/receipt/诊断
# 面可对账「期望代 vs 某 worker 的生效代」，绝不泄露 key 明文。
# ---------------------------------------------------------------------------

def config_generation() -> str:
    """当前解析后配置（execution 角色）的不可逆短指纹（sha256[:16]）。

    输入 = 基础三元组（运行时覆盖 > Settings）；输出稳定、不可逆 ——
    同配置同指纹，改任一元素即换代。
    """
    import hashlib

    base_url, model, api_key = _base_triple()
    payload = f"{base_url}\n{model}\n{api_key}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
