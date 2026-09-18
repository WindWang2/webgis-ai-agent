"""离线打包（ADR-0196 §4）——脱敏 + 自包含单文件 JSON/HTML，纯函数无 IO。

StoryBundle 自解压单文件：HTML 形态把 bundle 内嵌为
``<script type="application/json" id="story-bundle">``，内嵌文本把 ``<``/``>``
统一转义为合法 JSON 转义 ``\u003c``/``\u003e``（可无损还原，同时天然阻断
``</script`` 早闭合与 ``<!--`` 注释逃逸；不用 ``\\<`` 之类非法 JSON 转义）。
查看器对一切数据面文本走 ``esc()`` HTML 实体转义后才拼入 DOM —— 会话正文是
用户可控内容，离线专报必须保证打开即安全。脱敏是默认项：键名子串提示递归
REDACT（对齐运行时 ``bound_meta`` 的敏感键语义）。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Sequence

from app.lib.storymap.spec import (
    StoryMapSpec,
    assert_json_depth,
    strip_surrogates,
)

BUNDLE_SCHEMA_VERSION = "storybundle-1"

# 键名脱敏按**词元边界**匹配：先按 `-`/`_`/`.`/空白与 camelCase 边界
# 分段，再匹配敏感词元。子串匹配会把 capital/rapid/therapist 误杀在
# "api" 上、把 author 误杀在 "auth" 上；词元匹配仍覆盖 access_key、
# signing_key、encryption_key、AccessKeyId 等真实敏感键。
_KEY_TOKEN_SPLIT_RE = re.compile(
    r"[-_.\s]+|(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])"
)

# 单段词元命中即 REDACT。"key" 作为完整词元覆盖 *_key/*Key 命名，不会
# 命中 monkey/keyboard 这类普通词。
SENSITIVE_KEY_TOKENS = frozenset({
    "api", "apikey", "auth", "authkey", "authorization", "bearer",
    "cookie", "credential", "encryptionkey", "key", "ownerid",
    "password", "passwd", "private", "privatekey", "pwd", "secret",
    "secretkey", "sessionid", "signingkey", "token",
})

# 折叠后仍常见的复合敏感名（访问密钥类命名常写成无分隔符小写）。
SENSITIVE_FOLDED_SUBSTRINGS = (
    "accesskey", "accesstoken", "apikey", "authkey", "authorization",
    "credential", "encryptionkey", "password", "passwd", "privatekey",
    "secretkey", "sessionid", "signingkey",
)

_REDACTED = "REDACTED"


def _fold_key(key: str) -> str:
    return key.lower().replace("-", "").replace("_", "").replace(".", "")


def _key_tokens(key: str) -> set[str]:
    return {part.lower() for part in _KEY_TOKEN_SPLIT_RE.split(key) if part}


def _is_sensitive_key(key: str) -> bool:
    tokens = _key_tokens(key)
    if tokens & SENSITIVE_KEY_TOKENS:
        return True
    folded = _fold_key(key)
    return any(hint in folded for hint in SENSITIVE_FOLDED_SUBSTRINGS)


def sanitize_dict(value: Any) -> Any:
    """递归脱敏：命中敏感键提示的值替换 REDACTED，其余结构原样拷贝。

    `client_secret` / `x-api-key` / `AccessToken` 等变体因去分隔符折叠 +
    子串匹配而全部命中（旧实现只做小写精确匹配，存在明文通道）。
    """
    if isinstance(value, Mapping):
        out: Dict[str, Any] = {}
        for k, v in value.items():
            if isinstance(k, str) and _is_sensitive_key(k):
                out[k] = _REDACTED
            else:
                out[k] = sanitize_dict(v)
        return out
    if isinstance(value, (list, tuple)):
        return [sanitize_dict(v) for v in value]
    return value


def build_story_bundle(
    spec: StoryMapSpec,
    *,
    layers: Optional[Sequence[Mapping[str, Any]]] = None,
    mapspec: Optional[Mapping[str, Any]] = None,
    sanitize: bool = True,
) -> Dict[str, Any]:
    """spec + 图层 + MapSpec 样式 → 自包含 bundle dict。"""
    layer_list = [dict(fc) for fc in (layers or []) if isinstance(fc, Mapping)]
    spec_dict = spec.model_dump()
    # Boundary guards before recursive sanitize/embed: a hostile deep spec or
    # layer payload must fail as contract input, and lone surrogate code units
    # must not reach JSON.stringify/UTF-8 response serialization.
    assert_json_depth(spec_dict)
    assert_json_depth(layer_list)
    if mapspec is not None:
        assert_json_depth(mapspec)
    spec_dict = strip_surrogates(spec_dict)  # type: ignore[assignment]
    data = {
        "layers": strip_surrogates(layer_list),
        "mapspec": strip_surrogates(dict(mapspec)) if mapspec else None,
    }
    bundle: Dict[str, Any] = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "spec": spec_dict,
        "data": data,
        "manifest": {
            "chapter_count": len(spec_dict.get("chapters", [])),
            "widget_count": len(spec_dict.get("linked_widgets", [])),
            "layer_count": len(layer_list),
            "feature_count": sum(
                len(fc.get("features") or [])
                for fc in layer_list if isinstance(fc.get("features"), list)
            ),
        },
    }
    if sanitize:
        return sanitize_dict(bundle)
    return bundle


def bundle_to_json(bundle: Mapping[str, Any]) -> str:
    """单文件 JSON 形态（UTF-8 原文，不转义非 ASCII）。"""
    return json.dumps(bundle, ensure_ascii=False)


def _embed_json(payload: Mapping[str, Any]) -> str:
    """JSON 内嵌 script 安全化。

    ``<``/``>` → ``\\u003c``/``\\u003e``（合法 JSON 转义；严格解析无损还原）：
    同时覆盖 ``</script`` 早闭合、``<!--`` 注释逃逸与任何标签形态；
    ``\\u2028/\\u2029`` 一并转义（对 JSON.parse 无害，防未来改内联 JS 字面量
    时踩 ASI 坑）。
    """
    text = json.dumps(payload, ensure_ascii=False)
    return (text.replace("<", "\\u003c")
                .replace(">", "\\u003e")
                .replace("\u2028", "\\u2028")
                .replace("\u2029", "\\u2029"))


_VIEWER_JS = """
(function () {
  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  var root = document.getElementById('story-root');
  try {
    var bundle = JSON.parse(document.getElementById('story-bundle').textContent);
    var spec = bundle.spec || {};
    var meta = spec.metadata || {};
    var html = '<h1>' + esc(meta.title || 'StoryMap') + '</h1>';
    html += '<p class="meta">生成于 ' + esc(bundle.generated_at || '') +
            ' · 章节 ' + esc(bundle.manifest ? bundle.manifest.chapter_count : '?') + '</p>';
    if (meta.summary) html += '<div class="summary">' + esc(meta.summary) + '</div>';
    var kfs = {};
    (spec.camera_keyframes || []).forEach(function (k) { kfs[k.chapter_id] = k; });
    (spec.chapters || []).forEach(function (ch) {
      html += '<section><h2>' + esc(ch.title) + '</h2>';
      var cam = kfs[ch.id];
      if (cam) {
        html += '<p class="cam">镜头 center=[' + esc(cam.center.join(', ')) +
                '] zoom=' + esc(cam.zoom) + ' pitch=' + esc(cam.pitch) +
                '° bearing=' + esc(cam.bearing) + '°</p>';
      }
      html += '<div class="narrative">' + esc(ch.narrative) + '</div></section>';
    });
    root.innerHTML = html;
  } catch (err) {
    root.textContent = '专报数据损坏：' + err;
  }
})();
"""

_HTML_SHELL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ · 离线专报</title>
<style>
:root { color-scheme: dark; }
body { margin: 0; background: #0b0f14; color: #d7dde6;
       font: 15px/1.7 "Segoe UI", "Microsoft YaHei", sans-serif; }
main { max-width: 860px; margin: 0 auto; padding: 48px 24px 96px; }
h1 { color: #4ea1ff; letter-spacing: .12em; }
h2 { color: #7fc4ff; border-bottom: 1px solid #223041; padding-bottom: 6px;
     margin-top: 48px; }
.meta { color: #7d8b9d; font-size: 13px; }
.summary { background: #101822; border: 1px solid #223041; border-radius: 8px;
           padding: 12px 16px; }
.cam { font-family: Consolas, monospace; font-size: 12px; color: #8fa3b8; }
.narrative { white-space: pre-wrap; }
</style>
</head>
<body>
<main id="story-root"><p class="meta">正在解压专报…</p></main>
<script type="application/json" id="story-bundle">__JSON__</script>
<script>__VIEWER__</script>
</body>
</html>
"""


def render_standalone_html(bundle: Mapping[str, Any]) -> str:
    """bundle → 自解压单文件 HTML（零外链，双击即看）。

    title 只做最小转义（``&``/``<``/``>`` 换实体）就足够 —— 它进的是
    ``<title>`` 文本节点；查看器内的一切数据面渲染由 ``esc()`` 负责。
    """
    spec = bundle.get("spec") or {}
    metadata = spec.get("metadata") or {}
    title = str(metadata.get("title") or "StoryMap")
    title_escaped = (title.replace("&", "&amp;").replace("<", "&lt;")
                     .replace(">", "&gt;"))
    # Single-pass template fill: replacements are inserted into the output
    # without re-scanning their content. Sequential str.replace let a title
    # containing "__VIEWER__"/"__JSON__" consume a later placeholder and inject
    # script/JSON into the <title> text node.
    replacements = {
        "TITLE": title_escaped,
        "VIEWER": _VIEWER_JS,
        "JSON": _embed_json(bundle),
    }
    return re.sub(
        r"__(TITLE|VIEWER|JSON)__",
        lambda match: replacements[match.group(1)],
        _HTML_SHELL,
    )
