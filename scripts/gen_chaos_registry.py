#!/usr/bin/env python
"""生成 docs/quality/certifications/CHAOS_FAULT_REGISTRY.md（ADR-0104 Wave 7+8）。

认证表是派生物：唯一事实源 = tests/fixtures/chaos.py 的 ``FAULTS``
注册表（``fault_catalog()`` 导出）。测试包外零依赖（工厂惰性 import
app 模块，目录导出是纯数据）。

用法：
    python scripts/gen_chaos_registry.py            # 写入
    python scripts/gen_chaos_registry.py --check    # 过期则退出 1
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

DEFAULT_OUT = Path("docs/quality/certifications/CHAOS_FAULT_REGISTRY.md")


def generate() -> str:
    from tests.fixtures.chaos import fault_catalog

    catalog = fault_catalog()
    subsystems = sorted({item["subsystem"] for item in catalog})
    lines = [
        "# Chaos 故障注册表（ADR-0104 Wave 7+8）",
        "",
        "> 本文件是**派生物**：唯一事实源 = `tests/fixtures/chaos.py` 的 `FAULTS`",
        "> 注册表。再生：`python scripts/gen_chaos_registry.py`；字节一致性闸：",
        "> `tests/quality/test_chaos_foundation.py::test_chaos_fault_registry_document_current_and_complete`。",
        "",
        "生产关闭是**结构性的**：注册表只存在于 `tests/` 包内，`app/` 零引用",
        "（`test_no_app_source_references_chaos_module` 全量扫描锁定）。确定性纪律：",
        "只接受显式次数/序列的 schedule，无 RNG、无同步用途的 sleep；每次注入",
        "记录 journal（armed → fired → disarmed），测试必须断言「故障真的开火」。",
        "",
        "| Fault ID | 子系统 | 描述 | 注入方式（接缝） | 期望系统行为 | 注入点（审计 05 证据） |",
        "|---|---|---|---|---|---|",
    ]
    for item in catalog:
        lines.append(
            "| `{fault_id}` | {subsystem} | {description} | {attack} | {expected} | `{injection_point}` |".format(
                **item
            )
        )
    lines.append("")
    lines.append(
        f"共 {len(catalog)} 个注册故障点；子系统："
        + "、".join(f"`{s}`" for s in subsystems)
        + "。"
    )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只校验，不写入")
    args = parser.parse_args()

    content = generate()
    if args.check:
        if not DEFAULT_OUT.exists() or DEFAULT_OUT.read_text(encoding="utf-8") != content:
            print(f"stale: {DEFAULT_OUT}")
            print("run: python scripts/gen_chaos_registry.py")
            return 1
        print("chaos fault registry up to date")
        return 0

    DEFAULT_OUT.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_OUT.write_text(content, encoding="utf-8")
    print(f"wrote {DEFAULT_OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
