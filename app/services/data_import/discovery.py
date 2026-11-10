"""G17 导入工作台 —— 容器成员发现（inspect 阶段）。

对 staging 中的每个源文件枚举可导入成员：
- .geojson/.json → 单矢量成员（metadata 读取：pyogrio.read_info，零行读）
- .gpkg/.kml     → 每图层一个成员（pyogrio.list_layers + read_info(layer=)）
- .zip           → Shapefile 组件按（容器内目录, basename）归组；每集一成员
- .csv           → 单表格成员（嗅探分隔符/编码/表头；提供坐标列候选）
- .xlsx          → 每 sheet 一个表格成员（read_only + data_only；公式缓存披露）

纪律：
- 全部 GDAL 读取走既有不可信内容守卫（魔数/驱动白名单/zip 校验），
  只在已批准目录（DATA_DIR staging）操作。
- 有界：成员数、字段展示数、zip 条目/展开字节、sheet 采样行。
- 诚实：feature_count 未知即 None；不支持的内容给 unsupported_reason。
"""
from __future__ import annotations

import logging
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.services.data_import import budgets
from app.services.data_import.models import (
    ContainerKind,
    MemberKind,
    MemberRecord,
    SourceFile,
)
from app.services.data_parser import (
    LAT_COLUMNS,
    LNG_COLUMNS,
    ParseError,
    check_detected_driver,
    check_untrusted_vector_content,
    detect_csv_columns,
    geojson_declares_crs,
    validate_shapefile_zip,
)

logger = logging.getLogger(__name__)

_SHAPEFILE_COMPONENT_EXTS = {".shp", ".dbf", ".shx", ".prj", ".cpg", ".sbn", ".sbx", ".aih", ".aix", ".qix", ".atx"}
_ENCODING_FALLBACK_CHAIN = ("utf-8-sig", "utf-8", "gb18030")


class DiscoveryError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = str(detail)[:300]


def _read_info_bounded(path: Path, layer: Optional[str] = None) -> Dict[str, Any]:
    import pyogrio

    kwargs: Dict[str, Any] = {}
    if layer is not None:
        kwargs["layer"] = layer
    try:
        info = pyogrio.read_info(str(path), **kwargs)
    except Exception as e:  # noqa: BLE001 — GDAL 错误面统一转 typed
        raise DiscoveryError("METADATA_READ_FAILED", f"{path.name}: {e}") from e
    return {k: (v.tolist() if hasattr(v, "tolist") else v) for k, v in dict(info).items()}


def _crs_name(info: Dict[str, Any]) -> Optional[str]:
    crs = info.get("crs")
    if crs is None:
        return None
    # pyogrio 返回 dict（{"id"/"wkt"} 形态）或字符串
    if isinstance(crs, dict):
        ident = crs.get("id") or crs.get("name")
        if isinstance(ident, str) and ident:
            return ident
        wkt = crs.get("wkt")
        if isinstance(wkt, str) and wkt:
            return wkt[:120]
        return None
    text = str(crs)
    return text or None


def _fields_from_info(info: Dict[str, Any]) -> List[str]:
    fields = info.get("fields")
    names = [str(f) for f in (fields or [])][: budgets.MAX_COLUMNS_SHOWN]
    return names


def _vector_member(
    source: SourceFile,
    member_key: str,
    container: ContainerKind,
    display_name: str,
    info: Dict[str, Any],
    *,
    crs_source_hint: str = "source_metadata",
    size_bytes: Optional[int] = None,
) -> MemberRecord:
    geom = info.get("geometry_type") or None
    crs = _crs_name(info)
    return MemberRecord(
        member_key=member_key,
        source_id=source.source_id,
        container=container,
        kind=MemberKind.VECTOR,
        display_name=display_name,
        fields=_fields_from_info(info),
        geometry_type=str(geom) if geom else None,
        feature_count=int(info["features"]) if info.get("features") is not None else None,
        crs=crs,
        crs_source="source_metadata" if crs else "unknown",
        size_bytes=size_bytes,
    )


