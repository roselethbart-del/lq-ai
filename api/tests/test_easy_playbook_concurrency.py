"""Tests for the Easy Playbook pipeline's bounded LLM fan-out.

Covers the three properties the callers depend on: results align
positionally with the submitted work, the semaphore actually caps
in-flight calls, and one failure doesn't take its siblings down.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.config import get_settings
from app.playbooks.easy.concurrency import gather_bounded, new_semaphore


@pytest.mark.unit
async def test_gather_bounded_preserves_submission_order() -> None:
    """Results align positionally with `factories`, regardless of the order
    the work actually finishes in — the extract phase relies on this to keep
    clauses in document order."""

    async def slow(value: int, delay: float) -> int:
        await asyncio.sleep(delay)
        return value

    # Deliberately inverted delays: the last item finishes first.
    results = await gather_bounded(
        [
            lambda: slow(0, 0.03),
            lambda: slow(1, 0.02),
            lambda: slow(2, 0.01),
        ],
        semaphore=asyncio.Semaphore(3),
    )
    assert results == [0, 1, 2]


@pytest.mark.unit
async def test_gather_bounded_caps_in_flight_calls() -> None:
    """Never more than `semaphore` coroutines run at once.

    The bound is the point: an unbounded fan-out against a local Ollama
    server (which serves OLLAMA_NUM_PARALLEL at a time) is a thundering
    herd for no throughput gain.
    """

    in_flight = 0
    peak = 0

    async def tracked() -> int:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return 1

    await gather_bounded([tracked for _ in range(12)], semaphore=asyncio.Semaphore(3))
    assert peak <= 3
    assert peak > 1, "expected genuine concurrency, not serialized execution"


@pytest.mark.unit
async def test_gather_bounded_returns_failures_in_place() -> None:
    """A failing item comes back as an exception at its own index; its
    siblings still return their results."""

    async def ok(value: int) -> int:
        return value

    async def boom() -> int:
        raise RuntimeError("span failed")

    results = await gather_bounded(
        [lambda: ok(0), boom, lambda: ok(2)],
        semaphore=asyncio.Semaphore(2),
    )
    assert results[0] == 0
    assert isinstance(results[1], RuntimeError)
    assert results[2] == 2


@pytest.mark.unit
async def test_gather_bounded_empty_input() -> None:
    """No work submitted is not an error — an empty corpus reaches here."""

    assert await gather_bounded([], semaphore=asyncio.Semaphore(2)) == []


@pytest.mark.unit
async def test_assemble_playbook_fans_out_without_stalling() -> None:
    """Regression test for a semaphore re-entrancy deadlock in assembly.

    `assemble_playbook` previously bounded `_build_position` itself with
    the same semaphore its tier calls needed. Every slot ended up held by
    a position waiting on tiers that could only run once a position
    released, so throughput collapsed to roughly serial (measured at 0.73x
    against a live provider) while the isolated `gather_bounded` tests all
    passed — they never exercised the nested composition.

    The bound belongs on the leaf LLM calls, never on the composite. This
    asserts both halves of that: real concurrency across positions, and a
    peak that still respects the bound.
    """

    import json
    from dataclasses import dataclass

    from app.playbooks.easy.assembly import assemble_playbook
    from app.playbooks.easy.clustering import ClauseInput, Cluster
    from app.playbooks.easy.extractor import ExtractedClauseSourceOffsets  # noqa: F401

    @dataclass
    class _Msg:
        content: str

    @dataclass
    class _Choice:
        message: _Msg

    @dataclass
    class _Resp:
        choices: list[_Choice]

    in_flight = 0
    peak = 0

    class _SlowGateway:
        """Every call takes real time, so serialization is observable."""

        async def chat_completion(self, request: object) -> _Resp:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.02)
            in_flight -= 1
            return _Resp(
                choices=[
                    _Choice(
                        message=_Msg(
                            content=json.dumps(
                                {
                                    "description": "d",
                                    "redline_strategy": "r",
                                    "severity_if_missing": "medium",
                                }
                            )
                        )
                    )
                ]
            )

    def _ci(label: str, text: str) -> ClauseInput:
        return ClauseInput(document_id=uuid.uuid4(), issue=label, clause_text=text)

    # 4 clusters, each with 2 fallback tiers => 4 x (1 + 2) = 12 LLM calls.
    # Under the old shape those 12 calls serialized behind held slots.
    clusters = [
        Cluster(
            issue_label=f"Issue {i}",
            member_clauses=[_ci(f"Issue {i}", "modal")],
            modal_clause=_ci(f"Issue {i}", "modal"),
            neighbor_clauses=[_ci(f"Issue {i}", "n1"), _ci(f"Issue {i}", "n2")],
        )
        for i in range(4)
    ]

    sem = asyncio.Semaphore(4)
    async with asyncio.timeout(10):
        playbook = await assemble_playbook(
            clusters=clusters,
            name="Concurrency probe",
            contract_type="Subcontract",
            gateway=_SlowGateway(),  # type: ignore[arg-type]
            semaphore=sem,
        )

    assert len(playbook.positions) == 4
    assert peak > 1, "assembly serialized — the re-entrancy deadlock is back"
    assert peak <= 4, "exceeded the configured concurrency bound"


@pytest.mark.unit
async def test_assembled_positions_are_densely_renumbered() -> None:
    """`position_order` must stay dense and zero-indexed — the schema and
    the M3-A2 executor both assume it — even though assembly now drops a
    failed position rather than failing the whole playbook."""

    from app.playbooks.easy.assembly import assemble_playbook
    from app.playbooks.easy.clustering import ClauseInput, Cluster

    def _ci(label: str, text: str) -> ClauseInput:
        return ClauseInput(document_id=uuid.uuid4(), issue=label, clause_text=text)

    clusters = [
        Cluster(
            issue_label=f"Issue {i}",
            member_clauses=[_ci(f"Issue {i}", "m")],
            modal_clause=_ci(f"Issue {i}", "m"),
            neighbor_clauses=[],
        )
        for i in range(3)
    ]

    class _EmptyGateway:
        async def chat_completion(self, request: object) -> object:
            raise RuntimeError("no content")

    playbook = await assemble_playbook(
        clusters=clusters,
        name="Renumber probe",
        contract_type="Subcontract",
        gateway=_EmptyGateway(),  # type: ignore[arg-type]
    )
    orders = [p.position_order for p in playbook.positions]
    assert orders == list(range(len(orders))), f"sparse position_order: {orders}"


@pytest.mark.unit
def test_new_semaphore_follows_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Operators tune the bound to their provider's parallelism; 1 restores
    the previous strictly-sequential behavior."""

    monkeypatch.setenv("EASY_PLAYBOOK_MAX_CONCURRENCY", "1")
    get_settings.cache_clear()
    try:
        assert new_semaphore()._value == 1
    finally:
        monkeypatch.delenv("EASY_PLAYBOOK_MAX_CONCURRENCY", raising=False)
        get_settings.cache_clear()
