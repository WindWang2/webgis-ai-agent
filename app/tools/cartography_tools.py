"""webgis_* Canonical Cartography Tools for MapSpec Harness."""
import hashlib
import json
import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool
from app.services.mapspec_store import mapspec_store
from app.services.session_data import session_data_manager
from app.services.session_data_protocol import is_unavailable_ref

logger = logging.getLogger(__name__)

# HARNESS-V3 / BE-3: adapter 结果中的证据字段，必须透传到工具结果——
# harness MapSpecValidity 阶梯读 is_compiled；被拒绝的 mutation 需要
# message + correction_hint 让 LLM 自愈（此前包装层硬编码 success:True，
# 丢弃全部证据，导致生产证据链断裂）。
_EVIDENCE_KEYS = (
  "is_compiled",
  "warnings",
  "checkpoint_id",
  "correction_hint",
  "message",
  "cartography_findings",
  "cartographic_review",
  "mapspec_fingerprint",
  "runtime_observation_seq",
  "runtime_projection_fingerprint",
  "mutation_revision",
)


def _forward_evidence(res: Dict[str, Any], out: Dict[str, Any]) -> Dict[str, Any]:
  """透传 adapter 证据字段，并从 adapter 结果推导 success（不再硬编码 True）。"""
  out["success"] = bool(res.get("success", False))
  for key in _EVIDENCE_KEYS:
    if key in res and res[key] is not None:
      out[key] = res[key]
  return out


def _descriptor_profile(descriptor: Dict[str, Any]) -> Dict[str, Any]:
  """Project an O(1) ref descriptor into truthful spatial review metadata."""
  return {
      "featureCount": descriptor.get("feature_count"),
      "geometryTypes": list(descriptor.get("geometry_types") or []),
      "bbox": descriptor.get("bbox"),
      "fields": {},
      "fields_status": "unknown",
      # RFC 7946 convention is not proof of the stored dataset's CRS. Unknown
      # remains unknown until the producing tool supplies explicit metadata.
      "crs": None,
      "crs_status": "unknown",
  }


def _fingerprint_metadata(value: Any, prefix: str) -> str:
  payload = json.dumps(
      value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
  ).encode("utf-8")
  return f"{prefix}-sha256:{hashlib.sha256(payload).hexdigest()}"


def _runtime_patch(
    reviewed_layer: Dict[str, Any],
    result_ref: Optional[str],
    mapspec_fingerprint: Optional[str],
    repair_attempts: List[Dict[str, Any]],
) -> Dict[str, Any]:
  paint = reviewed_layer.get("paint") if isinstance(reviewed_layer.get("paint"), dict) else {}
  opacity = next(
    (
      float(paint[key]) for key in (
        "opacity", "fill-opacity", "line-opacity", "circle-opacity", "raster-opacity"
      )
      if isinstance(paint.get(key), (int, float)) and not isinstance(paint.get(key), bool)
    ),
    1.0,
  )
  patch: Dict[str, Any] = {
    "layer_id": reviewed_layer.get("id"),
    "result_ref": result_ref,
    "visible": (
      reviewed_layer.get("visible") is not False
      and (reviewed_layer.get("layout") or {}).get("visibility") != "none"
    ),
    "opacity": opacity,
    "legend_spec": reviewed_layer.get("legend_spec"),
    "mapspec_fingerprint": mapspec_fingerprint,
    "repair_attempts": repair_attempts[:2],
  }
  runtime_style: Dict[str, Any] = {}
  color = next(
    (
      paint[key] for key in ("color", "fill-color", "line-color", "circle-color")
      if isinstance(paint.get(key), str)
    ),
    None,
  )
  if color:
    runtime_style["color"] = color
  for source_key, target_key in (
    ("fill-outline-color", "strokeColor"),
    ("line-width", "strokeWidth"),
    ("circle-radius", "pointSize"),
  ):
    value = paint.get(source_key)
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
      runtime_style[target_key] = value
  if runtime_style:
    patch["style"] = runtime_style
  patch["projection_fingerprint"] = _fingerprint_metadata(
      {
        key: patch.get(key)
        for key in ("layer_id", "result_ref", "visible", "opacity", "legend_spec", "style")
      },
      "runtime",
  )
  return patch


