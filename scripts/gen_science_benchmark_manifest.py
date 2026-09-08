#!/usr/bin/env python3
"""重新生成 docs/science/BENCHMARK_MANIFEST.md（Wave 10 · science-v3）。

事实源（唯一真相，manifest 只是投影）：
- AlgorithmDescriptor 的科学/规模元数据：complexity、approximation_class、
  approximate、resource_envelope、backend_variants（规模窗口）、
  cancellation_profile、tolerance、cpu/memory/io_cost、preferred_execution_policy。

定位（与 tests/benchmarks/ 的哲学一致）：
- 这是**声明面**的机器可读投影（asymptotic risk / 规模窗口 / 资源包络 /
  取消画像 / 近似分类），回答「这个算法允许跑多大、超限谁拒绝」；
- 运行时硬闸仍在实现层（ResourceScaleMismatch / RasterResourceGuard），
  benchmark 用例消费同一批 descriptor 声明做 count/bytes 结构门。

用法：``python scripts/gen_science_benchmark_manifest.py``（工作树根执行）。
输出确定性：同注册表状态必产生字节相同的文档（parity 测试锁定）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.lib.gis.algorithm_registry import get_algorithm_registry  # noqa: E402

HEADER = """# Benchmark Manifest（自动生成 · science-v3 Wave 10）

> **本文件由注册表生成，请勿手工编辑。** 事实源：AlgorithmDescriptor 的
> 规模/精度/资源声明（complexity、approximation_class、resource_envelope、
> backend_variants、cancellation_profile、tolerance）。
> 再生成：`python scripts/gen_science_benchmark_manifest.py`。
>
> 口径：本 manifest 是**声明面**投影；运行时硬闸在实现层
> （ResourceScaleMismatch / RasterResourceGuard），benchmark 结构门消费
> 同一批声明。空字段 = 未声明（不构成承诺）。
"""

# 收录口径：heavy 算法 = 高成本声明或带资源/变体声明的算法。
def _is_heavy(algo) -> bool:
    return (
        algo.cpu_cost == "high" or algo.memory_cost == "high"
        or algo.backend_variants or algo.resource_envelope is not None
    )


def _fmt_window(v) -> str:
    lo = v.min_features if v.min_features is not None else ""
    hi = v.max_features if v.max_features is not None else "∞"
    return f"[{lo},{hi}]"


def _fmt_envelope(env) -> str:
    bits = []
    if env.bytes_per_feature is not None:
        bits.append(f"{env.bytes_per_feature:g}B/feat")
    if env.bytes_per_cell is not None:
        bits.append(f"{env.bytes_per_cell:g}B/cell")
    if env.max_pairs is not None:
        bits.append(f"pairs≤{env.max_pairs}")
    if env.hard_max_features is not None:
        bits.append(f"feat≤{env.hard_max_features}")
    if env.hard_max_cells is not None:
        bits.append(f"cells≤{env.hard_max_cells}")
    return " ".join(bits)


def generate() -> str:
    algos = get_algorithm_registry()
    rows: list[str] = []
    heavy = [
        algos.get(aid) for aid in sorted(algos.all_ids) if _is_heavy(algos.get(aid))
    ]
    for algo in heavy:
        variants = ";".join(
            f"{v.id}({v.backend},{_fmt_window(v)}"
            + (f",{v.approximation_class}" if v.approximation_class else "")
            + ")"
            for v in algo.backend_variants
        ) or "—"
        env = (
            _fmt_envelope(algo.resource_envelope)
            if algo.resource_envelope is not None else "—")
        tol = ""
        if algo.tolerance is not None:
            bits = []
            if algo.tolerance.rtol is not None:
                bits.append(f"rtol={algo.tolerance.rtol:g}")
            if algo.tolerance.atol is not None:
                bits.append(f"atol={algo.tolerance.atol:g}")
            tol = ",".join(bits)
        cancel = algo.cancellation_profile or "—"
        approx = algo.approximation_class or ("approximate" if algo.approximate else "—")
        rows.append(
            f"| `{algo.id}` | {algo.complexity or '—'} | {approx} | {env} | "
            f"{variants} | {cancel} | {tol or '—'} | "
            f"{algo.cpu_cost}/{algo.memory_cost} | "
            f"{algo.preferred_execution_policy or '—'} |"
        )

    lines = [
        HEADER,
        f"统计：{len(heavy)}/{algos.count} 算法进入 heavy 清单"
        "（cpu/memory=high 或声明了资源/变体）。",
        "",
        "| 算法 | 复杂度 | 精度 | 资源包络 | 变体(窗口) | 取消 | 容差 | 成本 cpu/mem | 执行策略 |",
        "|---|---|---|---|---|---|---|---|---|",
        *rows,
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    target = ROOT / "docs" / "science" / "BENCHMARK_MANIFEST.md"
    content = generate()
    target.write_text(content, encoding="utf-8")
    print(f"wrote {target.relative_to(ROOT)} ({len(content.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
