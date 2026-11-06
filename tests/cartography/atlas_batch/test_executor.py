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
