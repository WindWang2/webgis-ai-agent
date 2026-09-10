"""ModelOps Typed Error Taxonomy（ADR-0119）。

设计约束（与 ``app/lib/gis/scientific_errors.py`` 同一口径）：

- 每个错误携带稳定 ``modelops_code``（机器可读，进工具结果 / manifest /
  correction_hint）；产品层永不解析 traceback 来「解释失败」；
- 全部 subclass ``ValueError``：ToolRegistry.dispatch 既有错误映射
  （ValueError → std_error_response）逐位兼容；
- 不滥用：只有 **ModelOps 域**失败（描述符/兼容性/包安全/provider 生命
  周期/资源/取消）使用这些类型；普通参数校验继续走 pydantic/ValueError。
"""
from __future__ import annotations

from typing import Optional


class ModelOpsError(ValueError):
    """ModelOps 域失败基类。"""

    modelops_code = "MODELOPS_ERROR"

    def __init__(self, detail: str, *, correction_hint: str = "") -> None:
        super().__init__(detail)
        self.detail = detail
        self.correction_hint = correction_hint or self._default_hint()

    def _default_hint(self) -> str:
        return ""

    def to_dict(self) -> dict:
        return {
            "modelops_code": self.modelops_code,
            "detail": self.detail,
            "correction_hint": self.correction_hint,
        }


# ── 描述符 / 注册表 ──────────────────────────────────────────────────


class DescriptorError(ModelOpsError):
    """ModelDescriptor 非法（schema/字段/引用）。"""

    modelops_code = "DESCRIPTOR_INVALID"


class ModelVersionCollision(DescriptorError):
    """同 (model_id, model_version) 不同 checksum —— 身份碰撞。"""

    modelops_code = "MODEL_VERSION_COLLISION"

    def _default_hint(self) -> str:
        return "bump model_version for changed weights; identical content must reuse the existing version"


class ModelNotFoundError(ModelOpsError):
    """registry 中不存在该模型（或不在请求者 scope 内 —— 语义不区分）。"""

    modelops_code = "MODEL_NOT_FOUND"

    def _default_hint(self) -> str:
        return "list models visible to this owner scope first"


class RegistryParityError(ModelOpsError):
    """registry index 与文档不一致（存储层损坏/外部篡改）。"""

    modelops_code = "REGISTRY_PARITY_BROKEN"

    def _default_hint(self) -> str:
        return "run registry parity repair or re-register the model"


class ModelChecksumError(ModelOpsError):
    """模型包内容与登记 checksum 不符（注册时与 load 时双验）。"""

    modelops_code = "MODEL_CHECKSUM_MISMATCH"

    def _default_hint(self) -> str:
        return "re-upload the model package; never register with a computed-at-runtime checksum"


# ── 包安全 ──────────────────────────────────────────────────────────


class PackageSecurityError(ModelOpsError):
    """模型包安全门拒绝（traversal/symlink/超限/危险成员/元数据非法）。

    包**只校验不执行**：这是校验层失败，不是运行时失败。
    """

    modelops_code = "PACKAGE_SECURITY_REJECTED"


# ── 兼容性 / 规划 ───────────────────────────────────────────────────


class CompatibilityError(ModelOpsError):
    """模型与输入在语义上不兼容（bands/dtype/resolution/CRS/temporal…）。

    「能跑」不等于兼容：qualifier 在任何读取/执行之前拒绝。
    """

    modelops_code = "COMPATIBILITY_FAILED"

    def __init__(self, detail: str, *, failures: Optional[list] = None,
                 correction_hint: str = "") -> None:
        super().__init__(detail, correction_hint=correction_hint)
        #: 结构化失败明细（CompatibilityFailure.to_dict() 列表）。
        self.failures = list(failures or [])

    def to_dict(self) -> dict:
        payload = super().to_dict()
        payload["failures"] = list(self.failures)
        return payload


class PlanningError(ModelOpsError):
    """tile/资源规划失败（描述符与规划参数矛盾、栅格过小等）。"""

    modelops_code = "PLANNING_FAILED"


class PreprocessError(ModelOpsError):
    """预处理执行失败（band 选择越界、归一化参数缺失等）。"""

    modelops_code = "PREPROCESS_FAILED"


# ── Provider 生命周期 / 资源 ────────────────────────────────────────


class ProviderError(ModelOpsError):
    """provider 运行失败（load/infer/health 非取消类失败）。"""

    modelops_code = "PROVIDER_FAILED"


class ProviderLoadFailed(ProviderError):
    """模型加载失败（坏权重/不支持的 artifact_format/runtime 缺失）。"""

    modelops_code = "PROVIDER_LOAD_FAILED"


class ProviderOOM(ProviderError):
    """provider 报告内存不足（真实或 mock 探测）。

    引擎据此执行**有界**降批重试（≤MAX_OOM_DOWNSHIFTS，batch 下限 1）；
    超出后如实失败，绝不无限重试。
    """

    modelops_code = "PROVIDER_OOM"


class ResourceUnavailable(ModelOpsError):
    """资源计划不可满足（GPU required 但无 GPU、VRAM 预算不足、并发槽满）。"""

    modelops_code = "RESOURCE_UNAVAILABLE"

    def _default_hint(self) -> str:
        return "relax device_requirements (allow CPU fallback) or reduce concurrent load"


class InferenceTimeout(ModelOpsError):
    """推理超过声明墙钟 deadline（取消语义的一部分，非 provider 故障）。"""

    modelops_code = "INFERENCE_TIMEOUT"


# ── 远程 / 安全 ─────────────────────────────────────────────────────


class RemoteEndpointPolicyError(ModelOpsError):
    """remote endpoint 未通过 allowlist/SSRF 策略（默认拒绝）。"""

    modelops_code = "REMOTE_ENDPOINT_REJECTED"


class RemoteInferenceError(ProviderError):
    """remote 推理失败（协议/状态码/响应超限/错误体）。错误体不可信，
    构造方必须先 sanitize。"""

    modelops_code = "REMOTE_INFERENCE_FAILED"


class OutputBudgetExceeded(ModelOpsError):
    """输出超过声明字节/像素预算（output bomb 防护）。"""

    modelops_code = "OUTPUT_BUDGET_EXCEEDED"


# ── 取消 ────────────────────────────────────────────────────────────


class InferenceCancelled(ModelOpsError):
    """推理被调用方取消（正常控制流，不是失败；isolated 便于引擎区分）。"""

    modelops_code = "INFERENCE_CANCELLED"


class SecretLeakGuardError(ModelOpsError):
    """secret 试图进入 descriptor/manifest/日志路径（供给即授权，只留 ref）。"""

    modelops_code = "SECRET_LEAK_GUARD"
