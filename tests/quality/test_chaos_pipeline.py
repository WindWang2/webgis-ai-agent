"""Chaos：摄入竞态 / 写入中途取消 / LLM provider 病态行为（审计 05 #6 + §3.7 + §3.1）。

三个家族：

- **INGEST_DUP_RACE / INGEST_REGISTER_FAIL**：dedup 是 check-then-act
  （pipeline.py:157-166），审计 #6 指出并发同内容摄入可双双 miss。
  本套件用 Event 屏障**确定性**再现该窗口并钉死现状（两个 ref 都成功
  = 文档化残余风险；管线修复受本波次只读约束不做），同时验证失败
  一路的补偿回滚不留孤儿 ref。
- **CANCEL_MID_WRITE**：CURRENT_TOKEN 接缝在 chunk 边界注入取消 ——
  写入路径必须让 OperationCancelled 上抛且绝不晋升部分制品；
  「完成但已取消」的 finalize 竞态由 atomic_output(should_abort) 兜住。
- **LLM_TIMEOUT / LLM_MALFORMED_STREAM**：复用 provider 契约测试的
  httpx.MockTransport 接缝，确定性注入超时与截断流 —— 诚实失败，
  绝不假成功。
"""
import asyncio
import os

import httpx
import pytest

from app.services.artifact_registry import list_artifacts
from app.services.data_ingest.pipeline import (
    compute_payload_fingerprint,
    get_ingest_pipeline,
    reset_ingest_pipeline,
)
from app.services.session_data import session_data_manager
from tests.fixtures.chaos import chaos, reset_journal


@pytest.fixture(autouse=True)
def _reset():
    reset_ingest_pipeline()
    reset_journal()
    yield
    reset_ingest_pipeline()
    reset_journal()


def _fc(n=3):
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "geometry": {
                    "type": "Point",
                    "coordinates": [116.0 + i * 0.1, 39.0],
                },
                "properties": {"v": f"p{i}"},
            }
            for i in range(n)
        ],
    }


# ── INGEST：并发同内容摄入的竞态窗（现状钉死 + 干净失败）──────────────────


@pytest.mark.asyncio
async def test_concurrent_duplicate_ingest_pins_check_then_act_race():
    """两路并发 dedup 检查都通过后才注册 → 现状：两个 ref 都成功。

    审计 #6 的残余风险以确定性 Event 屏障钉死（非 RNG、非 sleep）；
    若未来管线改为注册侧二次校验/唯一约束，本测试红 = 行为变更被审阅。
    """
    sid = "chaos-ingest-race"
    pipe = get_ingest_pipeline()
    with chaos("INGEST_DUP_RACE", wait_for=2) as dup:
        r1, r2 = await asyncio.gather(
            pipe.ingest(sid, _fc()),
            pipe.ingest(sid, _fc()),
        )

    assert dup.fired
    assert r1.ok and r2.ok
    assert not r1.duplicate and not r2.duplicate, "竞态窗内双方都未观测到对方"
    assert r1.ref_id != r2.ref_id
    assert "dedup-miss" in r1.steps_completed and "dedup-miss" in r2.steps_completed

    fp = compute_payload_fingerprint(_fc())
    records = await list_artifacts(sid)
    same_fp = [r for r in records if r.metadata.get("ingest_content_sha256") == fp]
    assert len(same_fp) == 2, "现状钉死：check-then-act 竞态下允许重复 ref"
    for r in (r1, r2):
        assert await session_data_manager.ref_exists(sid, r.ref_id)


@pytest.mark.asyncio
async def test_concurrent_ingest_register_failure_loses_cleanly():
    """竞态窗内一路注册被拒 → 补偿回滚其 ref，账本恰好一条、无孤儿。"""
    sid = "chaos-ingest-rollback"
    pipe = get_ingest_pipeline()
    with chaos("INGEST_DUP_RACE", wait_for=2):
        with chaos("INGEST_REGISTER_FAIL", fail_after=1) as reg:
            results = await asyncio.gather(
                pipe.ingest(sid, _fc()),
                pipe.ingest(sid, _fc()),
            )

    assert reg.fired
    ok = [r for r in results if r.ok]
    failed = [r for r in results if not r.ok]
    assert len(ok) == 1 and len(failed) == 1
    assert failed[0].error_code == "REGISTER_FAILED_ROLLED_BACK"
    # 失败方的 ref 已补偿删除 —— 绝不留孤儿 ref
    assert not await session_data_manager.ref_exists(sid, failed[0].ref_id)
    assert len(await list_artifacts(sid)) == 1


