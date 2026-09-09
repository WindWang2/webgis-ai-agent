"""Quality V3 协调面测试：ownership / migration / ADR / preflight（W2–W4）。

正/负/边界三例齐备；全部基于真实仓库只读扫描或 tmp 目录合成夹具，
无网络、无 DB。
"""
from __future__ import annotations

import json
import shutil
import sys

import pytest

from app.lib.integration import adr as adr_mod
from app.lib.integration import migrations_coord as mig_mod
from app.lib.integration import ownership as ownership_mod

REPO = ownership_mod.REPO_ROOT


# ── ownership：真实仓库（正例：当前文档合法且 parity 双向成立） ─────────

def test_ownership_document_valid_on_real_repo():
    doc = ownership_mod.load_document(REPO)
    assert doc.version == 1
    assert doc.rules, "ownership 规则不得为空"
    assert ownership_mod.validate_document(doc, REPO) == []


def test_ownership_parity_with_artifact_graph():
    from app.lib.quality.artifact_graph import DECLARED

    doc = ownership_mod.load_document(REPO)
    errors = ownership_mod.parity_with_artifact_graph(
        doc, [e.artifact for e in DECLARED])
    assert errors == [], f"ownership↔DECLARED parity 破裂: {errors}"


def test_classify_known_surfaces():
    doc = ownership_mod.load_document(REPO)
    cases = {
        "docs/adr/0101-foo.md": "docs/adr/*",
        "migrations/versions/0034_new_thing.py": "migrations/versions/*",
        "CHANGELOG.md": "CHANGELOG.md",
        "docs/quality/QUALITY_MANIFEST.md": "docs/quality/QUALITY_MANIFEST.md",
        "docs/quality/certifications/DETERMINISM.md": "docs/quality/certifications/*",
        "app/tools/new_tool.py": "app/tools/*",
        "tests/quality/snapshots/realtime-contract.json": "tests/quality/snapshots/*",
    }
    for path in cases:
        rule = doc.classify(path)
        assert rule is not None, f"{path} 应有归属"
        assert rule.pattern == cases[path]


def test_classify_unowned_path_returns_none():
    doc = ownership_mod.load_document(REPO)
    assert doc.classify("some/random/file.xyz") is None


def test_risk_of_path_conflict_policies():
    doc = ownership_mod.load_document(REPO)
    assert ownership_mod.risk_of_path(doc, "CHANGELOG.md")[0] == "high"
    assert ownership_mod.risk_of_path(doc, "app/tools/x.py")[0] == "medium"
    assert ownership_mod.risk_of_path(doc, "app/services/whatever.py")[0] == "none"


# ── ownership：合成负例（词表 / 不相交 / 自指 / parity 破裂） ─────────────

def _doc_from_rules(rules: list[dict], **kw) -> ownership_mod.OwnershipDocument:
    return ownership_mod.OwnershipDocument(
        version=kw.get("version", 1),
        adr_watermark=kw.get("adr_watermark", 118),
        migration_watermark=kw.get("migration_watermark", 33),
        rules=tuple(ownership_mod.OwnershipRule(
            pattern=r["pattern"], owner=r["owner"], policy=r["policy"],
            suites=tuple(r.get("suites", ())),
        ) for r in rules),
    )


def test_validate_rejects_unknown_owner_and_policy():
    doc = _doc_from_rules([
        {"pattern": "a/*", "owner": "nonexistent-domain", "policy": "additive-only"},
        {"pattern": "b/*", "owner": "tools", "policy": "make-it-so"},
    ])
    errors = ownership_mod.validate_document(doc, REPO)
    assert any("owner" in e for e in errors)
    assert any("policy" in e for e in errors)


def test_validate_rejects_overlapping_patterns():
    doc = _doc_from_rules([
        {"pattern": "docs/quality/*", "owner": "quality",
         "policy": "regenerate-dont-edit"},
        {"pattern": "docs/quality/certifications/*", "owner": "quality",
         "policy": "regenerate-dont-edit"},
    ])
    errors = ownership_mod.validate_document(doc, REPO)
    assert any("同时命中" in e for e in errors), "嵌套 pattern 必须判为重叠"


def test_validate_rejects_self_referential_regenerate_pattern():
    doc = _doc_from_rules([
        {"pattern": "docs/integration/*", "owner": "integration",
         "policy": "regenerate-dont-edit"},
    ])
    errors = ownership_mod.validate_document(doc, REPO)
    assert any("自指" in e for e in errors)


def test_parity_fails_when_artifact_uncovered():
    doc = _doc_from_rules([
        {"pattern": "docs/unrelated/*", "owner": "quality",
         "policy": "regenerate-dont-edit"},
    ])
    errors = ownership_mod.parity_with_artifact_graph(
        doc, ["docs/quality/QUALITY_MANIFEST.md"])
    assert errors and "命中 regenerate pattern 数=0" in errors[0]


