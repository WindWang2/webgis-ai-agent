"""中国县域统计年鉴（乡镇卷）+ 县域面板数据入库与查询服务。

数据流：
- ``python manage.py yearbook-ingest`` 扫描
  ``<LOCAL_GEODATA_DIR>/EXCEL-中国县域统计年鉴（乡镇卷）/``：
  - 历年 zip 内「乡镇基本情况」xlsx（2014-2025，双行表头、目录名逐年漂移）
    → 动态定位表头行、跨年列名归一 → 乡镇行（名称 = 区县名 + 乡镇名）；
  - 区县连接：与 district.shp 的 dt_name 做最长前缀匹配（含 县↔区 改名
    别名、多省重名用文件名省份消歧），写入 district_adcode；
  - 县域面板 xlsx（2000-2024，自带 区县代码）→ county_panel。
- 产物 ``<LOCAL_GEODATA_DIR>/yearbook/yearbook.sqlite``：
  - township_yearbook(pub_year, full_name, district_adcode, indicators JSON)
  - county_panel(year, adcode, county, indicators JSON)
  - township_centers(name, adcode, lng, lat)（gd-poi-ingest 回填乡镇中心点）
  - meta（连接率/指标词表/生成时间）。

查询面：catalog / query_township / query_county_panel，供工具与路由共用。
坐标系：本库纯属性表；空间连接由调用方用 adcode 去 district.shp 取几何，
或用 township_centers 的 WGS84 点。
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── 路径与数据可用性 ──────────────────────────────────────────────────────


def yearbook_root() -> Path:
    root = (settings.LOCAL_GEODATA_DIR or "").strip()
    return Path(root).expanduser() if root else Path("data")


def yearbook_db_path() -> Path:
    return yearbook_root() / "yearbook" / "yearbook.sqlite"


def yearbook_source_dir() -> Path:
    return yearbook_root() / "EXCEL-中国县域统计年鉴（乡镇卷）"


def yearbook_available() -> bool:
    return yearbook_db_path().exists()


# ── 跨年表头归一（2014-2025 实测表头 → 规范指标名）────────────────────────

_WS = re.compile(r"\s+")
_PAREN = re.compile(r"[（(][^）)]*[）)]")

# 规范指标名 → 值单位说明（入库 key 即规范名，查询侧无需再解释单位）。
INDICATOR_UNITS = {
    "行政区域面积(公顷)": "公顷",
    "居民委员会（社区）个数(个)": "个",
    "村民委员会个数(个)": "个",
    "常住人口(人)": "人",
    "户籍人口(人)": "人",
    "城镇建成区人口(人)": "人",
    "从业人员(人)": "人",
    "二三产业从业人员(人)": "人",
    "企业个数(个)": "个",
    "企业从业人员(人)": "人",
    "工业企业个数(个)": "个",
    "规模以上工业企业个数(个)": "个",
    "工业总产值(万元)": "万元",
    "城镇建成区面积(公顷)": "公顷",
    "营业面积50平方米以上商店或超市个数(个)": "个",
}


def _clean_header(v: Any) -> str:
    return _WS.sub("", str(v or ""))


def normalize_indicator(merged: str) -> Optional[str]:
    """把（双行合并后的）表头归一为规范指标名；未识别返回 None（保留原名入库）。"""
    t = _PAREN.sub("", _clean_header(merged))
    # 顺序即优先级：长词在前，防止「工业企业个数」被「企业个数」截胡。
    rules: List[Tuple[Tuple[str, ...], str]] = [
        (("规模以上",), "规模以上工业企业个数(个)"),
        (("营业面积50平方米以上",), "营业面积50平方米以上商店或超市个数(个)"),
        (("居民委员会", "居委会"), "居民委员会（社区）个数(个)"),
        (("村民委员会", "村委会"), "村民委员会个数(个)"),
        (("二三产业从业人员",), "二三产业从业人员(人)"),
        (("工业企业单位", "工业企业个数", "工业企业数"), "工业企业个数(个)"),
        (("企业从业人员",), "企业从业人员(人)"),
        (("企业个数",), "企业个数(个)"),
        (("城镇建成区面积",), "城镇建成区面积(公顷)"),
        (("城镇建成区总人口", "城镇建成区常住人口"), "城镇建成区人口(人)"),
        (("工业总产值",), "工业总产值(万元)"),
        (("行政区域面积",), "行政区域面积(公顷)"),
        (("常住人口",), "常住人口(人)"),
        (("户籍人口",), "户籍人口(人)"),
        (("从业人员",), "从业人员(人)"),
    ]
    for keys, canonical in rules:
        if any(k in t for k in keys):
            return canonical
    return None


def _jsonable(v: Any) -> Any:
    """numpy 标量 → JSON 可序列化的 Python 标量；NaN/None → None。

    真实年鉴 xlsx 的数值常为文本型（'7455'），此处顺手数字化，
    让查询侧能直接做数值比较/排序。
    """
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(v, "item"):
        try:
            v = v.item()
        except Exception:  # noqa: BLE001
            pass
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str):
        s = v.strip().replace(",", "")
        if s and (s.lstrip("-").replace(".", "", 1).isdigit()):
            f = float(s)
            return int(f) if f.is_integer() else f
    return v


# ── 行政区连接（district.shp 最长前缀匹配 + 改名别名）────────────────────


class DistrictIndex:
    """district.shp 名称索引：最长前缀匹配、县↔区 改名别名、多省消歧。"""

    def __init__(self, gdf: pd.DataFrame):
        df = pd.DataFrame({
            "dt_name": gdf["dt_name"].astype(str),
            "dt_adcode": gdf["dt_adcode"].astype(str).str.zfill(6),
            "pr_name": gdf["pr_name"].astype(str),
            "ct_name": gdf.get("ct_name", pd.Series([""] * len(gdf))).astype(str),
        })
        self._by_name: Dict[str, List[Dict[str, str]]] = {}
        for rec in df.to_dict("records"):
            self._by_name.setdefault(rec["dt_name"], []).append(rec)
            # 县↔区 改名别名（2014-2025 大量撤县设区；仅当别名不与真实区县名冲突时注册）
            alias = self._suffix_alias(rec["dt_name"])
            if alias and alias not in self._by_name:
                self._by_name.setdefault(alias, []).append({**rec, "alias_of": rec["dt_name"]})
        self.max_name_len = max((len(k) for k in self._by_name), default=0)

    @staticmethod
    def _suffix_alias(name: str) -> Optional[str]:
        if name.endswith("县") and not name.endswith("自治县"):
            return name[:-1] + "区"
        if name.endswith("区") and len(name) > 2:
            return name[:-1] + "县"
        return None

    def match(self, full_name: str, province_hint: str = "") -> Optional[Dict[str, str]]:
        """最长前缀匹配 full_name 的区县部分；province_hint 消歧同名区县。

        返回记录含 dt_name（SHP 真名）/alias_of（经别名匹配时的真名），
        前缀长度按真名长度截取（县↔区 别名等长，安全）。
        """
        for size in range(min(self.max_name_len, len(full_name) - 1), 1, -1):
            hits = self._by_name.get(full_name[:size])
            if not hits:
                continue
            chosen = hits[0]
            for h in hits:
                if h["pr_name"] == province_hint:
                    chosen = h
                    break
            return chosen
        return None


def _load_district_index() -> Optional[DistrictIndex]:
    try:
        from app.tools.local_admin import _load_level

        gdf = _load_level("district")
        if gdf is None or "dt_name" not in gdf.columns:
            return None
        return DistrictIndex(gdf)
    except Exception as exc:  # noqa: BLE001 - 连接失败时行照收，adcode 置空
        logger.error("[yearbook] district index unavailable: %s", exc)
        return None


# ── 乡镇卷解析 ────────────────────────────────────────────────────────────

_STOP_ROW = re.compile(r"^(续表|单位[:：]|计算单位|注[:：]|备注|数据来源)")
_SHEET_OK = re.compile(r"乡镇基本情况")


def _find_header_row(raw: pd.DataFrame) -> Optional[int]:
    for i in range(min(10, len(raw))):
        if _clean_header(raw.iat[i, 0]).startswith("名称"):
            return i
    return None


def _merged_headers(raw: pd.DataFrame, hdr: int) -> List[str]:
    """双行表头合并：下一行 #子表头（如 #规模以上）前置拼进上一行。"""
    r1 = [_clean_header(v) for v in raw.iloc[hdr]]
    r2 = (
        [_clean_header(v) for v in raw.iloc[hdr + 1]]
        if hdr + 1 < len(raw) else [""] * len(r1)
    )
    cols: List[str] = []
    for a, b in zip(r1[1:], r2[1:]):  # 首列「名称」跳过
        if b.startswith("#"):  # 子表头（如 #规模以上）：置前拼进主表头（主表头可为空）
            cols.append(b[1:] + a)
        elif a not in ("", "nan"):
            cols.append(a + b)
    return cols


