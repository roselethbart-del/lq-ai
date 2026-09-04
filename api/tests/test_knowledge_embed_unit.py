"""Unit tests for the C6 embedding module.

Targets the helpers that don't require a live DB or gateway: token
counting, vector formatting, batch packing, and the request_embedding_*
functions against a respx-mocked GatewayClient.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from app.clients.gateway import GatewayClient
from app.config import get_settings
from app.errors import GatewayInvalidResponse
from app.knowledge.embed import (
    DEFAULT_EMBEDDING_MODEL,
    EMBEDDING_DIMENSION,
    _batched as batched,
    _format_vector,
    count_tokens,
    effective_embedding_dimension,
    request_embedding_vector,
    request_embedding_vectors,
)

GATEWAY_BASE = "http://test-gateway"
GATEWAY_KEY = "test-gw-key"

# Width of the toy vectors these tests hand to `_format_vector`, which
# guards against the configured column dimension. Declaring a matching
# width beats shipping 1536-float fixtures.
TOY_DIMENSION = 3


@pytest.fixture(autouse=True)
def _toy_embedding_dimension(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Point `settings.embedding_dimension` at the toy vector width.

    Per the documented convention (`app.config.get_settings`), tests that
    need a different config monkeypatch the env and clear the cache.
    """

    monkeypatch.setenv("EMBEDDING_DIMENSION", str(TOY_DIMENSION))
    get_settings.cache_clear()
    yield
    monkeypatch.delenv("EMBEDDING_DIMENSION", raising=False)
    get_settings.cache_clear()


# --- Token counting -----------------------------------------------------


@pytest.mark.unit
def test_count_tokens_returns_positive() -> None:
    """C6: count_tokens returns a positive int for non-empty input."""

    n = count_tokens("Hello, world.")
    assert n > 0


@pytest.mark.unit
def test_count_tokens_empty_string_returns_zero_or_one() -> None:
    """C6: empty input is permitted (the fallback path returns 1; tiktoken
    returns 0). Either is acceptable for downstream summation."""

    n = count_tokens("")
    assert n >= 0


@pytest.mark.unit
def test_count_tokens_grows_with_input() -> None:
    """C6: longer text produces strictly more tokens (monotonic)."""

    short = count_tokens("Hello")
    long = count_tokens("Hello, this is a much longer string with more words.")
    assert long > short


# --- Vector formatting --------------------------------------------------


@pytest.mark.unit
def test_format_vector_simple() -> None:
    """C6: pgvector textual form."""

    assert _format_vector([1.0, 2.0, 3.0]) == "[1.0,2.0,3.0]"


@pytest.mark.unit
def test_format_vector_handles_negatives_and_zero() -> None:
    """C6: zero and negative values format correctly."""

    assert _format_vector([-1.0, 0.0, 1.0]) == "[-1.0,0.0,1.0]"


@pytest.mark.unit
def test_embedding_dimension_default_matches_shipped_column_type() -> None:
    """The shipped default stays 1536 — the width migration 0005 created
    and the width ADR 0008's OpenAI pick produces."""

    assert EMBEDDING_DIMENSION == 1536


@pytest.mark.unit
def test_effective_embedding_dimension_follows_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Mode-2 operator repointing `embedding` at an Ollama model sets
    EMBEDDING_DIMENSION to that model's native width (768/1024); the
    effective width follows the setting, not the shipped constant."""

    monkeypatch.setenv("EMBEDDING_DIMENSION", "768")
    get_settings.cache_clear()
    try:
        assert effective_embedding_dimension() == 768
    finally:
        monkeypatch.delenv("EMBEDDING_DIMENSION", raising=False)
        get_settings.cache_clear()


# --- Batching -----------------------------------------------------------


class _FakeChunk:
    """Stand-in for DocumentChunk for batching tests — we don't need
    SQLAlchemy attributes, just `content`."""

    def __init__(self, content: str) -> None:
        self.content = content


@pytest.mark.unit
def test_batched_groups_within_size_limit() -> None:
    """C6: batches respect the per-batch chunk count cap."""

    # 200 small chunks should batch into multiple groups (default 64
    # per batch).
    chunks = [_FakeChunk(f"chunk{idx}") for idx in range(200)]
    batches = list(batched(chunks))
    assert all(len(batch) <= 64 for batch in batches)
    assert sum(len(b) for b in batches) == 200


@pytest.mark.unit
def test_batched_respects_char_cap() -> None:
    """C6: a single very-large chunk forces a tighter batch."""

    huge_content = "x" * 50_000  # well into the cap
    chunks = [_FakeChunk(huge_content) for _ in range(3)]
    batches = list(batched(chunks))
    # Each big chunk roughly fills its own batch (60K cap).
    assert len(batches) >= 2


@pytest.mark.unit
def test_batched_empty_input() -> None:
    """C6: empty input yields no batches."""

    assert list(batched([])) == []


# --- request_embedding_vector(s) against mocked gateway ----------------


def _embedding_payload(vectors: list[list[float]]) -> dict[str, Any]:
    """Build an OpenAI-shaped /v1/embeddings response."""

    return {
        "object": "list",
        "data": [
            {"object": "embedding", "embedding": vec, "index": idx}
            for idx, vec in enumerate(vectors)
        ],
        "model": "text-embedding-3-small",
        "usage": {"prompt_tokens": 5, "total_tokens": 5},
    }


@pytest.mark.unit
async def test_request_embedding_vector_happy_path() -> None:
    """C6: single-input embedding returns the vector verbatim."""

    expected = [0.1, 0.2, 0.3, 0.4]
    with respx.mock(base_url=GATEWAY_BASE) as router:
        router.post("/v1/embeddings").mock(
            return_value=httpx.Response(200, json=_embedding_payload([expected]))
        )
        gateway = GatewayClient(GATEWAY_BASE, GATEWAY_KEY)
        try:
            vector = await request_embedding_vector("Hello world", gateway=gateway)
        finally:
            await gateway.aclose()
        assert vector == expected


@pytest.mark.unit
async def test_request_embedding_vector_empty_data_raises() -> None:
    """C6: a payload with empty data raises GatewayInvalidResponse."""

    with respx.mock(base_url=GATEWAY_BASE) as router:
        router.post("/v1/embeddings").mock(
            return_value=httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [],
                    "model": "x",
                    "usage": {"prompt_tokens": 0, "total_tokens": 0},
                },
            )
        )
        gateway = GatewayClient(GATEWAY_BASE, GATEWAY_KEY)
        try:
            with pytest.raises(GatewayInvalidResponse):
                await request_embedding_vector("Hello", gateway=gateway)
        finally:
            await gateway.aclose()


@pytest.mark.unit
async def test_request_embedding_vectors_batch() -> None:
    """C6: a batch request returns a list of vectors, one per input."""

    expected = [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]]
    with respx.mock(base_url=GATEWAY_BASE) as router:
        router.post("/v1/embeddings").mock(
            return_value=httpx.Response(200, json=_embedding_payload(expected))
        )
        gateway = GatewayClient(GATEWAY_BASE, GATEWAY_KEY)
        try:
            vectors = await request_embedding_vectors(["a", "b", "c"], gateway=gateway)
        finally:
            await gateway.aclose()
        assert vectors == expected


@pytest.mark.unit
def test_format_vector_width_mismatch_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A model whose native width doesn't match the pgvector column is
    caught at the write boundary, naming the misconfiguration, rather
    than failing with an opaque driver error at INSERT."""

    monkeypatch.setenv("EMBEDDING_DIMENSION", "1024")
    get_settings.cache_clear()
    try:
        with pytest.raises(GatewayInvalidResponse) as exc_info:
            _format_vector([0.1, 0.2])
        assert "1024" in str(exc_info.value)
    finally:
        monkeypatch.delenv("EMBEDDING_DIMENSION", raising=False)
        get_settings.cache_clear()


