"""executor 测试：工作目录/页缓存（不重渲染）/失败定向重试/取消清理/typed 失败。

诚实声明：mapspec_store 边界以 monkeypatch 返回内联合成数据（simulated）；
规划/编译（真实 SVG 孪生）/组装（真实 WeasyPrint）/QA/落盘全链真实执行。
"""
import json
import os

import pytest

from app.services.atlas_batch.executor import AtlasJobError, execute_atlas_job
from app.services.jobs.lifecycle import JobKind
from app.services.jobs.store import DurableJobStore

from .fixtures import district_document, spec_params

pytestmark = pytest.mark.cartography

weasyprint = pytest.importorskip("weasyprint")


def _make_job(sess, params=None):
    with sess() as db:
        job = DurableJobStore.create_sync(
            db,
            dispatch_spec={"task": "app.services.atlas_batch.tasks.run_atlas_export",
                           "args": [], "kwargs": {}},
            task_type="atlas_export",
            kind=JobKind.atlas_export,
            display_name="测试图册",
            parameters=spec_params(**(params or {})),
            session_id=(params or {}).get("session_id", "sess-atlas-test"),
            owner_id="atlas-test-user",
        )
        db.commit()
        return int(job.id)


def _get_job(sess, job_id):
    with sess() as db:
        rec = DurableJobStore.get_sync(db, job_id)
        db.expunge_all()
        return rec


@pytest.fixture()
def compile_counter(monkeypatch):
    """计数真实编译调用（缓存命中证明不重渲染）。"""
    from app.services.atlas_batch import page_compiler as pc

    state = {"count": 0, "fail_pages": set()}
    real = pc.compile_publication_page

    def counting(document, page, pub_ir, **kw):
        if page.page_id in state["fail_pages"]:
            from app.services.publication_export import PageCompileFailure

            raise PageCompileFailure("atlas_page_skipped",
                                     f"page {page.page_id}: ForcedFailure")
        state["count"] += 1
        return real(document, page, pub_ir, **kw)

    monkeypatch.setattr(pc, "compile_publication_page", counting)
    return state


def test_happy_path_small_atlas(atlas_exports, atlas_job_db, atlas_spec_store,
                                compile_counter):
    atlas_spec_store["set_document"](district_document(districts=2))
    job_id = _make_job(atlas_job_db)
    result = execute_atlas_job(job_id)

    # 封面 + 2 区页
    assert result["page_count_expected"] == 3
    assert result["page_count_pdf"] == 3
    assert result["page_count_rendered"] == 3
    assert result["qa"]["passed"] is True
    assert result["qa"]["classification_consistent"] is True
    assert compile_counter["count"] == 3
    # 产物落 exports + 属主 sidecar + 工作目录清理
    pdf = os.path.join(str(atlas_exports), result["artifacts"]["pdf"])
    manifest = os.path.join(str(atlas_exports), result["artifacts"]["manifest"])
    assert os.path.isfile(pdf) and os.path.getsize(pdf) > 1000
    assert os.path.isfile(manifest)
    assert os.path.isfile(pdf + ".owner")
    with open(pdf + ".owner", encoding="utf-8") as f:
        assert f.read() == "atlas-test-user"
    assert not os.path.exists(os.path.join(str(atlas_exports), "atlas_jobs",
                                           str(job_id)))
    # manifest 内容对账
    m = json.load(open(manifest, encoding="utf-8"))
    assert m["schema"] == "atlas_publication_manifest.v1"
    assert m["disclosures"]["simulated_data"] is True
    assert m["atlas"]["cross_page_comparability"] is True
    assert len(m["pages"]) == 3
    assert all(p["product_manifest_digest"].startswith("prodman-sha256:")
               for p in m["pages"])


def test_page_cache_prevents_recompile(atlas_exports, atlas_job_db,
                                       atlas_spec_store, compile_counter):
    atlas_spec_store["set_document"](district_document(districts=2))
    first = execute_atlas_job(_make_job(atlas_job_db))
    assert compile_counter["count"] == 3

    # 同一数据新 job：全部页指纹缓存命中 → 零编译
    second = execute_atlas_job(_make_job(atlas_job_db))
    assert compile_counter["count"] == 3  # 未增加
    assert second["cache_hits"] == 3
    assert second["page_count_pdf"] == 3
    assert first["artifacts"]["pdf"] != second["artifacts"]["pdf"]


