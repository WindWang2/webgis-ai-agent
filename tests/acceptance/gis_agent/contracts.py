"""G05 task-level ScenarioContract DSL（task_scenario.v1）。

评测/测试侧结构（本目录专有），**不是** production planner 的第二事实源：
场景描述「用户目标 → 数据承诺 → scripted agent 行为 → 各链路断言面期望」，
执行与判定全部复用生产权威模块（dispatch 缝 / settle / MapSpecStore /
goal_satisfaction / product_manifest）。

诚实边界（设计即披露）：
- 生产中 NL→tool-call 由 Pi LLM 决策；离线场景以 ``AgentStep`` 脚本代替
  该决策（负例场景 = 脚本注入错误行为，断言判错面翻红）。NL 理解本身
  在本 lane 一律 ``not_evaluated``（见 ``ScenarioContract.not_evaluated``）。
- 不可观测的面（真实 SSE 下行 / 前端渲染像素）不伪造断言；渲染证据以
  RenderWorkEnvelope 投影与导出语义一致性为准。
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List

from pydantic import BaseModel, Field

SCHEMA_VERSION = "task_scenario.v1"


# ── 用户数据承诺（离线合成 fixture；一律 simulated）──────────────────────

class DatasetSpec(BaseModel):
    """场景内命名数据集：由 fixtures_data 的确定性工厂生成（体积有界）。

    ``dataset_kind`` 对应工厂注册键；所有样本均为**模拟合成数据**
    （成都地名仅作空间参考 bbox，不暗示实测）。
    """

    name: str = Field(..., description="场景内引用名（{{ref:<name>}}）")
    dataset_kind: str = Field(
        ..., description="fixtures_data 工厂键：school_points/district_polygons/...")
    simulated: bool = True
    note: str = ""


# ── scripted agent 行为（NL→tool-call 的离线替身）───────────────────────

class StepKind(str, Enum):
    INTENT = "intent"              # webgis_map_intent（query 必填）
    STORE_DATA = "store_data"      # session_data_manager.store(dataset.name) → ref
    EXECUTE = "execute"            # webgis_execute 代理（tool=目标工具名）
    PRODUCT = "product"            # webgis_map_product
    COMPONENT_UPDATE = "component_update"
    PRODUCT_EDIT = "product_edit"
    RAW = "raw"                    # 按名直调（如 webgis_cartography_status）


class AgentStep(BaseModel):
    """一步 scripted tool-call（经真实 dispatch_tool 缝执行）。

    ``arguments`` 中可引用模板占位符（runner 展开）：
    - ``{{ref:<dataset>}}``  → STORE_DATA 产出的 session ref
    - ``{{layer:<layer_id>}}`` → 前一步结果透出的 layer_id（如 product 补层）
    - ``{{step:<call_id>:<jsonpath>}}`` → 前一步 dispatch 结果取值
    """

    call_id: str
    kind: StepKind
    tool: str = ""                 # EXECUTE 的目标工具名 / RAW 的工具名
    arguments: Dict[str, Any] = Field(default_factory=dict)
    query: str = ""                # INTENT/PRODUCT：用户请求文本（缺省取 turn.message）
    expect_error: bool = Field(
        False, description="scripted 行为本身预期失败（负例注入：错参数/坏数据）")
    allow_error: bool = Field(
        False, description="允许失败但不断言（数据条件分支步）")


class TurnSpec(BaseModel):
    """一个用户 turn：消息 + scripted 行为 + settle 语义。"""

    turn_id: str
    message: str = Field(..., min_length=1)
    steps: List[AgentStep] = Field(default_factory=list)
    settle_cause: str = Field(
        "success", description="settle_turn cause：success/user_cancel/timeout/"
                               "send_error/process_died/unclassified_error")
    failure_class: str = ""
    failure_detail: str = ""
    # 多轮中断语义：True = 本 turn 不结算（进程死亡/中断），留给恢复场景。
    leave_unsettled: bool = False


# ── 断言面与期望（期望由人工选取的不变量构成，非录制当前输出）────────────

class Face(str, Enum):
    """链路断言面（同时是 failure taxonomy 的归因面）。"""

    INTENT = "intent"              # 意图/范围/主体/recipe 解析
    REQUIREMENT = "requirement"    # RequirementDocument 终态
    METHOD = "method"              # 方法科学资格（qualification/fallback/样本量）
    DATA = "data"                  # 数据 ref/来源/CRS/字段
    MAPSPEC = "mapspec"            # 图层角色/可见性/组件/revision
    RENDER_EXPORT = "render_export"  # RenderWork/导出 manifest 语义一致性
    FINAL_ANSWER = "final_answer"  # 任务完成披露（task_complete/goal_satisfaction）


#: 面到权威生产模块的归因映射（attribution.py 消费；单一事实源在此）。
FACE_AUTHORITY: Dict[str, str] = {
    "intent": "app/services/gis_harness/intent.py + planner.py",
    "requirement": "app/services/gis_harness/requirement_ir/",
    "method": "app/services/gis_harness/{planner,data_qualification}.py",
    "data": "app/services/session_data.py + tools producer",
    "mapspec": "app/services/mapspec/ (store/lifecycle engine)",
    "render_export": "app/lib/cartography/{render_work_envelope,product_manifest}.py",
    "final_answer": "app/services/gis_harness/completion/pipeline.py + goal_satisfaction/",
}


class Expectation(BaseModel):
    """一条可判定期望：对 TaskEvidence 投影的 path 断言。

    ``expected_verdict``：该检查的目标判定（pass/fail/not_evaluated）。
    语料口径注记：负例通过「对降级不变量的 pass 期望 + not_evaluated
    诚实缺席钉扎 + 行为反转测试」达成（见 docs/quality §2 负例分层）；
    expected_verdict="fail" 为受支持的 DSL 能力（单测覆盖），语料暂无
    使用——若使用须配合反转防线防止恒绿。
    """

    id: str
    face: Face
    kind: str = Field(
        ..., description="检查类别：goal_verdict/mapspec_layer/export_status/"
                         "tool_receipt/requirement_state/revision_monotonic/...")
    path: str = Field(..., min_length=1,
                      description="TaskEvidence 投影内 dotted path")
    op: str = Field("eq", description="eq/ne/in/not_in/contains/ge/le/exists/"
                                      "absent/regex")
    value: Any = None
    expected_verdict: str = Field(
        "pass",
        description="pass|fail|not_evaluated —— 本场景期望该检查的判定",
        pattern="^(pass|fail|not_evaluated)$")
    note: str = ""


# ── 场景契约本体 ──────────────────────────────────────────────────────────

class ScenarioContract(BaseModel):
    """一个 task-level 验收场景（正例或负例/恢复/多轮）。"""

    schema_version: str = SCHEMA_VERSION
    id: str
    family: str                     # case family（F01..F18，见 TEST_MATRIX）
    title: str
    description: str = ""
    user_goal: str                  # NL 目标（多轮场景 = 初始目标）
    negative: bool = False          # 负例（期望判错面翻红）
    datasets: List[DatasetSpec] = Field(default_factory=list)
    turns: List[TurnSpec]
    expectations: List[Expectation]
    #: 诚实边界：本场景内不可能离线判定的面（进报告，不算失败）。
    not_evaluated: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    timeout_s: int = 60

    def expectation(self, eid: str) -> Expectation:
        for exp in self.expectations:
            if exp.id == eid:
                return exp
        raise KeyError(eid)


# ── 运行结果（reporting.py 消费）──────────────────────────────────────────

class CheckVerdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NOT_EVALUATED = "not_evaluated"


class CheckResult(BaseModel):
    expectation_id: str
    face: str
    kind: str
    verdict: CheckVerdict
    expected_verdict: str
    detail: str = Field("", description="有界判定说明（含实际值）")
    actual: Any = None


class ScenarioRunResult(BaseModel):
    scenario_id: str
    family: str
    negative: bool
    checks: List[CheckResult] = Field(default_factory=list)
    error: str = ""                 # runner 级异常（非期望失败）
    duration_ms: int = 0
    # 执行侧副产物（报告/归因用；不参与 ok 判定）
    session_id: str = ""
    refs: Dict[str, str] = Field(default_factory=dict)
    settle_records: List[Dict[str, Any]] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """场景通过 = 每条检查的实际判定 == 场景期望判定（负例靠期望 fail 达成）。"""
        if self.error:
            return False
        return all(
            c.verdict.value == c.expected_verdict for c in self.checks)

    @property
    def first_failing_stage(self) -> str:
        """first-failing stage：第一条 期望≠实际 的检查的 face；全绿为空。"""
        for c in self.checks:
            if c.verdict.value != c.expected_verdict:
                return c.face
        return ""

    def summary_line(self) -> str:
        counts: Dict[str, int] = {}
        for c in self.checks:
            counts[c.verdict.value] = counts.get(c.verdict.value, 0) + 1
        verdict = "OK" if self.ok else f"FAIL@{self.first_failing_stage or 'runner'}"
        parts = ",".join(f"{k}={v}" for k, v in sorted(counts.items())) or "no-checks"
        return f"{self.scenario_id}[{self.family}] {verdict} ({parts})"


def scenario_digest_payload(scenario: ScenarioContract) -> Dict[str, Any]:
    """场景的确定性序列化载体（pin/roundtrip 用；sorted keys、无时间戳）。"""
    return scenario.model_dump(mode="json")
