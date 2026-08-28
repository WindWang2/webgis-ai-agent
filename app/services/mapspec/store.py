"""MapSpec Storage Module (app/services/mapspec/store.py).

负责 MapSpec JSON 文件的持久化、版本 Revision 生成，
以及 Redis map_state 的底层缓存同步。

可靠性契约（REL-03 / REL-04）：
- 磁盘写入原子（temp + os.replace），崩溃不会留下半截 mapspec.json。
- 文件 IO 经 asyncio.to_thread 卸载，不阻塞 event loop（大 inline GeoJSON
  不再冻结所有 session 的 I/O）。
- 磁盘与 Redis 双写的顺序：先落盘（durability），再写 Redis（cache）。落盘
  失败绝不写 Redis，避免 cache 持有磁盘没有的 state。
"""
import asyncio
import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from uuid import uuid4

from app.core.config import settings
from app.services.session_data import session_data_manager

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
# #760: 之前硬编码 PROJECT_ROOT/".webgis-agent"，游离于 settings.DATA_DIR 的
# 部署矩阵之外 —— k8s readOnlyRootFilesystem 下首个 MapSpec mutation/raster
# save 即 OSError(EROFS)；compose 可写但落在 per-container 层，容器重启丢
# mapspec/revision/checkpoint/raster 而 Redis 缓存仍在，破坏 "cache 不持有
# 磁盘没有的 state" 顺序契约（#519 只修了 DATA_DIR 这一族路径，本路径绕过
# 了它，deploy-matrix 契约测试也因此看不见）。归入 DATA_DIR（k8s/compose
# 均已挂共享卷），MAPSPEC_STORAGE_DIR 保留为显式覆盖口。
BASE_STORAGE_DIR = (
    Path(os.environ.get("MAPSPEC_STORAGE_DIR") or Path(settings.DATA_DIR)).resolve()
    / ".webgis-agent"
)

LABEL_LAYER_SUFFIX = "-label"

# Revision 保留上限：每次 save 都会生成一份完整快照 mapspec_rev_<ms>.json。
# 无上限时磁盘随会话生命周期无界增长（审计 Phase 8 发现）。裁剪到最近 N 份。
MAPSPEC_REV_RETENTION = 20
# revision 文件名的进程内单调序列（与毫秒时间戳拼接，免同毫秒碰撞）
_REV_SEQ = 0


def _should_remove_layer(layer: Dict[str, Any], target_layer_id: str) -> bool:
    """判断图层是否为目标图层或其伴随标签/派生子图层"""
    lid = str(layer.get("id") or "")
    if not lid:
        return False
    return (
        lid == target_layer_id
        or lid == f"{target_layer_id}{LABEL_LAYER_SUFFIX}"
        or lid.startswith(f"{target_layer_id}__")
        or lid.startswith(f"{target_layer_id}-")
    )


def view_has_center(mapspec: Dict[str, Any]) -> bool:
    """Predicate: MapSpec 是否显式设置了视点 Center 坐标"""
    view = mapspec.get("view") or {}
    center = view.get("center", None)
    return "center" in view and center is not None


def _atomic_write_json_sync(path: Path, payload: Any) -> None:
    """同步原子写 JSON：写到同目录临时文件再 os.replace。

    POSIX 下 os.replace 是原子的；崩溃要么看到旧文件、要么看到新文件，
    不会出现半截写入。临时文件与目标同目录保证 replace 不跨文件系统。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, indent=2)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp_name, str(path))
    except BaseException:
        # 清理未替换的临时文件，避免遗留垃圾
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _read_json_sync(path: Path) -> Optional[Any]:
    """同步读 JSON；文件不存在或损坏返回 None（不抛）。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.warning(f"[mapspec] read failed for {path}: {e}")
        return None