def discover_geojson(path: Path, source: SourceFile) -> List[MemberRecord]:
    check_untrusted_vector_content(path, ".geojson")
    check_detected_driver(path, ".geojson")
    info = _read_info_bounded(path)
    member = _vector_member(source, f"f:{source.source_id}", ContainerKind.FILE, source.original_name, info)
    if not geojson_declares_crs(path):
        # RFC 7946：无 crs 成员即缺省 EPSG:4326（与旧上传语义一致，非猜测）
        member.crs = "EPSG:4326"
        member.crs_source = "rfc7946_default"
    return [member]


def discover_gpkg(path: Path, source: SourceFile) -> List[MemberRecord]:
    import pyogrio

    check_untrusted_vector_content(path, ".gpkg")
    check_detected_driver(path, ".gpkg")
    try:
        layers = pyogrio.list_layers(str(path))
    except Exception as e:  # noqa: BLE001
        raise DiscoveryError("LAYER_LIST_FAILED", f"{source.original_name}: {e}") from e
    members: List[MemberRecord] = []
    for row in layers[: budgets.MAX_MEMBERS_PER_CONTAINER]:
        name = str(row[0])
        info = _read_info_bounded(path, layer=name)
        m = _vector_member(
            source, f"c:{source.source_id}:gpkg:{name}", ContainerKind.GPKG, name, info
        )
        m.container_ref = name
        members.append(m)
    if len(layers) > budgets.MAX_MEMBERS_PER_CONTAINER:
        raise DiscoveryError(
            "TOO_MANY_LAYERS",
            f"{source.original_name}: {len(layers)} 个图层超过上限 "
            f"{budgets.MAX_MEMBERS_PER_CONTAINER}",
        )
    return members


def discover_kml(path: Path, source: SourceFile) -> List[MemberRecord]:
    import pyogrio

    check_untrusted_vector_content(path, ".kml")
    try:
        layers = pyogrio.list_layers(str(path))
    except Exception as e:  # noqa: BLE001
        raise DiscoveryError("LAYER_LIST_FAILED", f"{source.original_name}: {e}") from e
    members: List[MemberRecord] = []
    for row in layers[: budgets.MAX_MEMBERS_PER_CONTAINER]:
        name = str(row[0])
        info = _read_info_bounded(path, layer=name)
        km = _vector_member(
            source,
            f"c:{source.source_id}:kml:{name}",
            ContainerKind.KML,
            name,
            info,
        )
        km.container_ref = name
        members.append(km)
    if len(layers) > budgets.MAX_MEMBERS_PER_CONTAINER:
        raise DiscoveryError(
            "TOO_MANY_LAYERS",
            f"{source.original_name}: {len(layers)} 个成员超过上限 "
            f"{budgets.MAX_MEMBERS_PER_CONTAINER}",
        )
    return members


def _group_shapefile_sets(zf: zipfile.ZipFile) -> Dict[Tuple[str, str], Dict[str, str]]:
    """zip 条目 →（容器内目录, basename）→ {ext: entry_name} 组件映射。

    组件必须同目录同名才归为一套：不同目录同名 basename 不串用（场景 3）。
    """
    sets: Dict[Tuple[str, str], Dict[str, str]] = {}
    for name in zf.namelist():
        if name.endswith("/"):
            continue
        pure = Path(name)
        ext = pure.suffix.lower()
        if ext not in _SHAPEFILE_COMPONENT_EXTS:
            continue
        key = (str(pure.parent), pure.stem)
        sets.setdefault(key, {})[ext] = name
    return sets


def zipset_dir_name(key_dir: str, base: str) -> str:
    """zipset 提取子目录的确定式名称（discovery 与 preview/commit 共用）。"""
    return "set-" + _MEMBER_SAFE_RE.sub("_", f"{key_dir}__{base}")[:120]


