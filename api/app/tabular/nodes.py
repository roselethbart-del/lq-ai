"""LangGraph nodes for the Tabular Review executor — M3-C2.

Three nodes run sequentially:

1. :func:`load_documents_node` — resolve ``document_ids`` to
   :class:`app.models.document.Document` rows; emit a list of
   document snapshots (id + name) the extraction node reads. Documents
   that have been soft-deleted between request time and worker pickup
   are skipped silently — the row is preserved as audit but the result
   set is honest about which sources were resolvable.
2. :func:`extract_cells_node` — for each ``(document, column)`` pair,
   FTS over the document's chunks using the column's ``query`` as
   keyword input, then run a structured-output LLM call to extract the
   answer + cited chunk indices. Per-cell try/except: any failure (no
   chunks, gateway error, malformed response) lands as
   ``confidence='failed'`` per Decision C-10. Sequential dispatch in
   v0.3.0; per-cell parallelism is a follow-on if 200 x 10 latency
   forces it.
3. :func:`aggregate_node` — group per-cell results by document into
   the final ``tabular_executions.results`` JSONB payload. Flips status
   to ``'completed'`` (or ``'failed'`` if a prior node set
   ``state['error']``). Persists ``cost_actual_usd`` as the sum across
   all cells.

Failure handling: any node may set ``state['error']`` to short-circuit
later nodes. :func:`aggregate_node` reads ``error`` and routes the row
to ``'failed'`` rather than ``'completed'``. Gateway / DB exceptions
inside a node bubble up; the executor catches them at the
graph-invocation boundary.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.citation.verification import verify
from app.knowledge.embed import request_embedding_vector
from app.knowledge.retrieval import hybrid_search_document
from app.models.document import Document, DocumentChunk
from app.models.file import File
from app.models.tabular import TabularExecution
from app.observability_helpers import get_tracer, record_attributes
from app.schemas.gateway import ChatCompletionMessage, ChatCompletionRequest
from app.schemas.tabular import ColumnSpec
from app.tabular.cost import TABULAR_EXTRACTION_PURPOSE
from app.tabular.state import TabularExecutionState

if TYPE_CHECKING:
    from app.clients.gateway import EnsembleConfig, GatewayClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cell-verification candidate / document stubs (Donna #6)
# ---------------------------------------------------------------------------
#
# The Citation Engine's :func:`app.citation.verification.verify` reads a
# candidate (the "claim") and a document (the "source") via duck-typed
# protocols. For a tabular cell the claim is the extracted value and the
# source is the concatenated cited-chunk text. We mint these minimal
# stubs (mirroring ``tests/citation/test_ensemble.py``'s
# ``_StubCandidate`` / ``_StubDocument``) rather than load a real
# Document row — the cell's grounding evidence is the chunk text already
# in hand, and the synthetic ids only feed a trace-span attribute.


# Not ``frozen`` — the verifier's ``_CandidateProtocol`` /
# ``_DocumentProtocol`` declare settable attributes, so a frozen
# (read-only) dataclass fails the structural-subtype check. ``slots``
# keeps them lightweight; we never mutate them after construction.
@dataclass(slots=True)
class _CellVerifyCandidate:
    source_offset_start: int
    source_offset_end: int
    source_text: str
    source_document_id: uuid.UUID


@dataclass(slots=True)
class _CellVerifyDocument:
    id: uuid.UUID
    normalized_content: str
    was_ocrd: bool = False


# Top-k chunks retrieved per cell. Bounded so the per-cell LLM context
# stays reasonable across the 200 x 10 cell upper bound — at 4 chunks
# x ~500 chars each, input is ~2K plus the system prompt.
RETRIEVAL_TOP_K = 4

# Max tokens for the cell-extraction LLM call. The structured output
# is short — value + cited indices + confidence + brief justification.
EXTRACT_MAX_TOKENS = 500

# Schema-version stamp on the persisted ``results`` JSONB. Bumped on
# any shape-breaking change so the result-view renderer can refuse to
# render unknown versions instead of crashing.
RESULTS_SCHEMA_VERSION = "m3-c2-v1"

_VALID_CONFIDENCES: frozenset[str] = frozenset({"high", "medium", "low", "failed"})

# Per-cell retrieval outcome, persisted alongside the cell so a reader
# can tell WHY a cell is empty. Before this existed, a retrieval miss
# and a genuine absence both rendered as `confidence='failed'` with
# "no value in extraction response", and the operator had no way to see
# that the model had been handed the document's opening pages instead of
# anything relevant to the column.
RETRIEVAL_MATCHED = "matched"
"""Hybrid search returned chunks relevant to the column's query."""

