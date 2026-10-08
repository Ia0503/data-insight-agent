import logging
import math
import time
from datetime import date

import pytest
from app.core.config import get_settings
from app.services import embeddings
from app.services.analysis import money, paid_date, previous_period


def test_calendar_and_money_boundaries():
    assert previous_period(date(2024, 3, 1), date(2024, 3, 31)) == (
        date(2024, 2, 1),
        date(2024, 2, 29),
    )
    assert previous_period(date(2026, 9, 5), date(2026, 9, 10)) == (
        date(2026, 8, 30),
        date(2026, 9, 4),
    )
    assert paid_date("2026-08-31T16:00:00Z") == date(2026, 9, 1)
    assert str(money("0.10") + money("0.20")) == "0.30"
    for text in ["NaN", "Infinity", "-1", "1.001", "1e3", ""]:
        with pytest.raises(ValueError):
            money(text)


def test_token_offsets_overlap_and_no_truncation():
    class Tokenizer:
        def __call__(self, content, **_):
            return {"offset_mapping": [(i, i + 1) for i in range(len(content))]}

    content = "中文证据。" * 20
    chunks = embeddings.token_chunks(content, Tokenizer(), 10, 2)
    assert all(c["content"] == content[c["start"] : c["end"]] for c in chunks)
    assert chunks[-1]["end"] == len(content)
    assert all(len(c["content"]) <= 10 for c in chunks)
    assert chunks[1]["start"] == chunks[0]["end"] - 2
    with pytest.raises(embeddings.EmbeddingError):
        embeddings.token_chunks(content, Tokenizer(), 10, 10)


@pytest.mark.parametrize("vectors", [[[0, 0]], [[math.nan, 1]], [[1]], []])
def test_invalid_vectors_fail_closed(vectors):
    with pytest.raises(embeddings.EmbeddingError):
        embeddings.validate_vectors(vectors, 1, 2)


def sleeping_worker(connection, options):
    time.sleep(15)


def test_inference_timeout_stops_child(monkeypatch):
    settings = get_settings().model_copy(update={"embedding_timeout_seconds": 5})
    provider = embeddings.LocalTransformersProvider(settings)
    monkeypatch.setattr(embeddings, "_model_worker", sleeping_worker)
    with pytest.raises(embeddings.EmbeddingError, match="超时"):
        provider.encode(["test"])
    assert provider._process is None and provider._connection is None
    provider.close()


def test_profile_tracks_processing_rules():
    settings = get_settings()
    first = embeddings.LocalTransformersProvider(settings).profile()
    second = embeddings.LocalTransformersProvider(
        settings.model_copy(update={"embedding_query_prefix": "different"})
    ).profile()
    assert embeddings.fingerprint(first) != embeddings.fingerprint(second)


def test_pooling_last_supports_both_padding_sides():
    import torch

    hidden = torch.tensor([[[10.0], [20.0], [30.0], [40.0]], [[50.0], [60.0], [70.0], [80.0]]])
    mask = torch.tensor([[0, 0, 1, 1], [1, 1, 0, 0]])
    assert embeddings.pool_hidden(hidden, mask, "last").tolist() == [[40.0], [60.0]]
    assert embeddings.pool_hidden(hidden, mask, "mean").tolist() == [[35.0], [55.0]]
    assert embeddings.pool_hidden(hidden, mask, "cls").tolist() == [[10.0], [50.0]]
    with pytest.raises(embeddings.EmbeddingError, match="有效 token"):
        embeddings.pool_hidden(hidden, torch.zeros_like(mask), "last")
    with pytest.raises(embeddings.EmbeddingError, match="池化"):
        embeddings.pool_hidden(hidden, mask, "unsupported")
    profile = embeddings.LocalTransformersProvider(
        get_settings().model_copy(update={"embedding_pooling": "last"})
    ).profile()
    assert profile["adapter"] == "transformers-last-v2"
    assert embeddings.LocalTransformersProvider().profile()["adapter"] == "transformers-v1"


def diagnostic_worker(connection, options):
    try:
        raise RuntimeError("SECRET_QUERY_AND_CREDENTIALS")
    except RuntimeError as exc:
        connection.send((False, embeddings.worker_failure(exc, "inference")))
    finally:
        connection.close()


def test_worker_diagnostics_are_actionable_without_sensitive_text(monkeypatch, caplog):
    provider = embeddings.LocalTransformersProvider()
    monkeypatch.setattr(embeddings, "_model_worker", diagnostic_worker)
    logger = logging.getLogger("app")
    logger.addHandler(caplog.handler)
    try:
        with pytest.raises(embeddings.EmbeddingError, match="加载或推理失败"):
            provider.encode(["PRIVATE_DOCUMENT"])
    finally:
        logger.removeHandler(caplog.handler)
        provider.close()
    assert "exception_type=RuntimeError" in caplog.text
    assert "stage=inference" in caplog.text
    assert "test_embeddings.py:" in caplog.text
    assert "SECRET_QUERY_AND_CREDENTIALS" not in caplog.text
    assert "PRIVATE_DOCUMENT" not in caplog.text
    assert provider._process is None