# ── CANCEL：写入中途取消，绝不晋升部分制品 ────────────────────────────────


def test_cancel_mid_chunked_write_promotes_nothing(tmp_path):
    """chunk 边界取消 → OperationCancelled 上抛、临时件被丢弃、缓存零痕迹。"""
    import tempfile

    from app.lib.artifact_cache import get_artifact, make_artifact_key, publish_artifact
    from app.lib.artifacts import discard_partial
    from app.lib.cancellation import OperationCancelled, cancellable

    out_path = os.path.join(tempfile.gettempdir(), "chaos-cancel-write-out.tif")
    with chaos("CANCEL_MID_WRITE", cancel_at_chunk=2) as fault:
        chunks = [f"CHUNK-{i}-PAYLOAD".encode() for i in range(5)]
        try:
            with open(out_path, "wb") as dst:
                for chunk in cancellable(chunks):  # 生产同款可取消循环
                    dst.write(chunk)
                    fault.gate()  # 第 2 块后取消
            # 取消后绝不到达晋升 —— lib 纪律：取消优先于任何产出
            publish_artifact(
                make_artifact_key(out_path, "op", {}), out_path, lambda: out_path
            )
            raise AssertionError("cancelled write must not reach publish_artifact")
        except OperationCancelled as exc:
            assert "chaos" in str(exc)
        finally:
            discard_partial(out_path)  # lib 纪律：finally 清理临时件

        assert fault.fired and fault.token.cancelled
        assert get_artifact(make_artifact_key(out_path, "op", {})) is None
        assert not os.path.exists(out_path), "部分写入的临时件必须被清理"


def test_cancel_arrived_at_finalize_discards_completed_artifact(tmp_path):
    """「写完但已取消」竞态：atomic_output(should_abort) 必须丢弃不晋升。"""
    from app.lib.artifacts import atomic_output

    final = str(tmp_path / "final.tif")
    with chaos("CANCEL_MID_WRITE", cancel_at_chunk=1) as fault:
        with pytest.raises(RuntimeError, match="discarded"):
            with atomic_output(final, should_abort=lambda: fault.token.cancelled) as part:
                with open(part, "wb") as f:
                    f.write(b"complete-but-cancelled-output")
                fault.gate()  # 取消恰好在 finalize 前到达
        assert fault.fired and fault.token.cancelled
        assert not os.path.exists(final), "已取消任务的产物绝不能 finalize"
        assert not os.path.exists(part), "临时件必须被丢弃"


# ── LLM：超时与截断流的诚实失败 ───────────────────────────────────────────


def _cfg():
    from app.services.chat.llm_client import LLMConfig

    return LLMConfig(base_url="http://provider-fake.example/v1", model="fake", api_key="k")


@pytest.mark.asyncio
async def test_llm_read_timeout_fails_honestly():
    """读取相位超时 → 显式抛 ReadTimeout（不在连接相位重试白名单内）。"""
    from app.services.chat import llm_client

    with chaos("LLM_TIMEOUT") as fault:
        with pytest.raises(httpx.ReadTimeout):
            await llm_client.call_llm(_cfg(), [])

    assert fault.fired


@pytest.mark.asyncio
async def test_llm_truncated_stream_never_fakes_done():
    """截断 SSE → ProviderStreamTruncated，绝不产生 done 帧（防假成功）。"""
    from app.services.chat import llm_client
    from app.services.chat.llm_client import ProviderStreamTruncated

    with chaos("LLM_MALFORMED_STREAM") as fault:
        events = []
        with pytest.raises(ProviderStreamTruncated):
            async for ev in llm_client.call_llm_stream(_cfg(), [], []):
                events.append(ev)

    assert fault.fired
    assert all(kind != "done" for kind, _ in events), "断流绝不允许包装成正常 done"
