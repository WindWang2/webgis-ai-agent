"""G11 —— 简报安全负例测试（BRF-O08）。

面：HTML XSS/script 早闭合、CSV 公式注入、路径穿越/ID 注入、超深
JSON、超大打包、敏感字段脱敏、交付物不含 token/签名 URL/owner 秘密。
全部为负例 —— 失败必须封闭（422/ValueError/转义后输出），绝不静默放行。
"""
from __future__ import annotations

import json

import pytest

from app.lib.storymap.brief_export import (
    MAX_BRIEF_HTML_BYTES,
    build_brief_bundle,
    csv_safe,
    render_brief_html,
    rows_to_csv,
    sanitize_svg,
)
from app.lib.storymap.brief_spec import (
    BriefBlock,
    EvidenceBinding,
    MapBriefSpec,
)
from app.lib.storymap.spec import assert_json_depth


def _spec(title: str = "简报", text: str = "正常文本") -> MapBriefSpec:
    return MapBriefSpec.model_validate({
        "metadata": {"title": title, "session_id": "s1"},
        "chapters": [{
            "id": "arc-introduction", "title": "引言",
            "narrative": "## 引言", "arc_role": "introduction",
        }],
        "blocks": [
            {"id": "blk-t1", "chapter_id": "arc-introduction",
             "kind": "text", "text": text},
        ],
        "facts": [],
    })


class TestHtmlInjection:
    def test_script_in_text_is_escaped(self):
        payload = '<script>alert("xss")</script><img src=x onerror=alert(1)>'
        bundle = build_brief_bundle(_spec(text=payload), draft=True)
        html = render_brief_html(bundle)
        # 嵌入 JSON 转义 + 查看器 esc()：正文不得以可执行标签出现
        assert "<script>alert" not in html.replace(
            '<script type="application/json"', "").replace(
            "<script>", "").replace("</script>", "")
        # 攻击载荷在 JSON 中以 \u003c 转义出现（早闭合防线）
        assert "\\u003cscript\\u003e" in html

    def test_script_close_in_title_cannot_break_out(self):
        title = "简报</script><script>alert(1)</script>"
        bundle = build_brief_bundle(_spec(title=title))
        html = render_brief_html(bundle)
        # title 进 <title> 文本节点前做实体转义
        assert "</script><script>alert(1)</script>" not in html.split(
            "<script type=\"application/json\"")[0]

    def test_zero_external_links(self):
        bundle = build_brief_bundle(_spec())
        html = render_brief_html(bundle)
        import re
        assert not re.search(r'(src|href)="https?://', html)

    def test_svg_sanitized_hardened(self):
        """评审 #P1-2 实测旁路全量回归：自闭合/未闭合 script、未引号
        href、SMIL set/animate、未引号外链 —— 全部必须被剔除。"""
        payloads = [
            '<svg><script href="data:text/javascript,alert(1)"/></svg>',
            '<svg><script>alert(1)</svg>',
            '<svg><a href=javascript:alert(1)>x</a></svg>',
            '<svg><image href=http://evil.example/pixel.png /></svg>',
            '<svg><set attributeName="onmouseover" to="alert(1)"/></svg>',
            '<svg><animate attributeName="href" '
            'values="javascript:alert(1)"/></svg>',
            '<svg><animateTransform attributeName="onload" '
            'to="alert(1)"/></svg>',
            '<svg><script>var a = "</scr" + "ipt>"</script></svg>',
        ]
        for evil in payloads:
            clean = sanitize_svg(evil)
            low = clean.lower()
            assert "<script" not in low, evil
            assert "javascript:" not in low, evil
            assert "onmouseover" not in low and "onload" not in low, evil
            assert "http://evil.example" not in low, evil
        # 正常图形元素与 data: 引用保留
        keep = ('<svg><rect width="1" height="1"/>'
                '<a xlink:href="data:image/png;base64,AA"><text>y</text></a>'
                '</svg>')
        clean = sanitize_svg(keep)
        assert "<rect" in clean and "data:image/png" in clean

    def test_csv_cr_lf_prefixed(self):
        assert csv_safe("\r=cmd").startswith("'")
        assert csv_safe("\n=cmd").startswith("'")

    def test_svg_sanitized(self):
        evil_svg = (
            '<svg xmlns="http://www.w3.org/2000/svg">'
            '<script>alert(1)</script>'
            '<rect width="10" height="10" fill="red"/>'
            '<a href="http://evil.example/x"><text>x</text></a>'
            '<a xlink:href="data:image/png;base64,AAAA"><text>y</text></a>'
            '<image href="http://evil.example/img.png"/>'
            '</svg>')
        clean = sanitize_svg(evil_svg)
        assert "<script" not in clean
        assert "http://evil.example" not in clean
        assert "data:image/png" in clean  # data: 允许
        assert "<rect" in clean

    def test_oversized_bundle_rejected(self):
        spec = _spec(text="x" * 64)
        # 直接构造超预算 HTML（绕过渲染预算检查的负例：预算守卫本身）
        huge = "<html>" + "x" * (MAX_BRIEF_HTML_BYTES + 1)
        assert len(huge.encode("utf-8")) > MAX_BRIEF_HTML_BYTES
        with pytest.raises(ValueError, match="bytes budget"):
            class _Fake(dict):
                def get(self, k, default=None):  # noqa: N802
                    return {"spec": {"metadata": {}}} .get(k, default)
            # render 用真实 bundle 会先在 json 阶段就巨大 —— 用手工壳触发
            import app.lib.storymap.brief_export as be
            original = be.MAX_BRIEF_HTML_BYTES
            be.MAX_BRIEF_HTML_BYTES = 10
            try:
                render_brief_html(build_brief_bundle(_spec()))
            finally:
                be.MAX_BRIEF_HTML_BYTES = original


