"""Resize document_chunks.embedding to the configured embedding dimension.

ADR 0008 picked OpenAI ``text-embedding-3-small`` (1536-dim) as the
first embedding path and sized ``document_chunks.embedding`` as
``vector(1536)`` to match. It also recorded the Mode-2 follow-on: an
operator running air-gapped wants an Ollama-served embedding model, and
those are natively 768-dim (``nomic-embed-text``, ``embeddinggemma``) or
1024-dim (``bge-m3``, ``qwen3-embedding``) — never 1536. The gateway's
Ollama ``/api/embed`` adapter now exists, so the column has to be able
to follow the model.

This migration resizes the column to ``settings.embedding_dimension``
(default 1536 — a no-op for every deployment that hasn't changed it).

**It refuses rather than destroys.** pgvector cannot reinterpret an
existing vector at a different width, so a resize necessarily discards
stored vectors. If any row holds a non-NULL embedding AND the target
width differs from the current one, the migration raises instead of
silently dropping work an operator paid an embedding provider to
produce. Clearing that state is a deliberate operator act:

    UPDATE document_chunks SET embedding = NULL;

...after which re-running the migration resizes cleanly, and the
existing backfill path (``embed_chunks_job``) regenerates vectors with
the newly-configured model.

The ivfflat ANN index is dropped and recreated because it binds to the
column's declared dimension.

Revision ID: 0067
Revises: 0066
"""

from sqlalchemy import text

from alembic import op
from app.config import get_settings

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None

# The width the column shipped with (ADR 0008 / migration 0005). Used as
# the downgrade target and as the "was it ever changed?" reference point.
SHIPPED_DIMENSION = 1536


def _current_dimension(conn) -> int | None:  # type: ignore[no-untyped-def]
    """Read the column's declared vector width from the catalog.

    ``format_type`` renders a pgvector column as ``vector(1536)``; we
    parse the width back out. Returns ``None`` if the column is absent
    or not a sized vector (nothing to do in that case).
    """

    rendered = conn.execute(
        text(
            """
            SELECT format_type(a.atttypid, a.atttypmod)
            FROM pg_attribute a
            WHERE a.attrelid = 'document_chunks'::regclass
              AND a.attname = 'embedding'
              AND NOT a.attisdropped
            """
        )
    ).scalar()
    if not rendered or not rendered.startswith("vector(") or not rendered.endswith(")"):
        return None
    try:
        return int(rendered[len("vector(") : -1])
    except ValueError:
        return None


def _resize(conn, target: int) -> None:  # type: ignore[no-untyped-def]
    """Drop the ANN index, retype the column, rebuild the index."""

    conn.execute(text("DROP INDEX IF EXISTS idx_chunks_embedding"))
    # No USING clause: every surviving value is NULL (guaranteed by the
    # caller's guard), so there is nothing for pgvector to cast.
    conn.execute(text(f"ALTER TABLE document_chunks ALTER COLUMN embedding TYPE vector({target})"))
    conn.execute(
        text(
            """
            CREATE INDEX idx_chunks_embedding
                ON document_chunks
                USING ivfflat (embedding vector_cosine_ops)
                WITH (lists = 100)
            """
        )
    )


def _apply(target: int) -> None:
    conn = op.get_bind()
    current = _current_dimension(conn)
    if current is None or current == target:
        # Column missing, unsized, or already the right width.
        return

    embedded = conn.execute(
        text("SELECT count(*) FROM document_chunks WHERE embedding IS NOT NULL")
    ).scalar_one()
    if embedded:
        raise RuntimeError(
            f"Refusing to resize document_chunks.embedding from {current} to {target}: "
            f"{embedded} row(s) hold vectors that a resize would destroy. Clear them "
            "deliberately (UPDATE document_chunks SET embedding = NULL) and re-run, "
            "then re-embed with the newly configured model."
        )

    _resize(conn, target)


def upgrade() -> None:
    _apply(get_settings().embedding_dimension)


def downgrade() -> None:
    _apply(SHIPPED_DIMENSION)
