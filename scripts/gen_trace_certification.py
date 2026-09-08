#!/usr/bin/env python
"""生成 docs/quality/certifications/TRACE_COMPLETENESS.md（ADR-0104 Wave 5）。

认证表是派生物：契约（app/lib/quality/trace_contract.py）+ 运行时填充现状
（静态发现 app/ 内真实 record 站点）。

用法：
    python scripts/gen_trace_certification.py            # 写入
    python scripts/gen_trace_certification.py --check    # 过期则退出 1
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_OUT = Path("docs/quality/certifications/TRACE_COMPLETENESS.md")


def generate() -> str:
    from app.lib.quality.trace_contract import render_certification_md

    return render_certification_md()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只校验，不写入")
    args = parser.parse_args()

    content = generate()
    if args.check:
        if not DEFAULT_OUT.exists() or DEFAULT_OUT.read_text(encoding="utf-8") != content:
            print(f"stale: {DEFAULT_OUT}")
            print("run: python scripts/gen_trace_certification.py")
            return 1
        print("trace certification up to date")
        return 0

    DEFAULT_OUT.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_OUT.write_text(content, encoding="utf-8")
    print(f"wrote {DEFAULT_OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