def test_parity_fails_when_pattern_covers_nothing():
    doc = _doc_from_rules([
        {"pattern": "docs/quality/QUALITY_MANIFEST.md", "owner": "quality",
         "policy": "regenerate-dont-edit"},
        {"pattern": "no/such/dir/*", "owner": "quality",
         "policy": "regenerate-dont-edit"},
    ])
    errors = ownership_mod.parity_with_artifact_graph(
        doc, ["docs/quality/QUALITY_MANIFEST.md"])
    assert any("未覆盖任何登记生成物" in e for e in errors)


def test_walk_repo_bounded_excludes_vendored_trees():
    files = ownership_mod._walk_repo_bounded(REPO)
    assert len(files) < ownership_mod._MAX_WALK_FILES
    assert not any(f.startswith("frontend/node_modules/") for f in files)
    assert "app/main.py" in files


# ── migration 协调：真实仓库 + 合成图 ──────────────────────────────────

def test_real_migration_graph_single_head():
    graph = mig_mod.scan(REPO)
    # R2-M1：不硬编码 head 名 —— 并发分支合法追加 0034+ 后本闸不得误红
    assert len(graph.heads) == 1, f"master 必须单头，实际 {graph.heads}"
    assert len(graph.revisions) >= 30
    # 文件名 ≠ revision id 的已知反例必须被 ScriptDirectory 正确解析
    assert any(i.revision == "g1109_legacy_owner" and
               i.file.endswith("g1109_legacy_owner_tokens.py")
               for i in graph.revisions)


def test_real_migration_watermark_current():
    # R2-M1：水位从 ownership 权威读取（allocator 合法推进后不得误红）
    from app.lib.integration.ownership import load_document

    doc = load_document(REPO)
    graph = mig_mod.scan(REPO)
    assert graph.revisions_over_watermark(doc.migration_watermark) == []
    under = graph.revisions_over_watermark(doc.migration_watermark - 1)
    assert under, "最高序号 revision 应恰好等于水位"


def _fake_graph(monkeypatch: pytest.MonkeyPatch, infos: list[mig_mod.MigrationInfo]):
    graph = mig_mod.MigrationGraph(revisions=tuple(infos))
    monkeypatch.setattr(mig_mod, "scan", lambda repo_root=None: graph)
    return graph


def test_heads_detects_multiple_heads(monkeypatch):
    def info(rev, down, file):
        return mig_mod.MigrationInfo(
            revision=rev,
            down_revision=(down,) if down else None,
            file=file, seq=mig_mod._seq_of_file(file), tables=frozenset())
    _fake_graph(monkeypatch, [
        info("0001_a", None, "0001_a.py"),
        info("0002_b", "0001_a", "0002_b.py"),
        info("0034_x", "0002_b", "0034_x.py"),
        info("0034_y", "0002_b", "0034_y.py"),  # 并发分支各挂同一 down
    ])
    graph = mig_mod.scan(None)
    assert sorted(graph.heads) == ["0034_x", "0034_y"]


def test_branch_collisions_same_file_and_sequence(tmp_path):
    """同文件 / 同 NNNN / 同 down fork 三类负例 + 兼容重叠正例。"""
    mig = tmp_path / "migrations/versions"
    mig.mkdir(parents=True)
    (mig / "0001_root.py").write_text(
        'revision: str = "0001_root"\n'
        'down_revision: Union[str, None] = None\n', encoding="utf-8")
    # 分支 A：0034_x 挂 0002_b；分支 B：0034_y 也挂 0002_b（同 NNNN + fork）
    (mig / "0034_x.py").write_text(
        'revision = "0034_x"\ndown_revision = "0002_b"\n', encoding="utf-8")
    (mig / "0034_y.py").write_text(
        'revision: str = "0034_y"\n'
        'down_revision: Union[str, None] = "0002_b"\n', encoding="utf-8")
    # 分支 B 的另一条不冲突 migration
    (mig / "0035_z.py").write_text(
        'revision = "0035_z"\ndown_revision = "0002_b"\n', encoding="utf-8")

    base = ["migrations/versions/0034_x.py"]
    collisions = mig_mod.branch_collisions(
        base, ["migrations/versions/0034_y.py", "migrations/versions/0035_z.py"],
        repo_root=tmp_path)
    assert collisions["sequence_collision"], "同 NNNN 必须检出"
    assert collisions["forked_down_revision"], "同 down 新增 revision 必须检出"
    assert "same_revision" not in collisions

    # 兼容重叠正例（Epic §15）：B 的 migration 线性链接在 A 的之后
    # （rebase 后的合法形态）→ 干净。同挂 0002_b 的 0035_z 会 fork 多 head，
    # 属于必须检出的冲突（上面已验证）。
    (mig / "0035_chain.py").write_text(
        'revision = "0035_chain"\ndown_revision = "0034_x"\n', encoding="utf-8")
    assert mig_mod.branch_collisions(
        base, ["migrations/versions/0035_chain.py"], repo_root=tmp_path) == {}


