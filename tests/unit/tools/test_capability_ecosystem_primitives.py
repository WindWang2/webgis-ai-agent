"""G06 CAPABILITY ECOSYSTEM — 原生原语 / 扩展全链红灯测试（M1）。

覆盖 Oracle：CAP-O08（条件多分位数分区栅格统计）、CAP-O09（线网服务覆盖
一致性）、CAP-O06 的 dispatch 段（cost_unit=seconds 请求 distance 的 typed
错误）、CAP-O10（扩展包 certify/activate/discover/deactivate 全链）。

纪律同 ``tests/unit/gis/test_capability_ecosystem_oracles.py``：新实现模块
import 集中在单个 try/except ModuleNotFoundError 里置 MISSING，每个用例开头
``assert not MISSING``；真实 import、无 stub、无 importorskip、无 skip。

真实数据（10x10 float32 GeoTIFF、面/线 GeoJSON、最小扩展 pack）一律用
``tmp_path`` 在测试进程内生成，不写仓库任何源码/账本。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.transform import from_origin

# ── 既有权威源（M0 present；正常 import）──────────────────────────────
from app.tools.registry import ToolRegistry
from app.tools import init_tools
from app.lib.gis.execution_catalog import (
    build_certification_index_from_host,
    compile_execution_catalog,
)
from app.extensions_platform.host import (
    ExtensionHost,
    ExtensionState,
    HostPolicy,
    configure_extension_host,
    get_extension_host,
)
from app.extensions_platform.certification import certify_extension
from app.extensions_platform.signing import sign_pack

# ── 新实现模块（主 Agent 待写）：集中在单个 try/except ─────────────────
MISSING = False
try:
    from app.tools.capability_ecosystem_tools import (
        register_capability_ecosystem_tools,
    )
    from app.lib.gis.capability_availability import compile_availability_snapshot
    from app.lib.gis.provider_eligibility import extension_lifecycle_view
except ModuleNotFoundError:
    MISSING = True


RED = "实现模块尚未落地（M1 红灯证据）"
_ENV_KEYS = ("data", "artifact", "science", "budget")

# CAP-O08：面 + 栅格（两个全幅多边形，保证分位数已知答案不依赖 zone 切法）
_O08_ZONES: dict[str, Any] = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"zone_id": 1},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0, 10], [10, 10], [10, 0], [0, 0], [0, 10]]],
            },
        },
        {
            "type": "Feature",
            "properties": {"zone_id": 2},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0, 10], [10, 10], [10, 0], [0, 0], [0, 10]]],
            },
        },
    ],
}

# CAP-O09：线网（networkx 图）+ 设施点；两方案 A/B 几何完全相同
_O09_NETWORK: dict[str, Any] = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "properties": {"edge": "e1"},
         "geometry": {"type": "LineString", "coordinates": [[0, 0], [2, 0]]}},
        {"type": "Feature", "properties": {"edge": "e2"},
         "geometry": {"type": "LineString", "coordinates": [[2, 0], [2, 2]]}},
        {"type": "Feature", "properties": {"edge": "e3"},
         "geometry": {"type": "LineString", "coordinates": [[2, 2], [0, 2]]}},
        {"type": "Feature", "properties": {"edge": "e4"},
         "geometry": {"type": "LineString", "coordinates": [[0, 2], [0, 0]]}},
    ],
}
_O09_FACILITIES: dict[str, Any] = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "properties": {"scheme": "A", "id": "f1"},
         "geometry": {"type": "Point", "coordinates": [0, 0]}},
        {"type": "Feature", "properties": {"scheme": "B", "id": "f2"},
         "geometry": {"type": "Point", "coordinates": [0, 0]}},
    ],
}


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    init_tools(reg)
    return reg


def _envelope(result: Any, _depth: int = 0) -> dict[str, Any]:
    """从 dispatch 结果里取出 ``data/artifact/science/budget`` 封套。

    兼容工具直接返回封套 dict，或外层再包一层（{"ok":..., "result":...}）。
    """
    if _depth > 8:
        raise AssertionError("结果封套嵌套过深")
    if isinstance(result, dict):
        if any(k in result for k in _ENV_KEYS):
            return result
        for v in result.values():
            try:
                return _envelope(v, _depth + 1)
            except AssertionError:
                continue
    if hasattr(result, "to_dict"):
        return _envelope(result.to_dict(), _depth + 1)
    raise AssertionError(
        f"工具结果未含封套字段 {_ENV_KEYS}：{type(result).__name__}")


def _as_dict(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    return dict(vars(obj))


def _numeric_values(obj: Any, _depth: int = 0) -> list[float]:
    out: list[float] = []
    if _depth > 12:
        return out
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        return [float(obj)]
    if isinstance(obj, dict):
        for v in obj.values():
            out.extend(_numeric_values(v, _depth + 1))
    elif isinstance(obj, (list, tuple, set)):
        for v in obj:
            out.extend(_numeric_values(v, _depth + 1))
    return out


def _count_like(obj: Any, _depth: int = 0) -> list[float]:
    """递归收集『有效样本计数 / 覆盖率』类的数值（metamorphic 对拍用）。"""
    out: list[float] = []
    if _depth > 12:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            lk = str(k).lower()
            if any(t in lk for t in (
                "count", "valid", "sample", "n_pixels", "n_samples",
                "coverage", "covered")) and isinstance(v, (int, float)) \
                    and not isinstance(v, bool):
                out.append(float(v))
            out.extend(_count_like(v, _depth + 1))
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out.extend(_count_like(v, _depth + 1))
    return out


def _allowed_raster_path(name: str) -> Path:
    """栅格必须落在 ``validate_data_path`` 的合法工作区 ./tmp（既有工具同闸）。

    R1 独立冷审 F-01 修复后，新原语复用 ``app/utils/path.validate_data_path``
    拒绝目录遍历 / symlink 逃逸 / /vsi* 远端读取；pytest 的 ``tmp_path``
    在 /tmp/pytest-* 不在允许范围内，因此这里写到仓库 ./tmp 工作区
    （``.gitignore`` 的 ``/tmp/`` 已忽略，不污染提交）。
    """
    base = Path.cwd() / "tmp" / "g06_capability_tests"
    base.mkdir(parents=True, exist_ok=True)
    return base / name


def _write_raster(path: Path) -> np.ndarray:
    """10x10 float32 真实 GeoTIFF，含 nodata；返回写入的数组（已知答案源）。"""
    data = np.arange(100, dtype="float32").reshape(10, 10)
    data[0, 0] = -9999.0
    with rasterio.open(
        path, "w", driver="GTiff", height=10, width=10, count=1,
        dtype="float32", nodata=-9999.0, crs="EPSG:3857",
        transform=from_origin(0, 10, 1, 1),
    ) as ds:
        ds.write(data, 1)
    return data


# ── CAP-O08 原生算法 #1 ───────────────────────────────────────────────


def test_cap_o08_registration_function_registers_both_primitives():
    """register_capability_ecosystem_tools 真实注册两项原语；init_tools 亦可达。"""
    assert not MISSING, RED
    reg = ToolRegistry()
    register_capability_ecosystem_tools(reg)
    assert "conditional_quantile_zonal_stats" in reg.tool_names()
    assert "network_service_coverage_consistency" in reg.tool_names()

    boot = _registry()
    assert "conditional_quantile_zonal_stats" in boot.tool_names(), (
        "契约 E：注册函数须挂进 app/tools/__init__.py:_TOOL_MODULES，"
        "init_tools 后真实可达")
    assert "network_service_coverage_consistency" in boot.tool_names()


def test_cap_o08_conditional_quantile_zonal_stats(tmp_path: Path):
    """真实 dispatch：分位数与 numpy 手算一致；where 变化 → 有效样本计数变化。"""
    assert not MISSING, RED
    reg = _registry()
    assert "conditional_quantile_zonal_stats" in reg.tool_names()

    raster_path = _allowed_raster_path(f"grid_{tmp_path.name}.tif")
    data = _write_raster(raster_path)
    args: dict[str, Any] = {
        "zones": _O08_ZONES,
        "raster_path": str(raster_path),
        "quantiles": [0.25, 0.5, 0.75],
        "where": "value > 50",
        "nodata": -9999.0,
    }
    result = asyncio.run(
        reg.dispatch("conditional_quantile_zonal_stats", args))
    env = _envelope(result)
    for key in _ENV_KEYS:
        assert key in env, f"结果封套缺字段 {key!r}"

    # 已知答案 oracle：where='value > 50' → 有效值 51..99（排除 nodata）
    valid = data[(data != -9999.0) & (data > 50)]
    expected = np.quantile(valid.astype("float64"), [0.25, 0.5, 0.75])
    nums = _numeric_values(env["data"])
    matched = sum(
        1 for e in expected if any(abs(n - float(e)) <= 0.5 for n in nums))
    assert matched >= 2, (
        f"至少 2 个分位数须与 numpy 手算一致：expected={expected.tolist()}，"
        f"结果数值样本={nums[:20]}")
    assert len(args["quantiles"]) >= 2

    # metamorphic：where 收紧 → 有效样本计数 / 分位数必须变化
    args2 = dict(args)
    args2["where"] = "value > 90"
    result2 = asyncio.run(
        reg.dispatch("conditional_quantile_zonal_stats", args2))
    env2 = _envelope(result2)
    nums2 = _numeric_values(env2["data"])
    assert (_count_like(env["data"]) != _count_like(env2["data"])
            or nums != nums2), (
        "where 条件变化必须改变有效样本计数 / 覆盖率（metamorphic）")


# ── CAP-O09 原生算法 #2 ───────────────────────────────────────────────


def test_cap_o09_network_service_coverage_consistency(tmp_path: Path):
    """真实 dispatch：线网一致性数值 + artifact ref + 科学字段；完全相同 → 1.0。"""
    assert not MISSING, RED
    reg = _registry()
    assert "network_service_coverage_consistency" in reg.tool_names()

    args: dict[str, Any] = {
        "network": _O09_NETWORK,
        "facilities": _O09_FACILITIES,
        "cutoff": 2.0,
        "cost_unit": "meters",
    }
    result = asyncio.run(
        reg.dispatch("network_service_coverage_consistency", args))
    env = _envelope(result)
    for key in _ENV_KEYS:
        assert key in env, f"结果封套缺字段 {key!r}"
    assert env["artifact"] is not None, "须给 artifact ref"
    assert env["science"] is not None, "须给科学方法披露"

    nums = _numeric_values(env["data"])
    assert nums, "输出须含一致性数值（Jaccard / 差异段）"
    assert any(0.0 <= n <= 1.0 for n in nums), (
        f"Jaccard 类一致性数值须落在 [0,1]：{nums[:20]}")

    # 不同 geometry 形态：O09 是线网，O08 是面 + 栅格
    net_types = {f["geometry"]["type"] for f in _O09_NETWORK["features"]}
    zone_types = {f["geometry"]["type"] for f in _O08_ZONES["features"]}
    assert net_types == {"LineString"} and zone_types == {"Polygon"}
    assert net_types != zone_types, "两项原语输入几何形态必须真实不同"

    # metamorphic：两方案完全相同时一致性 == 1.0
    identical = dict(args)
    identical["facilities"] = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"scheme": "A", "id": "f1"},
             "geometry": {"type": "Point", "coordinates": [0, 0]}},
            {"type": "Feature", "properties": {"scheme": "B", "id": "f1"},
             "geometry": {"type": "Point", "coordinates": [0, 0]}},
        ],
    }
    result_ident = asyncio.run(
        reg.dispatch("network_service_coverage_consistency", identical))
    nums_ident = _numeric_values(_envelope(result_ident)["data"])
    assert any(abs(n - 1.0) < 1e-6 for n in nums_ident), (
        f"两方案完全相同时一致性应 == 1.0，实际 {nums_ident[:20]}")


# ── CAP-O06 dispatch 段：cost_unit=seconds 请求 distance 的 typed 错误 ──


def test_cap_o06_network_seconds_distance_returns_typed_error():
    """cost_unit='seconds' 而请求 distance（米）不得静默成功，须 typed 错误。"""
    assert not MISSING, RED
    reg = _registry()
    assert "network_service_coverage_consistency" in reg.tool_names()

    base: dict[str, Any] = {
        "network": _O09_NETWORK,
        "facilities": _O09_FACILITIES,
        "cutoff": 2.0,
        "cost_unit": "seconds",
    }
    variants = [
        dict(base, metric="distance"),
        dict(base, output_unit="meters"),
        dict(base, request_metric="distance"),
    ]
    typed_errors: list[str] = []
    for args in variants:
        raised: BaseException | None = None
        result: Any = None
        try:
            result = asyncio.run(
                reg.dispatch("network_service_coverage_consistency", args))
        except Exception as exc:  # noqa: BLE001 — dispatch 抛错是预期路径之一
            raised = exc
        if raised is not None:
            text = f"{type(raised).__name__}: {raised}".upper()
            assert type(raised).__name__ != "AssertionError"
            if any(k in text for k in (
                "UNIT", "DIMENSION", "SECOND", "DISTANCE", "CONFLICT",
                "INELIGIBLE", "ERROR", "TYPE")):
                typed_errors.append(text)
        else:
            d = _as_dict(result)
            text = json.dumps(d, default=str).upper()
            # 返回封套必须是显式 typed 错误，不得伪装成完整成功封套
            if not all(k in d for k in _ENV_KEYS) and any(
                k in text for k in (
                    "ERROR", "INELIGIBLE", "CONFLICT", "UNIT", "DIMENSION")):
                typed_errors.append(text)
    assert typed_errors, (
        "cost_unit='seconds' + 请求 distance 必须返回 typed 错误（CAP-O06 回归保护）")
    assert any(
        any(k in t for k in ("UNIT", "DIMENSION", "SECOND", "DISTANCE"))
        for t in typed_errors), (
        "typed 错误应携带量纲冲突语义，而非泛化的参数校验失败")


# ── R1 独立冷审 P1 修复回归（F-01 / F-02 / F-03）─────────────────────


def test_r1_f01_raster_path_guard_rejects_escape_and_remote():
    """F-01：拒绝目录遍历 / 绝对路径越界 / GDAL /vsi* 远端读取。"""
    assert not MISSING, RED
    reg = _registry()
    cases = [
        "../../etc/passwd",
        "/etc/passwd",
        "/vsicurl/https://example.com/grid.tif",
        "https://example.com/grid.tif",
        "/vsis3/bucket/grid.tif",
    ]
    for bad in cases:
        result = asyncio.run(reg.dispatch(
            "conditional_quantile_zonal_stats", {
                "zones": _O08_ZONES, "raster_path": bad,
                "quantiles": [0.5], "where": "",
            }))
        d = _as_dict(result)
        text = json.dumps(d, default=str).upper()
        assert not all(k in d for k in _ENV_KEYS), (
            f"非法路径 {bad!r} 不得返回成功封套")
        assert "FORBIDDEN" in text or "UNREADABLE" in text or "ERROR" in text, (
            f"非法路径 {bad!r} 必须 typed 拒绝，实际 {text[:200]}")


def test_r1_f02_time_mode_requires_real_time_cost():
    """F-02：时间模式不得把几何长度当秒；缺时间成本须 not_evaluated。"""
    assert not MISSING, RED
    reg = _registry()

    # 主路径：cost_unit=seconds 且边无时间属性 → typed not_evaluated，
    # 绝不伪称 time dimension / 绝不返回完整成功封套。
    result = asyncio.run(reg.dispatch(
        "network_service_coverage_consistency", {
            "network": _O09_NETWORK, "facilities": _O09_FACILITIES,
            "cutoff": 2.0, "cost_unit": "seconds",
        }))
    d = _as_dict(result)
    text = json.dumps(d, default=str).upper()
    assert "TIME_COST_NOT_EVALUATED" in text, (
        f"时间模式主路径必须 typed not_evaluated，实际 {text[:250]}")
    assert not all(k in d for k in _ENV_KEYS)

    # 提供真实秒数 → 走真实计算，且 science 明确 time dimension。
    timed_network = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "properties": {"edge": f"e{i+1}", "travel_time_s": 1.0},
             "geometry": f["geometry"]}
            for i, f in enumerate(_O09_NETWORK["features"])
        ],
    }
    ok = asyncio.run(reg.dispatch(
        "network_service_coverage_consistency", {
            "network": timed_network, "facilities": _O09_FACILITIES,
            "cutoff": 2.0, "cost_unit": "seconds",
        }))
    env = _envelope(ok)
    assert env["science"]["cost_dimension"] == "time"
    assert env["science"]["unit_contract"]["time_unit"] == "seconds"
    assert env["science"]["unit_contract"]["distance_unit"] is None
    assert env["science"]["time_cost_source"].startswith("edge travel_time_s")
    assert any(abs(n - 1.0) < 1e-6 for n in _numeric_values(env["data"]))


def test_r1_f03_large_raster_rejected_before_read(monkeypatch):
    """F-03：超预算栅格必须在 read() 之前被拒，不得先分配整幅波段。"""
    assert not MISSING, RED
    from unittest.mock import MagicMock

    reg = _registry()
    allowed = _allowed_raster_path("f03_probe.tif")
    # 只需一个位于允许目录内的占位路径，真实栅格由 mock dataset 提供。
    allowed.write_bytes(b"placeholder")

    fake_ds = MagicMock()
    fake_ds.width = 10_000
    fake_ds.height = 10_000       # 100M cells > MAX_RASTER_CELLS(40M)
    fake_ds.crs = "EPSG:3857"
    fake_ds.transform = from_origin(0, 10, 1, 1)
    fake_ds.nodata = -9999.0
    fake_ds.read.side_effect = AssertionError(
        "read(1) 被调用 —— 判界没有发生在分配之前")
    fake_ds.__enter__.return_value = fake_ds
    fake_ds.__exit__.return_value = False
    # rasterio 在函数体内惰性 import，因此 patch 真正的 rasterio.open。
    monkeypatch.setattr(rasterio, "open", lambda *a, **k: fake_ds)

    result = asyncio.run(reg.dispatch(
        "conditional_quantile_zonal_stats", {
            "zones": _O08_ZONES, "raster_path": str(allowed),
            "quantiles": [0.5], "where": "",
        }))
    d = _as_dict(result)
    text = json.dumps(d, default=str, ensure_ascii=False)
    assert "BUDGET_EXCEEDED" in text, (
        f"超预算栅格必须 typed 拒绝，实际 {text[:250]}")
    assert "读取前拒绝" in text, "必须明确披露是读取前拒绝"
    fake_ds.read.assert_not_called()


# ── CAP-O10 扩展包全链 ────────────────────────────────────────────────


def _build_minimal_pack(tmp_path: Path) -> tuple[Path, str, str, dict[str, Any]]:
    """只在 tmp_path 造最小本地 pack（manifest + main.py + health.py + 签名）。"""
    packs_dir = tmp_path / "packs"
    pack_dir = packs_dir / "cap_o10_pack"
    pack_dir.mkdir(parents=True)
    keys_dir = tmp_path / "keys"
    keys_dir.mkdir()
    key_id = "g06_cap_o10_test_key"
    key_file = keys_dir / "cap_o10.key"
    key_file.write_bytes(b"g06-cap-o10-hmac-key-for-tests-only")

    ns, ext_id = "cap_o10", "cap_o10.pack"
    manifest = {
        "schema_version": 1,
        "id": ext_id,
        # 真实 schema 约束：manifest.id 必须等于 f"{namespace}.{name}"
        # （app/extensions_platform/manifest.py:466 _cross_field）。
        # ext_id="cap_o10.pack" + namespace="cap_o10" ⇒ name 必须是 "pack"。
        "name": "pack",
        "namespace": ns,
        "version": "1.0.0",
        "api_version": "1.0.0",
        "minimum_core_version": "0.1.0",
        "title": "CAP-O10 minimal local pack",
        "description": "Minimal local pack built in tmp_path only for CAP-O10.",
        "vendor": "g06 capability ecosystem tests",
        "trust": "trusted_builtin",
        "permissions": [],
        "extension_types": ["tools"],
        "entry_point": "main",
        "diagnostics_entry": "health:check",
        "tools": [
            {"name": "cap_o10_probe", "description": "probe tool",
             "summary": "probe", "tier": 1, "side_effect": "pure"},
        ],
    }
    (pack_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (pack_dir / "main.py").write_text(
        "from app.extensions_platform.sdk import ToolExtensionSpec\n"
        "\n"
        "def _probe(value: str = 'x') -> dict:\n"
        "    return {'ok': True, 'value': value}\n"
        "\n"
        "PROBE_TOOL = ToolExtensionSpec(\n"
        "    name='cap_o10_probe', description='CAP-O10 probe tool',\n"
        "    func=_probe, summary='probe', tier=1, side_effect='pure')\n"
        "\n"
        "def activate(ctx) -> None:\n"
        "    ctx.register_tool(PROBE_TOOL)\n",
        encoding="utf-8")
    (pack_dir / "health.py").write_text(
        "def check() -> dict:\n"
        "    return {'ok': True, 'status': 'healthy'}\n",
        encoding="utf-8")

    # 只读复用现有签名能力（不修改 signing.py）
    sign_pack(pack_dir, key_id, key_file)
    policy = HostPolicy(
        roots=(packs_dir,),
        builtin_ids=frozenset({ext_id}),
        trusted_publishers={key_id: key_file},
        allow_local_untrusted_activation=True,
    )
    return packs_dir, ns, ext_id, {"policy": policy, "key_id": key_id}


def test_cap_o10_extension_full_lifecycle_marks_namespace_unavailable(
    tmp_path: Path,
):
    """certify / activate / discover / deactivate 全链；停用后 namespace 不可用
    且 catalog generation 变化；只读 extension_lifecycle_view，不调私有 worker。"""
    assert not MISSING, RED
    packs_dir, ns, ext_id, extra = _build_minimal_pack(tmp_path)
    reg = _registry()
    host = ExtensionHost(reg, extra["policy"])

    prev = get_extension_host()
    configure_extension_host(host)
    try:
        assert get_extension_host() is host

        # discover（先有 records，certify / activate 才有对象）
        host.discover()
        assert host.get_record(ext_id) is not None, "最小 pack 应被真实发现"

        # certify：公开 API certify_extension（结构化报告，不调私有 worker）
        cert = certify_extension(host, ext_id)
        assert isinstance(cert, dict) and "certified" in cert

        # activate
        host.activate(ext_id)
        rec_active = host.get_record(ext_id)
        assert rec_active is not None
        assert rec_active.state in (
            ExtensionState.ACTIVE, ExtensionState.DEGRADED), (
            f"最小 pack 应可激活，实际 {rec_active.state}")

        view_before = extension_lifecycle_view(host)
        idx_before = build_certification_index_from_host(host)
        gen_before = compile_execution_catalog(
            tool_registry=reg,
            certification_index=idx_before).generation_fingerprint
        snap_before = compile_availability_snapshot(
            registry=reg, certification_index=idx_before)

        # deactivate
        host.deactivate(ext_id)
        rec_after = host.get_record(ext_id)
        assert rec_after is not None
        assert rec_after.state is not ExtensionState.ACTIVE, (
            "停用后不得仍是 ACTIVE")

        view_after = extension_lifecycle_view(host)
        assert view_before != view_after, (
            "停用必须改变 extension_lifecycle_view")
        blob = json.dumps(view_after, default=str).lower()
        assert ns in blob, "lifecycle 视图须含该 namespace"
        assert any(k in blob for k in (
            "unavailable", "disabled", "inactive", "deactivated",
            "stale", "false", "not_evaluated")), (
            "停用后该 namespace 须标为不可用（fail-closed）")

        idx_after = build_certification_index_from_host(host)
        gen_after = compile_execution_catalog(
            tool_registry=reg,
            certification_index=idx_after).generation_fingerprint
        snap_after = compile_availability_snapshot(
            registry=reg, certification_index=idx_after)
        assert (gen_before != gen_after
                or idx_before != idx_after
                or snap_before.catalog_generation != snap_after.catalog_generation
                or snap_before.content_fingerprint != snap_after.content_fingerprint), (
            "停用扩展必须改变 catalog generation / 可用性快照指纹")
    finally:
        configure_extension_host(prev)
