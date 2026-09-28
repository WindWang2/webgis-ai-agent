"""P2-5 专用：首查失明的 Session 包装（模拟先查后插的并发窗口）。

首个 ``execute``（预查）返回"无行"；后续 execute 透传真实 session——
INSERT 撞唯一约束 → 回滚 → 兜底重查应命中 winner（duplicate）。
"""
from __future__ import annotations


class _BlindResult:
    def scalar_one_or_none(self):  # noqa: ANN201
        return None

    def scalars(self):  # noqa: ANN201
        raise AssertionError("blind pre-check must not be iterated")

    def all(self):  # noqa: ANN201
        raise AssertionError("blind pre-check must not be iterated")


class BlindSession:
    """薄代理：首次 execute 失明，其余全透传（含上下文管理协议）。"""

    def __init__(self, engine) -> None:
        self._real = engine.connect()
        self._sess = None
        self._calls = 0

    def _ensure(self):
        if self._sess is None:
            from sqlalchemy.orm import Session

            self._sess = Session(bind=self._real)
        return self._sess

    def execute(self, stmt, *args, **kwargs):  # noqa: ANN001, ANN202
        self._calls += 1
        if self._calls == 1:
            return _BlindResult()
        return self._ensure().execute(stmt, *args, **kwargs)

    def __getattr__(self, name):  # noqa: ANN001
        return getattr(self._ensure(), name)

    def __enter__(self):  # noqa: ANN204
        self._ensure()
        self._sess.__enter__()
        return self

    def __exit__(self, *exc):  # noqa: ANN002
        return self._sess.__exit__(*exc)


def blind_factory(sessionmaker_like):
    """从测试 sessionmaker 提取 engine，产出"首查失明"的会话工厂。"""
    engine = sessionmaker_like.kw["bind"]

    def _factory():
        return BlindSession(engine)

    return _factory
