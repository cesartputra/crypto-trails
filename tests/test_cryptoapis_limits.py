"""Synthetic credit estimates and a shared pacer; no provider calls."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from services import cryptoapis_limits as module


class Clock:
    def __init__(self):
        self.now = 100.0

    async def sleep(self, seconds):
        self.now += seconds
        await asyncio.sleep(0)


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(module, '_sleep', clock.sleep)
    monkeypatch.setattr(module, '_monotonic', lambda: clock.now)
    monkeypatch.setattr(module, '_next_allowed', 0.0)
    monkeypatch.setattr(module, '_inflight_lock', threading.Lock())
    monkeypatch.setattr(module, '_state_lock', threading.Lock())
    return clock


@pytest.mark.parametrize('chain,category,expected', [
    ('ETHEREUM', 'transactions', 142), ('BSC', 'transactions', 520),
    ('POLYGON', 'transactions', 394), ('ARBITRUM', 'transactions', 294),
    ('OPTIMISM', 'transactions', 243), ('BASE', 'transactions', 193),
    ('BTC', 'transactions', 121), ('SOLANA', 'transactions', 90),
    ('ETHEREUM', 'tokens-transfers', 352), ('BSC', 'tokens-transfers', 1045),
    ('POLYGON', 'tokens-transfers', 814), ('ARBITRUM', 'tokens-transfers', 630),
    ('OPTIMISM', 'tokens-transfers', 537), ('BASE', 'tokens-transfers', 445),
])
def test_estimates_include_isolated_soft_limit_penalty_rounded_up(chain, category, expected):
    assert module.estimate_request_credits(chain, category) == expected


@pytest.mark.parametrize('chain,category', [
    ('UNKNOWN', 'transactions'), ('ETHEREUM', 'balance'),
    ('BTC', 'tokens-transfers'), ('SOLANA', 'tokens-transfers'),
])
def test_unsupported_estimates_fail_closed(chain, category):
    with pytest.raises(ValueError):
        module.estimate_request_credits(chain, category)


def test_gap_starts_after_response_finishes_across_successive_event_loops(clock):
    starts = []

    async def request(duration):
        async with module.request_slot():
            starts.append(clock.now)
            clock.now += duration

    asyncio.run(request(4))
    asyncio.run(request(0))
    asyncio.run(request(0))
    assert starts == pytest.approx([100, 105.2, 106.4])


def test_concurrent_coroutines_never_overlap_and_keep_post_response_gap(clock):
    events = []
    active = 0

    async def request():
        nonlocal active
        async with module.request_slot():
            active += 1
            assert active == 1
            events.append(('start', clock.now))
            await asyncio.sleep(0)
            clock.now += 2
            events.append(('end', clock.now))
            active -= 1

    async def run():
        await asyncio.gather(request(), request(), request())

    asyncio.run(run())
    assert [event for event, _ in events] == ['start', 'end'] * 3
    assert events[2][1] - events[1][1] >= 1.2 - 1e-9
    assert events[4][1] - events[3][1] >= 1.2 - 1e-9


@pytest.mark.parametrize('phase', ['response', 'cooldown'])
def test_cancellation_releases_slot_and_preserves_required_delay(clock, monkeypatch, phase):
    async def run():
        entered = asyncio.Event()
        never = asyncio.Event()

        async def blocked_sleep(seconds):
            entered.set()
            await never.wait()

        async def request():
            async with module.request_slot():
                entered.set()
                await never.wait()

        if phase == 'cooldown':
            module.defer_requests(8)
            monkeypatch.setattr(module, '_sleep', blocked_sleep)
        task = asyncio.create_task(request())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        monkeypatch.setattr(module, '_sleep', clock.sleep)
        async with module.request_slot():
            assert clock.now == pytest.approx(108 if phase == 'cooldown' else 101.2)

    asyncio.run(run())


def test_cooldown_set_inside_active_request_survives_context_exit(clock):
    async def run():
        async with module.request_slot():
            clock.now += 0.3
            module.defer_requests(8)
            module.defer_requests(2)
        async with module.request_slot():
            assert clock.now == pytest.approx(108.3)

    asyncio.run(run())


def test_cooldown_extension_is_rechecked_after_sleep(clock, monkeypatch):
    module.defer_requests(3)

    async def sleep_and_extend(seconds):
        await clock.sleep(seconds)
        monkeypatch.setattr(module, '_sleep', clock.sleep)
        module.defer_requests(5)

    monkeypatch.setattr(module, '_sleep', sleep_and_extend)

    async def run():
        async with module.request_slot():
            assert clock.now == pytest.approx(108)

    asyncio.run(run())


def test_separate_threads_and_event_loops_share_one_inflight_slot(clock, monkeypatch):
    first_entered = threading.Event()
    second_waiting = threading.Event()
    second_entered = threading.Event()
    release_first = threading.Event()

    async def poll(seconds):
        second_waiting.set()
        await clock.sleep(seconds)

    monkeypatch.setattr(module, '_sleep', poll)

    async def first():
        async with module.request_slot():
            first_entered.set()
            while not release_first.is_set():
                await asyncio.sleep(0.001)

    async def second():
        async with module.request_slot():
            second_entered.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_result = executor.submit(asyncio.run, first())
        assert first_entered.wait(2)
        second_result = executor.submit(asyncio.run, second())
        try:
            assert second_waiting.wait(2)
            assert not second_entered.is_set()
        finally:
            release_first.set()
        first_result.result(timeout=2)
        second_result.result(timeout=2)
    assert second_entered.is_set()


@pytest.mark.parametrize('delay', [-1, float('nan'), float('inf')])
def test_invalid_cooldown_cannot_poison_shared_queue(clock, delay):
    with pytest.raises(ValueError):
        module.defer_requests(delay)

    async def run():
        async with module.request_slot():
            assert clock.now == 100

    asyncio.run(run())