RETRIEVAL_FALLBACK = "fallback"
"""Search found nothing; the model was shown the document's first
chunks purely so it had some context. A ``failed`` cell carrying this
means "we could not find the subject in this document", NOT "this
document does not contain it"."""

RETRIEVAL_EMPTY = "empty"
"""The document has no chunks at all (unparsed or empty extraction)."""


# ---------------------------------------------------------------------------
# load_documents_node
# ---------------------------------------------------------------------------


def make_load_documents_node(
    db: AsyncSession,
    document_ids: list[uuid.UUID],
) -> Callable[[TabularExecutionState], Awaitable[dict[str, Any]]]:
    """Build the load-documents node bound to a DB session."""

    async def load_documents_node(state: TabularExecutionState) -> dict[str, Any]:
        # Join the parent File for the operator-facing filename — the
        # display name for the grid's row label. The Document row itself
        # carries no filename (it lives on files.filename), so without
        # the join the row label falls back to the document UUID.
        stmt = (
            select(Document.id, File.filename)
            .join(File, Document.file_id == File.id)
            .where(Document.id.in_(document_ids))
        )
        by_id = {row.id: row.filename for row in (await db.execute(stmt)).all()}

        # Preserve the operator's selection order (matches the playbook
        # easy_playbook_worker's _load_documents helper); missing rows
        # are silently skipped.
        documents: list[dict[str, str]] = []
        for did in document_ids:
            filename = by_id.get(did)
            if filename is None:
                logger.info(
                    "tabular_executor.load_documents: document missing; skipping",
                    extra={
                        "event": "tabular_load_documents_missing",
                        "document_id": str(did),
                    },
                )
                continue
            documents.append(
                {
                    "id": str(did),
                    "name": filename,
                }
            )

        return {"documents": documents}

    return load_documents_node


# ---------------------------------------------------------------------------
# extract_cells_node
# ---------------------------------------------------------------------------


_EXTRACT_SYSTEM_PROMPT = """\
You are a Tabular Extraction Assistant for a legal AI tool.

You will be given:
* A DOCUMENT NAME (for context).
* A QUERY (the column header's per-row prompt).
* A ranked list of DOCUMENT EXCERPTS retrieved via lexical search over
  the document.

Your job: extract a concise answer to the QUERY from the EXCERPTS, and
cite which excerpts back your answer.

Output STRICTLY VALID JSON in this exact shape:

  {"value": "<extracted value as a short string; empty string if not present>",
   "cited_chunk_indices": [<int>, ...],
   "confidence": "high" | "medium" | "low" | "failed",
   "justification": "<one or two sentences explaining the value or why none was found>"}

Confidence meanings:

* "high" — the answer is stated explicitly in one or more excerpts.
* "medium" — the answer is implied but not stated verbatim.
* "low" — the excerpts only weakly support the answer; flag for human review.
* "failed" — the excerpts do not contain enough information to answer.

The ``cited_chunk_indices`` field is a list of 0-based indices of the
excerpts (in the order presented below) whose content supports your
answer. Always include at least one index for non-"failed" confidence.

Bias toward "failed" when the document does not address the QUERY at
all — false positives are worse than false negatives in tabular review.
"""


