"""G12：f03 lifecycle ownership matrix 从 CSV 文档升级为代码级断言。

守护对象：``docs/dev/f03-lifecycle-ownership-matrix.csv``（PR #1506 / F03 的
产物）——harness 各状态词表的生命周期归属（定义点 / 权威写者 / 投影关系 /
持久化面 / 双写规则）。矩阵此前是纯文档：代码重构后行会静默漂移，无任何
CI 手段发现；本文件把它变成可执行断言，矩阵漂移即红。

断言分两层（13 行全量覆盖）：

- **行为层**（内存桩 harness 实例化生命周期关键路径，确定性：无 LLM /
  无 Pi 子进程 / 无外部服务）——``turn_phase``、``turn_status``、
  ``turn_event``、``map_mutation_event``、``abort_source``、
  ``refusal_signal``；
- **结构层**（AST 定义点检索 + import 实体存在性 + 文件域文本引用 +
  CSV 侧单元格锚与 "N 值"/值清单注解反向解析，
  检索范围为断言表指向的全 app/ 模块，非 gis_harness 单模块）——
  其余全部行 100% 覆盖。仅结构层的行与理由：
  * ``requirement_state``：evaluator 纯函数语义已由 goal_satisfaction
    契约套件覆盖，此处钉实体存在与定义点；
  * ``workflow_instance_status``：独立执行域权威，行为需 driver/store
    fencing 全链路（独立方向管辖），此处钉词表与 fencing 记号；
  * ``mission_state``：独立任务域，矩阵明示"本方向禁触碰"，此处钉词表。

已知文档漂移登记（矩阵事实以 CSV 为准、代码现实以断言为准；修复任一侧
后同步更新 ``KNOWN_VOCAB_DRIFTS``，见专用测试）：

- ``mission_state`` 行 vocabulary 写 ``MissionStatus``，代码实体实为
  ``MissionState``（app/ 全域 ``MissionStatus`` 零命中）；
- ``turn_event`` 行 authoritative_writer 写 ``GISSessionRuntime._event``，
  ``_event`` 实为 runtime.py 的模块级 seam 函数（GISSessionRuntime 各
  生命周期方法经它落账）——期望表按模块级实体钉扎。

深度行为回归（降级判定 / abort 竞态 / 投影 parity 等）由
``tests/unit/test_f03_kernel_lifecycle_events.py`` 等 F03 专项套件钉住；
本文件的 map_mutated / refusal 用例是对矩阵声明的守护级抽查（语义子集，
修改矩阵归属时先红），不是该套件的替代——两套件互为邻里、互不替代。

CSV 侧守护面（review P1 清偿）：每行 authoritative_writer / projection_of
/ persisted_in 的承重子串由 ``cell_anchors`` 逐行锚定（锚即今日矩阵原文，
改写任一承重列即红）；vocabulary 括号内的 "N 值" 与斜杠值清单直接从
CSV 反向解析后对代码验证，注解漂移（如 12 值改 99 值、删 refused）即红。
"""

from __future__ import annotations

import ast
import csv
import importlib
import inspect
import re
import uuid
from functools import lru_cache
from pathlib import Path
from typing import get_args

import pytest

REPO = Path(__file__).resolve().parents[2]
MATRIX = REPO / "docs" / "dev" / "f03-lifecycle-ownership-matrix.csv"

EXPECTED_HEADER = [
    "state_domain",
    "vocabulary",
    "definition_site",
    "authoritative_writer",
    "projection_of",
    "persisted_in",
    "double_write_rule",
]

