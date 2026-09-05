"""Bounded concurrency for the Easy Playbook pipeline's LLM fan-out.

The extract and assemble phases each issue many small, independent calls
— one per document span, one per position, one per fallback tier. Run
strictly one-at-a-time, wall-clock scales linearly with corpus size,
which is what pushes a larger corpus past the worker's ``job_timeout``.

The bound matters as much as the concurrency. An unbounded fan-out would
open one connection per span against a single local Ollama server, which
serves ``OLLAMA_NUM_PARALLEL`` generations at a time and queues the rest
— converting a tidy sequential workload into a thundering herd for no
throughput gain, while risking rate-limit rejections on cloud providers.
One semaphore per generation keeps in-flight work at a level the
operator chose.

Callers share a single semaphore across nested fan-outs (assembly runs
positions concurrently, and each position's tiers concurrently) so the
total in flight stays bounded rather than multiplying per level.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence

from app.config import get_settings


def new_semaphore() -> asyncio.Semaphore:
    """Semaphore sized to ``settings.easy_playbook_max_concurrency``.

    One per generation run, shared down through every nested fan-out so
    the bound applies to total in-flight calls, not per level.
    """

    return asyncio.Semaphore(get_settings().easy_playbook_max_concurrency)


async def gather_bounded[T](
    factories: Sequence[Callable[[], Awaitable[T]]],
    *,
    semaphore: asyncio.Semaphore,
) -> list[T | BaseException]:
    """Run ``factories`` concurrently under ``semaphore``, preserving order.

    Each entry is a zero-argument callable returning an awaitable, rather
    than an already-created coroutine, so nothing starts before its slot
    is free.

    Results come back positionally aligned with ``factories``. Failures
    are returned in place as exception objects instead of cancelling
    their siblings — every caller here already tolerates a partial
    result (a failed span degrades clustering signal; a failed position
    is dropped), and one bad item must not lose the work of the others.
    """

    async def _run(factory: Callable[[], Awaitable[T]]) -> T:
        async with semaphore:
            return await factory()

    if not factories:
        return []
    return list(await asyncio.gather(*(_run(f) for f in factories), return_exceptions=True))
