"""Neutral classification of established tool error-as-value shapes."""
from typing import Any


def is_error_like_result(result: Any) -> bool:
    """#529/#589: normal-return failure shapes.

    Tool sites return failure dicts instead of raising or returning the
    canonical ``std_error_response`` shape; only the canonical shape was
    recognized, so these were classified as success (marked completed, same-args
    retries intercepted with a fabricated "已成功执行", plans advanced past
    failures).

    Recognized error-as-value key shapes:
      - ``{"error": <str>}``                — the #529 family (~139 sites),
      - ``{"type": "error", ...}``          — network/temporal/spatial_decision,
      - ``{"status": "error"|"failed"}``    — project/workflow/explorer,
      - ``{"success": False, "message": <str>}`` — audit4 #984: the
        cartography_tools/webgis_component_update family (~17 sites).
        ``success is False`` 是无歧义的失败信号（与 registry metrics 的判定
        口径一致），此前仅因缺 code 键逃过归一 —— 被标 completed、同参重试
        被「已成功执行」拦截。

    Conservative by design (mirrors ``chinese_maps/http.py``'s
    ``_is_provider_error_dict``): only a **string** ``error`` value classifies,
    so business payloads that merely carry an ``error`` key (``None``, numeric,
    nested error info) are not reclassified. The string may be empty — an
    ``{"error": ""}`` result is still an error channel, not a success. ``type``
    and ``status`` only classify at their canonical error values, so legitimate
    uses of those keys (``{"type": "FeatureCollection"}``, ``{"status": "ok"}``,
    ``{"status": "template_applied"}``, ...) are never reclassified. An explicit
    ``success=True`` shields partial-success payloads (e.g. geocode returning
    results alongside an error note).
    """
    if not isinstance(result, dict) or result.get("success") is True:
        return False
    if isinstance(result.get("error"), str):
        return True
    if result.get("type") == "error":
        return True
    if result.get("status") in ("error", "failed"):
        return True
    if result.get("success") is False and isinstance(result.get("message"), str):
        return True
    # V2(§34) GeoAnalysisResult 失败族：to_llm_response 的失败形状是
    # {success: False, summary: <message>, data: None}（无 error/message
    # 键），此前逃过所有族 → 被标 completed、同参重试被“已成功执行”拦截。
    # data 缺席守卫：带 data 的部分成功载荷不由本族分类。
    if (
        result.get("success") is False
        and isinstance(result.get("summary"), str)
        and result.get("summary")
        and result.get("data") is None
    ):
        return True
    return False



def is_suspicious_result(result: Any) -> bool:
    """检测工具返回是否"可疑"（空数据 / 错误响应），用于触发自愈提示尾。

    纯函数无副作用。本函数为单一权威定义（unified-tool-dispatch 票据 04 contract：
    旧 chat/dispatcher.py 已删除，收敛至此）。供 ToolDispatchService 内部与
    ChatEngine._log_tool_decision / _detect_suspicious_result 共用。
    """
    if not result:
        return True
    if isinstance(result, dict):
        # #529: the {"error": <str>} shape is a failure, not an empty/suspicious
        # success — classify it so standalone consumers (engine
        # _detect_suspicious_result / decision logging) don't treat it as
        # success. (The dispatch error branch handles it before this point.)
        if (result.get("success") is False and "code" in result) or is_error_like_result(result):
            return True
        # MapSpec 封装会剥掉 features、只留 result_ref + feature_count。
        # 若仍按「无 features 即空」判断，本地 OSM/行政区成功也会被标 empty，
        # 模型就会再去调高德 search_poi。
        ref = result.get("result_ref") or result.get("ref")
        n = result.get("feature_count")
        if n is None:
            n = result.get("count")
        if isinstance(ref, str) and ref.startswith("ref:") and isinstance(n, (int, float)) and n > 0:
            return False
        if result.get("type") == "FeatureCollection" and not result.get("features"):
            return True
        if "data" in result and isinstance(result["data"], list) and not result["data"]:
            return True
        if "poi_count" in result and result["poi_count"] == 0:
            return True
    if isinstance(result, list) and not result:
        return True
    return False
