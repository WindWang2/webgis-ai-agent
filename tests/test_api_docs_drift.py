"""V9 契约基石：api-docs.md 生成区 drift 闸（ADR-0138 / P6）。

生成物（scripts/gen_api_docs.py 从 app.openapi() 产出）与提交物逐字比对：
不一致即 fail —— 「限流 60 vs 240」「缺章节」类手写漂移在 CI 必死。

修复方式：本地运行 `python scripts/gen_api_docs.py` 后随 PR 提交。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from gen_api_docs import BEGIN, END, build_catalog  # noqa: E402

DOC = REPO / "docs" / "api-docs.md"


def test_api_docs_generated_section_matches_openapi():
    text = DOC.read_text(encoding="utf-8")
    assert BEGIN in text and END in text, (
        "docs/api-docs.md 缺少生成区 marker —— 运行 python scripts/gen_api_docs.py"
    )
    m = re.search(re.escape(BEGIN) + r".*?" + re.escape(END), text, re.S)
    committed = m.group(0).strip()
    generated = build_catalog().strip()
    assert committed == generated, (
        "docs/api-docs.md 生成区与当前 OpenAPI 不一致（手写漂移）——\n"
        "运行 python scripts/gen_api_docs.py 刷新并随 PR 提交"
    )


def test_api_docs_rate_limit_matches_code():
    """限流文档数值与 main.py 实际值一致（#1217 同类文档漂移的样本闸）。"""
    text = DOC.read_text(encoding="utf-8")
    assert "240 次 / 60 秒" in text, (
        "api-docs.md 全局限流数值与代码 (max_requests=240, window_seconds=60) 不一致"
    )
    assert not re.search(r"每客户端 IP 60 次", text), (
        "api-docs.md 残留旧的 60/min 限流描述"
    )


def test_api_docs_covers_v8_subsystems():
    """V7/V8 子系统（lakehouse / geocompute / workflow-runtime）章节必须在场。"""
    text = DOC.read_text(encoding="utf-8").lower()
    for subsystem in ("lakehouse", "geocompute", "workflow"):
        assert subsystem in text, f"api-docs.md 缺 {subsystem} 相关章节"