def make_extract_cells_node(
    *,
    db: AsyncSession,
    gateway: GatewayClient,
    judge_model: str,
) -> Callable[[TabularExecutionState], Awaitable[dict[str, Any]]]:
    """Build the cell-extraction node bound to a DB session + gateway.

    Walks ``documents x columns`` sequentially; per cell, fetches the
    top-K relevant chunks via FTS using the column's query as keyword
    input, then calls :func:`extract_cell` to dispatch the LLM call.
    Accumulates results into ``state['per_cell_results']``.
    """

    async def extract_cells_node(state: TabularExecutionState) -> dict[str, Any]:
        documents = state.get("documents", []) or []
        columns_raw = state.get("columns", []) or []
        # Re-hydrate the column spec from the state dict shape; the
        # serializable representation lives in state but the cell-level
        # logic wants the Pydantic shape for field access.
        columns = [ColumnSpec.model_validate(col) for col in columns_raw]

        per_cell_results: list[dict[str, Any]] = []

        # Resolve the gateway's Stage-4 ensemble config ONCE for the whole
        # run (the lookup is process-cached). Per column we then decide
        # the effective flag: explicit per-column value wins; None falls
        # back to the gateway's deployment default. ``verify_ensemble_config``
        # is the gateway config when this column should actually run
        # ensemble AND the gateway has ensemble configured — else None.
        #
        # Cost posture (intentional): tabular ensemble verification runs one
        # ensemble pass per cell and is NOT bounded by a mid-run per-message
        # cost cap the way the chat path is (``_resolve_ensemble_config`` in
        # ``api/app/api/chats.py`` falls back to a single judge once an
        # estimate exceeds ``max_cost_per_message_usd``). Instead, tabular
        # gates cost up-front: ``POST /api/v1/tabular/preview-cost`` surfaces
        # the ensemble premium and the operator confirms ``confirmed_cost_usd``
        # before the run starts (Decision C-5 cost-confirmation gate). A future
        # mid-run / per-cell ensemble cost ceiling is deferred as DE-331.
        ensemble_config = await gateway.get_citation_engine_ensemble_config()

        # Embed each column's retrieval text ONCE for the whole run. The
        # text is identical across every document in the grid, so
        # embedding per cell would multiply an identical call by the row
        # count for no benefit. A column whose embedding fails maps to
        # None and its cells run FTS-only.
        query_embeddings = await _embed_column_queries(columns, gateway=gateway)

        tracer = get_tracer()
        for document in documents:
            document_id = uuid.UUID(document["id"])
            for column in columns:
                effective = (
                    column.ensemble_verification
                    if column.ensemble_verification is not None
                    else (ensemble_config.default_enabled if ensemble_config is not None else False)
                )
                verify_ensemble_config = (
                    ensemble_config if (effective and ensemble_config is not None) else None
                )
                with tracer.start_as_current_span("tabular.cell") as cell_span:
                    record_attributes(
                        cell_span,
                        **{
                            "tabular.document.id": document["id"],
                            "tabular.column.name": column.name,
                        },
                    )
                    chunks, retrieval = await _retrieve_for_cell(
                        db,
                        document_id=document_id,
                        column=column,
                        query_embedding=query_embeddings.get(column.name),
                    )
                    record_attributes(cell_span, **{"tabular.retrieval": retrieval})
                    cell = await extract_cell(
                        gateway=gateway,
                        judge_model=judge_model,
                        document_name=document["name"],
                        chunks=chunks,
                        column=column,
                        verify_ensemble_config=verify_ensemble_config,
                        retrieval=retrieval,
                    )
                    cell["document_id"] = str(document_id)
                    cell["column_name"] = column.name
                    per_cell_results.append(cell)

        return {"per_cell_results": per_cell_results}

    return extract_cells_node


