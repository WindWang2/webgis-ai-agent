#!/usr/bin/env python3
"""api-docs.md 生成区生成器（ADR-0138 / P6）。

从 `app.openapi()` 生成 `docs/api-docs.md` 的「端点目录」生成区；
`tests/test_api_docs_drift.py` import 本模块的 BEGIN/END/build_catalog
与提交物逐字比对，不一致即 CI fail。

用法：python scripts/gen_api_docs.py   # 原地刷新 docs/api-docs.md 生成区
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs" / "api-docs.md"

BEGIN = "<!-- BEGIN GENERATED:API-CATALOG -->"
END = "<!-- END GENERATED:API-CATALOG -->"

# 与 app/main.py RateLimitMiddleware 及安全面的实际常数一致；
# 数值漂移由 tests/test_api_docs_drift.py 的样本闸兜底。
_CONVENTIONS = """\
### 通用约定（生成自代码常数）

- 全局前缀：`/api/v1`（V8 前特性统一挂 v1；v2 见 ADR-0138）。
- 全局限流：每客户端 IP **240 次 / 60 秒**（`app/main.py` `RateLimitMiddleware(max_requests=240, window_seconds=60)`，`/docs`、`/redoc`、`/openapi.json` 豁免），超限 429。
- 登录失败：每 IP 5 次 / 5 分钟；注册：每 IP 5 次 / 小时；refresh：每用户 30 次 / 5 分钟；WebSocket 连接：每 IP 5 次 / 60 秒。
- 错误信封：统一 `{code, success, message, data}`（含 `category`/`retryable` 分类附加字段）；过渡期回退见 ADR-0138（`LEGACY_DETAIL_ENVELOPE` / 请求头 `X-Error-Envelope: detail`）。"""

HTTP_METHODS = ("get", "put", "post", "delete", "patch")


def _load_spec():
    sys.path.insert(0, str(REPO))
    from app.main import app  # noqa: E402 - 脚本入口，延迟导入

    return app.openapi()


def _response_model(op: dict) -> str:
    """200 响应列：$ref → schema 名；无 200 → —；200 无 schema → object。"""
    responses = op.get("responses") or {}
    if "200" not in responses:
        return "—"
    content = (responses["200"].get("content") or {})
    schema = (content.get("application/json") or {}).get("schema")
    if not schema:
        return "object"
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    return schema.get("type") or "object"


def _summary(op: dict) -> str:
    summary = op.get("summary")
    if summary:
        return str(summary)
    # FastAPI 对无 summary 的操作回退 operationId（蛇形 → 标题式）
    operation_id = str(op.get("operationId") or "").rsplit(".", 1)[-1]
    return operation_id.replace("_", " ").strip().capitalize()


def build_catalog() -> str:
    """生成「BEGIN..END 区间内」的全部文本（含两侧 marker）。"""
    spec = _load_spec()

    sections: list[tuple[str, list[str]]] = []
    index: dict[str, list[str]] = {}
    total = 0
    for path, path_item in spec.get("paths", {}).items():
        for method, op in path_item.items():
            if method not in HTTP_METHODS or not isinstance(op, dict):
                continue
            total += 1
            tag = (op.get("tags") or ["未分类"])[0]
            row = (
                f"| `{method.upper()}` | `{path}` | {_summary(op)} "
                f"| {_response_model(op)} |"
            )
            if tag not in index:
                index[tag] = []
                sections.append((tag, index[tag]))
            index[tag].append(row)

    parts = [BEGIN, "", "## 端点目录（自动生成）", "",
             "> 本节由 `scripts/gen_api_docs.py` 从 `app.openapi()` 生成 ——",
             "> **禁止手改**；漂移由 `tests/test_api_docs_drift.py` 在 CI 强制。",
             "", _CONVENTIONS, ""]
    for tag, rows in sections:
        parts.append(f"### {tag}")
        parts.append("")
        parts.append("| 方法 | 路径 | 说明 | 响应模型 |")
        parts.append("|---|---|---|---|")
        parts.extend(rows)
        parts.append("")
    parts.append(
        f"_端点总数：{total}（OpenAPI operations，不含流式豁免面外资源）_"
    )
    parts.append("")
    parts.append(END)
    return "\n".join(parts)


def refresh() -> None:
    text = DOC.read_text(encoding="utf-8")
    pattern = re.escape(BEGIN) + r".*?" + re.escape(END)
    if not re.search(pattern, text, re.S):
        raise SystemExit("docs/api-docs.md 缺少生成区 marker，无法定位刷新区间")
    DOC.write_text(
        re.sub(pattern, lambda _: build_catalog(), text, count=1, flags=re.S),
        encoding="utf-8",
    )
    print(f"refreshed {DOC}")


if __name__ == "__main__":
    refresh()
