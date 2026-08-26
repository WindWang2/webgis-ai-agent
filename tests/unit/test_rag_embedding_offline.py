"""#662：RAG embedding 模型加载的离线开关契约。

生产部署开启 `RAG_EMBEDDING_OFFLINE` 后，惰性模型加载必须完全离线
（`local_files_only=True`）：未缓存时有界快失败，而不是 #660 那种无超时的
TLS 挂起（它会把 asyncio.to_thread 的 worker 线程永久滞留，进而卡死进程
优雅关停）。默认关闭：开发机首用自动下载行为不变。

模型缓存预置方式（bake / 预热 volume / 构建期下载）是部署侧建议，见
docs/DEPLOYMENT.md —— 不在本契约内。
"""
import time

import pytest

from app.core.config import settings
from app.services.rag.faiss_store import FaissVectorStore


def _fresh_store(tmp_path):
    return FaissVectorStore(index_dir=str(tmp_path / "vectors"))


@pytest.fixture(autouse=True)
def _real_model_loader(monkeypatch):
    """换回真 ``_get_embedding_model``（conftest 的 offline guard 默认拦截）。

    本文件专门测加载器 wiring：构造器被 mock，或强制离线 + 空缓存使其
    秒级失败 —— 不会真加载模型，也不发起网络。guard 移除后这里自动
    变 no-op。
    """
    current = FaissVectorStore._get_embedding_model
    real = getattr(current, "_real_implementation", current)
    monkeypatch.setattr(FaissVectorStore, "_get_embedding_model", real)


def _spy_constructor(monkeypatch):
    """替身 SentenceTransformer：记录构造参数后立刻抛错（构造本身不测）。"""
    import sentence_transformers

    calls = {}

    def fake_st(model_name_or_path, *args, **kwargs):
        calls["name"] = model_name_or_path
        calls["kwargs"] = kwargs
        raise RuntimeError("construction stopped - wiring under test")

    monkeypatch.setattr(sentence_transformers, "SentenceTransformer", fake_st)
    return calls



def _hf_tls_trusted() -> bool:
    """/#1011 环境守卫：本机 TLS 劫持/企业代理会让 huggingface.co 证书校验
    失败（hostname mismatch）——离线边界的错误语义随之漂移（SSL 错误替代
    缓存/离线错误）。该前提破坏时显式 skip；CI/正常网络照常执行。"""
    import socket
    import ssl

    cached = _hf_tls_trusted.__dict__.get("_result")
    if cached is not None:
        return cached
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection(("huggingface.co", 443), timeout=3) as sock:
            with ctx.wrap_socket(sock, server_hostname="huggingface.co"):
                result = True
    except Exception:  # noqa: BLE001 - 不可达/证书不可信都视为前提破坏
        result = False
    _hf_tls_trusted.__dict__["_result"] = result
    return result

def test_offline_flag_constructs_model_with_local_files_only(
    monkeypatch, tmp_path
):
    """开关 on：必须以 local_files_only=True 构造 —— 不发起任何网络请求。"""
    calls = _spy_constructor(monkeypatch)
    monkeypatch.setattr(settings, "RAG_EMBEDDING_OFFLINE", True, raising=False)

    store = _fresh_store(tmp_path)
    with pytest.raises(RuntimeError, match="wiring under test"):
        store._get_embedding_model()

    assert calls["kwargs"].get("local_files_only") is True


def test_default_keeps_network_download_path(monkeypatch, tmp_path):
    """默认 off：不得传 local_files_only —— 开发机首用自动下载行为不变。"""
    calls = _spy_constructor(monkeypatch)
    monkeypatch.setattr(settings, "RAG_EMBEDDING_OFFLINE", False, raising=False)

    store = _fresh_store(tmp_path)
    with pytest.raises(RuntimeError, match="wiring under test"):
        store._get_embedding_model()

    assert "local_files_only" not in calls["kwargs"]


@pytest.mark.skipif(
    not _hf_tls_trusted(),
    reason="huggingface.co TLS 信任链被本机劫持破坏（#1011 环境假象）——离线错误语义漂移；CI 不受影响",
)
def test_offline_and_uncached_model_fails_fast(monkeypatch, tmp_path):
    """#662 核心验收：开关 on + 无缓存 → 有界快失败，模型不落 _model。

    HF 缓存指到空目录模拟"未缓存的部署"。离线模式根本不发起网络请求，
    所以失败是秒级的 —— 与 #660 的无超时 TLS 挂起形成对照。
    """
    import huggingface_hub.constants as hf_constants

    empty_home = tmp_path / "empty-hf-home"
    monkeypatch.setenv("HF_HOME", str(empty_home))
    # huggingface_hub 若已被 import，HF_HOME 环境变量不再重读 —— 直接钉常量。
    monkeypatch.setattr(hf_constants, "HF_HUB_CACHE", str(empty_home / "hub"))
    monkeypatch.setattr(settings, "RAG_EMBEDDING_OFFLINE", True, raising=False)

    store = _fresh_store(tmp_path)
    start = time.monotonic()
    with pytest.raises(Exception) as excinfo:
        store._get_embedding_model()
    elapsed = time.monotonic() - start

    assert elapsed < 10.0, f"离线加载应在秒级失败，实际 {elapsed:.1f}s"
    assert store._model is None, "失败的加载不得留下半初始化模型"
    # 报错要能指路：运维看到就知道是缓存缺失 + 离线开关的组合。
    message = str(excinfo.value).lower()
    assert any(
        word in message
        for word in ("cache", "offline", "local", "snapshot", "找到", "缓存")
    ), f"错误信息应指向缓存/离线语义，实际: {excinfo.value!r}"