def discover_zip(
    path: Path, source: SourceFile, extract_dir: Path
) -> Tuple[List[MemberRecord], List[str]]:
    """枚举 zip 内 shapefile 集；返回 (成员, 披露注记)。

    组件解压到 extract_dir/<set-index>/，读取走 .shp 实路径。
    """
    validate_shapefile_zip(path)  # 穿越/vnd 成员/组件/炸弹（既有守卫）
    notes: List[str] = []
    try:
        zf = zipfile.ZipFile(path, "r")
    except zipfile.BadZipFile:
        raise DiscoveryError("BAD_ZIP", source.original_name)
    with zf:
        names = zf.namelist()
        if len(names) > budgets.MAX_ZIP_ENTRIES:
            raise DiscoveryError("TOO_MANY_ZIP_ENTRIES", f"{len(names)} > {budgets.MAX_ZIP_ENTRIES}")
        for name in names:
            if Path(name).suffix.lower() == ".zip":
                notes.append(f"{source.original_name}: 嵌套 zip 不受支持（{name} 已跳过）")
        total_uncompressed = sum(i.file_size for i in zf.infolist())
        if total_uncompressed > budgets.MAX_BATCH_EXPANDED_BYTES:
            raise DiscoveryError(
                "ZIP_EXPANSION_TOO_LARGE",
                f"{source.original_name}: 展开后 {total_uncompressed} 字节超过批次预算",
            )
        groups = _group_shapefile_sets(zf)
        unsupported = {k: v for k, v in groups.items() if not (".shp" in v and ".dbf" in v)}
        complete = {k: v for k, v in groups.items() if ".shp" in v and ".dbf" in v}
        for (dirn, base) in sorted(unsupported):
            missing = [e for e in (".shp", ".dbf") if e not in unsupported[(dirn, base)]]
            notes.append(
                f"{source.original_name}: {dirn}/{base} 缺少组件 {'/'.join(missing)}，不可导入"
            )
        if len(complete) > budgets.MAX_MEMBERS_PER_CONTAINER:
            raise DiscoveryError(
                "TOO_MANY_LAYERS",
                f"{source.original_name}: {len(complete)} 套 shapefile 超过上限 "
                f"{budgets.MAX_MEMBERS_PER_CONTAINER}",
            )
        members: List[MemberRecord] = []
        extract_dir.mkdir(parents=True, exist_ok=True)
        for (dirn, base), comps in sorted(complete.items()):
            key_dir = dirn if dirn not in (".", "") else "."
            set_dir = extract_dir / zipset_dir_name(key_dir, base)
            set_dir.mkdir(exist_ok=True)
            size_bytes = 0
            for ext, entry in sorted(comps.items()):
                target = set_dir / f"{base}{ext}"
                data = zf.read(entry)
                size_bytes += len(data)
                if len(data) > 64 * 1024 * 1024:  # pragma: no cover - 单组件防御
                    raise DiscoveryError("ZIP_MEMBER_TOO_LARGE", entry)
                target.write_bytes(data)
            shp_path = set_dir / f"{base}.shp"
            try:
                check_untrusted_vector_content(shp_path, ".shp")
                info = _read_info_bounded(shp_path)
            except DiscoveryError as e:
                notes.append(f"{source.original_name}: {dirn}/{base} 读取失败（{e.code}）")
                continue
            # member_key 用容器内目录+basename（与 UI 排序无关；同 zip 重传可复现）
            m = _vector_member(
                source,
                f"c:{source.source_id}:zipset:{key_dir}:{base}",
                ContainerKind.ZIPSET,
                f"{base} ({key_dir})" if key_dir != "." else base,
                info,
                size_bytes=size_bytes,
            )
            m.container_ref = zipset_dir_name(key_dir, base)
            m.container_ref_detail = base
            members.append(m)
    return members, notes


