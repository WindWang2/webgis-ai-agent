"""Exercise the dependency boundaries changed by the November lock refresh.

No downloaded models or external services: the embedding checkpoint is a tiny,
randomly initialized BERT built locally. These are compatibility tests, not an
embedding-quality benchmark or a substitute for the real-services lane.
"""
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts import check_dependency_bounds as bounds


REPO = Path(__file__).resolve().parents[1]


def test_filelock_recursion_and_torch_thread_timeout(tmp_path):
    """Torch's instrumented lock must honor another owner's recursive lifetime."""
    from concurrent.futures import ThreadPoolExecutor

    from filelock import FileLock, Timeout
    from torch.utils._filelock import FileLock as TorchFileLock

    lock_path = tmp_path / "model.lock"
    owner = FileLock(lock_path)

    def acquire_from_thread():
        with TorchFileLock(lock_path, timeout=0.05) as contender:
            return contender.is_locked

    with ThreadPoolExecutor(max_workers=1) as pool:
        with owner:
            with owner:
                assert owner.lock_counter == 2
                with pytest.raises(Timeout):
                    pool.submit(acquire_from_thread).result(timeout=10)
            assert owner.is_locked
            assert owner.lock_counter == 1
            with pytest.raises(Timeout):
                pool.submit(acquire_from_thread).result(timeout=10)
        assert not owner.is_locked
        assert pool.submit(acquire_from_thread).result(timeout=10)


def test_huggingface_cache_lock_excludes_process_and_preserves_metadata(tmp_path):
    """Exercise the Hub's real lock wrapper and cache read/write consumers."""
    import subprocess
    import sys
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from huggingface_hub._local_folder import (
        get_local_download_paths,
        read_download_metadata,
        write_download_metadata,
    )
    from huggingface_hub.utils import WeakFileLock

    filename = "model/config.json"
    paths = get_local_download_paths(tmp_path, filename)
    paths.file_path.write_text("{}", encoding="utf-8")
    contender = """
import sys
from filelock import Timeout
from huggingface_hub.utils import WeakFileLock
try:
    with WeakFileLock(sys.argv[1], timeout=0.05):
        print("acquired")
except Timeout:
    print("timeout")
"""

    def acquire_from_process():
        return subprocess.run(
            [sys.executable, "-c", contender, str(paths.lock_path)],
            capture_output=True, text=True, check=True, timeout=30,
        ).stdout.strip()

    with WeakFileLock(paths.lock_path):
        assert acquire_from_process() == "timeout"
    assert acquire_from_process() == "acquired"

    start = Barrier(4)
    valid = {(f"commit-{i}", f"etag-{i}") for i in range(4)}

    def cache_round_trip(index):
        start.wait(timeout=10)
        for _ in range(10):
            write_download_metadata(tmp_path, filename, f"commit-{index}", f"etag-{index}")
            metadata = read_download_metadata(tmp_path, filename)
            assert metadata is not None
            assert (metadata.commit_hash, metadata.etag) in valid

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(cache_round_trip, range(4)))


