"""app.lib.redaction 中立模块契约（ADR-0214 D1，WP8）。

fuzz / negative 纪律：种子化伪随机载荷（确定性、无 hypothesis 依赖）+
对抗秘密形态；钉死的不变量：

- never-raises：任意输入类型/形状不崩；
- idempotent：scrub(scrub(x)) == scrub(x)；
- 秘密注入：随机注入的秘密值经消毒后不出现在输出（键级 + 值级双防线）；
- 兼容面：replay.sanitize re-export 语义与 redaction 逐字一致；runtime
  消费方（decision_record / gis_trace）不再 import replay 包。
"""
from __future__ import annotations

import random
import string

import pytest

from app.lib.redaction import (
    SECRET_KEY_MARKERS,
    SECRET_STRING_PATTERNS,
    is_secret_key,
    scrub_secret_strings,
)

pytestmark = pytest.mark.cartography

_REDACTED = "[REDACTED]"

#: 高置信秘密形态（与 redaction 词表同源的对抗样本）。
_SECRETS = [
    "sk-proj-abcdef1234567890",
    "AKIAIOSFODNN7EXAMPLE",
    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
    "api_key=9f8e7d6c5b4a",
    "password: hunter22",
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEpA",
]

_PLAIN = [
    "把 A 区人均公园面积做成分布图",
    "token 出现在词典里也是普通词",
    "layer_id=parks, style=choropleth",
    "",
]


# ── 基础契约 ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("secret", _SECRETS)
def test_value_level_patterns_redact_secret(secret):
    out = scrub_secret_strings(f"prefix {secret} suffix")
    assert _REDACTED in out
    assert secret not in out


@pytest.mark.parametrize("text", _PLAIN)
def test_plain_text_passes_through(text):
    assert scrub_secret_strings(text) == text


def test_key_level_markers():
    for marker in SECRET_KEY_MARKERS:
        assert is_secret_key(f"x_{marker}_y")
    # 表单归一：连字符/空格/大小写。
    assert is_secret_key("X-Api-Key")
    assert is_secret_key("Private Key")
    assert not is_secret_key("layer_id")
    assert not is_secret_key("monotonic")
    # 「token 出现在散文里」不是键 —— is_secret_key 只判键名。
    assert not is_secret_key("sentence")


def test_idempotent():
    for text in _SECRETS + _PLAIN:
        once = scrub_secret_strings(text)
        assert scrub_secret_strings(once) == once


def test_never_raises_on_arbitrary_inputs():
    weird = [None, 123, 4.5, b"bytes", {"k": "v"}, ["l"], object(), True]
    for item in weird:
        # 非 str 输入按 str() 走，绝不抛。
        assert isinstance(scrub_secret_strings(item), str)  # type: ignore[arg-type]


# ── fuzz：种子化伪随机载荷 + 秘密注入 ────────────────────────────────────────

_ALPHABET = string.printable[:94]  # 可打印 ASCII，避开换行噪声


def _random_payload(rng: random.Random, length: int) -> str:
    return "".join(rng.choice(_ALPHABET) for _ in range(length))


def test_fuzz_idempotent_and_never_raises():
    rng = random.Random(20260926)
    for _ in range(300):
        text = _random_payload(rng, rng.randint(0, 400))
        out = scrub_secret_strings(text)
        assert scrub_secret_strings(out) == out


def test_fuzz_injected_secrets_never_survive():
    """随机载体中注入秘密：消毒后秘密本体不得出现（允许载体其余部分保留）。"""
    rng = random.Random(20260926)
    for _ in range(300):
        carrier = _random_payload(rng, rng.randint(0, 200))
        secret = rng.choice(_SECRETS)
        text = carrier + "::" + secret + "::" + carrier
        out = scrub_secret_strings(text)
        assert secret not in out


def test_fuzz_pattern_substitution_is_total():
    """任一 pattern 匹配的输入，净化后必然不再被同 pattern 命中（替换彻底）。"""
    rng = random.Random(42)
    for _ in range(200):
        text = _random_payload(rng, rng.randint(0, 120))
        for pattern in SECRET_STRING_PATTERNS:
            if pattern.search(text):
                out = scrub_secret_strings(text)
                assert not pattern.search(out)


# ── 下沉兼容面（ADR-0212 §3 P2 follow-up 清偿）───────────────────────────────

def test_replay_sanitize_reexports_neutral_module():
    from app.lib.harness.replay import sanitize as replay_sanitize

    assert replay_sanitize.scrub_secret_strings is scrub_secret_strings
    assert replay_sanitize.SECRET_KEY_MARKERS == SECRET_KEY_MARKERS
    assert replay_sanitize.SECRET_STRING_PATTERNS == SECRET_STRING_PATTERNS
    # 内部消费点（schema 的决策域感知消毒）经由 re-export 生效。
    from app.lib.harness.replay.sanitize import _is_secret_key

    assert _is_secret_key("api_key") is True


def test_runtime_no_longer_imports_replay_for_redaction():
    """生产 runtime 的净化 import 必须指向中立模块（方向性倒挂清偿）。"""
    import inspect

    from app.lib.runtime import decision_record, gis_trace

    for module in (decision_record, gis_trace):
        src = inspect.getsource(module)
        assert "from app.lib.harness.replay.sanitize import" not in src
        assert "from app.lib.redaction import scrub_secret_strings" in src


def test_decision_record_scrub_uses_neutral_module(monkeypatch):
    """runtime 决策面的值级 scrub 行为经中立模块生效（语义不变）。"""
    from app.lib.runtime import decision_record as dr

    record = dr.decision_record(
        dr.DECISION_KIND_PLAN_SELECTION,
        selected="recipe:choropleth",
        inputs={"note": "key=sk-abcdef1234567890"},
    )
    assert "sk-abcdef1234567890" not in str(record["inputs"])