def _sniff_csv(path: Path, encoding: Optional[str]) -> Tuple[str, List[str], Optional[str]]:
    """有界嗅探：返回 (encoding, columns, delimiter)。"""
    import csv
    import io

    chain: Tuple[str, ...] = (encoding,) if encoding else _ENCODING_FALLBACK_CHAIN
    last_err: Optional[Exception] = None
    sample = path.read_bytes()[: 256 * 1024]
    for enc in chain:
        try:
            text = sample.decode(enc)
        except (UnicodeDecodeError, LookupError) as e:
            last_err = e
            continue
        first_lines = [ln for ln in text.splitlines() if ln.strip()][:5]
        if not first_lines:
            raise DiscoveryError("EMPTY_TABLE", f"{path.name}: 无数据行")
        delim: Optional[str] = None
        try:
            dialect = csv.Sniffer().sniff("\n".join(first_lines), delimiters=",;\t|")
            delim = dialect.delimiter
        except csv.Error:
            delim = ","
        header = next(csv.reader(io.StringIO(first_lines[0]), delimiter=delim or ","))
        columns = [c.strip() for c in header][: budgets.MAX_COLUMNS_SHOWN]
        return enc, columns, delim
    raise DiscoveryError(
        "ENCODING_UNDECODABLE", f"{path.name}: 无法按候选编码解码（{last_err}）"
    )


def discover_csv(path: Path, source: SourceFile) -> Tuple[List[MemberRecord], List[str]]:
    encoding, columns, delim = _sniff_csv(path, None)
    notes: List[str] = []
    lowered = [c.lower() for c in columns]
    lng = next((c for c in columns if c.strip().lower() in LNG_COLUMNS), None)
    lat = next((c for c in columns if c.strip().lower() in LAT_COLUMNS), None)
    if lng or lat:
        notes.append(f"{source.original_name}: 检测到疑似坐标列 x={lng} y={lat}（默认 auto 模式）")
    member = MemberRecord(
        member_key=f"f:{source.source_id}",
        source_id=source.source_id,
        container=ContainerKind.FILE,
        kind=MemberKind.TABLE,
        display_name=source.original_name,
        fields=columns,
        feature_count=None,
        crs=None,
        crs_source="unknown",
        size_bytes=source.size,
    )
    member.hints = {"encoding": encoding, "delimiter": delim, "x_column": lng, "y_column": lat}
    return [member], notes


def xlsx_available() -> bool:
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        return False
    return True