def test_failed_page_evidence_and_targeted_retry(atlas_exports, atlas_job_db,
                                                 atlas_spec_store, compile_counter):
    atlas_spec_store["set_document"](district_document(districts=3))
    compile_counter["fail_pages"] = {"cat_金牛区"}
    job_id = _make_job(atlas_job_db)

    with pytest.raises(AtlasJobError) as ei:
        execute_atlas_job(job_id)
    assert ei.value.code == "atlas_pages_failed"
    assert "cat_金牛区" in ei.value.message

    # 证据已直写 result_summary（JobView page_status 投影源）
    rec = _get_job(atlas_job_db, job_id)
    pages = rec.result_summary["pages"]
    by_id = {p["page_id"]: p for p in pages}
    assert by_id["cat_金牛区"]["status"] == "failed"
    assert by_id["cat_锦江区"]["status"] == "rendered"
    assert rec.result_summary["failed_page_ids"] == ["cat_金牛区"]

    # 失败后工作目录清理（重试依赖缓存而非 attempt 目录）
    assert not os.path.exists(os.path.join(str(atlas_exports), "atlas_jobs",
                                           str(job_id)))

    # 定向重试：去掉失败注入 → 只有金牛区重新编译，其余缓存命中
    compile_counter["fail_pages"] = set()
    compile_counter["count"] = 0
    result = execute_atlas_job(job_id)  # attempt 1 同 job 重入（重试语义）
    assert compile_counter["count"] == 1
    assert result["page_count_pdf"] == 4
    assert result["cache_hits"] == 3


def test_missing_committed_spec_typed_failure(atlas_exports, atlas_job_db,
                                              atlas_spec_store, monkeypatch):
    async def none(*_a, **_k):
        return None

    monkeypatch.setattr(
        "app.services.mapspec_store.mapspec_store.get_mapspec", none)
    job_id = _make_job(atlas_job_db)
    with pytest.raises(AtlasJobError) as ei:
        execute_atlas_job(job_id)
    assert ei.value.code == "atlas_spec_missing"


def test_cancel_cleans_workdir(atlas_exports, atlas_job_db, atlas_spec_store,
                               compile_counter):
    from app.services.jobs.cancellation import OperationCancelled

    atlas_spec_store["set_document"](district_document(districts=2))

    class CancellingHandle:
        def __init__(self):
            self.n = 0

        def checkpoint(self):
            self.n += 1
            if self.n >= 2:
                raise OperationCancelled("cancelled by user", job_id="x")

        def progress(self, *a, **k):
            pass

        def ensure_not_cancelled(self):
            raise OperationCancelled("cancelled by user", job_id="x")

    job_id = _make_job(atlas_job_db)
    with pytest.raises(OperationCancelled):
        execute_atlas_job(job_id, job=CancellingHandle())
    # 取消后无临时文件泄漏
    assert not os.path.exists(os.path.join(str(atlas_exports), "atlas_jobs",
                                           str(job_id)))
    # 无产物（cancelled 不交付）
    assert not [n for n in os.listdir(str(atlas_exports))
                if n.startswith(f"atlas_{job_id}_")]


