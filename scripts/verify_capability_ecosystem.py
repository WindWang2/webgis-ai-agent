"""G06 5 分钟本地验收（无 API Key / 无 PostGIS / 无网络 / 不等线上 CI）。

用法::

    python scripts/verify_capability_ecosystem.py
    python scripts/verify_capability_ecosystem.py --json

逐项跑：能力快照 CLI → manifest 检查器 → 2 组离线演示 → 定向 pytest →
catalog 生成物漂移门。任一项失败返回非零，并如实打印失败原因。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


def _run(label: str, cmd: list[str]) -> dict:
    try:
        proc = subprocess.run(
            cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=180,
            check=False)
        return {
            "label": label, "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "command": " ".join(cmd),
            "stdout_tail": (proc.stdout or "").strip()[-600:],
            "stderr_tail": (proc.stderr or "").strip()[-400:],
        }
    except subprocess.TimeoutExpired:
        return {"label": label, "ok": False, "returncode": -1,
                "command": " ".join(cmd), "stdout_tail": "",
                "stderr_tail": "TIMEOUT 180s"}
    except Exception as exc:  # noqa: BLE001
        return {"label": label, "ok": False, "returncode": -1,
                "command": " ".join(cmd), "stdout_tail": "",
                "stderr_tail": f"{type(exc).__name__}: {exc}"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--skip-pytest", action="store_true",
                    help="只跑 CLI / 检查器 / 演示（快速路径）")
    args = ap.parse_args(argv)

    steps = [
        _run("availability_cli", [
            PY, "-m", "app.lib.gis.capability_availability", "--bootstrap",
            "--limit", "3"]),
        _run("pack_manifest_checker", [
            PY, "examples/gis_capability_ecosystem/check_pack.py"]),
        _run("demo_school_poi", [
            PY, "examples/gis_capability_ecosystem/demo_school_poi.py"]),
        _run("demo_nodata_science", [
            PY, "examples/gis_capability_ecosystem/demo_nodata_science.py"]),
    ]
    if not args.skip_pytest:
        steps.append(_run("oracle_pytest", [
            PY, "-m", "pytest",
            "tests/unit/gis/test_capability_ecosystem_oracles.py",
            "tests/unit/tools/test_capability_ecosystem_primitives.py",
            "--no-cov", "-q", "-p", "no:cacheprovider"]))
    steps.append(_run("catalog_drift_gate", [
        PY, "-m", "app.lib.gis.execution_catalog_docs", "--check"]))

    ok = all(s["ok"] for s in steps)
    result = {
        "ok": ok,
        "root": str(ROOT),
        "steps": steps,
        "passed": sum(1 for s in steps if s["ok"]),
        "total": len(steps),
        "disclosure": (
            "全部为本地验证；不等待线上 CI/CD，未修改 Actions。"
            "PostGIS 相关专项列为 opt-in（pytest -m real_services），"
            "不在本默认命令内。"),
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for s in steps:
            print(f"  [{'PASS' if s['ok'] else 'FAIL'}] {s['label']}")
            if not s["ok"]:
                print(f"      cmd: {s['command']}")
                print(f"      rc : {s['returncode']}")
                print(f"      out: {s['stdout_tail'][-300:]}")
                print(f"      err: {s['stderr_tail'][-300:]}")
        print(f"result: {result['passed']}/{result['total']} passed")
        print(result["disclosure"])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