def discover_xlsx(path: Path, source: SourceFile) -> Tuple[List[MemberRecord], List[str]]:
    """XLSX sheet 枚举（read_only + data_only；公式缓存值可用性披露）。

    openpyxl 生产依赖由 requirements.txt 保证；缺席时成员给出
    xlsx_engine_unavailable 诚实披露（不假装支持）。
    """
    if not xlsx_available():
        member = MemberRecord(
            member_key=f"f:{source.source_id}",
            source_id=source.source_id,
            container=ContainerKind.FILE,
            kind=MemberKind.TABLE,
            display_name=source.original_name,
            fields=[],
            unsupported_reason="xlsx_engine_unavailable",
            size_bytes=source.size,
        )
        return [member], [f"{source.original_name}: XLSX 引擎（openpyxl）不可用，无法导入"]
    import openpyxl

    notes: List[str] = []
    try:
        wb_v = openpyxl.load_workbook(path, read_only=True, data_only=True)
        wb_f = openpyxl.load_workbook(path, read_only=True, data_only=False)
    except Exception as e:  # noqa: BLE001
        raise DiscoveryError("XLSX_OPEN_FAILED", f"{source.original_name}: {e}") from e
    members: List[MemberRecord] = []
    with wb_v, wb_f:
        sheets = list(wb_v.sheetnames)[: budgets.XLSX_MAX_SHEETS]
        if len(wb_v.sheetnames) > budgets.XLSX_MAX_SHEETS:
            raise DiscoveryError(
                "TOO_MANY_SHEETS",
                f"{source.original_name}: {len(wb_v.sheetnames)} 个工作表超过上限",
            )
        for sheet in sheets:
            ws_v = wb_v[sheet]
            ws_f = wb_f[sheet]
            columns: List[str] = []
            formula_cells = 0
            uncached_formulas = 0
            rows_seen = 0
            empty = True
            for row_v, row_f in zip(ws_v.iter_rows(max_row=budgets.XLSX_SHEET_SCAN_ROWS, values_only=True),
                                    ws_f.iter_rows(max_row=budgets.XLSX_SHEET_SCAN_ROWS, values_only=True)):
                rows_seen += 1
                if all(v is None or str(v).strip() == "" for v in row_v):
                    continue
                empty = False
                if not columns:
                    columns = [
                        str(v).strip() if v is not None else f"col{i + 1}"
                        for i, v in enumerate(row_v)
                    ][: budgets.MAX_COLUMNS_SHOWN]
                for v, f in zip(row_v, row_f):
                    if isinstance(f, str) and f.startswith("="):
                        formula_cells += 1
                        if v is None:
                            uncached_formulas += 1
            if empty:
                notes.append(f"{source.original_name}/{sheet}: 空工作表")
            member = MemberRecord(
                member_key=f"c:{source.source_id}:sheet:{sheet}",
                source_id=source.source_id,
                container=ContainerKind.SHEET,
                kind=MemberKind.TABLE,
                display_name=f"{source.original_name} :: {sheet}",
                container_ref=sheet,
                fields=columns,
                feature_count=None,
                crs=None,
                crs_source="unknown",
                size_bytes=None,
                unsupported_reason=None if not empty else "empty_sheet",
            )
            if formula_cells:
                note = (
                    f"{source.original_name}/{sheet}: 含 {formula_cells} 个公式单元格"
                    f"（读取缓存值；其中 {uncached_formulas} 个无缓存值将按空值处理）"
                )
                notes.append(note)
            members.append(member)
    return members, notes


def discover_source(
    source: SourceFile, path: Path, extract_root: Path
) -> Tuple[List[MemberRecord], List[str]]:
    """单文件 → 成员清单 + 披露注记（unsupported 文件给占位成员 + 原因）。"""
    ext = source.ext
    notes: List[str] = []
    if ext not in budgets.SUPPORTED_EXTS:
        member = MemberRecord(
            member_key=f"f:{source.source_id}",
            source_id=source.source_id,
            container=ContainerKind.FILE,
            kind=MemberKind.TABLE,
            display_name=source.original_name,
            unsupported_reason="unsupported_format",
        )
        notes.append(f"{source.original_name}: 暂不支持的格式 {ext}（本工作台支持 {' '.join(sorted(budgets.SUPPORTED_EXTS))}）")
        return [member], notes
    try:
        if ext in (".geojson",):
            return discover_geojson(path, source), notes
        if ext == ".gpkg":
            return discover_gpkg(path, source), notes
        if ext == ".kml":
            return discover_kml(path, source), notes
        if ext == ".zip":
            members, znotes = discover_zip(path, source, extract_root / source.source_id)
            return members, znotes
        if ext == ".csv":
            members, cnotes = discover_csv(path, source)
            return members, cnotes
        if ext == ".xlsx":
            return discover_xlsx(path, source)
    except ParseError as e:
        member = MemberRecord(
            member_key=f"f:{source.source_id}",
            source_id=source.source_id,
            container=ContainerKind.FILE,
            kind=MemberKind.TABLE,
            display_name=source.original_name,
            unsupported_reason="content_guard_rejected",
        )
        notes.append(f"{source.original_name}: 内容校验拒绝（{e}）")
        return [member], notes
    raise DiscoveryError("UNSUPPORTED_EXT", ext)  # pragma: no cover
