# TEST_MATRIX — GOAL 05

| 里程碑 | 测试文件 | 用例数 | 钉死内容 |
|---|---|---|---|
| M1 | tests/unit/gis_harness/recovery_policy/test_contracts.py | 16 | 词表封闭（registry↔enum 双射；11 粗类全覆盖；terminal 仅 3 码）、有界、确定性、payload 键集 |
| M1 | tests/unit/gis_harness/recovery_policy/test_diagnose.py | 40 | 429/5xx/权限/CRS 歧义/跨会话 ref/取消/预算/eligibility/render gap/goal gap 映射矩阵 + 绝不抛出 |
| M2 | tests/unit/gis_harness/recovery_policy/test_policy_guards.py | 24 | 风险×处置守卫（读自动/写确认/tier3 永不/未分类保守/幂等键豁免）、预算耗尽、取消即停、退避纯计算 |
| M3 | tests/unit/gis_harness/recovery_policy/test_wiring.py | 8 | TDS recovery 键、LLM note、step_error SSE、last_recovery 落账/幂等、旧形状零漂移、成功零新键 |
| M4 | tests/unit/lib/test_goal_verification_reconcile.py | 19 | false-PASS 红线（渲染缺失/科学不足永不 pass）、user-wins、矛盾披露、N/A 不拖低、确定性、垃圾输入 |
| M5 | tests/unit/gis_harness/recovery_policy/test_continuation.py | 13 | 选择性重试/保持已验证/跨会话+未知归属 fail-closed/预算与 terminal 门/有界确定 |
| M6 | tests/unit/gis_harness/recovery_policy/test_explain.py | 14 | 四段解释、四类处境（含优先级）、有界、绝不抛出 |
| M7 | tests/unit/evaluation/test_recovery_corpus.py | 36（含 29 参数化） | ≥25 案例、21 类别覆盖、ID 唯一稳定、全通过、双跑确定 |
| M8 | tests/unit/gis_harness/recovery_policy/test_perf_security_regression.py | 10 | 吞吐门、payload/note 有界、风暴无增长、零泄漏、签名面、形状门 |
| M9 | tests/unit/gis_harness/recovery_policy/test_e2e_recovery_gates.py | 5 | 3 恢复成功（瞬态/数据缺口/渲染对账翻转）+ 2 安全终止（跨会话/破坏性+取消）真实 seam |

合计新增 **185** 用例。存量回归锚点见 EVIDENCE.md（4+1 基线存量红已核对与本 PR 无关）。
