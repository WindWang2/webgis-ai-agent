"""G45 坐标操作域 — typed 错误码、预算常量与错误类。

错误语义与 G38 data_join_authoring/errors.py 同构：stable machine code +
用户可读 reason + correction_hint；HTTP/工具/Pi 三面共用同一词表。
"""
from __future__ import annotations

from typing import Any, Dict


class CoordinateOperationError(Exception):
    """typed 域错误（error_code 对应下方词表）。"""

    def __init__(self, error_code: str, reason: str, **details: Any):
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason
        self.details: Dict[str, Any] = {k: v for k, v in details.items() if v is not None}

    def payload(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "error_code": self.error_code,
            "reason": self.reason,
        }
        if self.details:
            out["details"] = self.details
        out["correction_hint"] = _HINTS.get(self.error_code)
        return out


#: 错误码 → 修复建议（UI/Pi 回执统一附带）。
_HINTS: Dict[str, str] = {
    "source_unqualified": "选择带几何的矢量数据源（session FC ref / fabric-parquet / 项目产物）",
    "source_crs_missing": "先在导入/数据源侧确认源 CRS（本域不做 CRS 猜测）",
    "source_crs_untrusted": "源 CRS 标签来源不可信；请先修正源数据 CRS 声明",
    "source_incomplete": "换用完整数据源（当前选择/截断样本不能作为完整执行范围）",
    "source_revision_changed": "源在预览后发生变化；请重新描述源并更新计划",
    "source_expired": "源引用已过期；请先物化或晋升为项目产物",
    "crs_unsupported": "该 CRS 超出当前支持范围（如垂向/compound/动态 datum）",
    "crs_unknown": "目标 CRS 无法解析；请检查代码或名称",
    "operation_no_candidates": "该源/目标在当前离线环境无可用坐标操作",
    "operation_unavailable": "所选操作依赖的格网在当前离线环境不可用；不能静默降级",
    "operation_not_in_plan": "candidate_id 不属于当前计划的服务器签发候选集",
    "operation_env_changed": "PROJ/格网环境已变化；原计划失效，请重新列出候选并锁定",
    "plan_revision_conflict": "计划已被修改；请刷新后重试（CAS）",
    "plan_not_locked": "先锁定一个候选操作，再预览/执行",
    "plan_invalid": "计划内容不完整或非法",
    "preview_stale": "计划已修改，预览失效；请重新预览",
    "execute_failed": "全量执行失败（all-or-nothing：未发布任何完整成果）",
    "budget_exceeded": "数据规模超过本域预算上限",
    "unsupported_dimension": "几何维度超出支持（M/第四维/垂向基准未实现）",
    "invalid_spec": "请求参数非法",
    "unauthorized": "无权访问该资源",
    "not_found": "资源不存在",
    "internal_error": "内部错误",
    "result_unavailable": "成果不可用（可能已过期或未完成）",
    "display_unavailable": "WGS84 显示副本不可用（主成果不受影响）",
    "publish_blocked": "发布前置校验未通过（取消/授权/claim 状态无法确认）",
}


def http_status(err: CoordinateOperationError) -> int:
    code = err.error_code
    if code == "unauthorized":
        return 403
    if code in ("not_found", "source_expired"):
        return 404
    if code == "plan_revision_conflict":
        return 409
    if code in ("invalid_spec", "plan_invalid", "source_unqualified",
                "source_crs_missing", "source_crs_untrusted", "source_incomplete",
                "crs_unknown", "crs_unsupported", "unsupported_dimension",
                "operation_no_candidates", "operation_unavailable",
                "operation_not_in_plan", "operation_env_changed",
                "plan_not_locked", "preview_stale", "budget_exceeded"):
        return 422
    if code == "internal_error":
        return 500
    return 400


# ── 预算（PLAN.md 里程碑；不低于 G18 语义、更严部署配置服从更严值）─────────

#: 单次全量执行最大行数（与 G18 交付行数上限同量级）。
MAX_ROWS = 200_000
#: 全量执行最大总顶点数（分配前闸）。
MAX_TOTAL_VERTICES = 5_000_000
#: 单几何最大顶点数（分配前闸；防单要素爆炸）。
MAX_SINGLE_GEOM_VERTICES = 200_000
#: 输出最大字节数（staging 成果 + 显示副本合计上限基线）。
MAX_OUTPUT_BYTES = 512 * 1024 * 1024
#: 批读取行数（执行体批粒度）。
EXECUTE_BATCH_ROWS = 2_000
#: 预览最大样本行数。
PREVIEW_MAX_ROWS = 50
#: 预览/回执展示的 before/after 坐标对上限。
PREVIEW_MAX_COORD_PAIRS = 8
#: 显示副本（session FC ref）行数上限（渲染投影语义）。
DISPLAY_REF_MAX_ROWS = 10_000
#: 计划 JSON 落库上限（模型列 String/JSON 约束）。
PLAN_JSON_MAX_BYTES = 32 * 1024
#: 预览证据落库上限。
PREVIEW_JSON_MAX_BYTES = 16 * 1024
#: 收据落库上限。
RECEIPT_JSON_MAX_BYTES = 16 * 1024
#: 结果分页每页上限。
RESULT_PAGE_MAX_ROWS = 200
#: 幂等键最大长度（analysis_tasks.idempotency_key 列宽内）。
IDEMPOTENCY_KEY_MAX = 255


TASK_TYPE = "coordinate_operation_execute"
#: JobKind 值（analysis_tasks.kind String(20)：coord_operations=16 字符）。
JOB_KIND = "coord_operations"
#: DataObject kind 白名单值。
DATA_OBJECT_KIND = "coordinate_operation"
#: 成果目录名（DATA_DIR 下）。
DIR_NAME = "coord-ops"
#: session 台账 ref 前缀。
ARTIFACT_REF_PREFIX = "ref:coord-ops/"
#: 显示副本 session FC ref 前缀。
DISPLAY_REF_PREFIX = "coord-ops-display"
#: 冻结源文件名（staging 内）。
FROZEN_SOURCE_NAME = "source-frozen"