@pytest.mark.unit
async def test_request_embedding_vectors_does_not_enforce_column_width() -> None:
    """Transient consumers (Easy Playbook clustering) compute cosine
    similarity in memory and never persist vectors, so the fetch path
    must not impose the storage column's width on them."""

    with respx.mock(base_url=GATEWAY_BASE) as router:
        router.post("/v1/embeddings").mock(
            return_value=httpx.Response(200, json=_embedding_payload([[0.1, 0.2, 0.3]]))
        )
        gateway = GatewayClient(GATEWAY_BASE, GATEWAY_KEY)
        try:
            vectors = await request_embedding_vectors(["a"], gateway=gateway)
        finally:
            await gateway.aclose()
    assert vectors == [[0.1, 0.2, 0.3]]


@pytest.mark.unit
async def test_request_embedding_vectors_count_mismatch_raises() -> None:
    """C6: gateway returning a different number of vectors than inputs raises."""

    expected = [[0.1, 0.2]]
    with respx.mock(base_url=GATEWAY_BASE) as router:
        router.post("/v1/embeddings").mock(
            return_value=httpx.Response(200, json=_embedding_payload(expected))
        )
        gateway = GatewayClient(GATEWAY_BASE, GATEWAY_KEY)
        try:
            with pytest.raises(GatewayInvalidResponse):
                await request_embedding_vectors(["a", "b", "c"], gateway=gateway)
        finally:
            await gateway.aclose()


@pytest.mark.unit
async def test_request_embedding_vectors_sorts_by_index() -> None:
    """C6: the gateway may return entries out of order; we sort by ``index``."""

    payload = {
        "object": "list",
        "data": [
            {"object": "embedding", "embedding": [3.0], "index": 2},
            {"object": "embedding", "embedding": [1.0], "index": 0},
            {"object": "embedding", "embedding": [2.0], "index": 1},
        ],
        "model": "x",
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
    }
    with respx.mock(base_url=GATEWAY_BASE) as router:
        router.post("/v1/embeddings").mock(return_value=httpx.Response(200, json=payload))
        gateway = GatewayClient(GATEWAY_BASE, GATEWAY_KEY)
        try:
            vectors = await request_embedding_vectors(["a", "b", "c"], gateway=gateway)
        finally:
            await gateway.aclose()
        assert vectors == [[1.0], [2.0], [3.0]]


@pytest.mark.unit
async def test_request_embedding_vectors_empty_input() -> None:
    """C6: empty input list short-circuits — no gateway call made."""

    with respx.mock(base_url=GATEWAY_BASE) as router:
        # No mock configured — if a request is made, it'll fail.
        gateway = GatewayClient(GATEWAY_BASE, GATEWAY_KEY)
        try:
            vectors = await request_embedding_vectors([], gateway=gateway)
        finally:
            await gateway.aclose()
        assert vectors == []
        assert not router.calls


@pytest.mark.unit
def test_default_embedding_model_is_alias() -> None:
    """C6: the backend asks for the alias 'embedding' so operators can
    repoint without changing application code."""

    assert DEFAULT_EMBEDDING_MODEL == "embedding"