# ── 期望表：每行矩阵 ↔ 代码实体的钉扎（ curated，勿泛化） ──────────────────
#
# site            —— 与 CSV definition_site 首词一致的仓库相对路径；
# principal_vocab —— 必须出现在该行 vocabulary 列里的主实体名（CSV 文本与
#                    代码实体的锚链；篡改列内类名在此红）；
# site_symbols    —— AST 级必须存在于 site 文件的定义名（class/func/assign）；
# import_pairs    —— (module, 可能带点的 attr) 导入级存在性；
# text_refs       —— (文件, 子串) 文件域文本引用（跨模块 seam / 记号）；
# cell_anchors    —— 承重列内容锚：{列名: [必须原样出现在该单元格内的
#                    子串]}（锚取自矩阵原文；改写承重语义即红）；
# enum_ref        —— "N 值" 计数行的 (module, 枚举名)；期望值本身从
#                    CSV vocabulary 的 "(N 值)" 注解反向解析，不硬编码。
ROW_EXPECTATIONS: dict[str, dict] = {
    # canonical 权威域（harness_kernel）
    "turn_phase": dict(
        site="app/services/harness_kernel/models.py",
        principal_vocab="TurnPhase",
        site_symbols=["TERMINAL_PHASES"],
        import_pairs=[
            ("app.services.harness_kernel.models", "TurnPhase"),
            ("app.services.harness_kernel.runtime", "GISSessionRuntime.end_turn"),
        ],
        text_refs=[],
        cell_anchors={
            "authoritative_writer": ["end_turn"],
            "persisted_in": ["turns[].phase"],
        },
    ),
    "turn_status": dict(
        site="app/services/harness_kernel/models.py",
        principal_vocab="TurnStatus",
        site_symbols=["TERMINAL_PHASES"],
        import_pairs=[
            ("app.services.harness_kernel.models", "TurnStatus"),
            ("app.services.harness_kernel.runtime", "GISSessionRuntime.end_turn"),
        ],
        text_refs=[],
        cell_anchors={
            "authoritative_writer": ["GISSessionRuntime.end_turn"],
            "persisted_in": ["turns[].status"],
        },
    ),
    "turn_event": dict(
        site="app/services/harness_kernel/models.py",
        principal_vocab="PlanDecision",
        site_symbols=["EVENT_KINDS"],
        import_pairs=[
            ("app.services.harness_kernel.models", "PlanDecision"),
            ("app.services.harness_kernel.models", "EVENT_KINDS"),
            # 矩阵写 GISSessionRuntime._event；_event 是 runtime.py 模块级
            # seam 函数（已知登记漂移，见模块 docstring）
            ("app.services.harness_kernel.runtime", "_event"),
            ("app.services.harness_kernel.runtime", "GISSessionRuntime"),
        ],
        text_refs=[],
        cell_anchors={
            "authoritative_writer": ["_event"],
            "persisted_in": ["decisions[]"],
        },
    ),
    "map_mutation_event": dict(
        site="app/services/harness_kernel/models.py",
        principal_vocab="map_mutated",
        site_symbols=["EVENT_KINDS", "RESERVED_EVENT_KINDS"],
        import_pairs=[
            ("app.services.harness_kernel.runtime",
             "GISSessionRuntime.record_map_mutation"),
        ],
        # emit 缝 = gis_world_state/mutation.py apply_gis_mutation post-success
        text_refs=[
            ("app/services/gis_world_state/mutation.py", "record_map_mutation"),
            ("app/services/gis_world_state/mutation.py", "def apply_gis_mutation"),
        ],
        cell_anchors={
            "authoritative_writer": ["record_map_mutation", "apply_gis_mutation"],
            "persisted_in": ["decisions[]"],
        },
    ),
    # 投影域（gis_harness 派生）
    "v7_runtime_phase": dict(
        site="app/services/gis_harness/runtime_state_machine.py",
        principal_vocab="RuntimePhase",
        site_symbols=["derive_runtime_state", "commit_runtime_context"],
        import_pairs=[
            ("app.services.harness_kernel.phase_adapter", "project_runtime_phase"),
        ],
        text_refs=[],
        cell_anchors={
            "authoritative_writer": ["derive_runtime_state", "commit_runtime_context"],
            "projection_of": ["phase_adapter.project_runtime_phase"],
            "persisted_in": ["gis_chapter"],
        },
        enum_ref=("app.services.gis_harness.runtime_state_machine",
                  "RuntimePhase"),
    ),
    "stage_state": dict(
        site="app/services/gis_harness/workflow_instance.py",
        principal_vocab="StageState",
        site_symbols=["maybe_update_workflow_instance"],
        import_pairs=[
            ("app.services.harness_kernel.phase_adapter", "render_stage_view"),
        ],
        text_refs=[],
        cell_anchors={
            "authoritative_writer": ["maybe_update_workflow_instance"],
            "projection_of": ["phase_adapter.render_stage_view"],
            "persisted_in": ["gis_chapter"],
        },
        enum_ref=("app.services.gis_harness.workflow_instance", "StageState"),
    ),
    "goal_node_status": dict(
        site="app/services/gis_harness/goal_graph.py",
        principal_vocab="GoalNodeStatus",
        site_symbols=[],  # 图构造派生（无 turn 级写者）
        import_pairs=[
            ("app.services.harness_kernel.phase_adapter", "render_goal_view"),
        ],
        text_refs=[],
        cell_anchors={
            "projection_of": ["phase_adapter.render_goal_view"],
        },
        enum_ref=("app.services.gis_harness.goal_graph", "GoalNodeStatus"),
    ),
    "requirement_state": dict(
        site="app/services/gis_harness/goal_satisfaction/contracts.py",
        principal_vocab="RequirementState",
        site_symbols=[],
        import_pairs=[
            ("app.services.gis_harness.goal_satisfaction.contracts",
             "RequirementState"),
            ("app.services.gis_harness.goal_satisfaction.contracts",
             "HarnessSignal"),
        ],
        text_refs=[],
        cell_anchors={"projection_of": ["TurnPhase.verifying"]},
    ),
    # 独立域 / host 投影
    "tracker_task_status": dict(
        site="app/services/task_tracker.py",
        principal_vocab=None,  # vocabulary 是字面值清单，由 EXTRA_CHECKS 钉
        site_symbols=[],
        import_pairs=[
            ("app.services.task_tracker", "TaskStatus"),
            ("app.services.task_tracker", "StepStatus"),
        ],
        # PiBridge._settle_turn_outcome（单 seam）由 abort/settle AST 契约钉
        text_refs=[],
        cell_anchors={"authoritative_writer": ["PiBridge._settle_turn_outcome"]},
    ),
    "workflow_instance_status": dict(
        site="app/services/workflow_runtime/contracts.py",
        principal_vocab="InstanceStatus",
        site_symbols=["INSTANCE_TERMINAL_STATUSES"],
        import_pairs=[
            ("app.services.workflow_runtime.contracts", "InstanceStatus"),
        ],
        # fencing CLAIM_MISMATCH/heartbeat：driver/service/recovery/store
        text_refs=[
            ("app/services/workflow_runtime/store.py", "CLAIM_MISMATCH"),
            ("app/services/workflow_runtime/driver.py", "CLAIM_MISMATCH"),
        ],
        cell_anchors={
            "authoritative_writer": ["CLAIM_MISMATCH"],
            "persisted_in": ["workflow"],
        },
    ),
    "mission_state": dict(
        site="app/services/mission_runtime/contracts.py",
        # 已知文档漂移：CSV 写 MissionStatus，代码实体为 MissionState
        principal_vocab="MissionStatus",
        site_symbols=[],
        import_pairs=[
            ("app.services.mission_runtime.contracts", "MissionState"),
        ],
        text_refs=[],
        cell_anchors={
            "authoritative_writer": ["mission_runtime"],
            "persisted_in": ["mission"],
        },
    ),
    "abort_source": dict(
        site="app/agent_pi_bridge.py",
        principal_vocab=None,  # 字面值 user/system/policy 由 AST 契约测试钉
        site_symbols=["_TURN_ABORT_SOURCES"],
        import_pairs=[],  # 桥模块重（fastapi/redis），一律 AST 级断言
        text_refs=[],
        cell_anchors={"authoritative_writer": ["PiBridge.abort"]},
    ),
    "refusal_signal": dict(
        site="app/services/harness_kernel/runtime.py",
        principal_vocab="turn_refusal_candidate",
        site_symbols=[],
        import_pairs=[
            ("app.services.harness_kernel.runtime",
             "GISSessionRuntime.turn_refusal_candidate"),
        ],
        text_refs=[],
        cell_anchors={"authoritative_writer": ["GISSessionRuntime"]},
    ),
}


