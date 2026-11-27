"""生成器侧回归锁：case_id 唯一性 / 契约兜底 / anchor 标注 / 输出编码。

这些测试直接调用 ``build_<domain>()``（不读冻结 JSON），锁定
``scripts/gen_science_oracles.py`` 的生成语义本身。
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from scripts.gen_science_oracles import (
    build_network,
    build_regression,
    build_science_v5,
    case,
)

NETWORK = build_network()


def test_regression_ols_has_fallback_case_when_list_rejected() -> None:
    """_ols_core 拒绝 list 入参时，OLS 数值面不能无痕消失。"""
    ols = [c for c in build_regression() if c["target"].endswith(":_ols_core")]
    assert ols, "OLS case 全部消失，且没有 ols_list_args_rejected 兜底"
    if not any(c["expect"]["kind"] != "error" for c in ols):
        assert any(
            c["id"] == "ols_list_args_rejected"
            and c["expect"].get("code") == "AttributeError"
            for c in ols
        )


def test_e2sfca_first_ids_are_unique() -> None:
    """zones∈{2,4,5} 的第二循环（cutoff=30）不得与第一循环（cutoff=15）撞 id。"""
    ids = [c["id"] for c in NETWORK if c["id"].startswith("e2sfca_weights_z")]
    dup = sorted(i for i, n in Counter(ids).items() if n > 1)
    assert not dup, f"重复 e2sfca case_id: {dup}"


def test_parse_metrics_ids_preserve_input_case() -> None:
    """'degree' 与 'DEGREE' 必须得到不同 case_id（大小写断言不可被覆盖）。"""
    ids = [c["id"] for c in NETWORK if c["id"].startswith("parse_metrics")]
    dup = sorted(i for i, n in Counter(ids).items() if n > 1)
    assert not dup, f"重复 parse_metrics case_id: {dup}"


def test_normalize_break_unit_ids_preserve_whitespace_variant() -> None:
    """'minutes' 与 ' Minutes ' 必须得到不同 case_id（空白修剪断言不可被覆盖）。"""
    ids = [c["id"] for c in NETWORK if c["id"].startswith("normalize_break_unit")]
    dup = sorted(i for i, n in Counter(ids).items() if n > 1)
    assert not dup, f"重复 normalize_break_unit case_id: {dup}"


def test_science_v5_echo_cases_are_anchored() -> None:
    """黄金路径 echo 的 case 必须标 anchor=True，与独立参考区分。"""
    entries = {c["id"]: c for c in build_science_v5()}
    echo_ids = {
        "lmc_driver_rho",
        "lmc_driver_variance_max",
        "lmc_driver_estimator",
        "lmc_driver_execution_plan",
        "sgs_driver_backend",
        "sgs_driver_std_max",
        "sgs_driver_uncertainty_estimator",
        "st_driver_variance_max",
        "st_driver_estimator",
        "st_driver_plan_variant",
        "pheno_sine_sos",
        "pheno_sine_peak_time",
        "pheno_sine_amplitude",
        "anomaly_z_hand_anchor",
        "pfaf_ml_distinct_codes",
        "pfaf_ml_code_distribution",
        "pfaf_ml_sample_cell_code",
        "topo_is_consistent",
        "topo_cycle_count",
        "topo_accumulation_violations",
    }
    missing = sorted(i for i in echo_ids if not entries[i].get("anchor"))
    assert not missing, f"echo 型 case 未标 anchor: {missing}"


def test_write_domain_uses_utf8_encoding(tmp_path, monkeypatch) -> None:
    """ensure_ascii=False 的载荷必须显式 UTF-8 落盘（不回落 locale 编码）。"""
    import scripts.gen_science_oracles as gen

    captured = {}
    real_write_text = Path.write_text

    def spy(self, data, encoding=None, errors=None, newline=None):
        captured["encoding"] = encoding
        return real_write_text(
            self, data, encoding=encoding, errors=errors, newline=newline
        )

    monkeypatch.setattr(Path, "write_text", spy)
    monkeypatch.setattr(gen, "DATA_DIR", tmp_path)
    monkeypatch.setattr(gen, "ROOT", tmp_path)
    gen.write_domain("utf8_probe", [case("non_ascii", "x:y", "σ⁰β⁰中文")])
    assert captured["encoding"] == "utf-8"