def _atomic_write_text_sync(path: Path, data: str) -> None:
    """同步原子写已序列化文本（#687：sidecar 等小目标用）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp_name, str(path))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _read_text_sync(path: Path) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.warning(f"[mapspec] read failed for {path}: {e}")
        return None


def _fingerprint_sync(mapspec: Dict[str, Any]) -> str:
    """#687：spec 的 canonical-JSON SHA256 指纹（提交面 no-op 快比较用）。

    单次 O(spec bytes)；调用方必须在线程内执行（大 spec 的 dumps 在
    事件循环上即为本票要消灭的停顿源）。
    """
    payload = json.dumps(mapspec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# #687：no-op 快比较的磁盘 sidecar（内容为 _fingerprint_sync 的输出）。
_FP_SIDECAR_NAME = "mapspec.json.fp"

# #1073(F-9)：mutation revision 的磁盘 sidecar —— Redis 状态过期而磁盘 spec
# 存活时随 get_mapspec 的复活路径一并恢复 CAS 令牌。
_REV_SIDECAR_NAME = "mapspec.json.rev"


class MapSpecStore:
    """MapSpec JSON 文件持久化与 Revision 管理服务"""

    def __init__(self) -> None:
        # #687：session → 最近一次成功落盘（disk+redis 均已写）的 spec 指纹。
        # 命中即 no-op 短路，O(1)；进程重启后经 sidecar 文件 + Redis 定向
        # 字段恢复同等语义（两者均为 O(1) 读）。
        self._persisted_fp: Dict[str, str] = {}
        # 对象同一性快路径：CoW 架构下重复保存同一 spec 常复用同一对象
        # （lifecycle 持引用不变时）。`is` 比较 O(1)；等值不同对象才走
        # 指纹路径（指纹计算虽在线程，但 json.dumps 不释放 GIL，大 spec
        # 仍会造成循环停顿——同一性短路消除了这个最常见的重复保存场景）。
        self._persisted_obj: Dict[str, Dict[str, Any]] = {}

    def _session_dir_path(self, session_id: str) -> Path:
        """会话目录路径（只算路径，不创建目录）。"""
        base = BASE_STORAGE_DIR.resolve()
        session_dir = (base / session_id).resolve()
        if session_dir.parent != base:
            raise ValueError("invalid session id for MapSpec storage")
        return session_dir

    def get_session_dir(self, session_id: str) -> Path:
        session_dir = self._session_dir_path(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        return session_dir

    def _invalidate_process_cache(self, session_id: str) -> None:
        """audit #838: 进程级 no-op 缓存失效 —— clear/discard 后必须弹出，
        否则 (a) 最后一个完整 spec 对象按会话滞留到进程退出（内存单调增长，
        Redis/磁盘/会话层都有回收，唯独这层没有）；(b) 同 session_id 复用时
        进程内指纹短路在 sidecar/Redis 交叉核对之前触发，静默跳过落盘。"""
        self._persisted_fp.pop(session_id, None)
        self._persisted_obj.pop(session_id, None)

    async def clear_session_files(self, session_id: str) -> None:
        """Purge durable MapSpec/checkpoint/revision state for one session.

        #470：目录缺失时静默返回（幂等）—— 清除是"尽力回收"，不是前置条件；
        也不得像旧实现那样先 mkdir 再 rmtree（读路径才需要 mkdir 语义）。
        """
        session_dir = self._session_dir_path(session_id)
        self._invalidate_process_cache(session_id)

        def _rmtree() -> None:
            try:
                shutil.rmtree(session_dir)
            except FileNotFoundError:
                pass

        await asyncio.to_thread(_rmtree)

    async def discard_mapspec(self, session_id: str) -> None:
        """Remove a first-mutation MapSpec that must not survive rollback.

        Used when the session had no prior spec: saving then failing would
        otherwise leave the half-committed candidate as last-known-good.
        This is not a session-delete tombstone — later mutations may create a
        fresh spec.
        """
        mapspec_path = self.get_session_dir(session_id) / "mapspec.json"

        def _unlink() -> None:
            mapspec_path.unlink(missing_ok=True)

        await asyncio.to_thread(_unlink)
        self._invalidate_process_cache(session_id)  # audit #838
        await session_data_manager.set_map_state(session_id, "mapspec", None)
        await session_data_manager.set_map_state(
            session_id, "_cartographic_mutation_revision", 0
        )

    async def get_mapspec(
        self,
        session_id: str,
        state_hint: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """读取 MapSpec。

        #1082(F-10): ``state_hint`` 允许调用方传入锁内已读的 map_state
        （apply_mutation 此前 pre_state 与 get_mapspec 各做一次全量
        get_map_state，mapspec 字段被完整 JSON 解析两遍）。hint 含
        ``mapspec`` 键即直接采用（调用方已完成 deleted 检查）；缺省或
        hint 无键时行为不变（含磁盘兜底 + revision 复活）。
        """
        if state_hint is not None and isinstance(
            state_hint.get("mapspec"), dict
        ):
            return state_hint["mapspec"]
        map_state = (
            state_hint
            if state_hint is not None
            else await session_data_manager.get_map_state(session_id)
        )
        if map_state.get("_cartographic_deleted") is True:
            return None
        if "mapspec" in map_state:
            return map_state["mapspec"]

        mapspec_file = self.get_session_dir(session_id) / "mapspec.json"
        # 文件读卸载到线程，避免大文件阻塞 event loop。
        mapspec = await asyncio.to_thread(_read_json_sync, mapspec_file)
        if mapspec is not None:
            # #1073(F-9): Redis 状态过期而磁盘 spec 存活时，CAS 令牌必须随
            # spec 一起复活 —— 否则下一次 mutation 以 prior=0 写 revision=1
            # 给世代 N 的 spec（单调性 N→0→1 破坏，重放的 expected_revision=0
            # 开世 mutation 会通过针对新 spec 的 CAS）。
            rev_sidecar = await asyncio.to_thread(
                _read_text_sync, mapspec_file.parent / _REV_SIDECAR_NAME
            )
            try:
                disk_rev = int(rev_sidecar) if rev_sidecar else None
            except (TypeError, ValueError):
                disk_rev = None
            stored_rev = map_state.get("_cartographic_mutation_revision")
            restore_fields = {"mapspec": mapspec}
            if disk_rev is not None and int(stored_rev or 0) < disk_rev:
                restore_fields["_cartographic_mutation_revision"] = disk_rev
            await session_data_manager.set_map_state_fields(session_id, restore_fields)
            if state_hint is not None:
                # v2(audit F1): 复活结果同步回写 hint —— 调用方（engine）在
                # hint 上捕获 CAS 令牌；不回写则 prior 停留在复活前的 stale 0，
                # commit 会以 0+1 覆盖世代 N（单调性破坏 + 重放 CAS 通过）。
                state_hint.update(restore_fields)
            return mapspec

        return None

    async def save_mapspec(
        self,
        session_id: str,
        mapspec: Dict[str, Any],
        mutation_revision: Optional[int] = None,
        layer_op: Optional[Tuple[str, str, Optional[Any]]] = None,
    ) -> Dict[str, Any]:
        """持久化 MapSpec；可选携带 mutation_revision 一并原子落地。

        #1073: spec 与其 CAS 令牌此前是两笔独立 Redis 事务（engine 里
        save_mapspec → set_map_state(revision)），crash 落在两写之间破坏
        相等 CAS 的前提。携带 revision 时经 set_map_state_fields 单 MULTI
        提交，并落 _REV_SIDECAR_NAME 供磁盘复活路径恢复令牌。返回
        ``revision_persisted`` 标记是否已随本调用落地（后端缺接口时调用方
        自行补写）。

        v2(audit F4): ``layer_op``（("upsert"|"remove"|"replace", id, payload)）
        把 runtime layers 的 read-modify-write 并入同一提交事务
        （commit_mapspec_state），消灭 spec=世代 N+1 而 layers=世代 N 的
        crash 窗口；返回 ``layers_persisted``，调用方据此跳过旧的第二笔
        layers 写。携带 layer_op 时跳过幂等短路（layers 字段与 spec 内容
        是两个表示，spec 等值不蕴含 layers 已写）。
        """
        session_dir = self.get_session_dir(session_id)
        rev_dir = session_dir / "revisions"
        mapspec_path = session_dir / "mapspec.json"

        # No-op 保护（#687 重构）：对象同一性 + 指纹快比较替代「全量读盘 +
        # 双深比较」。旧路径把两次 O(spec bytes) 的全树 == 比较跑在事件循环
        # 上，并对 Redis 走 get_map_state 全字段冷解析——12MB spec 的幂等保
        # 存即百毫秒级循环停顿。现在：同一对象 O(1) 短路；等值不同对象经指
        # 纹（线程内单次 O(bytes)）；冷启动/跨进程经 sidecar 文件 + Redis
        # 定向字段（均 O(1) 读）恢复语义。
        # v2(audit F4): 携带 layer_op 时永不短路 —— layers 写必须发生。
        if layer_op is None and mapspec is self._persisted_obj.get(session_id):
            # #1074(F-8): 同一性短路此前完全跳过存活复检（#838 只修了指纹
            # 路径）—— 他 worker 的 clear_session（空闲逐出，无 tombstone）
            # 后本进程同对象再保存会静默不持久化。sidecar + Redis 指纹两个
            # O(1) 读复检；缺失即失效进程缓存走全量落盘。
            sidecar_alive = await asyncio.to_thread(
                _read_text_sync, session_dir / _FP_SIDECAR_NAME
            )
            _get_fp8 = getattr(session_data_manager, "get_map_spec_fingerprint", None)
            redis_fp8 = await _get_fp8(session_id) if _get_fp8 is not None else None
            if sidecar_alive and redis_fp8 is not None:
                return {"mapspec": mapspec}
            self._invalidate_process_cache(session_id)
        fp = await asyncio.to_thread(_fingerprint_sync, mapspec)
        if layer_op is None and self._persisted_fp.get(session_id) == fp:
            # audit #838: 进程内指纹命中不再无条件短路 —— sidecar 仍在且指纹
            # 一致才算数。会话在别处被清除/盘上目录被回收后，同 id 复用的等值
            # spec 会在此走到全量落盘，而不是静默跳过（磁盘/Redis 双缺失）。
            sidecar_alive = await asyncio.to_thread(
                _read_text_sync, mapspec_path.parent / _FP_SIDECAR_NAME
            )
            if sidecar_alive == fp:
                return {"mapspec": mapspec}
            # sidecar 缺失/不一致：清除进程内陈旧指纹，走全量落盘
            self._invalidate_process_cache(session_id)
        sidecar_fp = await asyncio.to_thread(
            _read_text_sync, mapspec_path.parent / _FP_SIDECAR_NAME
        )
        # 鸭子类型守卫：内存后端/测试替身可能未实现定向指纹接口——视为
        # miss（退回全量落盘），不因缺方法而崩。
        _get_fp = getattr(session_data_manager, "get_map_spec_fingerprint", None)
        redis_fp = await _get_fp(session_id) if _get_fp is not None else None
        if layer_op is None and sidecar_fp == fp and redis_fp == fp:
            self._persisted_fp[session_id] = fp
            self._persisted_obj[session_id] = mapspec
            return {"mapspec": mapspec}

        # 原子落盘（mapspec.json + revision 快照 + 指纹 sidecar + 裁剪）
        # 整体卸载到线程。#1073: revision sidecar 同步落盘。
        await asyncio.to_thread(
            self._persist_disk_sync, mapspec_path, rev_dir, mapspec, fp,
            mutation_revision,
        )

        # 落盘成功后再写 Redis cache（顺序契约：cache 不持有磁盘没有的 state）。
        # v2(audit F4+F-P4): commit_mapspec_state 把 spec+revision+指纹+layers
        # 的 read-modify-write 并入单 WATCH/MULTI（此前为 spec/令牌一笔 +
        # layers 另一笔 + 指纹第三笔 HSET）。后端缺接口（测试替身）退回旧
        # 序列，由 engine 补写 layers。
        revision_persisted = False
        layers_persisted = False
        fingerprint_persisted = False
        _commit = getattr(session_data_manager, "commit_mapspec_state", None)
        if _commit is not None:
            commit_fields: Dict[str, Any] = {"mapspec": mapspec}
            if mutation_revision is not None:
                commit_fields["_cartographic_mutation_revision"] = int(mutation_revision)
            commit_fields["_mapspec_fp"] = fp
            committed = await _commit(session_id, commit_fields, layer_op=layer_op)
            if committed:
                revision_persisted = mutation_revision is not None
                layers_persisted = layer_op is not None
                fingerprint_persisted = True
            else:
                raise RuntimeError("authoritative MapSpec cache write rejected")
        else:
            _set_fields = getattr(session_data_manager, "set_map_state_fields", None)
            if mutation_revision is not None and _set_fields is not None:
                persisted = await _set_fields(session_id, {
                    "mapspec": mapspec,
                    "_cartographic_mutation_revision": int(mutation_revision),
                })
                revision_persisted = bool(persisted)
            else:
                persisted = await session_data_manager.set_map_state(
                    session_id, "mapspec", mapspec
                )
            if persisted is False:
                raise RuntimeError("authoritative MapSpec cache write rejected")
            _set_fp = getattr(session_data_manager, "set_map_spec_fingerprint", None)
            if _set_fp is not None:
                await _set_fp(session_id, fp)
        self._persisted_fp[session_id] = fp
        self._persisted_obj[session_id] = mapspec
        return {
            "mapspec": mapspec,
            "revision_persisted": revision_persisted,
            "layers_persisted": layers_persisted,
            "fingerprint_persisted": fingerprint_persisted,
        }

    @staticmethod
    def _persist_disk_sync(
        mapspec_path: Path,
        rev_dir: Path,
        mapspec: Dict[str, Any],
        fingerprint: Optional[str] = None,
        mutation_revision: Optional[int] = None,
    ) -> None:
        """同步：原子写 mapspec.json + 写 revision + 裁剪旧 revision。

        #687：走 _atomic_write_json_sync（perf 基准以其为写放大探针，契约
        稳定优先）；fingerprint 非空时顺手落 no-op sidecar（文本写）。
        #1073: mutation_revision 非空时落 _REV_SIDECAR_NAME（磁盘复活路径
        恢复 CAS 令牌的依据）。
        """
        _atomic_write_json_sync(mapspec_path, mapspec)
        if mutation_revision is not None:
            (mapspec_path.parent / _REV_SIDECAR_NAME).write_text(
                str(int(mutation_revision))
            )

        rev_dir.mkdir(parents=True, exist_ok=True)
        # revision 文件名必须免碰撞：毫秒时间戳在快速连续 save（同一毫秒）
        # 下会互相覆盖 —— 快照静默丢失（#687 测试在全量套件负载下偶发红）。
        # 追加进程内单调序列（定宽），字典序 == 时间序，保留裁剪逻辑不变。
        # #761: 序列是进程内的 —— 存储迁到共享卷后（#760），两个 pod 同一
        # 毫秒可铸出同 (ts, seq) 文件名互相覆盖。尾部再拼 uuid 短后缀；排序
        # 仍按 ts+seq 前缀（uuid 只作消歧尾缀，不破坏字典序语义）。
        global _REV_SEQ
        _REV_SEQ += 1
        rev_filename = (
            f"mapspec_rev_{int(time.time() * 1000):013d}_{_REV_SEQ:06d}"
            f"_{uuid4().hex[:6]}.json"
        )
        _atomic_write_json_sync(rev_dir / rev_filename, mapspec)
        if fingerprint:
            _atomic_write_text_sync(
                mapspec_path.parent / _FP_SIDECAR_NAME, fingerprint + "\n"
            )

        # Revision 保留上限：裁剪到最近 MAPSPEC_REV_RETENTION 份
        # （按文件名时间戳排序，删除最旧）。
        try:
            rev_files = sorted(rev_dir.glob("mapspec_rev_*.json"))
            if len(rev_files) > MAPSPEC_REV_RETENTION:
                for stale in rev_files[:-MAPSPEC_REV_RETENTION]:
                    stale.unlink(missing_ok=True)
        except OSError as e:
            logger.warning(f"[mapspec] revision pruning failed: {e}")


mapspec_store_instance = MapSpecStore()


# ── #470：会话磁盘生命周期 ────────────────────────────────────────────────
#
# 会话磁盘状态（mapspec.json + revisions/ + checkpoints/ + raster/）此前
# 唯一的清除入口是 clear_session_files，唯一调用方是 DELETE /sessions/{id}。
# TTL 过期、idle 淘汰、周期清理都只删 Redis/内存 —— 磁盘无限累积。下面的
# 两个函数是给 session 存储层（clear_session / cleanup_idle_sessions）和
# 周期任务（main._periodic_session_cleanup）复用的失败容忍入口。


async def purge_session_disk_state(session_id: str) -> None:
    """尽力清除一个会话的磁盘状态：目录缺失 OK，失败记日志、绝不抛出。

    供 session_data 的 clear_session（内存 + Redis 两个后端）在淘汰/删除
    会话时联动调用 —— 与"Redis 键被删除"同语义：会话没了，盘上状态也回收。
    """
    try:
        await mapspec_store_instance.clear_session_files(session_id)
    except FileNotFoundError:
        pass
    except Exception as e:  # noqa: BLE001
        logger.warning("[mapspec] disk purge failed for session %s: %s", session_id, e)


def _session_storage_entries(base: Path):
    """BASE_STORAGE_DIR 的直接子目录（跳过非目录/隐藏/危险名字）。

    安全边界：只处理名字形如常规会话 id（[A-Za-z0-9._-]+ 且非 . / .. 开头）
    的目录。名字异常的条目不属于会话布局，不猜语义、不碰。
    """
    import re

    valid = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    try:
        entries = list(os.scandir(base))
    except FileNotFoundError:
        return []
    for entry in entries:
        if not entry.is_dir():
            continue
        name = entry.name
        if name.startswith(".") or not valid.match(name):
            continue
        yield Path(entry.path)


def _expired_session_dirs_sync(base: Path, cutoff: float) -> list:
    """同步（#687 线程内）：返回 newest_mtime ≤ cutoff 的会话目录名列表。"""
    out: list = []
    for session_dir in _session_storage_entries(base):
        try:
            if _newest_mtime(session_dir) > cutoff:
                continue
            out.append(session_dir.name)
        except OSError as e:
            logger.warning("[mapspec] session scan failed for %s: %s", session_dir, e)
    return out