# ── CSV 解析（全部行） ──────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _load_matrix() -> tuple[list[str], tuple[dict[str, str], ...]]:
    with MATRIX.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        header = list(reader.fieldnames or [])
        rows = tuple(
            {k: (v or "").strip() for k, v in row.items() if k is not None}
            for row in reader
        )
    return header, rows


def _rows_by_domain() -> dict[str, dict[str, str]]:
    _header, rows = _load_matrix()
    return {r["state_domain"]: r for r in rows}


def test_matrix_csv_parses_with_expected_shape():
    """CSV 可解析、7 列齐全、13 行、主键唯一、关键列非空。"""
    header, rows = _load_matrix()
    assert header == EXPECTED_HEADER, f"矩阵表头漂移: {header}"
    assert len(rows) == 13, f"矩阵行数漂移: {len(rows)}（增删行必须同步期望表）"
    domains = [r["state_domain"] for r in rows]
    assert len(set(domains)) == len(domains), "state_domain 主键重复"
    for row in rows:
        for col in EXPECTED_HEADER:
            assert row[col], f"行 {row['state_domain']} 的 {col} 列为空（含 '-' 占位）"


def test_matrix_rows_and_expectations_cover_bidirectionally():
    """矩阵每行都接入断言；断言表无游离条目——增删行必须双向同步。"""
    domains = {r["state_domain"] for r in _load_matrix()[1]}
    assert domains == set(ROW_EXPECTATIONS), (
        f"矩阵与期望表脱钩: csv_only={domains - set(ROW_EXPECTATIONS)}, "
        f"table_only={set(ROW_EXPECTATIONS) - domains}"
    )


