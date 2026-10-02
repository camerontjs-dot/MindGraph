"""Embedder registry and text formatting tests."""

import sys
import types

import pytest

from mindgraph import embedders
from mindgraph.exceptions import EmbeddingError


def test_resolve_default_embedder():
    spec = embedders.resolve_embedder(None)
    assert spec.key == "minilm"
    assert spec.dimensions == 384


def test_explicit_device_is_optional(monkeypatch):
    monkeypatch.delenv("MINDGRAPH_DEVICE", raising=False)
    assert embedders.resolve_embedding_device() is None


@pytest.mark.parametrize("device", ["cpu", "mps", "cuda", "cuda:0", "cuda:12"])
def test_explicit_device_values(monkeypatch, device):
    monkeypatch.setenv("MINDGRAPH_DEVICE", device)
    assert embedders.resolve_embedding_device() == device


@pytest.mark.parametrize("device", ["", "auto", " cpu", "CPU", "cuda:-1", "mps:0", "cpu;other"])
def test_invalid_explicit_device_fails_without_a_loader(monkeypatch, device):
    monkeypatch.setenv("MINDGRAPH_DEVICE", device)
    with pytest.raises(EmbeddingError, match="Invalid MINDGRAPH_DEVICE"):
        embedders.load_sentence_embedder(embedders.resolve_embedder("minilm"))


@pytest.mark.parametrize("loader", [embedders.load_sentence_embedder, embedders.bootstrap_sentence_embedder])
def test_explicit_cpu_reaches_real_library_constructor(monkeypatch, loader):
    captured = {}

    class CapturingSentenceTransformer:
        def __init__(self, model_name_or_path, **kwargs):
            captured.update(kwargs)

    module = types.ModuleType("sentence_transformers")
    module.SentenceTransformer = CapturingSentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    monkeypatch.setenv("MINDGRAPH_DEVICE", "cpu")
    loader(embedders.resolve_embedder("minilm"))
    assert captured["device"] == "cpu"
    assert captured.get("local_files_only") is (True if loader is embedders.load_sentence_embedder else None)


def test_unavailable_explicit_device_does_not_retry_another_device(monkeypatch):
    calls = []

    class UnavailableSentenceTransformer:
        def __init__(self, *args, **kwargs):
            calls.append(kwargs)
            raise RuntimeError("requested backend unavailable")

    module = types.ModuleType("sentence_transformers")
    module.SentenceTransformer = UnavailableSentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    monkeypatch.setenv("MINDGRAPH_DEVICE", "cuda:0")
    with pytest.raises(EmbeddingError, match="requested backend unavailable"):
        embedders.load_sentence_embedder(embedders.resolve_embedder("minilm"))
    assert len(calls) == 1
    assert calls[0]["device"] == "cuda:0"


def test_resolve_bge_and_e5_prefixes():
    bge = embedders.resolve_embedder("bge-small")
    assert bge.query_prefix == "query: "
    e5 = embedders.resolve_embedder("e5-small")
    assert e5.passage_prefix == "passage: "


def test_unknown_embedder_raises():
    with pytest.raises(ValueError, match="Unknown embedder"):
        embedders.resolve_embedder("not-a-model")


def test_mainframe_template_prefixes():
    spec = embedders.resolve_embedder("minilm")
    query = embedders.format_query_text(
        spec, "regulated crosswalk", template="mainframe"
    )
    assert query.startswith("[intent=query]")
    passage = embedders.format_passage_text(
        spec,
        "chunk body",
        template="mainframe",
        title="GxP Note",
        domain="regulated-systems",
        doc_type="note",
    )
    assert "[domain=regulated-systems]" in passage
    assert "[type=note]" in passage
    assert "GxP Note" in passage


def test_load_sentence_embedder_uses_local_files_only(monkeypatch):
    """Loader must resolve models from the local cache only (no hub metadata)."""
    captured: dict[str, object] = {}
    monkeypatch.delenv("MINDGRAPH_DEVICE", raising=False)

    class FakeSentenceTransformer:
        def __init__(self, model_name_or_path, *args, **kwargs):
            captured["model_name_or_path"] = model_name_or_path
            captured["args"] = args
            captured["kwargs"] = kwargs

    fake_module = types.ModuleType("sentence_transformers")
    fake_module.SentenceTransformer = FakeSentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)

    spec = embedders.resolve_embedder("minilm")
    model = embedders.load_sentence_embedder(spec)

    assert isinstance(model, FakeSentenceTransformer)
    assert captured["model_name_or_path"] == "all-MiniLM-L6-v2"
    assert captured["kwargs"].get("local_files_only") is True
    assert "device" not in captured["kwargs"]


def test_load_sentence_embedder_missing_cache_raises_embedding_error(monkeypatch):
    class BoomSentenceTransformer:
        def __init__(self, *args, **kwargs):
            raise OSError("model not found in local cache")

    fake_module = types.ModuleType("sentence_transformers")
    fake_module.SentenceTransformer = BoomSentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)

    spec = embedders.resolve_embedder("minilm")
    with pytest.raises(EmbeddingError, match="local_files_only=True"):
        embedders.load_sentence_embedder(spec)


def test_bootstrap_sentence_embedder_is_the_explicit_acquisition_path(monkeypatch):
    captured: dict[str, object] = {}
    monkeypatch.delenv("MINDGRAPH_DEVICE", raising=False)

    class FakeSentenceTransformer:
        def __init__(self, model_name_or_path, *args, **kwargs):
            captured["model_name_or_path"] = model_name_or_path
            captured["args"] = args
            captured["kwargs"] = kwargs

    fake_module = types.ModuleType("sentence_transformers")
    fake_module.SentenceTransformer = FakeSentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)

    spec = embedders.resolve_embedder("minilm")
    model = embedders.bootstrap_sentence_embedder(spec)

    assert isinstance(model, FakeSentenceTransformer)
    assert captured["model_name_or_path"] == "all-MiniLM-L6-v2"
    assert captured["kwargs"] == {}
