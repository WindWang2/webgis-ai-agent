"""证据链持久化 V6 —— 分段 / 增量读 / 压缩（ADR-0119 决策 D6）。

V5（ADR-0118 D1）交付：flock 跨进程互斥 + 单调 seq + settle 幂等 +
FINAL_VERDICT trim 保护 + pinned 防驱逐。残留缺口（baseline G4）：
- ``read_chains``/``last_seq`` 每次全量 JSONL 解析；
- trim 在锁内全量 read→rewrite（O(n) 写放大）；
- 无增量游标读（评测门/finalizer 重复解析旧记录）；
- 无分段/压缩 —— 长程会话的读取成本随窗口线性；
- 无跨进程汇聚接口。

V6 分段布局（per session，additive —— V5 单文件布局**读取容忍**）::

    <dir>/trace_chains.jsonl          # V4/V5 legacy（存在则只读保留）
    <dir>/trace_v6/manifest.json      # 段清单 + last_seq
    <dir>/trace_v6/seg_<n>.jsonl      # 当前段（append 目标）
    <dir>/trace_v6/seg_<n>.jsonl.gz   # 已滚动段（gzip 压缩）

契约（全部 V5 语义的分段化重述，测试钉死）：
- **单调 seq**：manifest.last_seq 锁内 +1（legacy 缺 seq 历史 → 从 1 起）；
- **幂等**：``(turn_id, total_records)`` 去重窗口覆盖全部段 + legacy；
- **精确有界窗口**：可见记录总数 ≤ ``MAX_RECORDS_PER_SESSION``（64），
  溢出淘汰顺序 = 最旧非保护整段 → 最旧非保护记录（边界段重写）→
  最旧保护整段 → 最旧保护记录 —— 与 V5 逐记录语义对齐；
- **不撕裂读**：所有读写（含 manifest）在 flock 内；manifest 原子替换；
- **增量读**：``read_chains_since(sid, after_seq)`` 跳过 max_seq ≤ 游标
  的整段（评测门/finalizer 只解析新证据）；
- **压缩**：段滚动时 gzip 化（读侧按后缀透明解压）；
- **汇聚接口**：``iter_session_chains`` 有界迭代器（GeoCompute trace
  bridge 消费；只读证据面，绝不是第二 SessionPlan/Workflow 状态源）；
- ``GIS_TRACE_PERSIST=0`` 一键关停；任何失败静默 False —— 记录面绝不
  阻断业务。
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

logger = logging.getLogger(__name__)

MAX_RECORDS_PER_SESSION = 64
_WRITE_LOCK = threading.Lock()

#: V6 分段参数：段大小 × 段数 ≥ 记录窗口（64 = 4×16）。
SEGMENT_SIZE = 16
MAX_SEGMENTS = MAX_RECORDS_PER_SESSION // SEGMENT_SIZE
_MANIFEST = "manifest.json"

try:  # POSIX：跨进程文件锁可用
    import fcntl  # type: ignore

    _HAS_FCNTL = True
except ImportError:  # 非 POSIX：降级进程锁（诚实披露，V4 行为）
    fcntl = None  # type: ignore[assignment]
    _HAS_FCNTL = False

# 永不被 trim 丢弃的记录（证据链关键事件）。
_PROTECTED_STAGE_NAMES = frozenset({"FINAL_VERDICT"})


def _enabled() -> bool:
    return os.getenv("GIS_TRACE_PERSIST", "1") not in ("0", "false", "False")


def _fsync_enabled() -> bool:
    return os.getenv("GIS_TRACE_FSYNC", "0") in ("1", "true", "True")


def _compress_enabled() -> bool:
    return os.getenv("GIS_TRACE_COMPRESS", "1") not in ("0", "false", "False")


def _session_dir(session_id: str) -> Optional[Path]:
    """会话数据目录（与 mapspec store 同一 DATA_DIR 矩阵 —— #760）。"""
    if not session_id or not all(c.isalnum() or c in "-_" for c in session_id):
        return None
    try:
        from app.core.config import settings

        base = Path(os.environ.get("MAPSPEC_STORAGE_DIR") or Path(settings.DATA_DIR))
        d = base.resolve() / ".webgis-agent" / session_id
        d.mkdir(parents=True, exist_ok=True)
        return d
    except Exception:  # noqa: BLE001 — 目录不可得 → 不持久化
        return None