# ── 结构层：每行 definition_site / 实体 / 记号全量断言 ──────────────────────


def _defined_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _has_dotted(module: object, dotted: str) -> bool:
    obj = module
    for part in dotted.split("."):
        if not hasattr(obj, part):
            return False
        obj = getattr(obj, part)
    return True


@pytest.mark.parametrize("domain", sorted(ROW_EXPECTATIONS))
def test_matrix_row_matches_runtime_ownership(domain):
    """单行矩阵 ↔ 运行时实体：定义点存在、实体存在、归属记号在位。

    篡改 CSV 任何一行的 definition_site / vocabulary 主实体 / "N 值"
    注解，或重命名对应代码实体而不改矩阵，本测试即红。
    """
    row = _rows_by_domain()[domain]
    exp = ROW_EXPECTATIONS[domain]

    # vocabulary 列与代码主实体锚链（文档侧漂移在 KNOWN_VOCAB_DRIFTS 登记）
    if exp["principal_vocab"] is not None:
        assert exp["principal_vocab"] in row["vocabulary"], (
            f"{domain}: vocabulary 与代码主实体 "
            f"{exp['principal_vocab']} 脱钩: {row['vocabulary']!r}"
        )

    # definition_site：路径一致且真实存在；行内附带的符号必须在文件中有定义
    site_rel = row["definition_site"].split()[0]
    assert site_rel == exp["site"], (
        f"{domain}: definition_site 漂移: {site_rel!r} != {exp['site']!r}"
    )
    site_file = REPO / site_rel
    assert site_file.is_file(), f"{domain}: definition_site 指向不存在的文件 {site_rel}"
    defined = _defined_names(site_file)
    for token in row["definition_site"].split()[1:]:
        assert token in defined, (
            f"{domain}: definition_site 引用的符号 {token} 不在 {site_rel}"
        )
    for sym in exp["site_symbols"]:
        assert sym in defined, f"{domain}: 定义点缺少符号 {sym}（{site_rel}）"

    # 导入级实体存在性（含类方法级 dotted 路径）
    for module_name, attr in exp["import_pairs"]:
        module = importlib.import_module(module_name)
        assert _has_dotted(module, attr), (
            f"{domain}: 运行时实体 {module_name}.{attr} 不存在"
        )

    # 跨模块 seam / fencing 记号（文件域文本引用）
    for ref_rel, token in exp["text_refs"]:
        ref_file = REPO / ref_rel
        assert ref_file.is_file(), f"{domain}: 引用文件不存在 {ref_rel}"
        assert token in ref_file.read_text(encoding="utf-8", errors="replace"), (
            f"{domain}: {ref_rel} 缺少记号 {token!r}"
        )

    # 承重列内容锚（矩阵原文锚定：改写 authoritative_writer /
    # projection_of / persisted_in 的承重语义即红）
    for col, tokens in exp.get("cell_anchors", {}).items():
        for token in tokens:
            assert token in row[col], (
                f"{domain}: {col} 承重锚 {token!r} 从矩阵单元格消失: "
                f"{row[col]!r}"
            )

    # CSV 侧 "(N 值)" 注解反向解析 ↔ 枚举成员数（注解漂移即红）
    if exp.get("enum_ref") is not None:
        m = re.search(r"\((\d+)\s*值\)", row["vocabulary"])
        assert m, f"{domain}: vocabulary 缺少 '(N 值)' 注解: {row['vocabulary']!r}"
        expected_n = int(m.group(1))
        module_name, attr = exp["enum_ref"]
        enum_cls = getattr(importlib.import_module(module_name), attr)
        assert len(enum_cls) == expected_n, (
            f"{domain}: 矩阵标注 {expected_n} 值，实际 {len(enum_cls)} 成员"
        )

    # CSV 侧值清单反向解析：vocabulary 括号内斜杠清单与裸斜杠清单
    # （completed/failed/…、user/system/policy、running/completed/…、
    # seq/event_id/causal_id）的小写词必须原样出现在 definition_site
    # 文件文本中（CamelCase 斜杠对如 RequirementState/HarnessSignal
    # 不属值清单，由 import_pairs 钉扎）
    site_text = site_file.read_text(encoding="utf-8", errors="replace")
    value_groups = re.findall(r"\(([^()]*/[^()]*)\)", row["vocabulary"])
    if re.fullmatch(r"[a-z]+(?:/[a-z_]+)+", row["vocabulary"]):
        value_groups.append(row["vocabulary"])  # 裸清单：abort_source / tracker
    for group in value_groups:
        for token in group.split("/"):
            token = token.strip()
            if re.fullmatch(r"[a-z_]{3,}", token):
                assert token in site_text, (
                    f"{domain}: vocabulary 值清单词 {token!r} 不在 {site_rel}"
                )

    extra = EXTRA_CHECKS.get(domain)
    if extra is not None:
        extra(row)


