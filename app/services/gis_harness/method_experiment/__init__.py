"""GIS 科学方法实验引擎（method-experiment，02 任务核心交付）。

「同一空间问题的多个合理科学方法/参数组合」如何被**公平、可复现地
执行、评价、解释与采纳**：

- ``cards``     WP1 科学方法卡（候选空间/参数角色/协议建议；引用既有
                 AlgorithmRegistry/MetricRegistry/ToolRegistry id，悬空 fatal）
- ``metrics``   WP4 指标契约注册表（方向/单位类/可比组；零计算实现）
- ``protocol``  WP2 评价协议（折分配词表引用 cv.py；kind 决定指标语义）
- ``plan``      WP2 实验计划（确定性展开 + 执行前 typed 拒绝 + 预算硬门）
- ``state``     WP3 运行态持久（会话 store 单键；诚实结局词表）
- ``runner``    WP3 试验执行（真实 ToolDispatchService 分发 + 内容寻址
                 复用 + 取消/预算/部分结果）
- ``evaluate``  WP4 评价裁决（可比组排名 + Pareto + 证据推荐/弃权 +
                 采纳回执）

红线（与 analysis_workflows 同纪律）：不建第二 registry / 不复制算子
语义 / 大数据 ref 直通（工具层解引用）/ 工具失败不得得到 pass verdict /
未评估不是 pass / LLM 生成的文字不是科学规则。
"""
from app.services.gis_harness.method_experiment.cards import (  # noqa: F401
    EXPERIMENT_FAMILY_IDS,
    PARAM_ROLES,
    CandidateSpec,
    MethodCard,
    all_cards,
    card_exists,
    get_card,
    match_family_by_question,
    validate_cards,
)
from app.services.gis_harness.method_experiment.metrics import (  # noqa: F401
    COMPARABLE_GROUPS,
    METRIC_IDS,
    METRIC_REGISTRY,
    get_metric,
    metric_exists,
    metrics_for_family,
)
from app.services.gis_harness.method_experiment.plan import (  # noqa: F401
    DEFAULT_MAX_TRIALS,
    MAX_TRIALS_HARD_CAP,
    MEXP_BUDGET_EXCEEDED,
    MEXP_NO_INPUT_REF,
    MEXP_UNKNOWN_CANDIDATE,
    MEXP_UNKNOWN_FAMILY,
    MEXP_VALUE_FIELD_REQUIRED,
    MethodExperimentPlan,
    PlanCompileError,
    compile_experiment_plan,
)
from app.services.gis_harness.method_experiment.protocol import (  # noqa: F401
    CV_SCHEMES,
    EvaluationProtocol,
    build_protocol_from_card,
)
from app.services.gis_harness.method_experiment.runner import (  # noqa: F401
    ExperimentRunner,
    trial_content_fingerprint,
)
from app.services.gis_harness.method_experiment.state import (  # noqa: F401
    ExperimentRunState,
    TrialRecord,
    new_state,
    state_alias,
    state_from_payload,
    state_to_payload,
)
from app.services.gis_harness.method_experiment.evaluate import (  # noqa: F401
    ExperimentEvaluation,
    build_adoption_receipt,
    evaluate_experiment,
)

__all__ = [
    "EXPERIMENT_FAMILY_IDS", "PARAM_ROLES", "CandidateSpec", "MethodCard",
    "all_cards", "card_exists", "get_card", "match_family_by_question",
    "validate_cards",
    "COMPARABLE_GROUPS", "METRIC_IDS", "METRIC_REGISTRY", "get_metric",
    "metric_exists", "metrics_for_family",
    "DEFAULT_MAX_TRIALS", "MAX_TRIALS_HARD_CAP", "MEXP_BUDGET_EXCEEDED",
    "MEXP_NO_INPUT_REF", "MEXP_UNKNOWN_CANDIDATE", "MEXP_UNKNOWN_FAMILY",
    "MEXP_VALUE_FIELD_REQUIRED", "MethodExperimentPlan", "PlanCompileError",
    "compile_experiment_plan",
    "CV_SCHEMES", "EvaluationProtocol", "build_protocol_from_card",
    "ExperimentRunner", "trial_content_fingerprint",
    "ExperimentRunState", "TrialRecord", "new_state", "state_alias",
    "state_from_payload", "state_to_payload",
    "ExperimentEvaluation", "build_adoption_receipt", "evaluate_experiment",
]