@pytest.mark.parametrize("xdg_override", [False, True])
def test_libpysal_data_directory_honors_platformdirs(tmp_path, monkeypatch, xdg_override):
    """The actual spatial-library consumer must keep isolated, writable data paths."""
    import subprocess
    import sys

    if sys.platform != "linux":
        pytest.skip("Linux XDG paths used by the deployment image")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    if xdg_override:
        data_root = tmp_path / "xdg-data"
        monkeypatch.setenv("XDG_DATA_HOME", str(data_root))
    else:
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        data_root = tmp_path / "home" / ".local" / "share"

    expected = data_root / "pysal"
    # libpysal initializes example-manager paths during its first import. Keep
    # those globals out of the pytest process, and inherit its dependency overlay.
    probe = """
import sys
from pathlib import Path
from libpysal.examples.base import get_data_home
expected = Path(sys.argv[1])
assert Path(get_data_home()) == expected
assert expected.is_dir()
(expected / "probe.txt").write_text("writable", encoding="utf-8")
assert Path(get_data_home()) == expected
assert (expected / "probe.txt").read_text(encoding="utf-8") == "writable"
"""
    result = subprocess.run(
        [sys.executable, "-c", probe, str(expected)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr


def test_dateutil_bound_protects_the_supported_pandas_line():
    """pandas 2.3.3 cannot consume the tzfile rewrite in dateutil 3/4."""
    declarations = [
        bounds.parse_requirements(REPO / "requirements.txt"),
        bounds.parse_pyproject_dependencies(REPO / "pyproject.toml"),
    ]
    for declared in declarations:
        spec = declared.get("python-dateutil")
        assert spec is not None, "The pandas/dateutil compatibility bound must be explicit"
        assert bounds.admits(spec, "2.9.0.post0")
        assert not bounds.admits(spec, "3.0.0")
        assert not bounds.admits(spec, "4.0.0")
    locked = bounds.parse_lock(REPO / "requirements.lock")["python-dateutil"]
    assert all(bounds.admits(declared["python-dateutil"], locked) for declared in declarations)


def test_pandas_dateutil_timezone_localization_and_dst():
    """Upstream pandas #71459 reproducer plus a DST transition round-trip."""
    winter = pd.Timestamp("2020-01-01").tz_localize("dateutil/US/Pacific")
    summer = pd.Timestamp("2020-07-01").tz_localize("dateutil/US/Pacific")
    assert winter.utcoffset() == timedelta(hours=-8)
    assert summer.utcoffset() == timedelta(hours=-7)
    utc = pd.date_range("2020-03-08T09:00:00Z", periods=3, freq="h")
    local = utc.tz_convert("dateutil/US/Pacific")
    assert local.hour.tolist() == [1, 3, 4]
    pd.testing.assert_index_equal(local.tz_convert("UTC"), utc)


def test_local_transformer_embedding_stack(tmp_path, monkeypatch):
    """Real Torch/Transformers/Hub/SentenceTransformer loading, without a download."""
    import socket

    import torch
    from sentence_transformers import SentenceTransformer
    from sentence_transformers.sentence_transformer import modules as models
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from tokenizers.pre_tokenizers import Whitespace
    from transformers import BertConfig, BertModel, PreTrainedTokenizerFast

    from app.core.config import settings
    from app.services.chat import tool_semantic_retrieval as semantic
    from app.services.rag import faiss_store

    def deny_network(*args, **kwargs):
        raise AssertionError("A local embedding checkpoint must not open a socket")

    monkeypatch.setattr(socket.socket, "connect", deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", deny_network)
    monkeypatch.setattr(socket, "create_connection", deny_network)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("HF_HUB_DISABLE_TELEMETRY", "1")
    monkeypatch.setenv("RAG_EMBEDDING_OFFLINE", "true")
    monkeypatch.setattr(settings, "RAG_EMBEDDING_OFFLINE", True)

    checkpoint = tmp_path / "bert"
    checkpoint.mkdir()
    vocab = {word: index for index, word in enumerate(
        ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]", "river", "map", "road"]
    )}
    tokenizer = Tokenizer(WordLevel(vocab=vocab, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = Whitespace()
    PreTrainedTokenizerFast(
        tokenizer_object=tokenizer, unk_token="[UNK]", pad_token="[PAD]",
        cls_token="[CLS]", sep_token="[SEP]", mask_token="[MASK]",
    ).save_pretrained(checkpoint)
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(7)
        BertModel(BertConfig(
            vocab_size=len(vocab), hidden_size=16, num_hidden_layers=1,
            num_attention_heads=2, intermediate_size=32, max_position_embeddings=32,
        )).save_pretrained(checkpoint)
    encoder = models.Transformer(
        str(checkpoint), max_seq_length=16,
        model_args={"local_files_only": True},
        tokenizer_args={"local_files_only": True},
    )
    local_model = tmp_path / "sentence-transformer"
    SentenceTransformer(modules=[encoder, models.Pooling(16)], device="cpu").save(str(local_model))

    monkeypatch.setattr(faiss_store, "_EMBEDDING_MODEL_NAME", str(local_model))
    current = faiss_store.FaissVectorStore._get_embedding_model
    monkeypatch.setattr(
        faiss_store.FaissVectorStore, "_get_embedding_model",
        getattr(current, "_real_implementation", current),
    )
    store = faiss_store.FaissVectorStore(index_dir=str(tmp_path / "vectors"))
    vectors = store.embed_texts(["river map", "road map"])
    assert vectors.shape == (2, 16)
    assert np.isfinite(vectors).all()
    np.testing.assert_allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-5)
    index = store._get_index(dim=16)
    index.add(vectors)
    scores, ids = index.search(vectors[:1], 1)
    assert ids.tolist() == [[0]]
    assert scores[0, 0] == pytest.approx(1.0, abs=1e-5)

    monkeypatch.setattr(semantic, "EMBEDDING_MODEL_NAME", str(local_model))
    monkeypatch.setattr(semantic, "_model_cache", {})
    semantic_vectors = np.asarray(semantic._default_embed(["river map", "road map"]))
    np.testing.assert_allclose(semantic_vectors, vectors, atol=1e-5)