def test_branch_collisions_same_file(tmp_path):
    mig = tmp_path / "migrations/versions"
    mig.mkdir(parents=True)
    (mig / "0034_x.py").write_text(
        'revision = "0034_x"\ndown_revision = "0002_b"\n', encoding="utf-8")
    shared = "migrations/versions/0034_x.py"
    collisions = mig_mod.branch_collisions([shared], [shared], repo_root=tmp_path)
    assert collisions["same_revision"] == [
        {"base": shared, "other": shared, "detail": "双方都修改同一 migration 文件"}]


def test_parse_revision_fields_handles_merge_tuple_and_unparseable(tmp_path):
    mig = tmp_path / "migrations/versions"
    mig.mkdir(parents=True)
    merge = mig / "0036_merge.py"
    merge.write_text(
        'revision: str = "0036_merge"\n'
        'down_revision: Union[str, Sequence[str], None] = ("0034_x", "0034_y")\n',
        encoding="utf-8")
    assert mig_mod._parse_revision_fields(merge) == ("0036_merge", "0034_x")
    # 非 migration 文件 / 语法错误 → None（降级 unknown，不抛异常）
    junk = mig / "junk.py"
    junk.write_text("this is not python(((\n", encoding="utf-8")
    assert mig_mod._parse_revision_fields(junk) is None
    assert mig_mod._parse_revision_fields(mig / "missing.py") is None


def test_extract_tables_best_effort():
    tables = mig_mod._extract_tables(
        str(REPO / "migrations/versions/0033_geocompute_v6_cluster.py"))
    assert isinstance(tables, frozenset)  # 尽力而为：空集合法


# ── ADR 协调 ────────────────────────────────────────────────────────────

def test_real_adr_scan_parses_corpus():
    current = adr_mod.scan(REPO)
    assert len(current.adrs) > 120
    by_file = {a.file: a for a in current.adrs}
    info = by_file["docs/adr/0118-quality-reliability-security-platform-v2.md"]
    assert info.title_number == 118
    assert 104 in info.references, "前置 ADR 0104 必须被解析为引用"


def test_real_adr_duplicates_are_known_limitations_at_watermark():
    current = adr_mod.scan(REPO)
    dups = current.duplicates()
    assert 118 in dups and len(dups[118]) == 6, "存量 6×0118 必须可见"
    assert adr_mod.check_watermark(REPO, 118) == [], "水位之下不得红"


def test_watermark_reds_on_new_duplicate(tmp_path):
    docs = tmp_path / "docs/adr"
    docs.mkdir(parents=True)
    for name in ("0119-alpha.md", "0119-beta.md"):
        (docs / name).write_text(f"# ADR 0119 — {name}\n", encoding="utf-8")
    errors = adr_mod.check_watermark(tmp_path, 118)
    assert len(errors) == 1 and "0119" in errors[0]


def test_next_number_advances(monkeypatch):
    assert adr_mod.next_number(REPO)[0] > 118


def test_referenced_files_ratchet_no_new_dangling_on_master():
    """棘轮语义：master 存量悬空（7 条）在基线内不红；基线外新增 = 红。"""
    assert adr_mod.check_referenced_files(REPO) == [], (
        "master 出现基线外新增悬空链接")
    baseline = adr_mod.load_dangling_baseline(REPO)
    assert len(baseline) == 7, f"存量悬空应恰为 7 条，实际 {len(baseline)}"


def test_referenced_files_ratchet_reds_on_new_dangling(tmp_path):
    docs = tmp_path / "docs/adr"
    docs.mkdir(parents=True)
    (docs / "0100-new.md").write_text(
        "# ADR 0100 — x\n引用 `app/does_not_exist_xyz.py`\n", encoding="utf-8")
    (tmp_path / "docs/integration").mkdir(parents=True)
    (tmp_path / "docs/integration/adr-link-baseline.json").write_text(
        '{"dangling": []}\n', encoding="utf-8")
    errors = adr_mod.check_referenced_files(tmp_path)
    assert len(errors) == 1 and "does_not_exist_xyz" in errors[0]


def test_adr_reference_extraction_excludes_self():
    info = adr_mod.scan(REPO).adrs[0]
    assert info.number not in info.references


