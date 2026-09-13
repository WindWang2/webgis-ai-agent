"""defaults.py 单点断言（V11 W0.4，ADR-0160，缺口 G8）。

三层断言：

1. 常量值本身（既有对外行为缺省值，改动须走 ADR）；
2. **grep 断言**：业务代码文件清单内禁止再现兜底字面量
   （``"quantiles"`` / ``k = 5`` 形态 / ``"YlOrRd"``）—— 新增违规即红，
   收敛范围随 W3（阈值策略）扩展；
3. 消费方签名真的绑定 defaults（防「import 了但签名仍写字面量」）。

圈定边界（与 defaults.py docstring 一致）：palettes/themes 等注册表是
权威定义本身，不在本断言清单内；selfheal_actions 的 quantiles 命中是
docstring 里的线间约定示例，亦不在清单内。
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from app.lib.cartography.classify import classify_values
from app.lib.cartography.defaults import (

    DEFAULT_CATEGORICAL_PALETTE,
    DEFAULT_CLASSIFICATION_METHOD,
    DEFAULT_CLASS_COUNT,
    DEFAULT_PALETTE,
)

pytestmark = pytest.mark.cartography

REPO_ROOT = Path(__file__).resolve().parents[2]

#: 业务代码清单：文件 → 禁止再现的**缺省位形态**（源码文本级精确断言）。
#: 只禁「兜底缺省」形态（签名缺省 / ``or`` 兜底 / ``get(key, default)`` /
#: 裸 ``k = 5``）；**注册表词汇与分发比较豁免**（如 ``method == "quantiles"``
#: 是方法名分发，model_library 的 CLASSIFICATION_METHODS 清单、palette
#: 定义、template_schema 的种子 payload 都是权威内容本身，不是兜底）。
FORBIDDEN: dict[str, tuple[str, ...]] = {
    "app/lib/cartography/classify.py": (
        'method: str = "quantiles"', "k: int = 5", "k = 5",
    ),
    "app/lib/cartography/thematic_spec.py": (
        '"YlOrRd"', "k: int = 5", 'or "quantiles"', "k or 5",
    ),
    "app/lib/cartography/bivariate.py": ('method: str = "quantiles"',),
    "app/lib/cartography/model_library.py": ("default_k: int = 5",),
    "app/services/cartography_service.py": (
        '"YlOrRd"', 'method: str = "quantiles"', "k: int = 5", "k = 5",
    ),
    "app/tools/templates.py": (
        '"YlOrRd"', 'get("method", "quantiles")',
    ),
    "app/services/mapspec/composite_builder.py": (
        '"YlOrRd"', 'get("method", "quantiles")',
    ),
    "app/schemas/template_schema.py": (
        'palette: str = "YlOrRd"', 'method: str = "quantiles"', "k: int = 5",
    ),
}


def test_default_constants_frozen_values() -> None:
    """既有对外缺省值锁定：变更须走 ADR 并同步 golden。"""
    assert DEFAULT_CLASS_COUNT == 5
    assert DEFAULT_CLASSIFICATION_METHOD == "quantiles"
    assert DEFAULT_PALETTE == "YlOrRd"
    assert DEFAULT_CATEGORICAL_PALETTE == "Set2"


@pytest.mark.parametrize("rel, patterns", sorted(FORBIDDEN.items()))
def test_no_hardcoded_fallback_literals(rel: str, patterns: tuple[str, ...]) -> None:
    """grep 断言：清单文件内兜底字面量归零（G8）。"""
    path = REPO_ROOT / rel
    assert path.exists(), f"清单文件不存在（移动后请更新清单）: {rel}"
    src = path.read_text(encoding="utf-8")
    for pat in patterns:
        hits = [
            lineno for lineno, line in enumerate(src.splitlines(), start=1)
            if pat in line
        ]
        assert not hits, (
            f"{rel}: 兜底字面量 {pat!r} 回潮于行 {hits}；"
            "请改用 app/lib/cartography/defaults.py 的单点常量"
        )


def test_consumers_bind_defaults_in_signatures() -> None:
    """消费方签名绑定 defaults（身份断言，非值断言）。"""
    sig = inspect.signature(classify_values)
    assert sig.parameters["method"].default is DEFAULT_CLASSIFICATION_METHOD
    assert sig.parameters["k"].default is DEFAULT_CLASS_COUNT