class WebgisProjectInitArgs(BaseModel):
  view: Optional[Dict[str, Any]] = Field(default=None, description="初始视图，如 {'center': [120.1, 30.2], 'zoom': 10}")
  thresholds: Optional[Dict[str, Any]] = Field(default=None, description="运行阈值，如 {'maxFeatures': 50000}")


class WebgisViewSetArgs(BaseModel):
  center: Optional[List[float]] = Field(default=None, description="中心点经纬度 [lng, lat]")
  zoom: Optional[float] = Field(default=None, description="缩放级别 (1~20)")
  pitch: Optional[float] = Field(default=None, description="倾角 (0~85)")
  bearing: Optional[float] = Field(default=None, description="旋转角 (-180~180)")


class WebgisStateGetArgs(BaseModel):
  pass


class WebgisSourceProfileArgs(BaseModel):
  source_id: str = Field(..., description="数据源唯一标识符，如 'earthquakes'")
  geojson_data: Any = Field(..., description="GeoJSON 数据对象、数据引用 ref:xxx 或 URL/文件路径")


class WebgisLayerUpsertArgs(BaseModel):
  layer: Dict[str, Any] = Field(..., description="图层规范对象 (包含 id, source, type, paint, layout, label)")
  source_data: Optional[Any] = Field(default=None, description="可选的 GeoJSON 数据对象或引用 ref:xxx，供自动分析与视图计算")


class WebgisLayerRemoveArgs(BaseModel):
  layer_id: str = Field(..., description="要移除的图层 ID")


class WebgisLayoutSetArgs(BaseModel):
  legend: Optional[Dict[str, Any]] = Field(default=None, description="图例配置，如 {'title': '标题', 'position': 'top-right', 'visible': True}")
  controls: Optional[List[Dict[str, Any]]] = Field(default=None, description="地图控件配置列表")
  margins: Optional[Dict[str, Any]] = Field(default=None, description="边距配置")


class WebgisValidateArgs(BaseModel):
  pass


class WebgisCompileMaplibreArgs(BaseModel):
  pass


class WebgisCheckpointArgs(BaseModel):
  checkpoint_id: Optional[str] = Field(default=None, description="可选的快照名称 ID")


class WebgisRollbackArgs(BaseModel):
  checkpoint_id: str = Field(..., description="要回滚到的快照 ID")


class WebgisRuntimeValidateArgs(BaseModel):
  pass


class WebgisCartographyStatusArgs(BaseModel):
  pass