def _chains_path(session_id: str) -> Optional[Path]:
    d = _session_dir(session_id)
    return d / "trace_chains.jsonl" if d is not None else None


def _v6_dir(session_id: str) -> Optional[Path]:
    d = _session_dir(session_id)
    if d is None:
        return None
    v6 = d / "trace_v6"
    try:
        v6.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return v6


def _is_protected_record(rec: Dict[str, Any]) -> bool:
    """FINAL_VERDICT 等关键证据永不 trim 丢弃。"""
    for stage in rec.get("stages") or []:
        if isinstance(stage, dict) and stage.get("stage") in _PROTECTED_STAGE_NAMES:
            return True
    return bool(rec.get("final"))


def _parse_lines_text(text: str) -> List[Tuple[Optional[int], str, Dict[str, Any]]]:
    out: List[Tuple[Optional[int], str, Dict[str, Any]]] = []
    for ln in text.splitlines():
        if not ln.strip():
            continue
        try:
            rec = json.loads(ln)
        except json.JSONDecodeError:
            out.append((None, ln, {}))
            continue
        seq = rec.get("seq")
        out.append((int(seq) if isinstance(seq, int) else None, ln, rec))
    return out


def _parse_lines(path: Path) -> List[Tuple[Optional[int], str, Dict[str, Any]]]:
    """读文件 → [(seq, raw_line, parsed_dict)]；损坏行 seq=None 原样保留。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    return _parse_lines_text(raw)


def _read_segment_text(path: Path) -> str:
    """段文件读取（.gz 透明解压；损坏/缺席 → 空串）。"""
    try:
        if path.name.endswith(".gz"):
            with gzip.open(path, "rt", encoding="utf-8") as f:
                return f.read()
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""
    except Exception:  # noqa: BLE001 — 损坏段按空（读面不抛）
        return ""


class _FileLock:
    """线程锁 + （POSIX）flock 文件锁双层互斥；任何失败不抛出。"""

    def __init__(self, path: Path):
        self._lock_path = path.with_suffix(path.suffix + ".lock")
        self._fd: Any = None

    def __enter__(self) -> "_FileLock":
        if _HAS_FCNTL:
            try:
                self._fd = os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR, 0o644)
                fcntl.flock(self._fd, fcntl.LOCK_EX)
            except OSError:
                if self._fd is not None:
                    os.close(self._fd)
                    self._fd = None
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None


# ---------------------------------------------------------------------------
# manifest（V6 段清单；flock 内原子替换）
# ---------------------------------------------------------------------------

def _empty_manifest() -> Dict[str, Any]:
    return {"version": 1, "last_seq": 0, "segments": []}


def _manifest_path(v6_dir: Path) -> Path:
    return v6_dir / _MANIFEST


def _load_manifest(v6_dir: Path) -> Dict[str, Any]:
    try:
        data = json.loads(_manifest_path(v6_dir).read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("segments"), list):
            data.setdefault("last_seq", 0)
            data.setdefault("version", 1)
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return _empty_manifest()


def _save_manifest(v6_dir: Path, manifest: Dict[str, Any]) -> None:
    path = _manifest_path(v6_dir)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _segment_name(index: int) -> str:
    return f"seg_{index:06d}.jsonl"


def _segment_display(v6_dir: Path, name: str) -> Optional[Path]:
    """段名 → 实际文件（优先压缩版）。"""
    if not name:
        return None
    gz = v6_dir / (name + ".gz")
    if gz.exists():
        return gz
    plain = v6_dir / name
    if plain.exists():
        return plain
    return None


# ---------------------------------------------------------------------------
# 写路径
# ---------------------------------------------------------------------------

def _parse_segment_records(v6_dir: Path, seg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """解析一个段（manifest 记录驱动）；损坏行跳过。"""
    path = _segment_display(v6_dir, str(seg.get("file") or ""))
    if path is None:
        return []
    out: List[Dict[str, Any]] = []
    for _, _, rec in _parse_lines_text(_read_segment_text(path)):
        if rec:
            out.append(rec)
    return out


def _rewrite_segment(
    v6_dir: Path, seg: Dict[str, Any], kept: List[Tuple[Optional[int], str, Dict[str, Any]]],
) -> None:
    """边界段原子重写（含压缩态保持）+ manifest 计数刷新。"""
    target = v6_dir / str(seg.get("file") or "")
    payload = "".join(ln + "\n" for _, ln, _ in kept)
    if target.name.endswith(".gz"):
        tmp = target.with_suffix(".gz.tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            f.write(payload)
    else:
        tmp = target.with_suffix(".jsonl.tmp")
        tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, target)
    seg["count"] = len(kept)
    seg["protected"] = sum(1 for _, _, r in kept if _is_protected_record(r))
    seqs = [s for s, _, _ in kept if s is not None]
    seg["min_seq"] = min(seqs) if seqs else 0


def _drop_oldest_records(
    v6_dir: Path, seg: Dict[str, Any], drop: int, *, force: bool = False,
) -> int:
    """边界段记录级淘汰：丢最旧 ``drop`` 行（非保护 pass 只丢非保护行）。

    返回实际丢弃行数。"""
    path = _segment_display(v6_dir, str(seg.get("file") or ""))
    if path is None:
        return 0
    parsed = [
        (s, ln, rec) for s, ln, rec in _parse_lines_text(_read_segment_text(path))
        if s is not None or ln
    ]
    kept: List[Tuple[Optional[int], str, Dict[str, Any]]] = []
    dropped = 0
    for s, ln, rec in parsed:
        if dropped < drop:
            is_prot = _is_protected_record(rec) if rec else False
            if force or not is_prot:
                dropped += 1
                continue
        kept.append((s, ln, rec))
    if dropped:
        _rewrite_segment(v6_dir, seg, kept)
    return dropped


def _trim_segments(v6_dir: Path, manifest: Dict[str, Any]) -> None:
    """精确窗口淘汰（V5 逐记录语义的分段化执行）。

    溢出时按 V5 pass 顺序：先非保护（最旧优先）、后保护；能整段删则
    整段删（manifest 驱动零解析），跨界段记录级重写（O(段) ≤16 行）。
    窗口**精确** ≤ MAX_RECORDS_PER_SESSION。"""
    segs: List[Dict[str, Any]] = list(manifest.get("segments") or [])
    total = sum(int(s.get("count") or 0) for s in segs)
    overflow = total - MAX_RECORDS_PER_SESSION
    if overflow <= 0:
        return

    def _pop_seg(index: int) -> None:
        """弹段必删文件（审查 R1 回归：孤儿段文件会触发误 heal → manifest
        重复条目 → 窗口失真）。"""
        seg = segs[index]
        p = _segment_display(v6_dir, str(seg.get("file") or ""))
        if p is not None:
            p.unlink(missing_ok=True)
        segs.pop(index)

    for pass_protected in (False, True):
        while overflow > 0 and segs:
            seg = segs[0]
            count = int(seg.get("count") or 0)
            if count == 0:
                # 空段（fresh roll）：绝不弹掉 append 目标（最后一段）
                if len(segs) > 1:
                    _pop_seg(0)
                    continue
                break
            seg_protected = int(seg.get("protected") or 0)
            if not pass_protected and seg_protected >= count:
                break  # 最旧段全保护 → 本 pass 无可丢（转保护 pass）
            droppable_in_seg = count if pass_protected else (count - seg_protected)
            if overflow >= count and seg_protected == 0:
                # 整段淘汰（零解析）；append 目标（最后一段）不可整删
                if len(segs) == 1:
                    break
                _pop_seg(0)
                overflow -= count
                continue
            take = min(droppable_in_seg, overflow)
            dropped = _drop_oldest_records(
                v6_dir, seg, take, force=pass_protected)
            overflow -= dropped
            if dropped == 0:
                break  # 该段无可丢（防御：防死循环）
            if int(seg.get("count") or 0) == 0 and len(segs) > 1:
                _pop_seg(0)
        if overflow <= 0:
            break
    # 空段过滤只针对非 append 目标（最后一段必须保留 —— 后续 append 写它）
    manifest["segments"] = [
        s for s in segs[:-1] if int(s.get("count") or 0) > 0
    ] + segs[-1:]


def _roll_segment(v6_dir: Path, manifest: Dict[str, Any]) -> None:
    """当前段压缩归档 + 开新段（manifest 原子更新）。"""
    segs = list(manifest.get("segments") or [])
    if not segs:
        return
    current = segs[-1]
    plain_name = str(current.get("file") or "")
    plain = v6_dir / plain_name
    if plain_name.endswith(".gz") is False and _compress_enabled() and plain.exists():
        gz_target = v6_dir / (plain_name + ".gz")
        tmp = gz_target.with_suffix(".tmp")
        try:
            with open(plain, "rb") as f_in, gzip.open(tmp, "wb") as f_out:
                f_out.writelines(f_in)
            os.replace(tmp, gz_target)
            plain.unlink(missing_ok=True)
            current["file"] = plain_name + ".gz"
        except OSError:
            logger.debug("[TraceStore] compress roll failed", exc_info=True)
    # 段号单调递增（不复用已淘汰段名）：heal 按名重建时保持时序，
    # 防「新数据进低号段 → 名序 ≠ 时序」的乱序重建。
    max_index = 0
    for s in segs:
        stem = str(s.get("file") or "").replace(".gz", "")
        try:
            max_index = max(max_index, int(stem.split("_")[1].split(".")[0]))
        except (IndexError, ValueError):
            continue
    new_name = _segment_name(max_index + 1)
    (v6_dir / new_name).touch(exist_ok=True)
    segs.append({"file": new_name, "count": 0, "min_seq": 0, "protected": 0,
                 "max_seq": 0})
    manifest["segments"] = segs


def _heal_manifest(v6_dir: Path, manifest: Dict[str, Any],
                   sid: str) -> Dict[str, Any]:
    """manifest 丢失/半写自愈（审查 R1 M5）：段文件在而账目缺失时，扫描
    v6 段（数量有界）重建 entries 与 last_seq —— 防 seq 重复与窗口失真。"""
    segs = list(manifest.get("segments") or [])
    disk_names = sorted(
        p.name for p in v6_dir.glob("seg_*.jsonl*")
        if p.is_file() and not p.name.endswith(".tmp")
    )
    known = {str(s.get("file") or "") for s in segs}
    orphans = [n for n in disk_names
               if n not in known and n.replace(".gz", "") not in known]
    # 同名双形态（plain + .gz）→ gz 是归档真相，plain 是孤儿（重写期
    # 崩溃残留）→ 删除 plain 孤儿，不计入重建
    for n in list(orphans):
        if n.endswith(".jsonl") and (n + ".gz") in disk_names:
            (v6_dir / n).unlink(missing_ok=True)
            orphans.remove(n)
    if not orphans and int(manifest.get("last_seq") or 0) > 0:
        return manifest
    # 全量重扫（段数有界 ≤ 若干；每段 ≤ SEGMENT_SIZE 行）
    rebuilt: List[Dict[str, Any]] = []
    max_seq = 0
    for name in disk_names:
        seg = {"file": name, "count": 0, "min_seq": 0, "protected": 0,
               "max_seq": 0}
        recs = _parse_segment_records(v6_dir, seg)
        seg["count"] = len(recs)
        segs_seqs = [int(r["seq"]) for r in recs
                     if isinstance(r.get("seq"), int)]
        seg["protected"] = sum(1 for r in recs if _is_protected_record(r))
        seg["min_seq"] = min(segs_seqs) if segs_seqs else 0
        seg["max_seq"] = max(segs_seqs) if segs_seqs else 0
        max_seq = max([max_seq] + segs_seqs)
        rebuilt.append(seg)
    if not rebuilt:
        return manifest
    # legacy 最大 seq 吸收
    legacy_path = _chains_path(sid)
    if legacy_path is not None and legacy_path.exists():
        for s, _, _ in _parse_lines(legacy_path):
            if s is not None and s > max_seq:
                max_seq = s
    healed = {"version": 1, "last_seq": max_seq, "segments": rebuilt}
    try:
        _save_manifest(v6_dir, healed)
    except Exception:  # noqa: BLE001 — 自愈写失败按内存态继续
        pass
    return healed


def persist_chain(chain_dict: Dict[str, Any], session_id: str = "") -> bool:
    """append 一条已序列化的链（``chain.as_dict()``）。

    V6：flock 内 manifest 驱动 —— 幂等去重（全段+legacy）、单调 seq
    （manifest.last_seq+1）、段满滚动压缩、精确窗口 trim；全程原子
    （os.replace）。V4/V5 单文件 legacy 存在 → 只读保留 + seq 续写。
    """
    if not _enabled() or not isinstance(chain_dict, dict):
        return False
    sid = str(session_id or chain_dict.get("session_id") or "")
    v6_dir = _v6_dir(sid)
    if v6_dir is None:
        return False
    try:
        with _WRITE_LOCK, _FileLock(v6_dir / "trace_v6.lock"):
            manifest = _load_manifest(v6_dir)
            manifest = _heal_manifest(v6_dir, manifest, sid)
            segs = list(manifest.get("segments") or [])
            if not segs:
                name = _segment_name(1)
                (v6_dir / name).touch(exist_ok=True)
                segs = [{"file": name, "count": 0, "min_seq": 0,
                         "protected": 0, "max_seq": 0}]
                manifest["segments"] = segs

            # 幂等：同 (turn_id, total_records) 已持久化 → 跳过
            # （扫描 ≤MAX_SEGMENTS 段 + legacy；窗口有界故扫描有界）
            dup_turn = str(chain_dict.get("turn_id") or "")
            dup_total = chain_dict.get("total_records")
            if dup_turn and _dup_exists(v6_dir, sid, manifest,
                                        dup_turn, dup_total):
                return True

            # 单调 seq（legacy 缺 seq 历史不伪造 → 从 1 起）
            last_seq_val = int(manifest.get("last_seq") or 0)
            if last_seq_val == 0:
                legacy_last = 0
                legacy_path = _chains_path(sid)
                if legacy_path is not None and legacy_path.exists():
                    for s, _, _ in _parse_lines(legacy_path):
                        if s is not None and s > legacy_last:
                            legacy_last = s
                last_seq_val = legacy_last
            # 半写窗口兜底（append 后 save 前崩溃 → manifest 落后）：以当前
            # 段尾 seq 为准（段 ≤ SEGMENT_SIZE 行，解析 O(段)）
            try:
                cur_path = v6_dir / str(segs[-1].get("file") or "")
                cur_recs = _parse_segment_records(v6_dir, segs[-1]) if (
                    cur_path.exists()) else []
                for r in cur_recs:
                    s = r.get("seq")
                    if isinstance(s, int) and s > last_seq_val:
                        last_seq_val = s
            except Exception:  # noqa: BLE001 — 兜底失败按 manifest 值
                pass
            chain_dict = dict(chain_dict)
            chain_dict["seq"] = last_seq_val + 1

            current = segs[-1]
            path = v6_dir / str(current.get("file") or "")
            # Torn-tail 自愈（V6 chaos 加固）：上次写中断可能留下无换行
            # 的截断尾巴 —— 不补 \n 会把本次合法记录黏连成坏行（双损）。
            try:
                if path.exists() and path.stat().st_size > 0:
                    with path.open("rb") as f:
                        f.seek(-1, os.SEEK_END)
                        if f.read(1) != b"\n":
                            with path.open("a", encoding="utf-8") as f_nl:
                                f_nl.write("\n")
            except OSError:
                pass
            line = json.dumps(chain_dict, ensure_ascii=False, sort_keys=False,
                              default=str)
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
                if _fsync_enabled():
                    f.flush()
                    os.fsync(f.fileno())
            current["count"] = int(current.get("count") or 0) + 1
            if not current.get("min_seq"):
                current["min_seq"] = chain_dict["seq"]
            current["max_seq"] = chain_dict["seq"]
            if _is_protected_record(chain_dict):
                current["protected"] = int(current.get("protected") or 0) + 1
            manifest["last_seq"] = chain_dict["seq"]

            # 段满滚动（压缩归档上一段）
            if int(current["count"]) >= SEGMENT_SIZE:
                _roll_segment(v6_dir, manifest)

            # 精确窗口 trim（整段删除 + 边界重写）
            _trim_segments(v6_dir, manifest)
            _save_manifest(v6_dir, manifest)
        return True
    except Exception:  # noqa: BLE001 — 记录面绝不阻断业务
        logger.debug("[TraceStore] persist_chain failed session=%s", sid,
                     exc_info=True)
        return False


def _dup_exists(v6_dir: Path, sid: str, manifest: Dict[str, Any],
                turn_id: str, total_records: Any) -> bool:
    for seg in manifest.get("segments") or []:
        for rec in _parse_segment_records(v6_dir, seg):
            if (str(rec.get("turn_id") or "") == turn_id
                    and rec.get("total_records") == total_records):
                return True
    legacy_path = _chains_path(sid)
    if legacy_path is not None and legacy_path.exists():
        for _, _, rec in _parse_lines(legacy_path):
            if (str(rec.get("turn_id") or "") == turn_id
                    and rec.get("total_records") == total_records):
                return True
    return False


def persist_turn_chain(turn_id: str, session_id: str = "") -> bool:
    """按 turn_id 取进程内链并持久化（turn 收尾调用）。

    registry LRU 驱逐后仍可从 pinned 区取链（start 即 pin、持久化成功
    即 unpin）—— 高并发下链不被驱逐丢链（V5 语义保持）。"""
    if not _enabled() or not turn_id:
        return False
    try:
        from app.lib.runtime.gis_trace import get_gis_trace_registry

        registry = get_gis_trace_registry()
        chain = registry.get(turn_id)
        if chain is None:
            return False
        payload = chain.as_dict()
        if session_id:
            payload["session_id"] = session_id
        ok = persist_chain(payload, session_id=session_id)
        if ok:
            registry.unpin(turn_id)
        return ok
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# 读路径（manifest 驱动；增量游标；legacy 容忍）
# ---------------------------------------------------------------------------

def read_chains(session_id: str) -> List[Dict[str, Any]]:
    """读取会话的全部持久化链（评测门离线消费；损坏行跳过）。

    V6：legacy（若在，时间在前）+ 各段按序；与 V5 单文件布局逐位兼容。"""
    out: List[Dict[str, Any]] = []
    path = _chains_path(session_id)
    if path is not None and path.exists():
        out.extend(rec for _, _, rec in _parse_lines(path) if rec)
    v6_dir = _v6_dir(session_id)
    if v6_dir is not None:
        # 段解析在锁内完成（审查 R1 M-minor-7）：锁外解析与并发 trim 的
        # 整段删除竞争 → 静默丢段。段 ≤5×16 行，锁内有界。
        with _WRITE_LOCK, _FileLock(v6_dir / "trace_v6.lock"):
            manifest = _load_manifest(v6_dir)
            for seg in list(manifest.get("segments") or []):
                out.extend(_parse_segment_records(v6_dir, seg))
    return out


def read_chains_since(session_id: str, after_seq: int) -> List[Dict[str, Any]]:
    """增量读：仅返回 seq > ``after_seq`` 的记录（旧整段零解析跳过）。

    评测门/finalizer 的游标消费入口 —— 长程会话不必反复解析旧证据。
    legacy（V4 无 seq）仅在 ``after_seq < 1`` 时包含。"""
    out: List[Dict[str, Any]] = []
    if after_seq < 1:
        path = _chains_path(session_id)
        if path is not None and path.exists():
            out.extend(rec for _, _, rec in _parse_lines(path) if rec)
    v6_dir = _v6_dir(session_id)
    if v6_dir is not None:
        with _WRITE_LOCK, _FileLock(v6_dir / "trace_v6.lock"):
            manifest = _load_manifest(v6_dir)
            for seg in list(manifest.get("segments") or []):
                max_seq = int(seg.get("max_seq") or 0)
                if max_seq and max_seq <= after_seq:
                    continue  # 整段在游标之前
                for rec in _parse_segment_records(v6_dir, seg):
                    seq = rec.get("seq")
                    if isinstance(seq, int) and seq > after_seq:
                        out.append(rec)
    return out


def iter_session_chains(
    session_ids: List[str], *, after_seq: int = 0,
) -> Iterator[Tuple[str, Dict[str, Any]]]:
    """跨进程汇聚接口（GeoCompute trace bridge / 离线评测消费）。

    有界迭代器：逐 session 产出 ``(session_id, record)``；只读证据面 ——
    本模块仍不是第二 SessionPlan/Workflow 状态源。"""
    for sid in session_ids:
        recs = read_chains_since(sid, after_seq) if after_seq else read_chains(sid)
        for rec in recs:
            yield sid, rec


def last_seq(session_id: str) -> int:
    """会话当前最大 seq（无记录/文件缺失 = 0）——resume/丢行检测游标。

    V6：manifest O(1)（legacy-only 会话回退全量解析，与 V5 一致）。"""
    v6_dir = _v6_dir(session_id)
    if v6_dir is not None:
        seq = int(_load_manifest(v6_dir).get("last_seq") or 0)
        if seq:
            return seq
    path = _chains_path(session_id)
    if path is None or not path.exists():
        return 0
    max_seq = 0
    for seq, _, _ in _parse_lines(path):
        if seq is not None and seq > max_seq:
            max_seq = seq
    return max_seq


__all__ = [
    "persist_chain", "persist_turn_chain", "read_chains", "read_chains_since",
    "iter_session_chains", "last_seq", "MAX_RECORDS_PER_SESSION",
    "SEGMENT_SIZE", "MAX_SEGMENTS", "_HAS_FCNTL",
]