async def extract_cell(
    *,
    gateway: GatewayClient,
    judge_model: str,
    document_name: str,
    chunks: list[dict[str, Any]],
    column: ColumnSpec,
    verify_ensemble_config: EnsembleConfig | None = None,
    retrieval: str = RETRIEVAL_MATCHED,
) -> dict[str, Any]:
    """Run one cell extraction; return a cell-result dict.

    Public surface (not just an internal helper) so the unit tests can
    exercise the LLM-dispatch + parsing logic without standing up the
    full LangGraph workflow + DB.

    ``retrieval`` is the outcome of the search that produced ``chunks``
    (see :data:`RETRIEVAL_MATCHED` / :data:`RETRIEVAL_FALLBACK`). It is
    recorded on the cell and folded into the error text of a failure, so
    an empty cell says which of the two very different things happened:
    the search missed, or the model read relevant text and found no
    answer in it.

    Failure paths:

    * No chunks at all (document has no parsed text) → short-circuit to
      ``confidence='failed'`` without a gateway call.
    * Gateway raises → ``confidence='failed'`` with ``error`` populated.
    * LLM response is malformed JSON → ``confidence='failed'``.
    * LLM returns an empty ``value`` → ``confidence='failed'``, with the
      error distinguishing "not present in the retrieved text" from
      "nothing relevant was retrieved to begin with".
    """

    if not chunks:
        return _failed_cell("document has no parsed text", retrieval=RETRIEVAL_EMPTY)

    messages = _build_extract_messages(
        document_name=document_name,
        column=column,
        chunks=chunks,
    )

    request = ChatCompletionRequest(
        model=judge_model,
        messages=messages,
        max_tokens=EXTRACT_MAX_TOKENS,
        think=False,
        # Structured-JSON output, no analysis needed — a hidden
        # chain-of-thought pass on an Ollama reasoning model can consume
        # the whole `max_tokens` budget before any content is emitted.
        # No-op on non-Ollama providers.
        anonymize=False,
        lq_ai_purpose=TABULAR_EXTRACTION_PURPOSE,
        minimum_inference_tier=column.minimum_inference_tier,
    )

    try:
        response = await gateway.chat_completion(request)
    except Exception as exc:
        logger.warning(
            "tabular extract_cell gateway error: %s",
            exc,
            extra={
                "event": "tabular_extract_cell_error",
                "error_type": type(exc).__name__,
            },
        )
        return _failed_cell(f"{type(exc).__name__}: {exc}", retrieval=retrieval)

    try:
        choices = response.choices
        if not choices:
            return _failed_cell("empty response from gateway", retrieval=retrieval)
        content = choices[0].message.content
    except AttributeError:
        return _failed_cell("malformed gateway response", retrieval=retrieval)

    if not content:
        return _failed_cell("empty response content", retrieval=retrieval)

    parsed = _parse_cell_response(content)
    if not parsed:
        # Covers all three ways the parse yields nothing usable: invalid
        # JSON, valid JSON that isn't an object, and an empty object.
        # Worth separating from an empty `value` — that is a result,
        # this is the model failing to answer in the required shape.
        return _failed_cell("extraction response was not a usable JSON object", retrieval=retrieval)
    value = parsed.get("value")
    if not value or not isinstance(value, str) or not value.strip():
        # The two cases read identically on the wire but mean opposite
        # things to a reviewer deciding whether to trust the blank.
        reason = (
            "no relevant text found in this document for this column"
            if retrieval == RETRIEVAL_FALLBACK
            else "not found in the retrieved text"
        )
        return _failed_cell(reason, retrieval=retrieval)

    confidence = _coerce_confidence(parsed.get("confidence"))
    cited_indices = _coerce_chunk_indices(
        parsed.get("cited_chunk_indices"),
        n_chunks=len(chunks),
    )
    cited_chunk_ids = [chunks[i]["id"] for i in cited_indices]
    value = value.strip()

    # Stage-4 ensemble verification (Donna #6). When this column should
    # run ensemble (config supplied) and the cell has grounding chunks,
    # run ONE ensemble pass over the concatenation of ALL cited chunks:
    # the "claim" is the extracted value, the "source" is the cited
    # chunk text. Stages 1-2 usually MISS (a short value rarely equals
    # the long concatenation), so Stage 4 fires. Note: a near-verbatim
    # single-chunk value CAN legitimately hit Stage 1 (``exact_match``) or
    # Stage 2 (``fuzz.ratio`` >= 95, ``tolerant_match``) — that surfaces a
    # ``verification_method`` of ``exact_match``/``tolerant_match`` instead
    # of an ``ensemble_*`` value, which is a STRONGER verification, not an
    # error. A verification failure must NEVER fail the cell or alter its
    # value/confidence/citations, so the whole pass is defensively wrapped.
    verification_method: str | None = None
    if verify_ensemble_config is not None and cited_chunk_ids:
        verification_method = await _verify_cell_ensemble(
            gateway=gateway,
            value=value,
            chunks=chunks,
            cited_chunk_ids=cited_chunk_ids,
            ensemble_config=verify_ensemble_config,
        )

    return {
        "value": value,
        "cited_chunk_ids": cited_chunk_ids,
        "confidence": confidence,
        "tier_used": column.minimum_inference_tier,
        # cost_usd is best filled by the gateway response surface; for
        # v0.3.0 we leave it at 0 here and reconcile from the routing
        # log post-hoc in the aggregate node. The cost-estimator's
        # rolling-average converges off the routing log either way.
        "cost_usd": "0",
        "error": None,
        "verification_method": verification_method,
        "retrieval": retrieval,
    }