def register_mapspec_cartography_tools(registry: ToolRegistry) -> None:
  """注册 MapSpec Harness 规范化 webgis_* 工具。"""

  @tool(
      registry,
      name="webgis_project_init",
      description="初始化当前会话的 MapSpec 制图 Intent 文档与基线配置。",
      args_model=WebgisProjectInitArgs,
      tier=1,
  )
  async def webgis_project_init(
      view: Optional[dict] = None,
      thresholds: Optional[dict] = None,
      session_id: Optional[str] = None,
  ) -> dict:
    if not session_id:
      return {"success": False, "message": "Missing session_id"}
    res = await mapspec_store.init_project(session_id, view, thresholds)
    out: Dict[str, Any] = {"mapspec": res.get("mapspec")}
    if res.get("success"):
      out["summary"] = "MapSpec project initialized"
    return _forward_evidence(res, out)

  @tool(
      registry,
      name="webgis_state_get",
      description="读取当前会话的 MapSpec 制图 Intent 文档与 MapMeta Profile。",
      args_model=WebgisStateGetArgs,
      tier=1,
  )
  async def webgis_state_get(session_id: Optional[str] = None) -> dict:
    if not session_id:
      return {"success": False, "message": "Missing session_id"}
    mapspec = await mapspec_store.get_mapspec(session_id)
    if not mapspec:
      res = await mapspec_store.init_project(session_id)
      mapspec = res["mapspec"]
    return {
        "success": True,
        "mapspec": mapspec,
        "summary": "MapSpec state retrieved",
    }

  @tool(
      registry,
      name="webgis_view_set",
      description="设置或覆盖地图视图参数 (center, zoom, pitch, bearing)，并同步重新编译 runtime map_state。",
      args_model=WebgisViewSetArgs,
      tier=1,
  )
  async def webgis_view_set(
      center: Optional[List[float]] = None,
      zoom: Optional[float] = None,
      pitch: Optional[float] = None,
      bearing: Optional[float] = None,
      session_id: Optional[str] = None,
  ) -> dict:
    if not session_id:
      return {"success": False, "message": "Missing session_id"}
    res = await mapspec_store.set_view(session_id, center, zoom, pitch, bearing)
    view = (res.get("mapspec") or {}).get("view", {})
    out: Dict[str, Any] = {"view": view}
    if res.get("success"):
      # Round-2 P2：LLM 可见文本绝不声称相机已移动 —— 工具层只完成了 mapspec.view
      # 写入 + 下发 fly_to 指令，前端是否真正落定相机由 ACK 证据闭环判定
      # （后端 _is_verifiable_ack 从 actual 重算收敛）。此前 "View updated to …"
      # 在没有任何实时 ACK 前就向 LLM 宣称更新完成，属于"假成功"自我表扬。
      out["summary"] = "视图指令已下发，等待前端执行"
      # HARNESS-V3 / BE-3: 此前只写 mapspec.view，实时相机从不动。这里在调用方
      # 确实传了视图参数时，同步 runtime map_state.viewport（无 seq 的服务端真相
      # 写入，同 ws_service 的 viewport 契约），并下发 fly_to 命令让前端相机移动。
      fly_params: Dict[str, Any] = {}
      if center is not None:
        fly_params["center"] = center
      if zoom is not None:
        fly_params["zoom"] = zoom
      if pitch is not None:
        fly_params["pitch"] = pitch
      if bearing is not None:
        fly_params["bearing"] = bearing
      if fly_params:
        current = (await session_data_manager.get_map_state(session_id)).get("viewport") or {}
        merged = {**current, **fly_params}
        await session_data_manager.set_map_state(session_id, "viewport", merged)
        out["command"] = "fly_to"
        # P2 fix: 下发合并后的完整视口（当前视口 ∪ 本次参数）。此前只透传
        # fly_params —— 部分参数（如仅 pitch）会产出 center-less 的 fly_to，
        # 前端按缺 center 判 invalid_params，相机永不移动、viewport 失步。
        out["params"] = merged
    return _forward_evidence(res, out)

  @tool(
      registry,
      tier=2, domains=["dataset"], name="webgis_source_profile",
      description="剖析 GeoJSON 数据源生成 Spatial Meta Profile (BBOX, 建议视图, 字段统计, 数值分布)。",
      args_model=WebgisSourceProfileArgs
  )
  async def webgis_source_profile(
      source_id: str,
      geojson_data: Any,
      session_id: Optional[str] = None,
  ) -> dict:
    if not session_id:
      return {"success": False, "message": "Missing session_id"}
    # The adapter profiles once, stores the body behind a session ref, then
    # commits metadata through the serialized MapSpec lifecycle.
    try:
      profile = await mapspec_store.source_profile(session_id, source_id, geojson_data)
    except Exception as e:
      logger.warning(f"[webgis_source_profile] profile failed for '{source_id}': {e}")
      return {
          "success": False,
          "source_id": source_id,
          "message": f"Source profile failed: {e}",
          "correction_hint": "请检查 geojson_data 是否为合法 GeoJSON、ref:xxx 引用或可读 URL/路径后重试。",
      }
    return {
        "success": True,
        "source_id": source_id,
        "profile": profile,
        "summary": f"Profile generated for source '{source_id}'",
    }

  @tool(
      registry,
      name="webgis_layer_upsert",
      description="创建或更新 MapSpec 图层规范，自动剖析数据源并设置建议视角，且同步编译发布到 runtime map_state。",
      args_model=WebgisLayerUpsertArgs,
      tier=1,
  )
  async def webgis_layer_upsert(
      layer: Dict[str, Any],
      source_data: Optional[Any] = None,
      session_id: Optional[str] = None,
  ) -> dict:
    if not session_id:
      return {"success": False, "message": "Missing session_id"}
    layer = dict(layer)
    source_ref: Optional[str] = None
    source_descriptor: Optional[Dict[str, Any]] = None
    if isinstance(source_data, str):
      resolved_alias = await session_data_manager.resolve_alias(session_id, source_data)
      # URL/path sources are valid MapSpec inputs.  Only an explicit ref or a
      # session-known alias is dereferenced; arbitrary strings must not become
      # fabricated session cursors.
      if source_data.startswith("ref:") or resolved_alias != source_data:
        source_ref = resolved_alias
        source_descriptor = await session_data_manager.get_ref_descriptor(
            session_id, source_ref
        )
        if source_descriptor is None:
          return {
            "success": False,
            "code": "REFERENCE_NOT_FOUND",
            "message": f"Source reference '{source_data}' is unavailable in this session",
            "correction_hint": "Use a result ref created in the current session.",
          }
        profile = _descriptor_profile(source_descriptor)
        source_data = {
            "type": "geojson",
            "ref_id": source_ref,
            "profile": profile,
            "profile_fingerprint": _fingerprint_metadata(profile, "profile"),
            "data_fingerprint": _fingerprint_metadata(
                {"ref_id": source_ref, "descriptor": source_descriptor}, "data"
            ),
        }
        provenance = (
          dict(layer.get("provenance"))
          if isinstance(layer.get("provenance"), dict) else {}
        )
        provenance["result_ref"] = source_ref
        layer["provenance"] = provenance
    elif (
        isinstance(source_data, dict)
        and source_data.get("type") in ("Feature", "FeatureCollection")
    ):
      # Inline inputs are already in memory; persist them once and let MapSpec
      # retain only the resulting opaque identity plus its derived profile.
      source_ref = await session_data_manager.store(
          session_id, source_data, prefix="geojson"
      )
      # R2-2 (tool-level): a Redis outage makes store() return the
      # unavailable-ref sentinel. Bailing out HERE keeps the phantom ref out
      # of MapSpec desired state — the dispatch-level check fires after
      # layer_upsert has already persisted a layer pointing at a ref with no
      # payload anywhere.
      if is_unavailable_ref(source_ref):
        return {
            "success": False,
            "code": "SESSION_STORE_UNAVAILABLE",
            "message": "会话存储暂时不可用，无法保存图层数据；请稍后重试，无需改变参数。",
        }
      provenance = (
          dict(layer.get("provenance"))
          if isinstance(layer.get("provenance"), dict) else {}
      )
      provenance["result_ref"] = source_ref
      layer["provenance"] = provenance
    res = await mapspec_store.layer_upsert(session_id, layer, source_data)
    out: Dict[str, Any] = {
        "layer_id": layer.get("id"),
        "mapspec": res.get("mapspec"),
    }
    if res.get("success"):
      out["summary"] = f"Layer '{layer.get('id')}' upserted into MapSpec"
      mapspec = res.get("mapspec") if isinstance(res.get("mapspec"), dict) else {}
      reviewed_layer = next(
          (
            item for item in mapspec.get("layers", [])
            if isinstance(item, dict) and item.get("id") == layer.get("id")
          ),
          layer,
      )
      source_id = reviewed_layer.get("source")
      source_entry = (
          mapspec.get("sources", {}).get(source_id, {})
          if isinstance(mapspec.get("sources"), dict) else {}
      )
      authoritative_ref = (
          source_ref
          or source_entry.get("ref")
          or source_entry.get("ref_id")
          or source_entry.get("imageRef")
      )
      runtime_attempts = (
          (res.get("cartographic_review") or {}).get("attempts", [])
          if isinstance(res.get("cartographic_review"), dict) else []
      )
      runtime_patch = _runtime_patch(
          reviewed_layer,
          authoritative_ref if isinstance(authoritative_ref, str) else None,
          res.get("mapspec_fingerprint"),
          runtime_attempts if isinstance(runtime_attempts, list) else [],
      )
      commands: List[Dict[str, Any]] = []
      if source_entry.get("type") == "raster":
        image_ref = source_entry.get("imageRef")
        bounds = source_entry.get("bounds")
        if isinstance(image_ref, str) and image_ref.startswith("ref:raster/"):
          raster_id = image_ref[len("ref:raster/"):]
          image_url = f"/api/v1/sessions/{session_id}/raster/{raster_id}.png"
          # SEC-08/#408：路由要求所有权校验；MapLibre 图片请求带不了请求头，
          # 匿名会话的 owner_token 以查询参数附加在 URL 上。
          try:
            from app.api.routes.raster import lookup_session_owner_token
            session_token = await lookup_session_owner_token(session_id)
          except Exception:
            session_token = None
          if session_token:
            image_url = f"{image_url}?token={session_token}"
          out.update({
              "type": "heatmap_raster",
              "image": image_url,
              "bbox": bounds,
              "result_ref": image_ref,
          })
          runtime_patch["image_ref"] = image_ref
          commands.append({
              "command": "add_heatmap_raster",
              "params": {
                  "id": reviewed_layer.get("id"),
                  "image": image_url,
                  "bbox": bounds,
                  "mapspec_fingerprint": res.get("mapspec_fingerprint"),
              },
          })
      elif isinstance(authoritative_ref, str) and authoritative_ref.startswith("ref:"):
        out["result_ref"] = authoritative_ref
        # Existing runtime seam: the step result mounts/updates a ref-backed
        # HUD layer, then MapSpecRuntime reconciles it into MapLibre.  The ACK
        # proves only store mounting; a later live observation proves quality.
        commands.append({
          "command": "add_layer",
          "params": {
            "layerId": reviewed_layer.get("id"),
            "result_ref": authoritative_ref,
            "mapspec_fingerprint": res.get("mapspec_fingerprint"),
          },
        })
      desired_view = mapspec.get("view") if isinstance(mapspec.get("view"), dict) else {}
      if commands and (
          isinstance(desired_view.get("center"), (list, tuple))
          and len(desired_view["center"]) >= 2
          and isinstance(desired_view.get("zoom"), (int, float))
      ):
        commands.append({
            "command": "set_map_view",
            "params": {
              key: desired_view[key]
              for key in ("center", "zoom", "bearing", "pitch")
              if key in desired_view
            },
        })
      if commands:
        out["commands"] = commands
        out["runtime_patch"] = runtime_patch
        out["runtime_projection_fingerprint"] = runtime_patch["projection_fingerprint"]
    return _forward_evidence(res, out)

  @tool(
      registry,
      tier=2, domains=["report"], name="webgis_layer_remove",
      description="从 MapSpec 中移除指定图层并同步从 runtime map_state 擦除。",
      args_model=WebgisLayerRemoveArgs,
  )
  async def webgis_layer_remove(
      layer_id: str,
      session_id: Optional[str] = None,
  ) -> dict:
    if not session_id:
      return {"success": False, "message": "Missing session_id"}
    res = await mapspec_store.layer_remove(session_id, layer_id)
    out: Dict[str, Any] = {"removed_id": layer_id}
    if res.get("success"):
      out["summary"] = f"Layer '{layer_id}' removed from MapSpec"
    return _forward_evidence(res, out)

  @tool(
      registry,
      tier=2, domains=["report"], name="webgis_layout_set",
      description="设置 MapSpec 版面配置 (图例位置、控件、边距)。",
      args_model=WebgisLayoutSetArgs
  )
  async def webgis_layout_set(
      legend: Optional[Dict[str, Any]] = None,
      controls: Optional[List[Dict[str, Any]]] = None,
      margins: Optional[Dict[str, Any]] = None,
      session_id: Optional[str] = None,
  ) -> dict:
    if not session_id:
      return {"success": False, "message": "Missing session_id"}
    res = await mapspec_store.layout_set(session_id, legend, controls, margins)
    out: Dict[str, Any] = {"layout": res.get("layout", {})}
    if res.get("success"):
      out["summary"] = "MapSpec layout updated"
    return _forward_evidence(res, out)

  @tool(
      registry,
      tier=2, domains=["report"], name="webgis_validate",
      description="在编译前检验 MapSpec 规范性 (CRS, 字段存在性, stops 严格单调性, view 合理性)。",
      args_model=WebgisValidateArgs
  )
  async def webgis_validate(session_id: Optional[str] = None) -> dict:
    if not session_id:
      return {"success": False, "message": "Missing session_id"}
    return await mapspec_store.validate_mapspec(session_id)

  @tool(
      registry,
      tier=2, domains=["report"], name="webgis_compile_maplibre",
      description="执行 MapSpec 编译，产出 style.json, index.html 与 compile-report.json。",
      args_model=WebgisCompileMaplibreArgs,
  )
  async def webgis_compile_maplibre(session_id: Optional[str] = None) -> dict:
    if not session_id:
      return {"success": False, "message": "Missing session_id"}
    return await mapspec_store.compile_mapspec_cli(session_id)

  @tool(
      registry,
      tier=2, domains=["report"], name="webgis_checkpoint",
      description="创建 MapSpec 快照并具象化落地所引用的全部 ref_id 数据载荷。",
      args_model=WebgisCheckpointArgs,
  )
  async def webgis_checkpoint(
      checkpoint_id: Optional[str] = None,
      session_id: Optional[str] = None,
  ) -> dict:
    if not session_id:
      return {"success": False, "message": "Missing session_id"}
    return await mapspec_store.checkpoint(session_id, checkpoint_id)

  @tool(
      registry,
      tier=2, domains=["report"], name="webgis_rollback",
      description="回滚 MapSpec 与 runtime map_state 到指定的快照点。",
      args_model=WebgisRollbackArgs,
  )
  async def webgis_rollback(
      checkpoint_id: str,
      session_id: Optional[str] = None,
  ) -> dict:
    if not session_id:
      return {"success": False, "message": "Missing session_id"}
    return await mapspec_store.rollback(session_id, checkpoint_id)

  @tool(
      registry,
      tier=2, domains=["report"], name="webgis_runtime_validate",
      description="重新编译当前 MapSpec 并在 Headless 环境下进行运行时验收与 5-维度评分 (80% max)。",
      args_model=WebgisRuntimeValidateArgs
  )
  async def webgis_runtime_validate(session_id: Optional[str] = None) -> dict:
    from app.services.runtime_validator import runtime_validator
    if not session_id:
      return {"success": False, "message": "Missing session_id"}
    return await runtime_validator.validate_runtime(session_id)

  @tool(
      registry,
      tier=2, domains=["report"], name="webgis_cartography_status",
      description="查询制图 harness 对当前地图状态的服务端验证结论（desired↔runtime 收敛判定、失败检查项与修复进度）。只读，不触发重评估或修复。",
      args_model=WebgisCartographyStatusArgs
  )
  async def webgis_cartography_status(session_id: Optional[str] = None) -> dict:
    if not session_id:
      return {"success": False, "message": "Missing session_id"}
    from app.lib.cartography.verdict_summary import render_verdict_for_llm
    state = await session_data_manager.get_map_state(session_id)
    review = state.get("_cartographic_review")
    if not isinstance(review, dict):
      return {
          "success": True,
          "summary": "No cartography harness verdict yet (no MapSpec mutation evaluated).",
          "cartography": {"status": "not_evaluated", "termination_reason": "no_session_harness"},
          "overall_passed": False,
      }
    cartography = review.get("cartography") if isinstance(review.get("cartography"), dict) else {}
    return {
        "success": True,
        # summary 驱动 slim LLM payload；渲染器已封顶，不会搬运数据体。
        "summary": render_verdict_for_llm(review),
        "cartography": cartography,
        "gate": review.get("gate"),
        "overall_passed": bool(review.get("overall_passed")),
    }
