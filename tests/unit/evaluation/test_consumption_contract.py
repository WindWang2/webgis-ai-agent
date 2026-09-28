"""消费链活性契约（G08）：app/evaluation 的每个模块都可导入，注册面无断链。

消费链图谱（rg 实证，基线 de3f97c5）见同目录 README.md。本文件锁的是
「被消费的路径真实存在」这一底线：
- 32 个模块全部可导入（import 断链 = 立刻红）；
- ``__init__`` 的 re-export 面与源模块同一对象（重导出不漂移）；
- index 语料注册表：18 个内置语料、前缀跨语料唯一、manifest 形状稳定、
  版本哈希内容寻址、落盘/读回往返一致。
"""
from __future__ import annotations

import importlib
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parents[3] / "app" / "evaluation"


def test_all_evaluation_modules_importable():
    files = sorted(PKG_DIR.glob("*.py"))
    assert len(files) == 32, f"app/evaluation 模块数漂移: {len(files)}"
    for f in files:
        assert importlib.import_module(f"app.evaluation.{f.stem}") is not None


def test_package_reexport_surface_bound_to_source_modules():
    from app.evaluation import (
        GOLDEN_CASES,
        GISBenchmarkCase,
        GISBenchmarkRunner,
        NumericAssertion,
        ScriptStep,
        case,
        golden_cases,
        render_markdown,
        report,
        runner,
    )

    assert GOLDEN_CASES is golden_cases.GOLDEN_CASES
    assert GISBenchmarkCase is case.GISBenchmarkCase
    assert NumericAssertion is case.NumericAssertion
    assert ScriptStep is case.ScriptStep
    assert GISBenchmarkRunner is runner.GISBenchmarkRunner
    assert render_markdown is report.render_markdown


def test_builtin_corpus_manifest_shape_and_prefix_uniqueness():
    from app.evaluation.index import corpus_manifest

    manifest = corpus_manifest()
    assert len(manifest) == 18, f"内置语料数漂移: {sorted(manifest)}"
    prefixes = []
    for name, entry in manifest.items():
        assert set(entry) == {"count", "version_hash", "prefix", "group"}, name
        assert entry["count"] > 0, name
        assert entry["prefix"], name
        prefixes.append(entry["prefix"])
    assert len(prefixes) == len(set(prefixes)), "语料 id 前缀必须跨语料唯一"


def test_corpus_version_hash_is_content_addressed():
    from app.evaluation.case_matrix import build_matrix_cases
    from app.evaluation.index import corpus_version_hash

    cases = build_matrix_cases()
    assert corpus_version_hash(cases) == corpus_version_hash(list(cases))
    mutated = [c.model_copy(update={"query": c.query + " 变"}) for c in cases[:1]]
    assert corpus_version_hash(mutated + cases[1:]) != corpus_version_hash(cases)


def test_manifest_write_load_roundtrip(tmp_path):
    from app.evaluation.index import (
        corpus_manifest,
        load_manifest,
        write_manifest,
    )

    path = tmp_path / "manifest.json"
    write_manifest(path)
    assert load_manifest(path) == corpus_manifest()


def test_iter_all_cases_unique_ids_and_benchmark_typing():
    from app.evaluation.case import GISBenchmarkCase
    from app.evaluation.index import iter_all_cases

    cases = list(iter_all_cases())
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids)), "跨语料案例 id 冲突（前缀唯一性被破坏）"
    assert all(isinstance(c, GISBenchmarkCase) for c in cases)
    assert len(cases) >= 20000