async def _verify_cell_ensemble(
    *,
    gateway: GatewayClient,
    value: str,
    chunks: list[dict[str, Any]],
    cited_chunk_ids: list[str],
    ensemble_config: EnsembleConfig,
) -> str | None:
    """Run one Stage-4 ensemble verify pass for a tabular cell.

    Concatenates ALL cited chunks' content as the source and the
    extracted ``value`` as the claim, then dispatches through
    :func:`app.citation.verification.verify`. Returns the method string
    (e.g. ``ensemble_strict``) when the ensemble verified the value, else
    ``None``. Any exception degrades to ``None`` — a verification failure
    is never a cell failure.
    """

    try:
        cited_set = set(cited_chunk_ids)
        concat = "\n---\n".join(chunk["content"] for chunk in chunks if chunk["id"] in cited_set)
        # Deterministic synthetic ids — used only for a trace-span
        # attribute, so a stable uuid5 off the primary chunk id is
        # preferred over uuid4.
        synthetic_id = uuid.uuid5(uuid.NAMESPACE_DNS, cited_chunk_ids[0])
        candidate = _CellVerifyCandidate(
            source_offset_start=0,
            source_offset_end=len(concat),
            source_text=value,
            source_document_id=synthetic_id,
        )
        document = _CellVerifyDocument(id=synthetic_id, normalized_content=concat)
        result = await verify(
            candidate,
            document,
            gateway=gateway,
            ensemble_config=ensemble_config,
        )
        return result.method if result.verified else None
    except Exception as exc:
        logger.warning(
            "tabular extract_cell ensemble verification error: %s",
            exc,
            extra={
                "event": "tabular_extract_cell_verify_error",
                "error_type": type(exc).__name__,
            },
        )
        return None


def _failed_cell(reason: str, *, retrieval: str = RETRIEVAL_MATCHED) -> dict[str, Any]:
    return {
        "value": None,
        "cited_chunk_ids": [],
        "confidence": "failed",
        "tier_used": None,
        "cost_usd": "0",
        "error": reason,
        "verification_method": None,
        "retrieval": retrieval,
    }


def _build_extract_messages(
    *,
    document_name: str,
    column: ColumnSpec,
    chunks: list[dict[str, Any]],
) -> list[ChatCompletionMessage]:
    """Render the extraction-prompt messages for one cell."""

    chunk_blocks: list[str] = []
    for i, chunk in enumerate(chunks):
        chunk_blocks.append(f"[CHUNK {i}]\n{chunk['content']}")

    user_content = (
        f"DOCUMENT NAME: {document_name}\n\n"
        f"QUERY: {column.query}\n\n"
        f"DOCUMENT EXCERPTS:\n" + ("\n\n".join(chunk_blocks) if chunk_blocks else "(none)")
    )
    return [
        ChatCompletionMessage(role="system", content=_EXTRACT_SYSTEM_PROMPT),
        ChatCompletionMessage(role="user", content=user_content),
    ]


def _column_retrieval_text(column: ColumnSpec) -> str:
    """The text both retrieval sides search on for a column.

    The column NAME is included, not just the query. A name is the
    operator's own noun phrase for the thing they want ("Governing
    Law", "Liquidated Damages") — short, on-vocabulary, and free of the
    instruction words ("look for", "search the whole document") that a
    query written as a prompt carries. Those instruction words are
    exactly what poisons a lexical search, and the name is the cleanest
    available signal for the vector side.
    """

    name = (column.name or "").strip()
    query = (column.query or "").strip()
    if name and query:
        return f"{name}. {query}"
    return name or query


async def _embed_column_queries(
    columns: list[ColumnSpec],
    *,
    gateway: GatewayClient,
) -> dict[str, list[float] | None]:
    """Embed each column's retrieval text once for the whole run.

    A failure here is never fatal: the column maps to ``None`` and its
    cells fall back to FTS-only retrieval. That keeps a deployment with
    no embedding model configured — or a document ingested before
    embeddings were switched on — working exactly as it did before,
    rather than failing every cell.
    """

    embeddings: dict[str, list[float] | None] = {}
    for column in columns:
        retrieval_text = _column_retrieval_text(column)
        if not retrieval_text:
            embeddings[column.name] = None
            continue
        try:
            embeddings[column.name] = await request_embedding_vector(
                retrieval_text,
                gateway=gateway,
            )
        except Exception as exc:
            logger.warning(
                "tabular column query embedding failed; falling back to FTS-only: %s",
                exc,
                extra={
                    "event": "tabular_column_embed_failed",
                    "column_name": column.name,
                    "error_type": type(exc).__name__,
                },
            )
            embeddings[column.name] = None
    return embeddings


