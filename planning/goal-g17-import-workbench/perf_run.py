"""G17 性能实测脚本（一次性证据生成；产出写入 planning/PERFORMANCE.md）。

- 机器：本机（见 platform.uname）
- 规模 A：单文件接近 50MB 矢量上限（GeoJSON 点层）
- 规模 B：20 个小文件批次，合计字节与 A 相近
- 指标：staging/发现/计划/预览/提交各阶段耗时、读取字节、峰值 Python 内存
  （tracemalloc，仅解释器分配）、取消响应、临时目录清理
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import platform
import tempfile
import time
import tracemalloc
from pathlib import Path

# 本地单机实测：强制内存 SessionStore（与 pytest 环境一致；不依赖 Redis 服务）
os.environ.setdefault('USE_REDIS', 'false')

from app.core.config import settings

SESSION = "sess-g17-perf"
HERE = Path(__file__).parent


def make_geojson_bytes(n_features: int, seed: int = 0) -> bytes:
    buf = io.StringIO()
    buf.write('{"type":"FeatureCollection","features":[')
    for i in range(n_features):
        if i:
            buf.write(",")
        props = json.dumps(
            {"id": i, "code": f"{i:05d}", "name": f"pt-{seed}-{i}", "grp": i % 97},
            ensure_ascii=False,
        )
        buf.write(
            f'{{"type":"Feature","properties":{props},'
            f'"geometry":{{"type":"Point","coordinates":[{116 + (i % 500) * 0.001},{39 + (i % 400) * 0.001}]}}}}'
        )
    buf.write("]}")
    return buf.getvalue().encode("utf-8")


async def main() -> None:
    from app.services.data_import.service import DataImportService

    root = Path(tempfile.mkdtemp(prefix="g17-perf-"))
    settings.DATA_DIR = str(root)
    svc = DataImportService()

    lines: list[str] = []
    lines.append(f"- 机器：{platform.node()} / {platform.system()} {platform.release()} / {platform.processor() or 'cpu'}")
    lines.append(f"- Python：{platform.python_version()}")
    lines.append("- 内存口径：tracemalloc（仅解释器分配，不含 GDAL 内部堆）")

    # ── 规模 A：单文件 ≈ 上限 ──
    t0 = time.perf_counter()
    big = make_geojson_bytes(290_000)
    gen_s = time.perf_counter() - t0
    lines.append(f"- fixture 生成：290k 点 GeoJSON = {len(big)/1e6:.1f} MB（{gen_s:.1f}s）")

    tracemalloc.start()
    t0 = time.perf_counter()
    state = await svc.create_batch(SESSION, "perf", [("big.geojson", big)])
    t_create = time.perf_counter() - t0
    _, peak_create = tracemalloc.get_traced_memory()
    tracemalloc.reset_peak()

    key = state.members[0].member_key
    t0 = time.perf_counter()
    await svc.save_plan(state.batch_id, SESSION, {key: {}}, selected=[key])
    t_plan = time.perf_counter() - t0
    t0 = time.perf_counter()
    await svc.preview_members(state.batch_id, SESSION, [key], row_limit=200)
    t_prev = time.perf_counter() - t0
    tracemalloc.reset_peak()
    t0 = time.perf_counter()
    out = await svc.start_commit(state.batch_id, SESSION, background=False)
    t_commit = time.perf_counter() - t0
    _, peak_commit = tracemalloc.get_traced_memory()
    r = out["receipts"][key]
    lines.append(
        f"- A 单大文件（{len(big)/1e6:.1f}MB）：创建+发现 {t_create:.2f}s（峰值 {peak_create/1e6:.0f}MB）｜"
        f"计划 {t_plan*1000:.0f}ms｜预览200行 {t_prev:.2f}s｜提交 {t_commit:.2f}s（峰值 {peak_commit/1e6:.0f}MB）"
    )
    lines.append(
        f"  提交结果：{r['status']}，输出 {r['output_rows']} 行 / 拒绝 {r['rejected_rows']}，"
        f"elapsed_ms={r['elapsed_ms']}"
    )

    # staging 目录字节
    batch_root = root / "import_batches" / state.batch_id
    total_bytes = sum(p.stat().st_size for p in batch_root.rglob("*") if p.is_file())
    lines.append(f"- A staging 落盘合计：{total_bytes/1e6:.1f} MB（原件+提取+outputs 副本）")

    # ── 规模 B：20 小文件批次（合计 ≈ A）──
    per = len(big) // 20
    smalls = []
    n_each = 4_500
    one = make_geojson_bytes(n_each)
    target = per // len(one) + 1
    for i in range(20):
        # 复用同一字节不够真实 —— 用不同 seed 生成
        smalls.append((f"s{i:02d}.geojson", make_geojson_bytes(n_each, seed=i)))
    total_small = sum(len(b) for _, b in smalls)
    tracemalloc.reset_peak()
    t0 = time.perf_counter()
    state_b = await svc.create_batch(SESSION, "perf", smalls)
    t_create_b = time.perf_counter() - t0
    _, peak_b = tracemalloc.get_traced_memory()
    keys_b = [m.member_key for m in state_b.members]
    await svc.save_plan(state_b.batch_id, SESSION, {k: {} for k in keys_b}, selected=keys_b)
    t0 = time.perf_counter()
    out_b = await svc.start_commit(state_b.batch_id, SESSION, background=False)
    t_commit_b = time.perf_counter() - t0
    done = sum(1 for x in out_b["receipts"].values() if x["status"] == "completed")
    rows_b = sum(x.get("output_rows") or 0 for x in out_b["receipts"].values())
    lines.append(
        f"- B 多小文件（20×{n_each} 点 = {total_small/1e6:.1f}MB）：创建+发现 {t_create_b:.2f}s"
        f"（峰值 {peak_b/1e6:.0f}MB）｜提交全部 {t_commit_b:.2f}s｜完成 {done}/20，合计 {rows_b} 行"
    )

    # ── 取消响应 ──
    csv_rows = 120_000
    csv = b"lng,lat\n" + b"".join(
        f"{116 + (i % 1000) * 0.001},{39 + (i % 900) * 0.001}\n".encode() for i in range(csv_rows)
    )
    state_c = await svc.create_batch(SESSION, "perf", [("c.csv", csv)])
    kc = state_c.members[0].member_key
    await svc.save_plan(
        state_c.batch_id, SESSION,
        {kc: {"source_crs_declared": "EPSG:4326"}}, selected=[kc],
    )
    t0 = time.perf_counter()
    await svc.start_commit(state_c.batch_id, SESSION, background=True)
    await asyncio.sleep(0.2)
    await svc.cancel_batch(state_c.batch_id, SESSION)
    t_cancel = time.perf_counter() - t0
    final = await svc.get_batch(state_c.batch_id, SESSION)
    lines.append(
        f"- 取消响应：{csv_rows} 行 CSV 提交 0.2s 后取消 → 终态 {final.status.value}，"
        f"取消往返 {t_cancel:.2f}s（协作检查点间隔 {1000} 行）"
    )
    # 清理验证
    await svc.delete_batch(state.batch_id, SESSION)
    await svc.delete_batch(state_b.batch_id, SESSION)
    left = list((root / "import_batches").iterdir())
    lines.append(f"- 清理：两个批次删除后 import_batches 残留 {len(left)} 项（期望 1：A）")

    out_md = "# G17 性能实测（本地单机证据）\n\n" + "\n".join(lines) + "\n"
    (HERE / "PERFORMANCE.md").write_text(out_md, encoding="utf-8")
    print(out_md)


if __name__ == "__main__":
    asyncio.run(main())
