"""数据平面边界守卫（W12）+ MVT/分页 parity。

1. 依赖方向：services/extensions_platform/lib 绝不 import app.api（#1545
   回归防线 —— tile 缓存与数据面事实源必须住在服务域，route 只做协议适配）。
2. parity：同一份要素集，MVT 编码（显示路径）与 bbox 窗口分页（浏览路径）
   必须服务同一事实 —— 瓦片内要素集与分页窗口要素集一致（不漏不重）。
"""
import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# 这些目录是数据平面服务域：唯一允许的依赖方向是「route → service」。
SERVICE_DOMAINS = ("app/services", "app/extensions_platform", "app/lib")

# ── 依赖方向 ─────────────────────────────────────────────────────────────────


def _imported_modules(tree: ast.AST) -> list[str]:
    mods: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                mods.append(node.module)
    return mods


def test_service_domain_never_imports_api_routes():
    offenders: list[str] = []
    scanned = 0
    for domain in SERVICE_DOMAINS:
        for path in sorted((REPO_ROOT / domain).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            scanned += 1
            mods = _imported_modules(ast.parse(path.read_text(encoding="utf-8")))
            offenders.extend(
                f"{path.relative_to(REPO_ROOT).as_posix()}: {m}"
                for m in mods
                if m.startswith("app.api.")
            )
    assert scanned > 100, "scanner found too few files — glob broke"
    assert not offenders, (
        "services/extensions_platform/lib 绝不 import app.api（#1545 同族层倒置）；"
        f"把消费逻辑移到服务域或改由 route 注入: {offenders}"
    )


# ── MVT / 分页 parity ────────────────────────────────────────────────────────


def _point_fc(n, cx=104.0, cy=30.0):
    return [
        {
            "type": "Feature",
            "id": i,
            "geometry": {"type": "Point", "coordinates": [cx, cy] if i % 2 == 0 else [cx + 30.0, cy - 30.0]},
            "properties": {"i": i},
        }
        for i in range(n)
    ]


def test_mvt_tile_and_paged_window_serve_same_features():
    """显示（MVT）与浏览（分页）同一事实源：瓦片内的要素集 == bbox 窗口的要素集。"""
    from app.services.data_fabric.tile_identity import tile_bounds_lonlat
    from app.services.feature_pages import page_features
    from app.services.mvt import encode_tile

    z, x, y = 9, 420, 200
    min_lon, min_lat, max_lon, max_lat = tile_bounds_lonlat(z, x, y)
    cx, cy = (min_lon + max_lon) / 2, (min_lat + max_lat) / 2
    features = _point_fc(60, cx=cx, cy=cy)
    fc = {"type": "FeatureCollection", "features": features}

    # 显示路径：整包进编码器（与会话/回退路径同款调用）
    tile = encode_tile(features, z, x, y)
    assert tile, "瓦片编码不得为空"
    tile_ids = _decode_tile_property_i_ids(tile)

    # 浏览路径：bbox 窗口分页（扫描收集全部匹配）
    window_ids = set()
    cursor = None
    while True:
        page = page_features(fc, limit=1000, cursor=cursor, bbox=(min_lon, min_lat, max_lon, max_lat))
        window_ids.update(f["id"] for f in page["features"])
        if not page["has_more"]:
            break
        cursor = page["next_cursor"]

    assert tile_ids == window_ids, (
        "MVT 显示路径与分页浏览路径必须服务同一要素集："
        f"tile={sorted(tile_ids)} window={sorted(window_ids)}"
    )
    assert len(tile_ids) == 30, "恰好一半要素入窗"


# ── 最小 MVT 解码（仅读 layer=data 的属性 i；protobuf 纪律同
#    tests/unit/test_mvt_encoder.py）────────────────────────────────────────


def _read_varint(buf: bytes, pos: int):
    shift = val = 0
    while True:
        b = buf[pos]
        pos += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            return val, pos
        shift += 7


def _iter_fields(buf: bytes):
    """(field_number, wire_type, payload) 流。"""
    pos = 0
    while pos < len(buf):
        key, pos = _read_varint(buf, pos)
        field, wire = key >> 3, key & 7
        if wire == 0:
            v, pos = _read_varint(buf, pos)
            yield field, wire, v
        elif wire == 2:
            ln, pos = _read_varint(buf, pos)
            yield field, wire, buf[pos:pos + ln]
            pos += ln
        else:
            raise ValueError(f"unsupported wire type {wire}")


def _decode_tile_property_i_ids(data: bytes) -> set:
    ids: set = set()
    for field, _wire, layer_buf in _iter_fields(data):
        if field != 3:  # layers
            continue
        name = None
        features = []
        keys: list = []
        values: list = []
        for f, _w, v in _iter_fields(layer_buf):
            if f == 1:
                name = v.decode()
            elif f == 2:
                features.append(v)
            elif f == 3:
                keys.append(v.decode())
            elif f == 4:
                values.append(v)
        if name != "data":
            continue
        i_key_idx = keys.index("i") if "i" in keys else None
        if i_key_idx is None:
            continue
        for fb in features:
            for f, _w, v in _iter_fields(fb):
                if f != 2:  # tags
                    continue
                # tags 是 packed varint 流：(key_idx, val_idx) 成对
                raw = []
                pos = 0
                while pos < len(v):
                    tv, pos = _read_varint(v, pos)
                    raw.append(tv)
                for idx in range(0, len(raw) - 1, 2):
                    if raw[idx] != i_key_idx:
                        continue
                    for vf, _vw, vv in _iter_fields(values[raw[idx + 1]]):
                        if vf == 4:  # int_value（sint64 语义，本测试全为非负）
                            ids.add(vv)
    return ids