async def _retrieve_for_cell(
    db: AsyncSession,
    *,
    document_id: uuid.UUID,
    column: ColumnSpec,
    query_embedding: list[float] | None,
) -> tuple[list[dict[str, Any]], str]:
    """Retrieve the chunks one cell's extraction reads.

    Hybrid (vector + FTS) search scoped to the target document, per ADR
    0008 — the same retrieval the KB query endpoint uses, rather than
    the bare lexical search this node used to run. Returns the chunks
    plus a retrieval outcome the caller records on the cell.

    When the search finds nothing, we still hand the model the
    document's first chunks (so a cell is evaluated rather than skipped)
    but mark the cell :data:`RETRIEVAL_FALLBACK`. Those opening chunks
    are typically a cover page or table of contents; any answer drawn
    from them deserves the flag.
    """

    results = await hybrid_search_document(
        db,
        document_id=document_id,
        query=_column_retrieval_text(column),
        query_embedding=query_embedding,
        top_k=RETRIEVAL_TOP_K,
    )
    if results:
        return (
            [
                {
                    "id": str(r.chunk_id),
                    "chunk_index": r.chunk_index,
                    "content": r.content,
                    "char_offset_start": r.char_offset_start,
                    "char_offset_end": r.char_offset_end,
                    "page_start": r.page_start,
                }
                for r in results
            ],
            RETRIEVAL_MATCHED,
        )

    logger.info(
        "tabular retrieval found nothing; using the document's first chunks",
        extra={
            "event": "tabular_retrieval_fallback",
            "document_id": str(document_id),
            "column_name": column.name,
        },
    )
    fallback = await _fetch_first_chunks(db, document_id, limit=RETRIEVAL_TOP_K)
    return fallback, (RETRIEVAL_FALLBACK if fallback else RETRIEVAL_EMPTY)


