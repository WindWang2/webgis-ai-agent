"""离线打包（ADR-0196 §4）——脱敏 + 自包含单文件 JSON/HTML，纯函数无 IO。

StoryBundle 自解压单文件：HTML 形态把 bundle 内嵌为
``<script type="application/json" id="story-bundle">``（``</`` 与 ``<!--``
转义防早闭合/注释逃逸），配零依赖 vanilla 查看器，无任何外链，可离线打开。
脱敏是默认项：键名黑名单递归 REDACT（与运行时 ``bound_meta`` 同语义）。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Sequence

from app.lib.storymap.spec import StoryMapSpec

BUNDLE_SCHEMA_VERSION = "storybundle-1"

# 键名脱敏黑名单（小写化后精确匹配；值一律替换 REDACTED）。
SENSITIVE_KEYS = frozenset({
    "token", "secret", "password", "api_key", "apikey", "authorization",
    "credential", "cookie", "cookies", "set-cookie", "owner_token",
    "session_token", "x-session-token", "refresh_token", "access_token",
    "bearer", "private_key",
})

_REDACTED = "REDACTED"


def sanitize_dict(value: Any) -> Any:
    """递归脱敏：命中黑名单键的值替换 REDACTED，其余结构原样拷贝。"""
    if isinstance(value, Mapping):
        out: Dict[str, Any] = {}
        for k, v in value.items():
            if isinstance(k, str) and k.lower() in SENSITIVE_KEYS:
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
    data = {
        "layers": layer_list,
        "mapspec": dict(mapspec) if mapspec else None,
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
    """JSON 内嵌 script 安全化：``</`` 转义防 script 早闭合，``<!--`` 防注释逃逸。"""
    text = json.dumps(payload, ensure_ascii=False)
    return text.replace("</", "<\\/").replace("<!--", "<\\!--")


_HTML_SHELL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · 离线专报</title>
<style>
:root {{ color-scheme: dark; }}
body {{ margin: 0; background: #0b0f14; color: #d7dde6;
       font: 15px/1.7 "Segoe UI", "Microsoft YaHei", sans-serif; }}
main {{ max-width: 860px; margin: 0 auto; padding: 48px 24px 96px; }}
h1 {{ color: #4ea1ff; letter-spacing: .12em; }}
h2 {{ color: #7fc4ff; border-bottom: 1px solid #223041; padding-bottom: 6px;
     margin-top: 48px; }}
.meta {{ color: #7d8b9d; font-size: 13px; }}
.summary {{ background: #101822; border: 1px solid #223041; border-radius: 8px;
            padding: 12px 16px; }}
.cam {{ font-family: Consolas, monospace; font-size: 12px; color: #8fa3b8; }}
.narrative {{ white-space: pre-wrap; }}
</style>
</head>
<body>
<main id="story-root"><p class="meta">正在解压专报…</p></main>
<script type="application/json" id="story-bundle">{json}</script>
<script>
(function () {{
  var root = document.getElementById('story-root');
  try {{
    var bundle = JSON.parse(document.getElementById('story-bundle').textContent);
    var spec = bundle.spec || {{}};
    var meta = spec.metadata || {{}};
    var html = '<h1>' + (meta.title || 'StoryMap') + '</h1>';
    html += '<p class="meta">生成于 ' + (bundle.generated_at || '') +
            ' · 章节 ' + (bundle.manifest ? bundle.manifest.chapter_count : '?') + '</p>';
    if (meta.summary) html += '<div class="summary">' + meta.summary + '</div>';
    var kfs = {{}};
    (spec.camera_keyframes || []).forEach(function (k) {{ kfs[k.chapter_id] = k; }});
    (spec.chapters || []).forEach(function (ch) {{
      html += '<section><h2>' + ch.title + '</h2>';
      var cam = kfs[ch.id];
      if (cam) {{
        html += '<p class="cam">镜头 center=[' + cam.center.join(', ') +
                '] zoom=' + cam.zoom + ' pitch=' + cam.pitch +
                '° bearing=' + cam.bearing + '°</p>';
      }}
      html += '<div class="narrative">' + ch.narrative + '</div></section>';
    }});
    root.innerHTML = html;
  }} catch (err) {{
    root.innerHTML = '<p class="meta">专报数据损坏：' + err + '</p>';
  }}
}})();
</script>
</body>
</html>
"""


def render_standalone_html(bundle: Mapping[str, Any]) -> str:
    """bundle → 自解压单文件 HTML（零外链，双击即看）。"""
    spec = bundle.get("spec") or {}
    metadata = spec.get("metadata") or {}
    title = str(metadata.get("title") or "StoryMap").replace("<", "&lt;")
    return _HTML_SHELL.format(title=title, json=_embed_json(bundle))