# ── 行级词表值断言（vocabulary 里的字面值清单） ─────────────────────────────


def _check_turn_status_values(_row):
    from app.services.harness_kernel import models as hk_models

    status_values = set(get_args(hk_models.TurnStatus))
    # 矩阵: completed/failed/cancelled/aborted/refused/interrupted —— 终态族
    # 与 TERMINAL_PHASES 1:1（running 是唯一非终态）
    terminals = status_values - {"running"}
    assert terminals == set(hk_models.TERMINAL_PHASES), (
        f"终态 status 与 TERMINAL_PHASES 不再 1:1: {terminals}"
    )


def _check_turn_event_fields(_row):
    from app.services.harness_kernel import models as hk_models

    fields = set(getattr(hk_models.PlanDecision, "model_fields", None) or
                 hk_models.PlanDecision.__fields__)
    assert {"seq", "event_id", "causal_id", "kind", "turn_id"} <= fields, (
        f"PlanDecision 日志身份字段漂移: {sorted(fields)}"
    )
    assert "map_mutated" in hk_models.EVENT_KINDS


def _check_map_mutation_graduated(_row):
    from app.services.harness_kernel import models as hk_models

    # 矩阵: map_mutated (ex-reserved) —— 已从 RESERVED 转正
    assert "map_mutated" in hk_models.EVENT_KINDS
    assert "map_mutated" not in hk_models.RESERVED_EVENT_KINDS


def _check_tracker_status_values(_row):
    from app.services.task_tracker import StepStatus, TaskStatus

    expected = {"running", "completed", "failed", "cancelled"}
    assert expected <= {s.value for s in TaskStatus}, "TaskStatus 词表漂移"
    assert expected <= {s.value for s in StepStatus}, "StepStatus 词表漂移"


def _check_turn_phase_nonterminal(_row):
    from app.services.harness_kernel import models as hk_models

    phase_values = set(get_args(hk_models.TurnPhase))
    status_values = set(get_args(hk_models.TurnStatus))
    assert set(hk_models.TERMINAL_PHASES) <= phase_values
    # 生命周期起点在词表内（kernel begin_turn 驱动的第一相）
    assert "created" in phase_values
    assert "running" in status_values


