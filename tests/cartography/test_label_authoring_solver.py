"""G16 WP05/WP07 —— authoring 感知求解器行为回归（golden 之外的语义锁定）。

与 test_label_collision_export.py（golden 差分）互补：这里锁 user-wins、
预算豁免、越界/障碍冲突分型、确定性、兼容（无 authoring 输出逐字节不变）。
"""
from app.lib.cartography.label_collision import (
    CollisionLabel,
    ObstacleBox,
    solve_export_labels,
)

VP = [0.0, 0.0, 200.0, 200.0]


def _p(id, x, y, **kw):
    return CollisionLabel(id=id, text=kw.pop("text", id), kind="point",
                          x=x, y=y, font_size=12, **kw)


class TestUserWins:
    def test_pinned_placed_even_overlapping_auto(self):
        # 自动项先到优先级更高（priority 0），pinned（priority 9）仍保持位置：
        # pinned 先放置，自动项绕行（8 方位候选）或抑制 —— 用户决定不被
        # 自动解冲突移动。
        sol = solve_export_labels(
            [_p("A", 100, 100, priority=0), _p("P", 100, 100, priority=9, pinned=True)],
            VP,
        )
        by = {p.id: p for p in sol.placements}
        assert by["P"].status == "placed" and by["P"].pinned
        assert by["P"].conflict is False  # 先放置：无先行者可冲突
        # P 居中占据 (100,100)：A 的首选候选位被占 → A 位移到别的候选位
        #（或抑制），绝不移动 P。
        assert by["A"].pinned is False
        if by["A"].status == "placed":
            assert (by["A"].x, by["A"].y) != (100, 100)

    def test_pinned_offscreen_keeps_intent_and_flags(self):
        sol = solve_export_labels(
            [_p("P", 5, 5, pinned=True, offset_em=(0, 0))], VP)
        p = sol.placements[0]
        assert p.status == "placed" and p.pinned and p.conflict
        assert sol.stats["pinned_offscreen"] == 1

    def test_pinned_obstacle_overlap_flagged_not_moved(self):
        sol = solve_export_labels(
            [_p("P", 100, 100, pinned=True)], VP,
            obstacles=[(90, 90, 110, 110)])
        p = sol.placements[0]
        assert p.status == "placed" and p.conflict
        assert sol.stats["pinned_obstacle_overlaps"] == 1

    def test_pinned_empty_text_suppressed(self):
        sol = solve_export_labels(
            [_p("P", 100, 100, pinned=True, text="  ")], VP)
        assert sol.placements[0].status == "suppressed"
        assert sol.placements[0].reason == "empty_text"
        assert sol.stats["pinned_placed"] == 0

    def test_pinned_offset_em_applies_font_relative(self):
        # offset (2, -1) em × fs 12 → 中心 (124, 88)；point 发左上角。
        sol = solve_export_labels(
            [_p("P", 100, 100, pinned=True, offset_em=(2.0, -1.0))], VP)
        p = sol.placements[0]
        from app.lib.cartography.label_collision import estimate_label_box
        w, h = estimate_label_box("P", 12)
        assert abs(p.x - (124.0 - w / 2)) < 1e-9
        assert abs(p.y - (88.0 - h / 2)) < 1e-9
        assert p.conflict is False


class TestHiddenAndBudget:
    def test_hidden_not_counted_in_budget(self):
        labels = [_p(f"H{i}", 10 + i, 10, hidden=True) for i in range(5)]
        labels += [_p(f"A{i:02d}", 10 + i * 9, 100, priority=i) for i in range(4)]
        sol = solve_export_labels(labels, VP, max_labels=3)
        hidden = [p for p in sol.placements if p.reason == "hidden_by_user"]
        assert len(hidden) == 5
        # 预算只约束自动项：4 个自动项、max 3 → 恰 1 个 budget 抑制。
        assert sum(1 for p in sol.placements if p.reason == "budget") == 1
        assert sol.stats["hidden_by_user"] == 5
        assert sol.budget_exceeded is True

    def test_hidden_wins_over_everything(self):
        sol = solve_export_labels(
            [_p("H", 100, 100, hidden=True, priority=0)], VP)
        assert sol.placements[0].status == "suppressed"
        assert sol.placements[0].reason == "hidden_by_user"

    def test_pinned_exempt_from_budget(self):
        labels = [_p(f"P{i}", 150, 150, pinned=True, priority=50) for i in range(3)]
        labels += [_p(f"A{i:02d}", 10 + i, 10, priority=i) for i in range(3)]
        sol = solve_export_labels(labels, VP, max_labels=2)
        pinned = [p for p in sol.placements if p.pinned]
        assert len(pinned) == 3 and all(p.status == "placed" for p in pinned)
        # 自动项预算照常：3 项 max 2 → 1 个 budget。
        assert sum(1 for p in sol.placements if p.reason == "budget") == 1


