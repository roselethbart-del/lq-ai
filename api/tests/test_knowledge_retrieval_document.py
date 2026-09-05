"""Document-scoped hybrid retrieval — :func:`hybrid_search_document`.

Regression coverage for the retrieval bug that made Tabular Review and
Playbook execution silently read the wrong part of a document.

Both executors built their tsquery from free text and matched with
``websearch_to_tsquery``, believing it gave OR semantics. It does not —
it AND-joins every lexeme. A column query written the way an operator
naturally writes one ("Look for the governing law. Search in the whole
document") therefore demanded a single chunk contain *look*, *search*
and *whole* alongside *govern* and *law*. No clause does, so retrieval
returned nothing, the caller fell back to the document's opening chunks,
and the model dutifully reported that a cover page contains no governing
law — as a confident "not found" rather than a retrieval miss.

These tests pin the fixed behaviour: all-terms matching first (it is the
most precise when it hits), any-term backoff ranked by term coverage
when it does not, and an empty result — never a silent wrong answer —
when the document shares no vocabulary with the query.

Marked ``integration``: needs Postgres for ``content_tsv`` / FTS.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.knowledge.retrieval import hybrid_search_document
from app.models.document import Document, DocumentChunk
from app.models.file import File as FileModel
from app.models.user import User
from app.security import hash_password

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

COVER = "Cover page. Subcontract agreement between the Contractor and the Subcontractor."
PAYMENT = "Article 12. Payment terms. The Contractor shall pay within thirty days."
GOVERNING = (
    "Article 21. This Subcontract shall be governed by and construed in "
    "accordance with the laws of the State of Qatar."
)
LABOUR = "The Subcontractor shall comply with all applicable labour law requirements."
FORUM = "Disputes shall be referred to the competent courts of Doha."

# Written the way an operator writes a column prompt: an instruction,
# not a keyword list. Every one of `look`, `search` and `whole` becomes
# a mandatory search term under AND semantics.
INSTRUCTION_QUERY = "Look for the governing / applicable law. Search in the whole document"


async def _make_user(db: AsyncSession) -> User:
    user = User(
        email=f"u-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("pw"),
        is_admin=False,
        role="member",
        mfa_enabled=False,
        must_change_password=False,
    )
    db.add(user)
    await db.flush()
    return user


async def _make_doc(db: AsyncSession, *, owner: User, chunks: list[str]) -> Document:
    """Create a Document whose chunks are ``chunks``, in order."""

    body = "\n".join(chunks)
    f = FileModel(
        owner_id=owner.id,
        filename=f"doc-{uuid.uuid4().hex[:6]}.pdf",
        mime_type="application/pdf",
        size_bytes=len(body),
        hash_sha256="a" * 64,
        storage_path=f"retrieval-test/{uuid.uuid4()}",
        ingestion_status="ready",
    )
    db.add(f)
    await db.flush()
    doc = Document(
        file_id=f.id,
        parser="pymupdf-only",
        parser_version="pymupdf=1.27",
        page_count=1,
        character_count=len(body),
        normalized_content=body,
        was_ocrd=False,
    )
    db.add(doc)
    await db.flush()
    offset = 0
    for index, content in enumerate(chunks):
        db.add(
            DocumentChunk(
                document_id=doc.id,
                chunk_index=index,
                content=content,
                page_start=1,
                page_end=1,
                char_offset_start=offset,
                char_offset_end=offset + len(content),
            )
        )
        offset += len(content) + 1
    await db.flush()
    return doc


def _indices(results: list) -> list[int]:
    return [r.chunk_index for r in results]


# ---------------------------------------------------------------------------
# The regression: an instruction-shaped query must still find the clause
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_instruction_query_still_finds_the_governing_law_clause(
    db_session: AsyncSession,
) -> None:
    """The bug: AND semantics returned nothing for a prose query.

    None of these chunks contains 'look', 'search' or 'whole', so the
    all-terms pass finds nothing. The any-term backoff must still surface
    the governing-law clause rather than leaving the caller to fall back
    to the cover page.
    """

    owner = await _make_user(db_session)
    doc = await _make_doc(db_session, owner=owner, chunks=[COVER, PAYMENT, GOVERNING])

    results = await hybrid_search_document(
        db_session,
        document_id=doc.id,
        query=INSTRUCTION_QUERY,
        query_embedding=None,
        top_k=4,
        alpha=1.0,
    )

    assert _indices(results) == [2]
    assert "laws of the State of Qatar" in results[0].content


@pytest.mark.asyncio
async def test_all_terms_match_wins_when_it_hits(db_session: AsyncSession) -> None:
    """A precise query keeps its precision — the backoff never fires.

    'governing law' matches chunk 2 alone under AND. Chunk 3 contains
    'law' but not 'governing', so it must NOT be returned: falling back
    to any-term matching here would dilute a perfectly good result set.
    """

    owner = await _make_user(db_session)
    doc = await _make_doc(db_session, owner=owner, chunks=[COVER, PAYMENT, GOVERNING, LABOUR])

    results = await hybrid_search_document(
        db_session,
        document_id=doc.id,
        query="governing law",
        query_embedding=None,
        top_k=4,
        alpha=1.0,
    )

    assert _indices(results) == [2]


@pytest.mark.asyncio
async def test_backoff_ranks_by_term_coverage(db_session: AsyncSession) -> None:
    """In the backoff, matching more distinct query terms ranks higher.

    No chunk holds all of {govern, law, jurisdict, court, venue}. The
    governing-law clause matches two of them; the forum clause matches
    one. Coverage, not raw term frequency, has to drive the order — the
    naive alternative (rank purely by ``ts_rank_cd``) lets a chunk that
    repeats one common word outrank a chunk that hits several.
    """

    owner = await _make_user(db_session)
    doc = await _make_doc(
        db_session, owner=owner, chunks=[COVER, PAYMENT, GOVERNING, LABOUR, FORUM]
    )

    results = await hybrid_search_document(
        db_session,
        document_id=doc.id,
        query="governing law jurisdiction courts venue",
        query_embedding=None,
        top_k=5,
        alpha=1.0,
    )

    ordered = _indices(results)
    assert ordered[0] == 2, f"governing-law clause should rank first, got {ordered}"
    assert 4 in ordered, "the forum clause should still be a candidate"
    assert ordered.index(2) < ordered.index(4)


@pytest.mark.asyncio
async def test_returns_empty_when_document_shares_no_vocabulary(
    db_session: AsyncSession,
) -> None:
    """No overlap must return nothing, so callers can say so.

    This is the signal that separates 'we could not find it' from 'the
    model read the clause and found no answer'. If this ever returned
    chunks, that distinction collapses again.
    """

    owner = await _make_user(db_session)
    doc = await _make_doc(db_session, owner=owner, chunks=[COVER, PAYMENT, GOVERNING])

    results = await hybrid_search_document(
        db_session,
        document_id=doc.id,
        query="refrigerated shipping containers",
        query_embedding=None,
        top_k=4,
        alpha=1.0,
    )

    assert results == []


@pytest.mark.asyncio
async def test_blank_query_returns_empty(db_session: AsyncSession) -> None:
    """A whitespace-only query is not a match-everything query."""

    owner = await _make_user(db_session)
    doc = await _make_doc(db_session, owner=owner, chunks=[COVER, GOVERNING])

    results = await hybrid_search_document(
        db_session,
        document_id=doc.id,
        query="   ",
        query_embedding=None,
        top_k=4,
        alpha=1.0,
    )

    assert results == []


@pytest.mark.asyncio
async def test_scoped_to_the_requested_document(db_session: AsyncSession) -> None:
    """Never return another document's chunks, however well they match."""

    owner = await _make_user(db_session)
    target = await _make_doc(db_session, owner=owner, chunks=[COVER, PAYMENT])
    other = await _make_doc(db_session, owner=owner, chunks=[GOVERNING, FORUM])

    results = await hybrid_search_document(
        db_session,
        document_id=target.id,
        query="governing law",
        query_embedding=None,
        top_k=4,
        alpha=1.0,
    )

    assert results == []
    assert all(r.document_id != other.id for r in results)


@pytest.mark.asyncio
async def test_results_carry_chunk_index_and_offsets(db_session: AsyncSession) -> None:
    """Callers rebuild citation payloads from these fields."""

    owner = await _make_user(db_session)
    doc = await _make_doc(db_session, owner=owner, chunks=[COVER, PAYMENT, GOVERNING])

    results = await hybrid_search_document(
        db_session,
        document_id=doc.id,
        query="governing law",
        query_embedding=None,
        top_k=4,
        alpha=1.0,
    )

    assert len(results) == 1
    hit = results[0]
    assert hit.chunk_index == 2
    assert hit.char_offset_start == len(COVER) + len(PAYMENT) + 2
    assert hit.char_offset_end == hit.char_offset_start + len(GOVERNING)
    assert hit.document_id == doc.id
