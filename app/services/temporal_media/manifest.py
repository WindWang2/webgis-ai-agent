"""WP08 — 媒体 manifest、PNG 帧证据包与原子发布。

manifest 是三种身份的唯一可解释映射表：观测桶 → 显示段 → 数字帧 → 编码帧。
只有核验通过并完成原子发布后才登记成果与授权下载；失败媒体不放在
completed 链接下。
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any, Dict, List

from .contracts import MAX_MANIFEST_CHARS, SCHEMA_VERSION, TemporalMediaError
from .freeze import sha256_bytes
from .store import new_artifact_name, owner_key, publish_bytes


def build_manifest(plan: Dict[str, Any], encoded: List[Dict[str, Any]],
                   media: Dict[str, Any], verification: Dict[str, Any],
                   digital_by_segment: List[Dict[str, Any]]) -> Dict[str, Any]:
    unique: Dict[str, Dict[str, Any]] = {}
    for evidence in digital_by_segment:
        unique.setdefault(evidence["digital_frame_id"], evidence)
    segments = plan["display"]["segments"]
    return {
        "schema_version": "temporal_media_manifest.v1",
        "plan_schema_version": SCHEMA_VERSION,
        "plan_id": plan["plan_id"],
        "plan_hash": plan["plan_hash"],
        "owner_key": owner_key(plan["owner"]),
        "session_id": plan["session_id"],
        "mapspec_revision": plan["mapspec_revision"],
        "source": {
            "frozen_fingerprints": plan["frozen_fingerprints"],
            "temporal_contract": plan["temporal_contract"],
            "source_ref": plan["frozen_fingerprints"]["source_ref"],
            "content_revision": plan["frozen_fingerprints"]["content_revision"],
            "user_pinned_at_plan_time": plan["temporal_contract"]["user_pinned"],
        },
        "fixed": {
            "viewport": plan["frozen"]["viewport"],
            "style_fingerprint": plan["frozen_fingerprints"]["style"],
            "legend_fingerprint": plan["frozen_fingerprints"]["style"],
            "classification": "frozen as committed; no per-frame fit/reclassification",
            "font_version": plan["frozen_fingerprints"]["font"],
            "renderer": plan["frozen_fingerprints"]["renderer"],
        },
        "identity_model": {
            "observation": "real temporal bucket from temporal_view.v1",
            "digital_frame": "real offscreen PNG per display window",
            "encoded_frame": "container frame; identical pictures may be merged",
            "note": "one observation may dwell across several encoded frames "
                    "without increasing the observation count",
        },
        "observations": plan["observations"],
        "missing_periods": plan["gaps"],
        "selection": plan["selection"],
        "timing": plan["display"]["timing"],
        "order": plan["display"]["order"],
        "direction": plan["display"]["direction"],
        "missing_display": plan["display"]["missing_display"],
        "time_display": plan["display"]["time_display"],
        "segments": [
            {
                "index": s["index"], "kind": s["kind"],
                "observation_id": s["observation_id"],
                "observation_label": s["observation_label"],
                "observation_window": {"start": s["observation_start"],
                                       "end": s["observation_end"]},
                "identity": s["identity"],
                "missing_period": s["missing_period"],
                "held_from": s["held_from"],
                "time_label": s["time_label"],
                "display_duration_ms": s["duration_ms"],
                "encoded_repeat": s["encoded_repeat"],
                "encoded_durations_ms": s["encoded_durations_ms"],
                "digital_frame_id": digital_by_segment[int(s["index"])][
                    "digital_frame_id"],
            }
            for s in segments],
        "digital_frames": [
            {
                "digital_frame_id": e["digital_frame_id"],
                "segment_indices": [i for i, x in enumerate(digital_by_segment)
                                    if x["digital_frame_id"] == e["digital_frame_id"]],
                "content_window": e["content_window"],
                "display_window": e["display_window"],
                "time_label": e["time_label"],
                "identity": e["identity"],
                "layers": e["layers"],
                "feature_count": e["feature_count"],
                "svg_sha256": e["svg_sha256"],
                "png_sha256": e["png_sha256"],
                "png_bytes": e["png_bytes"],
                "size": e["size"],
                "renderer": e["renderer"],
                "diagnostics": e["diagnostics"],
            }
            for e in unique.values()],
        "encoded_frames": [
            {
                "index": i,
                "digital_frame_id": e["digital_frame_id"],
                "duration_ms": e["duration_ms"],
                "segment_indices": e["segment_indices"],
                "observation_ids": e["observation_ids"],
                "identities": e["identities"],
                "merged": bool(e.get("merged")),
                "segment_durations_ms": e.get("segment_durations_ms") or {},
                "equivalent_digital_frames": e.get("equivalent_digital_frames") or [],
            }
            for i, e in enumerate(encoded)],
        "container": {
            "format": plan["format"],
            "media_type": media["media_type"],
            "codec": media["codec"],
            "container": media["container"],
            "bytes": media["bytes"],
            "checksum": media["checksum"],
            "size": [int(plan["canvas"]["width"]), int(plan["canvas"]["height"])],
            "total_duration_ms": media["declared_total_duration_ms"],
            "encoded_frame_count": media["encoded_frame_count"],
            "loop": media["loop"],
            "quantization": media["quantization"],
        },
        "partial": plan["partial"],
        "omitted_layers": plan["partial"]["omitted_layers"],
        "disclosures": plan["disclosures"],
        "resource_estimate": plan["resource_estimate"],
        "verification": {
            "passed": verification["passed"],
            "decoded_frames": verification["decoded_frames"],
            "total_duration_ms": verification["total_duration_ms"],
            "failures": verification["failures"],
            "checks_count": len(verification["checks"]),
        },
    }


def package_evidence(manifest: Dict[str, Any], frames_dir: Path,
                     svgs_dir: Path) -> bytes:
    """真实 PNG 数字帧 + SVG + manifest 的 ZIP（可独立回读）。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED,
                        compresslevel=6) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                       indent=2, default=str))
        archive.writestr(
            "README.txt",
            "G35 temporal media evidence\n"
            "frame_*.png = independently decodable digital frames\n"
            "frame_*.svg = compiled vector source with baked time annotation\n"
            "manifest.json = observation/display/digital/encoded identity mapping\n")
        for frame in manifest["digital_frames"]:
            key = frame["digital_frame_id"]
            for suffix, root in ((".png", frames_dir), (".svg", svgs_dir)):
                path = root / f"{key}{suffix}"
                if path.is_file():
                    archive.write(path, f"frame_{key}{suffix}")
    data = buffer.getvalue()
    return data


