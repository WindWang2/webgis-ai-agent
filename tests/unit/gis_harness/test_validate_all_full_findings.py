"""R2-P1（qc-loop round 4）回归锁：_validate_all 返回全量 findings。

历史缺陷：``_validate_all`` 末尾 ``return findings[:MAX_FINDINGS]`` ——
截断发生在状态判定（review 终审 F6 承诺"用全量 findings 判状态"）之前，
第 13 条起的 error 被静默丢弃、可能误判 complete。截断只允许发生在
``result.findings`` 的披露层。
"""
import app.services.gis_harness.completion.pipeline as pl
from app.services.gis_harness.completion.contracts import MapCompletionFinding


def test_validate_all_returns_full_list_beyond_disclosure_cap(monkeypatch):
    errs = [
        MapCompletionFinding(code=f"e{i:02d}", severity="error",
                             target="map", detail="x")
        for i in range(13)
    ]
    for name in ("validate_artifacts", "validate_layers", "validate_components",
                 "validate_layout", "validate_semantics"):
        monkeypatch.setattr(pl, name, lambda *a, **k: [])
    monkeypatch.setattr(pl, "validate_artifacts", lambda *a, **k: errs)
    import app.services.gis_harness.completion.validators.observation as obs_mod
    monkeypatch.setattr(obs_mod, "validate_map_model_compat", lambda *a, **k: [])
    monkeypatch.setattr(obs_mod, "validate_layer_visibility_quality",
                        lambda *a, **k: [])
    import app.services.gis_harness.map_critique as mc
    monkeypatch.setattr(mc, "critique_map_state", lambda *a, **k: [])

    findings = pl._validate_all(
        {"mapspec": {}, "descriptors": {}, "required_slots": []}, {})
    assert len(findings) == 13
    assert all(f.severity == "error" for f in findings)
