#!/usr/bin/env python
"""config JSON Schema 导出（Platform V4，ADR-0131 D7）。

把 pydantic Settings 的 JSON Schema 导出到 ``docs/platform-v4/generated/
config.schema.json``，配 ``--check`` 字节闸：配置字段漂移（新增/删除/
改约束）未同步再生 schema 即红——配置面从此有机器强制的契约文档。

用法::

    python scripts/gen_config_schema.py            # 再生
    python scripts/gen_config_schema.py --check    # 字节闸（quick lane）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = REPO_ROOT / "docs" / "platform-v4" / "generated" / "config.schema.json"
sys.path.insert(0, str(REPO_ROOT))


def generate_schema() -> str:
    from app.core.config import Settings

    schema = Settings.model_json_schema()
    # 确定性导出：排序键 + 统一换行（字节闸要求跨机器一致）
    return json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    check = "--check" in sys.argv
    rendered = generate_schema()
    if check:
        if not OUTPUT.exists():
            print(f"[config-schema] MISSING {OUTPUT}", file=sys.stderr)
            return 1
        current = OUTPUT.read_text(encoding="utf-8")
        if current != rendered:
            print(
                "[config-schema] DRIFT: Settings schema changed; "
                "run `python scripts/gen_config_schema.py` to regenerate",
                file=sys.stderr,
            )
            return 1
        print("[config-schema] ok")
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(rendered, encoding="utf-8")
    print(f"[config-schema] wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