EXTRA_CHECKS = {
    "turn_phase": _check_turn_phase_nonterminal,
    "turn_status": _check_turn_status_values,
    "turn_event": _check_turn_event_fields,
    "map_mutation_event": _check_map_mutation_graduated,
    "tracker_task_status": _check_tracker_status_values,
}


# ── 行为层：内存桩 harness 实例化生命周期关键路径 ───────────────────────────


@pytest.fixture
async def sid():
    from app.services.session_data import session_data_manager

    session_id = f"sess-g12-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(session_id)
    yield session_id
    await session_data_manager.clear_session(session_id)


async def _reload(sid):
    from app.services.session_plan import load_session_plan

    return await load_session_plan(sid)


def _turn(plan, turn_id):
    return next(t for t in plan.turns if t.turn_id == turn_id)


async def test_turn_phase_and_status_owned_by_end_turn(sid):
    """矩阵 turn_phase/turn_status 行：kernel 方法驱动 phase，end_turn 是
    唯一终态写者；终态一经写入不可被二次结算改写。"""
    from app.services.harness_kernel import get_runtime
    from app.services.harness_kernel.models import TERMINAL_PHASES

    rt = get_runtime(sid)
    await rt.begin_turn("t1", host="pi", message="画图")
    t1 = _turn(await _reload(sid), "t1")
    assert t1.status == "running"
    assert t1.phase not in TERMINAL_PHASES, "begin_turn 不得直接落终态 phase"

    await rt.end_turn("t1", host="pi", status="failed")
    t1 = _turn(await _reload(sid), "t1")
    assert (t1.status, t1.phase) == ("failed", "failed"), (
        "终态归属必须是 end_turn（唯一终态化器），且 status/phase 对齐"
    )

    # 幂等重结算 no-op：第二个 end_turn 不得改写终态
    await rt.end_turn("t1", host="pi", status="completed")
    t1 = _turn(await _reload(sid), "t1")
    assert t1.status == "failed", "终态被二次 end_turn 改写"


async def test_turn_event_journal_identity_fields(sid):
    """矩阵 turn_event 行：事件经单一 journal 落账，seq/event_id/causal_id
    身份字段齐备、归因字段指向发起 turn。"""
    from app.services.harness_kernel import get_runtime
    from app.services.harness_kernel.models import PlanDecision

    rt = get_runtime(sid)
    await rt.begin_turn("t1", host="pi", message="画图")
    assert await rt.record_map_mutation(
        mutation_id="mut-1", revision=7, kind="PatchLayerStyleIntent",
        actor="pi", origin="agent", turn_id="t1",
    )
    plan = await _reload(sid)
    row = next(d for d in plan.decisions if d.kind == "map_mutated")
    assert isinstance(row.seq, int) and row.seq >= 0
    assert row.event_id, "event_id 缺失（幂等键）"
    assert row.turn_id == "t1" and row.causal_id == "mut-1", "归因字段漂移"
    fields = set(getattr(PlanDecision, "model_fields", None) or
                 PlanDecision.__fields__)
    assert {"seq", "event_id", "causal_id"} <= fields


async def test_map_mutation_event_single_write_and_late_attribution(sid):
    """矩阵 map_mutation_event 行：每次成功 mutation 恰一条事件；同
    mutation_id 重放不双写；迟到回调归原 turn 且不重开终态。

    守护级抽查（矩阵归属修改时先红）；穷尽语义由
    tests/unit/test_f03_kernel_lifecycle_events.py 钉住。"""
    from app.services.harness_kernel import get_runtime

    rt = get_runtime(sid)
    await rt.begin_turn("t1", host="pi", message="画图")
    args = dict(mutation_id="mut-1", revision=7,
                kind="PatchLayerStyleIntent", actor="pi", origin="agent",
                turn_id="t1")
    assert await rt.record_map_mutation(**args)
    assert not await rt.record_map_mutation(**args), "同 mutation_id 双写"
    plan = await _reload(sid)
    assert len([d for d in plan.decisions if d.kind == "map_mutated"]) == 1

    # 迟到：turn 已终态后回调到达 → 归原 turn、detail.late、不重开
    await rt.end_turn("t1", host="pi", status="completed")
    await rt.begin_turn("t2", host="pi", message="下一问")
    assert await rt.record_map_mutation(
        mutation_id="mut-late", revision=9, kind="PatchLayerStyleIntent",
        actor="pi", origin="agent", turn_id="t1",
    )
    plan = await _reload(sid)
    late = next(d for d in plan.decisions if d.causal_id == "mut-late")
    assert late.turn_id == "t1", "迟到事件必须归原 turn"
    assert late.detail.get("late") is True
    t1, t2 = _turn(plan, "t1"), _turn(plan, "t2")
    assert t1.status == "completed", "迟到事件不得重开终态"
    assert t2.status == "running", "successor turn 被迟到事件污染"