def _sheet_rows(
    raw: pd.DataFrame,
    pub_year: int,
    province_hint: str,
    idx: Optional[DistrictIndex],
) -> List[Tuple[Any, ...]]:
    hdr = _find_header_row(raw)
    if hdr is None:
        return []
    cols = _merged_headers(raw, hdr)
    rows: List[Tuple[Any, ...]] = []
    for rec in raw.iloc[hdr + 1:].itertuples(index=False):
        full = _clean_header(rec[0])
        if (
            len(full) < 2
            or full in ("nan", "名称")
            or _STOP_ROW.match(full)
            or full == province_hint
        ):
            continue
        values = [_jsonable(v) for v in rec[1 : 1 + len(cols)]]
        if not any(v is not None and str(v) != "" for v in values):
            continue  # 整行无数据（表尾空白/省名行/章节行）
        indicators: Dict[str, Any] = {}
        for merged, v in zip(cols, values):
            if v is None or str(v) == "":
                continue
            key = normalize_indicator(merged) or merged
            indicators[key] = v
        if not indicators:
            continue
        hit = idx.match(full, province_hint) if idx else None
        if hit:
            prefix_len = len(hit.get("alias_of") or hit["dt_name"])
            district = full[:prefix_len]
            township = full[prefix_len:] or district
            rows.append((
                pub_year, full, township, hit["dt_adcode"],
                hit["pr_name"], hit["ct_name"], district,
                json.dumps(indicators, ensure_ascii=False),
            ))
        else:
            rows.append((
                pub_year, full, full, None, None, None, None,
                json.dumps(indicators, ensure_ascii=False),
            ))
    return rows


