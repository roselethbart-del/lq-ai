"""Tests for the Easy Playbook pipeline's bounded LLM fan-out.

Covers the three properties the callers depend on: results align
positionally with the submitted work, the semaphore actually caps
in-flight calls, and one failure doesn't take its siblings down.
"""

from __future__ import annotations

import asyncio

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