async def test_refusal_signal_is_pure_read(sid):
    """矩阵 refusal_signal 行：turn_refusal_candidate 是纯读判定——
    不落账、零执行活动且无未解决澄清时不是候选。

    降级判定全语义（含 stale 澄清回归）由
    tests/unit/test_f03_kernel_lifecycle_events.py 钉住。"""
    from app.services.harness_kernel import get_runtime

    rt = get_runtime(sid)
    assert inspect.iscoroutinefunction(rt.turn_refusal_candidate)
    await rt.begin_turn("t1", host="pi", message="hi")
    before = await _reload(sid)
    assert await rt.turn_refusal_candidate("t1") is None, (
        "无章节的零执行 turn 不得成为 refusal 候选"
    )
    after = await _reload(sid)
    assert len(after.decisions) == len(before.decisions), (
        "refusal 判定必须纯读（矩阵: 读时判定，零落账）"
    )


# ── abort_source：桥模块重依赖，AST 级契约（不 import） ─────────────────────


def test_abort_source_ownership_matches_matrix():
    """矩阵 abort_source 行：来源词表 user/system/policy 单点记录；
    结算映射单表 user→cancelled、system/policy→aborted；进程内有界台账。"""
    bridge = REPO / "app" / "agent_pi_bridge.py"
    tree = ast.parse(bridge.read_text(encoding="utf-8", errors="replace"))

    mapping = None
    ledger_bound = None
    ledger_declared = False
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = {t.id for t in targets if isinstance(t, ast.Name)}
        if "_ABORT_SOURCE_STATUS" in names and isinstance(node.value, ast.Dict):
            mapping = ast.literal_eval(node.value)
        if "_TURN_ABORT_SOURCES_MAX" in names and isinstance(node.value,
                                                             ast.Constant):
            ledger_bound = node.value.value
        if "_TURN_ABORT_SOURCES" in names:
            ledger_declared = True

    assert ledger_declared, "_TURN_ABORT_SOURCES 台账声明缺失"
    assert mapping == {"user": "cancelled", "system": "aborted",
                       "policy": "aborted"}, (
        f"abort 结算映射单表漂移: {mapping!r}"
    )
    assert isinstance(ledger_bound, int) and 0 < ledger_bound <= 1024, (
        f"abort 台账失去有界性: {ledger_bound!r}"
    )

    bridge_methods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "PiBridge":
            bridge_methods = {
                n.name for n in node.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    assert {"abort", "_settle_turn_outcome"} <= bridge_methods, (
        f"PiBridge seam 缺失: abort/_settle_turn_outcome，现有 {sorted(bridge_methods)}"
    )


# ── 已知文档漂移登记（修复后同步删条目） ────────────────────────────────────

KNOWN_VOCAB_DRIFTS = {
    # CSV vocabulary 写 MissionStatus；app/ 全域零命中，代码实体为 MissionState
    "mission_state": ("MissionStatus", "MissionState"),
}


def test_known_vocabulary_drifts_are_still_registered():
    """登记表双向守护：漂移仍在则条目必须保留；任一侧修复则条目必须删。"""
    for domain, (csv_name, code_name) in KNOWN_VOCAB_DRIFTS.items():
        row = _rows_by_domain()[domain]
        assert csv_name in row["vocabulary"], (
            f"{domain}: CSV 已改用 {code_name}？请从 KNOWN_VOCAB_DRIFTS 删除登记"
        )
        module_name, attr = ROW_EXPECTATIONS[domain]["import_pairs"][0]
        module = importlib.import_module(module_name)
        assert hasattr(module, attr), (
            f"{domain}: 代码实体 {attr} 消失，登记过期"
        )
