"""Embedding backends. The sentence-transformers path is exercised with a tiny, randomly
initialised model built locally (no download); it is skipped if torch/transformers are absent."""

import math

import pytest

from src.ai.embeddings import HashingEmbedder, SentenceTransformerEmbedder, get_embedder, tokenize
from src.utils.errors import EmbeddingError


def test_tokenizer_splits_identifiers():
    tokens = tokenize("Sig_VehicleSpeed and BrakeTorqueCoordinator")
    assert "sig_vehiclespeed" in tokens and "vehicle" in tokens and "speed" in tokens
    assert "braketorquecoordinator" in tokens and "torque" in tokens


def test_hashing_embedder_is_deterministic_and_normalised():
    emb = HashingEmbedder()
    a, b = emb.embed_query("wheel speed signal"), emb.embed_query("wheel speed signal")
    assert a == b and len(a) == 1024
    assert math.isclose(sum(v * v for v in a), 1.0, rel_tol=1e-6)
    close = sum(x * y for x, y in zip(a, emb.embed_query("wheel speed")))
    far = sum(x * y for x, y in zip(a, emb.embed_query("hydraulic modulator valve")))
    assert close > far


def test_missing_model_raises_actionable_error(settings):
    embedder = SentenceTransformerEmbedder("this-model/does-not-exist-anywhere")
    with pytest.raises(EmbeddingError) as exc:
        embedder.embed_query("x")
    assert "hashing" in exc.value.user_message()


def _tiny_model(path):
    pytest.importorskip("torch")
    transformers = pytest.importorskip("transformers")
    st = pytest.importorskip("sentence_transformers")
    vocab = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]"] + [w for w in
             "the wheel speed signal brake torque request vehicle state component port interface".split()]
    path.mkdir()
    (path / "vocab.txt").write_text("\n".join(vocab))
    tok = transformers.BertTokenizerFast(vocab_file=str(path / "vocab.txt"))
    cfg = transformers.BertConfig(vocab_size=len(vocab), hidden_size=32, num_hidden_layers=1, num_attention_heads=2,
                                  intermediate_size=64, max_position_embeddings=128)
    transformers.BertModel(cfg).save_pretrained(path)
    tok.save_pretrained(path)
    try:  # sentence-transformers >= 6
        from sentence_transformers.sentence_transformer.modules import Pooling, Transformer
    except ImportError:  # older releases
        from sentence_transformers.models import Pooling, Transformer
    word = Transformer(str(path), max_seq_length=64)
    dim_fn = getattr(word, "get_embedding_dimension", None) or getattr(word, "get_word_embedding_dimension")
    pool = Pooling(dim_fn())
    out = path.parent / "st_model"
    st.SentenceTransformer(modules=[word, pool]).save(str(out))
    return out


def test_sentence_transformer_backend_end_to_end(settings, sample_pdfs, tmp_path):
    model_dir = _tiny_model(tmp_path / "bert")
    settings.embedding_backend = "sentence-transformers"
    settings.embedding_model = str(model_dir)
    embedder = get_embedder(settings)
    vec = embedder.embed_query("wheel speed signal")
    assert len(vec) == 32 and math.isclose(sum(v * v for v in vec), 1.0, rel_tol=1e-4)

    from src.ai.retrieval import Retriever
    from src.services import document_service

    rev = document_service.upload_revision(settings, "a.pdf", sample_pdfs["A"].read_bytes(), new_document_title="EBC")
    stats = document_service.process(settings, rev["id"])
    assert stats["embedding"] == f"sentence-transformers:{model_dir}"
    hits = Retriever(settings).search("Sig_VehicleSpeed unit", [rev["id"]], top_k=3)
    assert hits and all(h.revision_id == rev["id"] for h in hits)