_YEARBOOK_ZIP_RE = re.compile(r"^(20\d{2})-EXCEL")


def _province_hint(filename: str) -> str:
    """文件名省份提示：「…】四川省、贵州省.xlsx」→ 四川省（续表归属首省）。"""
    m = re.search(r"】([^】/]+?)\.xlsx$", filename)
    return m.group(1).split("、")[0].strip() if m else ""


def iter_yearbook_zips(source: Optional[Path] = None) -> List[Tuple[int, Path]]:
    out: List[Tuple[int, Path]] = []
    for p in sorted((source or yearbook_source_dir()).glob("*.zip")):
        m = _YEARBOOK_ZIP_RE.match(p.name)
        if m:
            out.append((int(m.group(1)), p))
    return out


_SCHEMA = """
CREATE TABLE IF NOT EXISTS township_yearbook (
  pub_year        INTEGER NOT NULL,
  full_name       TEXT NOT NULL,
  township        TEXT,
  district_adcode TEXT,
  province        TEXT,
  city            TEXT,
  district        TEXT,
  indicators      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_township_year ON township_yearbook(pub_year);
CREATE INDEX IF NOT EXISTS idx_township_name ON township_yearbook(full_name);
CREATE INDEX IF NOT EXISTS idx_township_adcode ON township_yearbook(district_adcode);
CREATE TABLE IF NOT EXISTS county_panel (
  year       INTEGER NOT NULL,
  adcode     TEXT NOT NULL,
  province   TEXT,
  city       TEXT,
  county     TEXT,
  region     TEXT,
  indicators TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_panel_adcode_year ON county_panel(adcode, year);
CREATE INDEX IF NOT EXISTS idx_panel_county ON county_panel(county);
CREATE TABLE IF NOT EXISTS township_centers (
  name     TEXT NOT NULL,
  adcode   TEXT,
  lng      REAL,
  lat      REAL,
  province TEXT,
  city     TEXT,
  county   TEXT,
  PRIMARY KEY (adcode, name)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def _open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    return conn


def _excel_sheets(data_or_path: Union[bytes, Path, str, BytesIO]) -> pd.ExcelFile:
    target = BytesIO(data_or_path) if isinstance(data_or_path, bytes) else data_or_path
    try:
        return pd.ExcelFile(target, engine="calamine")
    except Exception:  # noqa: BLE001 - calamine 不可用时退 openpyxl
        return pd.ExcelFile(target, engine="openpyxl")


def ingest_yearbook(
    source: Optional[Path] = None,
    *,
    force: bool = False,
    years: Optional[Iterable[int]] = None,
    progress_cb: Optional[Any] = None,
) -> Dict[str, Any]:
    """乡镇卷 zip 系列 → township_yearbook。幂等：默认跳过已导入年份。"""
    db = yearbook_db_path()
    conn = _open_db(db)
    stats: Dict[str, Any] = {"zips": 0, "years": [], "skipped": 0}
    try:
        done = {
            r[0] for r in conn.execute("SELECT DISTINCT pub_year FROM township_yearbook")
        }
        idx = _load_district_index()
        if idx is None:
            logger.warning("[yearbook] district.shp 不可用：乡镇行将缺少 adcode 连接")
        want = set(years) if years else None
        for pub_year, zpath in iter_yearbook_zips(source):
            if want and pub_year not in want:
                continue
            if pub_year in done and not force:
                stats["skipped"] += 1
                continue
            with conn:
                conn.execute("DELETE FROM township_yearbook WHERE pub_year=?", (pub_year,))
            year_rows = 0
            with zipfile.ZipFile(zpath) as zf:
                for name in zf.namelist():
                    if not name.endswith(".xlsx") or not _SHEET_OK.search(name):
                        continue
                    hint = _province_hint(name.rsplit("/", 1)[-1])
                    excel = _excel_sheets(zf.read(name))
                    for sheet in excel.sheet_names:
                        if sheet.upper().startswith("CNKI"):
                            continue
                        rows = _sheet_rows(
                            excel.parse(sheet, header=None), pub_year, hint, idx,
                        )
                        if rows:
                            year_rows += len(rows)
                            with conn:
                                conn.executemany(
                                    "INSERT INTO township_yearbook VALUES (?,?,?,?,?,?,?,?)",
                                    rows,
                                )
            stats["zips"] += 1
            stats["years"].append(pub_year)
            stats[f"rows_{pub_year}"] = year_rows
            logger.info("[yearbook] %s 出版年: %s 乡镇行", pub_year, year_rows)
            if progress_cb:
                try:
                    progress_cb(pub_year, year_rows)
                except Exception:  # noqa: BLE001
                    pass
        total = conn.execute("SELECT COUNT(*) FROM township_yearbook").fetchone()[0]
        linked = conn.execute(
            "SELECT COUNT(*) FROM township_yearbook WHERE district_adcode IS NOT NULL"
        ).fetchone()[0]
        stats["rows"] = total
        stats["linked"] = linked
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO meta VALUES ('township_link_rate', ?)",
                (f"{linked}/{total}",),
            )
            conn.execute(
                "INSERT OR REPLACE INTO meta VALUES ('generated_at', datetime('now'))"
            )
        return stats
    finally:
        conn.close()


# ── 面板数据（county_panel）──────────────────────────────────────────────

_PANEL_META_COLS = {"年份", "省份", "城市", "区县", "区县代码", "所属地域"}


def ingest_county_panel(
    xlsx_path: Optional[Path] = None,
    *,
    force: bool = False,
) -> Dict[str, Any]:
    path = (
        Path(xlsx_path) if xlsx_path
        else yearbook_source_dir() / "【数据年份2000-2024】中国县域面板数据.xlsx"
    )
    if not path.exists():
        return {"error": f"未找到面板数据: {path}"}
    conn = _open_db(yearbook_db_path())
    try:
        if (
            not force
            and conn.execute("SELECT COUNT(*) FROM county_panel").fetchone()[0] > 0
        ):
            return {"skipped": True}
        xl = _excel_sheets(path)
        sheet = "原始数据" if "原始数据" in xl.sheet_names else xl.sheet_names[0]
        df = xl.parse(sheet)
        rows = []
        for rec in df.to_dict("records"):
            indicators = {
                k: _jsonable(v)
                for k, v in rec.items()
                if k not in _PANEL_META_COLS and _jsonable(v) is not None
            }
            adcode = _jsonable(rec.get("区县代码"))
            if adcode is None:
                continue
            rows.append((
                int(_jsonable(rec.get("年份"))),
                str(adcode).zfill(6),
                rec.get("省份"), rec.get("城市"), rec.get("区县"), rec.get("所属地域"),
                json.dumps(indicators, ensure_ascii=False),
            ))
        with conn:
            conn.execute("DELETE FROM county_panel")
            conn.executemany("INSERT INTO county_panel VALUES (?,?,?,?,?,?,?)", rows)
        vocab = sorted({k for r in rows for k in json.loads(r[6])})
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO meta VALUES ('panel_indicators', ?)",
                (json.dumps(vocab, ensure_ascii=False),),
            )
        return {"rows": len(rows), "indicators": len(vocab)}
    finally:
        conn.close()


# ── 查询面（工具 / 路由共用）─────────────────────────────────────────────


def _rows(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> List[Dict[str, Any]]:
    cur = conn.execute(sql, args)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _ro_conn() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{yearbook_db_path()}?mode=ro", uri=True)


def yearbook_catalog() -> Dict[str, Any]:
    if not yearbook_available():
        return {
            "available": False,
            "error": "年鉴库未生成",
            "correction_hint": "运行 python manage.py yearbook-ingest 预处理后重试。",
        }
    conn = _ro_conn()
    try:
        out: Dict[str, Any] = {"available": True, "db": str(yearbook_db_path())}
        try:
            out["township_years"] = [
                r[0] for r in conn.execute(
                    "SELECT DISTINCT pub_year FROM township_yearbook ORDER BY 1"
                )
            ]
            total = conn.execute("SELECT COUNT(*) FROM township_yearbook").fetchone()[0]
            linked = conn.execute(
                "SELECT COUNT(*) FROM township_yearbook WHERE district_adcode IS NOT NULL"
            ).fetchone()[0]
            out["township_rows"] = total
            out["township_link_rate"] = f"{linked}/{total}"
        except sqlite3.OperationalError:
            out["township_years"] = []
        try:
            out["panel_years"] = [
                r[0] for r in conn.execute(
                    "SELECT DISTINCT year FROM county_panel ORDER BY 1"
                )
            ]
            out["panel_rows"] = conn.execute("SELECT COUNT(*) FROM county_panel").fetchone()[0]
        except sqlite3.OperationalError:
            out["panel_years"] = []
        vocab = conn.execute(
            "SELECT value FROM meta WHERE key='panel_indicators'"
        ).fetchone()
        out["panel_indicators"] = json.loads(vocab[0]) if vocab else []
        try:
            out["township_centers"] = conn.execute(
                "SELECT COUNT(*) FROM township_centers"
            ).fetchone()[0]
        except sqlite3.OperationalError:
            out["township_centers"] = 0
        out["note"] = (
            "query_local_yearbook(dataset='township'|'county_panel')；"
            "pub_year 为出版年份（数据年一般为其前一年）。"
        )
        return out
    finally:
        conn.close()


def query_township(
    name: str = "",
    *,
    pub_year: Optional[int] = None,
    province: str = "",
    district_adcode: str = "",
    limit: int = 200,
) -> Dict[str, Any]:
    """乡镇卷查询：名称包含匹配（乡镇名或全名），或按区县 adcode 下钻全部乡镇。"""
    if not yearbook_available():
        return {"error": "年鉴库未生成（python manage.py yearbook-ingest）"}
    if not name and not district_adcode:
        return {"error": "name 与 district_adcode 至少提供一个"}
    conn = _ro_conn()
    try:
        sql = "SELECT * FROM township_yearbook WHERE 1=1"
        args: List[Any] = []
        if name:
            sql += " AND (full_name LIKE ? OR township LIKE ?)"
            args += [f"%{name}%", f"%{name}%"]
        if district_adcode:
            sql += " AND district_adcode = ?"
            args.append(str(district_adcode).zfill(6))
        if province:
            sql += " AND province LIKE ?"
            args.append(f"%{province}%")
        if pub_year:
            sql += " AND pub_year = ?"
            args.append(int(pub_year))
        sql += " ORDER BY pub_year DESC, full_name LIMIT ?"
        limit = max(1, min(int(limit), 2000))
        args.append(limit)
        rows = _rows(conn, sql, tuple(args))
        for r in rows:
            r["indicators"] = json.loads(r["indicators"])
        return {
            "dataset": "township",
            "count": len(rows),
            "rows": rows,
            **({
                "truncated": True,
                "note": "结果截断，可加 pub_year/province/adcode 收窄。",
            } if len(rows) >= limit else {}),
        }
    finally:
        conn.close()


def query_county_panel(
    name: str = "",
    *,
    adcode: str = "",
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    indicators: Optional[List[str]] = None,
    limit: int = 100,
) -> Dict[str, Any]:
    """县域面板查询（2000-2024）：按区县名或 adcode 取时间序列。"""
    if not yearbook_available():
        return {"error": "年鉴库未生成（python manage.py yearbook-ingest）"}
    if not name and not adcode:
        return {"error": "name 与 adcode 至少提供一个"}
    conn = _ro_conn()
    try:
        sql = "SELECT * FROM county_panel WHERE 1=1"
        args: List[Any] = []
        if adcode:
            sql += " AND adcode = ?"
            args.append(str(adcode).zfill(6))
        else:
            sql += " AND county LIKE ?"
            args.append(f"%{name}%")
        if year_from:
            sql += " AND year >= ?"
            args.append(int(year_from))
        if year_to:
            sql += " AND year <= ?"
            args.append(int(year_to))
        sql += " ORDER BY county, year LIMIT ?"
        limit = max(1, min(int(limit), 2000))
        args.append(limit)
        rows = _rows(conn, sql, tuple(args))
        for r in rows:
            full = json.loads(r["indicators"])
            r["indicators"] = (
                {k: v for k, v in full.items() if k in indicators}
                if indicators else full
            )
        return {
            "dataset": "county_panel",
            "count": len(rows),
            "rows": rows,
            **({
                "truncated": True,
                "note": "结果截断（limit 按行计），可指定 indicators 或年份区间。",
            } if len(rows) >= limit else {}),
        }
    finally:
        conn.close()


_CENTER_COLS = ("name", "adcode", "lng", "lat", "province", "city", "county")


def lookup_township_center(name: str, adcode: str = "") -> Optional[Dict[str, Any]]:
    """乡镇中心点（WGS84，gd-poi-ingest 从高德乡镇级地名回填）；adcode 可选消歧。"""
    if not yearbook_available() or not name:
        return None
    conn = _ro_conn()
    try:
        if adcode:
            r = conn.execute(
                "SELECT name, adcode, lng, lat, province, city, county FROM township_centers "
                "WHERE name = ? AND adcode = ? LIMIT 1",
                (name, str(adcode).zfill(6)),
            ).fetchone()
        else:
            r = conn.execute(
                "SELECT name, adcode, lng, lat, province, city, county FROM township_centers "
                "WHERE name = ? LIMIT 1",
                (name,),
            ).fetchone()
        return dict(zip(_CENTER_COLS, r)) if r else None
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
