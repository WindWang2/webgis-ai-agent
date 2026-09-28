"""Transformation Provenance —— 语义谱系 DAG（H08 / ADR-0215 延伸）。

回答的问句：「这张地图的这层，是从哪个源数据、经什么算法/参数来的？」

- **记录面**：``TransformationRecord``（有界）—— output_ref +
  output_descriptor_fingerprint + inputs ≤8 {ref, descriptor_fingerprint} +
  algorithm/version + parameters_digest（canonical sha256，参数本体不入
  记录 —— 防 payload 膨胀，digest 足以对账「同参数吗」）；
- **铸造面**：``mint_output_descriptor`` —— 从 session ref descriptor
  元数据零扫描投影输出 descriptor（绝不读 FeatureCollection），入语义
  store（内容寻址，与 ingest/mapspec 同一身份体系）；
- **谱系面**：``lineage_for_ref`` —— 沿记录 + artifact_registry inputs 边
  反向走链（有界深度/宽度），MapSpec layer → analysis artifact →
  source dataset 可追查；记录缺席的段诚实截断（绝不虚构祖先）。

存储：session 语义目录单文件 ``_provenance.jsonl``（append-only，
≤MAX_RECORDS=128 条，超限丢最旧 —— 有界磁盘）。文件 IO 全部
``asyncio.to_thread``（与 store 同纪律）。additive evidence：任何失败
只 log，绝不阻断分析主链。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

#: 单条转换记录的输入上限（与 descriptor MAX_SOURCE_REFS 同量级）。
MAX_TRANSFORMATION_INPUTS = 8
#: 每 session 转换记录上限（有界磁盘；超限丢最旧）。
MAX_RECORDS = 128
#: 谱系走链深度/宽度上限（有界输出）。
MAX_LINEAGE_DEPTH = 4
MAX_LINEAGE_NODES = 16

_PROVENANCE_FILE = "_provenance.jsonl"


def parameters_digest(parameters: Any) -> str:
    """参数 → canonical sha256 前 16 位（确定性；不可序列化按 str 兜底）。"""
    try:
        blob = json.dumps(
            parameters, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), default=str,
        ).encode("utf-8")
    except Exception:  # noqa: BLE001 — 兜底字符串仍确定
        blob = str(parameters).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


@dataclass
class TransformationRecord:
    """一次分析转换的谱系记录（全部有界；不含参数本体/数据载荷）。"""

    output_ref: str
    output_fingerprint: str
    inputs: List[Dict[str, str]] = field(default_factory=list)
    algorithm: str = ""
    algorithm_version: str = ""
    parameters_digest_value: str = ""
    created_at: float = 0.0

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "output_ref": self.output_ref[:200],
            "output_fingerprint": self.output_fingerprint[:96],
            "inputs": [
                {"ref": str(i.get("ref") or "")[:200],
                 "descriptor_fingerprint": str(
                     i.get("descriptor_fingerprint") or "")[:96]}
                for i in self.inputs[:MAX_TRANSFORMATION_INPUTS]
                if isinstance(i, dict) and i.get("ref")
            ],
            "algorithm": self.algorithm[:64],
            "algorithm_version": self.algorithm_version[:32],
            "parameters_digest": self.parameters_digest_value[:16],
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Any) -> Optional["TransformationRecord"]:
        if not isinstance(data, dict) or not data.get("output_ref"):
            return None
        inputs = [
            {"ref": str(i.get("ref") or "")[:200],
             "descriptor_fingerprint": str(i.get("descriptor_fingerprint") or "")[:96]}
            for i in (data.get("inputs") or [])[:MAX_TRANSFORMATION_INPUTS]
            if isinstance(i, dict) and i.get("ref")
        ]
        return cls(
            output_ref=str(data["output_ref"])[:200],
            output_fingerprint=str(data.get("output_fingerprint") or "")[:96],
            inputs=inputs,
            algorithm=str(data.get("algorithm") or "")[:64],
            algorithm_version=str(data.get("algorithm_version") or "")[:32],
            parameters_digest_value=str(data.get("parameters_digest") or "")[:16],
            created_at=float(data.get("created_at") or 0.0),
        )


# ── 存储（session 单文件 append-only；有界）────────────────────────────────


def _provenance_path(session_id: str) -> Path:
    from app.services.dataset_semantics.store import _storage_base

    return (_storage_base() / str(session_id) / "dataset_semantics"
            / _PROVENANCE_FILE)


def _load_sync(path: Path) -> List[TransformationRecord]:
    if not path.exists():
        return []
    out: List[TransformationRecord] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = TransformationRecord.from_dict(json.loads(line))
                except Exception:  # noqa: BLE001 — 坏行跳过（append-only 容忍）
                    continue
                if rec is not None:
                    out.append(rec)
    except Exception:  # noqa: BLE001 — 读失败按空（记录缺席 = 诚实无谱系）
        return []
    return out[-MAX_RECORDS:]


def _append_sync(path: Path, record: TransformationRecord) -> int:
    records = _load_sync(path)
    records.append(record)
    records = records[-MAX_RECORDS:]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r.to_bounded_dict(), ensure_ascii=False,
                               sort_keys=True, separators=(",", ":")) + "\n")
    import os

    os.replace(tmp, path)
    return len(records)


async def record_transformation(
    session_id: str,
    *,
    output_ref: str,
    output_fingerprint: str,
    inputs: Sequence[Dict[str, str]],
    algorithm: str,
    algorithm_version: str = "",
    parameters: Any = None,
) -> bool:
    """追加一条转换记录（有界；失败 log 不抛 —— additive 证据面）。"""
    if not session_id or not output_ref:
        return False
    record = TransformationRecord(
        output_ref=str(output_ref)[:200],
        output_fingerprint=str(output_fingerprint or "")[:96],
        inputs=[dict(i) for i in inputs[:MAX_TRANSFORMATION_INPUTS]
                if isinstance(i, dict) and i.get("ref")],
        algorithm=str(algorithm or "")[:64],
        algorithm_version=str(algorithm_version or "")[:32],
        parameters_digest_value=parameters_digest(parameters),
        created_at=time.time(),
    )
    try:
        await asyncio.to_thread(
            _append_sync, _provenance_path(session_id), record)
        return True
    except Exception:  # noqa: BLE001 — 证据面不阻断分析链
        logger.warning("[provenance] record_transformation skipped",
                       exc_info=True)
        return False


async def list_transformations(session_id: str) -> List[TransformationRecord]:
    """session 全部转换记录（≤MAX_RECORDS；读失败按空）。"""
    try:
        return await asyncio.to_thread(
            _load_sync, _provenance_path(session_id))
    except Exception:  # noqa: BLE001
        return []


# ── 铸造（分析产物语义身份；零扫描）────────────────────────────────────


async def mint_output_descriptor(
    session_id: str,
    ref_id: str,
    *,
    producer: str = "",
    method: str = "",
) -> Optional[Any]:
    """session ref descriptor 元数据 → 输出 descriptor 入 store（零扫描）。

    ref descriptor 缺席 / 投影失败 / put 失败 → None（诚实缺席；调用方
    按 DESCRIPTOR_MISSING 披露，分析主链不阻断）。
    """
    if not session_id or not ref_id:
        return None
    try:
        from app.services.dataset_semantics import (
            build_descriptor_from_ref_descriptor,
            get_dataset_semantic_store,
        )
        from app.services.session_data import session_data_manager

        ref_descriptor = await session_data_manager.get_ref_descriptor(
            session_id, ref_id)
        if not isinstance(ref_descriptor, dict):
            return None
        descriptor = build_descriptor_from_ref_descriptor(
            ref_descriptor,
            dataset_key=str(ref_id)[:200],
            provenance=[{"producer": str(producer or "analysis")[:64],
                         "method": str(method or "")[:64]}],
        )
        put = await get_dataset_semantic_store().put(
            session_id, str(ref_id)[:200], descriptor)
        return descriptor if put.ok else None
    except Exception:  # noqa: BLE001 — additive evidence
        logger.warning("[provenance] mint_output_descriptor skipped",
                       exc_info=True)
        return None


async def resolve_input_fingerprints(
    session_id: str,
    refs: Sequence[str],
) -> List[Dict[str, str]]:
    """输入 refs → [{ref, descriptor_fingerprint}]（≤8；缺席诚实空串）。"""
    from app.services.dataset_semantics import get_dataset_semantic_store

    store = get_dataset_semantic_store()
    out: List[Dict[str, str]] = []
    for ref in list(dict.fromkeys(str(r) for r in refs if r))[:MAX_TRANSFORMATION_INPUTS]:
        fp = ""
        try:
            rec = await store.get(session_id, ref)
            if rec.ok and rec.descriptor is not None:
                fp = rec.descriptor.descriptor_fingerprint
        except Exception:  # noqa: BLE001 — 单输入失败诚实缺席
            fp = ""
        out.append({"ref": ref[:200],
                    "descriptor_fingerprint": str(fp or "")[:96]})
    return out


# ── 谱系走链（MapSpec layer → artifact → source）─────────────────────────


async def lineage_for_ref(
    session_id: str,
    ref: str,
    *,
    max_depth: int = MAX_LINEAGE_DEPTH,
) -> List[Dict[str, Any]]:
    """ref → 有界祖先链（记录面优先，artifact_registry inputs 边兜底）。

    返回逐层节点 ``[{ref, descriptor_fingerprint?, algorithm?,
    inputs?: [...]}]``（index 0 = ref 本身；记录/台账缺席的段诚实截断）。
    环防护：已访问 ref 不重入（有界图不会死循环）。
    """
    ref = str(ref or "")[:200]
    if not session_id or not ref:
        return []
    records = {r.output_ref: r for r in await list_transformations(session_id)}
    chain: List[Dict[str, Any]] = []
    visited = set()
    frontier: List[str] = [ref]
    depth = 0
    while frontier and depth < max_depth and len(chain) < MAX_LINEAGE_NODES:
        next_frontier: List[str] = []
        for current in frontier[:MAX_LINEAGE_NODES]:
            if current in visited:
                continue
            visited.add(current)
            node: Dict[str, Any] = {"ref": current}
            record = records.get(current)
            if record is not None:
                node["descriptor_fingerprint"] = record.output_fingerprint
                if record.algorithm:
                    node["algorithm"] = record.algorithm
                    node["parameters_digest"] = record.parameters_digest_value
                node["inputs"] = list(record.inputs)
                chain.append(node)
                for i in record.inputs:
                    if i.get("ref"):
                        next_frontier.append(str(i["ref"])[:200])
                continue
            # 记录缺席 → artifact 台账 inputs 边兜底（生产谱系仍有）。
            artifact_inputs = await _artifact_inputs(session_id, current)
            if artifact_inputs:
                chain.append(node)
                next_frontier.extend(artifact_inputs)
            # 双缺席 → 叶子（源数据层），保留节点（链的终端，诚实截断）。
            if record is None and not artifact_inputs:
                node["descriptor_fingerprint"] = await _descriptor_fingerprint(
                    session_id, current)
                chain.append(node)
        frontier = next_frontier
        depth += 1
    return chain[:MAX_LINEAGE_NODES]


async def lineage_provenance_mint(
    session_id: str,
    output_ref: str,
    *,
    inputs: Sequence[Any],
    algorithm: str,
    parameters: Any = None,
    producer: str = "",
    algorithm_version: str = "",
) -> str:
    """分析产物一站式铸造（op 级生产缝）：输出 descriptor 入 store + 转换
    记录落谱系。返回输出指纹（"" = 诚实缺席）。

    ``inputs``：输入节点携带的 ref id（None/缺席项过滤；与 output 相同的
    自环 ref 丢弃 —— 单输入链的 ref 提取面会把产物自身当输入，自环在
    DAG 里无意义；≤8 有界）。
    任何一步失败返回 ""（log 不抛）—— additive 证据面，分析主链零阻断。
    """
    output_ref = str(output_ref)[:200]
    input_refs = [str(r) for r in inputs
                  if r and str(r)[:200] != output_ref]
    descriptor = await mint_output_descriptor(
        session_id, output_ref, producer=producer or "analysis",
        method=str(algorithm or "")[:64])
    if descriptor is None:
        return ""
    input_entries = await resolve_input_fingerprints(session_id, input_refs)
    ok = await record_transformation(
        session_id,
        output_ref=str(output_ref)[:200],
        output_fingerprint=descriptor.descriptor_fingerprint,
        inputs=input_entries,
        algorithm=str(algorithm or "")[:64],
        algorithm_version=algorithm_version,
        parameters=parameters,
    )
    if not ok:
        logger.warning("[provenance] transformation record skipped ref=%s",
                       str(output_ref)[:64])
    return descriptor.descriptor_fingerprint


async def _artifact_inputs(session_id: str, ref: str) -> List[str]:
    try:
        from app.services.artifact_registry import get_artifact

        record = await get_artifact(session_id, ref)
        if record is None:
            return []
        raw = getattr(record, "inputs", None)
        if not isinstance(raw, (list, tuple)):
            return []
        return [str(x)[:200] for x in raw[:MAX_TRANSFORMATION_INPUTS] if x]
    except Exception:  # noqa: BLE001 — 台账缺席按无边
        return []


async def _descriptor_fingerprint(session_id: str, ref: str) -> str:
    try:
        from app.services.dataset_semantics import get_dataset_semantic_store

        rec = await get_dataset_semantic_store().get(session_id, ref)
        if rec.ok and rec.descriptor is not None:
            return rec.descriptor.descriptor_fingerprint
    except Exception:  # noqa: BLE001
        pass
    return ""


__all__ = [
    "MAX_TRANSFORMATION_INPUTS",
    "MAX_RECORDS",
    "MAX_LINEAGE_DEPTH",
    "MAX_LINEAGE_NODES",
    "TransformationRecord",
    "parameters_digest",
    "record_transformation",
    "list_transformations",
    "mint_output_descriptor",
    "lineage_provenance_mint",
    "resolve_input_fingerprints",
    "lineage_for_ref",
]
