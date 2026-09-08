#!/usr/bin/env python
"""生成 docs/quality/certifications/SECURITY_CONTROLS.md（ADR-0104 Wave 15）。

认证表是派生物：契约（app/lib/quality/security_manifest.py 的控制→实现→测试
映射）+ 门禁校验现状。

用法：
    python scripts/gen_security_manifest.py            # 写入
    python scripts/gen_security_manifest.py --check    # 过期则退出 1
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_OUT = Path("docs/quality/certifications/SECURITY_CONTROLS.md")


def generate() -> str:
    from app.lib.quality.security_manifest import render_security_manifest_md

    return render_security_manifest_md()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只校验，不写入")
    args = parser.parse_args()

    content = generate()
    if args.check:
        if not DEFAULT_OUT.exists() or DEFAULT_OUT.read_text(encoding="utf-8") != content:
            print(f"stale: {DEFAULT_OUT}")
            print("run: python scripts/gen_security_manifest.py")
            return 1
        print("security controls certification up to date")
        return 0

    DEFAULT_OUT.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_OUT.write_text(content, encoding="utf-8")
    print(f"wrote {DEFAULT_OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