# ── allocate CLI（--dry-run，无副作用） ─────────────────────────────────

def test_allocate_migration_cli_dry_run(capsys):
    import subprocess

    proc = subprocess.run(
        [sys.executable, "scripts/allocate_migration.py", "quality_v3_probe",
         "--dry-run"], cwd=REPO, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # R2-M1：期望序号从当前 graph 推导（0034 被并发占用后 allocator
    # 合法输出 0035，闸不得惩罚协调流程本身）
    graph = mig_mod.scan(REPO)
    seqs = [i.seq for i in graph.revisions if i.seq is not None]
    expected_next = max(seqs) + 1
    assert f"{expected_next:04d}_quality_v3_probe" in proc.stdout
    assert graph.heads[0] in proc.stdout
    assert "migration_watermark" in proc.stdout


def test_allocate_migration_cli_rejects_bad_slug():
    import subprocess

    proc = subprocess.run(
        [sys.executable, "scripts/allocate_migration.py", "Bad Slug!"],
        cwd=REPO, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 2


def test_allocate_adr_cli_dry_run():
    import subprocess

    proc = subprocess.run(
        [sys.executable, "scripts/allocate_adr.py", "quality-v3-probe", "--dry-run"],
        cwd=REPO, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "adr_watermark" in proc.stdout or "watermark" in proc.stdout


# ── preflight 集成 ──────────────────────────────────────────────────────

def test_preflight_full_pass_on_master():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "check_integration_preflight",
        REPO / "scripts/check_integration_preflight.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    report = mod.run_preflight(REPO)
    assert report["ok"], json.dumps(report, ensure_ascii=False)[:2000]
    assert report["elapsed_s"] < 10, "quick preflight 必须秒级"
    assert {c["check"] for c in report["checks"]} == {
        "ownership_structure", "ownership_artifact_parity",
        "migration_heads", "adr_watermark", "generated_staleness",
        "generated_hand_edits"}


def test_find_hand_edits_detects_content_change_without_input_change():
    """手改检测正/负例：输入未变+内容变 = 手改；输入变 = 正常 stale，不算。"""
    from app.lib.quality.artifact_graph import (
        build_graph_state, content_fingerprint, find_hand_edits,
    )

    current = build_graph_state()
    target = "docs/quality/QUALITY_MANIFEST.md"
    assert content_fingerprint(target) is not None

    # 负例：账本与现状一致 → 无手改
    assert find_hand_edits(current) == []

    # 正例：输入指纹一致，content 指纹篡改 → 检出
    tampered = json.loads(json.dumps(current))
    tampered[target]["content_fingerprint"] = "0" * 64
    assert find_hand_edits(tampered) == [target]

    # 正常 stale 不算手改：输入指纹 + 内容指纹都变（再生成漂移）
    drifted = json.loads(json.dumps(current))
    drifted[target]["input_fingerprint"] = "1" * 64
    drifted[target]["content_fingerprint"] = "2" * 64
    assert find_hand_edits(drifted) == []

    # 缺失账本条目（新登记生成物）→ 不误报
    assert find_hand_edits({}) == []


def test_generated_entry_scope_and_version_defaults_additive():
    from app.lib.quality.artifact_graph import DECLARED

    for entry in DECLARED:
        assert entry.scope in ("global", "branch-local")
        assert isinstance(entry.generator_version, int) and entry.generator_version >= 1
        d = entry.as_dict()
        assert d["scope"] == entry.scope
        assert d["generator_version"] == entry.generator_version


def test_preflight_detects_watermark_violation(tmp_path):
    """合成仓库：adr 重复 > watermark → preflight 红（负例）。"""
    import importlib.util

    # 最小合成仓库：ownership 文档 + adr 重复 + 空 migrations
    (tmp_path / "docs/integration").mkdir(parents=True)
    shutil.copy(REPO / "docs/integration/ownership.json",
                tmp_path / "docs/integration/ownership.json")
    (tmp_path / "docs/adr").mkdir(parents=True)
    for name in ("0200-a.md", "0200-b.md"):
        (tmp_path / "docs/adr" / name).write_text(
            f"# ADR 0200 — {name}\n", encoding="utf-8")
    (tmp_path / "migrations").mkdir()
    (tmp_path / "migrations/versions").mkdir()
    (tmp_path / "app").mkdir()

    spec = importlib.util.spec_from_file_location(
        "check_integration_preflight",
        REPO / "scripts/check_integration_preflight.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    report = mod.run_preflight(tmp_path)
    assert not report["ok"]
    adr_check = next(c for c in report["checks"] if c["check"] == "adr_watermark")
    assert any("0200" in e for e in adr_check["errors"])
