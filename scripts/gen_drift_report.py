#!/usr/bin/env python
"""从各 registry 生成 Contract Drift Report（docs/quality/）。

report 是派生物：唯一事实源是各 registry 与 frontend 源码本身。

用法：
    python scripts/gen_drift_report.py            # 写入 MD + JSON
    python scripts/gen_drift_report.py --check    # 仅校验（过期则退出 1）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO = Path(__file__).resolve().parents[1]
DEFAULT_MD = REPO / "docs/quality/CONTRACT_DRIFT_REPORT.md"
DEFAULT_JSON = REPO / "docs/quality/contract-drift-report.json"


def generate() -> tuple[str, str]:
    from app.lib.quality.drift import compile_drift_report, render_json, render_markdown

    report = compile_drift_report()
    return render_markdown(report), render_json(report)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只校验，不写入")
    args = parser.parse_args()

    md, js = generate()
    if args.check:
        stale = []
        for path, content in ((DEFAULT_MD, md), (DEFAULT_JSON, js)):
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                stale.append(str(path))
        if stale:
            print("stale: " + ", ".join(stale))
            print("run: python scripts/gen_drift_report.py")
            return 1
        print("drift report up to date")
        return 0

    DEFAULT_MD.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_MD.write_text(md, encoding="utf-8")
    DEFAULT_JSON.write_text(js, encoding="utf-8")
    print(f"wrote {DEFAULT_MD} and {DEFAULT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