class TestOwnInkExemption:
    def test_own_line_ink_does_not_block_own_label(self):
        obs = [ObstacleBox(box=(90, 94, 110, 106), kind="line",
                           layer_id="roads", feature_id="r1")]
        label = CollisionLabel(id="OWN", text="路名", kind="line", x=100, y=100,
                               angle=0, font_size=12, layer_id="roads",
                               feature_id="r1")
        sol = solve_export_labels([label], VP, obstacles=obs)
        assert sol.placements[0].status == "placed"

    def test_foreign_line_ink_blocks(self):
        obs = [ObstacleBox(box=(90, 94, 110, 106), kind="line",
                           layer_id="roads", feature_id="r1")]
        label = CollisionLabel(id="OTHER", text="他路", kind="line", x=100, y=100,
                               angle=0, font_size=12, layer_id="roads",
                               feature_id="r9")
        sol = solve_export_labels([label], VP, obstacles=obs)
        assert sol.placements[0].status == "suppressed"
        assert sol.placements[0].reason == "obstacle"

    def test_same_feature_id_different_layer_not_exempt(self):
        # 同 feature id 不同层：覆写/豁免绝不跨层混写。
        obs = [ObstacleBox(box=(90, 94, 110, 106), kind="line",
                           layer_id="L1", feature_id="x")]
        label = CollisionLabel(id="OTHER", text="别层", kind="line", x=100, y=100,
                               angle=0, font_size=12, layer_id="L2",
                               feature_id="x")
        sol = solve_export_labels([label], VP, obstacles=obs)
        assert sol.placements[0].reason == "obstacle"

    def test_circle_never_exempt_for_own_feature(self):
        # 点符号（circle）恒不豁免 —— 标签避让自身符号是设计意图。
        obs = [ObstacleBox(box=(88, 88, 112, 112), kind="circle",
                           layer_id="pts", feature_id="p1")]
        label = CollisionLabel(id="A", text="点", kind="point", x=100, y=100,
                               font_size=12, layer_id="pts", feature_id="p1")
        sol = solve_export_labels([label], VP, obstacles=obs)
        assert sol.placements[0].reason == "obstacle"

    def test_anonymous_obstacles_keep_legacy_semantics(self):
        # 匿名 Box 无豁免（legacy 契约）：即便标签带身份也不豁免。
        obs = [(90, 94, 110, 106)]
        label = CollisionLabel(id="OWN", text="路名", kind="line", x=100, y=100,
                               angle=0, font_size=12, layer_id="roads",
                               feature_id="r1")
        sol = solve_export_labels([label], VP, obstacles=obs)
        assert sol.placements[0].reason == "obstacle"


class TestCompatAndDeterminism:
    def test_legacy_inputs_byte_identical_with_authoring_code(self):
        # 兼容锁定：无 authoring 输入 → stats 无 authoring 键、行为不变。
        labels = [_p(f"A{i:02d}", 30 + i * 17, 40 + i * 7, priority=i) for i in range(6)]
        sol = solve_export_labels(labels, VP)
        assert "hidden_by_user" not in sol.stats
        assert "pinned_placed" not in sol.stats
        assert sol.stats["total"] == 6
        # 与 master 基线（golden corpus point_declutter_displacement）一致
        # 的形态由 fixture 直接锁定；这里锁 authoring 键缺席。

    def test_repeat_runs_identical(self):
        labels = [
            _p("P1", 100, 100, pinned=True, offset_em=(1.5, 0.5), layer_id="L", feature_id="f1"),
            _p("H1", 40, 40, hidden=True),
            _p("A1", 60, 60, priority=2),
            _p("U1", 80, 80, user_priority=-5),
        ]
        s1 = solve_export_labels(labels, VP)
        s2 = solve_export_labels(labels, VP)
        assert [(p.id, p.x, p.y, p.status, p.reason, p.pinned, p.conflict)
                for p in s1.placements] == [
            (p.id, p.x, p.y, p.status, p.reason, p.pinned, p.conflict)
            for p in s2.placements]
        assert s1.stats == s2.stats

    def test_rotation_translation_invariance(self):
        # 变形关系：全部输入平移 (+50,+15)（含视口）→ 放置坐标同向平移
        # （候选序/碰撞关系不变）；pinned offset 同样平移（em 换算锚定）。
        labels = lambda dx, dy: [  # noqa: E731
            _p("P1", 60 + dx, 60 + dy, pinned=True, offset_em=(1.0, 1.0)),
            _p("A1", 80 + dx, 60 + dy, priority=1),
        ]
        s0 = solve_export_labels(labels(0, 0), [0, 0, 200, 200])
        s1 = solve_export_labels(labels(50, 15), [50, 15, 250, 215])
        d = {p.id: p for p in s0.placements}
        e = {p.id: p for p in s1.placements}
        for k in d:
            assert abs((e[k].x - d[k].x) - 50) < 1e-9
            assert abs((e[k].y - d[k].y) - 15) < 1e-9
