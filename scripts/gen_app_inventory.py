#!/usr/bin/env python
"""应用依赖 inventory（Platform V4，ADR-0131 D7；可选 SBOM-lite）。

枚举 ``requirements.txt`` 的直接依赖声明，导出
``docs/platform-v4/generated/app-inventory.json`` + ``--check`` 字节闸。
**字节闸只锁定声明名集合**（从 requirements.txt 确定性派生，跨机器一致）；
本机已安装版本仅打印到 stdout 供诊断——版本因环境而异，进闸必漂。

零新依赖、零网络。完整 CycloneDX SBOM 属打包工具链，此处刻意不做。

用法::

    python scripts/gen_app_inventory.py            # 再生 + 环境版本摘要
    python scripts/gen_app_inventory.py --check    # 字节闸（声明名集合）
"""
from __future__ import annotations

import json
import re
import sys
from importlib import metadata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = REPO_ROOT / "docs" / "platform-v4" / "generated" / "app-inventory.json"
_REQUIREMENTS = REPO_ROOT / "requirements.txt"

_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9._-]+)\s*(@|[{><=~!])?.*$")


def declared_direct_deps() -> list:
    """requirements.txt 的直接依赖名（跳过注释/空行/-r 引用；确定性排序）。"""
    names = []
    for line in _REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        m = _REQ_NAME.match(line)
        if m:
            names.append(m.group(1).lower())
    return sorted(set(names))


def generate_inventory() -> str:
    payload = {
        "generated_by": "scripts/gen_app_inventory.py",
        "source": "requirements.txt",
        "declared_packages": declared_direct_deps(),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def print_environment_summary() -> None:
    """本机实际安装版本（诊断信息，不进闸）。"""
    missing = []
    for name in declared_direct_deps():
        try:
            print(f"  {name}=={metadata.version(name)}")
        except metadata.PackageNotFoundError:
            missing.append(name)
    if missing:
        print(f"  (declared but not installed here: {', '.join(missing)})")


def main() -> int:
    check = "--check" in sys.argv
    rendered = generate_inventory()
    if check:
        if not OUTPUT.exists():
            print(f"[app-inventory] MISSING {OUTPUT}", file=sys.stderr)
            return 1
        if OUTPUT.read_text(encoding="utf-8") != rendered:
            print(
                "[app-inventory] DRIFT: requirements.txt 依赖集合变化；"
                "运行 `python scripts/gen_app_inventory.py` 再生",
                file=sys.stderr,
            )
            return 1
        print("[app-inventory] ok")
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(rendered, encoding="utf-8")
    print(f"[app-inventory] wrote {OUTPUT}")
    print("[app-inventory] installed versions (informational):")
    print_environment_summary()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