async def _fetch_first_chunks(
    db: AsyncSession,
    document_id: uuid.UUID,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Defensive fallback when FTS yields no rows."""

    stmt = (
        select(DocumentChunk)
        .where(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.chunk_index)
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "id": str(row.id),
            "chunk_index": row.chunk_index,
            "content": row.content,
            "char_offset_start": row.char_offset_start,
            "char_offset_end": row.char_offset_end,
            "page_start": row.page_start,
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# aggregate_node — group cells by document, persist results
# ---------------------------------------------------------------------------


def make_aggregate_node(
    db: AsyncSession,
) -> Callable[[TabularExecutionState], Awaitable[dict[str, Any]]]:
    """Build the aggregation node bound to a DB session.

    Groups ``state['per_cell_results']`` by document into the final
    ``tabular_executions.results`` JSONB shape; flips status to
    ``'completed'`` (or ``'failed'`` if a prior node set
    ``state['error']``); writes ``cost_actual_usd`` as the sum across
    cells; sets ``completed_at``.
    """

    async def aggregate_node(state: TabularExecutionState) -> dict[str, Any]:
        execution_id = uuid.UUID(state["execution_id"])
        documents = state.get("documents", []) or []
        per_cell_results = state.get("per_cell_results", []) or []
        error = state.get("error")

        results_payload = _shape_results_payload(per_cell_results, documents)
        cost_actual = _sum_cell_costs(per_cell_results)

        values: dict[str, Any] = {
            "results": results_payload,
            "cost_actual_usd": cost_actual,
            "completed_at": datetime.now(UTC),
        }
        if error:
            values["status"] = "failed"
            values["error_text"] = str(error)[:2000]
        else:
            values["status"] = "completed"

        await db.execute(
            update(TabularExecution).where(TabularExecution.id == execution_id).values(**values)
        )
        await db.commit()
        return {}

    return aggregate_node


def _assemble_rows(
    per_cell_results: list[Any],
    documents: list[Any],
) -> list[dict[str, Any]]:
    """Group cells by document and emit rows in ``documents`` order.

    Cells are keyed by ``column_name`` within each row. The per-cell
    in-flight shape (with ``document_id`` + ``column_name`` keys) gets
    converted to the persisted :class:`app.schemas.tabular.CellResult`
    shape (no document_id / column_name; those live on the row /
    cell-map key)."""

    by_doc: dict[str, dict[str, dict[str, Any]]] = {}
    for cell in per_cell_results:
        doc_id = cell.get("document_id")
        col_name = cell.get("column_name")
        if not doc_id or not col_name:
            continue
        by_doc.setdefault(doc_id, {})[col_name] = _strip_state_keys(cell)

    rows: list[dict[str, Any]] = []
    for doc in documents:
        cells = by_doc.get(doc["id"], {})
        rows.append(
            {
                "document_id": doc["id"],
                "document_name": doc["name"],
                "cells": cells,
            }
        )
    return rows


def _strip_state_keys(cell: dict[str, Any]) -> dict[str, Any]:
    """Project the in-flight cell shape down to the persisted shape."""
    keys = (
        "value",
        "cited_chunk_ids",
        "confidence",
        "tier_used",
        "cost_usd",
        "error",
        "verification_method",
        "retrieval",
    )
    return {key: cell.get(key) for key in keys}


def _shape_results_payload(
    per_cell_results: list[Any],
    documents: list[Any],
) -> dict[str, Any]:
    """Render the per-cell results into the JSONB payload shape."""

    rows = _assemble_rows(per_cell_results, documents)
    total = len(per_cell_results)
    failed = sum(1 for c in per_cell_results if c.get("confidence") == "failed")
    fallback = sum(1 for c in per_cell_results if c.get("retrieval") == RETRIEVAL_FALLBACK)
    # ``schema_version`` is deliberately NOT bumped: ``retrieval`` and
    # ``retrieval_fallback_cells`` are additive, and every existing
    # reader keys off named fields, so old rows and new rows both render.
    return {
        "schema_version": RESULTS_SCHEMA_VERSION,
        "rows": rows,
        "summary": {
            "total_cells": total,
            "failed_cells": failed,
            "retrieval_fallback_cells": fallback,
        },
    }


def _sum_cell_costs(per_cell_results: list[Any]) -> Decimal:
    """Sum per-cell costs to derive ``cost_actual_usd``.

    Cells without a recorded cost contribute 0 — the v0.3.0 cell node
    does not yet propagate per-call cost back from the gateway
    response surface, so this defaults to 0 across all cells until
    the gateway returns cost in its response shape. Once it does,
    this sum becomes the authoritative actual."""

    total = Decimal("0")
    for cell in per_cell_results:
        raw = cell.get("cost_usd")
        if raw is None:
            continue
        try:
            total += Decimal(str(raw))
        except Exception:
            continue
    return total


# ---------------------------------------------------------------------------
# Structured-output JSON parser (lenient)
# ---------------------------------------------------------------------------


def _parse_cell_response(content: str) -> dict[str, Any]:
    """Lenient JSON parse — trim a leading code fence if present, then
    :func:`json.loads`."""

    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("```", 2)[1]
        if stripped.startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.rstrip("`").strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        logger.warning(
            "tabular extract_cell returned malformed JSON: %s",
            exc,
            extra={"event": "tabular_extract_cell_malformed_json"},
        )
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


def _coerce_confidence(raw: Any) -> str:
    """Normalize confidence to one of the four valid values; default ``low``."""

    if isinstance(raw, str) and raw in _VALID_CONFIDENCES:
        return raw
    return "low"


def _coerce_chunk_indices(raw: Any, *, n_chunks: int) -> list[int]:
    """Filter the model-emitted index list to valid 0-based ints inside the chunk count."""

    if not isinstance(raw, list):
        return []
    out: list[int] = []
    for item in raw:
        if isinstance(item, int) and not isinstance(item, bool) and 0 <= item < n_chunks:
            out.append(item)
    return out


__all__ = [
    "RESULTS_SCHEMA_VERSION",
    "RETRIEVAL_EMPTY",
    "RETRIEVAL_FALLBACK",
    "RETRIEVAL_MATCHED",
    "RETRIEVAL_TOP_K",
    "extract_cell",
    "make_aggregate_node",
    "make_extract_cells_node",
    "make_load_documents_node",
]
