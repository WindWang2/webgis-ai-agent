"""统一关联主干（RuntimeContext）。

``RuntimeContext`` 承载一次 turn 的顶层身份：request / session / turn / run。
它通过 ContextVar 传播，随 asyncio.Task 自动复制（并发 session/turn 互不污染），
随 ``asyncio.to_thread`` 自动穿透到同步 GIS 工具线程（Py3.9+ copy_context）。

为什么不用 module-global mutable "current request id"：单例全局可变状态会让并发
session 互相串号（pi_agent_harness.set_correlation 的旧反模式即此）。ContextVar
天然 per-task，是并发安全的关联载体——这与已有的 ``CURRENT_TOKEN``（取消）、
``cache_hit_var``（缓存命中）、``CURRENT_ORIGIN``（durable job 来源）同构。

嵌套合并语义：``bind_runtime_context(turn_id=...)`` 在外层已有 request_id 时，会
*继承* 外层未覆盖的字段（child 继承 parent）。典型栈：

    chat_stream 生成器内:  bind_runtime_context(request_id=req, session_id=sid)
      └─ stream_prompt 内:   bind_runtime_context(turn_id=tid, run_id=rid)

read→construct→set 三步之间不得有 ``await``（纯 dataclass 构造），保证 per-task
原子合并。
"""
from __future__ import annotations

import contextlib
import contextvars
import uuid
from dataclasses import dataclass, replace
from typing import Dict, Iterator, Optional


@dataclass(frozen=True)
class RuntimeContext:
    """一次 turn 的顶层关联身份（不可变，可安全跨边界携带）。

    所有字段可选——不同阶段在不同层级绑定：HTTP 入口绑定 request_id/session_id，
    turn 开始时绑定 turn_id/run_id，工具派生 durable job 时继承全部。
    """

    request_id: Optional[str] = None
    session_id: Optional[str] = None
    turn_id: Optional[str] = None
    run_id: Optional[str] = None
    # 活动项目（ADR-0069 记忆的作用域）。HTTP 入口从请求体解析；工具层
    # （如 recipe 推荐）据此读取项目制图记忆——session 域与 project 域的
    # 唯一桥接点，避免到处传参或另建全局映射。
    project_id: Optional[str] = None

    def merged(self, **overrides: Optional[str]) -> "RuntimeContext":
        """返回一个用非 None 覆盖项更新后的新上下文（frozen，不修改自身）。"""
        return replace(
            self,
            **{k: v for k, v in overrides.items() if v is not None},  # type: ignore[arg-type]
        )

    def as_log_dict(self) -> Dict[str, Optional[str]]:
        """用于结构化日志的关联字段（无敏感数据）。"""
        return {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "run_id": self.run_id,
            "project_id": self.project_id,
        }


_CURRENT: contextvars.ContextVar[Optional[RuntimeContext]] = contextvars.ContextVar(
    "webgis_runtime_ctx", default=None
)


def _safe_reset(token: contextvars.Token) -> None:
    """Reset a ContextVar token, tolerating a cross-context reset.

    An async generator driven across multiple asyncio tasks (e.g. a test that
    wraps ``gen.__anext__()`` in ``asyncio.ensure_future``) can have its ``set``
    in one Context and its ``reset`` in a copied Context — ``reset`` then raises
    ``ValueError: <Token> was created in a different Context``. In that case the
    value is already isolated in the copied Context (it dies with that task), so
    skipping the reset is correct and leak-free in practice. In normal
    single-task driving (production SSE), set + reset share a Context and reset
    succeeds.
    """
    try:
        _CURRENT.reset(token)
    except (ValueError, LookupError):
        pass


def current_runtime_context() -> Optional[RuntimeContext]:
    """当前 task 的运行时关联上下文（无则 None）。"""
    return _CURRENT.get()


def runtime_context_snapshot() -> Optional[RuntimeContext]:
    """显式快照（frozen dataclass 本就不可变；提供此函数用于跨边界携带的语义清晰）。"""
    return _CURRENT.get()


@contextlib.contextmanager
def bind_runtime_context(
    *,
    request_id: Optional[str] = None,
    session_id: Optional[str] = None,
    turn_id: Optional[str] = None,
    run_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Iterator[RuntimeContext]:
    """绑定运行时关联上下文（与外层合并），离开作用域时恢复。

    child 继承 parent 的未覆盖字段。read→construct→set 之间无 await，per-task 原子。
    退出时 ``reset(token)``，异常/重入均安全。
    """
    parent = _CURRENT.get()
    base = parent if parent is not None else RuntimeContext()
    ctx = base.merged(
        request_id=request_id,
        session_id=session_id,
        turn_id=turn_id,
        run_id=run_id,
        project_id=project_id,
    )
    token = _CURRENT.set(ctx)
    try:
        yield ctx
    finally:
        _safe_reset(token)


# ── id 生成器（与既有风格一致：短前缀 + uuid hex）─────────────────────────────

def new_request_id() -> str:
    return f"req-{uuid.uuid4().hex[:12]}"


def new_turn_id() -> str:
    """turn id（与 Pi bridge 既有 ``turn-<hex12>`` 风格一致）。"""
    return f"turn-{uuid.uuid4().hex[:12]}"


def new_run_id() -> str:
    """run id（替代 jobs.context.new_run_id —— 那是零调用者的死代码；这里是首个真实调用点）。"""
    return f"run-{uuid.uuid4().hex[:12]}"
