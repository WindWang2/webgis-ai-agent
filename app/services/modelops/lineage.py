"""ModelLineageStore —— 模型版本 lineage / 指标 / 部署状态（V3 §F）。

设计裁决：registry 记录**保持不可变**（ADR-0124：条目不可变，seq 单调）
——训练指标、评估引用、晋升/退役状态是**持续追加**的时间线，塞进不可变
记录会迫使 schema 不断 bump + 全量重写。故采用 side-car append-only 日志：

- 存储：``{registry_dir}/lineage/<model_id>@<version>.jsonl``（每模型
  版本一个文件；每行一个事件，O_APPEND 小写入）；
- 事件词表（封闭）：``training_metrics`` / ``evaluation`` / ``promotion``
  / ``retirement`` / ``provenance_note``；
- payload：JSON object，大小上限 + secret 键扫描（descriptor 同纪律）；
- 查询：``history``（时间线）、``latest_metrics``（最近指标/评估摘要）、
  ``deployment_state``（由 promotion/retirement 事件推导；缺省 registered）。

跨进程：追加经 ``registry`` 的跨进程锁语义（O_EXCL lockfile）串行；
锁失效的最坏后果 = 交错行（JSONL 按行解析器可容忍），不产生文档损坏。
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.lib.modelops.errors import DescriptorError

LINEAGE_SCHEMA_VERSION = "modelops.lineage/v1"

EVENT_TRAINING_METRICS = "training_metrics"
EVENT_EVALUATION = "evaluation"
EVENT_PROMOTION = "promotion"
EVENT_RETIREMENT = "retirement"
EVENT_PROVENANCE_NOTE = "provenance_note"

EVENT_TYPES = frozenset(
    {
        EVENT_TRAINING_METRICS,
        EVENT_EVALUATION,
        EVENT_PROMOTION,
        EVENT_RETIREMENT,
        EVENT_PROVENANCE_NOTE,
    }
)

_MAX_PAYLOAD_BYTES = 256 * 1024
_MAX_EVENTS_PER_FILE = 10_000

_SECRET_MARKERS = ("password", "secret", "token", "api_key", "apikey", "credential")
_SAFE_RE = re.compile(r"[^A-Za-z0-9_\-.]")


def _safe(component: str) -> str:
    return _SAFE_RE.sub("_", component)[:160]


def _scan_secret_keys(where: str, node: Any, depth: int = 0) -> None:
    if depth > 6 or not isinstance(node, dict):
        return
    for key, value in node.items():
        if isinstance(key, str) and any(m in key.lower() for m in _SECRET_MARKERS):
            raise DescriptorError(
                f"{where}: key {key!r} looks like a secret — pass a reference, "
                "never a secret value"
            )
        _scan_secret_keys(where, value, depth + 1)


class ModelLineageStore:
    """append-only 模型 lineage 日志（每版本一文件）。"""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._lock = threading.Lock()
        self._seqs: Dict[str, int] = {}

    def _file(self, model_id: str, model_version: str) -> Path:
        return self._root / f"{_safe(model_id)}@{_safe(model_version)}.jsonl"

    def append(
        self,
        model_id: str,
        model_version: str,
        *,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
        actor: str = "",
    ) -> Dict[str, Any]:
        """追加一个 lineage 事件（typed 校验；返回规范化事件）。"""
        if event_type not in EVENT_TYPES:
            raise DescriptorError(
                f"unknown lineage event_type {event_type!r} (known: {sorted(EVENT_TYPES)})"
            )
        payload = dict(payload or {})
        raw = json.dumps(payload, ensure_ascii=False)
        if len(raw.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
            raise DescriptorError(
                f"lineage payload exceeds {_MAX_PAYLOAD_BYTES} bytes"
            )
        _scan_secret_keys(f"lineage.{event_type}", payload)
        key = f"{model_id}@{model_version}"
        with self._lock:
            self._root.mkdir(parents=True, exist_ok=True)
            if key not in self._seqs:
                # 重启恢复：首 append 前扫盘取历史 max seq（防跨进程重启
                # 后 seq 归零 → 排序碰撞 / 部署状态推导倒转）。
                max_seq = 0
                path = self._file(model_id, model_version)
                if path.exists():
                    for line in path.read_text(encoding="utf-8").splitlines():
                        try:
                            max_seq = max(max_seq, int(json.loads(line).get("seq", 0)))
                        except (ValueError, TypeError):
                            continue
                self._seqs[key] = max_seq
            seq = self._seqs.get(key, 0) + 1
            event = {
                "schema_version": LINEAGE_SCHEMA_VERSION,
                "seq": seq,
                "ts": time.time(),
                "model_id": model_id,
                "model_version": model_version,
                "event_type": event_type,
                "actor": actor[:128],
                "payload": payload,
            }
            path = self._file(model_id, model_version)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
            self._seqs[key] = seq
            return event

    def load(self, model_id: str, model_version: str) -> List[Dict[str, Any]]:
        """读取一个版本的全部事件（按 seq 升序；坏行跳过并计数上报）。"""
        path = self._file(model_id, model_version)
        if not path.exists():
            return []
        events: List[Dict[str, Any]] = []
        bad = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except ValueError:
                bad += 1
        events.sort(key=lambda e: int(e.get("seq", 0)))
        with self._lock:
            key = f"{model_id}@{model_version}"
            if events:
                self._seqs[key] = max(
                    self._seqs.get(key, 0), int(events[-1].get("seq", 0))
                )
        if bad:  # pragma: no cover — 观测面（交错行容忍）
            import logging

            logging.getLogger(__name__).warning(
                "lineage %s@%s: %d unparsable lines skipped",
                model_id, model_version, bad,
            )
        return events

    def history(self, model_id: str, model_version: Optional[str] = None) -> List[Dict[str, Any]]:
        """时间线（单版本或全部版本；全版本按文件名glob + seq 归并）。"""
        if model_version is not None:
            return self.load(model_id, model_version)
        events: List[Dict[str, Any]] = []
        if self._root.exists():
            prefix = f"{_safe(model_id)}@"
            for path in sorted(self._root.glob(f"{prefix}*.jsonl")):
                version = path.stem.split("@", 1)[1]
                events.extend(self.load(model_id, version))
        events.sort(key=lambda e: (str(e.get("model_version")), int(e.get("seq", 0))))
        return events

    def latest_metrics(self, model_id: str, model_version: str) -> Optional[Dict[str, Any]]:
        """最近一次 training_metrics / evaluation 事件的 payload（无则 None）。"""
        for event in reversed(self.load(model_id, model_version)):
            if event.get("event_type") in (EVENT_TRAINING_METRICS, EVENT_EVALUATION):
                return dict(event.get("payload") or {})
        return None

    def deployment_state(self, model_id: str, model_version: str) -> str:
        """部署状态（promotion/retirement 事件推导；缺省 registered）。"""
        state = "registered"
        for event in self.load(model_id, model_version):
            if event.get("event_type") == EVENT_PROMOTION:
                state = str((event.get("payload") or {}).get("stage") or "production")
            elif event.get("event_type") == EVENT_RETIREMENT:
                state = "retired"
        return state


__all__ = [
    "EVENT_TYPES",
    "ModelLineageStore",
]