def publish_manifest(plan: Dict[str, Any], principal: str, job_id: str,
                     manifest: Dict[str, Any], media: Dict[str, Any],
                     evidence_zip: bytes) -> Dict[str, Any]:
    """核验通过后才调用：原子写 + .owner + 签名 URL（不暴露路径/命令）。"""
    animation = new_artifact_name(job_id, "animation", media["extension"])
    evidence = new_artifact_name(job_id, "frames", ".zip")
    manifest_name = new_artifact_name(job_id, "manifest", ".json")
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                                indent=2, default=str).encode("utf-8")
    if len(manifest_bytes) > MAX_MANIFEST_CHARS:
        raise TemporalMediaError("MANIFEST_TOO_LARGE",
                                 "manifest exceeds bounded JSON budget; "
                                 "narrow the plan before publishing",
                                 status_code=413)
    with open(media["path"], "rb") as handle:
        animation_bytes = handle.read()
    if "sha256:" + sha256_bytes(animation_bytes) != media["checksum"]:
        raise TemporalMediaError("CONTAINER_CHANGED_BEFORE_PUBLISH",
                                 "media checksum changed before publish; refused",
                                 status_code=409)
    return {
        "animation": {
            "filename": animation, "bytes": len(animation_bytes),
            "media_type": media["media_type"],
            "checksum": media["checksum"],
            "url": publish_bytes(animation, animation_bytes,
                                 media["extension"], principal),
        },
        "evidence": {
            "filename": evidence, "bytes": len(evidence_zip),
            "media_type": "application/zip",
            "checksum": "sha256:" + sha256_bytes(evidence_zip),
            "url": publish_bytes(evidence, evidence_zip, ".zip", principal),
        },
        "manifest": {
            "filename": manifest_name, "bytes": len(manifest_bytes),
            "media_type": "application/json",
            "checksum": "sha256:" + sha256_bytes(manifest_bytes),
            "url": publish_bytes(manifest_name, manifest_bytes, ".json", principal),
        },
    }


__all__ = ["build_manifest", "package_evidence", "publish_manifest"]