@pytest.mark.parametrize("max_entries", [None, 0])
def test_cache_evict_survives_concurrent_removal(tmp_path, monkeypatch, max_entries):
    """R2-P2：listdir 后共享缓存条目被其它 worker 逐出 → getsize OSError 不得逃逸。"""
    from app.services.atlas_batch import executor as ex_mod

    (tmp_path / "a.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "b.svg").write_text("<svg/>", encoding="utf-8")
    if max_entries is not None:
        # 0 强制走「条目超限」分支（第二条 getsize 汇总）
        monkeypatch.setattr(ex_mod, "_PAGE_CACHE_MAX_ENTRIES", max_entries)

    real_getsize = os.path.getsize
    root = str(tmp_path)

    def vanished_getsize(path):
        if str(path).startswith(root):
            raise FileNotFoundError(2, "No such file or directory", str(path))
        return real_getsize(path)

    monkeypatch.setattr(os.path, "getsize", vanished_getsize)
    # 未修复时 FileNotFoundError 逃逸（页循环只捕 PageCompileFailure）
    ex_mod._cache_evict(str(tmp_path))
    # 容量守卫 best-effort：并发删除面下静默放弃本轮逐出，文件未被误删
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.svg", "b.svg"]


def test_busy_assembly_typed_failure_keeps_cache(atlas_exports, atlas_job_db,
                                                 atlas_spec_store, compile_counter,
                                                 monkeypatch):
    from app.services.atlas_batch import executor as ex_mod
    from app.services.publication_export import PublicationBusyError

    atlas_spec_store["set_document"](district_document(districts=1))
    monkeypatch.setattr(ex_mod, "_ASSEMBLE_BUSY_BACKOFF_S", 0.01)
    real_assemble = ex_mod.assemble_pages_pdf

    def always_busy(*a, **k):
        raise PublicationBusyError("busy")

    monkeypatch.setattr(ex_mod, "assemble_pages_pdf", always_busy)

    job_id = _make_job(atlas_job_db)
    with pytest.raises(AtlasJobError) as ei:
        execute_atlas_job(job_id)
    assert ei.value.code == "atlas_pdf_busy"

    # 页已缓存：恢复组装后零重编译（只组装）
    monkeypatch.setattr(ex_mod, "assemble_pages_pdf", real_assemble)
    compile_counter["count"] = 0
    result = execute_atlas_job(job_id)
    assert compile_counter["count"] == 0
    assert result["cache_hits"] == 2
    assert result["page_count_pdf"] == 2


def test_atomic_write_cleans_tempfile_on_replace_failure(tmp_path, monkeypatch):
    """os.replace 失败不得残留无 .svg 后缀的临时文件（逐出按后缀回收）。"""
    from app.services.atlas_batch import executor as ex_mod

    target = tmp_path / "product.pdf"

    def _fail_replace(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(ex_mod.os, "replace", _fail_replace)
    with pytest.raises(OSError):
        ex_mod._atomic_write(str(target), b"<svg/>")
    assert list(tmp_path.iterdir()) == []


def test_cache_write_oserror_is_typed_page_failure(atlas_exports, atlas_job_db,
                                                   atlas_spec_store, monkeypatch):
    """缓存写 IO 失败（磁盘满/权限/并发删除）→ 页级证据 + typed failed。"""
    from app.services.atlas_batch import executor as ex_mod

    atlas_spec_store["set_document"](district_document(districts=1))

    def _disk_full(path, data):
        raise OSError(28, "No space left on device", str(path))

    monkeypatch.setattr(ex_mod, "_atomic_write", _disk_full)
    job_id = _make_job(atlas_job_db)
    with pytest.raises(AtlasJobError) as ei:
        execute_atlas_job(job_id)
    assert ei.value.code == "atlas_pages_failed"
    assert "atlas_page_io_error" in ei.value.message

    rec = _get_job(atlas_job_db, job_id)
    pages = rec.result_summary["pages"]
    assert pages
    assert all(p["status"] == "failed" for p in pages)
    assert all("atlas_page_io_error" in (p["error"] or "") for p in pages)


def test_page_record_svg_bytes_counts_utf8_bytes(atlas_exports, atlas_job_db,
                                                 atlas_spec_store, monkeypatch):
    """预算记账（96MB 累积）必须按 UTF-8 字节而非字符数。"""
    from app.services.atlas_batch import executor as ex_mod
    from app.services.publication_export import (
        PageCompileFailure,
        PageCompileOutcome,
    )

    atlas_spec_store["set_document"](district_document(districts=1))
    svg = '<svg xmlns="http://www.w3.org/2000/svg"><text>金牛区小学分布</text></svg>'

    def _fake_compile(document, page, pub_ir, **kw):
        if not page.cover:
            raise PageCompileFailure("atlas_page_skipped", "forced failure")
        return PageCompileOutcome(
            svg=svg, width_mm=page.paper.width_mm,
            height_mm=page.paper.height_mm, page=page)

    monkeypatch.setattr(ex_mod, "compile_atlas_page", _fake_compile)
    job_id = _make_job(atlas_job_db)
    with pytest.raises(AtlasJobError):
        execute_atlas_job(job_id)

    rec = _get_job(atlas_job_db, job_id)
    rendered = [p for p in rec.result_summary["pages"]
                if p["status"] == "rendered"]
    assert len(rendered) == 1
    assert rendered[0]["svg_bytes"] == len(svg.encode("utf-8"))
    assert rendered[0]["svg_bytes"] > len(svg)