def _newest_mtime(session_dir: Path) -> float:
    """目录树内（含目录自身）最新的 mtime —— "最近一次磁盘写入"的时间。"""
    newest = session_dir.stat().st_mtime
    for root, _dirs, files in os.walk(session_dir):
        mtime = os.stat(root).st_mtime
        if mtime > newest:
            newest = mtime
        for f in files:
            try:
                mtime = os.stat(os.path.join(root, f)).st_mtime
            except OSError:
                continue
            if mtime > newest:
                newest = mtime
    return newest


async def sweep_expired_session_files(
    liveness=None,
    max_age_seconds: Optional[int] = None,
) -> list[str]:
    """按 mtime 清扫过期会话目录（TTL 过期的磁盘兜底），返回被清除的会话 id。

    背景：Redis 的 4h SESSION_TTL 是服务端静默过期 —— 键消失没有任何回调，
    clear_session 永远不会被触发，磁盘目录成为孤儿。这里按"最近一次磁盘
    写入"兜底回收：

    - ``max_age_seconds`` 默认 SESSION_TTL + 1h slack（从 session_data_redis
      读，库缺失时用等价字面量）—— 磁盘比 Redis 键还新的会话不会被动到；
    - ``liveness``（可选，``is_session_active`` 协程或函数）：store 里仍有
      状态的会话跳过 —— 会话可能只在聊天（续 Redis TTL）而多日无制图写盘，
      仅凭 mtime 清它的盘会丢 mapspec。liveness 抛错按"仍活跃"处理（宁可不
      回收也不误删）。
    """
    import inspect

    if max_age_seconds is None:
        try:
            from app.services.session_data_redis import SESSION_TTL

            ttl = SESSION_TTL
        except Exception:  # noqa: BLE001 — redis 库缺失时用等价默认
            ttl = 4 * 60 * 60
        max_age_seconds = ttl + 3600

    async def _is_live(sid: str) -> bool:
        if liveness is None:
            return False
        try:
            result = liveness(sid)
            if inspect.isawaitable(result):
                result = await result
            return bool(result)
        except Exception as e:  # noqa: BLE001
            logger.warning("[mapspec] liveness check failed for %s (%s) — skipping sweep of it", sid, e)
            return True

    cutoff = time.time() - max_age_seconds
    purged: list[str] = []
    base = BASE_STORAGE_DIR.resolve()
    # #687：walk/stat 扫描整块卸载到线程（本机 .webgis-agent/ 有数千会话目录，
    # 旧实现把数万次 os.stat 直接跑在事件循环上，每 600s 冻结一次全进程；
    # 同模块其余磁盘操作均已 to_thread，此处是唯一遗漏）。
    expired_candidates = await asyncio.to_thread(
        _expired_session_dirs_sync, base, cutoff
    )
    for session_name in expired_candidates:
        try:
            if await _is_live(session_name):
                continue
            await purge_session_disk_state(session_name)
            purged.append(session_name)
        except Exception as e:  # noqa: BLE001
            logger.warning("[mapspec] session sweep failed for %s: %s", session_name, e)
    if purged:
        logger.info("[mapspec] swept %d expired session dir(s): %s", len(purged), purged)
    return purged