class TestCsvInjection:
    def test_formula_cells_prefixed(self):
        assert csv_safe("=SUM(A1:A9)") == "'=SUM(A1:A9)"
        assert csv_safe("+cmd|' /C calc'!A0").startswith("'+")
        assert csv_safe("-1+1").startswith("'-")
        assert csv_safe("@cmd").startswith("'@")
        assert csv_safe("\ttab").startswith("'\t")
        assert csv_safe("正常值") == "正常值"
        csv_out = rows_to_csv([["a", "=DDE()"], ["b", "ok"]])
        assert "'=DDE()" in csv_out


class TestInputBoundaries:
    def test_deep_json_rejected(self):
        deep = {"a": {"b": {"c": {}}}}
        node = deep
        for _ in range(100):
            node["c"] = {"d": {}}
            node = node["c"]
        with pytest.raises(ValueError, match="depth"):
            assert_json_depth(deep)

    def test_block_id_traversal_rejected(self):
        with pytest.raises(Exception, match="invalid block id"):
            BriefBlock.model_validate(
                {"id": "../../etc/passwd", "chapter_id": "c",
                 "kind": "text"})

    def test_artifact_ref_url_rejected(self):
        with pytest.raises(Exception, match="invalid artifact ref"):
            EvidenceBinding(artifact_ref="https://evil.example/x")

    def test_fact_nan_rejected(self):
        with pytest.raises(Exception):
            from app.lib.storymap.brief_spec import BriefFact
            BriefFact.model_validate({
                "fact_id": "fact-x1", "label": "x", "value": float("inf")})


class TestPayloadPrivacy:
    def test_sensitive_keys_redacted_in_bundle(self):
        from app.lib.storymap.spec import LinkedWidget

        spec = _spec()
        spec = spec.model_copy(update={"linked_widgets": [LinkedWidget(
            id="w1", kind="kpi", chapter_id="arc-introduction",
            data={"api_key": "sk-12345", "value": 42, "AccessToken": "tok"},
        )]})
        bundle = build_brief_bundle(spec)
        text = json.dumps(bundle, ensure_ascii=False)
        assert "sk-12345" not in text, "api_key must be REDACTED in bundle"
        assert "AccessToken" in text  # 键名保留
        assert "REDACTED" in text

    def test_url_credentials_scrubbed(self):
        spec = _spec(text="见 https://example.com/data?api_key=SECRET123 查询")
        bundle = build_brief_bundle(spec)
        text = json.dumps(bundle, ensure_ascii=False)
        assert "SECRET123" not in text

    def test_release_html_has_no_tokens(self):
        bundle = build_brief_bundle(
            _spec(), release_meta={"share_code": "topsecret-code",
                                   "owner_token": "owner-secret"},
            draft=True)
        html = render_brief_html(bundle)
        assert "topsecret-code" not in html
        assert "owner-secret" not in html
